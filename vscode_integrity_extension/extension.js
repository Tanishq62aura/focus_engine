// extension.js – Focus Engine Integrity Watchdog
// Detects large paste bursts during focus sessions and reports them
// to the Focus Engine API at POST /sessions/active/paste-event.

'use strict';

const vscode = require('vscode');
const https = require('https');
const http = require('http');
const { URL } = require('url');

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let statusBarItem = /** @type {vscode.StatusBarItem} */ (null);
let isEnabled = true;
let lastFlaggedAt = 0;                // Epoch ms – debounce per-burst
const DEBOUNCE_MS = 3000;            // Don't spam the API faster than 3s
const IGNORED_SCHEMES = new Set(['output', 'extension-output', 'debug', 'git', 'search-editor']);
const IGNORED_LANGUAGES = new Set(['log', 'plaintext', 'markdown']); // adjust if needed

// ---------------------------------------------------------------------------
// Activate
// ---------------------------------------------------------------------------
/**
 * @param {vscode.ExtensionContext} context
 */
function activate(context) {
  // Read initial config
  refreshConfig();

  // Status bar widget
  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBarItem.command = 'focusEngine.toggleWatchdog';
  updateStatusBar('idle');
  statusBarItem.show();

  // ── Commands ──────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('focusEngine.toggleWatchdog', () => {
      isEnabled = !isEnabled;
      updateStatusBar(isEnabled ? 'idle' : 'disabled');
      vscode.window.showInformationMessage(
        `Focus Engine Watchdog ${isEnabled ? 'ENABLED ✅' : 'DISABLED ⛔'}`
      );
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('focusEngine.testConnection', async () => {
      const cfg = getConfig();
      try {
        await httpGet(`${cfg.apiUrl}/reports/today`);
        vscode.window.showInformationMessage('✅ Focus Engine API reachable!');
      } catch (e) {
        vscode.window.showErrorMessage(`❌ Focus Engine API unreachable: ${e.message}`);
      }
    })
  );

  // ── Document Change Listener ──────────────────────────────────────────────
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument(onDocumentChanged)
  );

  // ── Config change listener ────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration(e => {
      if (e.affectsConfiguration('focusEngine')) refreshConfig();
    })
  );

  console.log('[FocusEngine] Integrity Watchdog activated.');
}

// ---------------------------------------------------------------------------
// Core handler
// ---------------------------------------------------------------------------
/**
 * @param {vscode.TextDocumentChangeEvent} event
 */
async function onDocumentChanged(event) {
  if (!isEnabled) return;

  const doc = event.document;

  // Skip non-file schemes (output panels, git diffs, etc.)
  if (IGNORED_SCHEMES.has(doc.uri.scheme)) return;
  // Skip certain language modes
  if (IGNORED_LANGUAGES.has(doc.languageId)) return;
  // Skip untitled scratch buffers
  if (doc.isUntitled && doc.uri.scheme !== 'file') return;

  const cfg = getConfig();

  for (const change of event.contentChanges) {
    const inserted = change.text;
    const charCount = inserted.length;

    // Only flag if the insertion is large enough (paste threshold)
    if (charCount < cfg.pasteThresholdChars) continue;

    // Debounce: don't spam the API for rapid sequential changes
    const now = Date.now();
    if (now - lastFlaggedAt < DEBOUNCE_MS) continue;
    lastFlaggedAt = now;

    const fileName = doc.fileName.split(/[\\/]/).pop() || doc.fileName;
    const lineNumber = (change.range?.start?.line ?? 0) + 1;

    // Show status bar warning
    updateStatusBar('warning', charCount);

    // Fire & forget API call
    reportPasteEvent(cfg.apiUrl, fileName, charCount, lineNumber).catch(err => {
      console.warn('[FocusEngine] Could not report paste event:', err.message);
    });

    // Reset status bar after 5s
    setTimeout(() => updateStatusBar('idle'), 5000);

    // Only flag the first large paste per debounce window
    break;
  }
}

// ---------------------------------------------------------------------------
// API communication
// ---------------------------------------------------------------------------
/**
 * POST /sessions/active/paste-event
 */
async function reportPasteEvent(apiUrl, fileName, charCount, lineNumber) {
  const payload = JSON.stringify({ file_name: fileName, char_count: charCount, line_number: lineNumber });
  const endpoint = `${apiUrl}/sessions/active/paste-event`;
  console.log(`[FocusEngine] Reporting paste event → ${endpoint}`, { fileName, charCount, lineNumber });
  return httpPost(endpoint, payload);
}

/**
 * Simple promise-based HTTP/HTTPS POST.
 * @param {string} url
 * @param {string} body  JSON string
 * @returns {Promise<string>}
 */
function httpPost(url, body) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const lib = parsed.protocol === 'https:' ? https : http;
    const options = {
      hostname: parsed.hostname,
      port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
      path: parsed.pathname + parsed.search,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
        'User-Agent': 'FocusEngine-VSCode-Watchdog/1.0',
      },
      timeout: 4000,
    };
    const req = lib.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve(data));
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Request timed out')); });
    req.write(body);
    req.end();
  });
}

/**
 * Simple GET for connectivity check.
 * @param {string} url
 * @returns {Promise<string>}
 */
function httpGet(url) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const lib = parsed.protocol === 'https:' ? https : http;
    const options = {
      hostname: parsed.hostname,
      port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
      path: parsed.pathname + parsed.search,
      method: 'GET',
      timeout: 4000,
    };
    const req = lib.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve(data));
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Timeout')); });
    req.end();
  });
}

// ---------------------------------------------------------------------------
// Status bar helpers
// ---------------------------------------------------------------------------
/**
 * @param {'idle'|'warning'|'disabled'} state
 * @param {number} [chars]
 */
function updateStatusBar(state, chars) {
  if (!statusBarItem) return;
  switch (state) {
    case 'idle':
      statusBarItem.text = '$(eye) FocusGuard';
      statusBarItem.color = '#94a3b8';
      statusBarItem.tooltip = 'Focus Engine Integrity Watchdog is active. Click to toggle.';
      statusBarItem.backgroundColor = undefined;
      break;
    case 'warning':
      statusBarItem.text = `$(warning) PASTE BURST (${chars} chars) FLAGGED`;
      statusBarItem.color = '#f59e0b';
      statusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
      statusBarItem.tooltip = `Paste burst of ${chars} characters detected and reported to Focus Engine.`;
      break;
    case 'disabled':
      statusBarItem.text = '$(eye-closed) FocusGuard OFF';
      statusBarItem.color = '#475569';
      statusBarItem.backgroundColor = undefined;
      statusBarItem.tooltip = 'Focus Engine Integrity Watchdog is disabled. Click to enable.';
      break;
  }
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
function getConfig() {
  const cfg = vscode.workspace.getConfiguration('focusEngine');
  return {
    apiUrl: (cfg.get('apiUrl') || 'http://127.0.0.1:8000').replace(/\/$/, ''),
    pasteThresholdChars: cfg.get('pasteThresholdChars') || 40,
    enabled: cfg.get('enabled') !== false,
  };
}

function refreshConfig() {
  const cfg = getConfig();
  isEnabled = cfg.enabled;
}

// ---------------------------------------------------------------------------
// Deactivate
// ---------------------------------------------------------------------------
function deactivate() {
  statusBarItem?.dispose();
  console.log('[FocusEngine] Integrity Watchdog deactivated.');
}

module.exports = { activate, deactivate };
