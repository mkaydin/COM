// Prove the offline claim: block every hostname, then check the page still
// renders styled.
//
//   npx electron offline-check.js <page.html>
//
// `--host-resolver-rules "MAP * ~NOTFOUND"` makes every DNS lookup fail, so any
// request to a CDN errors exactly as it would on an air-gapped machine. What is
// then checked is not "no request was attempted" but whether the page actually
// looks right: theme colours applied, the pixel font loaded, and the icon font
// producing glyphs rather than ligature text.
const fs = require('fs');
const path = require('path');
const { app, BrowserWindow } = require('electron');

// `EXCLUDE localhost` keeps the local backend reachable — this is about the
// internet, not the API. Blocking localhost too is a useful variant: it
// exercises the "cannot reach backend" error state, which also renders styled.
app.commandLine.appendSwitch(
  'host-resolver-rules',
  'MAP * ~NOTFOUND, EXCLUDE localhost',
);

const WEB = path.join(__dirname, '..', 'com_website');
const page = process.argv[process.argv.length - 1];

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1280, height: 900, show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });

  const netErrors = [];
  win.webContents.on('did-fail-load', (_e, code, desc, url) => {
    if (url && /^https?:/.test(url)) netErrors.push(`${code} ${desc} ${url}`);
  });

  const loaded = new Promise((r) => {
    win.webContents.on('did-finish-load', r);
    win.webContents.on('did-fail-load', () => r());
  });
  win.loadURL('file://' + path.join(WEB, page)).catch(() => {});
  await Promise.race([loaded, new Promise((r) => setTimeout(r, 20000))]);
  await new Promise((r) => setTimeout(r, 6000));

  const report = await win.webContents.executeJavaScript(`(() => {
    const body = getComputedStyle(document.body);
    const probe = document.createElement('div');
    probe.className = 'text-runed-gold';
    document.body.appendChild(probe);
    const runed = getComputedStyle(probe).color;
    probe.remove();

    const icon = document.querySelector('.material-symbols-outlined');
    // If the icon font is absent the element's text is the ligature name and
    // the glyph renders as words.
    const iconText = icon ? (icon.textContent || '').trim() : '';
    const iconFont = icon ? getComputedStyle(icon).fontFamily : '';

    const el = document.querySelector('h1, h2, .font-pixel-display, .font-pixel-heading');
    const pixelFont = el ? getComputedStyle(el).fontFamily : '';
    const loaded = [...document.fonts].filter(f => f.status === 'loaded')
      .map(f => f.family + ' ' + f.weight);

    return {
      bodyBg: body.backgroundColor,
      bodyColor: body.color,
      runedGold: runed,
      iconFont, iconText,
      pixelFont,
      fontsLoaded: [...new Set(loaded)],
      stylesheets: [...document.styleSheets].map(s => (s.href || 'inline').split('/').pop()),
    };
  })()`);

  const out = { page, networkFailures: netErrors, ...report };
  fs.writeFileSync('/tmp/offline-check.json', JSON.stringify(out, null, 1));
  const img = await win.webContents.capturePage();
  fs.writeFileSync(`/tmp/offline-${page.replace('.html', '')}.png`, img.toPNG());
  app.exit(0);
});