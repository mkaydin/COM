"""Structure-aware chunking per RAG spec §2.4.

Detects section boundaries (markdown # headings), creates small retrieval
chunks (~300-500 tokens) within sections, and links each chunk to its parent
section (~1500-2000 tokens) for context expansion at answer time.

Never chunks across a section boundary — mixing "Methods" and "Results" text
in one chunk degrades retrieval precision for science content.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Token estimation: ~4 chars per token for English text.
_CHARS_PER_TOKEN = 4

# Small retrieval chunk: ~300-500 tokens.
MIN_CHUNK_TOKENS = 300
MAX_CHUNK_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 80

# Parent section: ~1500-2000 tokens.
MAX_PARENT_TOKENS = 2000

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


@dataclass
class Chunk:
    """A single retrieval chunk with its parent-section context."""
    text: str
    section: str
    chunk_index: int
    parent_section_text: str
    token_estimate: int = 0


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split text into (heading, body) sections.

    Markdown headings (# Foo) delimit sections. Text before the first heading
    goes into a "( preamble)" section. Non-markdown text (no headings) is
    treated as a single section titled "( document)".
    """
    if not text or not text.strip():
        return []

    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("(document)", text.strip())]

    sections: list[tuple[str, str]] = []

    # Preamble before first heading
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append(("(preamble)", preamble))

    for i, m in enumerate(matches):
        heading = m.group(2).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if body:
            sections.append((heading, body))

    return sections


def _split_long_section(
    heading: str, body: str, target_tokens: int, overlap_tokens: int
) -> list[str]:
    """Split a section body into overlapping sub-chunks of ~target_tokens."""
    target_chars = target_tokens * _CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * _CHARS_PER_TOKEN

    if len(body) <= target_chars:
        return [body]

    chunks: list[str] = []
    start = 0
    while start < len(body):
        end = start + target_chars

        # Try to break at a sentence/paragraph boundary near the target.
        if end < len(body):
            for boundary in (". ", ".\n", "\n\n", "\n"):
                last_break = body.rfind(boundary, start + target_chars // 2, end + 200)
                if last_break != -1:
                    end = last_break + len(boundary)
                    break

        chunk = body[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(body):
            break
        start = end - overlap_chars
        if start < 0:
            start = 0

    return chunks


def chunk_document(
    text: str,
    *,
    min_chunk_tokens: int = MIN_CHUNK_TOKENS,
    max_chunk_tokens: int = MAX_CHUNK_TOKENS,
    chunk_overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
    max_parent_tokens: int = MAX_PARENT_TOKENS,
) -> list[Chunk]:
    """Chunk a document into structure-aware retrieval units with parent context.

    Returns a list of Chunk objects. Each chunk:
      - Stays within a single section (never crosses headings).
      - Is ~300-500 tokens for retrieval precision.
      - Carries a `parent_section_text` (~1500-2000 tokens) for context expansion.
    """
    if not text or not text.strip():
        return []

    sections = _split_sections(text.strip())
    if not sections:
        return []

    chunks: list[Chunk] = []
    chunk_idx = 0

    for heading, body in sections:
        # Truncate parent section to max_parent_tokens for the context window.
        parent_chars = max_parent_tokens * _CHARS_PER_TOKEN
        parent_text = body[:parent_chars]
        if len(body) > parent_chars:
            parent_text += "...[section truncated]"

        # Split the section body into retrieval-sized chunks.
        sub_texts = _split_long_section(heading, body, max_chunk_tokens, chunk_overlap_tokens)

        # Merge tiny sub-chunks (below min_chunk_tokens) into one.
        if len(sub_texts) == 1 and _estimate_tokens(sub_texts[0]) < min_chunk_tokens:
            pass  # Keep the single small chunk — better than losing the content.

        for sub_text in sub_texts:
            chunks.append(
                Chunk(
                    text=sub_text,
                    section=heading,
                    chunk_index=chunk_idx,
                    parent_section_text=parent_text,
                    token_estimate=_estimate_tokens(sub_text),
                )
            )
            chunk_idx += 1

    return chunks
