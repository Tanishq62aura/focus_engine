/**
 * background.js - Focus Engine Chromium MV3 Service Worker
 * Compatible with Google Chrome and Microsoft Edge.
 *
 * Responsibilities:
 * 1. Dual-Browser detection (Chrome vs. Edge)
 * 2. Persistent 30-second telemetry pulse via chrome.alarms (ephemeral SW safe)
 * 3. Query active tab domain and dispatch POST /api/telemetry/heartbeat
 * 4. State machine badge & desktop notification handling:
 *    - 401 Unauthorized -> Clear auth, badge "AUTH" (#ef4444), clear alarm
 *    - 404 Not Found    -> Clear session, badge "", clear alarm
 *    - 200 OK           ->
 *        - "OK": badge "ON" (or ""), color #22c55e
 *        - "WARNING": badge "!${violation_count}", color #f59e0b, desktop notification
 *        - "SESSION_TERMINATED" or "ABORTED": badge "ABRT", color #ef4444, desktop notification, clear session & alarm
 */

'use strict';

// ---------------------------------------------------------------------------
// 1. Dual-Browser Source Detection
// ---------------------------------------------------------------------------
const BROWSER_SOURCE = navigator.userAgent.includes("Edg/") ? "edge" : "chrome";
const ALARM_NAME = "telemetry_heartbeat";
const HEARTBEAT_PERIOD_MINUTES = 0.5; // 30 seconds

// ---------------------------------------------------------------------------
// 2. Alarm Lifecycle Registration (Startup & Install)
// ---------------------------------------------------------------------------
function registerHeartbeatAlarm() {
  chrome.alarms.get(ALARM_NAME, (existing) => {
    if (!existing) {
      chrome.alarms.create(ALARM_NAME, { periodInMinutes: HEARTBEAT_PERIOD_MINUTES });
    }
  });
}

chrome.runtime.onInstalled.addListener(() => {
  console.info(`[FocusEngine] Extension installed on ${BROWSER_SOURCE}.`);
  chrome.storage.local.get(['activeSessionId', 'authToken'], (data) => {
    if (data?.activeSessionId && data?.authToken) {
      registerHeartbeatAlarm();
    }
  });
});

chrome.runtime.onStartup.addListener(() => {
  console.info(`[FocusEngine] Browser started on ${BROWSER_SOURCE}.`);
  chrome.storage.local.get(['activeSessionId', 'authToken'], (data) => {
    if (data?.activeSessionId && data?.authToken) {
      registerHeartbeatAlarm();
    }
  });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) {
    sendHeartbeatPulse();
  }
});

// Also trigger on active tab switch for immediate telemetry
chrome.tabs.onActivated.addListener(() => {
  sendHeartbeatPulse();
});

// ---------------------------------------------------------------------------
// 3. Runtime Message Listener (Relayed from content.js)
// ---------------------------------------------------------------------------
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "FOCUS_ENGINE_SYNC") {
    chrome.storage.local.set({
      authToken: message.token,
      activeSessionId: message.sessionId,
      apiBaseUrl: message.apiBaseUrl
    }, () => {
      // In background.js, chrome.alarms is fully accessible
      chrome.alarms.create("telemetry_heartbeat", { periodInMinutes: 0.5 });
      chrome.action.setBadgeText({ text: "ON" });
      chrome.action.setBadgeBackgroundColor({ color: "#22c55e" });
      sendResponse({ status: "ACK_SYNC" });
    });
    return true;
  }

  if (message.type === "FOCUS_ENGINE_CLEAR") {
    chrome.storage.local.remove(["activeSessionId"], () => {
      chrome.alarms.clear("telemetry_heartbeat");
      chrome.action.setBadgeText({ text: "" });
      sendResponse({ status: "ACK_CLEAR" });
    });
    return true;
  }
});

// ---------------------------------------------------------------------------
// 4. UI Helpers: Badges and Notifications
// ---------------------------------------------------------------------------
function setBadge(text, color) {
  try {
    if (chrome.action?.setBadgeText) {
      chrome.action.setBadgeText({ text: text || "" });
      if (color && chrome.action.setBadgeBackgroundColor) {
        chrome.action.setBadgeBackgroundColor({ color });
      }
    }
  } catch (err) {
    console.debug("[FocusEngine] Badge error:", err);
  }
}

