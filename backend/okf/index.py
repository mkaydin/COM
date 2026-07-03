from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from pathlib import Path
from typing import Callable

from okf.document import OKFDocument

_INDEX_FILE = "index.md"
_FALLBACK_MODEL = "lm-studio"


def _load_doc(path: Path) -> OKFDocument | None:
    try:
        return OKFDocument.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _build_index_text(entries: list[tuple[str, str, str, str]]) -> str:
    # entries: (type, title, relative_link, description)
    grouped: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for typ, title, link, desc in entries:
        grouped[typ or "Other"].append((title, link, desc))

    sections: list[str] = []
    for typ in sorted(grouped):
        lines = [f"# {typ}", ""]
        for title, link, desc in sorted(grouped[typ], key=lambda e: e[0].lower()):
            suffix = f" - {desc}" if desc else ""
            lines.append(f"* [{title}]({link}){suffix}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + "\n"


def _directories_to_index(bundle_root: Path) -> list[Path]:
    dirs: set[Path] = set()
    for md in bundle_root.rglob("*.md"):
        cur = md.parent
        while cur != bundle_root.parent:
            dirs.add(cur)
            if cur == bundle_root:
                break
            cur = cur.parent
    return sorted(dirs)


async def regenerate_indexes(
    bundle_root: Path,
    *,
    model: str = _FALLBACK_MODEL,
    synthesize: Callable[..., object] | None = None,
) -> list[Path]:
    """Walk the bundle and (re)write index.md for every non-empty directory.

    Mirrors the upstream contract:
      - Each index groups children by frontmatter `type` under `# <Type>` headings.
      - Root index uses `# Subdirectories`.
      - Single-child subdirectory reuses that child's description (synthesize
        NOT called); multi-child subdirectory calls synthesize.
      - Empty directories are skipped.

    `synthesize` may be sync or async. When None, uses okf.synthesizer.
    """
    if synthesize is None:
        from okf.synthesizer import synthesize_description
        synthesize = synthesize_description

    bundle_root = Path(bundle_root)
    written: list[Path] = []
    if not bundle_root.exists():
        return written

    directories = sorted(
        _directories_to_index(bundle_root),
        key=lambda p: (-len(p.relative_to(bundle_root).parts), str(p)),
    )

    dir_descriptions: dict[Path, str] = {}

    async def _maybe_synth(rel: str, pairs: list[tuple[str, str]]) -> str:
        result = synthesize(rel, pairs, model=model)
        if inspect.isawaitable(result):
            result = await result
        return str(result)

    for directory in directories:
        entries: list[tuple[str, str, str, str]] = []

        for child in sorted(directory.iterdir()):
            if child.name == _INDEX_FILE:
                continue
            if child.is_file() and child.suffix == ".md":
                doc = _load_doc(child)
                if doc is None:
                    continue
                fm = doc.frontmatter
                title = str(fm.get("title") or child.stem)
                desc = str(fm.get("description") or "")
                typ = str(fm.get("type") or "")
                entries.append((typ, title, child.name, desc))
            elif child.is_dir():
                desc = dir_descriptions.get(child, "")
                entries.append(("Subdirectories", child.name, f"{child.name}/{_INDEX_FILE}", desc))

        if not entries:
            continue

        index_path = directory / _INDEX_FILE
        index_path.write_text(_build_index_text(entries), encoding="utf-8")
        written.append(index_path)

        if directory == bundle_root:
            continue

        pairs = [(title, desc) for _, title, _, desc in entries]
        if len(pairs) == 1 and pairs[0][1]:
            dir_descriptions[directory] = pairs[0][1]
        else:
            rel = str(directory.relative_to(bundle_root))
            dir_descriptions[directory] = await _maybe_synth(rel, pairs)

    return written
