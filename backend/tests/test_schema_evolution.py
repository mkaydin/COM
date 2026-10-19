"""Schema evolution: the model and the database must not drift apart.

`create_all` only creates *missing tables*. So adding a column to a model
silently does nothing on any database that already has that table, and the
first write to it 500s with `column does not exist`. `llm_roles.provider` is
exactly that: a column that existed in Python and not in anyone's install.

These tests pin the two mechanisms that prevent a repeat:

* `_add_missing_columns` applies additive DDL at startup, and is a no-op once
  the schema matches.
* `alembic upgrade head` builds a schema that matches the models exactly, from
  nothing.
"""

import pytest
from pathlib import Path

# Source files are read relative to this package, not the working
# directory: the suite is run both from backend/ and from the repo root.
BACKEND = Path(__file__).resolve().parents[1]
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

import db.models as legacy_models  # noqa: F401  (registers Base.metadata)
import db.forum_models as forum_models  # noqa: F401
from db.models import Base
from db.postgres import _add_missing_columns, init_db

# `init_db()` uses the module-global engine, whose connection pool is bound to
# whichever event loop first used it. pytest-asyncio gives each test a fresh
# loop by default, so a pooled connection from an earlier test belongs to a dead
# one ("attached to a different loop"). A session-scoped loop keeps every async
# test here on the same loop, which is how the app actually runs.
SESSION_LOOP = pytest.mark.asyncio(loop_scope="session")


# ======================================================================================
# The additive ALTER pass
# ======================================================================================


