#!/usr/bin/env node
/**
 * Harvest the Material Symbols glyphs each page renders, into /tmp/icons.json.
 *
 * One Electron process per page: on this host Electron SIGTRAPs when several
 * BrowserWindows are created and destroyed in a single process.
 *
 *   node desktop/build-icons.js
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const WEB = path.join(__dirname, '..', 'com_website');
const ELECTRON = path.join(__dirname, 'node_modules', '.bin', 'electron');
const COLLECTOR = path.join(__dirname, 'collect-icons.js');

const PAGES = [
  'forum.html', 'topic.html', 'history.html', 'models.html', 'llm_settings.html',
  'council_chamber.html', 'divination.html', 'agent_profiles.html', 'runic_library.html',
  'setup.html', 'council_landing_page.html', 'council_rules.html', 'deliberation_view.html',
];

function main() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'com-icons-'));
  const all = new Set();
  let failed = [];

  for (const page of PAGES) {
    const out = path.join(tmp, `${page}.json`);
    try {
      execFileSync(ELECTRON, [COLLECTOR, page, out], {
        stdio: ['ignore', 'ignore', 'ignore'],
        timeout: 90000,
      });
      const found = JSON.parse(fs.readFileSync(out, 'utf8'));
      found.forEach((i) => all.add(i));
    } catch (e) {
      // A page that will not load is not a reason to ship a font missing its
      // icons, but it must be reported rather than silently skipped.
      failed.push(page);
    }
  }

  fs.rmSync(tmp, { recursive: true, force: true });
  fs.writeFileSync('/tmp/icons.json', JSON.stringify([...all].sort(), null, 1));
  process.stdout.write(`harvested ${all.size} icon(s) from ${PAGES.length} page(s)\n`);
  if (failed.length) {
    process.stdout.write(`FAILED to load: ${failed.join(', ')}\n`);
    process.exitCode = 1;
  }
}

main();