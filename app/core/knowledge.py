"""
Knowledge base for retrieval (FAISS).

* Hand-written markdown in `knowledge/` (schema, KPI definitions, methodology).
* Documents generated from the data at ingest time in `knowledge/generated/`
  (exact column values and EDA insights), so retrieved facts never go stale.
* `sql_examples.md` is parsed into question -> SQL pairs used as few-shot
  examples for text-to-SQL.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .analytics import SalesDB, fmt_inr


@dataclass
class Chunk:
    id: str
    source: str        # file name
    title: str         # document title (# heading)
    section: str       # section heading (## heading)
    text: str          # full text that is embedded and shown to the LLM
    kind: str = "doc"  # doc | sql_example

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SQLExample:
    title: str
    question: str
    chart: str
    sql: str

    def embed_text(self) -> str:
        return f"{self.question} ({self.title})"


# --------------------------------------------------------------------------- #
# Loading and chunking
# --------------------------------------------------------------------------- #

def chunk_markdown(path: Path, base: Path) -> list[Chunk]:
    """Split one markdown file on '## ' headings. Each chunk keeps its title."""
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(base).as_posix()
    title_m = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    title = title_m.group(1).strip() if title_m else path.stem
    parts = re.split(r"^##\s+", text, flags=re.MULTILINE)
    chunks: list[Chunk] = []
    for i, part in enumerate(parts[1:], start=1):
        heading, _, body = part.partition("\n")
        body = body.strip()
        if not body:
            continue
        chunks.append(
            Chunk(
                id=f"{rel}#{i}",
                source=rel,
                title=title,
                section=heading.strip(),
                text=f"{title} - {heading.strip()}\n{body}",
            )
        )
    if not chunks and text.strip():
        chunks.append(Chunk(id=f"{rel}#0", source=rel, title=title, section=title, text=text.strip()))
    return chunks


def load_doc_chunks(knowledge_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(knowledge_dir.rglob("*.md")):
        if path.name == "sql_examples.md":
            continue
        chunks.extend(chunk_markdown(path, knowledge_dir))
    return chunks


def load_sql_examples(knowledge_dir: Path) -> list[SQLExample]:
    path = knowledge_dir / "sql_examples.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    examples: list[SQLExample] = []
    for part in re.split(r"^##\s+", text, flags=re.MULTILINE)[1:]:
        title, _, body = part.partition("\n")
        q = re.search(r"^Question:\s*(.+)$", body, flags=re.MULTILINE)
        c = re.search(r"^Chart:\s*(\w+)", body, flags=re.MULTILINE)
        s = re.search(r"```sql\s*(.*?)```", body, flags=re.DOTALL)
        if q and s:
            examples.append(
                SQLExample(
                    title=title.strip(),
                    question=q.group(1).strip(),
                    chart=(c.group(1).strip().lower() if c else "bar"),
                    sql=s.group(1).strip(),
                )
            )
    return examples


def load_router_examples(knowledge_dir: Path) -> list[tuple[str, str]]:
    path = knowledge_dir / "router_examples.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    pairs = []
    for route, items in data.items():
        if route.startswith("_"):
            continue
        pairs.extend((u, route) for u in items)
    return pairs


# --------------------------------------------------------------------------- #
# Generated documents (computed from DuckDB so numbers are exact)
# --------------------------------------------------------------------------- #

def _pct(a: float, b: float) -> float:
    return 0.0 if not b else 100.0 * a / b


def generate_column_values_doc(db: SalesDB) -> str:
    lines = [
        "# Column values",
        "",
        "Exact values stored in the `sales` table (most frequent first). Use these spellings in WHERE clauses.",
        "",
    ]
    for col in ["product_category", "size", "status_group", "ship_status", "fulfilment",
                "service_level", "customer_type", "courier_ship_status", "month_name"]:
        vals = db.distinct_values(col, limit=40)
        lines += [f"## Values of {col}", ", ".join(f"'{v}'" for v in vals), ""]
    states = db.distinct_values("state", limit=60)
    lines += ["## Values of state", "State names are UPPER CASE: " + ", ".join(f"'{v}'" for v in states), ""]
    cities = db.distinct_values("city", limit=40)
    lines += ["## Most common cities", "City names are UPPER CASE. Top 40 by order lines: "
              + ", ".join(f"'{v}'" for v in cities), ""]
    return "\n".join(lines)


def generate_insights_doc(db: SalesDB) -> str:
    """Recompute the notebook's insights (plus a few more) as retrievable text."""
    info = db.dataset_info()
    out: list[str] = ["# EDA insights (computed from the data)", ""]

    # Monthly revenue
    m = db.records("SELECT month_num, month_name, SUM(order_amount) AS rev, COUNT(DISTINCT order_id) AS orders "
                   "FROM sales GROUP BY 1, 2 ORDER BY 1")
    total = sum(r["rev"] for r in m)
    out += ["## Monthly revenue trend"]
    prev = None
    for r in m:
        s = f"- {r['month_name']} 2022 revenue: {fmt_inr(r['rev'])} from {r['orders']:,} orders"
        if prev:
            chg = _pct(r["rev"] - prev, prev)
            s += f" ({chg:+.2f}% vs previous month)"
        out.append(s)
        prev = r["rev"]
    if len(m) >= 2:
        q_chg = _pct(m[-1]["rev"] - m[0]["rev"], m[0]["rev"])
        out.append(f"- Revenue changed {q_chg:+.2f}% from {m[0]['month_name']} to {m[-1]['month_name']}. "
                   f"Q2 total revenue: {fmt_inr(total)}. Q2 monthly average: {fmt_inr(total / len(m))}.")
    out.append("")

    # Category revenue and share
    c = db.records("SELECT product_category, SUM(order_amount) AS rev, AVG(order_amount) AS avg_amt, "
                   "SUM(order_quantity) AS units FROM sales GROUP BY 1 ORDER BY rev DESC")
    out += ["## Revenue by product category"]
    for r in c:
        out.append(f"- {r['product_category']}: {fmt_inr(r['rev'])} ({_pct(r['rev'], total):.2f}% of revenue), "
                   f"{r['units']:,} units")
    top2 = c[:2]
    if len(top2) == 2:
        out.append(f"- {top2[0]['product_category']} and {top2[1]['product_category']} together make "
                   f"{_pct(top2[0]['rev'] + top2[1]['rev'], total):.2f}% of revenue.")
    out.append("")

    out += ["## Average price by category"]
    for r in sorted(c, key=lambda r: r["avg_amt"], reverse=True)[:5]:
        out.append(f"- {r['product_category']}: average line amount {fmt_inr(r['avg_amt'])}")
    out.append("")

    # AOV by month
    a = db.records("SELECT month_num, month_name, SUM(order_amount) / COUNT(DISTINCT order_id) AS aov "
                   "FROM sales GROUP BY 1, 2 ORDER BY 1")
    out += ["## Average order value by month"]
    for r in a:
        out.append(f"- {r['month_name']}: AOV {fmt_inr(r['aov'])}")
    if len(a) >= 2:
        out.append(f"- AOV changed {_pct(a[-1]['aov'] - a[0]['aov'], a[0]['aov']):+.2f}% from "
                   f"{a[0]['month_name']} to {a[-1]['month_name']}, so the revenue decline came mainly from fewer "
                   "orders, not from lower order values.")
    out.append("")

    # Cancellations / returns
    s = db.records("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE status_group='Cancelled') AS canc, "
                   "COUNT(*) FILTER (WHERE status_group='Returned') AS ret FROM sales")[0]
    out += ["## Cancellations and returns",
            f"- Cancelled lines (Cancelled + Lost in Transit): {s['canc']:,} = {_pct(s['canc'], s['n']):.2f}% of all lines.",
            f"- Returned lines (returned, returning, rejected, damaged): {s['ret']:,} = {_pct(s['ret'], s['n']):.2f}% of all lines.",
            f"- Together {_pct(s['canc'] + s['ret'], s['n']):.2f}% of order lines did not result in a kept sale.",
            ""]
    cf = db.records("SELECT fulfilment, 100.0*COUNT(*) FILTER (WHERE status_group='Cancelled')/COUNT(*) AS cr "
                    "FROM sales GROUP BY 1 ORDER BY 1")
    out += ["## Cancellation rate by fulfilment"]
    out += [f"- {r['fulfilment']}: {r['cr']:.2f}% of lines cancelled" for r in cf]
    out.append("")

    # Sizes
    z = db.records("SELECT size, SUM(order_amount) AS rev, SUM(order_quantity) AS units FROM sales "
                   "GROUP BY size, size_order ORDER BY size_order")
    best = sorted(z, key=lambda r: r["rev"], reverse=True)[:3]
    out += ["## Sales by size",
            "- " + "; ".join(f"{r['size']}: {fmt_inr(r['rev'])}" for r in z),
            "- Best-selling sizes by revenue: " + ", ".join(r["size"] for r in best) + ".",
            ""]

    # Geography
    g = db.records("SELECT state, SUM(order_amount) AS rev FROM sales GROUP BY 1 ORDER BY 2 DESC LIMIT 5")
    out += ["## Top states by revenue"]
    out += [f"- {r['state']}: {fmt_inr(r['rev'])} ({_pct(r['rev'], total):.2f}%)" for r in g]
    out.append("")
    ct = db.records("SELECT city, SUM(order_amount) AS rev FROM sales WHERE city <> 'UNKNOWN' "
                    "GROUP BY 1 ORDER BY 2 DESC LIMIT 5")
    out += ["## Top cities by revenue"]
    out += [f"- {r['city']}: {fmt_inr(r['rev'])}" for r in ct]
    out.append("")

    pc = db.records(
        "SELECT state, product_category, units FROM (SELECT state, product_category, SUM(order_quantity) AS units, "
        "ROW_NUMBER() OVER (PARTITION BY state ORDER BY SUM(order_quantity) DESC) AS rn FROM sales "
        "GROUP BY 1, 2) t WHERE rn = 1 ORDER BY units DESC LIMIT 10")
    out += ["## Most popular category in the biggest states"]
    out += [f"- {r['state']}: {r['product_category']} ({r['units']:,} units)" for r in pc]
    out.append("")

    # Customer type, fulfilment, promotion
    b = db.records("SELECT customer_type, SUM(order_amount) AS rev, AVG(order_amount) AS avg_amt, "
                   "COUNT(DISTINCT order_id) AS orders FROM sales GROUP BY 1 ORDER BY 1")
    out += ["## Business (B2B) vs retail customers (B2C)"]
    out += [f"- {r['customer_type']}: {r['orders']:,} orders, revenue {fmt_inr(r['rev'])}, "
            f"average line amount {fmt_inr(r['avg_amt'])}" for r in b]
    out.append("")
    f = db.records("SELECT fulfilment, SUM(order_amount) AS rev, COUNT(DISTINCT order_id) AS orders "
                   "FROM sales GROUP BY 1 ORDER BY 2 DESC")
    out += ["## Fulfilment: Amazon vs Merchant"]
    out += [f"- {r['fulfilment']}: {fmt_inr(r['rev'])} ({_pct(r['rev'], total):.2f}% of revenue), "
            f"{r['orders']:,} orders" for r in f]
    out.append("")
    p = db.records("SELECT has_promotion, COUNT(*) AS n, AVG(order_amount) AS avg_amt FROM sales GROUP BY 1")
    n_all = sum(r["n"] for r in p)
    out += ["## Promotions"]
    out += [f"- {'With' if r['has_promotion'] else 'Without'} promotion: {r['n']:,} lines "
            f"({_pct(r['n'], n_all):.2f}%), average line amount {fmt_inr(r['avg_amt'])}" for r in p]
    out.append("")

    out += ["## Data coverage",
            f"- {info.get('final_rows')} cleaned order lines from {info.get('date_min')} to {info.get('date_max')}. "
            f"{info.get('duplicates_removed')} duplicate lines and {info.get('march_rows_removed')} March lines were removed.",
            ""]
    return "\n".join(out)


def write_generated_docs(db: SalesDB, knowledge_dir: Path, log=print) -> list[Path]:
    gen = knowledge_dir / "generated"
    gen.mkdir(parents=True, exist_ok=True)
    files = {
        gen / "column_values.md": generate_column_values_doc(db),
        gen / "eda_insights.md": generate_insights_doc(db),
    }
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
        log(f"[knowledge] wrote {path.relative_to(knowledge_dir.parent)}")
    return list(files)
