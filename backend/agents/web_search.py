"""
Web Search module — SearXNG metasearch + dual-path page scraping.

Architecture:
  1. Query SearXNG (local Docker) for search results across 70+ engines
  2. For each result URL, try httpx (fast) first, fall back to Lightpanda (JS rendering)
  3. Parse with BeautifulSoup either way
  4. Return structured results with titles, URLs, snippets, full text

SearXNG runs at http://localhost:8888 — JSON API at /search?format=json
"""
import asyncio
import json
from typing import List, Optional
from bs4 import BeautifulSoup
from config import get_settings
from logging_config import get_logger

logger = get_logger(__name__)
settings = get_settings()

SEARXNG_URL = get_settings().searxng_url
MAX_REDIRECTS = 5


def scout_headers(accept: str = "text/html") -> dict:
    """Headers for outbound research requests.

    Both halves matter, and each was learned from a real 403:

    * Wikimedia's robot policy rejects a User-Agent carrying no contact
      information — hence the configurable address rather than a bare
      ``CouncilOfMinds-Scout/1.0``.
    * It *also* refuses a request whose ``Accept`` is httpx's default ``*/*``.
      Holding the User-Agent constant and adding ``Accept: text/html`` turned
      a 403 into a 200, so the default was the whole problem.

    ``Accept`` is a parameter because SearXNG wants ``application/json``.
    """
    from config import get_settings

    return {
        "User-Agent": get_settings().scout_user_agent,
        "Accept": accept,
    }

STRIP_SELECTORS = [
    "script", "style", "nav", "footer", "header", "noscript",
    ".ad", ".ads", ".advertisement", ".sidebar", ".menu",
    ".cookie-notice", ".popup", ".modal", ".newsletter",
    ".related-posts", ".comments", ".comment-form",
    "iframe", "svg",
]

CONTENT_SELECTORS = ["article", "main", ".content", ".post-content", ".article-body", ".entry-content", "#content"]

MAX_CHARS = 8000


async def searxng_search(query: str, num_results: int = 8, categories: str = "general") -> List[dict]:
    """Query local SearXNG instance for web search results.

    Returns list of {url, title, snippet, engine} dicts.
    """
    import httpx

    params = {
        "q": query,
        "format": "json",
        "categories": categories,
        "pageno": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{SEARXNG_URL}/search",
                params=params,
                headers=scout_headers("application/json"),
            )
            resp.raise_for_status()
            data = resp.json()

            results = []
            for r in data.get("results", [])[:num_results]:
                results.append({
                    "url": r.get("url", ""),
                    "title": r.get("title", ""),
                    "snippet": r.get("content", ""),
                    "engine": r.get("engine", "unknown"),
                    "score": r.get("score", 0),
                })

            logger.info("searxng_results", query=query[:50], count=len(results))
            return results

    except httpx.ConnectError:
        logger.warning("searxng_unavailable", hint="SearXNG not running. Start with: docker compose up -d searxng")
        return []
    except Exception as e:
        logger.warning("searxng_search_failed", error=str(e))
        return []


async def scrape_with_httpx(url: str) -> Optional[str]:
    """Fast path: fetch HTML via httpx, parse with BeautifulSoup.

    Works for static pages. Returns None if content is too thin (likely JS-rendered).
    Redirects are walked manually so each hop is re-checked against the private
    network blocklist.
    """
    import httpx
    from agents.url_guard import UnsafeURLError, ensure_public_url

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            current = url
            html = None
            for _ in range(MAX_REDIRECTS + 1):
                ensure_public_url(current)
                resp = await client.get(current, headers=scout_headers())
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        return None
                    current = str(resp.url.join(location))
                    continue
                resp.raise_for_status()
                html = resp.text
                break
    except (UnsafeURLError, httpx.HTTPError) as e:
        logger.warning("httpx_scrape_skipped", url=url[:80], error=str(e))
        return None

    if html is None:
        return None
    return _parse_html(html)


async def scrape_with_lightpanda(url: str) -> Optional[str]:
    """Slow path: render with Lightpanda + Puppeteer, then parse.

    Used as fallback for JS-heavy pages (SPAs, dynamic content).
    """
    from agents.url_guard import UnsafeURLError, ensure_public_url
    try:
        ensure_public_url(url)
    except UnsafeURLError as e:
        logger.warning("lightpanda_scrape_blocked", url=url[:80], reason=str(e))
        return None

    try:
        from agents.scout_websearch import _render_html, _parse_html as scout_parse
        html = await _render_html(url)
        if not html:
            return None
        return scout_parse(html)
    except Exception as e:
        logger.warning("lightpanda_scrape_failed", url=url[:80], error=str(e))
        return None


def _parse_html(html: str) -> Optional[str]:
    """Extract clean text from HTML using BeautifulSoup."""
    soup = BeautifulSoup(html, "html.parser")

    for selector in STRIP_SELECTORS:
        for el in soup.select(selector):
            el.decompose()

    title = ""
    title_el = soup.select_one("title, h1, #firstHeading")
    if title_el:
        title = title_el.get_text(strip=True)

    parts = []
    for selector in CONTENT_SELECTORS:
        els = soup.select(selector)
        if els:
            for el in els:
                text = el.get_text(separator="\n", strip=True)
                if text and len(text) > 100:
                    parts.append(text)
            break

    if not parts:
        for tag in ["p", "h1", "h2", "h3", "li", "blockquote", "pre"]:
            for el in soup.find_all(tag):
                text = el.get_text(strip=True)
                if text and len(text) > 20:
                    parts.append(text)

    full = "\n".join(parts)
    if len(full) < 100:
        return None

    if len(full) > MAX_CHARS:
        full = full[:MAX_CHARS] + "..."

    return f"{title}\n\n{full}" if title else full


async def scrape_page(url: str) -> Optional[str]:
    """Dual-path scraping: try httpx first, fall back to Lightpanda.

    Returns clean text or None if both paths fail.
    """
    text = await scrape_with_httpx(url)
    if text and len(text) > 200:
        return text

    logger.info("falling_back_to_lightpanda", url=url[:60])
    text = await scrape_with_lightpanda(url)
    return text


async def search_and_scrape(
    query: str,
    num_results: int = 5,
    scrape_top: int = 3,
    categories: str = "general",
) -> dict:
    """Full search + scrape pipeline.

    1. Search SearXNG for results
    2. Scrape top N results (dual-path httpx/Lightpanda)
    3. Return structured intelligence

    Returns:
        {
            "query": str,
            "results": [{url, title, snippet, engine}],
            "scraped": [{url, title, text}],
        }
    """
    results = await searxng_search(query, num_results=num_results, categories=categories)

    scraped = []
    scrape_tasks = [scrape_page(r["url"]) for r in results[:scrape_top] if r.get("url")]
    scrape_results = await asyncio.gather(*scrape_tasks, return_exceptions=True)

    for r, text in zip(results[:scrape_top], scrape_results):
        if isinstance(text, Exception) or not text:
            continue
        scraped.append({
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "text": text,
        })
        logger.info("scraped", url=r.get("url","")[:60], chars=len(text))

    return {
        "query": query,
        "results": results,
        "scraped": scraped,
    }
