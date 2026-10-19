// Collect the Material Symbols glyphs a page actually renders.
//
//   npx electron collect-icons.js <page.html> /tmp/icons-<page>.json
//
// Regex over the HTML is not enough: icons reach the DOM three ways — as inline
// text, from an `icon:` object property, and from a NAV array like
// `['models.html', 'tune', 'Models']`. A first attempt caught the first two and
// missed the third, which is how the subset font shipped without `tune` and the
// nav rendered the literal word "TUNE".
//
// Reading the rendered DOM catches all of them, including icons only created
// after a fetch resolves. One page per process: Electron on this host SIGTRAPs
// when several BrowserWindows are created and destroyed in a single process.
const fs = require('fs');
const path = require('path');
const { app, BrowserWindow } = require('electron');

const WEB = path.join(__dirname, '..', 'com_website');
const page = process.argv[process.argv.length - 2];
const out = process.argv[process.argv.length - 1];

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1280, height: 900, show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  const loaded = new Promise((resolve) => {
    win.webContents.on('did-finish-load', resolve);
    win.webContents.on('did-fail-load', () => resolve());
  });
  win.loadURL('file://' + path.join(WEB, page)).catch(() => {});
  await Promise.race([loaded, new Promise((r) => setTimeout(r, 20000))]);
  await new Promise((r) => setTimeout(r, 4000));

  let found = [];
  try {
    found = await win.webContents.executeJavaScript(`
      [...document.querySelectorAll('.material-symbols-outlined')]
        .map(el => (el.textContent || '').trim())
        .filter(t => /^[a-z0-9_]+$/.test(t))
    `);
  } catch (e) {
    process.stderr.write(`${page}: ${e.message}\n`);
  }
  fs.writeFileSync(out, JSON.stringify(found));
  app.exit(0);
});