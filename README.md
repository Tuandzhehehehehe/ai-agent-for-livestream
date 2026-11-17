# AI hỗ trợ phiên livestream bán hàng

Hệ thống AI Agent, RAG và Data cho đồ án tốt nghiệp **“Xây dựng AI Agent hỗ trợ phiên livestream bán hàng”**. Repository bao gồm:
- **Gói `ai`**: Multi-Agent StateGraph Orchestrator (`ai.multi_agent`), Hybrid Vector RAG (`ai.vector_rag` hỗ trợ `FAISS`, `ChromaDB` và cosine embeddings nội bộ), Function/Tool Calling (`ai.agent_tools`), Customer Q&A có đối chiếu nguồn (`ai.customer_qa`), phân tích vận hành phiên live (`ai.operations`) và bộ đánh giá định lượng (`ai.evaluation`).
- **FastAPI Backend (`backend/`)**: Kết nối cơ sở dữ liệu đa gian hàng **MongoDB & Redis** thông qua `StoreDatabaseFacade` (tự động fallback về bộ nhớ đệm `recent_comments` khi chạy độc lập không có Docker/DB), tích hợp mô hình phân loại ý định huấn luyện cục bộ (`backend.local_model`), hỗ trợ Ollama LLM cục bộ tùy chọn (`backend.ollama_provider`), giao diện **Operator Console Dashboard** (`/dashboard`), và bộ kiểm thử End-to-End phiên livestream dài (`backend.e2e_verification`).
- **Frontend Dashboard (`frontend/`)**: Ứng dụng Next.js / React cho người vận hành theo dõi luồng bình luận thời gian thực, kiểm thử Multi-Agent + Tool Calling, tra cứu Vector RAG, quản lý sản phẩm/tồn kho và đơn hàng livestream.
- **YouTube Live Chrome Extension (`extension/`)**: Thu thập bình luận thời gian thực ở chế độ **chỉ đọc đối với khung chat công khai (Read-only Public Chat)**, cung cấp nút bật/tắt AI trên trình phát YouTube, thanh chip lọc bình luận đáng chú ý và hộp thoại nháp nội bộ (`Draft Ready` / `Needs Review`) cho Moderator.

## Mục tiêu và phạm vi an toàn

- Tạo bản nháp câu trả lời (`CustomerAnswerDraft` với `is_draft=True`) dựa trên catalog sản phẩm, FAQ và chính sách bán hàng kèm mã nguồn trích dẫn (`source_id`).
- Chuyển các câu hỏi thiếu bằng chứng, mâu thuẫn dữ liệu, vượt phạm vi hoặc yêu cầu thao tác quan trọng (hoàn tiền, đổi giá, chốt/hủy đơn) sang trạng thái `needs_review` để người vận hành xử lý.
- Phân loại bình luận, chấm điểm mức độ tiềm năng mua hàng (`lead_score`, `lead_label`: `hot`, `warm`, `cold`), tổng hợp câu hỏi lặp và sự kiện phiên livestream.
- Lưu trữ và đồng bộ bình luận, bản nháp (`seller_action`: `pending`, `approved`, `edited`), thống kê phiên, sản phẩm/tồn kho và đơn hàng qua **MongoDB & Redis Repository** (`StoreDatabaseFacade`).
- **Nguyên tắc an toàn cốt lõi**: Mọi câu trả lời do AI sinh ra đều là **bản nháp (`Draft Ready`)**. Extension và Backend **tuyệt đối không tự động gõ hay gửi tin nhắn vào khung chat công khai của YouTube Live** và không tự động thực thi giao dịch thanh toán/hoàn tiền.

## Kiến trúc mô-đun

