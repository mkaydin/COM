// One page per process. Electron on this host SIGTRAPs if several
// BrowserWindows are created and destroyed in a single process, so the
// shell drives a loop instead.
//
//   npx electron capture-one.js <page> <outDir>
const fs = require('fs');
const path = require('path');
const { app, BrowserWindow } = require('electron');

const WEB = path.join(__dirname, '..', 'com_website');

const page = process.argv[process.argv.length - 2];
const out = process.argv[process.argv.length - 1];

app.commandLine.appendSwitch('force-high-contrast-support', 'true');
fs.mkdirSync(out, { recursive: true });

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1280, height: 900, show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  const errors = [];
  win.webContents.on('console-message', (_e, l, m) => {
    if (l >= 2) errors.push(m);
  });

  const loaded = new Promise((resolve) => {
    win.webContents.on('did-finish-load', resolve);
    win.webContents.on('did-fail-load', () => resolve());
  });
  win.loadURL('file://' + path.join(WEB, page)).catch(() => {});
  await Promise.race([loaded, new Promise((r) => setTimeout(r, 20000))]);
  await new Promise((r) => setTimeout(r, 6000));

  const img = await win.webContents.capturePage();
  fs.writeFileSync(path.join(out, page.replace('.html', '.png')), img.toPNG());
  fs.writeFileSync(path.join(out, page.replace('.html', '.log')),
                   JSON.stringify({ chars: await win.webContents.executeJavaScript(
                     '(document.body.innerText||"").trim().length'), errors }));
  app.exit(0);
});