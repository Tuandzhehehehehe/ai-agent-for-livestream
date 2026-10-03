/**
 * Shopee Live Chat Injector
 * Chịu trách nhiệm điền text và gửi tin nhắn vào ô chat của Shopee Live PC
 * Xử lý được React/Vue controlled input thông qua native property descriptor.
 */

export class ShopeeChatInjector {
  constructor(customSelectors = {}) {
    this.selectors = {
      // Các selector khả dĩ cho ô chat Shopee Live PC / Shopee Creator
      input: [
        'textarea.chat-input',
        'input.chat-input',
        'textarea[placeholder*="chat"]',
        'textarea[placeholder*="bình luận"]',
        'textarea[placeholder*="Bình luận"]',
        'input[placeholder*="Bình luận"]',
        'input[placeholder*="bình luận"]',
        '.shopee-chat-input textarea',
        '.chat-editor textarea',
        '#mock-chat-input', // Hỗ trợ test mock page
        ... (customSelectors.input ? [customSelectors.input] : [])
      ],
      // Các selector khả dĩ cho nút gửi
      sendBtn: [
        'button.chat-send-btn',
        'button[type="submit"]',
        '.chat-send-btn',
        '.send-btn',
        'button:has(svg)',
        '#mock-send-btn', // Hỗ trợ test mock page
        ... (customSelectors.sendBtn ? [customSelectors.sendBtn] : [])
      ]
    };
  }

  /**
   * Tìm phần tử ô nhập chat
   */
  findInputElement() {
    for (const sel of this.selectors.input) {
      try {
        const el = document.querySelector(sel);
        if (el && el.offsetParent !== null) { // Phần tử đang hiển thị (visible)
          return el;
        }
      } catch (e) {
        // bỏ qua selector không hợp lệ
      }
    }
    // Fallback: Tìm textarea bất kỳ trong trang
    const textareas = Array.from(document.querySelectorAll('textarea'));
    const visibleTextarea = textareas.find(t => t.offsetParent !== null);
    if (visibleTextarea) return visibleTextarea;

    return null;
  }

  /**
   * Tìm phần tử nút gửi
   */
  findSendButton(inputEl) {
    for (const sel of this.selectors.sendBtn) {
      try {
        const btn = document.querySelector(sel);
        if (btn && btn.offsetParent !== null) {
          return btn;
        }
      } catch (e) {}
    }

    // Nếu không tìm thấy bằng selector, thử tìm nút nằm cạnh inputEl
    if (inputEl && inputEl.parentElement) {
      const siblingBtn = inputEl.parentElement.querySelector('button');
      if (siblingBtn) return siblingBtn;
      const parentSiblingBtn = inputEl.parentElement.parentElement?.querySelector('button');
      if (parentSiblingBtn) return parentSiblingBtn;
    }

    return null;
  }

  /**
   * Điền nội dung vào ô input và kích hoạt React state
   */
  setText(inputEl, text) {
    if (!inputEl) {
      console.warn('[Shopee AI Copilot] Không tìm thấy ô nhập chat!');
      return false;
    }

    inputEl.focus();

    // Dùng native value setter để React/Vue không ghi đè lại giá trị cũ
    const isTextArea = inputEl.tagName.toLowerCase() === 'textarea';
    const prototype = isTextArea ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const nativeSetter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;

    if (nativeSetter) {
      nativeSetter.call(inputEl, text);
    } else {
      inputEl.value = text;
    }

    // Dispatch các sự kiện tiêu chuẩn
    inputEl.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
    inputEl.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));

    return true;
  }

  /**
   * Điền text và kích hoạt gửi tin nhắn
   */
  fillAndSend(text, options = { delayMs: 150 }) {
    const inputEl = this.findInputElement();
    if (!inputEl) {
      return { success: false, reason: 'INPUT_NOT_FOUND' };
    }

    const ok = this.setText(inputEl, text);
    if (!ok) return { success: false, reason: 'SET_TEXT_FAILED' };

    return new Promise((resolve) => {
      setTimeout(() => {
        const sendBtn = this.findSendButton(inputEl);
        if (sendBtn && !sendBtn.disabled) {
          sendBtn.click();
          resolve({ success: true, method: 'BUTTON_CLICK' });
        } else {
          // Fallback: Giả lập bấm phím Enter
          inputEl.dispatchEvent(new KeyboardEvent('keydown', {
            key: 'Enter',
            code: 'Enter',
            keyCode: 13,
            which: 13,
            bubbles: true,
            cancelable: true
          }));
          inputEl.dispatchEvent(new KeyboardEvent('keypress', {
            key: 'Enter',
            code: 'Enter',
            keyCode: 13,
            which: 13,
            bubbles: true,
            cancelable: true
          }));
          inputEl.dispatchEvent(new KeyboardEvent('keyup', {
            key: 'Enter',
            code: 'Enter',
            keyCode: 13,
            which: 13,
            bubbles: true,
            cancelable: true
          }));
          resolve({ success: true, method: 'ENTER_KEY' });
        }
      }, options.delayMs || 150);
    });
  }
}
