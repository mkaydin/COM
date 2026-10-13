"""Tolerant JSON extraction from small local models.

Small models rarely emit clean JSON. They wrap it in ``` fences, prepend a
```<think>``` block, trail a sentence of commentary, or get cut off at the
token limit mid-object. Every agent funnels its output through `extract_json`,
so this is the single most failure-prone function in the Council — and a silent
failure here looks like "the agent returned nothing", not like a parse error.
"""
import json
import re
from typing import Type

from pydantic import BaseModel

# Reasoning blocks that must be discarded before looking for the answer object.
_REASONING_BLOCKS = re.compile(
    r"<(think|thinking|analysis|scratchpad)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL)


def _iter_json_candidates(text: str):
    """Yield every balanced top-level `{...}` in `text`, longest first.

    Scans for brace balance while respecting string literals and escapes, so a
    `}` inside a quoted value does not terminate the object early. Later
    candidates are yielded first because a model that thinks out loud puts its
    reasoning object *before* the real answer.
    """
    found: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_string = False
        escaped = False
        for j in range(i, n):
            ch = text[j]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    found.append(text[i:j + 1])
                    break
        else:
            # Unbalanced: a truncated final object. Emit the remainder so the
            # repair pass below has something to close.
            found.append(text[i:])
            break
        i = j + 1
    return list(reversed(found))


def _try_parse(fragment: str) -> dict | None:
    fragment = fragment.strip()
    if not fragment:
        return None
    try:
        value = json.loads(fragment)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _close_truncated(fragment: str) -> str | None:
    """Best-effort repair of a JSON object cut off mid-stream.

    Drops a dangling key or trailing comma, then closes open strings/braces.
    """
    s = fragment.strip()
    if not s.startswith("{"):
        return None

    # Strip trailing commas before a closing bracket: {"a": 1,} and [1, 2,]
    # are what small models emit constantly, and are not valid JSON.
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r",(\s*[}\]])", r"\1", s)

    # Remove a trailing partial token: a dangling key or a bare comma.
    s = re.sub(r",\s*$", "", s)
    s = re.sub(r'"[^"]*"\s*:\s*$', "", s)
    s = re.sub(r"[,{]\s*$", "", s)

    # The opening brace is already part of `s`, so the stack starts empty and
    # is rebuilt from the scan below.
    stack: list[str] = []
    in_string = False
    escaped = False
    for ch in s:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]" and stack:
            stack.pop()

    if in_string:
        s += '"'
    repaired = s + "".join(reversed(stack))
    return _try_parse(repaired)


def extract_json(raw: str) -> dict:
    """Extract a JSON object from raw LLM output.

    Returns `{}` if nothing parseable is present, so callers can rely on a
    dict and fall back to their degraded response.
    """
    if not raw:
        return {}

    # 1. Drop reasoning blocks; some models emit them before the answer.
    cleaned = _REASONING_BLOCKS.sub("", raw)

    # 2. Fenced blocks are high-signal — try each, longest first.
    for candidate in sorted(_FENCE.findall(cleaned), key=len, reverse=True):
        parsed = _try_parse(candidate)
        if parsed is not None:
            return parsed

    # 3. Balanced scan over the whole (fence-stripped) text, last object first.
    candidates = _iter_json_candidates(cleaned)
    for candidate in candidates:
        parsed = _try_parse(candidate)
        if parsed is not None:
            return parsed

    # 4. Repair pass for objects truncated by the token limit.
    for candidate in candidates:
        repaired = _close_truncated(candidate)
        if repaired is not None:
            return repaired

    return {}


def schema_hint(model: Type[BaseModel]) -> str:
    """Generate a human-readable JSON schema description for LLM prompts.

    Shows the exact field names, types, and defaults the model must produce,
    recursively expanding nested objects and arrays.
    """
    schema = model.model_json_schema()
    return _describe_object(schema, schema, 0)
