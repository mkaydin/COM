const { app, BrowserWindow, dialog, ipcMain, Notification, Tray, Menu, nativeImage, safeStorage } = require('electron');
const { spawn, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const Store = require('electron-store');

// Default provider reaches Zen's free models through the `opencode` CLI,
// because Zen's API refuses free models to standalone clients. That is also
// why the backend runs here on the host rather than in a container.
const DEFAULT_PROVIDER = 'opencode-cli';
const DEFAULT_MODEL = 'big-pickle';
const DEFAULT_OLLAMA_MODEL = 'gemma4:e4b';

// Stateful services live in Docker; the backend does not.
const REPO_ROOT = path.join(__dirname, '..');
const INFRA_SERVICES = ['postgres', 'chromadb'];

// Dev mode serves the site over HTTP instead of file://, so a reload picks up
// edits without restarting Electron. One constant: the port appeared three
// times, and changing the server without changing the URL it is loaded from
// produces a window that shows nothing but a connection error.
const DEV_SERVER_PORT = 5500;
const DEV_ORIGIN = `http://localhost:${DEV_SERVER_PORT}`;

const storeSchema = {
  pythonPath: { type: 'string', default: 'python3' },
  backendPort: { type: 'number', default: 8734 },
  // Provider selection. 'opencode' is the default (free models over HTTPS);
  // 'ollama' is the optional local provider.
  llmProvider: { type: 'string', default: DEFAULT_PROVIDER },
  llmModelDefault: { type: 'string', default: DEFAULT_MODEL },
  // The local tag, used only when llmProvider is 'ollama'.
  llmModel: { type: 'string', default: DEFAULT_OLLAMA_MODEL },
  opencodeApiKey: { type: 'string', default: '' },
  ollamaHost: { type: 'string', default: 'localhost' },
  ollamaPort: { type: 'number', default: 11434 },
  configured: { type: 'boolean', default: false }
};

let store;

function getStore() {
  if (store) return store;
  // Back the store with the OS keychain rather than a hardcoded literal key.
  const opts = { schema: storeSchema };
  try {
    if (safeStorage.isEncryptionAvailable()) {
      opts.encryptionKey = safeStorage.getSelectedStorageBackend() === 'basic_text'
        ? 'com-council'
        : safeStorage.encryptString('com-council').toString('base64');
    }
  } catch (e) {
    // No keychain available — fall back to an unencrypted store. It holds no
    // credentials, only paths, ports and the model tag.
  }
  store = new Store(opts);
  return store;
}

let mainWindow = null;
let pythonProcess = null;
let tray = null;
let restartAttempts = 0;
const MAX_RESTART_DELAY = 30000;

function createTrayIcon() {
  // Application icon = the gold "council" building (Material account_balance),
  // rendered into desktop/icons/. Shared by the window, taskbar, and tray.
  return path.join(__dirname, 'icons', '256x256.png');
}

const LOG_DIR = path.join(app.getPath('userData'), 'logs');
if (!fs.existsSync(LOG_DIR)) fs.mkdirSync(LOG_DIR, { recursive: true });

const logStream = fs.createWriteStream(path.join(LOG_DIR, 'electron.log'), { flags: 'a' });
function log(level, msg) {
  const line = `[${new Date().toISOString()}] [${level}] ${msg}\n`;
  logStream.write(line);
  console.log(line.trim());
}

function getBackendDir() {
  if (app.isPackaged) return path.join(process.resourcesPath, 'backend');
  return path.join(__dirname, '..', 'backend');
}

function writeBackendEnv(overrides = {}) {
  const envPath = path.join(getBackendDir(), '.env');

  const existing = fs.existsSync(envPath) ? fs.readFileSync(envPath, 'utf8').split(/\r?\n/) : [];

  // What the file already says, so a value the user set by hand survives.
  // Without this, `OPENCODE_API_KEY: ... || ''` wrote a blank key over a real
  // one on every launch whenever the Electron store did not hold it — and the
  // app showed no sign of it, because the default provider needs no key. The
  // loss only surfaced when someone switched to the Zen HTTP provider.
  const fromFile = {};
  for (const line of existing) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (m) fromFile[m[1]] = m[2].trim();
  }

  // Managed keys are (re)written from the app's settings; every other line is
  // preserved verbatim, so a hand-edited POSTGRES_URL or a comment survives.
  // Precedence: explicit override -> app store -> existing file -> default.
  const provider = overrides.llmProvider || getStore().get('llmProvider') || DEFAULT_PROVIDER;
  const pick = (...values) => values.find(v => v !== undefined && v !== null && v !== '') ?? '';

  const managed = {
    BACKEND_PORT: String(getStore().get('backendPort')),
    LLM_PROVIDER: provider,
    OPENCODE_API_KEY: pick(
      overrides.opencodeApiKey,
      getStore().get('opencodeApiKey'),
      fromFile.OPENCODE_API_KEY
    ),
    // With Ollama every role runs on one pulled tag, so it is both the
    // provider default and the legacy single-model setting.
    LLM_MODEL_DEFAULT: provider === 'ollama'
      ? pick(overrides.llmModel, getStore().get('llmModel'), fromFile.LLM_MODEL_DEFAULT, DEFAULT_OLLAMA_MODEL)
      : pick(overrides.llmModelDefault, getStore().get('llmModelDefault'), fromFile.LLM_MODEL_DEFAULT, DEFAULT_MODEL),
    OLLAMA_HOST: pick(overrides.ollamaHost, getStore().get('ollamaHost'), fromFile.OLLAMA_HOST, 'localhost'),
    OLLAMA_PORT: String(pick(overrides.ollamaPort, getStore().get('ollamaPort'), fromFile.OLLAMA_PORT, '11434')),
    LOG_LEVEL: 'INFO',
    LOG_DIR: LOG_DIR
  };

  // Clearing a key is only ever intentional: an explicit empty override.
  // A store that simply has no key must not mean "erase the user's".
  const explicitlyCleared = new Set(
    Object.keys(overrides).filter(k => overrides[k] === '')
  );
  for (const k of explicitlyCleared) {
    if (k === 'opencodeApiKey') managed.OPENCODE_API_KEY = '';
  }

  const out = [];
  const seen = new Set();

  for (const line of existing) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=/);
    if (!m) { if (line !== '' || out.length) out.push(line); continue; }
    const key = m[1];
    if (Object.prototype.hasOwnProperty.call(managed, key)) {
      if (!seen.has(key)) { out.push(`${key}=${managed[key]}`); seen.add(key); }
    } else {
      out.push(line);
    }
  }
  for (const [k, v] of Object.entries(managed)) {
    if (!seen.has(k)) out.push(`${k}=${v}`);
  }

  fs.writeFileSync(envPath, out.join('\n').replace(/\n+$/, '\n'));
  log('info', `Backend .env written (provider=${managed.LLM_PROVIDER}, default=${managed.LLM_MODEL_DEFAULT})`);
}

