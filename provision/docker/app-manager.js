#!/usr/bin/env node
/**
 * App Manager - Per-app process manager for core-apps container
 *
 * Replaces `concurrently` with a process manager that supports:
 *   - Per-app dev/prod mode toggling at runtime
 *   - Individual app restart without affecting others
 *   - HTTP control API on port 9999 (container-internal only)
 *   - Graceful shutdown with SIGTERM/SIGINT propagation
 *
 * Control API:
 *   GET  /status            - All app statuses, modes, PIDs, health
 *   POST /mode              - Toggle app mode: {app, mode} or {allApps: mode}
 *   POST /restart           - Restart app: {app}
 *   POST /build             - Build app for prod: {app}
 *
 * Environment:
 *   ROOT_DIR              - Monorepo root (default: /srv/busibox-frontend)
 *   ENABLED_APPS          - Comma-separated app names to start (default: all)
 *                           e.g. "portal,admin" to only run portal and admin
 *   CORE_APPS_MODE        - Global default mode: "dev" or "prod"
 *   INITIAL_APP_MODES     - JSON override: {"portal":"prod","admin":"dev",...}
 */

'use strict';

const http = require('http');
const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const ROOT_DIR = process.env.ROOT_DIR || '/srv/busibox-frontend';
const CONTROL_PORT = 9999;
const MODES_FILE = '/tmp/app-modes.json';

const COLORS = {
  reset: '\x1b[0m',
  gray: '\x1b[90m',
  blue: '\x1b[34m',
  green: '\x1b[32m',
  cyan: '\x1b[36m',
  yellow: '\x1b[33m',
  magenta: '\x1b[35m',
  red: '\x1b[31m',
  white: '\x1b[37m',
};

const APP_DEFS = [
  { name: 'portal',    filter: '@busibox/portal',     port: 3000, basePath: '/portal',    color: 'blue',    extraEnv: {} },
  { name: 'agents',    filter: '@busibox/agents',     port: 3001, basePath: '/agents',    color: 'green',   extraEnv: {
    DEFAULT_API_AUDIENCE: 'agent-api',
  }},
  { name: 'admin',     filter: '@busibox/admin',      port: 3002, basePath: '/admin',     color: 'cyan',    extraEnv: {} },
  { name: 'chat',      filter: '@busibox/chat',       port: 3003, basePath: '/chat',      color: 'yellow',  extraEnv: {} },
  { name: 'appbuilder',filter: '@busibox/appbuilder',  port: 3004, basePath: '/builder',   color: 'magenta', extraEnv: {
    APP_NAME: 'busibox-appbuilder',
    NEXT_PUBLIC_APP_URL: 'https://localhost/builder',
    NEXT_PUBLIC_BUSIBOX_PORTAL_URL: 'https://localhost/portal',
  }},
  { name: 'media',     filter: '@busibox/media',      port: 3005, basePath: '/media',     color: 'red',     extraEnv: {} },
  { name: 'documents', filter: '@busibox/documents',   port: 3006, basePath: '/documents', color: 'white',   extraEnv: {} },
];

function getEnabledAppDefs() {
  const envVal = process.env.ENABLED_APPS;
  if (!envVal || envVal.trim() === '' || envVal.trim().toLowerCase() === 'all') {
    return APP_DEFS;
  }
  const enabled = new Set(envVal.split(',').map(s => s.trim().toLowerCase()));
  const filtered = APP_DEFS.filter(d => enabled.has(d.name));
  if (filtered.length === 0) {
    console.error(`[manager] WARNING: ENABLED_APPS="${envVal}" matched no apps, starting all`);
    return APP_DEFS;
  }
  return filtered;
}

const apps = new Map();
let appLibProc = null;
let shuttingDown = false;

function log(prefix, color, msg) {
  const c = COLORS[color] || '';
  const ts = new Date().toISOString().slice(11, 19);
  process.stdout.write(`${COLORS.gray}${ts}${COLORS.reset} ${c}[${prefix}]${COLORS.reset} ${msg}\n`);
}

function managerLog(msg) {
  log('manager', 'gray', msg);
}

