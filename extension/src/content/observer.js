/**
 * Shopee Live Chat Observer
 * Lắng nghe DOM của Shopee Live PC để bắt bình luận mới thời gian thực
 */

export class ShopeeChatObserver {
  constructor(options = {}) {
    this.onComment = options.onComment || (() => {});
    this.customContainerSelector = options.containerSelector;
    this.customItemSelector = options.itemSelector;

    this.containerSelectors = [
      '#mock-chat-messages',         // Cho test mock page
      '.chat-messages-container',    // Thường gặp trên Shopee Live
      '.chat-list',
      '.live-chat-panel__messages',
      '.chat-history-container',
      '[class*="chat-message-list"]',
      '[class*="chat_list"]',
      '[class*="ChatList"]',
      ...(this.customContainerSelector ? [this.customContainerSelector] : [])
    ];

    this.processedIds = new Set();
    this.observer = null;
    this.isObserving = false;
    this.retryTimer = null;
  }

  /**
   * Tạo ID duy nhất cho mỗi comment để tránh trùng lặp
   */
  hashComment(sender, text, timestamp) {
    const raw = `${sender}_${text}_${Math.floor(timestamp / 5000)}`; // gom nhóm theo khoảng 5s
    let hash = 0;
    for (let i = 0; i < raw.length; i++) {
      hash = ((hash << 5) - hash) + raw.charCodeAt(i);
      hash |= 0;
    }
    return `cmt_${Math.abs(hash).toString(36)}`;
  }

  /**
   * Tìm phần tử chứa danh sách tin nhắn chat
   */
  findChatContainer() {
    for (const sel of this.containerSelectors) {
      try {
        const el = document.querySelector(sel);
        if (el) return el;
      } catch (e) {}
    }

    // Heuristic: Tìm container có thanh cuộn và chứa các thẻ có text/bình luận
    const scrollables = Array.from(document.querySelectorAll('div, ul, section')).filter(el => {
      const style = window.getComputedStyle(el);
      const isScroll = (style.overflowY === 'auto' || style.overflowY === 'scroll');
      return isScroll && el.innerText.length > 20 && el.offsetHeight > 150;
    });

    if (scrollables.length > 0) {
      return scrollables[0];
    }

    return null;
  }

  /**
   * Bóc tách dữ liệu từ một DOM node tin nhắn
   */
  extractComment(node) {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return null;

    // Bỏ qua nếu là phần tử của chính extension
    if (node.id === 'shopee-live-ai-copilot-host' || node.closest('#shopee-live-ai-copilot-host')) {
      return null;
    }

    // Bóc tách tên người gửi
    let sender = '';
    const senderEl = node.querySelector(
      '.user-name, .sender-name, .nickname, [class*="username"], [class*="nick"], strong, b'
    );
    if (senderEl) {
      sender = senderEl.innerText.replace(/[:：]/g, '').trim();
    }

    // Bóc tách nội dung
    let text = '';
    const contentEl = node.querySelector(
      '.content, .message, .chat-text, [class*="content"], [class*="text"], span:last-child'
    );
    if (contentEl) {
      text = contentEl.innerText.trim();
    } else {
      // Fallback: Lấy toàn bộ innerText của node trừ sender
      const fullText = node.innerText.trim();
      text = sender ? fullText.replace(sender, '').replace(/^[:：\s]+/, '').trim() : fullText;
    }

    if (!text || text.length === 0) return null;

    // Lấy ID có sẵn hoặc tự sinh ID
    const explicitId = node.getAttribute('data-msg-id') || node.getAttribute('id');
    const commentId = explicitId || this.hashComment(sender || 'guest', text, Date.now());

    if (this.processedIds.has(commentId)) {
      return null; // Đã xử lý
    }

    this.processedIds.add(commentId);
    // Giữ kích thước cache dưới 500 ID
    if (this.processedIds.size > 500) {
      const it = this.processedIds.values();
      for (let i = 0; i < 100; i++) this.processedIds.delete(it.next().value);
    }

    return {
      id: commentId,
      sender: sender || 'Khách hàng',
      text: text,
      timestamp: Date.now()
    };
  }

  /**
   * Bắt đầu quan sát DOM
   */
  start() {
    if (this.isObserving) return;

    const container = this.findChatContainer();
    if (!container) {
      console.log('[Shopee AI Copilot] Chưa tìm thấy khung chat, đang thử lại sau 2s...');
      this.retryTimer = setTimeout(() => this.start(), 2000);
      return;
    }

    console.log('[Shopee AI Copilot] Đã gắn Observer vào khung chat:', container);

    this.observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        for (const addedNode of mutation.addedNodes) {
          if (addedNode.nodeType === Node.ELEMENT_NODE) {
            // Trường hợp node vừa thêm chính là comment item
            const comment = this.extractComment(addedNode);
            if (comment) {
              this.onComment(comment);
            }

            // Trường hợp container bọc nhiều comment con
            const childItems = addedNode.querySelectorAll?.('li, div[class*="item"], div[class*="message"]');
            if (childItems && childItems.length > 0) {
              childItems.forEach(child => {
                const subComment = this.extractComment(child);
                if (subComment) this.onComment(subComment);
              });
            }
          }
        }
      }
    });

    this.observer.observe(container, {
      childList: true,
      subtree: true
    });

    this.isObserving = true;
  }

  /**
   * Dừng quan sát
   */
  stop() {
    if (this.retryTimer) clearTimeout(this.retryTimer);
    if (this.observer) {
      this.observer.disconnect();
      this.observer = null;
    }
    this.isObserving = false;
  }
}
