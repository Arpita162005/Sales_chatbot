"""
Text-to-SQL agent.

1. Retrieve similar solved examples + relevant definitions from FAISS.
2. Ask Qwen3 for {sql, chart, title} as schema-constrained JSON.
3. Validate (read-only, single SELECT) and run on DuckDB.
4. On error (or a suspicious empty result) feed the error back and retry.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from . import prompts
from .analytics import QueryResult, SalesDB, UnsafeSQLError, validate_sql
from .llm import OllamaClient
from .vector_store import VectorStore

log = logging.getLogger("sql_agent")

COLUMN_NOTES = {
    "row_id": "original CSV row index",
    "order_id": "Amazon order id; COUNT(DISTINCT order_id) = orders",
    "order_date": "DATE 2022-04-01..2022-06-29",
    "month_num": "4,5,6 - use for ordering months",
    "month_name": "'April','May','June'",
    "ship_status": "detailed Amazon status",
    "status_group": "'Shipped/Delivered','Cancelled','Returned','Pending'",
    "fulfilment": "'Amazon' or 'Merchant'",
    "service_level": "'Expedited' or 'Standard'",
    "style": "style code",
    "sku": "SKU code",
    "product_category": "'Set','kurta','Western Dress','Top','Ethnic Dress','Blouse','Bottom','Saree','Dupatta'",
    "size": "'Free','XS','S','M','L','XL','XXL','3XL','4XL','5XL','6XL'",
    "size_order": "1..11 - use for ordering sizes",
    "asin": "Amazon product id",
    "courier_ship_status": "'Shipped','Cancelled','Unshipped','unknown'",
    "order_quantity": "units on the line",
    "order_amount": "line amount in INR; revenue = SUM(order_amount)",
    "city": "UPPER CASE city, e.g. 'BENGALURU','HYDERABAD','MUMBAI','NEW DELHI','CHENNAI'",
    "state": "UPPER CASE state, e.g. 'MAHARASHTRA','KARNATAKA','ODISHA','DELHI','TAMIL NADU'",
    "state_raw": "state as typed in the raw file (avoid; use state)",
    "zip": "postal code",
    "promotion": "promotion ids or 'no promotion'",
    "has_promotion": "BOOLEAN",
    "customer_type": "'business' (B2B) or 'customer' (B2C)",
}


def build_schema_text(db: SalesDB) -> str:
    lines = ["sales("]
    for name, typ in db.columns():
        note = COLUMN_NOTES.get(name, "")
        lines.append(f"  {name} {typ}" + (f"  -- {note}" if note else ""))
    lines.append(")")
    return "\n".join(lines)


@dataclass
class SQLOutcome:
    ok: bool
    sql: str = ""
    chart: str = "none"
    title: str = ""
    explanation: str = ""
    result: QueryResult | None = None
    attempts: int = 0
    error: str = ""
    examples: list[dict] = field(default_factory=list)
    docs: list[dict] = field(default_factory=list)
    elapsed_ms: float = 0.0


class SQLAgent:
    def __init__(self, db: SalesDB, llm: OllamaClient, embedder, docs: VectorStore,
                 examples: VectorStore, schema_text: str, retries: int = 2,
                 k_docs: int = 3, k_examples: int = 4, temperature: float = 0.05):
        self.db = db
        self.llm = llm
        self.embedder = embedder
        self.docs = docs
        self.examples = examples
        self.schema_text = schema_text
        self.retries = retries
        self.k_docs = k_docs
        self.k_examples = k_examples
        self.temperature = temperature

    def retrieve(self, question: str, query_vec=None) -> tuple[list[dict], list[dict]]:
        vec = query_vec if query_vec is not None else self.embedder.encode_one(question)
        ex = [p | {"score": round(s, 3)} for s, p in self.examples.search(vec, k=self.k_examples)]
        docs = [p | {"score": round(s, 3)} for s, p in self.docs.search(vec, k=self.k_docs, min_score=0.15)]
        return ex, docs

    async def run(self, question: str, history_text: str = "", query_vec=None,
                  retrieval_query: str | None = None) -> SQLOutcome:
        t0 = time.perf_counter()
        examples, docs = self.retrieve(retrieval_query or question, query_vec)
        user = prompts.sql_user_prompt(question, self.schema_text, [d["text"] for d in docs],
                                       examples, history_text)
        messages = [{"role": "system", "content": prompts.SQL_SYSTEM},
                    {"role": "user", "content": user}]
        out = SQLOutcome(ok=False, examples=examples, docs=docs)
        empty_retry_used = False

        for attempt in range(1, self.retries + 2):
            out.attempts = attempt
            try:
                reply = await self.llm.chat_json(messages, prompts.SQL_SCHEMA_JSON,
                                                 temperature=self.temperature)
            except Exception as exc:  # noqa: BLE001
                out.error = f"LLM error: {exc}"
                break
            sql = (reply.get("sql") or "").strip()
            out.chart = reply.get("chart") or "none"
            out.title = reply.get("title") or ""
            out.explanation = reply.get("explanation") or ""
            if not sql:
                out.error = out.explanation or "The question cannot be answered from the sales table."
                break
            try:
                sql = validate_sql(sql)
                out.sql = sql
                result = self.db.query(sql)
            except (UnsafeSQLError, TimeoutError) as exc:
                out.sql, out.error = sql, str(exc)
            except Exception as exc:  # noqa: BLE001  (duckdb Binder/Parser/Catalog errors)
                out.sql, out.error = sql, str(exc).split("\n")[0][:400]
            else:
                out.result, out.error = result, ""
                has_text_filter = "where" in sql.lower() and "'" in sql
                if result.row_count == 0 and has_text_filter and not empty_retry_used \
                        and attempt <= self.retries:
                    empty_retry_used = True
                    messages += [{"role": "assistant", "content": json.dumps(reply)},
                                 {"role": "user", "content": prompts.SQL_EMPTY_PROMPT}]
                    continue
                out.ok = True
                break
            log.info("SQL attempt %d failed: %s", attempt, out.error)
            messages += [{"role": "assistant", "content": json.dumps(reply)},
                         {"role": "user", "content": prompts.SQL_FIX_PROMPT.format(error=out.error)}]

        out.elapsed_ms = (time.perf_counter() - t0) * 1000
        return out
