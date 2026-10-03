# Shopee Live AI Copilot - Chrome Extension (Bước 1)

Plugin Overlay hỗ trợ người bán hàng trên Shopee Live PC tự động phân loại bình luận, gợi ý câu trả lời theo thời gian thực và tương tác 1 chạm (1-click fill & send) bằng AI.

---

## 📁 Cấu trúc thư mục Extension

```text
extension/
├── manifest.json              # Khai báo Chrome Extension Manifest V3
├── test_mock_shopee.html      # Trang giả lập Shopee Live PC để test ngay lập tức
├── shopee_dom_inspector.js    # Script chạy trên DevTools để quét DOM Shopee thật
├── icons/                     # Icon 16x16, 48x48, 128x128
└── src/
    ├── content/
    │   ├── content.js         # Entry point content script tích hợp toàn bộ hệ thống
    │   ├── injector.js        # Module điền và kích hoạt gửi tin nhắn an toàn
    │   ├── observer.js        # Module MutationObserver lắng nghe chat realtime
    │   ├── mount.js           # Module khởi tạo Shadow DOM cô lập CSS
    │   ├── overlay.js         # Giao diện quản trị, tabs, cards, hotkeys
    │   └── overlay.css        # CSS giao diện Glassmorphism độc lập
    ├── background/
    │   └── service_worker.js  # Background worker kết nối AI Backend
    └── popup/
        ├── popup.html         # Giao diện cài đặt khi bấm vào icon extension
        ├── popup.css
        └── popup.js
```

---

## 🚀 Cách 1: Chạy thử nghiệm ngay trong 5 giây (Không cần cài Extension)

Bạn có thể mở trực tiếp file `extension/test_mock_shopee.html` bằng trình duyệt Chrome, Edge hoặc Cốc Cốc:
1. Mở file [test_mock_shopee.html](file:///home/Dx/dev/ai-agent-for-livestream/extension/test_mock_shopee.html) trên trình duyệt.
2. Giao diện Shopee Live PC giả lập sẽ xuất hiện, kèm theo cửa sổ **⚡ Shopee Live AI Copilot** màu đen cam nổi bật ở góc phải.
3. Bấm vào các nút trên thanh công cụ phía trên:
   - `[📏 Bắn hỏi Size (65kg)]`
   - `[🏷️ Bắn hỏi Giá/Sale]`
   - `[📦 Bắn hỏi Tồn kho]`
   - `[🚚 Bắn hỏi Freeship]`
   - `[⚠️ Bắn Khiếu nại (Safety)]`
   - `[⚡ Bật tự động bắn (3s)]`
4. Quan sát:
   - AI lập tức phân loại câu hỏi và tạo gợi ý câu trả lời kèm nguồn trích dẫn.
   - Bấm nút **[🟢 Gửi ngay]** hoặc bấm phím tắt **`Alt + 1`**: Tin nhắn sẽ tự động được điền vào ô chat và gửi đi ngay lập tức!
   - Thử kéo thả cửa sổ bằng thanh tiêu đề hoặc bấm `_` để thu nhỏ.

---

## 🧩 Cách 2: Cài đặt Extension vào Chrome / Cốc Cốc / Edge

1. Mở trình duyệt Chrome và truy cập: `chrome://extensions/`
2. Bật công tắc **Chế độ dành cho nhà phát triển (Developer mode)** ở góc trên bên phải.
3. Bấm nút **Tải tiện ích đã giải nén (Load unpacked)**.
4. Chọn thư mục `extension/` trong repo này:
   ```text
   /home/Dx/dev/ai-agent-for-livestream/extension
   ```
5. Tiện ích **"Shopee Live AI Copilot - Trợ lý Livestream"** sẽ xuất hiện trên thanh công cụ.
6. Mở trang [Shopee Live PC](https://live.shopee.vn) hoặc mở trang [test_mock_shopee.html](file:///home/Dx/dev/ai-agent-for-livestream/extension/test_mock_shopee.html), bạn sẽ thấy Overlay AI tự động kích hoạt!

---

## 🔍 Cách 3: Kiểm tra Selector trên Shopee Live thật của Shop

Nếu bạn đang mở trang quản trị Shopee Live thật:
1. Bấm `F12` mở Chrome DevTools -> Chuyển sang tab **Console**.
2. Mở file [shopee_dom_inspector.js](file:///home/Dx/dev/ai-agent-for-livestream/extension/shopee_dom_inspector.js), copy toàn bộ nội dung và dán vào Console rồi bấm Enter.
3. Console sẽ quét và hiển thị chính xác các selector chat của shop bạn và tự động thử nghiệm điền text mẫu.
