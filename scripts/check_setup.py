"""
Pre-flight check: Python, packages, GPU, Ollama, model, data.

    python scripts/check_setup.py
"""
from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

OK, WARN, FAIL = "[ OK ]", "[WARN]", "[FAIL]"


def check_pkg(name: str, required: bool = True) -> bool:
    try:
        mod = importlib.import_module(name)
        print(f"{OK} {name} {getattr(mod, '__version__', '')}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL if required else WARN} {name}: {type(exc).__name__}: {str(exc)[:120]}")
        return False


def main() -> None:
    print("== Python")
    v = sys.version_info
    print(f"{OK if v >= (3, 10) else FAIL} Python {v.major}.{v.minor}.{v.micro} ({sys.executable})")

    print("\n== Packages")
    for p in ("fastapi", "uvicorn", "httpx", "duckdb", "numpy", "pydantic"):
        check_pkg(p)
    if not check_pkg("faiss", required=False):
        print("       -> vector search will use the NumPy fallback (same results, fine for this size).")
    st = check_pkg("sentence_transformers", required=False)
    torch_ok = check_pkg("torch", required=False)
    if torch_ok:
        import torch
        print(f"       torch CUDA available: {torch.cuda.is_available()}"
              + (f" ({torch.cuda.get_device_name(0)})" if torch.cuda.is_available() else ""))
    if not st:
        print(f"       -> embeddings will use Ollama '{settings.ollama_embed_model}' "
              f"(run: ollama pull {settings.ollama_embed_model})")

    print("\n== GPU")
    if shutil.which("nvidia-smi"):
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version",
                              "--format=csv,noheader"], capture_output=True, text=True)
        print(f"{OK} {out.stdout.strip()}")
    else:
        print(f"{WARN} nvidia-smi not found (Ollama may still use the GPU)")

    print("\n== Ollama")
    import httpx
    try:
        ver = httpx.get(f"{settings.ollama_url}/api/version", timeout=3).json().get("version")
        print(f"{OK} Ollama {ver} at {settings.ollama_url}")
        tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3).json().get("models", [])
        names = sorted(m["name"] for m in tags)
        for want in (settings.llm_model, settings.ollama_embed_model):
            full = want if ":" in want else want + ":latest"
            has = full in names or want in names
            needed = want == settings.llm_model or not st
            tag = OK if has else (FAIL if needed else WARN)
            print(f"{tag} model {want}: {'installed' if has else 'missing -> ollama pull ' + want}")
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} Ollama not reachable at {settings.ollama_url}: {exc}\n"
              "       -> install from https://ollama.com and run `ollama serve`")

    print("\n== Data")
    print(f"{OK if settings.data_csv.exists() else FAIL} CSV: {settings.data_csv}")
    print(f"{OK if settings.duckdb_path.exists() else WARN} DuckDB: {settings.duckdb_path}"
          + ("" if settings.duckdb_path.exists() else "  (built on first run / scripts/ingest.py)"))
    man = settings.index_dir / "manifest.json"
    print(f"{OK if man.exists() else WARN} FAISS indexes: {settings.index_dir}"
          + ("" if man.exists() else "  (built on first run / scripts/ingest.py)"))


if __name__ == "__main__":
    main()
