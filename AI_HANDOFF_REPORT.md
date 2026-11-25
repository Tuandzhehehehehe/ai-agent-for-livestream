# Báo cáo bàn giao AI, Backend, Database & Extension

## 1. Trạng thái bàn giao thực tế

Workspace hiện bao gồm đầy đủ 4 thành phần đã tích hợp đồng bộ:
1. **Package `ai` (Multi-Agent, Vector RAG, Tool Calling, Customer Q&A, Operations, Evaluation)**:
   - `ai.multi_agent`: Hệ thống điều phối đa tác tử (`MultiAgentOrchestrator` tương thích `LangGraph` StateGraph kèm bộ thực thi đồ thị dự phòng) gồm 5 tác tử: `ModerationGuardAgent`, `IntentTriageAgent`, `ToolCallingRAGAgent`, `AnswerSynthesisReviewAgent` và `OperationsAnalyticsAgent`.
   - `ai.vector_rag`: Kho truy xuất ngữ nghĩa Hybrid Vector RAG (`FAISS`, `ChromaDB` và cosine similarity nội bộ) trên catalog sản phẩm, FAQ và chính sách bán hàng đang hiệu lực.
   - `ai.agent_tools`: Bộ công cụ Function Calling (`search_catalog_products`, `check_product_inventory`, `lookup_order_status`, `get_shipping_and_return_policy`, `search_knowledge_base_rag`).
   - `ai.customer_qa` & `ai.operations`: Phân loại ý định theo tập luật, truy xuất bằng chứng có mã nguồn (`source_id`), tạo bản nháp câu trả lời (`CustomerAnswerDraft` với `is_draft=True`), cổng kiểm duyệt an toàn và tổng hợp thống kê vận hành phiên livestream.
2. **FastAPI Backend & Database Layer (`backend/`)**:
   - `backend.main` được nối trực tiếp với tầng cơ sở dữ liệu đa gian hàng **MongoDB (`livestream_sales_db` gồm đủ 13 collections)** & **Redis (đủ 7 cấu trúc dữ liệu `Hash`, `List`, `Stream`, `Pub/Sub`, `String`)** thông qua `StoreDatabaseFacade` (`backend.database`, `backend.repositories`, `backend.infrastructure`, `backend.database.embedded_engine`) cho cả chiều **ghi** và chiều **đọc**:
     - `POST /api/plugin/comments`: Nhận bình luận, kiểm tra rate-limit/moderation, phân loại intent (hỗ trợ đầy đủ Q&A tiếng Việt về giá, tồn kho thời gian thực, phí ship COD/freeship, thời gian giao hàng, đổi trả), chấm điểm tiềm năng mua hàng (`lead_score`, `lead_label`), nhận diện yêu cầu mua hàng hợp lệ để **Auto Lên Đơn Hàng (`auto_order`)** (kiểm tra tồn kho thực tế `stock > 0` và `qty <= stock`, hỗ trợ chốt nhiều sản phẩm trong 1 câu và câu có dấu `?`, lọc câu phủ định/quá khứ/hỏi đơn, từ chối số lượng ngoài `1..20`, chống trùng đơn liên tiếp từ cùng viewer, đặt trạng thái `pending`/`unpaid` khi chưa có SĐT+địa chỉ và `confirmed`/`cod` khi đã có SĐT+địa chỉ, sinh `order_id` chống đè từ DB), sinh bản nháp câu trả lời và lưu vào MongoDB (`CustomerRepository`, `LivestreamCommentRepository`, `AnswerDraftRepository`, `OrderRepository`, kèm lưu bền vững xuống đĩa `backend/database/.local_db/livestream_sales_db.json` khi chạy Embedded DB), cập nhật `LivestreamSessionRepository.increment_metrics` và đồng bộ Redis.
     - `GET /api/plugin/comments` & `GET /api/plugin/summary`: Đọc danh sách bình luận, bản nháp, thông tin `auto_order` (khôi phục từ `OrderRepository` qua tag `[comment_id:...]` ngay cả sau khi restart server) và thống kê trực tiếp từ MongoDB Repository (`_load_comments_from_repository`) kèm trường `storage_mode="mongodb"`.
     - `POST /api/plugin/comments/{comment_id}/thread`: Lưu trao đổi/ghi chú nội bộ của Moderator trong bộ nhớ runtime (`threads_by_comment`) và cập nhật trạng thái duyệt nháp (`record_seller_action`: `approved` hoặc `edited`) trong MongoDB.
     - `GET /api/store/products`, `GET /api/store/orders`, `POST / PATCH /api/store/orders/{order_id}/status`, `POST /api/store/seed`: Đọc/ghi sản phẩm, biến thể, tồn kho (bảo toàn tồn kho đã trừ khi restart server) và quản lý trạng thái đơn hàng livestream (`Confirm COD` / `Cancel Order` kèm hoàn kho tự động).
     - `GET /api/database/overview`: Trả về cấu trúc chi tiết và số lượng bản ghi thời gian thực của toàn bộ **13 MongoDB collections** trong `livestream_sales_db` cùng **7 mẫu khóa Redis**.
   - `backend.local_model`: Mô hình TF-IDF character n-gram + Logistic Regression huấn luyện từ 260 mẫu song ngữ Anh/Việt và đánh giá trên 65 mẫu holdout độc lập.
