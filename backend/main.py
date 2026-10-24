import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path


# ======================================================================================
# `python main.py`
#
# This module only *defines* the app, so running it directly used to import
# everything and exit silently. It now starts the server — and because the
# likeliest reason someone reaches for it is that they are not in the venv, it
# says so plainly instead of dumping a bare ModuleNotFoundError.
#
# The check is above the imports on purpose: everything below is a top-level
# `from fastapi import ...`, so a check further down would run after the very
# failure it exists to explain.
# ======================================================================================

def _fail(msg: str, *hints: str) -> None:
    print(f"\n  {msg}\n", file=sys.stderr)
    for h in hints:
        print(f"    {h}", file=sys.stderr)
    print("", file=sys.stderr)
    sys.exit(1)


def _preflight() -> None:
    """Fail with an actionable message if this interpreter cannot serve."""
    try:
        import uvicorn  # noqa: F401
    except ModuleNotFoundError:
        root = Path(__file__).resolve().parent.parent
        venv = root / ".venv"
        exe = venv / "bin" / "python"
        _fail(
            "The backend's dependencies are not installed for this interpreter.",
            f"you are running : {sys.executable}",
            "",
            "Use the virtualenv:",
            f"    {exe} main.py",
            "",
            "or let the launcher handle it (it also starts Docker):",
            "    python start.py",
        )


if __name__ == "__main__":
    _preflight()


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
from api.forum import router as forum_router
from api.forum_ws import router as forum_ws_router
from api.llm import router as llm_router

setup_logging()
logger = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("backend_starting", port=settings.backend_port, provider=settings.llm_provider)

    # Recorded rather than raised: the process must still start so the UI can
    # load and *show* the problem. Silently continuing is what this used to do,
    # which left a server that answered 200 on /api/health while every real
    # request 500'd on a missing table.
    db_error: str | None = None
    try:
        await init_db()
    except Exception as e:
        db_error = str(e)
        logger.error("database_init_failed", error=db_error[:400])

    app.state.db_error = db_error

    # Load persisted role -> model overrides so a per-character model choice
    # survives a restart. Non-fatal: env config still applies without it.
    try:
        from llm import roles as llm_roles
        await llm_roles.load_overrides()
    except Exception as e:
        logger.warning("llm_roles_load_failed", error=str(e))

    if db_error:
        logger.error(
            "backend_running_without_database",
            hint=(
                "The API is up so this can be reported, but the database schema "
                "is not usable. Check POSTGRES_URL, that the server is "
                "reachable, and that pgvector is installed."
            ),
        )

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
app.include_router(forum_router)
app.include_router(llm_router)
app.include_router(websocket_router)
app.include_router(forum_ws_router)


@app.get("/api/health")
async def health():
    from llm import health as llm_health

    llm = await llm_health()
    checks = {
        "status": "ok" if llm.get("ok") else "degraded",
        # `configured` is deliberately NOT the same as `llm_ok`: a key can be
        # present and still unable to serve a single completion. `callable` is
        # null until POST /api/llm/verify proves it.
        "configured": llm.get("configured", llm.get("ok")),
        "callable": llm.get("callable"),
        "provider": llm.get("provider"),
        "transport": llm.get("transport"),
        "llm_ok": llm.get("ok"),
        # A delisted model is the failure that reads as healthy: the key is
        # fine, the provider answers, and the character produces nothing.
        "missing_models": llm.get("missing_models", []),
        "hint": llm.get("hint") or llm.get("model_hint"),
        "roles": llm.get("roles", []),
        "forum_enabled": settings.forum_enabled,
        "version": "2.0.0",
    }
    # Transport-specific detail.
    if llm.get("provider") == "ollama":
        checks["ollama_reachable"] = llm.get("reachable")
        checks["model_present"] = llm.get("model_present")
        checks["installed"] = llm.get("installed", [])
    elif llm.get("transport") == "cli_provider":
        checks["binary"] = llm.get("binary")

    try:
        from db.postgres import engine
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
        checks["postgres_ok"] = True
    except Exception as e:
        checks["postgres_ok"] = False
        checks["postgres_error"] = str(e)

    # `SELECT 1` only proves the socket is open. A database that is reachable
    # but has no schema passes that check and then 500s on every real request,
    # so the table itself is what gets asked about.
    if checks.get("postgres_ok"):
        try:
            from db.postgres import engine
            from sqlalchemy import text

            async with engine.connect() as conn:
                present = await conn.execute(text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = ANY(:names)"
                ), {"names": ["topics", "posts", "rag_chunks", "llm_roles"]})
                found = present.scalar_one()
            checks["schema_ok"] = found == 4
            checks["schema_tables_found"] = int(found)
            if found != 4:
                checks["schema_hint"] = (
                    f"{4 - int(found)} core table(s) missing. The database is "
                    "reachable but the schema was never created — check "
                    "pgvector, since rag_chunks.embedding is a Vector(384) "
                    "column that cannot be created without the extension."
                )
        except Exception as e:
            checks["schema_ok"] = False
            checks["schema_error"] = str(e)[:300]

    # Anything that went wrong during startup is reported here rather than only
    # in the log, so a client can refuse to use the API instead of failing
    # request by request.
    startup_error = getattr(app.state, "db_error", None)
    if startup_error:
        checks["startup_error"] = startup_error[:400]
        checks["status"] = "degraded"

    return checks


@app.get("/api/health/ready")
async def ready():
    checks = await health()
    all_ok = checks.get("postgres_ok", False) and checks.get("llm_ok", False)
    return {"ready": all_ok, "checks": checks}


@app.get("/api/ollama/status")
async def ollama_status():
    """Installed models on the local Ollama server and whether ours is present.

    Kept for the existing setup wizard; the provider-agnostic version is
    `GET /api/llm/models?provider=ollama`.
    """
    from ollama_client import ollama_status as _status
    return await _status()


@app.get("/api/forum/status")
async def forum_status():
    """Lightweight liveness for the forum surface, for the UI badge."""
    from agents import registry
    return {
        "enabled": settings.forum_enabled,
        "cast": registry.keys(),
        "max_rounds": settings.forum_max_rounds,
        "intake_open": not settings.forum_intake_key,
    }


if __name__ == "__main__":
    import uvicorn

    _host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    _port = settings.backend_port
    _reload = "--reload" in sys.argv
    _root = Path(__file__).resolve().parent.parent

    # flush=True: stdout is often a pipe here, and a server killed while
    # starting should still have shown what it was starting.
    print("\n  Council of Minds — backend", flush=True)
    print(f"    provider : {settings.llm_provider}", flush=True)
    print(f"    model    : {settings.default_model}", flush=True)
    print(f"    api      : http://{_host}:{_port}", flush=True)
    print(f"    health   : http://{_host}:{_port}/api/health", flush=True)
    print("\n  The forum is opened by Electron, or directly:", flush=True)
    print(f"    {_root / 'com_website' / 'forum.html'}\n", flush=True)

    uvicorn.run("main:app", host=_host, port=_port, reload=_reload)
