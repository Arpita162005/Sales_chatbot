"""
Core tests - no GPU or Ollama needed.

    python -m pytest -q

The analytics tests need data/Amazon_Sale_Report_1.csv and are skipped without it.
They check that the DuckDB cleaning reproduces the EDA notebook's figures exactly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.analytics import (QueryResult, SalesDB, UnsafeSQLError, build_database,  # noqa: E402
                                fmt_inr, validate_sql)
from app.core.charts import build_chart  # noqa: E402
from app.core.knowledge import load_sql_examples  # noqa: E402
from app.core.llm import ThinkFilter, strip_think  # noqa: E402
from app.core.router import QueryRouter  # noqa: E402

CSV = ROOT / "data" / "Amazon_Sale_Report_1.csv"


# --------------------------------------------------------------------------- #
# SQL guard
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sql", [
    "DROP TABLE sales",
    "SELECT 1; DROP TABLE sales",
    "DELETE FROM sales",
    "COPY sales TO 'out.csv'",
    "SELECT * FROM read_csv('secret.csv')",
    "SELECT * FROM query('DROP TABLE sales')",
    "ATTACH 'other.db'",
    "WITH x AS (SELECT 1) INSERT INTO sales SELECT * FROM x",
    "PRAGMA database_list",
    "SELECT getenv('PATH')",
])
def test_unsafe_sql_rejected(sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql)


def test_safe_sql_cleaned():
    raw = "```sql\nSELECT * FROM sales WHERE product_category = 'Set';\n```"
    assert validate_sql(raw) == "SELECT * FROM sales WHERE product_category = 'Set'"
    # keywords inside string literals are fine
    assert validate_sql("SELECT count(*) FROM sales WHERE ship_status = 'Cancelled; drop'")


# --------------------------------------------------------------------------- #
# Formatting / LLM helpers
# --------------------------------------------------------------------------- #
def test_fmt_inr_indian_grouping():
    assert fmt_inr(28838708.32) == "₹2,88,38,708.32"
    assert fmt_inr(915) == "₹915.00"
    assert fmt_inr(123456.5) == "₹1,23,456.50"


def test_strip_think():
    assert strip_think("<think>plan</think>Answer") == "Answer"
    assert strip_think("Answer<think>unfinished") == "Answer"


def test_think_filter_streaming():
    f = ThinkFilter()
    pieces = ["<th", "ink>secret rea", "soning</th", "ink>Hello", " world"]
    out = "".join(f.feed(p) for p in pieces) + f.flush()
    assert out == "Hello world"


# --------------------------------------------------------------------------- #
# Router rules
# --------------------------------------------------------------------------- #
def test_router_rule_scores():
    a = QueryRouter.rule_scores("Top 10 states by revenue")
    assert a["analytics"] > a["knowledge"]
    k = QueryRouter.rule_scores("How is AOV calculated? explain the definition")
    assert k["knowledge"] > k["analytics"]
    g = QueryRouter.rule_scores("hello")
    assert g["general"] == 1.0


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #
def _res(cols, types, rows, numeric):
    return QueryResult("SELECT 1", cols, types, rows, numeric_columns=numeric)


def test_chart_single_row_is_kpi():
    r = _res(["total_revenue"], ["DOUBLE"], [[100.0]], ["total_revenue"])
    assert build_chart(r, "bar", "x") is None


def test_chart_months_bar_and_scale_filter():
    r = _res(["month", "revenue", "orders"], ["VARCHAR", "DOUBLE", "BIGINT"],
             [["April", 2.8e7, 45000], ["May", 2.6e7, 39000], ["June", 2.3e7, 35000]],
             ["revenue", "orders"])
    c = build_chart(r, "bar", "Revenue by month")
    assert c["type"] == "bar" and c["labels"] == ["April", "May", "June"]
    assert [d["key"] for d in c["datasets"]] == ["revenue"]          # orders dropped: different scale
    assert c["format"] == "inr"


def test_chart_long_labels_become_rows():
    rows = [[f"STATE NUMBER {i}", 1000.0 * (10 - i)] for i in range(10)]
    r = _res(["state", "revenue"], ["VARCHAR", "DOUBLE"], rows, ["revenue"])
    assert build_chart(r, "bar", None)["type"] == "horizontalBar"


# --------------------------------------------------------------------------- #
# Analytics on the real data (matches the EDA notebook)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def db(tmp_path_factory):
    if not CSV.exists():
        pytest.skip("data/Amazon_Sale_Report_1.csv not present")
    path = tmp_path_factory.mktemp("db") / "test.duckdb"
    build_database(CSV, path, log=lambda *_: None)
    d = SalesDB(path)
    yield d
    d.close()


def test_notebook_monthly_revenue(db):
    rows = db.query("SELECT month_name, ROUND(SUM(order_amount), 2) FROM sales "
                    "GROUP BY month_num, month_name ORDER BY month_num").rows
    assert rows == [["April", 28838708.32], ["May", 26224517.75], ["June", 23424646.38]]


def test_notebook_category_and_status(db):
    top = db.query("SELECT product_category, ROUND(SUM(order_amount), 2) r FROM sales "
                   "GROUP BY 1 ORDER BY r DESC LIMIT 2").rows
    assert top == [["Set", 39148138.03], ["kurta", 21264270.13]]
    canc, ret = db.query("SELECT COUNT(*) FILTER (WHERE status_group='Cancelled'), "
                         "COUNT(*) FILTER (WHERE status_group='Returned') FROM sales").rows[0]
    assert (canc, ret) == (18316, 2109)


def test_notebook_customer_type(db):
    rows = dict(db.query("SELECT customer_type, ROUND(AVG(order_amount), 2) FROM sales GROUP BY 1").rows)
    assert rows == {"business": 678.78, "customer": 608.92}


def test_all_sql_examples_run(db):
    examples = load_sql_examples(ROOT / "knowledge")
    assert len(examples) >= 20
    for ex in examples:
        res = db.query(ex.sql)
        assert res.row_count > 0, ex.title


def test_database_is_read_only(db):
    with pytest.raises(Exception):
        db._con.execute("CREATE TABLE hack AS SELECT 1")
    with pytest.raises(Exception):
        db._con.execute(f"SELECT * FROM read_csv('{CSV.as_posix()}') LIMIT 1")