- `ai.multi_agent`: Điều phối 5 tác tử chuyên biệt trên đồ thị trạng thái (`LangGraph` StateGraph hoặc bộ thực thi đồ thị tích hợp sẵn): `ModerationGuardAgent`, `IntentTriageAgent`, `ToolCallingRAGAgent`, `AnswerSynthesisReviewAgent` và `OperationsAnalyticsAgent`.
- `ai.vector_rag`: Kho truy xuất ngữ nghĩa Hybrid Vector RAG hỗ trợ `FAISS`, `ChromaDB` và vector cosine similarity nội bộ trên sản phẩm, FAQ và chính sách đang hiệu lực.
- `ai.agent_tools`: Định nghĩa schema và thực thi Function Calling / Tool Calling (`search_catalog_products`, `check_product_inventory`, `lookup_Order_status`, `get_shipping_and_return_policy`, `search_knowledge_base_rag`).
- `ai.customer_qa`: `classify_intent`, `retrieve_evidence`, `draft_customer_answer`; trả `CustomerAnswerDraft` với trạng thái `answered` hoặc `needs_review`.
- `ai.data`: Schema dữ liệu, kiểm tra tính hợp lệ, truy vấn catalog, FAQ, chính sách bán hàng và đơn hàng mô phỏng.
- `ai.operations`: `SimulatedComment`, `SimulatedEvent`, phân loại bình luận, gom nhóm câu hỏi/vấn đề lặp và tóm tắt phiên (`summarize_session`).
- `ai.evaluation`: Bộ kịch bản kiểm thử chuẩn (golden cases) và báo cáo định lượng.
- `backend.local_model`: Mô hình TF-IDF character n-gram + Logistic Regression huấn luyện cục bộ trên tập dữ liệu song ngữ Anh/Việt, đóng vai trò fallback khi bộ luật từ khóa trả về `unknown`.
- `backend.main`: FastAPI server cung cấp các route nhận/đọc bình luận (`/api/plugin/comments`), luồng duyệt/ghi chú nháp nội bộ (`/api/plugin/comments/{id}/thread`), thống kê phiên (`/api/plugin/summary`), Multi-Agent & Vector RAG (`/api/agent/*`), quản lý cửa hàng/đơn hàng (`/api/store/*`) và giao diện `/dashboard`.
- `backend.database`, `backend.repositories`, `backend.infrastructure`: Lớp cơ sở dữ liệu đa gian hàng (Multi-tenant MongoDB + Redis) được nối trực tiếp vào các route của `backend.main` thông qua `StoreDatabaseFacade` cho cả chiều ghi và chiều đọc, kèm fallback `recent_comments` trong bộ nhớ khi chạy không có DB.
- `backend.e2e_verification` & `tests/`: Bộ kiểm thử End-to-End và kiểm thử chịu tải phiên livestream dài (300+ bình luận liên tục, kiểm chứng 0% mất bình luận, chống trùng lặp ID, kiểm tra đọc/ghi Repository và nhãn giao diện).

## Cấu trúc thư mục

```text
ai/
  multi_agent.py     # Multi-Agent StateGraph orchestrator (5 specialized agents)
  vector_rag.py      # Hybrid Vector RAG (FAISS / ChromaDB / cosine fallback)
  agent_tools.py     # Function-calling tool definitions and dispatcher
  customer_qa/       # schemas, intent, safety, retrieval, answer service
  data/              # models, loading, access, search, synthetic JSON (products, faqs, policies, orders)
  evaluation/        # scenarios.json, runner.py, metrics.py
  operations/        # schemas, classification, aggregation, summary
backend/
  main.py            # FastAPI backend wired to StoreDatabaseFacade + in-memory fallback
  dashboard.html     # Built-in React Operator Console served at GET /dashboard
  local_model.py     # Train/evaluate local TF-IDF + LogisticRegression intent classifier
  ollama_provider.py # Optional local Ollama client for grounded synthesis & tool calling
  e2e_verification.py# End-to-end & long-session stability verification runner
  database/          # MongoDB/Redis managers, Pydantic v2 models, validators, indexes, seed, facade
  repositories/      # Multi-tenant repositories (tenant, catalog, livestream, orders)
  infrastructure/    # Redis operational cache, key conventions, Redis Streams
frontend/            # Next.js / React Operator Dashboard application
extension/           # Chrome Extension YouTube Live (read-only public chat + operator chip/popover UI)
tests/               # Automated end-to-end & repository integration test suite
docs/                # Kiến trúc, hợp đồng tích hợp, hướng dẫn DB và các báo cáo đánh giá/kiểm thử
reports/             # JSON evaluation & E2E verification reports
docker-compose.yml   # MongoDB 7.0 & Redis 7.2 local infrastructure
.env.example         # Cấu hình môi trường mẫu
AI_HANDOFF_REPORT.md # Báo cáo bàn giao chi tiết
pyproject.toml
```

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
- `python -m backend.e2e_verification`: Chạy kiểm thử toàn trình (End-to-End) và phiên livestream dài (300 bình luận liên tục + 25 bản tin trùng lặp DOM + kiểm tra đọc/ghi MongoDB Repository + Multi-Agent/RAG + kiểm tra nhãn giao diện Extension), xuất `reports/e2e_verification_report.json` và `docs/E2E_VERIFICATION_REPORT.md`.