3. **Giao diện Operator Dashboard (`backend/dashboard.html` & `frontend/`)**:
   - Giao diện bảng điều khiển phục vụ trực tiếp tại `GET /dashboard` (tích hợp bộ engine dự phòng offline không phụ thuộc CDN `unpkg.com`, và ứng dụng Next.js tại `frontend/`) cho phép theo dõi bình luận thời gian thực, xác nhận/hủy đơn hàng Auto-Order (`Confirm COD` / `Cancel Order`), kiểm thử Multi-Agent + Tool Calling, tìm kiếm Vector RAG, xem danh mục sản phẩm/tồn kho và giám sát trực tiếp 13 MongoDB Collections + 7 Redis Key Patterns.
4. **Chrome Extension YouTube Live (`extension/`)**:
   - Thu thập bình luận thời gian thực từ YouTube Live, cung cấp 2 công tắc tách biệt (**`Enable Collector`** và **`Auto-Reply in Chat`** — mặc định tắt auto-reply) trên cả trình phát video, thanh công cụ đầu khung chat (`#livestream-ai-chat-toolbar` cho Popout Chat / YouTube Studio) và trong Extension Popup (`popup.js`). Hiển thị thanh chip lọc bình luận quan trọng và hộp thoại chi tiết với nhãn **`Draft Ready`**, **`Needs Review`**, **`Order #ORD-LIVE-...`** và trạng thái gửi chat **`Chat: Sent ✓ / Send Failed ⚠ / Restricted ⛔`**. Nút **`Save Note`** chỉ lưu ghi chú nội bộ vào `/thread`; cơ chế chống **Infinite Echo Loop 3 lớp** (đọc thẻ `<img alt="🤖">`, đối chiếu chữ ký chuẩn hóa `sentAiSignatures`, bỏ qua `owner`/`moderator`) ngăn chặn hoàn toàn việc bot tự đọc lại tin nhắn của chính mình.

---

## 2. Kết quả xác minh & Kiểm thử thực tế

Các lệnh kiểm chứng đã chạy thành công trên workspace:

| Lệnh | Kết quả thực tế |
| --- | --- |
| `python -m compileall -q ai backend tests` | Hoàn tất thành công; exit code 0 |
| `python -m ai.evaluation.runner` | Hoàn tất thành công; xuất `reports/evaluation_report.json` và `docs/EVALUATION_REPORT.md` |
| `python -m backend.local_model` | Huấn luyện 260 mẫu, đo holdout 65 mẫu; xuất `reports/local_intent_model_report.json` và `docs/LOCAL_MODEL_EVALUATION.md` |
| `python -m backend.e2e_verification` | Kiểm thử End-to-End & phiên livestream dài 300 bình luận liên tục; xuất `reports/e2e_verification_report.json` và `docs/E2E_VERIFICATION_REPORT.md` |
| `python -m unittest discover -s tests -v` | Toàn bộ 10 kịch bản kiểm thử E2E, Auto-Order (tồn kho, tiếng Việt không dấu, phân biệt số nhà/mã SP/số lượng `2x`/`3c`, gộp SĐT/địa chỉ bổ sung, chống trùng đơn, lọc false-positive), chống Echo Loop, chặn spam câu khen, Q&A tiếng Việt, lưu trữ đĩa & giữ tồn kho qua restart, API duyệt/hủy đơn đều PASS (`OK`) |