function notifyUser(id, title, message) {
  try {
    if (chrome.notifications?.create) {
      chrome.notifications.create(id || `fe_notice_${Date.now()}`, {
        type: "basic",
        iconUrl: "icons/icon48.png",
        title: title || "Focus Engine",
        message: message,
        priority: 2,
      });
    }
  } catch (err) {
    console.debug("[FocusEngine] Notification error:", err);
  }
}

// ---------------------------------------------------------------------------
// 4. Heartbeat Execution Flow & Response State Machine
// ---------------------------------------------------------------------------
async function sendHeartbeatPulse() {
  let stored;
  try {
    stored = await chrome.storage.local.get(['authToken', 'activeSessionId', 'apiBaseUrl']);
  } catch (err) {
    console.debug("[FocusEngine] Storage read error:", err);
    return;
  }

  const { authToken, activeSessionId, apiBaseUrl } = stored || {};

  // Skip dispatch if authToken or activeSessionId is not present
  if (!authToken || !activeSessionId) {
    return;
  }

  // Query active tab in the current window
  let activeTab = null;
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tabs && tabs.length > 0) {
      activeTab = tabs[0];
    }
  } catch (err) {
    console.debug("[FocusEngine] Tab query error:", err);
  }

  // Parse active URL (ignore non-HTTP URLs like chrome://, edge://, about:blank)
  let targetUrl = null;
  if (activeTab?.url) {
    const rawUrl = activeTab.url.trim();
    if (rawUrl.startsWith("http://") || rawUrl.startsWith("https://")) {
      targetUrl = rawUrl;
    }
  }

  const payload = {
    session_id: activeSessionId,
    source: BROWSER_SOURCE,
    event_type: "tab_active",
    active_target: targetUrl,
    is_idle: false,
  };

  const baseUrl = (apiBaseUrl || "http://127.0.0.1:8000").replace(/\/+$/, '');
  const endpoint = `${baseUrl}/api/telemetry/heartbeat`;

  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${authToken}`,
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(5000),
    });

    // --- State Machine Response Evaluation ---
    if (response.status === 401) {
      // HTTP 401 Unauthorized: Token invalid or expired
      console.warn("[FocusEngine] 401 Unauthorized: Invalid or expired token.");
      await chrome.storage.local.remove(["authToken"]);
      setBadge("AUTH", "#ef4444");
      chrome.alarms.clear(ALARM_NAME);
      return;
    }

    if (response.status === 404) {
      // HTTP 404 Not Found: Session ended on backend
      console.info("[FocusEngine] 404 Not Found: Active session has concluded.");
      await chrome.storage.local.remove(["activeSessionId"]);
      setBadge("", "");
      chrome.alarms.clear(ALARM_NAME);
      return;
    }

    if (response.ok) {
      const data = await response.json();
      const status = data?.status;
      const count = data?.violation_count ?? 0;

      if (status === "OK") {
        setBadge("ON", "#22c55e");
      } else if (status === "WARNING") {
        setBadge(`!${count}`, "#f59e0b");
        notifyUser(
          "focus_warning",
          "Focus Engine",
          `Focus Warning: Off-task target detected (${count}/3)`
        );
      } else if (status === "ABORTED" || status === "SESSION_TERMINATED") {
        setBadge("ABRT", "#ef4444");
        notifyUser(
          "focus_aborted",
          "Focus Engine",
          "Session Terminated: Auto-aborted due to repeated distractions"
        );
        await chrome.storage.local.remove(["activeSessionId"]);
        chrome.alarms.clear(ALARM_NAME);
      }
    } else {
      console.warn(`[FocusEngine] Backend returned ${response.status}`);
    }
  } catch (err) {
    if (err?.name !== 'AbortError') {
      console.debug("[FocusEngine] Telemetry pulse offline/unreachable:", err?.message);
    }
  }
}
