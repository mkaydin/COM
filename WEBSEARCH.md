# Web Search + LLM Summarization Pipeline

Headless browser scraping with Lightpanda/Puppeteer, summarized via LM Studio.

## Architecture

```
User keywords → Wikipedia Search → Lightpanda Browser → Puppeteer → Page Scrape
                                                                         ↓
                                                                   Raw content
                                                                         ↓
                        LM Studio API (google/gemma-4-e4b) → Summarized text → stdout
```

## Dependencies

```json
{
  "@lightpanda/browser": "^1.2.0",
  "puppeteer-core": "^24.43.0"
}
```

LM Studio must be running locally with the model loaded at `http://127.0.0.1:1234`.

## The Lightpanda + Puppeteer Pattern

Both scripts share this boilerplate. Reusable for any headless scraping task:

```js
import { lightpanda } from '@lightpanda/browser';
import puppeteer from 'puppeteer-core';

const LIGHTPANDA_OPTS = { host: '127.0.0.1', port: 9222 };

async function run() {
  const proc = await lightpanda.serve(LIGHTPANDA_OPTS);
  let browser;
  try {
    browser = await puppeteer.connect({
      browserWSEndpoint: `ws://127.0.0.1:9222`,
    });
    const context = await browser.createBrowserContext();
    const page = await context.newPage();

    // --- your scraping logic here ---

    await page.close();
    await context.close();
  } finally {
    if (browser) await browser.disconnect();
    proc.kill(); // always kill, even on error
  }
}
```

### Key points
| Concept | Detail |
|---|---|
| `lightpanda.serve()` | Starts a headless browser process, returns a child process handle |
| `puppeteer.connect()` | Connects to an already-running browser via WebSocket (unlike `puppeteer.launch()`) |
| `try/finally` | **Required.** Without it, a crash leaves a zombie Lightpanda process |
| `page.goto()` timeout | Default is 30s (no timeout otherwise, can hang forever) |
| `waitUntil` | Use `'networkidle2'` for SPAs/content-heavy pages to ensure JS has rendered |

## Wikipedia Scraping Strategy

### Step 1: Search
```
https://en.wikipedia.org/w/index.php?search={QUERY}
```
Wikipedia either:
- **Redirects** to the article if there's an exact match (url will contain `/wiki/ArticleTitle`)
- **Shows search results** with links in `.mw-search-result-heading a`

### Step 2: Detect redirect vs. results page
```js
const url = page.url();
if (url.includes('/wiki/') && !url.includes('w/index.php')) {
  // Landed directly on article — scrape now
} else {
  // Grab first search result link and navigate to it
  const href = await page.evaluate(() =>
    document.querySelector('.mw-search-result-heading a')?.getAttribute('href')
  );
  await page.goto(`https://en.wikipedia.org${href}`);
}
```

### Step 3: Extract clean content
Selectors to **keep**: `#firstHeading` (title), `#mw-content-text .mw-parser-output` (body).

Selectors to **remove** before text extraction:
```js
[
  '.mw-editsection',    // [edit] links
  '.navbox',            // navigation boxes at bottom
  '.infobox',           // right-side info tables
  '.sidebar',           // side panels
  '.reflist',           // reference lists
  '.mw-references-wrap',// reference popups
  '.thumb',             // image thumbnails
  '.toc',               // table of contents
  'table.ambox',        // warning banners
  'sup.reference',      // citation numbers like [1]
]
```

Extract text from `p, h2, h3, li` elements. Cap at ~8000 characters to fit LLM context windows.

## LM Studio API Integration

LM Studio exposes an OpenAI-compatible chat completions endpoint.

```
POST http://127.0.0.1:1234/v1/chat/completions
Content-Type: application/json

{
  "model": "google/gemma-4-e4b",
  "messages": [
    { "role": "system", "content": "You are a helpful assistant..." },
    { "role": "user", "content": "Article text + instructions..." }
  ],
  "temperature": 0.3,
  "max_tokens": 1024
}
```

Response shape: `data.choices[0].message.content`

### Reusable fetch wrapper
```js
const LM_STUDIO_URL = 'http://127.0.0.1:1234/v1/chat/completions';

async function askLLM(systemPrompt, userPrompt, model = 'google/gemma-4-e4b') {
  const res = await fetch(LM_STUDIO_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userPrompt },
      ],
      temperature: 0.3,
      max_tokens: 1024,
    }),
  });
  if (!res.ok) throw new Error(`LM Studio error (${res.status}): ${await res.text()}`);
  const data = await res.json();
  return data.choices?.[0]?.message?.content;
}
```

## Usage

```bash
# Ensure LM Studio is running with the model loaded
node websearch.mjs artificial intelligence
```

## Files in This Project

| File | Purpose |
|---|---|
| `index.mjs` | Scrape Wikipedia "web browser" page — extract external reference links |
| `extract_data.mjs` | Search Hacker News for "lightpanda" — extract story titles/URLs/meta |
| `websearch.mjs` | Search Wikipedia by keyword, scrape article, summarize via LLM |
| `package.json` | Project config (note: `type: "commonjs"` is overridden by `.mjs` extensions) |
