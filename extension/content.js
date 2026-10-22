// content.js - Pure relay bridge
window.addEventListener("message", (event) => {
  // Only accept messages from the active host
  if (event.origin !== window.location.origin) return;

  if (event.data?.type === "FOCUS_ENGINE_SYNC") {
    // Send message to background service worker
    chrome.runtime.sendMessage({
      type: "FOCUS_ENGINE_SYNC",
      token: event.data.token,
      sessionId: event.data.sessionId,
      apiBaseUrl: window.location.origin
    }).catch((err) => {
      console.debug("[FocusEngine Content] Message relay skipped:", err);
    });
  } else if (event.data?.type === "FOCUS_ENGINE_CLEAR") {
    chrome.runtime.sendMessage({
      type: "FOCUS_ENGINE_CLEAR"
    }).catch((err) => {
      console.debug("[FocusEngine Content] Clear relay skipped:", err);
    });
  }
});
