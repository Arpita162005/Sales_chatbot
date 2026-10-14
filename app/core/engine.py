"""
ChatEngine - wires everything together and runs one chat turn.

    question ─► QueryRouter ─┬─► analytics: SQLAgent (FAISS examples + DuckDB) ─► LLM narration
                             ├─► knowledge: FAISS documents ───────────────────► LLM answer
                             └─► general:   ───────────────────────────────────► LLM answer

`stream()` yields small JSON-able events that the API sends to the browser as
NDJSON, so the UI can show route, SQL, table and chart immediately and then
stream the answer token by token.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import AsyncIterator

from ..config import Settings
from . import prompts
from .analytics import SalesDB, build_database, result_as_text
from .charts import build_chart
from .embeddings import Embedder
from .knowledge import load_doc_chunks, load_router_examples, load_sql_examples, write_generated_docs
from .llm import OllamaClient, OllamaError
from .router import QueryRouter
from .sql_agent import SQLAgent, build_schema_text
from .vector_store import VectorStore

log = logging.getLogger("engine")

SUGGESTION_GROUPS = {
    "Revenue": [
        "What was the total revenue by month?",
        "Show the daily revenue trend",
        "Why did revenue decline in Q2?",
    ],
    "Products": [
        "Which product categories bring the most revenue and what is their share?",
        "Which sizes sell the most?",
        "Top 10 SKUs by units sold",
    ],
    "Geography": [
        "Top 10 states by revenue",
        "Which cities place the most orders?",
        "What is the most popular category in each state?",
    ],
    "Operations": [
        "Compare Amazon vs Merchant fulfilment",
        "What is the cancellation rate by category?",
        "Do promotions increase order value?",
    ],
    "Definitions": [
        "How is AOV calculated?",
        "How was the data cleaned?",
    ],
}
SUGGESTIONS = [q for qs in SUGGESTION_GROUPS.values() for q in qs]


# --------------------------------------------------------------------------- #
# Build helpers (also used by scripts/ingest.py)
# --------------------------------------------------------------------------- #

def _knowledge_fingerprint(knowledge_dir: Path, signature: str) -> str:
    h = hashlib.sha256(signature.encode())
    for p in sorted(knowledge_dir.rglob("*")):
        if p.is_file() and p.suffix in (".md", ".json"):
            h.update(p.relative_to(knowledge_dir).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def ensure_database(settings: Settings, force: bool = False) -> None:
    if force or not settings.duckdb_path.exists():
        build_database(settings.data_csv, settings.duckdb_path, log=log.info)
        # Regenerate data-derived documents whenever the DB is rebuilt.
        db = SalesDB(settings.duckdb_path)
        try:
            write_generated_docs(db, settings.knowledge_dir, log=log.info)
        finally:
            db.close()
    elif not (settings.knowledge_dir / "generated" / "eda_insights.md").exists():
        db = SalesDB(settings.duckdb_path)
        try:
            write_generated_docs(db, settings.knowledge_dir, log=log.info)
        finally:
            db.close()


def ensure_indexes(settings: Settings, embedder: Embedder, force: bool = False) -> dict[str, VectorStore]:
    idx_dir = settings.index_dir
    stores = {n: VectorStore(n, idx_dir) for n in ("docs", "sql_examples", "router")}
    fp = _knowledge_fingerprint(settings.knowledge_dir, embedder.signature)
    manifest = idx_dir / "manifest.json"
    current = json.loads(manifest.read_text()) if manifest.exists() else {}
    fresh = (not force and current.get("fingerprint") == fp
             and all(s.exists() for s in stores.values()))
    if fresh:
        for s in stores.values():
            s.load()
        return stores

    log.info("Building FAISS indexes (embedding backend: %s)...", embedder.signature)
    chunks = load_doc_chunks(settings.knowledge_dir)
    stores["docs"].build([c.text for c in chunks], [c.to_dict() for c in chunks], embedder)

    examples = load_sql_examples(settings.knowledge_dir)
    stores["sql_examples"].build(
        [e.embed_text() for e in examples],
        [{"title": e.title, "question": e.question, "chart": e.chart, "sql": e.sql} for e in examples],
        embedder,
    )
    pairs = load_router_examples(settings.knowledge_dir)
    stores["router"].build([u for u, _ in pairs], [{"text": u, "route": r} for u, r in pairs], embedder)

    idx_dir.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"fingerprint": fp, "signature": embedder.signature,
                                    "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "counts": {k: len(v) for k, v in stores.items()}}, indent=1))
    return stores


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #

class ChatEngine:
    def __init__(self, settings: Settings):
        self.s = settings
        ensure_database(settings)
        self.db = SalesDB(settings.duckdb_path, settings.sql_max_rows, settings.sql_timeout_s)
        self.llm = OllamaClient(settings.ollama_url, settings.llm_model, settings.llm_num_ctx,
                                settings.llm_think, settings.llm_keep_alive, settings.llm_timeout_s)
        self.embedder = Embedder(settings.embed_backend, settings.embed_model, settings.embed_device,
                                 settings.ollama_url, settings.ollama_embed_model)
        self.stores = ensure_indexes(settings, self.embedder)
        self.router = QueryRouter(self.stores["router"], self.embedder, self.llm,
                                  settings.router_min_confidence, settings.router_llm_fallback)
        self.schema_text = build_schema_text(self.db)
        self.sql_agent = SQLAgent(self.db, self.llm, self.embedder, self.stores["docs"],
                                  self.stores["sql_examples"], self.schema_text,
                                  retries=settings.sql_retries, k_docs=3,
                                  k_examples=settings.top_k_examples,
                                  temperature=settings.llm_sql_temperature)

    async def aclose(self) -> None:
        await self.llm.aclose()
        self.db.close()

    # ---- helpers -----------------------------------------------------------
    def _history_text(self, history: list[dict]) -> str:
        items = history[-self.s.history_turns:]
        lines = []
        for m in items:
            content = (m.get("content") or "").strip().replace("\n", " ")
            if m.get("role") == "user":
                lines.append(f"User: {content[:300]}")
            else:
                lines.append(f"Assistant: {content[:400]}")
                if m.get("sql"):
                    lines.append(f"(SQL used: {' '.join(m['sql'].split())[:600]})")
        return "\n".join(lines)

    def _retrieval_query(self, question: str, history: list[dict]) -> str:
        prev = [m["content"] for m in history if m.get("role") == "user"]
        if prev and len(question.split()) <= 7:
            return prev[-1] + " " + question
        return question

    def _search_docs(self, query: str, k: int) -> list[dict]:
        vec = self.embedder.encode_one(query)
        return [p | {"score": round(s, 3)} for s, p in self.stores["docs"].search(vec, k=k, min_score=0.15)]

    @staticmethod
    def _source_list(docs: list[dict]) -> list[dict]:
        return [{"title": d.get("title"), "section": d.get("section"), "source": d.get("source"),
                 "score": d.get("score")} for d in docs]

    def _msgs(self, system: str, user: str) -> list[dict]:
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    # ---- main entry ----------------------------------------------------------
    async def stream(self, question: str, history: list[dict] | None = None,
                     force_route: str | None = None) -> AsyncIterator[dict]:
        history = history or []
        t0 = time.perf_counter()
        timings: dict[str, float] = {}
        question = question.strip()
        if not question:
            yield {"type": "error", "message": "Please type a question."}
            return

        try:
            if force_route in ("analytics", "knowledge", "general"):
                from .router import RouteDecision
                decision = RouteDecision(force_route, 1.0, "forced", {})
            else:
                decision = await self.router.route(question, history)
        except Exception as exc:  # noqa: BLE001
            log.exception("router failed")
            yield {"type": "error", "message": f"Routing failed: {exc}"}
            return
        timings["route_ms"] = (time.perf_counter() - t0) * 1000
        yield {"type": "route", **decision.to_dict()}

        history_text = self._history_text(history)
        retrieval_q = self._retrieval_query(question, history)
        route = decision.route

        try:
            if route == "analytics":
                t1 = time.perf_counter()
                outcome = await self.sql_agent.run(question, history_text, retrieval_query=retrieval_q)
                timings["sql_ms"] = (time.perf_counter() - t1) * 1000
                if outcome.ok and outcome.result is not None:
                    res = outcome.result
                    yield {"type": "sql", "sql": outcome.sql, "attempts": outcome.attempts,
                           "elapsed_ms": round(res.elapsed_ms, 1), "title": outcome.title}
                    yield {"type": "table", **res.to_dict(max_rows=self.s.rows_to_ui)}
                    chart = build_chart(res, outcome.chart, outcome.title)
                    if chart:
                        yield {"type": "chart", "chart": chart}
                    yield {"type": "sources", "items": self._source_list(outcome.docs)}
                    user = prompts.analytics_user_prompt(
                        question, outcome.sql, result_as_text(res, self.s.rows_to_llm),
                        [d["text"] for d in outcome.docs], history_text)
                    messages = self._msgs(prompts.ANALYTICS_SYSTEM, user)
                else:
                    # SQL could not be produced -> answer from documents instead, and say so.
                    yield {"type": "sql_error", "sql": outcome.sql, "error": outcome.error,
                           "attempts": outcome.attempts}
                    docs = self._search_docs(retrieval_q, self.s.top_k_docs)
                    yield {"type": "sources", "items": self._source_list(docs)}
                    note = (f"(A database query for this question failed: {outcome.error}. "
                            "Answer from the documents if possible, and tell the user the exact query could not be run.)")
                    user = prompts.knowledge_user_prompt(question + "\n" + note, [d["text"] for d in docs],
                                                         history_text)
                    messages = self._msgs(prompts.KNOWLEDGE_SYSTEM, user)

            elif route == "knowledge":
                docs = self._search_docs(retrieval_q, self.s.top_k_docs)
                yield {"type": "sources", "items": self._source_list(docs)}
                user = prompts.knowledge_user_prompt(question, [d["text"] for d in docs], history_text)
                messages = self._msgs(prompts.KNOWLEDGE_SYSTEM, user)

            else:
                user = (("Conversation so far:\n" + history_text + "\n\n") if history_text else "") + question
                messages = self._msgs(prompts.GENERAL_SYSTEM, user)

            t2 = time.perf_counter()
            first = None
            async for piece in self.llm.stream_chat(messages, temperature=self.s.llm_temperature,
                                                    max_tokens=self.s.llm_max_tokens):
                if first is None:
                    first = (time.perf_counter() - t2) * 1000
                yield {"type": "token", "text": piece}
            timings["first_token_ms"] = first or 0.0
            timings["answer_ms"] = (time.perf_counter() - t2) * 1000

        except OllamaError as exc:
            yield {"type": "error", "message": str(exc) +
                   f"\nIs Ollama running and is the model pulled?  ollama pull {self.s.llm_model}"}
        except Exception as exc:  # noqa: BLE001
            log.exception("chat turn failed")
            yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}

        timings["total_ms"] = (time.perf_counter() - t0) * 1000
        yield {"type": "done", "timings": {k: round(v) for k, v in timings.items()}}

    async def answer(self, question: str, history: list[dict] | None = None,
                     force_route: str | None = None) -> dict:
        """Non-streaming version: collects all events into one JSON object."""
        out: dict = {"question": question, "answer": "", "route": None, "sql": None,
                     "table": None, "chart": None, "sources": [], "error": None, "timings": {}}
        async for ev in self.stream(question, history, force_route):
            t = ev["type"]
            if t == "token":
                out["answer"] += ev["text"]
            elif t == "route":
                out["route"] = {k: v for k, v in ev.items() if k != "type"}
            elif t == "sql":
                out["sql"] = ev["sql"]
            elif t == "sql_error":
                out["sql"] = ev.get("sql")
                out["sql_error"] = ev.get("error")
            elif t == "table":
                out["table"] = {k: v for k, v in ev.items() if k != "type"}
            elif t == "chart":
                out["chart"] = ev["chart"]
            elif t == "sources":
                out["sources"] = ev["items"]
            elif t == "error":
                out["error"] = ev["message"]
            elif t == "done":
                out["timings"] = ev["timings"]
        out["answer"] = out["answer"].strip()
        return out

    async def status(self) -> dict:
        llm = await self.llm.status()
        return {
            "llm": llm,
            "embeddings": {"backend": self.embedder.backend, "signature": self.embedder.signature,
                           "device": self.embedder.device},
            "vector_store": {"engine": self.stores["docs"].engine,
                             "counts": {k: len(v) for k, v in self.stores.items()}},
            "database": {"path": self.s.duckdb_path.name, "rows": self.db.row_count(),
                         **self.db.dataset_info()},
        }
