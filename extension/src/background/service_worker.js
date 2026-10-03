/**
 * Shopee Live AI Copilot - Background Service Worker
 * Quản lý cấu hình, lưu trữ và kết nối tới Backend AI Agent
 */

chrome.runtime.onInstalled.addListener(() => {
  console.log('[Shopee AI Copilot] Extension đã cài đặt thành công!');

  // Cấu hình mặc định
  chrome.storage.local.set({
    backendUrl: 'http://localhost:8000',
    autoReplyEnabled: false,
    soundAlertEnabled: true,
    teleprompterMode: false
  });
});

// Lắng nghe messages từ Content Script hoặc Popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === 'PING') {
    sendResponse({ status: 'PONG', timestamp: Date.now() });
    return true;
  }

  // Chuyển tiếp câu hỏi tới Backend AI
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

    return true; // Cho phép async response
  }
});
