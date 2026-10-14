#!/usr/bin/env bash
# One-time setup for Linux / macOS.  ./setup.sh   (CPU torch: ./setup.sh --cpu, no torch: ./setup.sh --no-torch)
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
[ -d .venv ] || $PY -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt
case "${1:-}" in
  --no-torch) sed 's/^EMBED_BACKEND=.*/EMBED_BACKEND=ollama/' .env.example > .env ;;
  --cpu) python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
         python -m pip install -r requirements-embeddings.txt ;;
  *)     python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
         python -m pip install -r requirements-embeddings.txt ;;
esac
[ -f .env ] || cp .env.example .env
if command -v ollama >/dev/null; then ollama pull qwen3:8b-q4_K_M; ollama pull all-minilm; fi
python scripts/ingest.py
python scripts/check_setup.py
echo "Start with: ./run.sh  (http://127.0.0.1:8000)"
