(function () {
  if (window.__SHOPEE_AI_COPILOT__) return;
  window.__SHOPEE_AI_COPILOT__ = true;

  function sendToShopeeChat(text, onlyFill = false) {
    const input = document.querySelector('textarea.chat-input, input.chat-input, [placeholder*="bình luận"], textarea');
    if (!input) return false;

    input.focus();
    // Trigger input update for React
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value')?.set;
    if (setter) setter.call(input, text);
    else input.value = text;

    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));

    if (!onlyFill) {
      const sendBtn = document.querySelector('button.chat-send-btn, button[type="submit"], .send-btn');
      if (sendBtn && !sendBtn.disabled) {
        sendBtn.click();
      } else {
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
      }
    }
    return true;
  }

  const host = document.createElement('div');
  host.id = 'shopee-live-ai-copilot-host';
  document.body.appendChild(host);

  const shadow = host.attachShadow({ mode: 'open' });
  const cssUrl = chrome.runtime?.getURL('src/content/overlay.css');
  shadow.innerHTML = `
    <link rel="stylesheet" href="${cssUrl}">
    <div class="copilot-container" id="copilot-box">
      <div class="copilot-toast" id="toast">Đã gửi vào chat</div>

      <div class="copilot-header" id="drag-handle">
        <div class="brand-title">
          <span class="brand-tag">AI</span>
          <span>Shopee Copilot</span>
          <span class="status-dot"></span>
        </div>
        <button class="header-btn" id="btn-minimize">_</button>
      </div>

      <div class="feed-list" id="feed">
        <div class="empty-state">
          <p><strong>Theo dõi bình luận live</strong></p>
          <p style="font-size:11px;margin-top:4px;">Gợi ý câu trả lời sẽ xuất hiện khi có câu hỏi mới.</p>
        </div>
      </div>

      <div class="copilot-footer">
        <span>Gửi: <span class="shortcut-key">Alt + 1</span></span>
        <span id="card-count">0 câu hỏi</span>
      </div>
    </div>
  `;

  const feed = shadow.querySelector('#feed');
  const toast = shadow.querySelector('#toast');
  const countLabel = shadow.querySelector('#card-count');
  const box = shadow.querySelector('#copilot-box');
  const comments = [];

  function showToast(msg) {
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 1800);
  }

  function renderFeed() {
    countLabel.textContent = `${comments.length} câu hỏi`;

    if (comments.length === 0) {
      feed.innerHTML = `
        <div class="empty-state">
          <p><strong>Không có câu hỏi nào cần xử lý</strong></p>
        </div>
      `;
      return;
    }

    feed.innerHTML = comments.map((c, idx) => `
      <div class="comment-card" data-id="${c.id}">
        <div class="comment-meta">
          <span class="sender-tag">@${c.sender}</span>
          <span style="color:#64748b;font-size:10px;">${c.time}</span>
        </div>
        <div class="comment-text">"${c.text}"</div>

        <div class="ai-answer-box">
          <div class="ai-header">
            <span>Gợi ý phản hồi:</span>
            <span class="ai-source">${c.source || 'AI Agent'}</span>
          </div>
          <div class="ai-draft-text">${c.answer}</div>
        </div>

        <div class="card-actions">
          <button class="btn-send" data-action="send" data-id="${c.id}">
            Gửi ngay ${idx === 0 ? '<span class="shortcut-key">Alt+1</span>' : ''}
          </button>
          <button class="btn-fill" data-action="fill" data-id="${c.id}">
            Điền
          </button>
          <button class="btn-dismiss" data-action="dismiss" data-id="${c.id}">✕</button>
        </div>
      </div>
    `).join('');
  }

  function handleSend(id, onlyFill = false) {
    const item = comments.find(c => c.id === id);
    if (!item) return;

    const ok = sendToShopeeChat(item.answer, onlyFill);
    if (ok) {
      showToast(onlyFill ? 'Đã điền vào ô chat' : 'Đã gửi vào chat');
      if (!onlyFill) {
        const idx = comments.findIndex(c => c.id === id);
        if (idx !== -1) comments.splice(idx, 1);
        renderFeed();
      }
    } else {
      showToast('Không tìm thấy ô chat');
    }
  }

  shadow.addEventListener('click', (e) => {
    if (e.target.closest('#btn-minimize')) {
      box.classList.toggle('minimized');
      e.target.closest('#btn-minimize').textContent = box.classList.contains('minimized') ? '□' : '_';
      return;
    }

    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    const id = btn.dataset.id;

    if (action === 'send') handleSend(id, false);
    else if (action === 'fill') handleSend(id, true);
    else if (action === 'dismiss') {
      const idx = comments.findIndex(c => c.id === id);
      if (idx !== -1) comments.splice(idx, 1);
      renderFeed();
    }
  });

  window.addEventListener('keydown', (e) => {
    if (e.altKey && (e.key === '1' || e.code === 'Digit1') && comments.length > 0) {
      e.preventDefault();
      handleSend(comments[0].id, false);
    }
  });

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

  const seenIds = new Set();
  const chatObserver = new MutationObserver((mutations) => {
    for (const mut of mutations) {
      for (const node of mut.addedNodes) {
        if (node.nodeType !== Node.ELEMENT_NODE) continue;
        if (node.closest('#shopee-live-ai-copilot-host')) continue;

        const textEl = node.querySelector('.content, .message, .chat-text, [class*="content"]') || node;
        const text = textEl.innerText?.trim();
        if (!text) continue;

        const senderEl = node.querySelector('.user-name, .sender-name, strong, b');
        const sender = senderEl ? senderEl.innerText.replace(/[:：]/g, '').trim() : 'Khách';

        const id = node.getAttribute('data-msg-id') || `${sender}_${text}`;
        if (seenIds.has(id)) continue;
        seenIds.add(id);

        // Filter questions
        const isQuestion = text.includes('?') || /(size|giá|còn|hết|màu|ship|voucher|bao nhiêu)/i.test(text);
        if (!isQuestion) continue;

        const commentItem = {
          id,
          sender,
          text,
          time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          answer: 'Đang kết nối AI...',
          source: 'Backend'
        };

        comments.unshift(commentItem);
        if (comments.length > 30) comments.pop();
        renderFeed();

        chrome.runtime?.sendMessage({
          type: 'AI_QUERY',
          payload: { text, sender, comment_id: id }
        }, (res) => {
          if (res?.success && res.data?.answer) {
            commentItem.answer = res.data.answer;
            commentItem.source = res.data.source || 'AI Agent';
            renderFeed();
          } else {
            commentItem.answer = `Dạ shop chào bạn @${sender}! Bạn bấm vào giỏ hàng để xem chi tiết và voucher ưu đãi nhé ạ!`;
            commentItem.source = 'Gợi ý nhanh';
            renderFeed();
          }
        });
      }
    }
  });

  function startObserver() {
    const container = document.querySelector('.chat-messages-container, .chat-list, [class*="chat-message"], [class*="chat_list"]');
    if (container) {
      chatObserver.observe(container, { childList: true, subtree: true });
    } else {
      setTimeout(startObserver, 2000);
    }
  }

  startObserver();
})();