### 2.1. Kết quả Kiểm thử End-to-End & Phiên Livestream Dài (`docs/E2E_VERIFICATION_REPORT.md`)

| Chỉ số kiểm chứng | Kết quả đo được |
| --- | ---: |
| Cơ sở dữ liệu MongoDB (`database_name`) | `livestream_sales_db` (`13 / 13` collections) |
| Cấu trúc dữ liệu Redis (`total_redis_key_patterns`) | `7 / 7` (`Hash`, `List`, `Stream`, `Pub/Sub`, `String`) |
| Số bình luận duy nhất gửi liên tục (`POST /api/plugin/comments`) | 300 / 300 (HTTP 200) |
| Số bản tin gửi lặp ID (`DOM re-render deduplication`) | 25 |
| Số bình luận lưu trong MongoDB Repository (`LivestreamCommentRepository`) | 300 |
| Số bình luận đọc qua `GET /api/plugin/comments` (`storage_mode="mongodb"`) | 300 |
| Số bình luận khôi phục khi khởi động lại (`Cold-Start Repository Read`) | 300 |
| Số bình luận bị mất (`Lost comments`) | **0** |
| Số bản ghi bị nhân đôi (`Duplicate records`) | **0** |
| Đơn hàng tự động lên đơn trong phiên (`auto_orders_created_in_session`) | **9** đơn (đặt `2x Aurora Studio Ringlight` mỗi đơn cho tới khi hết sạch tồn kho `18 -> 0`; các yêu cầu đặt tiếp khi `stock = 0` bị từ chối an toàn) |
| Độ trễ xử lý trung bình (`Mean latency / p50 / p95 / p99`) | `3.65 ms / 3.51 ms / 8.53 ms / 10.97 ms` |
| Kiểm thử Burst Rate Limit (6 bình luận liên tiếp / 1 viewer) | `[200, 200, 200, 200, 200, 429]` |
| Cập nhật trạng thái duyệt nháp qua thread (`record_seller_action`) | `pending` $\rightarrow$ `approved` / `edited` |
| Số lần xuất hiện nhãn `Auto Replied` trong Extension | **0** (đã thay bằng `Draft Ready`) |

### 2.2. Kết quả Golden-Case Evaluation (`docs/EVALUATION_REPORT.md`)

Evaluation gồm 19 customer Q&A cases, 13 comment-classification cases và 6 operations cases:

| Metric | Kết quả đo được |
| --- | ---: |
| Intent classification accuracy | 32 / 32 |
| Customer Q&A outcome accuracy | 19 / 19 |
| Human-review escalation recall | 20 / 20 |
| Unsupported-answer rate | 0 / 11 |
| Source-reference coverage cho Q&A được trả lời | 5 / 5 |
| Operations case accuracy | 6 / 6 |
| Duplicate-handling correctness | 2 / 2 |
| Operations recommendation source traceability | 7 / 7 |
| Operations summary source traceability | 12 / 12 |

### 2.3. Kết quả Local Intent Model (`docs/LOCAL_MODEL_EVALUATION.md`)

- Corpus train: `backend/intent_training_data.json` (260 mẫu synthetic song ngữ Anh/Việt; 20 mẫu mỗi intent).
- Holdout: `backend/intent_holdout_data.json` (65 mẫu synthetic độc lập; 5 mẫu mỗi intent).
- Kết quả trên holdout: accuracy `75.4%`, macro-F1 `77.7%`, answerable coverage `76.7%`, selective accuracy `95.7%`.

---

## 3. Tóm tắt Public API & Routes

