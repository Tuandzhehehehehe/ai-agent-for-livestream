const BACKEND_URL = 'http://127.0.0.1:8000/api/plugin/comments';
const DASHBOARD_URL = 'http://127.0.0.1:8000/dashboard';

const stateKey = (tabId) => `enabled_tab_${tabId}`;
const autoReplyKey = (tabId) => `auto_reply_tab_${tabId}`;

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
  chrome.storage.session.remove([stateKey(tabId), autoReplyKey(tabId)]).catch(() => {});
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
      } catch (_) {}

      sendResponse({ success: true, enabled });
    }).catch((error) => {
      sendResponse({ success: false, error: error.message });
    });

    return true;
  }

  if (request.type === 'SET_AUTO_REPLY') {
    if (typeof tabId !== 'number') {
      sendResponse({ success: false, error: 'Missing tab ID' });
      return false;
    }

    const autoReply = Boolean(request.autoReply);

    chrome.storage.session.set({
      [autoReplyKey(tabId)]: autoReply
    }).then(async () => {
      try {
        await chrome.tabs.sendMessage(tabId, {
          type: 'AI_AUTO_REPLY_CHANGED',
          autoReply
        });
      } catch (_) {}

      sendResponse({ success: true, autoReply });
    }).catch((error) => {
      sendResponse({ success: false, error: error.message });
    });

    return true;
  }

  if (request.type === 'GET_ENABLED') {
    if (typeof tabId !== 'number') {
      sendResponse({ enabled: false, autoReply: false });
      return false;
    }

    chrome.storage.session.get([stateKey(tabId), autoReplyKey(tabId)])
      .then((result) => {
        sendResponse({
          enabled: Boolean(result[stateKey(tabId)]),
          autoReply: Boolean(result[autoReplyKey(tabId)])
        });
      })
      .catch(() => {
        sendResponse({ enabled: false, autoReply: false });
      });

    return true;
  }

  if (request.type === 'OPEN_DASHBOARD') {
    chrome.tabs
      .create({ url: DASHBOARD_URL })
      .then(() => sendResponse({ success: true }))
      .catch((error) => sendResponse({ success: false, error: error.message }));
    return true;
  }

  if (request.type === 'SEND_THREAD_MESSAGE') {
    const commentId = String(request.commentId || '').trim();
    if (!commentId) {
      sendResponse({ success: false, error: 'Missing comment ID' });
      return false;
    }
    const url = `${BACKEND_URL}/${encodeURIComponent(commentId)}/thread`;
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
