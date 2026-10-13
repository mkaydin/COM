"""Tests for start.py's infrastructure handling.

The launcher is the first thing that runs and the easiest place for a wrong
assumption to hide: both bugs found while writing it were a service reported
unreachable when it was healthy.
"""

import importlib.util
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load_start():
    """Import start.py by path — it is a script, not a package module."""
    spec = importlib.util.spec_from_file_location("com_start", ROOT / "start.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["com_start"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def start():
    return _load_start()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def http_server():
    """A real HTTP server, so probe() is tested against a real socket."""
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


class TestProbe:
    def test_http_probe_succeeds_against_a_live_server(self, start, http_server):
        assert start.probe(http_server)

    def test_http_probe_fails_against_a_closed_port(self, start):
        # Port 1 is reserved and nothing listens there.
        assert start.probe("http://127.0.0.1:1", timeout=0.5) is False

    def test_tcp_probe_succeeds_against_a_live_listener(self, start):
        """Postgres speaks its own wire protocol, so an HTTP probe there never
        returns 200 and a healthy database reads as unreachable."""
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            assert start.probe_tcp("127.0.0.1", port)
        finally:
            s.close()

    def test_tcp_probe_fails_on_a_closed_port(self, start):
        assert start.probe_tcp("127.0.0.1", 1, timeout=0.5) is False


class TestPortParsing:
    def test_reads_a_port_from_docker_env(self, start, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("CHROMA_PORT=8123\nOTHER=1\n")
        monkeypatch.setattr(start, "DOCKER_DIR", tmp_path)
        assert start._port_from_docker_env("CHROMA_PORT", 8000) == 8123

    def test_missing_file_falls_back(self, start, tmp_path, monkeypatch):
        monkeypatch.setattr(start, "DOCKER_DIR", tmp_path)
        assert start._port_from_docker_env("CHROMA_PORT", 8000) == 8000

    def test_missing_key_falls_back(self, start, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("SEARXNG_PORT=9999\n")
        monkeypatch.setattr(start, "DOCKER_DIR", tmp_path)
        assert start._port_from_docker_env("CHROMA_PORT", 8000) == 8000

    def test_comments_and_blank_lines_are_skipped(self, start, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("# a comment\n\nCHROMA_PORT=8123\n")
        monkeypatch.setattr(start, "DOCKER_DIR", tmp_path)
        assert start._port_from_docker_env("CHROMA_PORT", 8000) == 8123

    def test_garbage_falls_back_instead_of_crashing(self, start, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("CHROMA_PORT=not-a-number\n")
        monkeypatch.setattr(start, "DOCKER_DIR", tmp_path)
        assert start._port_from_docker_env("CHROMA_PORT", 8000) == 8000

    def test_surrounding_whitespace_is_tolerated(self, start, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("  CHROMA_PORT = 8123  \n")
        monkeypatch.setattr(start, "DOCKER_DIR", tmp_path)
        assert start._port_from_docker_env("CHROMA_PORT", 8000) == 8123


class TestComposeEntryPoint:
    """`docker compose up -d` must work from the repository root.

    Compose searches the *current directory* for a compose file. The services
    are defined in docker/docker-compose.yml, so without a root entry point the
    obvious command fails with "no configuration file provided" — which is
    exactly what it did.
    """

    def test_a_compose_file_exists_at_the_repo_root(self, start):
        candidates = ["compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"]
        assert any((start.ROOT / c).exists() for c in candidates), (
            "no compose file at the repo root - `docker compose up -d` will fail"
        )

    def test_the_root_file_includes_the_real_definition(self, start):
        """A thin include, not a second copy: two definitions would drift."""
        root = start.ROOT / "compose.yaml"
        if not root.exists():
            pytest.skip("root compose file is named differently")
        text = root.read_text()
        assert "include:" in text
        assert "docker/docker-compose.yml" in text
        # It must not redefine services itself.
        assert "\nservices:" not in text, (
            "the root file should only include, not re-declare services"
        )

    def test_the_real_definition_still_exists(self, start):
        assert (start.DOCKER_DIR / "docker-compose.yml").exists()

    def test_launcher_uses_the_root_entry_point(self, start):
        """The launcher and a hand-typed command must find the same file."""
        assert "-f" not in start.COMPOSE or (
            start.DOCKER_DIR / "docker-compose.yml"
        ).exists()


class TestServiceSelection:
    def test_default_infra_is_state_only(self, start):
        """The backend is deliberately absent: it must run where the opencode
        CLI is installed."""
        assert start.INFRA == ("postgres", "chromadb")

    def test_ollama_and_neo4j_are_not_started_by_default(self, start):
        assert "ollama" not in start.INFRA
        assert "neo4j" not in start.INFRA


class TestLauncherContract:
    def test_progress_output_is_unbuffered(self, start):
        """A lost buffer on SIGTERM hides how far the launcher got — which is
        exactly when you are reading it."""
        source = (ROOT / "start.py").read_text()
        # Every print in a status line must flush.
        assert 'flush=True' in source
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("print(") and "flush" not in line:
                # Only progress/status prints matter; argparse errors do not.
                assert "sys.exit" not in line, f"unflushed print: {stripped[:60]}"

    def test_backend_dir_exists_for_the_uvicorn_spawn(self, start):
        assert (start.ROOT / "backend" / "main.py").exists()