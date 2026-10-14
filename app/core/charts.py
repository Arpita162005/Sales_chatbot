"""
Turns a DuckDB result into a Chart.js-ready spec.

The LLM only *suggests* a chart type; labels and values always come from the
query result, so the chart is exact. The frontend turns the spec into a
Chart.js config (see frontend/js/charts.js).
"""
from __future__ import annotations

from .analytics import QueryResult

VALID_TYPES = {"bar", "horizontalBar", "line", "pie", "doughnut"}
MAX_POINTS = 120
MAX_SERIES = 4


def _is_time_label(col: str, values: list) -> bool:
    name = col.lower()
    if any(t in name for t in ("date", "day", "week", "month", "time")):
        return True
    sample = [v for v in values[:5] if isinstance(v, str)]
    return bool(sample) and all(len(v) >= 10 and v[4] == "-" and v[7] == "-" for v in sample)


def _pretty(col: str) -> str:
    return col.replace("_pct", " %").replace("_", " ").strip().capitalize()


def build_chart(res: QueryResult, suggested: str | None, title: str | None) -> dict | None:
    if not res.rows or not res.columns:
        return None
    if suggested == "none":
        return None

    numeric = [c for c in res.numeric_columns]
    label_cols = [c for c in res.columns if c not in numeric]
    # A single row (one KPI or one group) reads better as text + table than as a chart.
    if res.row_count == 1 or not numeric:
        return None

    idx = {c: i for i, c in enumerate(res.columns)}
    if label_cols:
        label_col = label_cols[0]
        labels = [str(r[idx[label_col]]) for r in res.rows]
    else:
        # e.g. month_num + revenue: use the first numeric column as label.
        label_col = numeric[0]
        numeric = numeric[1:]
        labels = [str(r[idx[label_col]]) for r in res.rows]
        if not numeric:
            return None

    # Skip helper ordering columns.
    series_cols = [c for c in numeric if c not in ("month_num", "size_order", "rn", "isodow")]
    if not series_cols:
        return None

    # Avoid mixing very different scales (e.g. revenue in crores + a % column) on one axis.
    def magnitude(c: str) -> float:
        vals = [abs(r[idx[c]]) for r in res.rows if isinstance(r[idx[c]], (int, float))]
        return max(vals) if vals else 0.0

    # Prefer a money measure as the main series when the result has several measures.
    money = [c for c in series_cols if any(k in c.lower() for k in ("revenue", "amount", "sales", "aov"))]
    if money:
        series_cols = money[:1] + [c for c in series_cols if c != money[0]]
    main = series_cols[0]
    main_mag = magnitude(main) or 1.0
    series_cols = [c for c in series_cols if 0.05 <= (magnitude(c) or 0) / main_mag <= 20][:MAX_SERIES]

    is_time = _is_time_label(label_col, labels)
    ctype = suggested if suggested in VALID_TYPES else None
    if ctype is None:
        if is_time and len(labels) > 6:
            ctype = "line"
        elif len(labels) > 12 or max(len(x) for x in labels) > 14:
            ctype = "horizontalBar"
        else:
            ctype = "bar"
    longest = max(len(x) for x in labels)
    if ctype == "bar" and not is_time and len(labels) > 6 and longest > 8:
        ctype = "horizontalBar"   # rankings of states / cities / SKUs read better as rows
    if ctype in ("pie", "doughnut") and (len(labels) > 8 or len(series_cols) > 1):
        ctype = "bar"
    if ctype == "line" and len(labels) < 2:
        ctype = "bar"

    labels = labels[:MAX_POINTS]
    datasets = []
    for c in series_cols:
        data = [r[idx[c]] for r in res.rows][:MAX_POINTS]
        datasets.append({"label": _pretty(c), "key": c, "data": data})

    is_money = any(k in main.lower() for k in ("revenue", "amount", "aov", "value", "sales"))
    is_pct = main.lower().endswith("pct") or "rate" in main.lower() or "share" in main.lower()
    return {
        "type": ctype,
        "title": title or _pretty(main) + " by " + _pretty(label_col).lower(),
        "x_label": _pretty(label_col),
        "y_label": _pretty(main),
        "labels": labels,
        "datasets": datasets,
        "format": "inr" if is_money else ("pct" if is_pct else "number"),
        "truncated": res.row_count > MAX_POINTS,
    }