async def _make_legacy_llm_roles(url: str) -> None:
    """Create `llm_roles` in its *pre-migration* shape."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS llm_roles"))
        await conn.execute(text(
            "CREATE TABLE llm_roles ("
            "  id uuid PRIMARY KEY,"
            "  role varchar(64) NOT NULL UNIQUE,"
            "  model_id varchar(255) NOT NULL,"
            "  created_at timestamptz NOT NULL DEFAULT now())"
        ))
    await engine.dispose()


@pytest.mark.live
@pytest.mark.asyncio
async def test_existing_table_gains_the_new_column():
    """The regression: a column in the model but not in the database."""
    from config import get_settings

    url = get_settings().postgres_url
    await _make_legacy_llm_roles(url)

    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    async with engine.begin() as c:
        await c.run_sync(_add_missing_columns)
        cols = await c.run_sync(
            lambda conn: {x["name"] for x in inspect(conn).get_columns("llm_roles")}
        )
    await engine.dispose()

    assert "provider" in cols, (
        "init_db must add a column the model declares but the database lacks — "
        "otherwise every write to it raises UndefinedColumnError"
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_column_addition_is_idempotent():
    """Running twice must not fail; startup happens on every boot."""
    from config import get_settings

    url = get_settings().postgres_url
    await _make_legacy_llm_roles(url)

    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    async with engine.begin() as c:
        await c.run_sync(_add_missing_columns)
        await c.run_sync(_add_missing_columns)  # must not raise
    await engine.dispose()


@pytest.mark.live
@pytest.mark.asyncio
async def test_existing_rows_survive_the_addition():
    """Adding a column must not touch data, and NULL means 'use the default'."""
    import uuid

    from config import get_settings

    url = get_settings().postgres_url
    await _make_legacy_llm_roles(url)

    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    rid = uuid.uuid4()
    async with engine.begin() as c:
        await c.execute(
            text("INSERT INTO llm_roles (id, role, model_id) VALUES (:i, 'ORACLE', 'm')"),
            {"i": str(rid)},
        )
        await c.run_sync(_add_missing_columns)
        row = (await c.execute(
            text("SELECT model_id, provider FROM llm_roles WHERE id = :i"), {"i": str(rid)}
        )).one()
    await engine.dispose()

    assert row[0] == "m", "the existing binding was lost"
    assert row[1] is None, (
        "provider must default to NULL, which is the documented meaning of "
        "'whichever provider is globally active'"
    )


# ======================================================================================
# pgvector must exist before any Vector column can be created
# ======================================================================================


async def _drop_vector(url: str) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    engine = create_async_engine(url)
    async with engine.begin() as c:
        await c.execute(text("DROP EXTENSION IF EXISTS vector CASCADE"))
    await engine.dispose()


@pytest.mark.live
@SESSION_LOOP
async def test_init_db_installs_pgvector_itself():
    """A database without pgvector must still bootstrap.

    `docker/postgres-init/01-vector.sql` only runs against an empty pgdata
    volume, so every database created afterwards — CI, `CREATE DATABASE`, a
    colleague's clone — lacks the `vector` type. `create_all` then dies on the
    first `Vector(384)` column and the app comes up with no schema at all.
    """
    from config import get_settings

    url = get_settings().postgres_url
    engine = create_async_engine(url)
    async with engine.begin() as c:
        await c.execute(text("DROP TABLE IF EXISTS rag_chunks"))
        await c.execute(text("DROP EXTENSION IF EXISTS vector CASCADE"))
    await engine.dispose()

    result = await init_db()
    assert result["pgvector"] is True, "init_db did not install pgvector"

    engine = create_async_engine(url)
    async with engine.connect() as c:
        have = await c.execute(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
        )
        tables = await c.execute(
            text("SELECT count(*) FROM information_schema.tables "
                 "WHERE table_schema = 'public' AND table_name = 'rag_chunks'")
        )
    await engine.dispose()
    assert have.scalar_one() == 1
    assert tables.scalar_one() == 1, "rag_chunks (Vector(384)) was not created"


@pytest.mark.live
@SESSION_LOOP
async def test_pgvector_install_is_idempotent():
    """Startup runs on every boot."""
    from config import get_settings

    await init_db()
    assert (await init_db())["pgvector"] is True


# ======================================================================================
# Health must distinguish reachable from usable
# ======================================================================================


class TestHealthReportsAnUnusableDatabase:
    """`SELECT 1` proves the socket is open and nothing more.

    A reachable database with no schema passed the old health check while every
    real request 500'd. These pin the distinction.
    """

    def test_health_asks_about_the_schema_not_just_the_socket(self):

        src = (BACKEND / "main.py").read_text()
        assert "information_schema.tables" in src, (
            "health must check that the core tables exist, not only that "
            "Postgres answers SELECT 1"
        )

    def test_startup_failure_is_carried_on_app_state(self):

        src = (BACKEND / "main.py").read_text()
        assert "app.state.db_error" in src
        assert "startup_error" in src, (
            "a failed init_db must be visible on /api/health, not only in the log"
        )
        # The old behaviour: a bare `except` that logged and moved on.
        assert 'logger.error("database_init_failed"' in src
        assert src.count("await init_db()") == 1, (
            "init_db must still be called exactly once in the lifespan"
        )


# ======================================================================================
# Alembic
# ======================================================================================


def _run(coro):
    """Run a coroutine to completion from a synchronous test.

    These tests are sync on purpose: `alembic.command` drives its own event
    loop and raises if called from inside pytest-asyncio's.
    """
    import asyncio

    return asyncio.run(coro)


@pytest.fixture
def fresh_db_url():
    """An empty database, created and dropped around the test.

    "Built purely by alembic" only means something on a database that nothing
    else has touched — `create_all` leaves the same tables behind, and the
    earlier revisions create unconditionally. So these tests get their own.
    """
    import uuid as _uuid
    from urllib.parse import urlsplit, urlunsplit

    from sqlalchemy.ext.asyncio import create_async_engine

    from config import get_settings

    admin_url = get_settings().postgres_url
    parts = urlsplit(admin_url)
    name = f"alembic_scratch_{_uuid.uuid4().hex[:8]}"
    scratch_url = urlunsplit(parts._replace(path=f"/{name}"))

    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")

    async def _create():
        async with engine.connect() as c:
            from sqlalchemy import text
            await c.execute(text(f'CREATE DATABASE "{name}"'))
        await engine.dispose()

    _run(_create())

    # `db/migrations/env.py` reads POSTGRES_URL from the environment and ignores
    # `sqlalchemy.url` from the ini, so the env var is what actually redirects
    # alembic. Set both so neither path can quietly target the real database.
    import os

    previous = os.environ.get("POSTGRES_URL")
    os.environ["POSTGRES_URL"] = scratch_url
    try:
        yield scratch_url
    finally:
        if previous is None:
            os.environ.pop("POSTGRES_URL", None)
        else:
            os.environ["POSTGRES_URL"] = previous

        drop_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")

        async def _drop():
            async with drop_engine.connect() as c:
                from sqlalchemy import text
                await c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            await drop_engine.dispose()

        _run(_drop())


@pytest.mark.live
def test_alembic_head_matches_the_models(fresh_db_url):
    """A database built only by migrations must equal the ORM's schema.

    Not async: `alembic.command` drives its own event loop, so calling it from
    inside pytest-asyncio's loop raises.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", fresh_db_url.replace("+asyncpg", ""))
    command.upgrade(cfg, "head")

    engine = create_engine(fresh_db_url.replace("+asyncpg", ""))
    inspector = inspect(engine)

    for name, model in (
        ("llm_roles", forum_models.LlmRole),
        ("topics", forum_models.Topic),
        ("posts", forum_models.Post),
        ("topic_intake", forum_models.TopicIntake),
    ):
        assert inspector.has_table(name), f"{name} missing after `alembic upgrade head`"
        db_cols = {c["name"] for c in inspector.get_columns(name)}
        model_cols = {c.name for c in model.__table__.columns}
        assert db_cols == model_cols, (
            f"{name} drifted from the model: "
            f"db-only={sorted(db_cols - model_cols)} "
            f"model-only={sorted(model_cols - db_cols)}"
        )
    engine.dispose()


@pytest.mark.live
def test_cascade_delete_exists_after_migration(fresh_db_url):
    """The forum relies on this and an integration test asserts it."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", fresh_db_url.replace("+asyncpg", ""))
    command.upgrade(cfg, "head")

    engine = create_engine(fresh_db_url.replace("+asyncpg", ""))
    fks = {fk["constrained_columns"][0]: fk.get("options", {}).get("ondelete")
           for fk in inspect(engine).get_foreign_keys("posts")}
    engine.dispose()

    assert fks.get("topic_id") == "CASCADE", (
        "deleting a topic must take its thread; create_all used to be the "
        "only thing that put this cascade in place"
    )


def test_migrations_cover_every_model_table():
    """A table with no revision means an install depends on create_all.

    Cheap static check: the migration chain must mention each forum table.
    """

    combined = "\n".join(
        p.read_text() for p in (BACKEND / "db/migrations/versions").glob("*.py")
    )
    for table in ("llm_roles", "topics", "posts", "topic_intake"):
        assert f'"{table}"' in combined, f"{table} has no alembic revision"