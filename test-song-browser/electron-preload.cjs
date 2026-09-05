'use strict';
// Only used by the local Electron integration fixture, never packaged as a preload.
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('fixture', { invoke: (name, payload) => ipcRenderer.invoke(name, payload) });
