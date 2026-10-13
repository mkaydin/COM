const { app, BrowserWindow, dialog, ipcMain, Notification, Tray, Menu, nativeImage, safeStorage } = require('electron');
const { spawn } = require('child_process');
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
const DOCKER_DIR = path.join(__dirname, '..', 'docker');
const INFRA_SERVICES = ['postgres', 'chromadb'];

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

  // Managed keys are (re)written from the app's settings; every other line is
  // preserved verbatim, so a hand-edited POSTGRES_URL or a comment survives.
  // Values in `overrides` win; `force` is not needed because these keys are
  // owned by the app.
  const provider = overrides.llmProvider || getStore().get('llmProvider') || DEFAULT_PROVIDER;

  const managed = {
    BACKEND_PORT: String(getStore().get('backendPort')),
    LLM_PROVIDER: provider,
    OPENCODE_API_KEY: overrides.opencodeApiKey || getStore().get('opencodeApiKey') || '',
    // With Ollama every role runs on one pulled tag, so it is both the
    // provider default and the legacy single-model setting.
    LLM_MODEL_DEFAULT: provider === 'ollama'
      ? (overrides.llmModel || getStore().get('llmModel') || DEFAULT_OLLAMA_MODEL)
      : (overrides.llmModelDefault || getStore().get('llmModelDefault') || DEFAULT_MODEL),
    OLLAMA_HOST: overrides.ollamaHost || getStore().get('ollamaHost') || 'localhost',
    OLLAMA_PORT: String(overrides.ollamaPort || getStore().get('ollamaPort') || 11434),
    LOG_LEVEL: 'INFO',
    LOG_DIR: LOG_DIR
  };

  const existing = fs.existsSync(envPath) ? fs.readFileSync(envPath, 'utf8').split(/\r?\n/) : [];
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

function getPythonPath() {
  const stored = getStore().get('pythonPath');
  if (stored && stored !== 'python3' && fs.existsSync(stored)) {
    return stored;
  }

  const candidates = [
    path.join(__dirname, '..', '.venv', 'bin', 'python'),
    path.join(__dirname, '..', '.venv', 'bin', 'python3'),
    '/usr/bin/python3',
    'python3'
  ];

  for (const candidate of candidates) {
    try {
      if (fs.existsSync(candidate) || candidate === 'python3') {
        log('info', `Using Python: ${candidate}`);
        return candidate;
      }
    } catch {}
  }

  return 'python3';
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
  const proc = spawn('docker', ['compose', '-f', path.join(DOCKER_DIR, 'docker-compose.yml'),
                                'up', '-d', ...INFRA_SERVICES],
                     { cwd: DOCKER_DIR, stdio: ['ignore', 'pipe', 'pipe'] });
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

  log('info', `Starting Python backend (${pythonPath} uvicorn on port ${port})`);

  pythonProcess = spawn(pythonPath, ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: backendDir,
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    stdio: ['ignore', 'pipe', 'pipe']
  });

  pythonProcess.stdout.on('data', (data) => log('info', `[backend] ${data.toString().trim()}`));
  pythonProcess.stderr.on('data', (data) => log('error', `[backend] ${data.toString().trim()}`));

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

function createWindow() {
  const isDev = process.env.NODE_ENV === 'dev';

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
    mainWindow.loadURL('http://localhost:5500/forum.html');
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
      mainWindow.loadURL(`http://localhost:5500/${targetPage}`);
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
});
