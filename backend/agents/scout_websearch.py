"""
Scout Websearch Pipeline — Lightpanda + Puppeteer + BeautifulSoup

Architecture:
  1. Lightpanda headless browser renders JavaScript → final DOM
  2. Puppeteer extracts rendered HTML
  3. BeautifulSoup parses + extracts clean text
  4. LiteLLM summarizes into structured intelligence

Uses a Node.js subprocess for Lightpanda/Puppeteer (scout_scraper.mjs).
Python handles parsing, filtering, and LLM summarization.
"""

import asyncio
import os
import re
import signal
from bs4 import BeautifulSoup
from typing import Optional
from pydantic import BaseModel
from config import get_settings
from logging_config import get_logger
from agents.url_guard import UnsafeURLError, ensure_public_url

logger = get_logger(__name__)
settings = get_settings()

SCRAPER_SCRIPT = os.path.join(os.path.dirname(__file__), "scout_scraper.mjs")

STRIP_SELECTORS = [
    ".mw-editsection",
    ".navbox",
    ".infobox",
    ".sidebar",
    ".reflist",
    ".mw-references-wrap",
    ".thumb",
    ".toc",
    "table.ambox",
    "sup.reference",
    "script",
    "style",
    "nav",
    "footer",
    "header",
]

ELEMENTS_TO_EXTRACT = ["p", "h1", "h2", "h3", "li", "blockquote", "pre", "code"]

MAX_CHARS = 8000


class ScrapedPage(BaseModel):
    url: str
    title: str
    text: str
    length: int


async def _render_html(url: str) -> str:
    """Call Lightpanda+Puppeteer via Node.js, return rendered HTML.

    Async subprocess: a synchronous communicate() here would stall the whole
    FastAPI event loop for up to 45s on a single slow page. The process group
    is still used so the timeout can reap the whole tree.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "node", SCRAPER_SCRIPT, url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError:
        logger.error("node_not_found", hint="Install Node.js to use Lightpanda scraping")
        return ""
    except Exception as e:
        logger.error("scraper_error", error=str(e))
        return ""

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
    except asyncio.TimeoutError:
        logger.warning("scraper_timeout", url=url)
        _kill_process_group(proc)
        return ""

    if proc.returncode != 0:
        logger.warning("scraper_failed", url=url, stderr=(stderr or b"").decode("utf-8", "replace")[:200])
        return ""
    return stdout.decode("utf-8", "replace")


def _kill_process_group(proc) -> None:
    """SIGTERM the scraper's process group, then reap it."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()


def _parse_html(html: str) -> str:
    """BeautifulSoup: strip noise, extract clean text."""
    soup = BeautifulSoup(html, "html.parser")

    for selector in STRIP_SELECTORS:
        for el in soup.select(selector):
            el.decompose()

    title = ""
    title_el = soup.select_one("title, h1, #firstHeading")
    if title_el:
        title = title_el.get_text(strip=True)

    parts = []
    for tag in ELEMENTS_TO_EXTRACT:
        for el in soup.find_all(tag):
            text = el.get_text(strip=True)
            if text and len(text) > 10:
                parts.append(text)

    full = "\n".join(parts)
    if len(full) > MAX_CHARS:
        full = full[:MAX_CHARS] + "..."

    return f"{title}\n\n{full}" if title else full


async def scrape_page(url: str) -> ScrapedPage:
    """
    Lightpanda → rendered HTML → BeautifulSoup → clean text.
    """
    # URLs arrive from search results, so they are untrusted input.
    try:
        ensure_public_url(url)
    except UnsafeURLError as e:
        logger.warning("scrape_blocked_url", url=url[:100], reason=str(e))
        return ScrapedPage(url=url, title="(blocked)", text="", length=0)

    logger.info("scraping", url=url[:100])

    html = await _render_html(url)
    if not html:
        logger.warning("empty_html", url=url[:100])
        return ScrapedPage(url=url, title="(scrape failed)", text="", length=0)

    text = _parse_html(html)

    title = text.split("\n")[0] if text else "(unknown)"

    return ScrapedPage(url=url, title=title, text=text, length=len(text))


async def summarize_page(page: ScrapedPage, topic: str) -> str:
    """Summarize a scraped page using LLM."""
    if not page.text:
        return "(no content extracted)"

    from config import llm_complete
    system = "Summarize concisely. 2-4 sentences. Facts only."
    user = f"Topic: {topic}\n\nPage: {page.title}\nContent:\n{page.text}"

    try:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ]
        response = await llm_complete(messages, temperature=0.3, max_tokens=4096)
        return response.content
    except Exception as e:
        logger.warning("summarization_failed", error=str(e))
        return f"(summarization failed: {e})"


