from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """\
You are summarizing a directory in an Open Knowledge Format bundle in ONE \
sentence (max ~25 words).

Directory: {rel_path}
Contents:
{contents}

Write one sentence that names what this directory collectively contains. Be \
concrete and factual; do not editorialize. Output the sentence only — no \
preamble, no quotes, no trailing punctuation beyond a single period.
"""


def _fallback(children: list[tuple[str, str]]) -> str:
    titles = ", ".join(t for t, _ in children if t) or "no titled entries"
    return f"Contains {len(children)} entries: {titles}."


def synthesize_description_sync(
    rel_path: str,
    children: list[tuple[str, str]],
    *,
    model: str,
) -> str:
    """Synchronous fallback: never calls the LLM. Used when no event loop."""
    if not children:
        return ""
    return _fallback(children)


async def synthesize_description(
    rel_path: str,
    children: list[tuple[str, str]],
    *,
    model: str,
) -> str:
    """LLM-synthesize a one-sentence directory description.

    Routes through our config.llm_complete (local model friendly). Falls back
    to a deterministic template on any failure.
    """
    if not children:
        return ""
    contents = "\n".join(
        f"- {title}: {desc}" if desc else f"- {title}"
        for title, desc in children
    )
    prompt = _PROMPT_TEMPLATE.format(rel_path=rel_path, contents=contents)
    try:
        from config import llm_complete
        messages = [{"role": "user", "content": prompt}]
        response = await llm_complete(messages, temperature=0.2, max_tokens=256)
        text = (response.choices[0].message.content or "").strip()
        if not text:
            return _fallback(children)
        return text.splitlines()[0].strip()
    except Exception as exc:
        log.warning("synthesize_description failed for %s: %s", rel_path, exc)
        return _fallback(children)
