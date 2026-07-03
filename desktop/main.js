const { app, BrowserWindow, dialog, ipcMain, Notification, Tray, Menu, nativeImage } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const Store = require('electron-store');

const store = new Store({
  encryptionKey: 'com-council-key-v1',
  schema: {
    pythonPath: { type: 'string', default: 'python3' },
    backendPort: { type: 'number', default: 8734 },
    llmProvider: { type: 'string', default: '' },
    llmApiKey: { type: 'string', default: '' },
    llmApiBase: { type: 'string', default: '' },
    llmModel: { type: 'string', default: '' },
    configured: { type: 'boolean', default: false }
  }
});

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

function writeBackendEnv() {
  const envPath = path.join(getBackendDir(), '.env');

  if (fs.existsSync(envPath)) {
    log('info', 'Backend .env already exists, preserving');
    return;
  }

  const port = store.get('backendPort');
  const envContent = [
    `BACKEND_PORT=${port}`,
    `LLM_PROVIDER=openai`,
    `LLM_MODEL=openai/glm-4.6`,
    `LLM_API_KEY=your-api-key`,
    `LLM_API_BASE=https://api.z.ai/api/coding/paas/v4`,
    `POSTGRES_URL=postgresql+asyncpg://council:council_dev@localhost:5432/council`,
    `NEO4J_URL=bolt://localhost:7687`,
    `NEO4J_USER=neo4j`,
    `NEO4J_PASSWORD=council_dev`,
    `REDIS_URL=redis://localhost:6379/0`,
    `CHROMA_HOST=localhost`,
    `CHROMA_PORT=8000`,
    `LOG_LEVEL=INFO`,
    `LOG_DIR=${LOG_DIR}`
  ].join('\n');

  fs.writeFileSync(envPath, envContent);
  log('info', 'Created default .env');
}

function getPythonPath() {
  const stored = store.get('pythonPath');
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

async function startPythonBackend() {
  if (pythonProcess) {
    pythonProcess.kill();
    pythonProcess = null;
  }

  const port = store.get('backendPort');

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
  const port = store.get('backendPort');
  for (let i = 0; i < maxRetries; i++) {
    try {
      const res = await fetch(`http://localhost:${port}/api/health`);
      if (res.ok) {
        const data = await res.json();
        restartAttempts = 0;
        log('info', `Backend healthy (configured=${data.configured})`);

        if (data.configured && !store.get('configured')) {
          store.set('configured', true);
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
    mainWindow.loadURL('http://localhost:5500/council_landing_page.html');
  } else {
    const loadTarget = path.join(__dirname, '..', 'com_website', 'council_landing_page.html');
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

ipcMain.handle('store:get', (_, key) => store.get(key));
ipcMain.handle('store:set', (_, key, value) => store.set(key, value));
ipcMain.handle('store:delete', (_, key) => store.delete(key));

ipcMain.handle('notification:show', (_, { title, body }) => {
  if (Notification.isSupported()) {
    new Notification({ title, body }).show();
  }
});

ipcMain.handle('backend:getPort', () => store.get('backendPort'));
ipcMain.handle('backend:isReady', async () => {
  try {
    const res = await fetch(`http://localhost:${store.get('backendPort')}/api/health`);
    return res.ok;
  } catch { return false; }
});

ipcMain.handle('backend:restart', async () => {
  restartAttempts = 0;
  await startPythonBackend();
});

ipcMain.handle('backend:saveConfig', async (_, config) => {
  if (config.llmProvider) store.set('llmProvider', config.llmProvider);
  if (config.llmApiKey) store.set('llmApiKey', config.llmApiKey);
  if (config.llmApiBase) store.set('llmApiBase', config.llmApiBase);
  if (config.llmModel) store.set('llmModel', config.llmModel);
  store.set('configured', true);
  writeBackendEnv();
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

  await startPythonBackend();
  createWindow();

  // System tray
  try {
    const trayIconPath = createTrayIcon();
    tray = new Tray(trayIconPath);
    tray.setToolTip('Council of Minds');
    const ctxMenu = Menu.buildFromTemplate([
      { label: 'Show Window', click: () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } } },
      { label: 'New Deliberation', click: () => {
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
    const configured = store.get('configured');
    let targetPage = 'council_landing_page.html';

    if (!healthy) {
      targetPage = 'council_landing_page.html';
      log('warn', 'Backend not healthy, loading landing page');
    } else if (!configured) {
      const setupPath = path.join(__dirname, '..', 'com_website', 'setup.html');
      if (fs.existsSync(setupPath)) {
        targetPage = 'setup.html';
      } else {
        log('warn', 'setup.html not found, loading landing page instead');
        targetPage = 'council_landing_page.html';
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
