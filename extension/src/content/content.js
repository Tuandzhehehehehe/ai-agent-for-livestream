/**
 * Shopee Live AI Copilot - Content Script Entry Point
 * Tích hợp toàn bộ hệ thống: Mount Shadow DOM -> Overlay UI -> Observer -> Injector.
 */

(function () {
  console.log('[Shopee AI Copilot] Content Script đã kích hoạt trên:', window.location.href);

  // Tránh inject 2 lần
  if (window.__SHOPEE_AI_COPILOT_LOADED__) return;
  window.__SHOPEE_AI_COPILOT_LOADED__ = true;

  // ==========================================
  // 1. INJECTOR COMPONENT
  // ==========================================
  class ShopeeChatInjector {
    constructor() {
      this.selectors = {
        input: [
          '#mock-chat-input', // Mock test page
          'textarea.chat-input',
          'input.chat-input',
          'textarea[placeholder*="chat"]',
          'textarea[placeholder*="bình luận"]',
          'textarea[placeholder*="Bình luận"]',
          'input[placeholder*="Bình luận"]',
          'input[placeholder*="bình luận"]',
          '.shopee-chat-input textarea',
          '.chat-editor textarea'
        ],
        sendBtn: [
          '#mock-send-btn', // Mock test page
          'button.chat-send-btn',
          'button[type="submit"]',
          '.chat-send-btn',
          '.send-btn',
          'button:has(svg)'
        ]
      };
    }

    findInputElement() {
      for (const sel of this.selectors.input) {
        try {
          const el = document.querySelector(sel);
          if (el && el.offsetParent !== null) return el;
        } catch (e) {}
      }
      const textareas = Array.from(document.querySelectorAll('textarea'));
      return textareas.find(t => t.offsetParent !== null) || null;
    }

    findSendButton(inputEl) {
      for (const sel of this.selectors.sendBtn) {
        try {
          const btn = document.querySelector(sel);
          if (btn && btn.offsetParent !== null) return btn;
        } catch (e) {}
      }
      if (inputEl && inputEl.parentElement) {
        const siblingBtn = inputEl.parentElement.querySelector('button');
        if (siblingBtn) return siblingBtn;
      }
      return null;
    }

    setText(inputEl, text) {
      if (!inputEl) return false;
      inputEl.focus();

      const isTextArea = inputEl.tagName.toLowerCase() === 'textarea';
      const proto = isTextArea ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      const nativeSetter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;

      if (nativeSetter) {
        nativeSetter.call(inputEl, text);
      } else {
        inputEl.value = text;
      }

      inputEl.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
      inputEl.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
      return true;
    }

    fillAndSend(text, delayMs = 150) {
      const inputEl = this.findInputElement();
      if (!inputEl) return { success: false, reason: 'INPUT_NOT_FOUND' };

      this.setText(inputEl, text);

      return new Promise((resolve) => {
        setTimeout(() => {
          const sendBtn = this.findSendButton(inputEl);
          if (sendBtn && !sendBtn.disabled) {
            sendBtn.click();
            resolve({ success: true, method: 'BUTTON_CLICK' });
          } else {
            inputEl.dispatchEvent(new KeyboardEvent('keydown', {
              key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true
            }));
            resolve({ success: true, method: 'ENTER_KEY' });
          }
        }, delayMs);
      });
    }
  }

  // ==========================================
  // 2. OBSERVER COMPONENT
  // ==========================================
  class ShopeeChatObserver {
    constructor(onComment) {
      this.onComment = onComment;
      this.processedIds = new Set();
      this.observer = null;
      this.isObserving = false;
      this.retryTimer = null;
      this.containerSelectors = [
        '#mock-chat-messages',
        '.chat-messages-container',
        '.chat-list',
        '.live-chat-panel__messages',
        '.chat-history-container',
        '[class*="chat-message-list"]',
        '[class*="chat_list"]'
      ];
    }

    findChatContainer() {
      for (const sel of this.containerSelectors) {
        try {
          const el = document.querySelector(sel);
          if (el) return el;
        } catch (e) {}
      }
      return null;
    }

    extractComment(node) {
      if (!node || node.nodeType !== Node.ELEMENT_NODE) return null;
      if (node.id === 'shopee-live-ai-copilot-host' || node.closest('#shopee-live-ai-copilot-host')) return null;

      let sender = '';
      const senderEl = node.querySelector('.user-name, .sender-name, .nickname, [class*="username"], strong, b');
      if (senderEl) {
        sender = senderEl.innerText.replace(/[:：]/g, '').trim();
      }

      let text = '';
      const contentEl = node.querySelector('.content, .message, .chat-text, [class*="content"], [class*="text"]');
      if (contentEl) {
        text = contentEl.innerText.trim();
      } else {
        const fullText = node.innerText.trim();
        text = sender ? fullText.replace(sender, '').replace(/^[:：\s]+/, '').trim() : fullText;
      }

      if (!text) return null;

      const commentId = node.getAttribute('data-msg-id') || `cmt_${Date.now()}_${Math.random().toString(36).substr(2, 5)}`;
      if (this.processedIds.has(commentId)) return null;

      this.processedIds.add(commentId);
      if (this.processedIds.size > 400) {
        const it = this.processedIds.values();
        for (let i = 0; i < 80; i++) this.processedIds.delete(it.next().value);
      }

      return {
        id: commentId,
        sender: sender || 'Khách hàng',
        text: text,
        timestamp: Date.now()
      };
    }

    start() {
      if (this.isObserving) return;
      const container = this.findChatContainer();
      if (!container) {
        this.retryTimer = setTimeout(() => this.start(), 1500);
        return;
      }

      console.log('[Shopee AI Copilot] Gắn Observer thành công vào:', container);

      this.observer = new MutationObserver((mutations) => {
        for (const mut of mutations) {
          for (const node of mut.addedNodes) {
            if (node.nodeType === Node.ELEMENT_NODE) {
              const cmt = this.extractComment(node);
              if (cmt) this.onComment(cmt);

              const childItems = node.querySelectorAll?.('li, div[class*="item"], div[class*="message"]');
              if (childItems) {
                childItems.forEach(child => {
                  const sub = this.extractComment(child);
                  if (sub) this.onComment(sub);
                });
              }
            }
          }
        }
      });

      this.observer.observe(container, { childList: true, subtree: true });
      this.isObserving = true;
    }
  }

  // ==========================================
  // 3. SHADOW DOM MOUNT & OVERLAY UI
  // ==========================================
  function initCopilot() {
    let host = document.getElementById('shopee-live-ai-copilot-host');
    if (!host) {
      host = document.createElement('div');
      host.id = 'shopee-live-ai-copilot-host';
      document.body.appendChild(host);
    }

    const shadow = host.attachShadow({ mode: 'open' });

    // Inject CSS
    const cssUrl = chrome.runtime?.getURL ? chrome.runtime.getURL('src/content/overlay.css') : '';
    let styleLink = '';
    if (cssUrl) {
      styleLink = `<link rel="stylesheet" href="${cssUrl}">`;
    }

    // Default inline fallback styles for complete independence
    const inlineStyle = `
      <style>
        :host { all: initial; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        .copilot-container {
          position: fixed; top: 60px; right: 20px; width: 380px; max-width: calc(100vw - 40px);
          height: 600px; max-height: calc(100vh - 80px); background: rgba(15, 18, 26, 0.94);
          backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
          border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 16px;
          box-shadow: 0 20px 48px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(238, 77, 45, 0.2);
          display: flex; flex-direction: column; overflow: hidden; z-index: 2147483647; color: #e2e8f0; font-size: 13px;
        }
        .copilot-container.minimized { height: 48px; width: 230px; }
        .copilot-header {
          display: flex; align-items: center; justify-content: space-between; padding: 12px 16px;
          background: linear-gradient(135deg, rgba(238, 77, 45, 0.25) 0%, rgba(30, 36, 50, 0.8) 100%);
          border-bottom: 1px solid rgba(255, 255, 255, 0.08); cursor: grab; user-select: none;
        }
        .brand-title { display: flex; align-items: center; gap: 8px; font-weight: 700; color: #ffffff; }
        .brand-icon { width: 22px; height: 22px; background: #ee4d2d; border-radius: 5px; display: flex; align-items: center; justify-content: center; font-size: 12px; }
        .status-dot { width: 7px; height: 7px; border-radius: 50%; background: #10b981; display: inline-block; box-shadow: 0 0 6px #10b981; }
        .header-btn { background: rgba(255, 255, 255, 0.08); border: none; color: #94a3b8; width: 24px; height: 24px; border-radius: 4px; cursor: pointer; }
        .pinned-banner { display: flex; align-items: center; gap: 8px; padding: 7px 12px; background: rgba(238, 77, 45, 0.09); border-bottom: 1px solid rgba(238, 77, 45, 0.2); font-size: 12px; }
        .pinned-badge { background: #ee4d2d; color: #fff; padding: 1px 5px; border-radius: 3px; font-weight: 700; font-size: 10px; }
        .tabs-nav { display: flex; padding: 6px 10px 0 10px; background: rgba(20, 24, 34, 0.6); border-bottom: 1px solid rgba(255, 255, 255, 0.06); gap: 4px; }
        .tab-btn { background: transparent; border: none; color: #94a3b8; padding: 6px 10px; font-size: 12px; cursor: pointer; border-radius: 6px 6px 0 0; }
        .tab-btn.active { color: #fff; background: rgba(255, 255, 255, 0.06); font-weight: 600; border-bottom: 2px solid #ee4d2d; }
        .badge-count { background: #ee4d2d; color: #fff; font-size: 10px; padding: 1px 5px; border-radius: 10px; font-weight: 700; margin-left: 4px; }
        .feed-list { flex: 1; overflow-y: auto; padding: 10px; display: flex; flex-direction: column; gap: 10px; }
        .feed-list::-webkit-scrollbar { width: 4px; }
        .feed-list::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.2); border-radius: 3px; }
        .empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 50px 16px; text-align: center; color: #64748b; gap: 6px; }
        .comment-card { background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.07); border-radius: 10px; padding: 10px; display: flex; flex-direction: column; gap: 7px; }
        .comment-card.priority-card { border-left: 3px solid #ee4d2d; background: linear-gradient(90deg, rgba(238, 77, 45, 0.08) 0%, rgba(255, 255, 255, 0.02) 100%); }
        .comment-meta { display: flex; justify-content: space-between; align-items: center; font-size: 11px; }
        .sender-tag { color: #38bdf8; font-weight: 600; }
        .intent-tag { font-size: 10px; padding: 2px 5px; border-radius: 4px; font-weight: 600; background: rgba(56, 189, 248, 0.15); color: #38bdf8; }
        .comment-text { font-size: 13px; line-height: 1.4; color: #f8fafc; }
        .ai-answer-box { background: rgba(238, 77, 45, 0.12); border: 1px dashed rgba(238, 77, 45, 0.35); border-radius: 8px; padding: 8px 10px; display: flex; flex-direction: column; gap: 5px; }
        .ai-header { display: flex; justify-content: space-between; font-size: 11px; color: #fb923c; font-weight: 600; }
        .ai-draft-text { font-size: 12px; color: #ffffff; line-height: 1.4; }
        .card-actions { display: flex; gap: 6px; margin-top: 2px; }
        .btn-send { flex: 2; background: linear-gradient(135deg, #ff6535, #ee4d2d); color: #fff; border: none; padding: 6px 10px; border-radius: 5px; font-size: 12px; font-weight: 600; cursor: pointer; }
        .btn-send:hover { filter: brightness(1.1); }
        .btn-fill { flex: 1; background: rgba(255, 255, 255, 0.08); color: #e2e8f0; border: 1px solid rgba(255, 255, 255, 0.1); padding: 6px 8px; border-radius: 5px; font-size: 12px; cursor: pointer; }
        .btn-fill:hover { background: rgba(255, 255, 255, 0.14); }
        .btn-dismiss { background: transparent; color: #64748b; border: none; padding: 4px 6px; cursor: pointer; border-radius: 4px; }
        .btn-dismiss:hover { color: #cbd5e1; }
        .copilot-footer { padding: 6px 12px; background: rgba(15, 18, 25, 0.9); border-top: 1px solid rgba(255, 255, 255, 0.06); display: flex; justify-content: space-between; font-size: 11px; color: #64748b; }
        .shortcut-key { background: rgba(255, 255, 255, 0.12); color: #f1f5f9; padding: 1px 4px; border-radius: 3px; font-family: monospace; font-size: 10px; }
        .copilot-toast { position: absolute; bottom: 42px; left: 50%; transform: translateX(-50%); background: #10b981; color: #fff; padding: 5px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; opacity: 0; transition: opacity 0.2s; pointer-events: none; }
        .copilot-toast.show { opacity: 1; }
      </style>
    `;

    shadow.innerHTML = `
      ${styleLink}
      ${inlineStyle}
      <div class="copilot-container" id="copilot-box">
        <div class="copilot-toast" id="copilot-toast">Đã gửi vào chat!</div>

        <div class="copilot-header" id="drag-handle">
          <div class="brand-title">
            <div class="brand-icon">⚡</div>
            <span>Shopee Live AI Copilot</span>
            <span class="status-dot"></span>
          </div>
          <button class="header-btn" id="btn-minimize">_</button>
        </div>

        <div class="pinned-banner">
          <span class="pinned-badge">Đang ghim</span>
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#f8fafc;">
            <strong>Áo Polo Classic [PROD-POLO-01]</strong> • <span style="color:#fb923c;font-weight:600;">189.000đ</span>
          </span>
        </div>

        <div class="tabs-nav">
          <button class="tab-btn active" data-tab="questions">⚡ Cần trả lời <span class="badge-count" id="count-q">0</span></button>
          <button class="tab-btn" data-tab="all">Tất cả <span class="badge-count" style="background:#475569;" id="count-all">0</span></button>
          <button class="tab-btn" data-tab="review">⚠️ Chờ duyệt <span class="badge-count" style="background:#f59e0b;" id="count-rev">0</span></button>
        </div>

        <div class="feed-list" id="feed-container">
          <div class="empty-state">
            <div style="font-size:28px;">🎧</div>
            <p><strong>Đang lắng nghe bình luận Live...</strong></p>
            <p style="font-size:11px;">Khi người xem đặt câu hỏi về size, giá, ship, AI sẽ gợi ý câu trả lời tại đây.</p>
          </div>
        </div>

        <div class="copilot-footer">
          <span>Gửi nhanh: <span class="shortcut-key">Alt + 1</span></span>
          <span id="ai-status">AI Engine: Online</span>
        </div>
      </div>
    `;

    // Khởi tạo Injector
    const injector = new ShopeeChatInjector();

    // Quản lý State
    const state = {
      comments: [],
      activeTab: 'questions',
      minimized: false,
      pinnedProduct: { id: 'PROD-POLO-01', name: 'Áo Polo Classic', price: '189.000đ', stock: 24 }
    };

    function showToast(msg) {
      const toast = shadow.querySelector('#copilot-toast');
      if (toast) {
        toast.textContent = msg;
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), 2000);
      }
    }

    function classifyIntent(text) {
      const lower = text.toLowerCase();
      if (lower.includes('hoàn tiền') || lower.includes('đổi trả') || lower.includes('rách') || lower.includes('hỏng') || lower.includes('lừa đảo')) {
        return {
          intent: 'review',
          badge: '⚠️ Chờ duyệt',
          needsReview: true,
          answer: 'Dạ shop rất xin lỗi về trải nghiệm của bạn! Shop xin phép nhắn tin trực tiếp để kiểm tra và hỗ trợ đổi mới ngay cho bạn ạ.',
          source: 'Chính sách bảo hành CSKH'
        };
      }
      if (lower.includes('size') || lower.includes('kg') || lower.includes('m7') || lower.includes('m6') || lower.includes('nặng') || lower.includes('cao')) {
        let recSize = 'L';
        const m = lower.match(/(\d+)\s*kg/);
        if (m) {
          const kg = parseInt(m[1], 10);
          if (kg <= 60) recSize = 'M';
          else if (kg <= 70) recSize = 'L';
          else recSize = 'XL';
        }
        return {
          intent: 'size',
          badge: '📏 Hỏi Size',
          needsReview: false,
          answer: `Dạ mẫu ${state.pinnedProduct.name} form chuẩn! Bạn chọn size ${recSize} là mặc vừa vặn đẹp tôn dáng nhé ạ.`,
          source: `Bảng size ${state.pinnedProduct.id}`
        };
      }
      if (lower.includes('còn') || lower.includes('màu') || lower.includes('hết')) {
        return {
          intent: 'stock',
          badge: '📦 Tồn kho',
          needsReview: false,
          answer: `Dạ mẫu này kho còn ${state.pinnedProduct.stock} áo, đủ màu đen và trắng trong giỏ hàng góc trái bạn nha!`,
          source: `Tồn kho realtime: ${state.pinnedProduct.id}`
        };
      }
      if (lower.includes('giá') || lower.includes('nhiêu') || lower.includes('bao tiền') || lower.includes('sale') || lower.includes('voucher')) {
        return {
          intent: 'price',
          badge: '🏷️ Hỏi Giá',
          needsReview: false,
          answer: `Dạ giá live chỉ ${state.pinnedProduct.price} (giá gốc 250k), bạn bấm giỏ hàng lưu thêm voucher giảm 15k nhé!`,
          source: 'Bảng giá Flashsale live'
        };
      }
      if (lower.includes('ship') || lower.includes('freeship') || lower.includes('giao')) {
        return {
          intent: 'ship',
          badge: '🚚 Vận chuyển',
          needsReview: false,
          answer: 'Dạ đơn từ 150k được áp mã Freeship Extra toàn quốc, giao nhanh 1-2 ngày bạn nha!',
          source: 'Chính sách vận chuyển Shopee'
        };
      }
      return null;
    }

    function renderFeed() {
      const feed = shadow.querySelector('#feed-container');
      if (!feed) return;

      let filtered = [];
      if (state.activeTab === 'questions') {
        filtered = state.comments.filter(c => c.ai && !c.ai.needsReview && !c.answered);
      } else if (state.activeTab === 'review') {
        filtered = state.comments.filter(c => c.ai && c.ai.needsReview);
      } else {
        filtered = state.comments;
      }

      // Update counters
      const qC = state.comments.filter(c => c.ai && !c.ai.needsReview && !c.answered).length;
      const allC = state.comments.length;
      const revC = state.comments.filter(c => c.ai && c.ai.needsReview).length;

      const elQ = shadow.querySelector('#count-q');
      const elAll = shadow.querySelector('#count-all');
      const elRev = shadow.querySelector('#count-rev');
      if (elQ) elQ.textContent = qC;
      if (elAll) elAll.textContent = allC;
      if (elRev) elRev.textContent = revC;

      if (filtered.length === 0) {
        feed.innerHTML = `
          <div class="empty-state">
            <div style="font-size:28px;">✨</div>
            <p><strong>Không có bình luận nào cần xử lý</strong></p>
            <p style="font-size:11px;">Mọi câu hỏi đã được giải đáp hoặc đang chờ tin nhắn mới.</p>
          </div>
        `;
        return;
      }

      feed.innerHTML = filtered.map((c, idx) => {
        const isPriority = c.ai && !c.ai.needsReview;
        return `
          <div class="comment-card ${isPriority ? 'priority-card' : ''}" data-id="${c.id}">
            <div class="comment-meta">
              <span class="sender-tag">👤 @${c.sender}</span>
              ${c.ai ? `<span class="intent-tag">${c.ai.badge}</span>` : `<span style="color:#64748b;font-size:10px;">${c.timeStr}</span>`}
            </div>
            <div class="comment-text">"${c.text}"</div>
            ${c.ai ? `
              <div class="ai-answer-box">
                <div class="ai-header">
                  <span>🤖 Gợi ý phản hồi:</span>
                  <span style="font-size:10px;color:#94a3b8;font-weight:normal;">${c.ai.source}</span>
                </div>
                <div class="ai-draft-text">${c.ai.answer}</div>
              </div>
              <div class="card-actions">
                ${!c.ai.needsReview ? `
                  <button class="btn-send" data-action="send" data-id="${c.id}" data-text="${encodeURIComponent(c.ai.answer)}">
                    🟢 Gửi ngay ${idx === 0 ? '<span class="shortcut-key">Alt+1</span>' : ''}
                  </button>
                  <button class="btn-fill" data-action="fill" data-id="${c.id}" data-text="${encodeURIComponent(c.ai.answer)}">
                    ✏️ Điền ô
                  </button>
                ` : `
                  <button class="btn-fill" style="flex:1;border-color:#f59e0b;color:#fbbf24;" data-action="fill" data-id="${c.id}" data-text="${encodeURIComponent(c.ai.answer)}">
                    ⚠️ Xem & Điền ô chat
                  </button>
                `}
                <button class="btn-dismiss" data-action="dismiss" data-id="${c.id}" title="Ẩn">✕</button>
              </div>
            ` : `
              <div class="card-actions" style="justify-content: flex-end;">
                <button class="btn-dismiss" data-action="dismiss" data-id="${c.id}" title="Ẩn">✕</button>
              </div>
            `}
          </div>
        `;
      }).join('');
    }

    async function handleSend(id, text) {
      const decoded = decodeURIComponent(text);
      const res = await injector.fillAndSend(decoded);
      if (res.success) {
        showToast('✅ Đã gửi vào khung chat Shopee!');
        const item = state.comments.find(c => c.id === id);
        if (item) item.answered = true;
        renderFeed();
      } else {
        showToast('⚠️ Không tìm thấy ô nhập chat!');
      }
    }

    function handleFill(id, text) {
      const decoded = decodeURIComponent(text);
      const inputEl = injector.findInputElement();
      if (inputEl) {
        injector.setText(inputEl, decoded);
        showToast('✏️ Đã điền vào ô chat!');
      } else {
        showToast('⚠️ Không tìm thấy ô nhập chat!');
      }
    }

    // Sự kiện tương tác
    shadow.addEventListener('click', (e) => {
      const tabBtn = e.target.closest('.tab-btn');
      if (tabBtn) {
        shadow.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        tabBtn.classList.add('active');
        state.activeTab = tabBtn.getAttribute('data-tab');
        renderFeed();
        return;
      }

      if (e.target.closest('#btn-minimize')) {
        const box = shadow.querySelector('#copilot-box');
        state.minimized = !state.minimized;
        box.classList.toggle('minimized', state.minimized);
        e.target.textContent = state.minimized ? '□' : '_';
        return;
      }

      const actBtn = e.target.closest('[data-action]');
      if (actBtn) {
        const act = actBtn.getAttribute('data-action');
        const id = actBtn.getAttribute('data-id');
        const text = actBtn.getAttribute('data-text');
        if (act === 'send') handleSend(id, text);
        else if (act === 'fill') handleFill(id, text);
        else if (act === 'dismiss') {
          const item = state.comments.find(c => c.id === id);
          if (item) item.answered = true;
          renderFeed();
        }
      }
    });

    // Kéo thả Header
    const box = shadow.querySelector('#copilot-box');
    const handle = shadow.querySelector('#drag-handle');
    let isDragging = false, startX, startY, initL, initT;

    handle.addEventListener('mousedown', (e) => {
      if (e.target.closest('.header-btn')) return;
      isDragging = true;
      startX = e.clientX; startY = e.clientY;
      const r = box.getBoundingClientRect();
      initL = r.left; initT = r.top;
      box.style.right = 'auto';
      box.style.left = `${initL}px`;
      box.style.top = `${initT}px`;
      e.preventDefault();
    });

    window.addEventListener('mousemove', (e) => {
      if (!isDragging) return;
      box.style.left = `${Math.max(10, Math.min(window.innerWidth - box.offsetWidth - 10, initL + e.clientX - startX))}px`;
      box.style.top = `${Math.max(10, Math.min(window.innerHeight - box.offsetHeight - 10, initT + e.clientY - startY))}px`;
    });

    window.addEventListener('mouseup', () => { isDragging = false; });

    // Phím tắt Alt + 1
    window.addEventListener('keydown', (e) => {
      if (e.altKey && (e.key === '1' || e.code === 'Digit1')) {
        e.preventDefault();
        const topActionable = state.comments.find(c => c.ai && !c.ai.needsReview && !c.answered);
        if (topActionable) {
          handleSend(topActionable.id, encodeURIComponent(topActionable.ai.answer));
        }
      }
    });

    // Khởi tạo Chat Observer
    const observer = new ShopeeChatObserver((comment) => {
      const ai = classifyIntent(comment.text);
      state.comments.unshift({
        ...comment,
        ai: ai,
        answered: false,
        timeStr: new Date(comment.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      });
      if (state.comments.length > 70) state.comments.pop();
      renderFeed();
    });

    observer.start();
  }

  // Khởi động khi DOM sẵn sàng
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initCopilot);
  } else {
    initCopilot();
  }
})();
