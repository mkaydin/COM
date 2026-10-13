"""Tests for `python main.py`.

Running `main.py` directly used to be silently useless: the module only
defined the app, so it imported everything and exited. Two failures came out
of that —

  1. `python main.py` with the system interpreter raised a bare
     `ModuleNotFoundError: No module named 'fastapi'`, which says nothing
     about the actual problem (the venv is one directory up);
  2. `../.venv/bin/python main.py` started nothing at all.

Both are now covered here.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
MAIN = BACKEND / "main.py"


def _source() -> str:
    return MAIN.read_text()


class TestEntrypointExists:
    def test_main_has_a_main_block(self):
        """Otherwise running it imports everything and exits."""
        assert "__main__" in _source()

    def test_main_block_runs_uvicorn(self):
        assert "uvicorn.run(" in _source()

    def test_importing_main_still_works(self):
        """The test suite imports `from main import app`, so the entrypoint
        must not interfere with that."""
        assert '__name__ == "__main__"' in _source()

    def test_main_parses(self):
        ast.parse(_source())


class TestPreflight:
    def test_the_check_sits_above_the_imports(self):
        """Everything below is a top-level `from fastapi import ...`, so a
        check further down would run *after* the failure it explains."""
        src = _source()
        guard = src.index('_preflight()\n')
        fastapi_import = src.index("from fastapi import FastAPI")
        assert guard < fastapi_import, (
            "the dependency check must run before the imports it guards"
        )

    def test_the_import_is_still_guarded_by_dunder_main(self):
        """A bare `_preflight()` at module scope would break `import main`."""
        src = _source()
        for line in src.splitlines():
            if "_preflight()" in line and not line.strip().startswith("def"):
                assert '__name__ == "__main__"' in src.split(line)[0].rsplit("\n", 2)[-2] + line

    def test_it_names_the_venv_and_the_launcher(self):
        src = _source()
        assert ".venv" in src, "the message must point at the virtualenv"
        assert "start.py" in src, "the message must point at the launcher"


class TestWrongInterpreter:
    """The failure the user actually hit."""

    def test_system_python_gets_a_helpful_message_not_a_traceback(self):
        """Only meaningful when this interpreter genuinely lacks fastapi."""
        probe = subprocess.run(
            [sys.executable, "-c", "import fastapi"], capture_output=True, text=True
        )
        if probe.returncode == 0:
            pytest.skip("this interpreter has fastapi installed")

        result = subprocess.run(
            [sys.executable, "main.py"], cwd=BACKEND, capture_output=True, text=True, timeout=120
        )
        combined = result.stdout + result.stderr
        assert "Traceback" not in combined, "still a raw traceback"
        assert "ModuleNotFoundError" not in combined, "still a bare ImportError"
        assert "dependencies are not installed" in combined
        assert "start.py" in combined
        # It must not exit 0: a silent success would be worse.
        assert result.returncode != 0


class TestOutputIsFlushed:
    def test_the_banner_flushes(self):
        """stdout is often a pipe; a server killed while starting should still
        have shown what it was starting. This is the same bug start.py had."""
        src = _source()
        banner = [ln for ln in src.splitlines() if "Council of Minds — backend" in ln]
        assert banner, "no startup banner found"
        assert "flush=True" in banner[0]


class TestDocsMentionIt:
    def test_readme_documents_the_direct_run(self):
        readme = (BACKEND.parent / "README.md").read_text()
        assert "main.py" in readme, (
            "README should document `python main.py` for people who go straight "
            "to the backend instead of using start.py or Electron"
        )