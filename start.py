#!/usr/bin/env python3
"""Startup orchestrator.

The shape of this app:

    Electron  ──►  GUI
    host      ──►  backend (uvicorn)  ──►  opencode CLI  (free models)
    Docker    ──►  postgres · chromadb · searxng

The backend runs on the **host**, not in a container, because the default
provider reaches Zen's free models by shelling out to the `opencode` client.
Zen's API refuses free models for standalone clients, and a container has
neither the binary nor its credential store. Docker holds the stateful
services; the app itself runs where you can see it.

    python start.py                 infra + backend on the host  (normal)
    python start.py --local         + Ollama in Docker
    python start.py --graph         + Neo4j
    python start.py --server        backend only, no Docker at all
    python start.py --container     the whole stack in Docker (see README)
    python start.py --stop          bring the infra down
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCKER_DIR = ROOT / "docker"
COMPOSE = ["docker", "compose", "-f", str(DOCKER_DIR / "docker-compose.yml")]

BACKEND_PORT = int(os.environ.get("BACKEND_PORT", 8734))
FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", 8080))

#: The services the backend actually needs. Ollama and Neo4j are optional and
#: never started by default.
INFRA = ("postgres", "chromadb")


def run(cmd, cwd=None, check=False):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return run(["docker", "info"]).returncode == 0


def probe(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 400
    except (urllib.error.URLError, OSError, ValueError):
        return False


def probe_tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    """A plain TCP connect, for services that do not speak HTTP.

    Postgres on 5432 answers the wire protocol, not HTTP — an HTTP GET there
    never returns 200, so probing it that way waits out the full timeout and
    then reports a healthy database as unreachable.
    """
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for(label: str, check, attempts: int = 40, delay: float = 1.5) -> bool:
    print(f"  waiting for {label} …", flush=True)
    for _ in range(attempts):
        if check():
            print(f"  {label}: OK", flush=True)
            return True
        time.sleep(delay)
    print(f"  {label}: TIMED OUT", flush=True)
    return False


def compose(*args, profiles=()):
    cmd = list(COMPOSE)
    for p in profiles:
        cmd += ["--profile", p]
    cmd += list(args)
    return run(cmd)


def banner(text: str):
    print(f"\n=== {text} ===\n", flush=True)


# --------------------------------------------------------------------- Docker


def up_infra(profiles=(), with_build=False) -> bool:
    banner("Starting infrastructure (Docker)")
    result = compose("up", "-d", *(["--build"] if with_build else []), *INFRA,
                     profiles=profiles)
    if result.returncode != 0:
        print(result.stdout[-1500:])
        print(result.stderr[-1500:])
        return False
    for svc in INFRA:
        print(f"  {svc}: started", flush=True)
    return True


def up_container_stack() -> bool:
    banner("Starting the full stack in Docker")
    result = compose("up", "-d", "--build", profiles=[])
    if result.returncode != 0:
        print(result.stdout[-1500:])
        print(result.stderr[-1500:])
        return False
    return True


def stop(profiles=()) -> None:
    banner("Stopping")
    result = compose("down", profiles=profiles)
    if result.returncode != 0:
        print(result.stderr[-1500:])
    else:
        print("  infra down (volumes kept — add -v to wipe)")


def _port_from_docker_env(name: str, default: int) -> int:
    """Read a published host port out of docker/.env.

    The backend connects to the containers over localhost, so it needs the
    ports Docker actually published — not the ones the examples default to.
    """
    env = DOCKER_DIR / ".env"
    if not env.exists():
        return default
    try:
        for raw in env.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            # Tolerate `KEY = value` as well as `KEY=value` — a stray space is
            # an easy thing to leave in a hand-edited env file, and silently
            # falling back to the default port would point the backend at the
            # wrong container.
            key, _, value = line.partition("=")
            if key.strip() == name:
                return int(value.strip())
    except (ValueError, OSError):
        pass
    return default


# ------------------------------------------------------------------ backend


def ensure_env() -> None:
    """Create backend/.env from the example if there is none.

    The example is documented and complete, so this is a working start rather
    than a prompt for values nobody needs to change.
    """
    env = ROOT / "backend" / ".env"
    example = ROOT / "backend" / ".env.example"
    if not env.exists() and example.exists():
        shutil.copy(example, env)
        print(f"  created backend/.env from the example", flush=True)


def python_path() -> str:
    venv = ROOT / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    return sys.executable


def _bootstrap_backend_path() -> None:
    """Make the backend package importable from the repo root.

    The backend uses a flat package layout (`config`, `llm`, `agents`), so
    `backend/` must be on sys.path before importing from it — and its
    dependencies live in the repo's `.venv`, which plain `python3 start.py` is
    not necessarily running from.
    """
    import sys

    backend = str(ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)

    venv_site = ROOT / ".venv"
    if venv_site.exists() and sys.prefix != str(venv_site):
        for lib in venv_site.glob("lib/python*/site-packages"):
            if str(lib) not in sys.path:
                sys.path.append(str(lib))
                break


def check_llm() -> None:
    """Say up front whether the configured provider can actually serve a call.

    A provider can be present and unusable — Zen's API refuses free models to
    standalone clients, and an unfunded account refuses paid ones. Finding that
    out now beats finding it out one 200-second topic later.
    """
    try:
        import asyncio
        import logging

        _bootstrap_backend_path()

        from llm import providers, test_model

        # This is a CLI probe, not the server. structlog auto-configures to
        # stdout at DEBUG before anything else runs, so it would print a JSON
        # line per LLM call over the top of the launcher's own output.
        # `logging.disable` does not reach it — structlog uses its own
        # factory — so the filtering bound logger is the switch that works.
        import structlog

        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
        )

        provider = providers.get()
        print(f"  provider: {provider.id}", flush=True)

        if provider.transport == "cli_provider":
            from llm import cli_provider
            path = cli_provider.binary_path()
            if not path:
                print(f"    opencode CLI not found — {cli_provider.container_hint() or 'install it'}", flush=True)
                return
            print(f"    binary: {path}", flush=True)

        result = asyncio.run(test_model(""))
        if result.get("ok"):
            print(f"    verified: {result.get('model')} answered", flush=True)
        else:
            print(f"    NOT USABLE: {result.get('diagnosis') or result.get('error')}", flush=True)
            print("    the forum will run but no character can speak", flush=True)
    except Exception as e:
        # Not worth failing startup over — the backend prints the same detail
        # in its own logs, and the user can POST /api/llm/verify any time.
        print(f"    (could not verify here: {str(e)[:100]})", flush=True)


def run_backend(reload: bool = True) -> None:
    """Run uvicorn in the foreground, exactly as Electron does."""
    banner("Backend")
    ensure_env()

    print(f"  api      http://localhost:{BACKEND_PORT}", flush=True)
    print(f"  forum    file://{ROOT / 'com_website' / 'forum.html'}", flush=True)
    print(f"  (start.py --server to run without Docker)\n", flush=True)
    check_llm()
    print()

    cmd = [python_path(), "-m", "uvicorn", "main:app",
           "--host", "127.0.0.1", "--port", str(BACKEND_PORT)]
    if reload:
        cmd.append("--reload")

    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    try:
        subprocess.run(cmd, cwd=ROOT / "backend", env=env)
    except KeyboardInterrupt:
        print("\n  backend stopped.")


# --------------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Council of Minds launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--local", action="store_true",
                    help="also start Ollama in Docker (optional local LLM)")
    ap.add_argument("--graph", action="store_true",
                    help="also start Neo4j in Docker (optional graph store)")
    ap.add_argument("--server", action="store_true",
                    help="run the backend with no Docker at all")
    ap.add_argument("--container", action="store_true",
                    help="run backend + nginx in Docker instead (no free models)")
    ap.add_argument("--stop", action="store_true", help="bring the infrastructure down")
    ap.add_argument("--no-reload", action="store_true", help="disable uvicorn --reload")
    args = ap.parse_args()

    profiles = []
    if args.local:
        profiles.append("local")
    if args.graph:
        profiles.append("graph")

    if args.stop:
        stop(profiles=profiles)
        return

    if args.container:
        if not docker_available():
            print("[WARN] Docker is not available.")
            sys.exit(1)
        if not up_container_stack():
            sys.exit(1)
        wait_for("backend", f"http://localhost:{BACKEND_PORT}/api/health", attempts=90)
        wait_for("frontend", f"http://localhost:{FRONTEND_PORT}/forum.html", attempts=20)
        banner("Ready")
        print(f"  Forum       http://localhost:{FRONTEND_PORT}/forum.html", flush=True)
        print(f"  Backend API http://localhost:{BACKEND_PORT}/api/health\n", flush=True)
        print("  Note: this profile cannot use Zen's free models — it needs the")
        print("  OpenCode CLI, which the container does not ship. For those, run")
        print("  the backend on the host (the default).\n")
        return

    if args.server:
        run_backend(reload=not args.no_reload)
        return

    if not docker_available():
        print("[WARN] Docker is not available — postgres/chroma/searxng cannot start.")
        print("       The backend will still run, but knowledge retrieval will be")
        print("       degraded. Install Docker, or use a backend that already has\n")
        run_backend(reload=not args.no_reload)
        return

    if not up_infra(profiles=profiles):
        sys.exit(1)

    # Ports come from docker/.env, which is the same file that told Docker
    # where to publish them.
    pg_port = _port_from_docker_env("POSTGRES_PORT", 5432)
    chroma_port = _port_from_docker_env("CHROMA_PORT", 8000)

    wait_for("postgres", lambda: probe_tcp("localhost", pg_port), attempts=30, delay=1)
    wait_for("chromadb", lambda: probe(f"http://localhost:{chroma_port}/api/v2/heartbeat"),
             attempts=30, delay=1)

    run_backend(reload=not args.no_reload)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)