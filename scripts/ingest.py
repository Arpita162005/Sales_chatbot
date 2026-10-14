"""
Build everything the chatbot needs, in order:
  1. DuckDB database from the CSV (cleaning identical to the EDA notebook)
  2. Data-derived knowledge docs (knowledge/generated/*.md)
  3. FAISS indexes: docs, sql_examples, router

Usage (from the project folder):
    python scripts/ingest.py            # build what is missing / out of date
    python scripts/ingest.py --force    # rebuild everything
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.core.analytics import SalesDB, fmt_inr  # noqa: E402
from app.core.embeddings import Embedder  # noqa: E402
from app.core.engine import ensure_database, ensure_indexes  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="rebuild database and indexes")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    print(f"CSV      : {settings.data_csv}")
    print(f"DuckDB   : {settings.duckdb_path}")
    ensure_database(settings, force=args.force)

    db = SalesDB(settings.duckdb_path)
    k = db.overview()["kpis"]
    print(f"Rows     : {db.row_count():,}  ({k['date_min']} .. {k['date_max']})")
    print(f"Revenue  : {fmt_inr(k['revenue'])}   Orders: {k['orders']:,}   AOV: {fmt_inr(k['aov'])}")
    db.close()

    emb = Embedder(settings.embed_backend, settings.embed_model, settings.embed_device,
                   settings.ollama_url, settings.ollama_embed_model)
    print(f"Embedder : {emb.signature} on {emb.device}")
    stores = ensure_indexes(settings, emb, force=args.force)
    for name, s in stores.items():
        print(f"Index    : {name:<13} {len(s):>4} vectors  ({s.engine})")
    print("Done. Start the app with:  python run.py")


if __name__ == "__main__":
    main()
