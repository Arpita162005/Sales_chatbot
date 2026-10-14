"""
Sales analytics engine (DuckDB).

* `build_database()` turns the raw Amazon sales CSV into a clean `sales` table,
  reproducing the cleaning steps of the EDA notebook exactly (same duplicates
  rule, same fill values, same March removal) so every number the chatbot
  gives matches the notebook.
* `SalesDB` opens that database READ-ONLY with external file access disabled
  and runs validated SELECT queries with a row cap and a timeout. All numbers
  shown to the user come from here - the LLM never invents them.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import math
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

# --------------------------------------------------------------------------- #
# Build / cleaning
# --------------------------------------------------------------------------- #

SIZE_ORDER = ["Free", "XS", "S", "M", "L", "XL", "XXL", "3XL", "4XL", "5XL", "6XL"]

# State spellings found in the raw file -> canonical upper-case name.
STATE_ALIASES = {
    "ORISSA": "ODISHA",
    "RAJSHTHAN": "RAJASTHAN",
    "RAJSTHAN": "RAJASTHAN",
    "RJ": "RAJASTHAN",
    "PONDICHERRY": "PUDUCHERRY",
    "NEW DELHI": "DELHI",
    "PB": "PUNJAB",
    "PUNJAB/MOHALI/ZIRAKPUR": "PUNJAB",
    "NL": "NAGALAND",
    "AR": "ARUNACHAL PRADESH",
}

CANCELLED_STATUSES = ["Cancelled", "Shipped - Lost in Transit"]
RETURNED_STATUSES = [
    "Shipped - Returned to Seller",
    "Shipped - Returning to Seller",
    "Shipped - Rejected by Buyer",
    "Shipped - Damaged",
]
PENDING_STATUSES = ["Pending", "Pending - Waiting for Pick Up", "Shipping"]


def _sql_list(values: list[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _state_case() -> str:
    whens = " ".join(
        f"WHEN '{k}' THEN '{v}'" for k, v in STATE_ALIASES.items()
    )
    return (
        "CASE upper(trim(COALESCE(\"ship-state\", 'unknown'))) "
        f"{whens} ELSE upper(trim(COALESCE(\"ship-state\", 'unknown'))) END"
    )


def _size_case() -> str:
    whens = " ".join(f"WHEN '{s}' THEN {i + 1}" for i, s in enumerate(SIZE_ORDER))
    return f"CASE \"Size\" {whens} ELSE 99 END"


def build_database(csv_path: Path, db_path: Path, log=print) -> dict:
    """Create (or replace) the DuckDB file from the raw CSV. Returns build stats."""
    csv_path, db_path = Path(csv_path), Path(db_path)
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Sales CSV not found at {csv_path}. Copy Amazon_Sale_Report_1.csv into the data folder."
        )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(".building.duckdb")
    if tmp_path.exists():
        tmp_path.unlink()

    t0 = time.time()
    con = duckdb.connect(str(tmp_path))
    try:
        csv_sql = str(csv_path).replace("\\", "/").replace("'", "''")
        con.execute(
            f"""
            CREATE TEMP TABLE raw AS
            SELECT * FROM read_csv('{csv_sql}', header = true, all_varchar = true, quote = '"')
            """
        )
        raw_rows = con.execute("SELECT count(*) FROM raw").fetchone()[0]

        # Notebook step: drop_duplicates(['Order ID','ASIN']) keeping the first row.
        con.execute(
            f"""
            CREATE TABLE sales AS
            WITH dedup AS (
                SELECT *,
                       ROW_NUMBER() OVER (PARTITION BY "Order ID", "ASIN"
                                          ORDER BY TRY_CAST("index" AS BIGINT)) AS _rn
                FROM raw
            ), typed AS (
                SELECT
                    TRY_CAST("index" AS BIGINT)                                   AS row_id,
                    "Order ID"                                                    AS order_id,
                    COALESCE(try_strptime("Date", '%m-%d-%y'),
                             try_strptime("Date", '%m-%d-%Y'))::DATE              AS order_date,
                    "Status"                                                      AS ship_status,
                    "Fulfilment"                                                  AS fulfilment,
                    "ship-service-level"                                          AS service_level,
                    "Style"                                                       AS style,
                    "SKU"                                                         AS sku,
                    "Category"                                                    AS product_category,
                    "Size"                                                        AS size,
                    {_size_case()}                                                AS size_order,
                    "ASIN"                                                        AS asin,
                    COALESCE("Courier Status", 'unknown')                         AS courier_ship_status,
                    COALESCE(TRY_CAST("Qty" AS INTEGER), 0)                       AS order_quantity,
                    COALESCE(TRY_CAST("Amount" AS DOUBLE), 0)                     AS order_amount,
                    upper(trim(COALESCE("ship-city", 'unknown')))                 AS city,
                    {_state_case()}                                               AS state,
                    COALESCE("ship-state", 'unknown')                             AS state_raw,
                    COALESCE(regexp_replace("ship-postal-code", '\\.0$', ''), 'unknown') AS zip,
                    COALESCE("promotion-ids", 'no promotion')                     AS promotion,
                    CASE WHEN upper("B2B") = 'TRUE' THEN 'business' ELSE 'customer' END AS customer_type
                FROM dedup
                WHERE _rn = 1
            )
            SELECT
                row_id, order_id, order_date,
                CAST(month(order_date) AS INTEGER)            AS month_num,
                monthname(order_date)                         AS month_name,
                ship_status,
                CASE
                    WHEN ship_status IN ({_sql_list(CANCELLED_STATUSES)}) THEN 'Cancelled'
                    WHEN ship_status IN ({_sql_list(RETURNED_STATUSES)})  THEN 'Returned'
                    WHEN ship_status IN ({_sql_list(PENDING_STATUSES)})   THEN 'Pending'
                    ELSE 'Shipped/Delivered'
                END                                           AS status_group,
                fulfilment, service_level, style, sku, product_category,
                size, size_order, asin, courier_ship_status,
                order_quantity, order_amount,
                city, state, state_raw, zip, promotion,
                promotion <> 'no promotion'                   AS has_promotion,
                customer_type
            FROM typed
            """
        )
        after_dedup = con.execute("SELECT count(*) FROM sales").fetchone()[0]

        # Notebook step: March has a single day of data, so it is dropped.
        march_rows = con.execute("SELECT count(*) FROM sales WHERE month_num = 3").fetchone()[0]
        con.execute("DELETE FROM sales WHERE month_num = 3")
        final_rows = con.execute("SELECT count(*) FROM sales").fetchone()[0]
        dmin, dmax = con.execute("SELECT min(order_date), max(order_date) FROM sales").fetchone()

        con.execute(
            """
            CREATE TABLE dataset_info (
                key VARCHAR, value VARCHAR
            )
            """
        )
        info = {
            "source_file": csv_path.name,
            "raw_rows": raw_rows,
            "duplicates_removed": raw_rows - after_dedup,
            "march_rows_removed": march_rows,
            "final_rows": final_rows,
            "date_min": str(dmin),
            "date_max": str(dmax),
            "built_at": _dt.datetime.now().isoformat(timespec="seconds"),
        }
        con.executemany("INSERT INTO dataset_info VALUES (?, ?)", [(k, str(v)) for k, v in info.items()])
        con.execute("CHECKPOINT")
    finally:
        con.close()

    if db_path.exists():
        db_path.unlink()
    tmp_path.replace(db_path)
    wal = tmp_path.with_suffix(tmp_path.suffix + ".wal")
    if wal.exists():
        wal.unlink()
    info["seconds"] = round(time.time() - t0, 2)
    log(f"[analytics] built {db_path.name}: {final_rows:,} rows in {info['seconds']}s")
    return info


# --------------------------------------------------------------------------- #
# SQL safety
# --------------------------------------------------------------------------- #

class UnsafeSQLError(ValueError):
    """Raised when generated SQL is not a single read-only SELECT."""


_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|create|alter|copy|attach|detach|pragma|install|load|"
    r"export|import|call|set|reset|truncate|vacuum|checkpoint|begin|commit|rollback|grant|revoke|use)\b",
    re.IGNORECASE,
)
_FORBIDDEN_FUNCS = re.compile(
    r"\b(read_csv\w*|read_parquet|read_json\w*|read_text|read_blob|read_xlsx|parquet_scan|csv_scan|"
    r"sniff_csv|glob|query|query_table|getenv|duckdb_\w+|pragma_\w+|iceberg_\w+|delta_scan|"
    r"sqlite_\w+|postgres_\w+|mysql_\w+)\s*\(",
    re.IGNORECASE,
)


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def _strip_literals(sql: str) -> str:
    """Blank out '...' strings and "..." identifiers so keywords inside them are ignored."""
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    sql = re.sub(r'"(?:[^"]|"")*"', '""', sql)
    return sql


def clean_sql(sql: str) -> str:
    """Remove markdown fences / trailing semicolons the LLM may add."""
    sql = sql.strip()
    fence = re.search(r"```(?:sql)?\s*(.*?)```", sql, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        sql = fence.group(1)
    sql = _strip_comments(sql).strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    return sql


def validate_sql(sql: str) -> str:
    """Return the cleaned SQL or raise UnsafeSQLError."""
    sql = clean_sql(sql)
    if not sql:
        raise UnsafeSQLError("Empty SQL.")
    bare = _strip_literals(sql)
    if ";" in bare:
        raise UnsafeSQLError("Only one SQL statement is allowed.")
    if not re.match(r"^\s*(select|with)\b", bare, re.IGNORECASE):
        raise UnsafeSQLError("Only SELECT / WITH queries are allowed.")
    m = _FORBIDDEN_KEYWORDS.search(bare)
    if m:
        raise UnsafeSQLError(f"Keyword not allowed in read-only analytics: {m.group(1).upper()}")
    m = _FORBIDDEN_FUNCS.search(bare)
    if m:
        raise UnsafeSQLError(f"Function not allowed: {m.group(1)}")
    return sql


# --------------------------------------------------------------------------- #
# Query execution
# --------------------------------------------------------------------------- #

def _json_safe(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else round(v, 4)
    if isinstance(v, decimal.Decimal):
        return round(float(v), 4)
    if isinstance(v, (_dt.date, _dt.datetime, _dt.time)):
        return v.isoformat()
    if isinstance(v, _dt.timedelta):
        return str(v)
    if isinstance(v, (int, str)):
        return v
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    return str(v)


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    types: list[str]
    rows: list[list[Any]]
    truncated: bool = False
    elapsed_ms: float = 0.0
    numeric_columns: list[str] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_dict(self, max_rows: int | None = None) -> dict:
        rows = self.rows if max_rows is None else self.rows[:max_rows]
        return {
            "sql": self.sql,
            "columns": self.columns,
            "types": self.types,
            "rows": rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "numeric_columns": self.numeric_columns,
        }


_NUMERIC_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT",
                  "UINTEGER", "UBIGINT", "FLOAT", "DOUBLE", "DECIMAL", "REAL")


class SalesDB:
    """Read-only access to the cleaned sales database."""

    def __init__(self, db_path: Path, max_rows: int = 500, timeout_s: float = 15.0):
        self.db_path = Path(db_path)
        self.max_rows = max_rows
        self.timeout_s = timeout_s
        self._con = duckdb.connect(
            str(self.db_path),
            read_only=True,
            config={"enable_external_access": "false"},
        )
        self._lock = threading.Lock()
        self._overview_cache: dict | None = None

    def close(self) -> None:
        self._con.close()

    # ---- low level -------------------------------------------------------
    def query(self, sql: str, params: list | None = None, limit: int | None = None,
              validate: bool = True) -> QueryResult:
        sql = validate_sql(sql) if validate else sql
        cap = self.max_rows if limit is None else limit
        wrapped = f"SELECT * FROM ({sql}) AS _q LIMIT {cap + 1}"
        cur = self._con.cursor()
        timer = threading.Timer(self.timeout_s, cur.interrupt)
        t0 = time.perf_counter()
        timer.start()
        try:
            rel = cur.execute(wrapped, params or [])
            raw_rows = rel.fetchall()
            desc = rel.description or []
        except duckdb.InterruptException as exc:
            raise TimeoutError(f"Query exceeded {self.timeout_s}s and was stopped.") from exc
        finally:
            timer.cancel()
            cur.close()
        elapsed = (time.perf_counter() - t0) * 1000
        columns = [d[0] for d in desc]
        types = [str(d[1]) for d in desc]
        truncated = len(raw_rows) > cap
        rows = [[_json_safe(v) for v in r] for r in raw_rows[:cap]]
        numeric = [c for c, t in zip(columns, types) if t.upper().startswith(_NUMERIC_TYPES)]
        return QueryResult(sql, columns, types, rows, truncated, elapsed, numeric)

    def scalar(self, sql: str, params: list | None = None) -> Any:
        res = self.query(sql, params, limit=1, validate=False)
        return res.rows[0][0] if res.rows else None

    def records(self, sql: str, params: list | None = None) -> list[dict]:
        res = self.query(sql, params, validate=False)
        return [dict(zip(res.columns, r)) for r in res.rows]

    # ---- metadata --------------------------------------------------------
    def row_count(self) -> int:
        return int(self.scalar("SELECT count(*) FROM sales"))

    def dataset_info(self) -> dict:
        return {r["key"]: r["value"] for r in self.records("SELECT key, value FROM dataset_info")}

    def columns(self) -> list[tuple[str, str]]:
        res = self.query("DESCRIBE sales", validate=False)
        return [(r[0], r[1]) for r in res.rows]

    def distinct_values(self, column: str, limit: int = 40) -> list[Any]:
        res = self.query(
            f'SELECT "{column}" AS v, count(*) AS n FROM sales GROUP BY 1 ORDER BY n DESC LIMIT {limit}',
            validate=False,
        )
        return [r[0] for r in res.rows]

    # ---- dashboard -------------------------------------------------------
    def overview(self) -> dict:
        """KPI cards + starter charts for the welcome screen (cached)."""
        if self._overview_cache is not None:
            return self._overview_cache
        k = self.records(
            f"""
            SELECT
                sum(order_amount)                                   AS revenue,
                count(DISTINCT order_id)                            AS orders,
                sum(order_quantity)                                 AS units,
                sum(order_amount) / count(DISTINCT order_id)        AS aov,
                100.0 * count(*) FILTER (WHERE status_group = 'Cancelled') / count(*) AS cancel_rate,
                100.0 * count(*) FILTER (WHERE status_group = 'Returned')  / count(*) AS return_rate,
                min(order_date) AS date_min, max(order_date) AS date_max
            FROM sales
            """
        )[0]
        by_month = self.query(
            "SELECT month_name AS month, round(sum(order_amount), 2) AS revenue "
            "FROM sales GROUP BY month_num, month_name ORDER BY month_num",
            validate=False,
        )
        by_cat = self.query(
            "SELECT product_category, round(sum(order_amount), 2) AS revenue "
            "FROM sales GROUP BY 1 ORDER BY 2 DESC",
            validate=False,
        )
        daily = self.query(
            "SELECT order_date, round(sum(order_amount), 2) AS revenue "
            "FROM sales GROUP BY 1 ORDER BY 1",
            validate=False,
        )
        top_states = self.query(
            "SELECT state, round(sum(order_amount), 2) AS revenue "
            "FROM sales GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
            validate=False,
        )
        monthly = self.records(
            """
            SELECT month_num, month_name AS month,
                   sum(order_amount)                                   AS revenue,
                   count(DISTINCT order_id)                            AS orders,
                   sum(order_quantity)                                 AS units,
                   sum(order_amount) / count(DISTINCT order_id)        AS aov,
                   100.0 * count(*) FILTER (WHERE status_group = 'Cancelled') / count(*) AS cancel_rate,
                   100.0 * count(*) FILTER (WHERE status_group = 'Returned')  / count(*) AS return_rate
            FROM sales GROUP BY month_num, month_name ORDER BY month_num
            """
        )
        self._overview_cache = {
            "kpis": k,
            "monthly": monthly,
            "charts": {
                "revenue_by_month": by_month.to_dict(),
                "revenue_by_category": by_cat.to_dict(),
                "daily_revenue": daily.to_dict(),
                "top_states": top_states.to_dict(),
            },
        }
        return self._overview_cache


# --------------------------------------------------------------------------- #
# Formatting helpers (used in prompts and generated docs)
# --------------------------------------------------------------------------- #

def fmt_inr(x: float | int | None, decimals: int = 2) -> str:
    """Indian digit grouping: 28838708.32 -> ₹2,88,38,708.32"""
    if x is None:
        return "-"
    neg = x < 0
    x = abs(float(x))
    whole, _, frac = f"{x:.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    s = "₹" + whole + (("." + frac) if decimals else "")
    return "-" + s if neg else s


def fmt_num(x: Any) -> str:
    """Readable number for prompts: thousands separators, 2 decimals for floats."""
    if isinstance(x, bool) or x is None:
        return str(x)
    if isinstance(x, int):
        return f"{x:,}"
    if isinstance(x, float):
        if x.is_integer() and abs(x) >= 1000:
            return f"{int(x):,}"
        return f"{x:,.2f}"
    return str(x)


def result_as_text(res: QueryResult, max_rows: int = 40) -> str:
    """Compact pipe table for the LLM prompt (numbers pre-formatted, exact)."""
    if not res.columns:
        return "(no columns)"
    lines = [" | ".join(res.columns)]
    for row in res.rows[:max_rows]:
        lines.append(" | ".join(fmt_num(v) for v in row))
    if res.row_count > max_rows:
        lines.append(f"... ({res.row_count - max_rows} more rows not shown)")
    if res.truncated:
        lines.append("(result was capped at the row limit)")
    return "\n".join(lines)