function getInitialModes() {
  const globalDefault = process.env.CORE_APPS_MODE || 'dev';
  const modes = {};
  for (const def of APP_DEFS) {
    modes[def.name] = globalDefault;
  }

  if (process.env.INITIAL_APP_MODES) {
    try {
      const overrides = JSON.parse(process.env.INITIAL_APP_MODES);
      for (const [app, mode] of Object.entries(overrides)) {
        if (modes.hasOwnProperty(app) && (mode === 'dev' || mode === 'prod')) {
          modes[app] = mode;
        }
      }
    } catch (e) {
      managerLog(`WARNING: Failed to parse INITIAL_APP_MODES: ${e.message}`);
    }
  }

  if (fs.existsSync(MODES_FILE)) {
    try {
      const saved = JSON.parse(fs.readFileSync(MODES_FILE, 'utf8'));
      for (const [app, mode] of Object.entries(saved)) {
        if (modes.hasOwnProperty(app) && (mode === 'dev' || mode === 'prod')) {
          modes[app] = mode;
        }
      }
    } catch (e) {
      managerLog(`WARNING: Failed to read saved modes: ${e.message}`);
    }
  }

  return modes;
}

function saveModes() {
  const modes = {};
  for (const [name, state] of apps) {
    modes[name] = state.mode;
  }
  try {
    fs.writeFileSync(MODES_FILE, JSON.stringify(modes, null, 2));
  } catch (e) {
    managerLog(`WARNING: Failed to save modes: ${e.message}`);
  }
}

function pipeOutput(proc, appName, color) {
  const prefix = appName;
  const lineBuffer = { stdout: '', stderr: '' };

  function flush(stream, data) {
    lineBuffer[stream] += data;
    const lines = lineBuffer[stream].split('\n');
    lineBuffer[stream] = lines.pop();
    for (const line of lines) {
      if (line.trim()) {
        log(prefix, color, line);
      }
    }
  }

  if (proc.stdout) proc.stdout.on('data', (d) => flush('stdout', d.toString()));
  if (proc.stderr) proc.stderr.on('data', (d) => flush('stderr', d.toString()));
}

function startAppLib() {
  managerLog('Starting shared package watch (@jazzmind/busibox-app dev)...');
  const proc = spawn('pnpm', ['--filter', '@jazzmind/busibox-app', 'dev'], {
    cwd: ROOT_DIR,
    env: { ...process.env },
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: true,
  });
  pipeOutput(proc, 'app-lib', 'gray');
  proc.on('exit', (code) => {
    if (!shuttingDown) {
      managerLog(`app-lib exited with code ${code}, restarting in 2s...`);
      setTimeout(() => { appLibProc = startAppLib(); }, 2000);
    }
  });
  return proc;
}

