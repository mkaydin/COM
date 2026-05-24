from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import get_settings
from logging_config import setup_logging, get_logger
from db.postgres import init_db
from api.sessions import router as sessions_router
from api.documents import router as documents_router

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions_router)
app.include_router(documents_router)


@app.get("/api/health")
async def health():
    checks = {
        "status": "ok",
        "configured": bool(settings.llm_api_key),
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "llm_ok": bool(settings.llm_api_key),
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
    all_ok = checks.get("postgres_ok", False)
    return {"ready": all_ok, "checks": checks}
