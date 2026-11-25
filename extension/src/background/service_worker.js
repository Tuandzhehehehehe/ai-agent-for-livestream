const BACKEND_URL = 'http://127.0.0.1:8000/api/plugin/comments';
const DASHBOARD_URL = 'http://127.0.0.1:8000/dashboard';

const stateKey = (tabId) => `enabled_tab_${tabId}`;
const autoReplyKey = (tabId) => `auto_reply_tab_${tabId}`;
const GLOBAL_ENABLED_KEY = 'enabled_global';
const GLOBAL_AUTO_REPLY_KEY = 'auto_reply_global';

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

async function resolveTargetTabIds(explicitTabId, senderTabId) {
  if (typeof explicitTabId === 'number') return [explicitTabId];
  if (typeof senderTabId === 'number') return [senderTabId];
  try {
    const activeTabs = await chrome.tabs.query({ active: true, currentWindow: true });
    const ytTabs = await chrome.tabs.query({ url: 'https://*.youtube.com/*' });
    const ids = new Set();
    activeTabs.forEach((t) => {
      if (typeof t.id === 'number') ids.add(t.id);
    });
    ytTabs.forEach((t) => {
      if (typeof t.id === 'number') ids.add(t.id);
    });
    return Array.from(ids);
  } catch (_) {
    return [];
  }
}

chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.storage.session.remove([stateKey(tabId), autoReplyKey(tabId)]).catch(() => {});
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  const senderTabId = sender.tab?.id;

  if (request.type === 'SET_ENABLED') {
    const enabled = Boolean(request.enabled);
    resolveTargetTabIds(request.tabId, senderTabId)
      .then(async (tabIds) => {
        const updates = { [GLOBAL_ENABLED_KEY]: enabled };
        tabIds.forEach((id) => {
          updates[stateKey(id)] = enabled;
        });
        await chrome.storage.session.set(updates);
        await Promise.all(
          tabIds.map((id) =>
            chrome.tabs
              .sendMessage(id, { type: 'AI_STATE_CHANGED', enabled })
              .catch(() => {})
          )
        );
        sendResponse({ success: true, enabled });
      })
      .catch((error) => {
        sendResponse({ success: false, error: error.message });
      });
    return true;
  }

  if (request.type === 'SET_AUTO_REPLY') {
    const autoReply = Boolean(request.autoReply);
    resolveTargetTabIds(request.tabId, senderTabId)
      .then(async (tabIds) => {
        const updates = { [GLOBAL_AUTO_REPLY_KEY]: autoReply };
        tabIds.forEach((id) => {
          updates[autoReplyKey(id)] = autoReply;
        });
        await chrome.storage.session.set(updates);
        await Promise.all(
          tabIds.map((id) =>
            chrome.tabs
              .sendMessage(id, { type: 'AI_AUTO_REPLY_CHANGED', autoReply })
              .catch(() => {})
          )
        );
        sendResponse({ success: true, autoReply });
      })
      .catch((error) => {
        sendResponse({ success: false, error: error.message });
      });
    return true;
  }

  if (request.type === 'GET_ENABLED') {
    resolveTargetTabIds(request.tabId, senderTabId)
      .then(async (tabIds) => {
        const primaryId = tabIds[0];
        const keys = [GLOBAL_ENABLED_KEY, GLOBAL_AUTO_REPLY_KEY];
        if (typeof primaryId === 'number') {
          keys.push(stateKey(primaryId), autoReplyKey(primaryId));
        }
        const result = await chrome.storage.session.get(keys);
        const hasTabEnabled =
          typeof primaryId === 'number' &&
          typeof result[stateKey(primaryId)] === 'boolean';
        const hasTabAutoReply =
          typeof primaryId === 'number' &&
          typeof result[autoReplyKey(primaryId)] === 'boolean';

        sendResponse({
          enabled: hasTabEnabled
            ? result[stateKey(primaryId)]
            : Boolean(result[GLOBAL_ENABLED_KEY]),
          autoReply: hasTabAutoReply
            ? result[autoReplyKey(primaryId)]
            : Boolean(result[GLOBAL_AUTO_REPLY_KEY])
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

  const keys = [GLOBAL_ENABLED_KEY];
  if (typeof senderTabId === 'number') {
    keys.push(stateKey(senderTabId));
  }

  chrome.storage.session
    .get(keys)
    .then(async (result) => {
      const tabEnabled =
        typeof senderTabId === 'number' &&
        typeof result[stateKey(senderTabId)] === 'boolean'
          ? result[stateKey(senderTabId)]
          : Boolean(result[GLOBAL_ENABLED_KEY]);
      if (!tabEnabled) {
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
