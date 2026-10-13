from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import get_settings
from logging_config import setup_logging, get_logger
from db.postgres import init_db
from api.sessions import router as sessions_router
from api.documents import router as documents_router
from api.deliberations import router as deliberations_router
from api.verdicts import router as verdicts_router
from api.agents import router as agents_router
from api.websocket import router as websocket_router
from api.wiki import router as wiki_router
from api.divination import router as divination_router
from api.okf import router as okf_router
from api.rag import router as rag_router

setup_logging()
logger = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("backend_starting", port=settings.backend_port)

    try:
        await init_db()
        logger.info("database_initialized")
    except Exception as e:
        logger.error("database_init_failed", error=str(e))

    yield

    logger.info("backend_shutting_down")


app = FastAPI(
    title="Council of Minds API",
    version="1.0.0",
    lifespan=lifespan,
)

# The Council is a local desktop app: the backend binds to loopback and the UI
# is loaded via file:// or from a local dev server. `allow_origins=["*"]` with
# `allow_credentials=True` is rejected by browsers anyway, and would open the
# API to any origin if it were not.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?|null|file://.*)$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(sessions_router)
app.include_router(documents_router)
app.include_router(deliberations_router)
app.include_router(verdicts_router)
app.include_router(agents_router)
app.include_router(wiki_router)
app.include_router(divination_router)
app.include_router(okf_router)
app.include_router(rag_router)
app.include_router(websocket_router)


@app.get("/api/health")
async def health():
    from ollama_client import llm_health

    llm = await llm_health()
    checks = {
        "status": "ok" if llm["ok"] else "degraded",
        "configured": llm["ok"],
        "llm_model": settings.llm_model,
        "llm_ok": llm["ok"],
        "ollama_reachable": llm["reachable"],
        "model_present": llm["model_present"],
        "hint": llm["hint"],
        "version": "1.0.0",
    }

    try:
        from db.postgres import engine
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
        checks["postgres_ok"] = True
    except Exception as e:
        checks["postgres_ok"] = False
        checks["postgres_error"] = str(e)

    return checks


@app.get("/api/health/ready")
async def ready():
    checks = await health()
    all_ok = checks.get("postgres_ok", False) and checks.get("llm_ok", False)
    return {"ready": all_ok, "checks": checks}


@app.get("/api/ollama/status")
async def ollama_status():
    """Installed models on the local Ollama server and whether ours is present."""
    from ollama_client import ollama_status as _status
    return await _status()

