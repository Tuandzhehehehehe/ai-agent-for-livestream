const BACKEND_URL = 'http://localhost:8000/api/plugin/comments';

chrome.runtime.onMessage.addListener((request) => {
  if (request.type === 'NEW_COMMENT') {
    fetch(BACKEND_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request.payload)
    }).catch(() => {
      // Backend chua khoi dong hoac offline
    });
  }
});
