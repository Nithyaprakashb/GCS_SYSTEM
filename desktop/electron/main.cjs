"use strict";

/**
 * RescueSwarm GCS — Electron main process.
 *
 * Security posture:
 *  - Renderer is sandboxed, context-isolated and has no Node integration.
 *  - Only one loopback origin may be reached; external network requests are blocked.
 *  - The backend is a child process. Killing the GCS (or this window) cannot alter
 *    ArduPilot failsafe parameters on the aircraft.
 *  - No remote content, no auto-update, no cloud service.
 */

const { app, BrowserWindow, Menu, ipcMain, session, shell } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const PORT = Number(process.env.RESCUESWARM_PORT || 8765);
const BACKEND_URL = `http://127.0.0.1:${PORT}`;
const DEV_URL = process.env.RESCUESWARM_DEV_URL || null;
const DEV_ORIGIN = DEV_URL ? new URL(DEV_URL).origin : null;
const PACKAGED = app.isPackaged;

let mainWindow = null;
let backend = null;
let backendLog = null;
let quitting = false;

const logDir = () => path.join(app.getPath("userData"), "logs");
const dataDir = () => path.join(app.getPath("userData"), "data");

function logLine(message) {
  const line = `[${new Date().toISOString()}] ${message}\n`;
  try {
    fs.mkdirSync(logDir(), { recursive: true });
    fs.appendFileSync(path.join(logDir(), "gcs-shell.log"), line);
  } catch {
    /* Logging must never prevent station startup. */
  }
}

function backendRoot() {
  return PACKAGED
    ? path.join(process.resourcesPath, "backend")
    : path.resolve(__dirname, "..", "..", "backend");
}

function frontendDir() {
  return PACKAGED
    ? path.join(process.resourcesPath, "desktop", "dist")
    : path.resolve(backendRoot(), "..", "desktop", "dist");
}

function pythonExecutable() {
  if (process.env.RESCUESWARM_PYTHON) return process.env.RESCUESWARM_PYTHON;
  return process.platform === "win32" ? "python" : "python3";
}

async function healthy() {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 1200);
    const response = await fetch(`${BACKEND_URL}/api/health`, { signal: controller.signal });
    clearTimeout(timer);
    if (!response.ok) return false;
    const body = await response.json();
    return body && body.offline === true;
  } catch {
    return false;
  }
}