/**
 * Does this interpreter actually have the backend's dependencies?
 *
 * Checking that the file exists is not enough. `/usr/bin/python3` always
 * exists and almost never has uvicorn, so selecting it produces a backend that
 * dies instantly with a ModuleNotFoundError and a crash-restart loop, while
 * the window opens normally and looks fine until you click something.
 */
function pythonHasBackendDeps(pythonPath) {
  try {
    const probe = spawnSync(pythonPath, ['-c', 'import uvicorn, fastapi'], {
      timeout: 15000,
      stdio: 'ignore'
    });
    return probe.status === 0;
  } catch {
    return false;
  }
}

function getPythonPath() {
  const stored = getStore().get('pythonPath');
  if (stored && stored !== 'python3' && fs.existsSync(stored)) {
    return stored;
  }

  const candidates = [
    path.join(__dirname, '..', '.venv', 'bin', 'python'),
    path.join(__dirname, '..', '.venv', 'bin', 'python3'),
    'python3'
  ];

  for (const candidate of candidates) {
    try {
      if (!fs.existsSync(candidate) && candidate !== 'python3') continue;
      if (!pythonHasBackendDeps(candidate)) {
        log('warn', `${candidate} cannot import uvicorn/fastapi — skipping`);
        continue;
      }
      log('info', `Using Python: ${candidate}`);
      return candidate;
    } catch {}
  }

  return null;
}

function dockerAvailable() {
  try {
    return fs.existsSync('/var/run/docker.sock') || fs.existsSync('/run/docker.sock');
  } catch { return false; }
}

/**
 * Bring up postgres/chroma in the background, then wait for postgres.
 *
 * Non-blocking: a slow or absent Docker must never stop the window opening.
 * The backend degrades to reduced retrieval if the stores are not there, which
 * is better than a GUI that will not start.
 */
