# AI hỗ trợ phiên livestream bán hàng

Hệ thống AI Agent, Vector RAG, Auto-Order, Database Multi-Tenant và Chrome Extension cho đồ án tốt nghiệp **“Xây dựng AI Agent hỗ trợ phiên livestream bán hàng”**. Repository bao gồm:

- **Gói `ai` (`ai/`)**:
  - **Multi-Agent StateGraph Orchestrator (`ai.multi_agent`)**: Điều phối 5 tác tử chuyên biệt (`ModerationGuardAgent`, `IntentTriageAgent`, `ToolCallingRAGAgent`, `AnswerSynthesisReviewAgent`, `OperationsAnalyticsAgent`) tương thích `LangGraph` StateGraph kèm bộ thực thi đồ thị dự phòng.
  - **Hybrid Vector RAG (`ai.vector_rag`)**: Hỗ trợ `FAISS`, `ChromaDB` và vector cosine similarity nội bộ trên catalog sản phẩm, FAQ và chính sách bán hàng đang hiệu lực.
  - **Function / Tool Calling (`ai.agent_tools`)**: Định nghĩa schema và bộ thực thi công cụ (`search_catalog_products`, `check_product_inventory`, `lookup_order_status`, `get_shipping_and_return_policy`, `search_knowledge_base_rag`).
  - **Customer Q&A (`ai.customer_qa`), Operations (`ai.operations`) & Evaluation (`ai.evaluation`)**: Phân loại ý định, đối chiếu nguồn trích dẫn (`source_id`), kiểm duyệt an toàn, tổng hợp vận hành phiên live và bộ đánh giá định lượng (golden cases).
- **FastAPI Backend & Database Layer (`backend/`)**:
  - Kết nối cơ sở dữ liệu đa gian hàng **MongoDB (`livestream_sales_db` — 13 collections)** & **Redis (7 mẫu khóa: `Hash`, `List`, `Stream`, `Pub/Sub`, `String`)** qua `StoreDatabaseFacade`.
  - Khi chạy độc lập không bật Docker, backend tự động kích hoạt `EmbeddedMongoDatabaseManager` lưu bền vững xuống đĩa (`backend/database/.local_db/livestream_sales_db.json`) và `EmbeddedRedisManager`, bảo toàn toàn bộ bình luận, đơn hàng và số lượng tồn kho đã trừ qua các lần khởi động lại server.
  - Tích hợp mô hình phân loại ý định song ngữ Anh/Việt (`backend.local_model`), hỗ trợ Ollama Local LLM tùy chọn (`backend.ollama_provider`), giao diện **React 18 Operator Console Dashboard** (`/dashboard` có tích hợp bộ engine dự phòng offline không phụ thuộc CDN `unpkg.com`), và bộ kiểm thử End-to-End phiên livestream dài (`backend.e2e_verification`).
- **Frontend Dashboard (`frontend/`)**: Ứng dụng Next.js / React 18 cho người vận hành theo dõi bình luận thời gian thực, duyệt/xác nhận (`Confirm COD`) hoặc hủy đơn hàng (`Cancel Order` — tự động hoàn kho), kiểm thử Multi-Agent + Tool Calling, tra cứu Vector RAG và giám sát 13 MongoDB collections + 7 Redis key patterns.
- **YouTube Live Chrome Extension (`extension/`)**:
  - Thu thập bình luận thời gian thực từ YouTube Live với **hai công tắc độc lập** (`Enable Collector` và `Auto-Reply in Chat` — mặc định **TẮT** auto-reply), có thể bật/tắt trực tiếp trên **thanh điều khiển video YouTube**, trên **thanh công cụ đầu khung chat (`#livestream-ai-chat-toolbar`)** trong Popout Chat / YouTube Studio, hoặc trong **Extension Popup (`popup.html`)**.
  - Hiển thị thanh chip lọc bình luận đáng chú ý kèm trạng thái gửi chat (`✓ Sent`, `⚠ Failed`, `⛔ Restricted`, `⏳ Queued`) và hộp thoại chi tiết nội bộ (`Draft Ready` / `Needs Review` / `Order #ORD-LIVE-...`), nơi nút `Save Note` chỉ lưu ghi chú nội bộ vào `/thread`.