function startBackend() {
  const root = backendRoot();
  const entry = path.join(root, "rescueswarm", "main.py");
  if (!fs.existsSync(entry)) {
    logLine(`Backend package missing at ${entry}`);
    return null;
  }
  fs.mkdirSync(dataDir(), { recursive: true });
  fs.mkdirSync(logDir(), { recursive: true });
  backendLog = fs.createWriteStream(path.join(logDir(), "backend.log"), { flags: "a" });
  const env = {
    ...process.env,
    PYTHONUNBUFFERED: "1",
    PYTHONPATH: root,
    RESCUESWARM_DATA: dataDir(),
    RESCUESWARM_PORT: String(PORT),
    RESCUESWARM_FRONTEND: frontendDir(),
    RESCUESWARM_LOG_LEVEL: "info",
  };
  const child = spawn(pythonExecutable(), ["-m", "rescueswarm.main"], {
    cwd: root,
    env,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout.on("data", (chunk) => backendLog && backendLog.write(chunk));
  child.stderr.on("data", (chunk) => {
    if (backendLog) backendLog.write(chunk);
    logLine(`backend: ${chunk.toString().trim().slice(0, 400)}`);
  });
  child.on("error", (error) => {
    logLine(`Failed to start Python backend: ${error.message}`);
  });
  child.on("exit", (code) => {
    logLine(`Backend exited with code ${code}`);
    backend = null;
    if (!quitting && mainWindow) {
      mainWindow.webContents.send("gcs:backend-exit", code);
    }
  });
  logLine(`Started backend (${pythonExecutable()}) from ${root}`);
  backend = child;
  return child;
}

async function ensureBackend() {
  if (await healthy()) {
    logLine("Reusing an already-running local backend");
    return true;
  }
  startBackend();
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (await healthy()) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

function loadingPage(message) {
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>RescueSwarm GCS</title>
  <style>
    html,body{height:100%;margin:0;background:#070b12;color:#dce5f2;font-family:Segoe UI,system-ui,sans-serif}
    .wrap{height:100%;display:grid;place-items:center}
    .card{width:min(560px,86vw);border:1px solid #243146;background:#0c1420;border-radius:12px;padding:26px}
    h1{font-size:20px;margin:0 0 6px;letter-spacing:.01em}
    .sub{color:#8796ab;font-size:12px;letter-spacing:.14em;text-transform:uppercase}
    p{color:#a8b6c9;font-size:13px;line-height:1.6}
    code{font-family:Consolas,monospace;color:#f0a03c;font-size:12px}
    .bar{height:3px;background:#1a2536;border-radius:2px;overflow:hidden;margin:18px 0}
    .bar::after{content:"";display:block;height:100%;width:38%;background:#5ad1c8;animation:run 1.2s ease-in-out infinite}
    @keyframes run{0%{transform:translateX(-100%)}100%{transform:translateX(300%)}}
  </style></head><body><div class="wrap"><div class="card">
  <div class="sub">Team ADRODS-X · local authority</div>
  <h1>RescueSwarm Ground Control</h1>
  <div class="bar"></div>
  <p>${message}</p>
  </div></div></body></html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1600,
    height: 980,
    minWidth: 1180,
    minHeight: 760,
    show: false,
    backgroundColor: "#070b12",
    title: "RescueSwarm GCS",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      spellcheck: false,
      devTools: !PACKAGED,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow && mainWindow.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  mainWindow.webContents.on("will-navigate", (event, url) => {
    const allowed = [BACKEND_URL, DEV_ORIGIN].filter(Boolean).some((prefix) => url.startsWith(prefix));
    if (!allowed) {
      event.preventDefault();
      logLine(`Blocked navigation to ${url}`);
    }
  });
  mainWindow.webContents.on("will-attach-webview", (event) => event.preventDefault());
  mainWindow.webContents.on("render-process-gone", (_event, details) => {
    logLine(`Renderer gone: ${details.reason}`);
  });

  return mainWindow;
}

function lockDownSession() {
  const allowedHosts = new Set(["127.0.0.1", "localhost", "::1"]);
  const allowedPorts = new Set([String(PORT)]);
  if (DEV_URL) allowedPorts.add(new URL(DEV_URL).port || "80");

  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);

  session.defaultSession.webRequest.onBeforeRequest({ urls: ["*://*/*"] }, (details, callback) => {
    try {
      const target = new URL(details.url);
      const host = target.hostname.replace(/^\[|\]$/g, "");
      const port = target.port || (target.protocol === "https:" ? "443" : "80");
      if (allowedHosts.has(host) && allowedPorts.has(port)) {
        callback({ cancel: false });
        return;
      }
      logLine(`Blocked external request: ${details.url}`);
      callback({ cancel: true });
    } catch {
      callback({ cancel: true });
    }
  });
}

async function boot() {
  lockDownSession();
  const window = createWindow();
  window.loadURL(loadingPage("Starting the local ground service and evidence database…"));
  const ok = await ensureBackend();
  if (!ok) {
    const message = PACKAGED
      ? "The local Python backend did not start. Install Python 3.11+ with the requirements, or check <code>%APPDATA%/RescueSwarm GCS/logs/backend.log</code>. The GCS never falls back to a cloud service."
      : "The local Python backend did not start. Run <code>python -m rescueswarm.main</code> from <code>backend/</code> and restart. Check the shell log for details.";
    await window.loadURL(loadingPage(message));
    logLine("Backend failed to become healthy within 30 s");
    return;
  }
  const target = DEV_URL || BACKEND_URL;
  logLine(`Loading station UI from ${target}`);
  await window.loadURL(target);
}

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    Menu.setApplicationMenu(null);

    ipcMain.handle("gcs:runtime-info", () => ({
      backendUrl: BACKEND_URL,
      packaged: PACKAGED,
      appVersion: app.getVersion(),
      dataDir: dataDir(),
      logDir: logDir(),
      versions: {
        electron: process.versions.electron,
        chrome: process.versions.chrome,
        node: process.versions.node,
      },
    }));

    ipcMain.handle("gcs:open-data-folder", async () => {
      const result = await shell.openPath(dataDir());
      return { opened: result === "", error: result || null };
    });

    void boot().catch((error) => logLine(`Boot failure: ${error && error.message}`));

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        void boot().catch((error) => logLine(`Boot failure: ${error && error.message}`));
      }
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });

  app.on("before-quit", () => {
    quitting = true;
    if (backend && !backend.killed) {
      logLine("Stopping local backend");
      try {
        backend.kill();
      } catch (error) {
        logLine(`Backend stop error: ${error && error.message}`);
      }
      backend = null;
    }
  });
}
