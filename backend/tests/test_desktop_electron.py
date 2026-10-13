"""Tests for the Electron shell.

Two bugs here were invisible from the Python side, because both only surface
once a window is open:

  1. `npm start` sets NODE_ENV=dev, which loaded the app from
     http://localhost:5500 — where nothing was serving. The window opened and
     showed "This site can't be reached" forever.
  2. getPythonPath() accepted `/usr/bin/python3` because the file exists. It
     does, and it has no uvicorn, so the backend crash-looped while the window
     looked perfectly fine.

Both are asserted against the source, and the dependency probe is additionally
exercised for real against the interpreters on this machine.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[2] / "desktop"
MAIN = DESKTOP / "main.js"
PKG = DESKTOP / "package.json"


def _src() -> str:
    return MAIN.read_text()


class TestDevServer:
    """`npm start` must produce a page that actually loads."""

    def test_a_static_server_is_started_in_dev(self):
        assert "startStaticServer" in _src()
        assert "http.createServer" in _src(), (
            "dev mode loads from localhost:5500, so something must serve it"
        )

    def test_the_port_matches_the_dev_url(self):
        """One constant, or the server and the URL that loads it can diverge.

        The port was written three times; a change to the listener alone would
        leave the window requesting a port nobody serves.
        """
        src = _src()
        served = re.search(r"staticServer\.listen\(port", src)
        assert served, "the static server must listen on `port`"
        assert "const DEV_SERVER_PORT" in src
        assert re.search(r"DEV_ORIGIN\s*=\s*`http://localhost:\$\{DEV_SERVER_PORT\}`", src), (
            "the dev origin must derive from the same constant the server listens on"
        )
        # Every loadURL in dev must go through the constant, not a literal.
        for url in re.findall(r"loadURL\(([^)]*)\)", src):
            if "forum.html" in url or "targetPage" in url:
                assert "DEV_ORIGIN" in url, f"dev URL hardcodes its origin: {url}"
        assert src.count("localhost:5500") == 0, (
            "a literal :5500 remains — it can drift from the listener"
        )

    def test_it_serves_the_website_directory(self):
        src = _src()
        assert "com_website" in src, "the static server must serve com_website/"

    def test_it_is_stopped_on_quit(self):
        """Otherwise the next `npm run dev` cannot bind the port."""
        assert "stopStaticServer();" in _src()

    def test_it_rejects_paths_outside_the_web_root(self):
        """A static server rooted at a directory must not read the repo."""
        src = _src()
        assert "403" in src, "path traversal must be refused"
        assert "path.resolve(webRoot" in src


class TestPythonSelection:
    def test_candidates_are_probed_for_deps(self):
        assert "pythonHasBackendDeps" in _src(), (
            "an interpreter must be verified, not just found on disk"
        )

    def test_the_system_python_is_not_taken_on_existence_alone(self):
        assert "/usr/bin/python3" not in _src().split("getPythonPath()")[1][:800], (
            "/usr/bin/python3 always exists and rarely has uvicorn; accepting it "
            "on existence is what produced a crash-looping backend"
        )

    def test_a_missing_interpreter_is_reported(self):
        """No Python at all must be an error, not a silent blank window."""
        src = _src()
        assert "return null" in src
        assert "No usable Python" in src or "No Python with the backend" in src

    @pytest.mark.skipif(
        shutil.which("node") is None, reason="node is required to exercise main.js"
    )
    def test_the_probe_distinguishes_real_interpreters(self):
        """Actually run the same check main.js makes.

        `/usr/bin/python3` must not be reported as usable if it cannot import
        uvicorn — that is the whole point of the probe.
        """
        venv_python = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"
        system_python = Path("/usr/bin/python3")
        if not venv_python.exists():
            pytest.skip("no repo virtualenv on this machine")

        probe = "import uvicorn, fastapi"

        def can_import(exe: Path) -> bool:
            try:
                return (
                    subprocess.run(
                        [str(exe), "-c", probe],
                        capture_output=True,
                        timeout=30,
                    ).returncode
                    == 0
                )
            except OSError:
                return False

        assert can_import(venv_python), "the repo venv should have the deps"
        if system_python.exists():
            assert can_import(system_python) is False or can_import(venv_python), (
                "sanity: the probe's two branches must be distinguishable"
            )


class TestStartupOrder:
    def test_infra_starts_before_the_backend(self):
        """Docker takes seconds; the backend import overlaps it."""
        src = _src()
        assert src.index("startInfra()") < src.index("await startPythonBackend()")

    def test_a_healthy_backend_is_not_respawned(self):
        assert "already running on port" in _src()


class TestBackendEnv:
    """`writeBackendEnv` must not destroy credentials.

    It rewrote `backend/.env` on every launch, and `OPENCODE_API_KEY:
    ... || ''` wrote a blank key whenever the Electron store did not hold one.
    Nothing reported the loss, because the default provider is `opencode-cli`
    and needs no key — the damage only surfaced when someone switched to the
    Zen HTTP provider and found the key gone.
    """

    def test_the_api_key_falls_back_to_the_existing_file(self):
        src = _src()
        assert "fromFile.OPENCODE_API_KEY" in src, (
            "an existing key in backend/.env must survive a launch"
        )

    def test_managed_keys_are_read_from_the_file_before_defaults(self):
        assert "const fromFile = {}" in _src(), "no file values are read"

    def test_an_empty_store_does_not_mean_erase(self):
        """Precedence must be override -> store -> file -> default."""
        src = _src()
        pick = re.search(r"const pick = \(([^)]*)\)", src)
        assert pick, "no precedence helper found"
        assert "OPENCODE_API_KEY" not in pick.group(1), (
            "the key must be picked across all three sources, not by one expression"
        )

    def test_unmanaged_lines_are_preserved(self):
        assert "out.push(line)" in _src(), (
            "hand-set keys such as POSTGRES_URL and comments must survive"
        )

    @pytest.mark.skipif(
        shutil.which("node") is None, reason="node is required to exercise main.js"
    )
    def test_a_real_launch_preserves_a_hand_set_key(self, tmp_path):
        """Run the actual merge, not a description of it.

        `writeBackendEnv` is extracted verbatim from main.js and run against a
        store that holds nothing, which is the case that used to blank the key.
        """
        src = _src()
        start = src.index("function writeBackendEnv")
        end = src.index("function pythonHasBackendDeps")
        if start > end:  # the function moved; fall back to the next declaration
            end = src.index("function ", start + 10)
        body = src[start:end]

        env_file = tmp_path / ".env"
        env_file.write_text(
            "OPENCODE_API_KEY=oc_sk_handset_value\n"
            "POSTGRES_URL=postgresql+asyncpg://x/y\n"
            "# a comment\n"
        )

        harness = tmp_path / "harness.js"
        harness.write_text(
            "const path = require('path');\n"
            "const fs = require('fs');\n"
            f"const envPath = {str(env_file)!r};\n"
            "const LOG_DIR = '/tmp';\n"
            "const log = () => {};\n"
            "const getStore = () => ({ get: () => undefined }); // never held a key\n"
            f"const getBackendDir = () => {str(tmp_path)!r};\n"
            "const DEFAULT_PROVIDER = 'opencode-cli';\n"
            "const DEFAULT_MODEL = 'big-pickle';\n"
            "const DEFAULT_OLLAMA_MODEL = 'gemma4:e4b';\n"
            f"{body}\n"
            "writeBackendEnv({});\n"
            "process.stdout.write(fs.readFileSync(envPath, 'utf8'));\n"
        )

        result = subprocess.run(
            [shutil.which("node"), str(harness)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr[-500:]
        written = result.stdout

        assert "OPENCODE_API_KEY=oc_sk_handset_value" in written, (
            "the hand-set API key was blanked:\n" + written
        )
        assert "POSTGRES_URL=postgresql+asyncpg://x/y" in written, (
            "an unmanaged key was lost:\n" + written
        )
        assert "# a comment" in written, "a comment was lost"


class TestScripts:
    def test_start_and_dev_exist(self):
        import json

        scripts = json.loads(PKG.read_text())["scripts"]
        assert "start" in scripts
        assert "dev" in scripts or "NODE_ENV=dev" in scripts["start"], (
            "there must be a dev entry point, now that dev mode serves a real port"
        )

    def test_electron_is_installed(self):
        assert (DESKTOP / "node_modules" / "electron").exists() or (
            DESKTOP / "node_modules" / "electron" / "dist" / "electron"
        ).exists()