---

## Tính năng cốt lõi & Cơ chế an toàn

### 1. Hỏi đáp khách hàng (Customer Q&A) song ngữ Anh/Việt & Đối chiếu tồn kho sống
- Tạo bản nháp câu trả lời (`CustomerAnswerDraft` với `is_draft=True`) dựa trên catalog sản phẩm, **tồn kho thời gian thực trong DB** (`_get_live_stock`), FAQ và chính sách bán hàng kèm mã nguồn trích dẫn (`source_id`).
- Hỗ trợ đầy đủ câu hỏi tiếng Việt (có dấu & không dấu) và tiếng Anh về **giá bán**, **tồn kho thực tế sau khi trừ đơn**, **phí ship COD / Freeship đơn từ \$30.00**, **thời gian giao hàng (3 ngày làm việc)** và **chính sách đổi trả (14 ngày)**.
- Tự động nhận diện và bỏ qua các bình luận khen ngợi/cảm thán thuần túy (ví dụ: *"mình thấy đèn bàn đẹp quá"*, *"quạt nhìn xịn ghê"*) để không spam mô tả sản phẩm vào khung chat.
- Chuyển các câu hỏi thiếu bằng chứng, mâu thuẫn dữ liệu hoặc yêu cầu thao tác nhạy cảm (hoàn tiền, đổi giá, khiếu nại) sang trạng thái `needs_review` để người vận hành xử lý.

### 2. Tự động lên đơn hàng (Auto-Order) & Quản lý tồn kho an toàn
- **Hỗ trợ tiếng Việt có dấu, không dấu & tiếng Anh tự nhiên**: Nhận diện chính xác các mẫu chốt đơn phổ biến trên livestream như `"chot 1 den ban"`, `"dat 1 coc su"`, `"lay 1 quat cam tay"`, `"mua 1 binh giu nhiet"`, `"cho em 1 den ban"`, `"chốt 1 cái đèn bàn?"`, `"mua 2 đèn bàn và 3 cốc sứ"`, `"buy 2 LumaNest Desk Lamp"`.
- **Phân tích số lượng & đơn vị thông minh**:
  - Hỗ trợ hậu tố/tiền tố số lượng (`2x`, `x2`, `3c`, `3 cái`, `2 chiếc`) và số từ tiếng Việt/Anh (`một`/`mot`..`mười`/`muoi`, `one`..`ten`).
  - Tự động tách bỏ phần địa chỉ (`địa chỉ 123 Nguyễn Huệ`, `giao tới 15 Lê Lợi`), số quận/phường (`ở quận 3`, `Q1`), SĐT, mã sản phẩm (`SYN-PROD-007`) và thông số kỹ thuật (`65W`) trước khi trích xuất số lượng để không bao giờ nhầm số nhà/mã SP thành số lượng đặt.
  - Từ chối rõ ràng các số lượng ngoài khoảng `1..20` (như `0`, `-2`, `25`, `200`) và chuyển sang `needs_review` thay vì ép ngầm về `1`.
- **Lọc chặt False Positive**: Loại bỏ câu phủ định/trì hoãn (`"don't buy..."`, `"đừng mua..."`, `"chưa muốn mua..."`, `"I want to buy something later"`), câu kể quá khứ/hỏi đơn cũ (`"đã mua tuần trước"`, `"where is my order"`), và từ đa nghĩa tiếng Anh (`"I am a huge fan, give me a shoutout!"`, `"my son loves this livestream"`).
- **Kiểm tra tồn kho thực tế & Khóa đồng bộ (`asyncio.Lock`)**:
  - Bao bọc toàn bộ quá trình kiểm tra tồn kho $\rightarrow$ sinh mã `_next_order_id` từ DB $\rightarrow$ tạo/cập nhật đơn $\rightarrow$ trừ kho trong `async with order_lock:` để chống race condition khi nhiều bình luận chốt đơn đến cùng lúc.
  - Từ chối lên đơn khi sản phẩm đã hết hàng (`stock = 0`, ví dụ `SYN-PROD-004`) hoặc đặt vượt số lượng tồn kho hiện có (`qty > stock`).
  - Bảo toàn tồn kho đã trừ trong DB qua các lần restart server (`seed_demo_store_data` chỉ khởi tạo tồn kho cho sản phẩm chưa có trong DB).
