"""
Query router: decides which path answers a question.

  analytics -> DuckDB (text-to-SQL, exact numbers) + FAISS context -> LLM
  knowledge -> FAISS documents (schema, KPI definitions, EDA insights) -> LLM
  general   -> LLM directly (greetings, help, small talk)

Three layers, cheapest first:
  1. keyword rules           (instant)
  2. semantic kNN over labelled example utterances in FAISS (~5 ms)
  3. LLM classification, only when the first two disagree / are unsure
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .vector_store import VectorStore

ROUTES = ("analytics", "knowledge", "general")

_GENERAL_RE = re.compile(
    r"^\s*(hi+|hello|hey|hii+|good (morning|afternoon|evening)|namaste|thanks?|thank you|thx|ok(ay)?|"
    r"cool|great|bye|goodbye|see you|help|who are you|what can you do|how are you)\b[\s!.?]*$",
    re.IGNORECASE,
)
_FOLLOWUP_RE = re.compile(
    r"^\s*(and|what about|how about|same|now|also|then|only|just|instead|for)\b", re.IGNORECASE
)
_ANALYTIC_WORDS = [
    r"\btotal\b", r"\bsum\b", r"how many", r"how much", r"\bcount\b", r"number of", r"\baverage\b", r"\bavg\b",
    r"\btop\b", r"\bbottom\b", r"highest", r"lowest", r"\bmost\b", r"\bleast\b", r"\bbest\b", r"\bworst\b",
    r"\btrend", r"\bcompare", r"\bvs\.?\b", r"versus", r"\bby (month|state|city|category|size|day|week)",
    r"\bper (month|state|city|category|day|week|order)", r"breakdown", r"\bshare\b", r"percent", r"%",
    r"\bgrowth\b", r"\bgrew\b", r"\bdrop", r"increase", r"decrease", r"\bplot\b", r"\bchart\b", r"\bgraph\b",
    r"\bshow\b", r"\blist\b", r"\brank", r"\bwhich\b", r"\bin (april|may|june)\b",
]
_KNOWLEDGE_WORDS = [
    r"^\s*what (is|are|does) (a |an |the )?(aov|kpi|b2b|b2c|revenue|status_group|fulfil|order_amount|courier)",
    r"\bdefine\b", r"definition", r"\bmeaning\b", r"\bmean\b", r"\bexplain", r"how (is|are) .* (calculated|defined|computed)",
    r"how was", r"methodolog", r"\bcolumns?\b", r"\bschema\b", r"\bfields?\b", r"\bdataset\b", r"\bsource\b",
    r"clean(ed|ing)", r"insight", r"summar", r"recommend", r"\bwhy\b", r"objective", r"notebook",
]


@dataclass
class RouteDecision:
    route: str
    confidence: float
    method: str
    scores: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"route": self.route, "confidence": round(self.confidence, 3),
                "method": self.method, "scores": {k: round(v, 3) for k, v in self.scores.items()}}


class QueryRouter:
    def __init__(self, store: VectorStore, embedder, llm=None, min_confidence: float = 0.55,
                 use_llm_fallback: bool = True, k: int = 7):
        self.store = store
        self.embedder = embedder
        self.llm = llm
        self.min_confidence = min_confidence
        self.use_llm_fallback = use_llm_fallback
        self.k = k

    # ---- layers ------------------------------------------------------------
    @staticmethod
    def rule_scores(q: str) -> dict:
        ql = q.lower()
        a = sum(1 for p in _ANALYTIC_WORDS if re.search(p, ql))
        k = sum(1 for p in _KNOWLEDGE_WORDS if re.search(p, ql))
        g = 3 if _GENERAL_RE.match(q) else 0
        total = a + k + g
        if total == 0:
            return {r: 0.0 for r in ROUTES}
        return {"analytics": a / total, "knowledge": k / total, "general": g / total}

    def semantic_scores(self, q: str) -> dict:
        vec = self.embedder.encode_one(q)
        hits = self.store.search(vec, k=self.k)
        scores = {r: 0.0 for r in ROUTES}
        for sim, payload in hits:
            scores[payload["route"]] += max(sim, 0.0)
        total = sum(scores.values()) or 1.0
        return {r: s / total for r, s in scores.items()}

    async def llm_route(self, q: str, history: list[dict]) -> str | None:
        if not self.llm:
            return None
        ctx = ""
        if history:
            last_user = [m["content"] for m in history if m.get("role") == "user"][-2:]
            ctx = "Previous user messages: " + " | ".join(last_user) + "\n"
        schema = {"type": "object", "properties": {"route": {"type": "string", "enum": list(ROUTES)}},
                  "required": ["route"]}
        msgs = [
            {"role": "system", "content":
                "Classify the user's message for an Amazon India sales analytics chatbot.\n"
                "analytics = needs numbers computed from the sales data (totals, counts, rankings, trends, comparisons, charts).\n"
                "knowledge = asks what something means, how a metric is defined, about the dataset, cleaning, or EDA findings.\n"
                "general = greetings, thanks, help, small talk or unrelated topics.\n"
                "Reply with JSON only."},
            {"role": "user", "content": f"{ctx}Message: {q}"},
        ]
        try:
            out = await self.llm.chat_json(msgs, schema, temperature=0.0, max_tokens=30)
            route = out.get("route")
            return route if route in ROUTES else None
        except Exception:  # noqa: BLE001
            return None

    # ---- decision ----------------------------------------------------------
    async def route(self, q: str, history: list[dict] | None = None) -> RouteDecision:
        history = history or []
        q = q.strip()
        if _GENERAL_RE.match(q):
            return RouteDecision("general", 1.0, "rule", {"general": 1.0})

        last_route = next((m.get("route") for m in reversed(history)
                           if m.get("role") == "assistant" and m.get("route")), None)
        if last_route == "analytics" and len(q.split()) <= 7 and _FOLLOWUP_RE.match(q):
            return RouteDecision("analytics", 0.9, "follow-up", {"analytics": 0.9})

        rules = self.rule_scores(q)
        sem = self.semantic_scores(q)
        combined = {r: 0.65 * sem[r] + 0.35 * rules[r] for r in ROUTES}
        best = max(combined, key=combined.get)
        ordered = sorted(combined.values(), reverse=True)
        margin = ordered[0] - ordered[1]
        decision = RouteDecision(best, combined[best], "semantic+rules", combined)

        if (combined[best] < self.min_confidence or margin < 0.15) and self.use_llm_fallback:
            llm_choice = await self.llm_route(q, history)
            if llm_choice:
                decision = RouteDecision(llm_choice, max(combined[llm_choice], 0.6), "llm", combined)
        return decision
