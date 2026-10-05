chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({ backendUrl: 'http://localhost:8000' });
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === 'AI_QUERY') {
    chrome.storage.local.get(['backendUrl'], async (result) => {
      const backendUrl = result.backendUrl || 'http://localhost:8000';
      try {
        const response = await fetch(`${backendUrl}/api/plugin/ask`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(request.payload)
        });

        if (response.ok) {
          const data = await response.json();
          sendResponse({ success: true, data });
        } else {
          sendResponse({ success: false, error: `HTTP ${response.status}` });
        }
      } catch (err) {
        sendResponse({ success: false, error: err.message });
      }
    });

    return true;
  }
});