- **Trích xuất SĐT, Địa chỉ & Bổ sung thông tin cho đơn `pending`**:
  - Hỗ trợ SĐT có dấu chấm/khoảng trắng/gạch ngang (`0912.345.678`, `0912 345 678`) và địa chỉ có dấu phẩy (`"địa chỉ: 123 Nguyễn Huệ, Quận 1, TP HCM"`).
  - Bình luận chốt đơn chưa có SĐT & địa chỉ sẽ tạo đơn ở trạng thái `status="pending"`, `payment_status="unpaid"`. Khi cùng viewer nhắn tiếp bình luận bổ sung SĐT/địa chỉ (hoặc nhắn lại câu chốt kèm SĐT/địa chỉ), hệ thống tự động gộp thông tin giao hàng vào đơn `pending` trước đó và chuyển sang `status="confirmed"`, `payment_status="cod"`.
  - Hỗ trợ xác nhận đơn (`Confirm COD`) hoặc hủy đơn & hoàn kho (`Cancel Order`) trực tiếp trên Dashboard (`POST / PATCH /api/store/orders/{order_id}/status`).

### 3. Chống vòng lặp tự trả lời (Anti-Echo Loop) & Hàng đợi gửi chat tuần tự
- **Chống Infinite Echo Loop 3 lớp**:
  1. Hàm `extractCommentText` trong `extension/src/content/content.js` đọc cả thuộc tính `alt` của thẻ `<img class="emoji" alt="🤖">` do YouTube Live render (khắc phục việc `.innerText` làm mất emoji `🤖`).
  2. So khớp cả văn bản gốc lẫn chữ ký đã chuẩn hóa (`sentAiSignatures`), đồng thời bỏ qua toàn bộ bình luận từ chủ kênh/quản trị viên (`authorType === 'owner' | 'moderator'`).
  3. Backend (`_is_bot_echo_text`) chặn mọi văn bản mang mẫu phản hồi của bot hoặc bình luận từ `owner`/`moderator`, đảm bảo không bao giờ tự trả lời lại hoặc đọc mã đơn `#ORD-LIVE-0006` thành đặt 6 sản phẩm.
- **Hàng đợi gửi tin an toàn (`chatSendQueue`)**:
  - Sử dụng khóa mutex `isProcessingChatQueue`, khoảng nghỉ `CHAT_SEND_COOLDOWN_MS = 1800ms`, phát sự kiện `InputEvent` với `composed: true` cho Web Component của YouTube, bảo toàn chữ streamer đang gõ dở, cắt câu theo ranh giới từ (`truncateAtWordBoundary`, tối đa 195 ký tự) và polling kiểm tra kết quả gửi trong `~1.32s` để hiển thị trạng thái `Chat: Sent ✓` / `Chat: Send Failed ⚠` / `Chat: Restricted ⛔` ngay trên chip và popover.

---

## Cấu trúc thư mục

