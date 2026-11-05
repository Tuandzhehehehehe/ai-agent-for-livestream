const BACKEND_URL = 'http://localhost:8000/api/plugin/comments';
const tabEnabledState = new Map();
let fallbackEnabled = false;

function isTabEnabled(tabId) {
  if (typeof tabId === 'number') {
    return tabEnabledState.get(tabId) ?? false;
  }
  return fallbackEnabled;
}

chrome.tabs?.onRemoved?.addListener((tabId) => {
  tabEnabledState.delete(tabId);
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  const tabId = sender.tab?.id;

  if (request.type === 'SET_ENABLED') {
    const enabled = Boolean(request.enabled);
    if (typeof tabId === 'number') {
      tabEnabledState.set(tabId, enabled);
      chrome.tabs.sendMessage(tabId, { type: 'AI_STATE_CHANGED', enabled }).catch(() => {});
    } else {
      fallbackEnabled = enabled;
    }
    sendResponse({ success: true, enabled });
    return false;
  }

  if (request.type === 'GET_ENABLED') {
    sendResponse({ enabled: isTabEnabled(tabId) });
    return false;
  }

  if (request.type !== 'NEW_COMMENT') return false;

  if (!isTabEnabled(tabId)) {
    sendResponse({ success: false, disabled: true });
    return false;
  }

  fetch(BACKEND_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request.payload)
    }).then(async (response) => {
      if (!response.ok) {
        sendResponse({ success: false, status: response.status });
        return;
      }

      const result = await response.json();
      sendResponse({ success: true, data: result });
    }).catch((error) => {
      sendResponse({ success: false, error: error.name });
    });
  return true;
});