### 3.1. Python API (`ai.*`)
- **Multi-Agent & Tool Calling**:
  - `ai.multi_agent.MultiAgentOrchestrator`, `compute_lead_score(text, intent)`
  - `ai.vector_rag.VectorRAGStore`, `get_vector_rag_store(dataset=None)`
  - `ai.agent_tools.get_tool_definitions()`, `execute_agent_tool(tool_name, ...)`
- **Customer Q&A**:
  - `CustomerQuestionRequest`, `CustomerAnswerDraft`, `classify_intent`, `retrieve_evidence`, `draft_customer_answer`
- **Operations**:
  - `SimulatedComment`, `SimulatedEvent`, `classify_comment`, `summarize_session`
- **Data**:
  - `load_synthetic_data`, `load_products`, `load_faqs`, `load_policies`, `get_effective_policies`, `search_records`

### 3.2. HTTP Backend Routes (`backend.main`)
- `GET /dashboard` (và `GET /`): Giao diện Operator Console Dashboard.
- `GET /health`: Trạng thái AI model, Ollama, MongoDB, Redis, Multi-Agent orchestrator và Vector RAG.
- `POST /api/plugin/comments`: Nhận bình luận từ Extension, phân loại, tạo bản nháp và lưu vào MongoDB/Redis (hoặc bộ nhớ fallback).
- `GET /api/plugin/comments`: Đọc danh sách bình luận và bản nháp từ MongoDB Repository (hoặc bộ nhớ fallback).
- `POST /api/plugin/comments/{comment_id}/thread`: Lưu ghi chú/phê duyệt bản nháp nội bộ và cập nhật `seller_action` trong MongoDB.
- `GET /api/plugin/summary`: Thống kê phiên livestream kèm `storage_mode` (`mongodb` hoặc `in_memory`).
- `GET /api/agent/architecture`, `GET /api/agent/tools`, `POST /api/agent/chat`, `POST /api/agent/rag/search`: Các route Multi-Agent và Vector RAG.
- `GET /api/store/products`, `GET /api/store/orders`, `POST / PATCH /api/store/orders/{order_id}/status`, `POST /api/store/seed`: Các route truy vấn sản phẩm, tồn kho, danh sách đơn hàng và cập nhật trạng thái xác nhận/hủy đơn từ MongoDB Repository.
- `GET /api/database/overview`: Trả về cấu trúc chi tiết và số lượng bản ghi của 13 MongoDB collections và 7 mẫu khóa Redis.

---

## 4. Lưu ý vận hành & Giới hạn an toàn

- `pyproject.toml` đã khai báo đầy đủ package data cho cả `ai` (`data/synthetic/*.json`, `evaluation/*.json`) và `backend` (`*.json`, `*.html`).
- Mọi câu trả lời do hệ thống tạo ra đều có `is_draft=True` và trạng thái khởi tạo `seller_action="pending"`. Hệ thống không tự động gửi tin nhắn ra khung chat công khai của YouTube và không tự động thực hiện hoàn tiền, sửa giá hay chốt đơn.
- Các route API hiện hướng tới môi trường vận hành nội bộ (`127.0.0.1:8000`); nếu triển khai công khai qua mạng cần bổ sung lớp xác thực HTTP/JWT kết hợp với `StoreDatabaseFacade.authorize_tenant(...)`.

## 5. Tài liệu liên quan

- [README dự án](README.md)
- [Kiến trúc AI, Backend & Extension](docs/AI_ARCHITECTURE.md)
- [Hợp đồng tích hợp API & Runtime](docs/AI_INTEGRATION_CONTRACT.md)
- [Hướng dẫn tích hợp Database & Redis Multi-Tenant](docs/DATABASE_INTEGRATION_GUIDE.md)
- [Kiến trúc Extension YouTube Live](docs/YOUTUBE_LIVE_EXTENSION_PLAN.md)
- [Báo cáo kiểm thử End-to-End & Phiên Livestream dài](docs/E2E_VERIFICATION_REPORT.md)
- [Báo cáo đánh giá Golden Cases](docs/EVALUATION_REPORT.md)
- [Báo cáo đánh giá Local Intent Model](docs/LOCAL_MODEL_EVALUATION.md)
