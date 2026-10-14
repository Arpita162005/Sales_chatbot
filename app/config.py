"""
Central configuration.

Every value can be overridden with an environment variable or a line in the
project's `.env` file (copy `.env.example` to `.env`). No extra packages are
needed to read the .env file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env reader (KEY=VALUE per line, # comments). Real env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def _env(key: str, default: str) -> str:
    return os.getenv(key, default)


def _env_bool(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _path(key: str, default: str) -> Path:
    p = Path(_env(key, default))
    return p if p.is_absolute() else BASE_DIR / p


@dataclass(frozen=True)
class Settings:
    # ---- Server -------------------------------------------------------------
    host: str = _env("HOST", "127.0.0.1")
    port: int = _env_int("PORT", 8000)
    app_title: str = _env("APP_TITLE", "Sales Insight Chatbot")

    # ---- Data ---------------------------------------------------------------
    data_csv: Path = _path("DATA_CSV", "data/Amazon_Sale_Report_1.csv")
    duckdb_path: Path = _path("DUCKDB_PATH", "storage/sales.duckdb")
    knowledge_dir: Path = _path("KNOWLEDGE_DIR", "knowledge")
    index_dir: Path = _path("INDEX_DIR", "storage/index")
    frontend_dir: Path = _path("FRONTEND_DIR", "frontend")

    # ---- LLM (Ollama) -------------------------------------------------------
    ollama_url: str = _env("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    # Qwen3 8B, Q4_K_M quantisation (~5.2 GB). Pull with: ollama pull qwen3:8b-q4_K_M
    llm_model: str = _env("LLM_MODEL", "qwen3:8b-q4_K_M")
    llm_num_ctx: int = _env_int("LLM_NUM_CTX", 8192)
    llm_temperature: float = _env_float("LLM_TEMPERATURE", 0.3)
    llm_sql_temperature: float = _env_float("LLM_SQL_TEMPERATURE", 0.05)
    llm_max_tokens: int = _env_int("LLM_MAX_TOKENS", 900)
    # Qwen3 "thinking" mode: slower but sometimes better SQL. Off by default.
    llm_think: bool = _env_bool("LLM_THINK", False)
    llm_keep_alive: str = _env("LLM_KEEP_ALIVE", "30m")
    llm_timeout_s: float = _env_float("LLM_TIMEOUT_S", 180.0)

    # ---- Embeddings / vector search ----------------------------------------
    # auto | sentence-transformers | ollama
    embed_backend: str = _env("EMBED_BACKEND", "auto").lower()
    embed_model: str = _env("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    # The same all-MiniLM-L6-v2 model packaged for Ollama (fallback backend).
    ollama_embed_model: str = _env("OLLAMA_EMBED_MODEL", "all-minilm")
    # auto | cpu | cuda   (auto = cuda when torch sees the GPU)
    embed_device: str = _env("EMBED_DEVICE", "auto").lower()
    top_k_docs: int = _env_int("TOP_K_DOCS", 4)
    top_k_examples: int = _env_int("TOP_K_EXAMPLES", 4)

    # ---- Router -------------------------------------------------------------
    router_llm_fallback: bool = _env_bool("ROUTER_LLM_FALLBACK", True)
    router_min_confidence: float = _env_float("ROUTER_MIN_CONFIDENCE", 0.55)

    # ---- SQL safety ---------------------------------------------------------
    sql_max_rows: int = _env_int("SQL_MAX_ROWS", 500)
    sql_timeout_s: float = _env_float("SQL_TIMEOUT_S", 15.0)
    sql_retries: int = _env_int("SQL_RETRIES", 2)
    rows_to_llm: int = _env_int("ROWS_TO_LLM", 40)
    rows_to_ui: int = _env_int("ROWS_TO_UI", 100)
    enable_sql_console: bool = _env_bool("ENABLE_SQL_CONSOLE", True)

    # ---- Conversation -------------------------------------------------------
    history_turns: int = _env_int("HISTORY_TURNS", 6)

    extra: dict = field(default_factory=dict)


settings = Settings()
