/**
 * Shopee Live AI Copilot - Overlay UI Component
 * Render toàn bộ giao diện quản trị trong Shadow DOM, xử lý sự kiện kéo thả,
 * phím tắt và tương tác với Chat Injector.
 */

export class ShopeeOverlayUI {
  constructor(shadowRoot, injector, options = {}) {
    this.shadowRoot = shadowRoot;
    this.injector = injector;
    this.options = options;

    this.activeTab = 'questions'; // 'questions' | 'all' | 'review'
    this.isMinimized = false;
    this.comments = [];
    this.pinnedProduct = {
      id: 'PROD-POLO-01',
      name: 'Áo Polo Nam Classic Phối Bo Cổ',
      price: '189.000đ',
      stock: 24,
      sizes: 'M (50-60kg), L (61-70kg), XL (71-80kg)'
    };

    this.init();
  }

  init() {
    this.render();
    this.bindEvents();
    this.setupDraggable();
    this.setupHotkeys();
  }

  /**
   * Phân loại intent nhanh ở client (hoàn toàn tương thích với ai.customer_qa)
   */
  classifyIntentLocally(text) {
    const lower = text.toLowerCase();

    // 1. Kiểm tra an toàn / Khiếu nại / Hoàn tiền
    if (lower.includes('hoàn tiền') || lower.includes('đổi trả') || lower.includes('hỏng') || lower.includes('rách') || lower.includes('lừa đảo') || lower.includes('khiếu nại')) {
      return {
        intent: 'review',
        badge: '⚠️ Chờ duyệt',
        badgeClass: 'intent-review',
        needsReview: true,
        answer: 'Dạ shop rất xin lỗi về trải nghiệm của bạn! Shop xin phép nhắn tin riêng để hỗ trợ đổi trả/hoàn tiền ngay cho bạn nhé ạ.',
        source: 'Chính sách bảo hành & CSKH'
      };
    }

    // 2. Hỏi kích thước / Size
    if (lower.includes('size') || lower.includes('kg') || lower.includes('m7') || lower.includes('m6') || lower.includes('m8') || lower.includes('nặng') || lower.includes('cao')) {
      let recSize = 'L';
      const weightMatch = lower.match(/(\d+)\s*kg/);
      if (weightMatch) {
        const kg = parseInt(weightMatch[1], 10);
        if (kg <= 60) recSize = 'M';
        else if (kg <= 70) recSize = 'L';
        else recSize = 'XL';
      }
      return {
        intent: 'size',
        badge: '📏 Hỏi Size',
        badgeClass: 'intent-size',
        needsReview: false,
        answer: `Dạ mẫu ${this.pinnedProduct.name} form chuẩn thoải mái! Bạn chọn size ${recSize} là mặc vừa vặn đẹp luôn nhé ạ.`,
        source: `Bảng size ${this.pinnedProduct.id}`
      };
    }

    // 3. Hỏi tồn kho / Màu sắc
    if (lower.includes('còn') || lower.includes('màu') || lower.includes('hết hàng') || lower.includes('đen') || lower.includes('trắng')) {
      return {
        intent: 'stock',
        badge: '📦 Tồn kho',
        badgeClass: 'intent-stock',
        needsReview: false,
        answer: `Dạ mẫu này kho còn ${this.pinnedProduct.stock} áo, đủ màu đen và trắng trong giỏ hàng góc trái bạn nha!`,
        source: `Tồn kho realtime: ${this.pinnedProduct.id}`
      };
    }

    // 4. Hỏi giá / Giảm giá
    if (lower.includes('giá') || lower.includes('nhiêu') || lower.includes('bao tiền') || lower.includes('sale') || lower.includes('voucher') || lower.includes('mã')) {
      return {
        intent: 'price',
        badge: '🏷️ Hỏi Giá',
        badgeClass: 'intent-price',
        needsReview: false,
        answer: `Dạ giá live siêu ưu đãi chỉ ${this.pinnedProduct.price} (giá gốc 250k), bạn bấm giỏ hàng lưu thêm voucher giảm 15k nhé!`,
        source: `Bảng giá Flashsale live`
      };
    }

    // 5. Hỏi Vận chuyển / Freeship
    if (lower.includes('ship') || lower.includes('freeship') || lower.includes('giao') || lower.includes('vận chuyển')) {
      return {
        intent: 'ship',
        badge: '🚚 Vận chuyển',
        badgeClass: 'intent-ship',
        needsReview: false,
        answer: 'Dạ đơn từ 150k được hỗ trợ Freeship Extra toàn quốc, giao hỏa tốc 2h tại Hà Nội/HCM bạn nha!',
        source: 'Chính sách vận chuyển Shopee'
      };
    }

    // Bình luận thông thường
    return null;
  }

