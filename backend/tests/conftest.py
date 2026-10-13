"""Pytest configuration and safety guards.

Two categories of test:

* **unit** — pure logic, no external services. Always run.
* **live** — touch Postgres and/or the sentence-transformers embedder. Skipped
  by default because several of them issue `DELETE` against tables on the
  configured database.

Run them explicitly with `pytest -m live --run-live`.
"""
import os
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Substrings that mark a database as expendable. Anything else needs
# --run-live --allow-real-db as well.
_SAFE_DB_MARKERS = ("test", "tmp", "scratch")


def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run tests marked `live` (they hit Postgres and write to it).",
    )
    parser.addoption(
        "--allow-real-db",
        action="store_true",
        default=False,
        help="Permit live tests against a database whose name does not look like a test DB.",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: requires Postgres / embedder and may write to the DB")
    config.addinivalue_line("markers", "unit: pure logic, no external services")


def _live_guard(config):
    """Refuse to run live tests against a database we do not recognise."""
    from config import get_settings

    url = get_settings().postgres_url
    dbname = url.rsplit("/", 1)[-1].split("?")[0].lower()
    looks_safe = any(marker in dbname for marker in _SAFE_DB_MARKERS)
    if not looks_safe and not config.getoption("--allow-real-db"):
        pytest.exit(
            f"Refusing to run live tests against database {dbname!r}.\n"
            f"It does not look like a test database. Point POSTGRES_URL at a "
            f"throwaway database, or re-run with --allow-real-db if this really "
            f"is expendable data.",
            returncode=1,
        )
    return url


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        _live_guard(config)
        return

    skip_live = pytest.mark.skip(reason="live test (use: pytest -m live --run-live)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def anyio_backend():
    return "asyncio"
