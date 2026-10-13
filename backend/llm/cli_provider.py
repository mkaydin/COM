"""OpenCode CLI provider.

Zen's free models cannot be called over the plain API — the endpoint answers
`FreeTierError: "OpenCode's free tier can only be used from within OpenCode"`.
They *do* work from the OpenCode client, so this provider shells out to
`opencode run`.

It presents the same call signature as LiteLLM's `completion()`, which is what
lets `llm.llm_complete()` stay provider-agnostic: everything above this file
is unchanged, and swapping providers is a config value.

What this costs, stated plainly:

* **Latency.** 1.4–3.2s per call against roughly 400ms for the same model over
  HTTP. A forum run makes one call per character per round, so a five-character
  two-round topic goes from ~15s to ~40–60s.
* **Availability.** The backend container does not ship the `opencode` binary,
  so this provider needs the backend running on the host (`start.py
  --no-docker`) or an image with the CLI added. The compose stack keeps using
  an HTTP provider.
* **Auth.** The CLI uses its own credential store, so no `OPENCODE_API_KEY` is
  required. Setting one anyway lets paid models work through the same path.

`opencode run` takes a single user message and no system message, so the system
prompt is folded into the user turn. The agents parse with
`agents.json_utils.extract_json`, which is indifferent to where the prompt came
from.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any, Optional

from logging_config import get_logger

logger = get_logger(__name__)

#: A single call. Generous, because the CLI starts a session and an agent run
#: on top of the model call itself.
DEFAULT_TIMEOUT = 180.0

#: Models are addressed as `provider/model` on the CLI side. Zen models live
#: under the `opencode/` prefix there, with no `-free` suffix.
CLI_MODEL_PREFIX = "opencode/"


class CliProviderError(RuntimeError):
    """The opencode CLI could not serve the request."""


class CliNotInstalled(CliProviderError):
    """The binary is not on PATH."""


class CliCallFailed(CliProviderError):
    """The CLI ran but did not produce a usable completion."""


def binary_path() -> Optional[str]:
    """Resolve the opencode binary, honouring `OPENCODE_CLI_BIN`."""
    from config import get_settings

    configured = getattr(get_settings(), "opencode_cli_bin", "") or "opencode"
    return shutil.which(configured)


#: Set when we can tell we are inside the backend image.
_IN_CONTAINER = Path("/.dockerenv").exists()


def container_hint() -> Optional[str]:
    """The Docker-specific remedy, when that is what went wrong.

    Saying "not on PATH" to someone running the compose stack is not enough —
    the binary will never be on PATH there, because the image does not ship
    one. Naming the actual fix saves a long debugging detour.
    """
    if _IN_CONTAINER:
        return (
            "This is the backend container, which does not ship the OpenCode "
            "CLI. Use LLM_PROVIDER=ollama (free, local — add --profile local) "
            "or LLM_PROVIDER=opencode (Zen over HTTP, needs a funded account). "
            "The free Zen models are only reachable from a host install."
        )
    return None


def to_cli_model(model: str) -> str:
    """`big-pickle` -> `opencode/big-pickle`."""
    bare = (model or "").split("/", 1)[-1]
    return bare if bare.startswith(CLI_MODEL_PREFIX) else f"{CLI_MODEL_PREFIX}{bare}"


def flatten_messages(messages: list[dict]) -> str:
    """Collapse a chat transcript into one user turn.

    The CLI has no system-message flag, so instructions and content are
    concatenated with their roles kept as labels. Role labels matter: several
    characters' prompts address the reader ("Reply to the specific claim you
    are answering"), and dropping the framing would leave the instruction
    looking like quoted material.
    """
    parts: list[str] = []
    for m in messages or []:
        role = (m.get("role") or "user").upper()
        content = m.get("content") or ""
        if not content:
            continue
        parts.append(f"[{role}]\n{content}" if role != "USER" else content)
    return "\n\n".join(parts)


def parse_events(stdout: str) -> str:
    """Extract the assistant text from `opencode run --format json`.

    That format is newline-delimited JSON events; the answer arrives as one or
    more `{"type":"text","part":{"text":...}}` records. Concatenating them in
    order reconstructs the reply.
    """
    texts: list[str] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") != "text":
            continue
        part = event.get("part") or {}
        text = part.get("text")
        if isinstance(text, str) and text:
            texts.append(text)
    return "".join(texts).strip()


async def complete(
    messages: list,
    *,
    model: str,
    timeout: float = DEFAULT_TIMEOUT,
    **kwargs: Any,
) -> dict:
    """One completion via the opencode CLI.

    Returns a minimal object shaped like LiteLLM's response
    (`.choices[0].message.content`) so callers do not care which provider ran.
    """
    binary = binary_path()
    if binary is None:
        from config import get_settings

        wanted = getattr(get_settings(), "opencode_cli_bin", "") or "opencode"
        remedy = container_hint() or (
            "install the OpenCode CLI, or run the backend on the host "
            "(python start.py --no-docker)"
        )
        raise CliNotInstalled(f"{wanted!r} is not on PATH — {remedy}")

    prompt = flatten_messages(messages)
    if not prompt.strip():
        raise CliCallFailed("refusing to call the CLI with an empty prompt")

    cli_model = to_cli_model(model)
    cmd = [binary, "run", "--model", cli_model, "--format", "json", prompt]

    env = dict(os.environ)
    # The CLI has its own credential store, so a key is optional. Passing ours
    # through when set lets paid models work over the same path.
    from config import get_settings

    api_key = getattr(get_settings(), "opencode_api_key", "")
    if api_key:
        env["OPENCODE_API_KEY"] = api_key

    logger.debug("cli_completion_start", model=cli_model, prompt_chars=len(prompt))
    started = asyncio.get_event_loop().time()

    def _run() -> tuple[int, str, str]:
        import subprocess

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return proc.returncode, proc.stdout, proc.stderr

    try:
        returncode, stdout, stderr = await asyncio.to_thread(_run)
    except asyncio.TimeoutError as e:
        raise CliCallFailed(f"opencode CLI timed out after {timeout:.0f}s") from e

    elapsed = asyncio.get_event_loop().time() - started
    text = parse_events(stdout)

    if returncode != 0 or not text:
        detail = (stderr or "").strip() or (stdout or "").strip()
        detail = detail[-400:]
        raise CliCallFailed(
            f"opencode CLI exited {returncode} for {cli_model}: {detail or 'no output'}"
        )

    logger.info(
        "cli_completion_done",
        model=cli_model,
        elapsed_ms=int(elapsed * 1000),
        out_chars=len(text),
    )

    return {
        "choices": [{"message": {"content": text, "role": "assistant"}}],
        "model": cli_model,
        "_elapsed_ms": int(elapsed * 1000),
    }


async def health() -> dict:
    """Can this provider actually serve a call?"""
    binary = binary_path()
    if binary is None:
        from config import get_settings

        wanted = getattr(get_settings(), "opencode_cli_bin", "") or "opencode"
        return {
            "ok": False,
            "reachable": False,
            "hint": (
                f"{wanted!r} is not on PATH — "
                + (
                    container_hint()
                    or "install the OpenCode CLI. The backend must run where "
                    "the CLI is installed: `python start.py --no-docker`, or "
                    "use an HTTP provider in Docker."
                )
            ),
        }
    return {
        "ok": True,
        "reachable": True,
        "binary": binary,
        "hint": (
            "Calls go through the opencode CLI, so they are slower than HTTP "
            "(~1.4-3.2s each) and the backend must run where the CLI is installed."
        ),
    }