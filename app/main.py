"""
FastAPI application.

Run:  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
 or:  python run.py

Endpoints
  GET  /                    chat UI (frontend/)
  GET  /api/health          Ollama / model / VRAM / embeddings / FAISS / DuckDB status
  GET  /api/overview        KPI cards + starter charts for the welcome screen
  GET  /api/suggestions     example questions
  POST /api/chat            streamed answer (NDJSON events)
  POST /api/chat/sync       full answer as one JSON object
  POST /api/sql             run a read-only SQL query (if ENABLE_SQL_CONSOLE=true)
  GET  /docs                interactive API docs (Swagger)
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .core.analytics import UnsafeSQLError
from .core.engine import SUGGESTION_GROUPS, SUGGESTIONS, ChatEngine
from .schemas import ChatRequest, SQLRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting %s ...", settings.app_title)
    engine = await asyncio.to_thread(ChatEngine, settings)
    app.state.engine = engine
    st = await engine.status()
    llm = st["llm"]
    if not llm["reachable"]:
        log.warning("Ollama is not reachable at %s - start it with `ollama serve`.", settings.ollama_url)
    elif not llm["model_installed"]:
        log.warning("Model %s is not pulled - run `ollama pull %s`.", settings.llm_model, settings.llm_model)
    else:
        # Load Qwen3 into VRAM in the background so the first question is fast.
        asyncio.create_task(_warmup(engine))
    log.info("Ready: %s rows | embeddings=%s | vector search=%s | open http://%s:%s",
             f"{st['database']['rows']:,}", st["embeddings"]["signature"], st["vector_store"]["engine"],
             settings.host, settings.port)
    yield
    await engine.aclose()


async def _warmup(engine: ChatEngine) -> None:
    try:
        await engine.llm.warmup()
        log.info("Model %s loaded into memory.", settings.llm_model)
    except Exception as exc:  # noqa: BLE001
        log.warning("Warm-up failed: %s", exc)


app = FastAPI(title=settings.app_title, version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


def _engine(request: Request) -> ChatEngine:
    return request.app.state.engine


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

@app.get("/api/health")
async def health(request: Request):
    return await _engine(request).status()


@app.get("/api/overview")
async def overview(request: Request):
    return await asyncio.to_thread(_engine(request).db.overview)


@app.get("/api/suggestions")
async def suggestions():
    return {"suggestions": SUGGESTIONS, "groups": SUGGESTION_GROUPS}


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    engine = _engine(request)
    history = [h.model_dump() for h in req.history]

    async def gen():
        async for event in engine.stream(req.message, history, req.route):
            if await request.is_disconnected():
                break
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/chat/sync")
async def chat_sync(req: ChatRequest, request: Request):
    history = [h.model_dump() for h in req.history]
    return await _engine(request).answer(req.message, history, req.route)


@app.post("/api/sql")
async def run_sql(req: SQLRequest, request: Request):
    if not settings.enable_sql_console:
        raise HTTPException(403, "SQL console is disabled (ENABLE_SQL_CONSOLE=false).")
    try:
        res = await asyncio.to_thread(_engine(request).db.query, req.sql, None, req.limit)
    except UnsafeSQLError as exc:
        raise HTTPException(400, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(408, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc).split("\n")[0]) from exc
    return res.to_dict()


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


# --------------------------------------------------------------------------- #
# Frontend (served last so /api/* wins)
# --------------------------------------------------------------------------- #

@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(settings.frontend_dir / "index.html")


app.mount("/", StaticFiles(directory=str(settings.frontend_dir), html=True), name="frontend")
