(() => {
  const seenIds = new Set();

  function onComment(node) {
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    if (!node.matches?.('yt-live-chat-text-message-renderer, yt-live-chat-paid-message-renderer')) return;

    const sender = node.querySelector('#author-name')?.innerText?.trim();
    const text = node.querySelector('#message')?.innerText?.trim();
    if (!sender || !text) return;

    const id = node.id || `${sender}:${text}:${Date.now()}`;
    if (seenIds.has(id)) return;
    seenIds.add(id);

    const authorType = node.getAttribute('author-type') || 'viewer';
    console.log(`[YouTube Live] @${sender} [${authorType}]: ${text}`);

    chrome.runtime?.sendMessage({
      type: 'NEW_COMMENT',
      payload: { id, sender, text, authorType, timestamp: Date.now() }
    });
  }

  function startObserving(container) {
    console.log('%c[YouTube Live] Đã kết nối vào khung chat, đang thu thập bình luận realtime!', 'color: #10b981; font-weight: bold;');
    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        m.addedNodes.forEach(onComment);
      }
    });
    observer.observe(container, { childList: true });
  }

  function findContainer() {
    const container = document.querySelector('yt-live-chat-item-list-renderer #items, #item-scroller #items, #items');
    if (container) {
      startObserving(container);
      return true;
    }
    return false;
  }

  // Khởi động: tìm ngay hoặc chờ container xuất hiện trong DOM
  if (!findContainer()) {
    const docObserver = new MutationObserver(() => {
      if (findContainer()) {
        docObserver.disconnect();
      }
    });
    docObserver.observe(document.documentElement, { childList: true, subtree: true });
  }
})();
