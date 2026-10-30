"use strict";

/**
 * Minimal, allow-listed bridge between the sandboxed renderer and the shell.
 * No filesystem, process or network primitives are exposed. Flight, payload and
 * safety actions never pass through this bridge: they go to the local backend
 * where deterministic validation runs.
 */

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("rescueswarmShell", {
  runtimeInfo: () => ipcRenderer.invoke("gcs:runtime-info"),
  openDataFolder: () => ipcRenderer.invoke("gcs:open-data-folder"),
  onBackendExit: (callback) => {
    if (typeof callback !== "function") return () => {};
    const listener = (_event, code) => callback(code);
    ipcRenderer.on("gcs:backend-exit", listener);
    return () => ipcRenderer.removeListener("gcs:backend-exit", listener);
  },
});
