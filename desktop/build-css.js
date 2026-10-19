#!/usr/bin/env node
/**
 * Build a static stylesheet per page, from that page's own Tailwind config.
 *
 * Why per page and not one stylesheet: the twelve pages do **not** share a
 * config. `agent_profiles.html` and `history.html` set `pixel-body: 16px`
 * where the others use 18px, and `counld_chamber.html` omits letter-spacing
 * entirely. Those look deliberate — the denser pages use smaller type — so a
 * single shared config would silently restyle them. One build per page keeps
 * every page looking exactly as it did under the runtime CDN.
 *
 * The point of the build is to stop loading Tailwind, VT323, Be Vietnam Pro
 * and Material Symbols from a CDN at runtime: a desktop app that renders
 * unstyled the moment it is offline is not much of a desktop app.
 *
 *   node desktop/build-css.js
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const REPO = path.join(__dirname, '..');
const WEB = path.join(REPO, 'com_website');
const TMP = path.join(REPO, '.build-tmp');

const TAILWIND = path.join(__dirname, 'node_modules', '.bin', 'tailwindcss');

/** Colour tokens injected into `text-${…}` / `border-${…}` at runtime.
 *  A static scanner cannot see through a template literal, so without this the
 *  character cards would render with no colour at all. */
const DYNAMIC_COLOURS = [
  'runed-gold', 'spectral-blue', 'ember-glow', 'mystic-purple',
  'success', 'error', 'warning', 'neutral-gray', 'parchment',
];

function safelist() {
  const out = [];
  for (const c of DYNAMIC_COLOURS) {
    out.push(`text-${c}`, `border-${c}`, `bg-${c}`);
    for (const op of [5, 10, 15, 20, 30, 40, 50]) out.push(`bg-${c}/${op}`, `border-${c}/${op}`);
  }
  return out;
}

/** Write a per-page config for the CLI to consume.

  The page's config object is reused verbatim — JSON.stringify cannot carry the
  theme's `lineHeight`/`letterSpacing` objects, and rewriting it by hand is how
  the build would drift from what the page actually renders. */
function writeConfig(slug, cfg) {
  const file = path.join(TMP, `${slug}.config.cjs`);
  fs.writeFileSync(
    file,
    `const __safelist = ${JSON.stringify(safelist())};\n` +
    `const __cfg = ${JSON.stringify(cfg, null, 2)};\n` +
    `__cfg.content = [];\n` +
    `__cfg.safelist = __safelist;\n` +
    `module.exports = __cfg;\n`
  );
  return file;
}

function main() {
  fs.mkdirSync(TMP, { recursive: true });

  const input = path.join(TMP, 'input.css');
  fs.writeFileSync(input, '@tailwind base;\n@tailwind components;\n@tailwind utilities;\n');

  // Configs now live in com_website/tailwind.config.js, keyed by page slug —
  // extracted from the pages by `extract-configs.js`. Reading them from the
  // HTML would require the inline `tailwind.config` script to stay, and that
  // script throws `ReferenceError: tailwind is not defined` once the CDN that
  // defined the global is gone.
  const allConfigs = require(path.join(WEB, 'tailwind.config.js'));

  const pages = fs.readdirSync(WEB).filter((f) => f.endsWith('.html'));
  let built = 0;
  const skipped = [];

  for (const page of pages) {
    const slug = page.replace(/\.html$/, '');
    const cfg = allConfigs[slug];
    if (!cfg) { skipped.push(page); continue; }

    const config = writeConfig(slug, cfg);
    const outFile = path.join(WEB, `tailwind.${slug}.css`);

    execFileSync(TAILWIND, [
      '-i', input,
      '-o', outFile,
      '--content', path.join(WEB, page),
      '--config', config,
      '--minify',
    ], { stdio: ['ignore', 'ignore', 'pipe'] });

    const bytes = fs.statSync(outFile).size;
    process.stdout.write(`  ${page.padEnd(28)} -> tailwind.${slug}.css  ${(bytes / 1024).toFixed(1)} kB\n`);
    built += 1;
  }

  process.stdout.write(`\nbuilt ${built} stylesheet(s)\n`);
  if (skipped.length) {
    process.stdout.write(`no entry in tailwind.config.js, skipped: ${skipped.join(', ')}\n`);
  }
}

main();