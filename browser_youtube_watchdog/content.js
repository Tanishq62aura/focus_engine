// content.js - Pure window-to-worker message bridge
window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin) return;

  if (event.data?.type === "FOCUS_ENGINE_SYNC") {
    chrome.runtime.sendMessage({
      type: "FOCUS_ENGINE_SYNC",
      token: event.data.token,
      sessionId: event.data.sessionId,
      apiBaseUrl: window.location.origin
    }).catch((err) => {
      console.debug("[FocusEngine Bridge] Sync dispatch non-fatal:", err);
    });
  } else if (event.data?.type === "FOCUS_ENGINE_CLEAR") {
    chrome.runtime.sendMessage({
      type: "FOCUS_ENGINE_CLEAR"
    }).catch((err) => {
      console.debug("[FocusEngine Bridge] Clear dispatch non-fatal:", err);
    });
  }
});
