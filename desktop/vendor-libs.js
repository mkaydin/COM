#!/usr/bin/env node
/**
 * Vendor the two third-party libraries the pages load from jsDelivr.
 *
 * `marked` renders Markdown and **DOMPurify sanitises the result**. Beyond the
 * offline problem, the failure mode was the interesting part:
 *
 *     const html = window.marked ? marked.parse(src) : escapeHtml(src);
 *     return window.DOMPurify ? DOMPurify.sanitize(html) : html;
 *
 * If `marked` loaded but DOMPurify did not — which is exactly what a partial
 * CDN outage produces — the second line returns `marked`'s raw HTML with no
 * sanitising. Model output and user posts are rendered through this.
 *
 * Vendoring removes the network dependency; `sanitizeMarkdown` in both pages
 * makes the fallback fail closed, so a missing or partial library degrades to
 * escaped text instead of unfiltered HTML.
 *
 *   node desktop/vendor-libs.js
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const OUT = path.join(__dirname, '..', 'com_website', 'vendor');

const LIBS = [
  { file: 'marked.min.js', url: 'https://cdn.jsdelivr.net/npm/marked/marked.min.js' },
  { file: 'purify.min.js', url: 'https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js' },
];

function main() {
  fs.mkdirSync(OUT, { recursive: true });
  for (const lib of LIBS) {
    const dest = path.join(OUT, lib.file);
    execFileSync('curl', ['-fsSL', '--max-time', '60', lib.url, '-o', dest],
                 { stdio: ['ignore', 'ignore', 'pipe'] });
    const kb = fs.statSync(dest).size / 1024;
    process.stdout.write(`  ${lib.file.padEnd(18)} ${kb.toFixed(0)} kB\n`);
  }
  process.stdout.write(`\nvendored into com_website/vendor/\n`);
}

main();