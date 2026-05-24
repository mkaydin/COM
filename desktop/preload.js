const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  openFileDialog: () => ipcRenderer.invoke('dialog:openFile'),
  saveFileDialog: (defaultName) => ipcRenderer.invoke('dialog:saveFile', defaultName),
  storeGet: (key) => ipcRenderer.invoke('store:get', key),
  storeSet: (key, value) => ipcRenderer.invoke('store:set', key, value),
  storeDelete: (key) => ipcRenderer.invoke('store:delete', key),
  showNotification: (opts) => ipcRenderer.invoke('notification:show', opts),
  getBackendPort: () => ipcRenderer.invoke('backend:getPort'),
  isBackendReady: () => ipcRenderer.invoke('backend:isReady'),
  restartBackend: () => ipcRenderer.invoke('backend:restart'),
  saveBackendConfig: (config) => ipcRenderer.invoke('backend:saveConfig', config),
  getPlatform: () => ipcRenderer.invoke('app:getPlatform')
});