```text
ai/
  multi_agent.py        # Multi-Agent StateGraph orchestrator (5 specialized agents)
  vector_rag.py         # Hybrid Vector RAG (FAISS / ChromaDB / cosine fallback)
  agent_tools.py        # Function-calling tool definitions and dispatcher
  customer_qa/          # schemas, intent, safety, retrieval, answer service
  data/                 # models, loading, access, search, synthetic JSON (products, faqs, policies, orders)
  evaluation/           # scenarios.json, runner.py, metrics.py
  operations/           # schemas, classification, aggregation, summary
backend/
  main.py               # FastAPI backend wired to StoreDatabaseFacade, Auto-Order, Vietnamese Q&A & Order API
  dashboard.html        # React 18 Operator Console served at GET /dashboard (with built-in offline engine)
  local_model.py        # Train/evaluate local TF-IDF + LogisticRegression bilingual intent classifier
  ollama_provider.py    # Optional local Ollama client for grounded synthesis & tool calling
  e2e_verification.py   # End-to-end & 300-comment long-session stability verification runner
  database/             # MongoDB/Redis managers, embedded disk-persistent engine, Pydantic v2 models, seed, facade
  repositories/         # Multi-tenant repositories (tenant, catalog, livestream, orders)
  infrastructure/       # Redis operational cache, key conventions, Redis Streams
frontend/               # Next.js / React 18 Operator Dashboard application
extension/              # Chrome Extension YouTube Live (Watch page, Popout Chat, Studio toolbar & Popup controls)
tests/                  # Automated end-to-end, Auto-Order, Vietnamese Q&A & disk-persistence test suite
docs/                   # Kiến trúc, hợp đồng tích hợp, hướng dẫn DB và các báo cáo đánh giá/kiểm thử
reports/                # JSON evaluation & E2E verification reports
docker-compose.yml      # MongoDB 7.0 & Redis 7.2 local infrastructure
.env.example            # Cấu hình môi trường mẫu
AI_HANDOFF_REPORT.md    # Báo cáo bàn giao chi tiết
pyproject.toml
```

---

## Môi trường và chạy kiểm chứng trên Windows

Yêu cầu Python **3.11 trở lên**. Cài đặt dependencies cho backend và công cụ kiểm thử:

```powershell
python -m pip install -e ".[server]"
```

Chạy toàn bộ các lệnh biên dịch, đánh giá mô hình và kiểm thử End-to-End tại thư mục gốc repository:

```powershell
python -m compileall -q ai backend tests
python -m ai.evaluation.runner
python -m backend.local_model
python -m backend.e2e_verification
python -m unittest discover -s tests -v
```

- `python -m ai.evaluation.runner`: Chạy bộ golden cases và cập nhật `reports/evaluation_report.json` cùng `docs/EVALUATION_REPORT.md`.
- `python -m backend.local_model`: Huấn luyện và đánh giá mô hình phân loại ý định trên tập holdout độc lập, xuất `reports/local_intent_model_report.json` và `docs/LOCAL_MODEL_EVALUATION.md`.
- `python -m backend.e2e_verification`: Chạy kiểm thử toàn trình (End-to-End) và phiên livestream dài (300 bình luận liên tục + 25 bản tin trùng lặp DOM + kiểm tra đọc/ghi MongoDB Repository + Multi-Agent/RAG + kiểm tra hợp đồng an toàn Extension), xuất `reports/e2e_verification_report.json` và `docs/E2E_VERIFICATION_REPORT.md`.
- `python -m unittest discover -s tests -v`: Chạy toàn bộ 10 bộ kiểm thử tự động trong `tests/test_e2e_livestream_pipeline.py` (bao gồm kiểm tra tồn kho, tiếng Việt không dấu, phân biệt số nhà/mã SP/số lượng `2x`/`3c`, gộp SĐT/địa chỉ bổ sung vào đơn `pending`, chống Echo Loop, chặn spam câu khen, lưu trữ đĩa & giữ tồn kho khi restart, và API duyệt/hủy đơn).

---

## Khởi động Hạ tầng Database, Backend và Dashboard

### 1. Khởi động MongoDB & Redis (Tùy chọn)

```powershell
docker compose up -d
```

Khi khởi động, `backend.main` tự động kết nối MongoDB/Redis, khởi tạo schema validators/indexes và nạp dữ liệu mẫu (`seed_demo_store_data` — bảo toàn tồn kho và đơn hàng đã có). Nếu không chạy Docker, backend tự động sử dụng `EmbeddedMongoDatabaseManager` lưu bền vững vào `backend/database/.local_db/livestream_sales_db.json`.

