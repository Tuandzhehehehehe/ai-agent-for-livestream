/**
 * Shopee Live PC - Console DOM Inspector & Selector Verification Tool
 * 
 * Hướng dẫn sử dụng:
 * 1. Mở trang Shopee Live PC (https://live.shopee.vn/pc/setup hoặc https://creator.shopee.vn)
 * 2. Mở Chrome DevTools (F12 hoặc Ctrl + Shift + I) -> Chuyển sang tab Console
 * 3. Dán toàn bộ script này vào Console và bấm Enter
 * 4. Script sẽ tự động quét và báo cáo các selector chat của shop bạn!
 */

(function inspectShopeeLive() {
  console.clear();
  console.log('%c🔍 SHOPEE LIVE DOM INSPECTOR 🔍', 'color: #ee4d2d; font-size: 16px; font-weight: bold;');

  const report = {
    chatContainer: null,
    chatInput: null,
    sendButton: null,
    recentComments: []
  };

  // 1. Quét tìm Chat Container
  const containerSelectors = [
    '.chat-messages-container',
    '.chat-list',
    '.live-chat-panel__messages',
    '.chat-history-container',
    '[class*="chat-message-list"]',
    '[class*="chat_list"]',
    '[class*="ChatList"]',
    '[class*="message-container"]'
  ];

  for (const sel of containerSelectors) {
    const el = document.querySelector(sel);
    if (el) {
      report.chatContainer = { selector: sel, element: el, itemsCount: el.children.length };
      break;
    }
  }

  // 2. Quét tìm Ô nhập chat
  const inputSelectors = [
    'textarea.chat-input',
    'input.chat-input',
    'textarea[placeholder*="chat"]',
    'textarea[placeholder*="bình luận"]',
    'textarea[placeholder*="Bình luận"]',
    'input[placeholder*="Bình luận"]',
    'input[placeholder*="bình luận"]',
    '.shopee-chat-input textarea',
    '.chat-editor textarea'
  ];

  for (const sel of inputSelectors) {
    const el = document.querySelector(sel);
    if (el && el.offsetParent !== null) {
      report.chatInput = { selector: sel, element: el, placeholder: el.placeholder };
      break;
    }
  }

  // 3. Quét tìm Nút gửi
  const sendSelectors = [
    'button.chat-send-btn',
    'button[type="submit"]',
    '.chat-send-btn',
    '.send-btn',
    'button:has(svg)'
  ];

  for (const sel of sendSelectors) {
    const el = document.querySelector(sel);
    if (el && el.offsetParent !== null) {
      report.sendButton = { selector: sel, element: el };
      break;
    }
  }

  // 4. In kết quả
  console.log('%c📋 BÁO CÁO QUÉT SELECTOR:', 'color: #38bdf8; font-weight: bold;');
  if (report.chatContainer) {
    console.log('✅ Khung Chat:', report.chatContainer.selector, report.chatContainer.element);
  } else {
    console.warn('❌ Chưa tìm thấy khung chat theo danh sách selector chuẩn.');
  }

  if (report.chatInput) {
    console.log('✅ Ô Nhập Chat:', report.chatInput.selector, report.chatInput.element);
  } else {
    console.warn('❌ Chưa tìm thấy ô nhập tin nhắn.');
  }

  if (report.sendButton) {
    console.log('✅ Nút Gửi Chat:', report.sendButton.selector, report.sendButton.element);
  } else {
    console.warn('⚠️ Chưa tìm thấy nút gửi (sẽ dùng fallback phím Enter).');
  }

  // 5. Thử nghiệm điền text mẫu
  if (report.chatInput) {
    console.log('%c👉 Đang thử nghiệm điền text mẫu vào ô chat...', 'color: #f59e0b;');
    const input = report.chatInput.element;
    const isTextArea = input.tagName.toLowerCase() === 'textarea';
    const proto = isTextArea ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    
    if (setter) {
      setter.call(input, '[Test AI Copilot] Xin chào mọi người!');
    } else {
      input.value = '[Test AI Copilot] Xin chào mọi người!';
    }
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    console.log('🎉 Đã điền thử nghiệm text thành công vào ô chat của bạn!');
  }

  return report;
})();
