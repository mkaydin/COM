import os
import json
import re
from datetime import datetime, timezone
from pathlib import Path

WIKI_ROOT = Path(__file__).parent.parent.parent / "sage_wiki"
WIKI_DIR = WIKI_ROOT / "wiki"
RAW_DIR = WIKI_ROOT / "raw"


def _read_file(path: Path) -> str:
    try:
        return path.read_text()
    except Exception:
        return ""


def _write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _slugify(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


def load_index() -> str:
    return _read_file(WIKI_DIR / "index.md")


def load_log() -> str:
    return _read_file(WIKI_DIR / "log.md")


def search_wiki(query: str, max_pages: int = 5) -> list[dict]:
    index = load_index()
    results = []

    for md_file in WIKI_DIR.rglob("*.md"):
        rel = md_file.relative_to(WIKI_DIR)
        if rel.name in ("index.md", "log.md"):
            continue
        content = _read_file(md_file)
        query_lower = query.lower()
        if query_lower in str(rel).lower() or query_lower in content.lower()[:2000]:
            results.append({
                "path": "wiki/" + str(rel),
                "content": content[:3000],
                "title": rel.stem.replace("-", " ").title(),
            })
        if len(results) >= max_pages:
            break

    return results


def update_index(entry: str):
    index = load_index()
    if entry not in index:
        index += f"\n- {entry}"
        _write_file(WIKI_DIR / "index.md", index)


def append_log(entry: str):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    log = load_log()
    log += f"{entry}\n"
    _write_file(WIKI_DIR / "log.md", log)


def write_wiki_page(category: str, slug: str, title: str, content: str, tags: list[str] = None):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    frontmatter = f"""---
title: "{title}"
type: {category}
tags: {json.dumps(tags or [])}
created: {today}
updated: {today}
status: draft
---
"""
    path = WIKI_DIR / category / f"{slug}.md"
    _write_file(path, frontmatter + "\n" + content)
    update_index(f"[{today}] [[wiki/{category}/{slug}]] — {title}")

    return str(path)


async def ingest_source(source_path: str, title: str) -> dict:
    """Ingest a raw source file into the wiki."""
    content = _read_file(Path(source_path))
    if not content:
        return {"status": "failed", "reason": "empty source"}

    slug = _slugify(title)
    summary_path = write_wiki_page("sources", slug, title, content[:5000], ["ingested"])

    append_log(f"## [{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] ingest | {title}\n- Created: [[wiki/sources/{slug}]]")

    return {"status": "ingested", "path": str(summary_path), "title": title}


async def file_insight(query: str, sage_response: dict) -> dict:
    """
    File a good Sage insight back into the wiki as a persistent page.
    Called after deliberation if the Sage response is valuable.
    """
    synthesis = sage_response.get("synthesis", "")
    if not synthesis or len(synthesis) < 50:
        return {"status": "skipped", "reason": "insight too short"}

    slug = _slugify(query[:60])
    content = f"""## Query
{query}

## Sage Synthesis
{synthesis}

## Analogies
""" + "\n".join(
        f"- **{a['domain']}**: {a['concept']} → {a['application']}"
        for a in sage_response.get("analogies", [])
    )

    if sage_response.get("ethical_dimensions"):
        content += "\n\n## Ethical Dimensions\n" + "\n".join(f"- {e}" for e in sage_response["ethical_dimensions"])

    if sage_response.get("speculation_warning"):
        content += "\n\n> ⚠️ **Speculative**: This synthesis involves interpretation beyond documented facts."

    path = write_wiki_page("queries", slug, query[:80], content, ["deliberation", "sage"])

    append_log(f"## [{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] query | {query[:60]}\n- Filed: [[wiki/queries/{slug}]]")

    return {"status": "filed", "path": str(path)}


async def lint_wiki() -> dict:
    """Health-check the wiki for issues."""
    issues = []
    pages = {}
    for md_file in WIKI_DIR.rglob("*.md"):
        rel = str(md_file.relative_to(WIKI_DIR))
        if rel in ("index.md", "log.md"):
            continue
        content = _read_file(md_file)
        pages[rel] = content

    for path, content in pages.items():
        if "status: draft" in content:
            issues.append({"type": "draft", "page": path})

    for path, content in pages.items():
        page_slug = path.split("/")[-1].replace(".md", "")
        ref_count = 0
        for other_path, other_content in pages.items():
            if other_path != path and page_slug in other_content:
                ref_count += 1
        if ref_count == 0:
            issues.append({"type": "orphan", "page": path, "note": "no inbound links"})

    contradictions = []
    all_content = " ".join(pages.values()).lower()
    for path, content in pages.items():
        if "contradiction" in content.lower() or "⚠️" in content:
            contradictions.append({"type": "flagged_contradiction", "page": path})
    issues.extend(contradictions)

    append_log(f"## [{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] lint | {len(issues)} issues found")
    for issue in issues:
        append_log(f"  - {issue['type']}: {issue['page']}")

    return {"issues": issues, "total_pages": len(pages), "total_issues": len(issues)}