## Khởi động Hạ tầng Database, Backend và Dashboard

### 1. Khởi động MongoDB & Redis (Tùy chọn khuyến nghị)

```powershell
docker compose up -d
```

Khi khởi động, `backend.main` tự động kết nối MongoDB/Redis, khởi tạo schema validators/indexes, nạp dữ liệu mẫu (`seed_demo_store_data`) và đồng bộ hóa bình luận qua `StoreDatabaseFacade`. Nếu không bật Docker, backend tự động chuyển sang chế độ `in_memory` mà không báo lỗi dừng chương trình.

### 2. Chạy FastAPI Backend & Operator Console

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

- Mở trình duyệt truy cập **`http://127.0.0.1:8000/dashboard`** để sử dụng **Livestream Sales Multi-Agent & RAG Console**.
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

## Cài đặt và sử dụng Extension YouTube Live

Extension kết nối với khung chat YouTube Live ở chế độ **chỉ đọc đối với khung chat công khai (không tự động gửi tin nhắn cho khách)**, đồng thời cung cấp giao diện hỗ trợ nhanh cho người vận hành ngay trên trang livestream.

1. Mở trình duyệt Chrome / Brave / Edge, truy cập `chrome://extensions/`.
2. Bật **Developer mode** (Chế độ cho nhà phát triển) ở góc trên bên phải.
3. Nhấn **Load unpacked** (Tải tiện ích đã giải nén) và chọn thư mục `extension/` trong dự án.
4. Mở một video YouTube Live có khung chat:
   - Nhấn nút biểu tượng **AI** trên thanh điều khiển trình phát video để bật **Enable** (hoặc mở nhanh **Open AI Dashboard**).
   - Khi khán giả bình luận, extension trích xuất bình luận và gửi về `POST /api/plugin/comments`.
   - Các bình luận có ý định mua hàng hoặc có bản nháp câu trả lời sẽ xuất hiện dưới dạng **thanh chip ngang** phía trên khung chat.
   - Nhấn vào chip để mở hộp thoại nội bộ xem nguồn trích dẫn (`sources`), trạng thái **`Draft Ready`** (đã có bản nháp AI) hoặc **`Needs Review`** (cần người vận hành kiểm tra), và lưu ghi chú/duyệt nháp nội bộ (`Save Note`).

## Tài liệu chi tiết

- [Kiến trúc AI, Backend & Extension](docs/AI_ARCHITECTURE.md)
- [Hợp đồng tích hợp API & Runtime](docs/AI_INTEGRATION_CONTRACT.md)
- [Hướng dẫn tích hợp Database & Redis Multi-Tenant](docs/DATABASE_INTEGRATION_GUIDE.md)
- [Kiến trúc Extension YouTube Live](docs/YOUTUBE_LIVE_EXTENSION_PLAN.md)
- [Báo cáo kiểm thử End-to-End & Phiên Livestream dài](docs/E2E_VERIFICATION_REPORT.md)
- [Báo cáo đánh giá Golden Cases](docs/EVALUATION_REPORT.md)
- [Báo cáo đánh giá Local Intent Model](docs/LOCAL_MODEL_EVALUATION.md)
- [Báo cáo bàn giao chi tiết](AI_HANDOFF_REPORT.md)
