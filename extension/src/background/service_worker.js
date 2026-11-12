
const BACKEND_URL = 'http://127.0.0.1:8000/api/plugin/comments';

const stateKey = (tabId) => `enabled_tab_${tabId}`;

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    return { success: false, status: response.status };
  }

  return { success: true, data: await response.json() };
}

chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.storage.session.remove(stateKey(tabId)).catch(() => {});
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  const tabId = sender.tab?.id;

  if (request.type === 'SET_ENABLED') {
    if (typeof tabId !== 'number') {
      sendResponse({ success: false, error: 'Missing tab ID' });
      return false;
    }

    const enabled = Boolean(request.enabled);

    chrome.storage.session.set({
      [stateKey(tabId)]: enabled
    }).then(async () => {
      try {
        await chrome.tabs.sendMessage(tabId, {
          type: 'AI_STATE_CHANGED',
          enabled
        });
      } catch (_) {
        // Some frames may not contain the content script.
      }

      sendResponse({ success: true, enabled });
    }).catch((error) => {
      sendResponse({ success: false, error: error.message });
    });

    return true;
  }

  if (request.type === 'GET_ENABLED') {
    if (typeof tabId !== 'number') {
      sendResponse({ enabled: false });
      return false;
    }

    chrome.storage.session.get(stateKey(tabId))
      .then((result) => {
        sendResponse({
          enabled: Boolean(result[stateKey(tabId)])
        });
      })
      .catch(() => {
        sendResponse({ enabled: false });
      });

    return true;
  }

  if (request.type === 'SEND_THREAD_MESSAGE') {
    const url = `${BACKEND_URL}/${encodeURIComponent(request.commentId || '')}/thread`;
    postJson(url, request.payload)
      .then(sendResponse)
      .catch((error) => sendResponse({ success: false, error: error.message }));
    return true;
  }

  if (request.type !== 'NEW_COMMENT') {
    return false;
  }

  if (typeof tabId !== 'number') {
    sendResponse({ success: false, error: 'Missing tab ID' });
    return false;
  }

  chrome.storage.session.get(stateKey(tabId))
    .then(async (result) => {
      if (!result[stateKey(tabId)]) {
        sendResponse({ success: false, disabled: true });
        return;
      }

      sendResponse(await postJson(BACKEND_URL, request.payload));
    })
    .catch((error) => {
      sendResponse({ success: false, error: error.message });
    });

  return true;
});
