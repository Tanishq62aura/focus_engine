/**
 * content.js - Focus Engine Dashboard Sync Bridge (Chromium Manifest V3)
 * Compatible with Google Chrome and Microsoft Edge.
 *
 * Listens for window postMessage broadcasts from the Focus Engine dashboard
 * and updates extension local storage to drive telemetry heartbeats.
 */
'use strict';

window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin) return;

  if (event.data?.type === "FOCUS_ENGINE_SYNC") {
    chrome.storage.local.set({
      authToken: event.data.token,
      activeSessionId: event.data.sessionId,
      apiBaseUrl: window.location.origin
    }, () => {
      chrome.alarms.create("telemetry_heartbeat", { periodInMinutes: 0.5 });
    });
  } else if (event.data?.type === "FOCUS_ENGINE_CLEAR") {
    chrome.storage.local.remove(["activeSessionId"], () => {
      chrome.alarms.clear("telemetry_heartbeat");
    });
  }
});
