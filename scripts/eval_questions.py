"""
Run a batch of questions against a running server and print route, SQL and answer.
Useful after changing prompts, examples or the model.

    python scripts/eval_questions.py                       # default question set
    python scripts/eval_questions.py questions.txt         # one question per line
    python scripts/eval_questions.py --url http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

DEFAULT = [
    "hi",
    "What was the total revenue by month?",
    "How much did revenue drop from April to June?",
    "Top 5 states by revenue",
    "and only for kurta?",
    "Which sizes sell the most?",
    "What is the cancellation rate for Amazon vs Merchant fulfilment?",
    "Revenue from Odisha in May",
    "How is AOV calculated?",
    "Why was March removed from the data?",
    "What are the key insights from the EDA?",
    "Top 10 SKUs by units sold",
    "Show weekly orders trend",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", help="text file with one question per line")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    qs = DEFAULT
    if args.file:
        qs = [q.strip() for q in Path(args.file).read_text(encoding="utf-8").splitlines() if q.strip()]

    history: list[dict] = []
    with httpx.Client(base_url=args.url, timeout=300) as c:
        for q in qs:
            t = time.time()
            r = c.post("/api/chat/sync", json={"message": q, "history": history[-6:]})
            r.raise_for_status()
            d = r.json()
            route = (d.get("route") or {}).get("route")
            print("=" * 100)
            print(f"Q: {q}\nroute: {d.get('route')}  ({time.time() - t:.1f}s)")
            if d.get("sql"):
                print("SQL:", " ".join(d["sql"].split()))
            if d.get("sql_error"):
                print("SQL ERROR:", d["sql_error"])
            if d.get("table"):
                print(f"rows: {d['table']['row_count']}  first: {d['table']['rows'][:3]}")
            print("A:", d.get("answer") or d.get("error"))
            history += [{"role": "user", "content": q},
                        {"role": "assistant", "content": d.get("answer", ""), "route": route, "sql": d.get("sql")}]


if __name__ == "__main__":
    sys.exit(main())
