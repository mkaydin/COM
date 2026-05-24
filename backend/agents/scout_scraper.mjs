import { lightpanda } from '@lightpanda/browser';
import puppeteer from 'puppeteer-core';

const LIGHTPANDA_OPTS = { host: '127.0.0.1', port: 9222 };

const url = process.argv[2];
if (!url) {
  console.error('Usage: node scout_scraper.mjs <url>');
  process.exit(1);
}

async function scrape() {
  const proc = await lightpanda.serve(LIGHTPANDA_OPTS);
  let browser;
  try {
    browser = await puppeteer.connect({
      browserWSEndpoint: `ws://127.0.0.1:9222`,
    });
    const context = await browser.createBrowserContext();
    const page = await context.newPage();

    await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 });

    const html = await page.content();
    process.stdout.write(html);

    await page.close();
    await context.close();
  } finally {
    if (browser) await browser.disconnect();
    proc.kill();
  }
}

scrape().catch((err) => {
  console.error(err);
  process.exit(1);
});