### 2. Chạy FastAPI Backend & Operator Console

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

- Mở trình duyệt truy cập **`http://127.0.0.1:8000/dashboard`** để sử dụng **Livestream Sales Multi-Agent & RAG Console** (hỗ trợ duyệt đơn **`Confirm COD`**, hủy đơn & hoàn kho **`Cancel Order`**, kiểm thử Multi-Agent, Vector RAG và giám sát 13 MongoDB collections + 7 Redis key patterns; hoạt động cả khi offline).
- (Tùy chọn) Chạy thêm ứng dụng Next.js tại thư mục `frontend/`:
  ```powershell
  cd frontend
  npm install
  npm run dev
  ```

### 3. Cấu hình Ollama Local LLM (Tùy chọn)

Mặc định hệ thống chạy hoàn toàn độc lập không cần API key hay LLM. Nếu muốn sử dụng Ollama cục bộ để diễn đạt bản nháp và gọi hàm (Tool Calling):

```env
OLLAMA_ENABLED=false
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:3b-instruct
OLLAMA_TIMEOUT_SECONDS=30
```

---

## Cài đặt và sử dụng Extension YouTube Live

1. Mở trình duyệt Chrome / Brave / Edge, truy cập `chrome://extensions/`.
2. Bật **Developer mode** (Chế độ cho nhà phát triển) ở góc trên bên phải.
3. Nhấn **Load unpacked** (Tải tiện ích đã giải nén) và chọn thư mục `extension/` trong dự án.
4. Mở một video YouTube Live, cửa sổ **Popout Chat (`/live_chat`)** hoặc **YouTube Studio Live Control Room**:
   - Bật **`Enable Collector`** và tùy chọn bật/tắt **`Auto-Reply in Chat`** (mặc định **TẮT**) thông qua một trong 3 vị trí:
     1. Nút biểu tượng **AI** trên thanh điều khiển trình phát video YouTube.
     2. Thanh công cụ **`AI Live` (`Collector: ON/OFF` | `Auto-Reply: ON/OFF` | `Dashboard ↗`)** gắn trực tiếp ở đầu khung chat (hỗ trợ cả Popout Chat và YouTube Studio).
     3. Giao diện **Popup của Extension** trên thanh công cụ trình duyệt.
   - Khi khán giả bình luận, extension trích xuất bình luận và gửi về `POST /api/plugin/comments`.
   - Các bình luận có ý định mua hàng, lên đơn tự động hoặc có bản nháp câu trả lời sẽ xuất hiện dưới dạng **thanh chip ngang** phía trên khung chat kèm biểu tượng trạng thái gửi (`✓`, `⚠`, `⛔`, `⏳`).
   - Nhấn vào chip để mở hộp thoại nội bộ xem nguồn trích dẫn (`sources`), mã đơn hàng **`Order #ORD-LIVE-...`**, trạng thái **`Draft Ready`** / **`Needs Review`** / **`Chat: Sent ✓`**, và lưu ghi chú nội bộ (`Save Note` — chỉ lưu vào `/thread`, không gửi ra chat công khai).

---

## Tài liệu chi tiết

- [Kiến trúc AI, Backend & Extension](docs/AI_ARCHITECTURE.md)
- [Hợp đồng tích hợp API & Runtime](docs/AI_INTEGRATION_CONTRACT.md)
- [Hướng dẫn tích hợp Database & Redis Multi-Tenant](docs/DATABASE_INTEGRATION_GUIDE.md)
- [Kiến trúc Extension YouTube Live](docs/YOUTUBE_LIVE_EXTENSION_PLAN.md)
- [Báo cáo kiểm thử End-to-End & Phiên Livestream dài](docs/E2E_VERIFICATION_REPORT.md)
- [Báo cáo đánh giá Golden Cases](docs/EVALUATION_REPORT.md)
- [Báo cáo đánh giá Local Intent Model](docs/LOCAL_MODEL_EVALUATION.md)
- [Báo cáo bàn giao chi tiết](AI_HANDOFF_REPORT.md)