function startInfra() {
  if (!dockerAvailable()) {
    log('warn', 'Docker not detected — knowledge retrieval will be degraded');
    return;
  }
  log('info', 'Starting infrastructure (Docker): ' + INFRA_SERVICES.join(', '));
  // Run from the repo root so compose finds the same root compose.yaml that a
  // hand-typed `docker compose up -d` finds — one convention, not two.
  const proc = spawn('docker', ['compose', 'up', '-d', ...INFRA_SERVICES],
                     { cwd: REPO_ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
  proc.stdout.on('data', (d) => log('info', `[docker] ${d.toString().trim()}`));
  proc.stderr.on('data', (d) => log('warn', `[docker] ${d.toString().trim()}`));
  proc.on('error', (e) => log('warn', `Could not run docker: ${e.message}`));
  proc.on('close', (code) => {
    if (code !== 0) log('warn', `docker compose exited ${code}`);
    else log('info', 'Infrastructure ready');
  });
}

async function startPythonBackend() {
  if (pythonProcess) {
    pythonProcess.kill();
    pythonProcess = null;
  }

  const port = getStore().get('backendPort');

  // Check if a backend is already running on the port
  try {
    const res = await fetch(`http://localhost:${port}/api/health`);
    if (res.ok) {
      log('info', `Backend already running on port ${port}, skipping spawn`);
      return;
    }
  } catch {}

  writeBackendEnv();

  const pythonPath = getPythonPath();
  const backendDir = getBackendDir();

  if (!pythonPath) {
    // Say so, rather than spawning something that will crash-loop. An
    // unexplained blank window is the hardest version of this to debug.
    const message =
      'No Python with the backend dependencies was found.\n\n' +
      'Looked for .venv/bin/python, .venv/bin/python3 and python3 on PATH; ' +
      'none can import uvicorn.\n\n' +
      'Create it:\n' +
      `    python3 -m venv ${path.join(REPO_ROOT, '.venv')}\n` +
      `    ${path.join(REPO_ROOT, '.venv', 'bin', 'pip')} install -r ` +
      `${path.join(REPO_ROOT, 'backend', 'requirements.txt')}\n`;
    log('error', 'No usable Python found for the backend.');
    if (mainWindow && !mainWindow.isDestroyed()) {
      dialog.showErrorBox('Backend unavailable', message);
    }
    return;
  }

  log('info', `Starting Python backend (${pythonPath} uvicorn on port ${port})`);

  pythonProcess = spawn(pythonPath, ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: backendDir,
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    stdio: ['ignore', 'pipe', 'pipe']
  });

  pythonProcess.stdout.on('data', (data) => log('info', `[backend] ${data.toString().trim()}`));

  // Uvicorn writes its ordinary startup and request lines to stderr, so
  // logging stderr as errors turned "Application startup complete" into an
  // [error] line — and buried the tracebacks that actually matter.
  pythonProcess.stderr.on('data', (data) => {
    const text = data.toString().trim();
    const isFailure = /Traceback|^[A-Za-z_.]*(Error|Exception)\b|MODULE_NOT_FOUND|CRITICAL/i.test(text);
    log(isFailure ? 'error' : 'info', `[backend] ${text}`);
  });

  pythonProcess.on('close', (code) => {
    log('warn', `Python backend exited with code ${code}`);
    pythonProcess = null;

    if (mainWindow && !mainWindow.isDestroyed()) {
      const delay = Math.min(1000 * Math.pow(2, restartAttempts), MAX_RESTART_DELAY);
      restartAttempts++;
      log('info', `Auto-restarting backend in ${delay}ms (attempt ${restartAttempts})`);
      setTimeout(() => startPythonBackend(), delay);
    }
  });

  pythonProcess.on('error', (err) => {
    log('error', `Failed to start Python: ${err.message}`);
    pythonProcess = null;
  });
}

async function waitForBackend(maxRetries = 30, interval = 1000) {
  const port = getStore().get('backendPort');
  for (let i = 0; i < maxRetries; i++) {
    try {
      const res = await fetch(`http://localhost:${port}/api/health`);
      if (res.ok) {
        const data = await res.json();
        restartAttempts = 0;
        log('info', `Backend healthy (configured=${data.configured})`);

        if (data.configured && !getStore().get('configured')) {
          getStore().set('configured', true);
          log('info', 'Backend already configured, skipping setup wizard');
        }

        return true;
      }
    } catch {}
    await new Promise(r => setTimeout(r, interval));
  }
  return false;
}

/**
 * Serve com_website/ over HTTP for `npm run dev`.
 *
 * Dev mode loads the app over HTTP rather than file://, so a reload picks up
 * edits without restarting Electron. Nothing used to serve that port, so
 * `npm start` — which set NODE_ENV=dev — opened a window that could only ever
 * show "This site can't be reached".
 */
let staticServer = null;

function startStaticServer(port = DEV_SERVER_PORT) {
  if (staticServer) return;

  const webRoot = path.join(REPO_ROOT, 'com_website');
  const http = require('http');
  const url = require('url');

  const types = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png': 'image/png',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon'
  };

  staticServer = http.createServer((req, res) => {
    // Resolve inside webRoot and verify the result is still inside it, so a
    // crafted path cannot read the rest of the repo.
    const rel = decodeURIComponent(url.parse(req.url).pathname).replace(/^\/+/, '');
    const target = path.resolve(webRoot, rel || 'forum.html');
    if (target !== webRoot && !target.startsWith(webRoot + path.sep)) {
      res.writeHead(403).end('forbidden');
      return;
    }

    fs.readFile(target, (err, data) => {
      if (err) {
        res.writeHead(404, { 'Content-Type': 'text/plain' }).end('not found');
        return;
      }
      res.writeHead(200, { 'Content-Type': types[path.extname(target)] || 'application/octet-stream' });
      res.end(data);
    });
  });

  staticServer.on('error', (err) => {
    log('error', `Static server failed: ${err.message}`);
    staticServer = null;
  });

  staticServer.listen(port, '127.0.0.1', () => {
    log('info', `Dev static server on ${DEV_ORIGIN}`);
  });
}

