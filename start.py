#!/usr/bin/env python3
"""Startup orchestrator.

Brings up the Docker stack, waits for it to be healthy, then tells you where
COM is. The backend runs inside a container, so this no longer starts uvicorn
directly — that is the point of the `backend` service.

    python start.py                 # default stack -> http://localhost:8080
    python start.py --local         # + Ollama (optional local provider)
    python start.py --graph         # + Neo4j
    python start.py --no-docker     # skip Docker, run the backend on the host
    python start.py --stop          # bring the stack down
"""
import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCKER_DIR = ROOT / "docker"
COMPOSE = ["docker", "compose", "-f", str(DOCKER_DIR / "docker-compose.yml")]

FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", 8080))
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", 8734))


def run(cmd, cwd=None, check=False, env=None):
    return subprocess.run(cmd, shell=isinstance(cmd, str), cwd=cwd,
                          capture_output=True, text=True, check=check, env=env)


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


def wait_for(label: str, url: str, attempts: int = 40, delay: float = 2.0) -> bool:
    print(f"  waiting for {label} ({url}) ...", flush=True)
    for _ in range(attempts):
        if probe(url):
            print(f"  {label}: OK")
            return True
        time.sleep(delay)
    print(f"  {label}: TIMED OUT")
    return False


def compose(*args, profiles=()):
    cmd = list(COMPOSE)
    for p in profiles:
        cmd += ["--profile", p]
    cmd += list(args)
    return run(cmd)


def banner(text: str):
    print(f"\n=== {text} ===\n")


def up(profiles):
    banner("Starting the Council of Minds")
    args = ["up", "-d"]
    if "--build" in sys.argv or os.environ.get("COM_BUILD"):
        args.append("--build")
    if profiles:
        args.append("--wait")
    result = compose(*args, profiles=profiles)
    if result.returncode != 0:
        print(result.stdout[-2000:])
        print(result.stderr[-2000:])
        return False
    print("  containers started\n")
    return True


def report():
    banner("Ready")
    print(f"  Forum       http://localhost:{FRONTEND_PORT}/forum.html")
    print(f"  Backend API http://localhost:{BACKEND_PORT}/api/health")
    print(f"  Models      http://localhost:{FRONTEND_PORT}/llm_settings.html")
    print()
    print("  Models default to free-tier. Check the key with:")
    print(f"    curl -s localhost:{BACKEND_PORT}/api/llm/roles | python3 -m json.tool")
    print()
    print("  Follow logs:  docker compose -f docker/docker-compose.yml logs -f backend")
    print("  Stop:         python start.py --stop")


def host_mode():
    """Run uvicorn directly, for development without Docker."""
    banner("Running the backend on this host (no Docker)")
    venv = ROOT / ".venv" / "bin" / "python"
    python = str(venv) if venv.exists() else sys.executable
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    # The .env in backend/ is gitignored; copy the example if there is none.
    backend = ROOT / "backend"
    if not (backend / ".env").exists():
        shutil.copy(backend / ".env.example", backend / ".env")
        print(f"  created backend/.env from the example")

    print(f"  backend  http://localhost:{BACKEND_PORT}")
    print(f"  frontend file://{ROOT / 'com_website' / 'forum.html'}")
    print("  (the frontend file:// view needs a running backend)\n")
    try:
        subprocess.run([python, "-m", "uvicorn", "main:app",
                        "--host", "127.0.0.1", "--port", str(BACKEND_PORT),
                        "--reload"], cwd=backend, env=env)
    except KeyboardInterrupt:
        print("\n  backend stopped.")


def stop():
    banner("Stopping")
    result = compose("down")
    if result.returncode != 0:
        print(result.stderr[-2000:])
    else:
        print("  stack down (volumes kept — use `docker compose down -v` to wipe)")


def main():
    ap = argparse.ArgumentParser(description="Council of Minds launcher")
    ap.add_argument("--local", action="store_true", help="include the local Ollama provider")
    ap.add_argument("--graph", action="store_true", help="include Neo4j")
    ap.add_argument("--no-docker", action="store_true", help="run the backend on this host")
    ap.add_argument("--stop", action="store_true", help="bring the stack down")
    ap.add_argument("--build", action="store_true", help="force an image rebuild")
    args = ap.parse_args()

    if args.stop:
        stop()
        return

    if args.no_docker:
        host_mode()
        return

    if not docker_available():
        print("[WARN] Docker is not available.")
        print("       Install it, or run: python start.py --no-docker\n")
        host_mode()
        return

    profiles = []
    if args.local:
        profiles.append("local")
    if args.graph:
        profiles.append("graph")

    if not up(profiles):
        sys.exit(1)

    wait_for("backend", f"http://localhost:{BACKEND_PORT}/api/health", attempts=90, delay=2)
    wait_for("frontend", f"http://localhost:{FRONTEND_PORT}/forum.html", attempts=20, delay=1)
    report()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)