#: MediaWiki search endpoint. Using the API rather than the human search page
#: is deliberate — see `search_wikipedia`.
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"


def _title_relates(title: str, query: str) -> bool:
    """Does this article title plausibly have anything to do with the query?

    MediaWiki's text search always returns its best guess, however bad — asked
    for "matryoshka representation learning" it offers "Cultural icon", and for
    "asyncio task cancellation" it offers "Futures and promises". Handing those
    to the summariser yields a confident paragraph about the wrong subject,
    which is worse than admitting no source was found.

    Overlap is checked in both directions so that "pgvector" still matches the
    "Vector database" article — the query word contains the title word rather
    than the other way round.
    """
    def words(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}

    title_words, query_words = words(title), words(query)
    if not title_words or not query_words:
        return False
    return any(q in t or t in q for q in query_words for t in title_words)


async def search_wikipedia(query: str) -> Optional[str]:
    """Resolve a Wikipedia article URL for a topic.

    Queries the MediaWiki API instead of scraping
    ``w/index.php?search=<q>`` and looking for a redirect to ``/wiki/``. That
    page only redirects on an *exact title match* — for an ordinary query it
    renders a search-results page and stays on ``w/index.php``, so the old
    check returned ``None`` for every real query, including ones with an
    obvious article.

    The API fixes that, but always answers, so results are filtered through
    `_title_relates` before being trusted.
    """
    import httpx
    from agents.web_search import scout_headers

    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": 3,
        "format": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                WIKIPEDIA_API, params=params, headers=scout_headers("application/json")
            )
            resp.raise_for_status()
            hits = ((resp.json().get("query") or {}).get("search") or [])
    except Exception as e:
        logger.warning("wikipedia_lookup_failed", query=query, error=str(e))
        return None

    for hit in hits:
        title = str(hit.get("title") or "")
        if _title_relates(title, query):
            return f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"

    logger.info("wikipedia_no_relevant_article", query=query[:60],
                offered=[h.get("title") for h in hits][:3])
    return None


async def scout_gather(query: str) -> dict:
    """
    Full Scout reconnaissance pipeline:
      1. Search SearXNG (broad web search across 70+ engines)
      2. Scrape top results (httpx fast path → Lightpanda fallback)
      3. Also search Wikipedia as supplementary source
      4. LiteLLM summarizes all scraped pages in parallel
      5. Return structured findings
    """
    import asyncio as aio
    findings = {"topic": query, "sources": [], "summaries": [], "raw_pages": []}

    # Primary: SearXNG broad web search
    try:
        from agents.web_search import search_and_scrape
        search_data = await search_and_scrape(query, num_results=5, scrape_top=2)

        for r in search_data.get("results", []):
            findings["sources"].append({
                "url": r.get("url", ""),
                "type": r.get("engine", "web"),
                "title": r.get("title", ""),
            })

        # Summarize all scraped pages in parallel
        scraped = search_data.get("scraped", [])
        pages = [ScrapedPage(url=s["url"], title=s["title"], text=s["text"], length=len(s["text"])) for s in scraped]
        summaries = await aio.gather(*[summarize_page(p, query) for p in pages], return_exceptions=True)

        for s, summary in zip(scraped, summaries):
            if isinstance(summary, Exception):
                summary = f"(summarization failed)"
            findings["summaries"].append({"source": s["title"], "summary": summary})

        # Include raw scraped text alongside summaries
        for s in scraped:
            findings["raw_pages"].append({
                "url": s["url"], "title": s["title"], "text": s["text"]
            })
    except Exception as e:
        logger.warning("searxng_pipeline_failed", error=str(e))

    # Supplementary: Wikipedia (if SearXNG didn't find enough)
    if len(findings["summaries"]) < 2:
        wiki_url = await search_wikipedia(query)
        if wiki_url:
            page = await scrape_page(wiki_url)
            findings["sources"].append({
                "url": wiki_url, "type": "wikipedia", "title": page.title
            })
            summary = await summarize_page(page, query)
            findings["summaries"].append({"source": page.title, "summary": summary})
            findings["raw_pages"].append({
                "url": wiki_url, "title": page.title, "text": page.text
            })

    return findings
