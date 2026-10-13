-- Runs once, on an empty pgdata volume.
--
-- The `vector` extension has to exist before SQLAlchemy creates the tables
-- that use Vector columns. Postgres refuses CREATE EXTENSION inside a
-- transaction block in some versions, so guard with a DO block instead of a
-- bare CREATE EXTENSION IF NOT EXISTS.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vector;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgvector not available: %', SQLERRM;
END
$$;