  /**
   * Thêm bình luận mới vào danh sách
   */
  addComment(commentData) {
    const aiInsight = this.classifyIntentLocally(commentData.text);
    const enriched = {
      ...commentData,
      aiInsight: aiInsight,
      isAnswered: false,
      timestampStr: new Date(commentData.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    };

    // Thêm vào đầu mảng
    this.comments.unshift(enriched);
    if (this.comments.length > 80) this.comments.pop();

    this.updateFeed();
    this.updateCounters();

    // Nếu là câu hỏi mua hàng quan trọng thì hiển thị âm thanh/rung nhẹ
    if (aiInsight && !aiInsight.needsReview) {
      this.highlightHeader();
    }
  }

  highlightHeader() {
    const brandIcon = this.shadowRoot.querySelector('.brand-icon');
    if (brandIcon) {
      brandIcon.style.transform = 'scale(1.25)';
      setTimeout(() => { brandIcon.style.transform = 'scale(1)'; }, 300);
    }
  }

  showToast(message) {
    const toast = this.shadowRoot.querySelector('#copilot-toast');
    if (toast) {
      toast.textContent = message;
      toast.classList.add('show');
      setTimeout(() => toast.classList.remove('show'), 2200);
    }
  }

  render() {
    this.shadowRoot.innerHTML = `
      <link rel="stylesheet" href="${chrome.runtime?.getURL ? chrome.runtime.getURL('src/content/overlay.css') : 'overlay.css'}">
      
      <div class="copilot-container" id="copilot-box">
        <!-- Toast Notification -->
        <div class="copilot-toast" id="copilot-toast">Đã gửi vào chat!</div>

        <!-- Header -->
        <div class="copilot-header" id="drag-handle">
          <div class="brand-title">
            <div class="brand-icon">⚡</div>
            <span>Shopee AI Copilot</span>
            <span class="status-dot" title="Đang kết nối"></span>
          </div>
          <div class="header-actions">
            <button class="header-btn" id="btn-minimize" title="Thu nhỏ / Mở rộng">_</button>
          </div>
        </div>

        <!-- Pinned Product -->
        <div class="pinned-banner">
          <span class="pinned-badge">Đang ghim</span>
          <span class="pinned-info">
            <strong>${this.pinnedProduct.name}</strong> • <span class="pinned-price">${this.pinnedProduct.price}</span>
          </span>
        </div>

        <!-- Tabs -->
        <div class="tabs-nav">
          <button class="tab-btn active" data-tab="questions">
            ⚡ Cần trả lời <span class="badge-count" id="count-questions">0</span>
          </button>
          <button class="tab-btn" data-tab="all">
            Tất cả <span class="badge-count" style="background:#475569;" id="count-all">0</span>
          </button>
          <button class="tab-btn" data-tab="review">
            ⚠️ Chờ duyệt <span class="badge-count" style="background:#f59e0b;" id="count-review">0</span>
          </button>
        </div>

        <!-- Feed List -->
        <div class="feed-list" id="feed-container">
          <div class="empty-state">
            <div class="empty-icon">🎧</div>
            <p><strong>Đang lắng nghe bình luận Live...</strong></p>
            <p style="font-size:11px;">Khi người xem đặt câu hỏi về size, giá, tồn kho, AI sẽ lập tức gợi ý câu trả lời tại đây.</p>
          </div>
        </div>

        <!-- Footer -->
        <div class="copilot-footer">
          <span class="shortcut-hint">Gửi nhanh: <span class="shortcut-key">Alt + 1</span></span>
          <span id="ai-latency">AI Engine: Sẵn sàng</span>
        </div>
      </div>
    `;
  }

  updateCounters() {
    const qCount = this.comments.filter(c => c.aiInsight && !c.aiInsight.needsReview && !c.isAnswered).length;
    const allCount = this.comments.length;
    const rCount = this.comments.filter(c => c.aiInsight && c.aiInsight.needsReview && !c.isAnswered).length;

    const elQ = this.shadowRoot.querySelector('#count-questions');
    const elAll = this.shadowRoot.querySelector('#count-all');
    const elR = this.shadowRoot.querySelector('#count-review');

    if (elQ) elQ.textContent = qCount;
    if (elAll) elAll.textContent = allCount;
    if (elR) elR.textContent = rCount;
  }

  updateFeed() {
    const feed = this.shadowRoot.querySelector('#feed-container');
    if (!feed) return;

    let filtered = [];
    if (this.activeTab === 'questions') {
      filtered = this.comments.filter(c => c.aiInsight && !c.aiInsight.needsReview && !c.isAnswered);
    } else if (this.activeTab === 'review') {
      filtered = this.comments.filter(c => c.aiInsight && c.aiInsight.needsReview);
    } else {
      filtered = this.comments;
    }

    if (filtered.length === 0) {
      feed.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">✨</div>
          <p><strong>Không có câu hỏi nào cần xử lý</strong></p>
          <p style="font-size:11px;">Mọi bình luận đã được giải đáp hoặc đang chờ tin nhắn mới.</p>
        </div>
      `;
      return;
    }

    feed.innerHTML = filtered.map((c, index) => {
      const isPriority = c.aiInsight && !c.aiInsight.needsReview;
      const isReview = c.aiInsight && c.aiInsight.needsReview;

      return `
        <div class="comment-card ${isPriority ? 'priority-card' : ''} ${isReview ? 'review-card' : ''}" data-id="${c.id}">
          <div class="comment-meta">
            <span class="sender-tag">👤 @${this.escapeHtml(c.sender)}</span>
            ${c.aiInsight ? `<span class="intent-tag ${c.aiInsight.badgeClass}">${c.aiInsight.badge}</span>` : `<span style="color:#64748b;font-size:10px;">${c.timestampStr}</span>`}
          </div>

          <div class="comment-text">"${this.escapeHtml(c.text)}"</div>

          ${c.aiInsight ? `
            <div class="ai-answer-box">
              <div class="ai-header">
                <span>🤖 Gợi ý phản hồi:</span>
                <span class="ai-source">${this.escapeHtml(c.aiInsight.source)}</span>
              </div>
              <div class="ai-draft-text">${this.escapeHtml(c.aiInsight.answer)}</div>
            </div>

            <div class="card-actions">
              ${!c.aiInsight.needsReview ? `
                <button class="btn-send" data-action="send" data-id="${c.id}" data-text="${this.escapeHtml(c.aiInsight.answer)}">
                  🟢 Gửi ngay ${index === 0 ? '<span class="shortcut-key">Alt+1</span>' : ''}
                </button>
                <button class="btn-fill" data-action="fill" data-id="${c.id}" data-text="${this.escapeHtml(c.aiInsight.answer)}">
                  ✏️ Điền ô chat
                </button>
              ` : `
                <button class="btn-fill" style="flex:1; border-color:#f59e0b; color:#fbbf24;" data-action="fill" data-id="${c.id}" data-text="${this.escapeHtml(c.aiInsight.answer)}">
                  ⚠️ Xem xét & Điền chat
                </button>
              `}
              <button class="btn-dismiss" data-action="dismiss" data-id="${c.id}" title="Bỏ qua">✕</button>
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

  escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  bindEvents() {
    // 1. Chuyển Tabs
    this.shadowRoot.addEventListener('click', (e) => {
      const tabBtn = e.target.closest('.tab-btn');
      if (tabBtn) {
        this.shadowRoot.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        tabBtn.classList.add('active');
        this.activeTab = tabBtn.getAttribute('data-tab');
        this.updateFeed();
        return;
      }

      // 2. Thu nhỏ / Mở rộng
      if (e.target.closest('#btn-minimize')) {
        const box = this.shadowRoot.querySelector('#copilot-box');
        this.isMinimized = !this.isMinimized;
        box.classList.toggle('minimized', this.isMinimized);
        e.target.textContent = this.isMinimized ? '□' : '_';
        return;
      }

      // 3. Tác vụ trên Comment (Gửi ngay, Điền chat, Bỏ qua)
      const actionBtn = e.target.closest('[data-action]');
      if (actionBtn) {
        const action = actionBtn.getAttribute('data-action');
        const id = actionBtn.getAttribute('data-id');
        const text = actionBtn.getAttribute('data-text');

        if (action === 'send') {
          this.handleSend(id, text);
        } else if (action === 'fill') {
          this.handleFill(id, text);
        } else if (action === 'dismiss') {
          this.handleDismiss(id);
        }
      }
    });
  }

  async handleSend(commentId, text) {
    const res = await this.injector.fillAndSend(text);
    if (res.success) {
      this.showToast('✅ Đã gửi vào khung chat Shopee!');
      this.markAnswered(commentId);
    } else {
      this.showToast('⚠️ Không tìm thấy ô chat Shopee!');
    }
  }

  handleFill(commentId, text) {
    const inputEl = this.injector.findInputElement();
    if (inputEl) {
      this.injector.setText(inputEl, text);
      this.showToast('✏️ Đã điền vào ô chat!');
    } else {
      this.showToast('⚠️ Không tìm thấy ô chat Shopee!');
    }
  }

  handleDismiss(commentId) {
    this.markAnswered(commentId);
  }

  markAnswered(commentId) {
    const target = this.comments.find(c => c.id === commentId);
    if (target) {
      target.isAnswered = true;
      this.updateFeed();
      this.updateCounters();
    }
  }

  setupDraggable() {
    const box = this.shadowRoot.querySelector('#copilot-box');
    const handle = this.shadowRoot.querySelector('#drag-handle');
    if (!box || !handle) return;

    let isDragging = false;
    let startX, startY, initialLeft, initialTop;

    handle.addEventListener('mousedown', (e) => {
      if (e.target.closest('.header-btn')) return; // Không drag khi bấm nút
      isDragging = true;
      startX = e.clientX;
      startY = e.clientY;
      const rect = box.getBoundingClientRect();
      initialLeft = rect.left;
      initialTop = rect.top;

      box.style.right = 'auto'; // Hủy neo right
      box.style.left = `${initialLeft}px`;
      box.style.top = `${initialTop}px`;
      e.preventDefault();
    });

    window.addEventListener('mousemove', (e) => {
      if (!isDragging) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      box.style.left = `${Math.max(10, Math.min(window.innerWidth - box.offsetWidth - 10, initialLeft + dx))}px`;
      box.style.top = `${Math.max(10, Math.min(window.innerHeight - box.offsetHeight - 10, initialTop + dy))}px`;
    });

    window.addEventListener('mouseup', () => {
      isDragging = false;
    });
  }

  setupHotkeys() {
    window.addEventListener('keydown', (e) => {
      // Phím tắt Alt + 1: Gửi ngay câu hỏi đầu tiên
      if (e.altKey && (e.key === '1' || e.code === 'Digit1')) {
        e.preventDefault();
        const firstActionable = this.comments.find(c => c.aiInsight && !c.aiInsight.needsReview && !c.isAnswered);
        if (firstActionable) {
          this.handleSend(firstActionable.id, firstActionable.aiInsight.answer);
        }
      }
    });
  }
}
