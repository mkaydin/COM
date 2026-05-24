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

import subprocess
import os
from bs4 import BeautifulSoup
from typing import Optional
from pydantic import BaseModel
from config import get_settings
from logging_config import get_logger

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


def _render_html(url: str) -> str:
    """Call Lightpanda+Puppeteer via Node.js subprocess, return rendered HTML."""
    try:
        result = subprocess.run(
            ["node", SCRAPER_SCRIPT, url],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if result.returncode != 0:
            logger.warning("scraper_failed", url=url, stderr=result.stderr[:200])
            return ""
        return result.stdout
    except subprocess.TimeoutExpired:
        logger.warning("scraper_timeout", url=url)
        return ""
    except FileNotFoundError:
        logger.error("node_not_found", hint="Install Node.js to use Lightpanda scraping")
        return ""


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
    logger.info("scraping", url=url[:100])

    html = _render_html(url)
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

    from litellm import completion
    system = "You are a tactical intelligence analyst. Summarize key information concisely. Focus on facts, signals, and novelty."
    user = f"Topic: {topic}\n\nPage: {page.title}\nContent:\n{page.text}"

    try:
        response = completion(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            temperature=0.3,
            max_tokens=512,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.warning("summarization_failed", error=str(e))
        return f"(summarization failed: {e})"


async def search_wikipedia(query: str) -> Optional[str]:
    """Resolve a Wikipedia article URL for a topic (uses httpx for lightweight API call)."""
    import httpx
    url = f"https://en.wikipedia.org/w/index.php?search={query.replace(' ', '+')}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "CouncilOfMinds-Scout/1.0"})
            resp.raise_for_status()
            final_url = str(resp.url)
            if "/wiki/" in final_url and "w/index.php" not in final_url:
                return final_url
    except Exception as e:
        logger.warning("wikipedia_lookup_failed", query=query, error=str(e))
    return None


async def scout_gather(query: str) -> dict:
    """
    Full Scout reconnaissance pipeline:
      1. Resolve Wikipedia article URL
      2. Lightpanda renders the page
      3. BeautifulSoup extracts clean text
      4. LiteLLM summarizes
      5. Return structured findings
    """
    findings = {"topic": query, "sources": [], "summaries": []}

    wiki_url = await search_wikipedia(query)
    if wiki_url:
        page = await scrape_page(wiki_url)
        findings["sources"].append({
            "url": wiki_url, "type": "wikipedia", "title": page.title
        })
        summary = await summarize_page(page, query)
        findings["summaries"].append({"source": page.title, "summary": summary})

    return findings
