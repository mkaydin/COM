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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
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


@app.get("/api/providers")
async def list_providers():
    """List all available LLM providers and which is active."""
    from config import PROVIDERS, get_settings
    s = get_settings()
    return {
        "active": s.active_provider,
        "providers": {
            key: {
                "label": p["label"],
                "model": p["llm_model"],
                "base": p["llm_api_base"],
                "supports_json_mode": p["supports_json_mode"],
            }
            for key, p in PROVIDERS.items()
        },
    }


from pydantic import BaseModel


class ProviderSwitchRequest(BaseModel):
    provider: str


@app.post("/api/providers/switch")
async def switch_provider_endpoint(req: ProviderSwitchRequest):
    """Switch the active LLM provider at runtime."""
    from config import switch_provider as do_switch, PROVIDERS
    from fastapi import HTTPException
    if req.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}. Available: {list(PROVIDERS.keys())}")
    p = do_switch(req.provider)
    return {"status": "switched", "provider": req.provider, "model": p["llm_model"]}