function stopStaticServer() {
  if (staticServer) {
    staticServer.close();
    staticServer = null;
  }
}

function createWindow() {
  const isDev = process.env.NODE_ENV === 'dev';

  if (isDev) startStaticServer();

  mainWindow = new BrowserWindow({
    width: 1440, height: 900, minWidth: 1024, minHeight: 700,
    title: 'COM — Council of Minds',
    backgroundColor: '#1a1a2e',
    icon: path.join(__dirname, 'icons', 'icon.png'),
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  });

  if (isDev) {
    mainWindow.loadURL(`${DEV_ORIGIN}/forum.html`);
  } else {
    const loadTarget = path.join(__dirname, '..', 'com_website', 'forum.html');
    mainWindow.loadFile(loadTarget);
  }

  if (!app.isPackaged) {
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.on('closed', () => { mainWindow = null; });

  mainWindow.webContents.on('did-fail-load', (_, code, desc) => {
    log('error', `Page load failed: ${code} ${desc}`);
  });
}

ipcMain.handle('dialog:openFile', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile', 'multiSelections'],
    filters: [{ name: 'Documents', extensions: ['pdf', 'txt', 'md', 'csv', 'json'] }]
  });
  return result.canceled ? [] : result.filePaths;
});

ipcMain.handle('dialog:saveFile', async (_, defaultName) => {
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: defaultName,
    filters: [{ name: 'PDF', extensions: ['pdf'] }]
  });
  return result.canceled ? null : result.filePath;
});

ipcMain.handle('file:save', async (_, filePath, dataBase64) => {
  try {
    const buf = Buffer.from(dataBase64, 'base64');
    fs.writeFileSync(filePath, buf);
    return true;
  } catch (err) {
    log('error', `File save failed: ${err.message}`);
    return false;
  }
});

ipcMain.handle('store:get', (_, key) => getStore().get(key));
ipcMain.handle('store:set', (_, key, value) => getStore().set(key, value));
ipcMain.handle('store:delete', (_, key) => getStore().delete(key));

ipcMain.handle('notification:show', (_, { title, body }) => {
  if (Notification.isSupported()) {
    new Notification({ title, body }).show();
  }
});

ipcMain.handle('backend:getPort', () => getStore().get('backendPort'));
ipcMain.handle('backend:isReady', async () => {
  try {
    const res = await fetch(`http://localhost:${getStore().get('backendPort')}/api/health`);
    return res.ok;
  } catch { return false; }
});

ipcMain.handle('backend:restart', async () => {
  restartAttempts = 0;
  await startPythonBackend();
});

