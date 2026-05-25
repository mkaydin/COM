const { app, BrowserWindow, dialog, ipcMain, Notification } = require('electron');
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
let restartAttempts = 0;
const MAX_RESTART_DELAY = 30000;

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
  const port = store.get('backendPort');
  const envContent = [
    `BACKEND_PORT=${port}`,
    `LLM_PROVIDER=${store.get('llmProvider') || 'openai'}`,
    `LLM_MODEL=${store.get('llmModel') || 'gpt-4o'}`,
    `LLM_API_KEY=${store.get('llmApiKey') || ''}`,
    `LLM_API_BASE=${store.get('llmApiBase') || ''}`,
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

  fs.writeFileSync(path.join(getBackendDir(), '.env'), envContent);
}

function startPythonBackend() {
  if (pythonProcess) {
    pythonProcess.kill();
    pythonProcess = null;
  }

  writeBackendEnv();

  const pythonPath = store.get('pythonPath');
  const backendDir = getBackendDir();
  const port = store.get('backendPort');

  log('info', `Starting Python backend (${pythonPath} uvicorn on port ${port})`);

  pythonProcess = spawn(pythonPath, ['-m', 'uvicorn', 'main:app', '--host', '0.0.0.0', '--port', String(port)], {
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
      setTimeout(startPythonBackend, delay);
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
        restartAttempts = 0;
        log('info', 'Backend healthy');
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

ipcMain.handle('backend:restart', () => {
  restartAttempts = 0;
  startPythonBackend();
});

ipcMain.handle('backend:saveConfig', (_, config) => {
  if (config.llmProvider) store.set('llmProvider', config.llmProvider);
  if (config.llmApiKey) store.set('llmApiKey', config.llmApiKey);
  if (config.llmApiBase) store.set('llmApiBase', config.llmApiBase);
  if (config.llmModel) store.set('llmModel', config.llmModel);
  store.set('configured', true);
  writeBackendEnv();
  startPythonBackend();
  return true;
});

ipcMain.handle('app:getPlatform', () => process.platform);

app.whenReady().then(async () => {
  log('info', 'App starting');

  startPythonBackend();
  createWindow();

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
  app.quit();
});

app.on('before-quit', () => {
  if (pythonProcess) {
    pythonProcess.kill();
    pythonProcess = null;
  }
});