function startApp(def, mode) {
  const env = {
    ...process.env,
    PORT: String(def.port),
    NEXT_PUBLIC_BASE_PATH: def.basePath,
    ...def.extraEnv,
  };

  let proc;
  if (mode === 'dev') {
    proc = spawn('pnpm', ['--filter', def.filter, 'dev'], {
      cwd: ROOT_DIR,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: true,
    });
  } else {
    const appDir = path.join(ROOT_DIR, 'apps', def.name);
    // In pnpm monorepos, Next.js standalone preserves the directory structure:
    //   .next/standalone/apps/<name>/server.js
    // Fallback to the flat path for non-monorepo setups.
    const monorepoServerPath = path.join(appDir, '.next', 'standalone', 'apps', def.name, 'server.js');
    const flatServerPath = path.join(appDir, '.next', 'standalone', 'server.js');
    const serverPath = fs.existsSync(monorepoServerPath) ? monorepoServerPath : flatServerPath;
    if (!fs.existsSync(serverPath)) {
      log(def.name, def.color, `ERROR: standalone server not found. Checked:`);
      log(def.name, def.color, `  ${monorepoServerPath}`);
      log(def.name, def.color, `  ${flatServerPath}`);
      return null;
    }
    log(def.name, def.color, `Starting standalone server: ${serverPath}`);
    proc = spawn('node', [serverPath], {
      cwd: appDir,
      env: {
        ...env,
        NODE_ENV: 'production',
        HOSTNAME: '0.0.0.0',
        NODE_OPTIONS: '--max-old-space-size=512',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: true,
    });
  }

  pipeOutput(proc, def.name, def.color);

  proc.on('exit', (code) => {
    const state = apps.get(def.name);
    if (state && !state.stopping && !shuttingDown) {
      log(def.name, def.color, `Exited with code ${code}, restarting in 2s...`);
      state.pid = null;
      state.restarts++;
      setTimeout(() => {
        if (!shuttingDown && !state.stopping) {
          const newProc = startApp(def, state.mode);
          if (newProc) {
            state.proc = newProc;
            state.pid = newProc.pid;
          }
        }
      }, 2000);
    }
  });

  return proc;
}

function killProcessGroup(pid, signal) {
  try {
    process.kill(-pid, signal);
  } catch (e) {
    try { process.kill(pid, signal); } catch (e2) {}
  }
}

function waitForPortFree(port, timeoutMs = 5000) {
  const start = Date.now();
  return new Promise((resolve) => {
    function check() {
      if (Date.now() - start > timeoutMs) {
        resolve();
        return;
      }
      const srv = require('net').createServer();
      srv.once('error', () => {
        setTimeout(check, 200);
      });
      srv.once('listening', () => {
        srv.close(() => resolve());
      });
      srv.listen(port, '0.0.0.0');
    }
    check();
  });
}

async function stopApp(name) {
  const state = apps.get(name);
  if (!state || !state.proc) return;

  state.stopping = true;
  const proc = state.proc;
  const pid = proc.pid;
  const port = state.def.port;

  await new Promise((resolve) => {
    const timeout = setTimeout(() => {
      managerLog(`${name}: SIGTERM timeout, sending SIGKILL to process group ${pid}`);
      killProcessGroup(pid, 'SIGKILL');
      setTimeout(resolve, 500);
    }, 5000);

    proc.on('exit', () => {
      clearTimeout(timeout);
      resolve();
    });

    killProcessGroup(pid, 'SIGTERM');
  });

  state.proc = null;
  state.pid = null;

  await waitForPortFree(port);

  state.stopping = false;
}

function emptyDir(dirPath) {
  let entries;
  try { entries = fs.readdirSync(dirPath); } catch { return; }
  for (const entry of entries) {
    try { fs.rmSync(path.join(dirPath, entry), { recursive: true, force: true }); } catch { /* ignore */ }
  }
}

function cleanNextCache(def, full = false) {
  const appDir = path.join(ROOT_DIR, 'apps', def.name);
  const nextDir = path.join(appDir, '.next');
  if (!fs.existsSync(nextDir)) return;

  if (full) {
    log(def.name, def.color, 'Cleaning .next directory...');
    emptyDir(nextDir);
  } else {
    const subdirs = ['dev', 'cache'];
    for (const sub of subdirs) {
      const target = path.join(nextDir, sub);
      if (fs.existsSync(target)) {
        log(def.name, def.color, `Cleaning .next/${sub}...`);
        emptyDir(target);
      }
    }
  }
}

async function buildApp(def) {
  const env = {
    ...process.env,
    NEXT_PUBLIC_BASE_PATH: def.basePath,
    ...def.extraEnv,
  };

  cleanNextCache(def, true);
  log(def.name, def.color, 'Building for production...');

  return new Promise((resolve) => {
    const proc = spawn('pnpm', ['--filter', def.filter, 'build'], {
      cwd: ROOT_DIR,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    pipeOutput(proc, `${def.name}:build`, def.color);
    proc.on('exit', (code) => {
      if (code === 0) {
        const appDir = path.join(ROOT_DIR, 'apps', def.name);
        const standaloneDir = path.join(appDir, '.next', 'standalone');
        // In pnpm monorepos, standalone output nests under apps/<name>/
        const monorepoStandaloneAppDir = path.join(standaloneDir, 'apps', def.name);
        const targetDir = fs.existsSync(monorepoStandaloneAppDir) ? monorepoStandaloneAppDir : standaloneDir;
        if (fs.existsSync(standaloneDir)) {
          try {
            const publicDir = path.join(appDir, 'public');
            if (fs.existsSync(publicDir)) {
              execSync(`cp -r ${publicDir} ${targetDir}/public`, { stdio: 'ignore' });
            }
            const staticDir = path.join(appDir, '.next', 'static');
            if (fs.existsSync(staticDir)) {
              execSync(`mkdir -p ${targetDir}/.next && cp -r ${staticDir} ${targetDir}/.next/static`, { stdio: 'ignore' });
            }
          } catch (e) {
            log(def.name, def.color, `WARNING: Failed to copy standalone assets: ${e.message}`);
          }
        }
        log(def.name, def.color, 'Build completed successfully');
        resolve(true);
      } else {
        log(def.name, def.color, `Build failed with code ${code}`);
        resolve(false);
      }
    });
  });
}

async function checkHealth(def) {
  return new Promise((resolve) => {
    const req = http.get(`http://localhost:${def.port}${def.basePath}/api/health`, { timeout: 3000 }, (res) => {
      resolve(res.statusCode >= 200 && res.statusCode < 400);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

function getStatus() {
  const result = { apps: {} };
  for (const [name, state] of apps) {
    result.apps[name] = {
      mode: state.mode,
      pid: state.pid,
      port: state.def.port,
      basePath: state.def.basePath,
      running: state.proc !== null && state.pid !== null,
      stopping: state.stopping,
      restarts: state.restarts,
    };
  }
  result.appLib = {
    running: appLibProc !== null && appLibProc.exitCode === null,
    pid: appLibProc ? appLibProc.pid : null,
  };
  return result;
}

async function getStatusWithHealth() {
  const status = getStatus();
  const healthChecks = [];
  for (const [name, info] of Object.entries(status.apps)) {
    if (info.running) {
      const def = APP_DEFS.find(d => d.name === name);
      healthChecks.push(
        checkHealth(def).then(healthy => { info.healthy = healthy; })
      );
    } else {
      info.healthy = false;
    }
  }
  await Promise.all(healthChecks);
  return status;
}

// --- Control API ---

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (chunk) => { data += chunk; });
    req.on('end', () => {
      try {
        resolve(data ? JSON.parse(data) : {});
      } catch (e) {
        reject(new Error('Invalid JSON'));
      }
    });
    req.on('error', reject);
  });
}

function sendJson(res, status, data) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(data));
}

async function handleRequest(req, res) {
  const url = new URL(req.url, `http://localhost:${CONTROL_PORT}`);
  const pathname = url.pathname;

  try {
    if (req.method === 'GET' && pathname === '/status') {
      const status = await getStatusWithHealth();
      sendJson(res, 200, { success: true, data: status });
      return;
    }

    if (req.method === 'POST' && pathname === '/mode') {
      const body = await readBody(req);

      if (body.allApps) {
        const mode = body.allApps;
        if (mode !== 'dev' && mode !== 'prod') {
          sendJson(res, 400, { success: false, error: 'mode must be "dev" or "prod"' });
          return;
        }

        managerLog(`Setting ALL apps to ${mode} mode...`);
        const results = {};

        for (const def of APP_DEFS) {
          const state = apps.get(def.name);
          if (state.mode === mode) {
            results[def.name] = { changed: false, mode };
            continue;
          }

          await stopApp(def.name);

          if (mode === 'prod') {
            const built = await buildApp(def);
            if (!built) {
              results[def.name] = { changed: false, error: 'build failed' };
              state.mode = 'dev';
              cleanNextCache(def);
              const proc = startApp(def, 'dev');
              if (proc) { state.proc = proc; state.pid = proc.pid; }
              continue;
            }
          } else {
            cleanNextCache(def);
          }

          state.mode = mode;
          const proc = startApp(def, mode);
          if (proc) {
            state.proc = proc;
            state.pid = proc.pid;
            results[def.name] = { changed: true, mode };
          } else {
            results[def.name] = { changed: false, error: 'failed to start' };
          }
        }

        saveModes();
        sendJson(res, 200, { success: true, data: results });
        return;
      }

      if (body.app) {
        const { app, mode } = body;
        const def = APP_DEFS.find(d => d.name === app);
        if (!def) {
          sendJson(res, 400, { success: false, error: `Unknown app: ${app}` });
          return;
        }
        if (mode !== 'dev' && mode !== 'prod') {
          sendJson(res, 400, { success: false, error: 'mode must be "dev" or "prod"' });
          return;
        }

        const state = apps.get(app);
        if (state.mode === mode) {
          sendJson(res, 200, { success: true, data: { changed: false, mode } });
          return;
        }

        managerLog(`Switching ${app} to ${mode} mode...`);
        await stopApp(app);

        if (mode === 'prod') {
          const built = await buildApp(def);
          if (!built) {
            state.mode = 'dev';
            cleanNextCache(def);
            const proc = startApp(def, 'dev');
            if (proc) { state.proc = proc; state.pid = proc.pid; }
            sendJson(res, 500, { success: false, error: 'Build failed, reverted to dev mode' });
            return;
          }
        } else {
          cleanNextCache(def);
        }

        state.mode = mode;
        const proc = startApp(def, mode);
        if (proc) {
          state.proc = proc;
          state.pid = proc.pid;
        }

        saveModes();
        sendJson(res, 200, { success: true, data: { changed: true, mode } });
        return;
      }

      sendJson(res, 400, { success: false, error: 'Provide {app, mode} or {allApps: mode}' });
      return;
    }

    if (req.method === 'POST' && pathname === '/restart') {
      const body = await readBody(req);
      const { app } = body;

      if (!app) {
        sendJson(res, 400, { success: false, error: 'Provide {app}' });
        return;
      }

      const def = APP_DEFS.find(d => d.name === app);
      if (!def) {
        sendJson(res, 400, { success: false, error: `Unknown app: ${app}` });
        return;
      }

      const state = apps.get(app);
      const clean = body.clean !== false;
      managerLog(`Restarting ${app} (${state.mode} mode${clean ? ', cleaning cache' : ''})...`);
      await stopApp(app);

      if (clean) {
        cleanNextCache(def, state.mode === 'prod');
      }

      const proc = startApp(def, state.mode);
      if (proc) {
        state.proc = proc;
        state.pid = proc.pid;
      }

      sendJson(res, 200, { success: true, data: { restarted: true, mode: state.mode } });
      return;
    }

    if (req.method === 'POST' && pathname === '/build') {
      const body = await readBody(req);
      const { app } = body;

      if (!app) {
        sendJson(res, 400, { success: false, error: 'Provide {app}' });
        return;
      }

      const def = APP_DEFS.find(d => d.name === app);
      if (!def) {
        sendJson(res, 400, { success: false, error: `Unknown app: ${app}` });
        return;
      }

      const built = await buildApp(def);
      sendJson(res, built ? 200 : 500, { success: built });
      return;
    }

    sendJson(res, 404, { success: false, error: 'Not found' });
  } catch (e) {
    managerLog(`Control API error: ${e.message}`);
    sendJson(res, 500, { success: false, error: e.message });
  }
}

// --- Main ---

async function main() {
  managerLog('Starting app-manager...');
  managerLog(`ROOT_DIR: ${ROOT_DIR}`);

  const enabledDefs = getEnabledAppDefs();
  const enabledNames = new Set(enabledDefs.map(d => d.name));
  managerLog(`Enabled apps: ${enabledDefs.map(d => d.name).join(', ')}`);

  const modes = getInitialModes();
  managerLog(`Initial modes: ${JSON.stringify(modes)}`);

  // Clean stale .next caches from volume mounts on startup (enabled apps only)
  for (const def of enabledDefs) {
    const mode = modes[def.name];
    const nextDir = path.join(ROOT_DIR, 'apps', def.name, '.next');
    if (fs.existsSync(nextDir)) {
      managerLog(`Cleaning stale .next for ${def.name} (${mode} mode)...`);
      if (mode === 'dev') {
        cleanNextCache(def, true);
      } else {
        cleanNextCache(def);
      }
    }
  }

  // Check if any enabled apps need prod builds
  const needsBuild = Object.entries(modes).filter(([name, mode]) => mode === 'prod' && enabledNames.has(name));
  if (needsBuild.length > 0) {
    managerLog(`Building ${needsBuild.length} app(s) for production mode...`);
    for (const [appName] of needsBuild) {
      const def = enabledDefs.find(d => d.name === appName);
      if (def) {
        const built = await buildApp(def);
        if (!built) {
          managerLog(`WARNING: Build failed for ${appName}, falling back to dev mode`);
          modes[appName] = 'dev';
        }
      }
    }
  }

  // Check if any enabled apps are in dev mode — only start app-lib watcher if needed
  const hasDevApps = enabledDefs.some(d => modes[d.name] === 'dev');
  if (hasDevApps) {
    appLibProc = startAppLib();
  } else {
    managerLog('All apps in prod mode, skipping app-lib watcher');
  }

  // Start enabled apps only
  for (const def of enabledDefs) {
    const mode = modes[def.name];
    const proc = startApp(def, mode);
    apps.set(def.name, {
      def,
      mode,
      proc,
      pid: proc ? proc.pid : null,
      stopping: false,
      restarts: 0,
    });
    log(def.name, def.color, `Started in ${mode} mode (PID: ${proc ? proc.pid : 'N/A'})`);
  }

  saveModes();

  // Start control API
  const server = http.createServer(handleRequest);
  server.listen(CONTROL_PORT, '0.0.0.0', () => {
    managerLog(`Control API listening on port ${CONTROL_PORT}`);
  });

  // Graceful shutdown
  const shutdown = async (signal) => {
    if (shuttingDown) return;
    shuttingDown = true;
    managerLog(`Received ${signal}, shutting down...`);

    server.close();

    if (appLibProc && appLibProc.pid) {
      killProcessGroup(appLibProc.pid, 'SIGTERM');
    }

    const stops = [];
    for (const [name] of apps) {
      stops.push(stopApp(name));
    }
    await Promise.all(stops);

    managerLog('All processes stopped. Exiting.');
    process.exit(0);
  };

  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
}

main().catch((e) => {
  console.error('Fatal error in app-manager:', e);
  process.exit(1);
});
