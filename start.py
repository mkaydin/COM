#!/usr/bin/env python3
"""Startup orchestrator: ensures Docker services are running, then starts the backend."""
import subprocess
import sys
import time
import os
import signal

DOCKER_DIR = os.path.join(os.path.dirname(__file__), "docker")
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "backend")


def run(cmd, cwd=None, check=False):
    return subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, check=check)


def docker_available():
    try:
        result = run("docker info")
        return result.returncode == 0
    except Exception:
        return False


def services_healthy():
    try:
        import httpx
        resp = httpx.get("http://localhost:5432", timeout=2)
        return True
    except Exception:
        pass

    health_map = {
        "postgres": ("pg_isready -U council -h localhost", None),
        "chromadb": ("curl -s http://localhost:8000/api/v1/heartbeat", None),
        "ollama": ("curl -s http://localhost:11434/api/tags", None),
        "searxng": ("curl -s http://localhost:8888", None),
    }

    all_ok = True
    for name, (cmd, _) in health_map.items():
        try:
            result = run(cmd)
            ok = result.returncode == 0
        except Exception:
            ok = False
        status = "OK" if ok else "DOWN"
        print(f"  {name:12s} [{status}]")
        all_ok = all_ok and ok
    return all_ok


def main():
    print("=== Council of Minds — Startup ===\n")

    if not docker_available():
        print("[WARN] Docker not available. Services must be running externally.")
        print("       Install Docker and run: docker compose -f docker/docker-compose.yml up -d\n")
    else:
        print("[1/3] Starting Docker services...")
        result = run("docker compose up -d --wait", cwd=DOCKER_DIR)
        if result.returncode != 0:
            print(f"[ERROR] Docker compose failed:\n{result.stderr}")
            sys.exit(1)
        print("       Services started.\n")

    print("[2/3] Waiting for services to be healthy...")
    for attempt in range(30):
        if services_healthy():
            print("       All services healthy.\n")
            break
        time.sleep(2)
    else:
        print("[WARN] Some services may not be fully ready.\n")

    print("[3/3] Starting Python backend...")
    backend_port = os.environ.get("BACKEND_PORT", "8734")

    try:
        subprocess.run(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", backend_port],
            cwd=BACKEND_DIR,
            check=True,
        )
    except KeyboardInterrupt:
        print("\n[INFO] Backend stopped.")


if __name__ == "__main__":
    main()
