const BACKEND_URL = 'http://localhost:8000/api/plugin/comments';
const tabEnabledState = new Map();
let fallbackEnabled = false;

const isTabEnabled = (tabId) =>
  typeof tabId === 'number' ? (tabEnabledState.get(tabId) ?? false) : fallbackEnabled;

chrome.tabs?.onRemoved?.addListener((tabId) => tabEnabledState.delete(tabId));

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
  })
    .then(async (res) =>
      res.ok
        ? sendResponse({ success: true, data: await res.json() })
        : sendResponse({ success: false, status: res.status })
    )
    .catch((err) => sendResponse({ success: false, error: err.name }));

  return true;
});
