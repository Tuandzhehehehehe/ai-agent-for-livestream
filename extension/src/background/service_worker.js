const BACKEND_URL = 'http://localhost:8000/api/plugin/comments';

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.type !== 'NEW_COMMENT') return;

  fetch(BACKEND_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request.payload)
    }).then(async (response) => {
      if (!response.ok) {
        console.warn(`[YouTube Live] Backend rejected comment (${response.status})`);
        sendResponse({ success: false, status: response.status });
        return;
      }

      const result = await response.json();
      if (result.moderation_status === 'blocked') {
        console.warn(`[YouTube Live] Comment filtered (${result.moderation_reason})`);
      }
      sendResponse({ success: true, data: result });
    }).catch((error) => {
      console.warn(`[YouTube Live] Backend request failed (${error.name})`);
      sendResponse({ success: false, error: error.name });
    });
  return true;
});
