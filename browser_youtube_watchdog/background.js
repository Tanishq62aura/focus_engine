/**
 * background.js  –  Focus Engine YouTube Watchdog (Manifest V3 service worker)
 *
 * Responsibilities:
 *   1. Detect when the user navigates to a youtube.com/watch URL.
 *   2. Strip the " - YouTube" suffix from the tab title.
 *   3. Deduplicate: skip if the same URL was reported within the last 30 s.
 *   4. POST {video_title, channel_name, url} to the Focus Engine API.
 *   5. Suppress all network errors silently (backend may be offline).
 */

'use strict';

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const API_BASE       = 'http://127.0.0.1:8000';
const ENDPOINT       = `${API_BASE}/sessions/active/youtube-audit`;
const DEDUP_TTL_MS   = 30_000;   // ignore same URL within 30 s
const YT_WATCH_REGEX = /youtube\.com\/watch/;

// ---------------------------------------------------------------------------
// Dedup cache  { url -> timestamp }
// ---------------------------------------------------------------------------
const _reported = new Map();

function _isDuplicate(url) {
  const last = _reported.get(url);
  if (!last) return false;
  return (Date.now() - last) < DEDUP_TTL_MS;
}

function _markReported(url) {
  _reported.set(url, Date.now());
  // Prune stale entries to avoid unbounded growth
  for (const [u, ts] of _reported) {
    if (Date.now() - ts > DEDUP_TTL_MS * 2) _reported.delete(u);
  }
}

// ---------------------------------------------------------------------------
// Title cleanup
// ---------------------------------------------------------------------------
function _cleanTitle(rawTitle) {
  if (!rawTitle) return 'Unknown Video';
  // Strip trailing " - YouTube" (case-insensitive, handles extra spaces)
  return rawTitle.replace(/\s*-\s*youtube\s*$/i, '').trim() || rawTitle.trim();
}

// ---------------------------------------------------------------------------
// Auth token helper
// ---------------------------------------------------------------------------
async function _getAuthToken() {
  try {
    if (typeof chrome !== 'undefined' && chrome.storage) {
      if (chrome.storage.sync) {
        const data = await chrome.storage.sync.get(['focusAuthToken', 'authToken']);
        if (data?.focusAuthToken) return data.focusAuthToken;
        if (data?.authToken) return data.authToken;
      }
      if (chrome.storage.local) {
        const data = await chrome.storage.local.get(['focusAuthToken', 'authToken']);
        if (data?.focusAuthToken) return data.focusAuthToken;
        if (data?.authToken) return data.authToken;
      }
    }
  } catch (_) {}
  return null;
}

// ---------------------------------------------------------------------------
// Core: fetch tab info and report
// ---------------------------------------------------------------------------
async function _reportTab(tabId) {
  let tab;
  try {
    tab = await chrome.tabs.get(tabId);
  } catch (_) {
    return; // Tab may have closed before we could query it
  }

  const url = tab.url || '';
  if (!YT_WATCH_REGEX.test(url)) return;
  if (_isDuplicate(url)) return;

  const videoTitle = _cleanTitle(tab.title);

  // Optimistically mark before the fetch to prevent parallel duplicates
  _markReported(url);

  const payload = {
    video_title: videoTitle,
    channel_name: null,   // content-script needed for channel; null is valid
    url: url,
  };

  const headers = { 'Content-Type': 'application/json' };
  const token = await _getAuthToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  try {
    const res = await fetch(ENDPOINT, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(payload),
      // Keep-alive not supported in SW fetch; signal abort after 5 s
      signal: AbortSignal.timeout(5000),
    });

    if (res.ok) {
      const data = await res.json();
      _handleResponse(data, videoTitle, url);
    } else {
      console.warn(`[FocusYT] API returned ${res.status} for "${videoTitle}"`);
    }
  } catch (err) {
    // Backend is unreachable (server offline, CORS pre-check, timeout, etc.)
    // Silently suppress – never throw from a service worker listener.
    if (err?.name !== 'AbortError') {
      console.debug('[FocusYT] Backend unreachable:', err?.message);
    }
  }
}

// ---------------------------------------------------------------------------
// Response handler – show badge / log
// ---------------------------------------------------------------------------
function _handleResponse(data, title, url) {
  if (!data) return;

  if (data.status === 'no_session') {
    // No active session – nothing to flag
    console.debug('[FocusYT] No active focus session.');
    _setBadge('', '');
    return;
  }

  if (data.is_distraction) {
    console.warn(`[FocusYT] 🚨 DISTRACTION flagged: "${title}"`);
    _setBadge('!', '#ef4444');   // red badge
  } else {
    console.info(`[FocusYT] ✅ Approved: "${title}"`);
    _setBadge('✓', '#22c55e');   // green badge, clears after 5 s
    setTimeout(() => _setBadge('', ''), 5000);
  }
}

function _setBadge(text, color) {
  chrome.action.setBadgeText({ text });
  if (color) chrome.action.setBadgeBackgroundColor({ color });
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------

/**
 * Fires when a tab's URL or title changes.
 * We only care about 'complete' status so the title is fully resolved.
 */
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  // Only act when the page load is complete and it's a YouTube watch URL
  if (changeInfo.status !== 'complete') return;
  if (!tab.active) return;                    // Only audit the focused tab
  if (!YT_WATCH_REGEX.test(tab.url || '')) return;

  await _reportTab(tabId);
});

/**
 * Fires when the user switches to a different tab.
 * Covers the case where the tab is already loaded when the user switches to it.
 */
chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  await _reportTab(tabId);
});
