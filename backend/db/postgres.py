from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from db.models import Base
from config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.postgres_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> dict:
    """Bring the schema up to date. Returns what it did.

    Raises on failure — the caller decides whether that is fatal. Swallowing it
    (as this used to) leaves the app *running* with no tables, so every request
    500s much later and the connection between the two events is guesswork.
    """
    # Import for side effects: the forum models share Base.metadata, and
    # create_all only sees tables whose module has actually been imported.
    import db.forum_models  # noqa: F401

    from logging_config import get_logger

    logger = get_logger(__name__)

    async with engine.begin() as conn:
        # pgvector first: `Base.metadata.create_all` emits `Vector(384)`
        # columns, and without the extension that is a hard error — so the
        # whole create step fails on any database the container init script
        # never touched (a `CREATE DATABASE`, CI, a fresh clone).
        vector_ready = await conn.run_sync(_ensure_pgvector)

        await conn.run_sync(Base.metadata.create_all)
        # `create_all` only creates *missing tables*. Adding a column to a
        # model therefore does nothing to a database that already has the table
        # — which is how `llm_roles.provider` ended up missing on an existing
        # install and every write to it 500'd. The additive cases are safe to
        # apply here; anything requiring a backfill or a rewrite belongs in an
        # alembic revision instead.
        await conn.run_sync(_add_missing_columns)

    logger.info("database_initialized", pgvector=vector_ready)
    return {"pgvector": vector_ready}


def _ensure_pgvector(conn) -> bool:
    """Install the `vector` extension if it is missing. Returns whether it is.

    `docker/postgres-init/01-vector.sql` only runs against an empty pgdata
    volume, so any database created afterwards has no `vector` type and
    `create_all` dies on the first `Vector(384)` column.

    Guarded: the extension needs a superuser or the right privileges, and a
    managed Postgres may refuse. That is reported rather than raised — the
    failure surfaces immediately and legibly from `create_all` instead, rather
    than as a permission error here.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from logging_config import get_logger

    logger = get_logger(__name__)
    try:
        exists = conn.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        ).first()
        if exists:
            return True
        # Postgres refuses CREATE EXTENSION inside a transaction block in some
        # versions; DO block guard, matching docker/postgres-init.
        conn.execute(text(
            "DO $$ BEGIN CREATE EXTENSION IF NOT EXISTS vector; "
            "EXCEPTION WHEN OTHERS THEN RAISE NOTICE '%', SQLERRM; END $$;"
        ))
        ready = bool(conn.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        ).first())
        if ready:
            logger.info("pgvector_installed")
        else:
            logger.warning(
                "pgvector_unavailable",
                hint=(
                    "CREATE EXTENSION vector could not be run (insufficient "
                    "privileges?). rag_chunks.embedding is a Vector(384) "
                    "column, so the schema cannot be created until it exists."
                ),
            )
        return ready
    except SQLAlchemyError as e:
        logger.warning("pgvector_check_failed", error=str(e)[:200])
        return False


def _add_missing_columns(conn) -> None:
    """ALTER TABLE ... ADD COLUMN for columns the model has but the DB lacks.

    Deliberately additive only, and deliberately best-effort: a column that
    cannot be added (already present under another name, insufficient
    privilege) is logged rather than raised, because refusing to start the whole
    app over one column is worse than a query that fails loudly when used.
    """
    from sqlalchemy import inspect, text
    from sqlalchemy.exc import SQLAlchemyError

    from logging_config import get_logger

    logger = get_logger(__name__)
    inspector = inspect(conn)

    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue  # create_all just made it, so it is already complete
        present = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            ddl_type = column.type.compile(dialect=conn.dialect)
            sql = (
                f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}'
            )
            if column.server_default is not None:
                default = column.server_default.arg
                sql += f" DEFAULT {default.text if hasattr(default, 'text') else default}"
            try:
                conn.execute(text(sql))
                logger.info("db_column_added", table=table.name, column=column.name)
            except SQLAlchemyError as e:
                logger.warning(
                    "db_column_not_added",
                    table=table.name,
                    column=column.name,
                    error=str(e)[:200],
                    hint="Add it with an alembic revision.",
                )


async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