ipcMain.handle('backend:saveConfig', async (_, config) => {
  if (config.llmProvider) getStore().set('llmProvider', config.llmProvider);
  if (config.llmModel) getStore().set('llmModel', config.llmModel);
  if (config.llmModelDefault) getStore().set('llmModelDefault', config.llmModelDefault);
  if (config.opencodeApiKey) getStore().set('opencodeApiKey', config.opencodeApiKey);
  if (config.ollamaHost) getStore().set('ollamaHost', config.ollamaHost);
  if (config.ollamaPort) getStore().set('ollamaPort', Number(config.ollamaPort));
  getStore().set('configured', true);
  writeBackendEnv({
    llmProvider: config.llmProvider,
    llmModel: config.llmModel,
    llmModelDefault: config.llmModelDefault,
    opencodeApiKey: config.opencodeApiKey,
  });
  await startPythonBackend();
  return true;
});

ipcMain.handle('app:getPlatform', () => process.platform);

ipcMain.on('navigate', (_, page) => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    const target = path.join(__dirname, '..', 'com_website', page);
    if (fs.existsSync(target)) mainWindow.loadFile(target);
  }
});

app.whenReady().then(async () => {
  log('info', 'App starting');

  // Docker first and in parallel: the backend takes a few seconds to import,
  // which is enough time for the stores to come up.
  startInfra();
  await startPythonBackend();
  createWindow();

  // System tray
  try {
    const trayIconPath = createTrayIcon();
    tray = new Tray(trayIconPath);
    tray.setToolTip('Council of Minds');
    const ctxMenu = Menu.buildFromTemplate([
      { label: 'Show Window', click: () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } } },
      { label: 'New Topic', click: () => {
        if (mainWindow) {
          mainWindow.show();
          const target = path.join(__dirname, '..', 'com_website', 'forum.html');
          if (fs.existsSync(target)) mainWindow.loadFile(target);
        }
      }},
      { label: 'Chamber (legacy)', click: () => {
        if (mainWindow) {
          mainWindow.show();
          const target = path.join(__dirname, '..', 'com_website', 'council_chamber.html');
          if (fs.existsSync(target)) mainWindow.loadFile(target);
        }
      }},
      { label: 'Open Archive', click: () => {
        if (mainWindow) {
          mainWindow.show();
          const target = path.join(__dirname, '..', 'com_website', 'history.html');
          if (fs.existsSync(target)) mainWindow.loadFile(target);
        }
      }},
      { type: 'separator' },
      { label: 'Quit', click: () => { app.isQuitting = true; app.quit(); } },
    ]);
    tray.setContextMenu(ctxMenu);
    tray.on('double-click', () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } });
  } catch (e) {
    log('warn', 'Tray creation failed: ' + e.message);
  }

  // Minimize to tray
  mainWindow.on('minimize', (e) => {
    e.preventDefault();
    mainWindow.hide();
  });

  mainWindow.on('close', (e) => {
    if (!app.isQuitting) {
      e.preventDefault();
      mainWindow.hide();
    }
  });

  const healthy = await waitForBackend();

  if (mainWindow && !mainWindow.isDestroyed()) {
    const configured = getStore().get('configured');
    let targetPage = 'forum.html';

    if (!healthy) {
      targetPage = 'forum.html';
      log('warn', 'Backend not healthy, loading the forum');
    } else if (!configured) {
      const setupPath = path.join(__dirname, '..', 'com_website', 'setup.html');
      if (fs.existsSync(setupPath)) {
        targetPage = 'setup.html';
      } else {
        log('warn', 'setup.html not found, loading the forum instead');
        targetPage = 'forum.html';
      }
    }

    if (process.env.NODE_ENV === 'dev') {
      mainWindow.loadURL(`${DEV_ORIGIN}/${targetPage}`);
    } else {
      mainWindow.loadFile(path.join(__dirname, '..', 'com_website', targetPage));
    }
    mainWindow.webContents.once('did-finish-load', () => {
      log('info', `Loaded: ${targetPage}`);
    });
  }
});

app.on('window-all-closed', () => {
  if (pythonProcess) {
    pythonProcess.kill();
    pythonProcess = null;
  }
  logStream.end();
  if (tray) tray.destroy();
  app.quit();
});

app.on('before-quit', () => {
  app.isQuitting = true;
  if (tray) tray.destroy();
  if (pythonProcess) {
    pythonProcess.kill();
    pythonProcess = null;
  }
  // Release the dev port, otherwise the next `npm run dev` fails to bind and
  // the window silently falls back to a dead URL.
  stopStaticServer();
});
