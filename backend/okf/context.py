from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolContext:
    """Process-global context for OKF bundle tools.

    Holds the active bundle root so write_concept_doc / read_concept can
    resolve concept ids to on-disk paths without threading state through
    every call (mirrors the upstream ToolContext, minus the BigQuery Source).
    """
    bundle_root: Path


@dataclass
class WebState:
    """Crawl-state machine for budgeted web ingestion (mirrors upstream)."""
    allowed_hosts: set[str]
    max_pages: int
    allowed_path_prefixes: tuple[str, ...] = ()
    denied_path_substrings: tuple[str, ...] = ()
    max_depth: int = 2
    visited: set[str] = field(default_factory=set)
    fetched_count: int = 0
    url_depth: dict[str, int] = field(default_factory=dict)


_ctx: ToolContext | None = None
_web: WebState | None = None


def set_context(bundle_root: Path) -> None:
    global _ctx
    _ctx = ToolContext(bundle_root=Path(bundle_root))


def get_context() -> ToolContext:
    if _ctx is None:
        raise RuntimeError(
            "OKF tool context not set. Call set_context() before invoking tools."
        )
    return _ctx


def set_web_state(
    allowed_hosts: set[str],
    max_pages: int,
    *,
    seeds: list[str] | None = None,
    allowed_path_prefixes: list[str] | None = None,
    denied_path_substrings: list[str] | None = None,
    max_depth: int = 2,
) -> None:
    global _web
    _web = WebState(
        allowed_hosts=set(allowed_hosts),
        max_pages=int(max_pages),
        allowed_path_prefixes=tuple(allowed_path_prefixes or ()),
        denied_path_substrings=tuple(denied_path_substrings or ()),
        max_depth=int(max_depth),
    )
    for seed in seeds or ():
        _web.url_depth[seed] = 0


def get_web_state() -> WebState:
    if _web is None:
        raise RuntimeError(
            "Web state not set. Call set_web_state() before web ingestion."
        )
    return _web


def clear_web_state() -> None:
    global _web
    _web = None


def is_web_pass() -> bool:
    """True while a web-ingestion crawl is active (guards augmentation)."""
    return _web is not None
