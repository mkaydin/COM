// Screenshot harness: loads a page from com_website/ and captures it.
//
//   electron shot.js <page.html> <out.png> [width] [height] [extraJS] [waitFor]
//
// `waitFor` is a CSS selector: the capture waits for a matching element to
// appear, because these pages populate from the API and a screenshot taken on
// a fixed delay catches the loading state instead of the page.
const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

const REPO = path.join(__dirname, '..');
const WEB = path.join(REPO, 'com_website');

const [page = 'forum.html', out = 'shot.png', w = '1440', h = '1000', extraJs = '', waitFor = ''] =
  process.argv.slice(2);

app.commandLine.appendSwitch('force-high-contrast-support', 'true');

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: parseInt(w, 10),
    height: parseInt(h, 10),
    show: false,
    backgroundColor: '#141315',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Surface page errors: a screenshot of a broken page looks identical to a
  // screenshot of a slow one, and guessing which it is wastes a cycle.
  win.webContents.on('console-message', (_e, level, message, line, source) => {
    if (level >= 2) console.error(`[page] ${message} (${source}:${line})`);
  });
  win.webContents.on('render-process-gone', (_e, details) =>
    console.error(`[page] renderer gone: ${details.reason}`)
  );

  await win.loadFile(path.join(WEB, page));

  if (waitFor) {
    const deadline = Date.now() + 45000;
    let ok = false;
    while (Date.now() < deadline) {
      ok = await win.webContents.executeJavaScript(
        `!!document.querySelector(${JSON.stringify(waitFor)})`
      );
      if (ok) break;
      await new Promise((r) => setTimeout(r, 250));
    }
    if (!ok) console.error(`WARNING: timed out waiting for ${waitFor}`);
    // One beat for the reveal animation to finish.
    await new Promise((r) => setTimeout(r, 400));
  }

  if (extraJs) {
    await win.webContents.executeJavaScript(extraJs);
    await new Promise((r) => setTimeout(r, 900));
  }

  // Let webfonts settle; a capture taken too early shows fallback metrics.
  await win.webContents.executeJavaScript('document.fonts.ready.then(() => true)');
  await new Promise((r) => setTimeout(r, 600));

  // What the DOM actually holds at capture time. capturePage() can return a
  // stale frame, so this is the ground truth to compare the image against.
  const state = await win.webContents.executeJavaScript(`({
    rows: document.querySelectorAll('.topic-row').length,
    skeletons: document.querySelectorAll('.animate-pulse').length,
    count: (document.getElementById('topic-count') || {}).textContent || '',
    provider: (document.getElementById('provider-chip') || {}).textContent || '',
    body: document.body.innerText.slice(0, 120).replace(/\\n/g, ' | '),
  })`);
  console.log('[state] ' + JSON.stringify(state));

  // A hidden window never composites, so capturePage() hands back the last
  // painted frame — which here was the loading skeleton, even though the DOM
  // already held every row. Showing (but not focusing) forces a paint.
  win.showInactive();
  await new Promise((r) => setTimeout(r, 500));

  const image = await win.webContents.capturePage();
  fs.writeFileSync(out, image.toPNG());
  console.log(`wrote ${out}`);
  app.quit();
});

app.on('window-all-closed', () => app.quit());