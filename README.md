# AI hỗ trợ phiên livestream bán hàng

Mô-đun AI/Data cho đồ án tốt nghiệp “Xây dựng AI Agent hỗ trợ phiên livestream bán hàng”. Repository gồm thư viện AI, FastAPI backend local và Chrome extension thu thập bình luận YouTube Live ở chế độ chỉ đọc. Backend phân loại bình luận, tạo draft có nguồn và cung cấp thống kê; không có dashboard, database hay xác thực.

## Mục tiêu và phạm vi

- Trả lời câu hỏi dựa trên catalog, FAQ và policy mô phỏng, kèm source ID.
- Chuyển câu hỏi thiếu bằng chứng, mâu thuẫn, vượt phạm vi hoặc yêu cầu thao tác quan trọng sang người vận hành.
- Phân loại bình luận, tổng hợp câu hỏi lặp, issue và sự kiện phiên livestream được cung cấp.
- Đánh giá hành vi bằng các golden case tổng hợp, tái lập được và chạy offline.

Extension kết nối khung chat YouTube Live chỉ để thu thập bình luận. Backend không lưu đơn hàng, không xử lý thanh toán/hoàn tiền, không cung cấp màn hình phê duyệt và không tự gửi tin nhắn hay thực hiện thao tác kinh doanh.

## Khả năng theo phase

- **Phase 0:** Python package metadata và cấu hình môi trường.
- **Phase 1:** Dataclass có kiểu cho Product/FAQ/SalesPolicy; JSON mô phỏng; loader, validation, accessors và keyword search.
- **Phase 2:** Intent classification theo rule, retrieval và bản nháp customer Q&A có source reference.
- **Phase 3:** Review gate cho injection đã nhận diện, yêu cầu consequential, dữ liệu thiếu/mâu thuẫn và input quá dài. Giới hạn message là 4096 ký tự.
- **Phase 4:** Phân loại comment/event, nhóm câu hỏi và issue lặp, tóm tắt phiên cùng recommendation dạng draft có nguồn.
- **Phase 5:** Golden-case evaluation, metric có numerator/denominator, JSON report và Markdown report.

## Kiến trúc

- `ai.data`: schema, tải/kiểm tra dữ liệu mô phỏng, truy vấn và search.
- `ai.customer_qa`: `classify_intent`, `retrieve_evidence`, `draft_customer_answer`; trả `CustomerAnswerDraft` với status `answered` hoặc `needs_review`.
- `ai.operations`: `SimulatedComment`, `SimulatedEvent`, classifier, aggregator và `summarize_session`; không gọi hệ thống ngoài.
- `ai.evaluation`: scenarios tách khỏi demo catalog, runner và metric.
- `backend.local_model`: TF-IDF character n-gram + Logistic Regression, train local từ corpus synthetic song ngữ; chỉ fallback khi rule classifier chưa nhận diện intent.
- `backend.main`: API nhận bình luận, rate limit/moderation và answer draft dựa trên retrieval có nguồn. Không gọi dịch vụ LLM ngoài.
- `extension`: collector YouTube Live read-only; không tự động trả lời chat.

## Cấu trúc thư mục

```text
ai/
  customer_qa/       # schemas, intent, safety, retrieval, answer service
  data/              # models, loading, access, search, synthetic JSON
  evaluation/        # scenarios.json, runner.py, metrics.py
  operations/        # schemas, classification, aggregation, summary
  __init__.py
docs/                # kiến trúc, contract tích hợp, evaluation report
extension/           # Chrome Extension thu thập bình luận YouTube Live (read-only)
backend/             # FastAPI backend và local model
    local_model.py     # Train/evaluate local intent classifier
    intent_training_data.json # 260 mẫu synthetic để train classifier
    intent_holdout_data.json # 65 mẫu synthetic holdout, tách khỏi train
reports/             # JSON evaluation reports
AI_HANDOFF_REPORT.md
pyproject.toml
```

## Môi trường và chạy trên Windows

Yêu cầu Python **3.11 trở lên**. Cài dependencies backend/local model bằng `py -m pip install -e ".[server]"`. Build backend trong `pyproject.toml` khai báo `setuptools>=68`. Không cần API key hoặc dịch vụ LLM.

Mở PowerShell tại thư mục gốc repository (thư mục chứa `pyproject.toml`) và chạy:

```powershell
py --version
py -m compileall -q ai backend
py -m ai.evaluation.runner
py -m backend.local_model
```

Lệnh evaluator tạo/cập nhật `reports/evaluation_report.json` và `docs/EVALUATION_REPORT.md`. `py -m backend.local_model` train model local từ corpus synthetic. Nếu đóng gói thành wheel, các file JSON được cấu hình trong `pyproject.toml` để đi cùng package.

## Ví dụ sử dụng

Customer Q&A:

```python
from ai.customer_qa import CustomerQuestionRequest, draft_customer_answer

draft = draft_customer_answer(
    CustomerQuestionRequest(
        session_id="demo-session",
        comment_id="demo-comment-1",
        text="What is the shipping policy?",
    )
)

if draft.status == "needs_review" or draft.needs_human_review:
    print(draft.reason)
else:
    print(draft.answer)
    print([source.source_id for source in draft.sources])
```

Tất cả answer đều là draft. `answered` không có nghĩa đã gửi cho khách; Member 2 cần kiểm tra sources. `needs_review` phải được chuyển tới người vận hành.

Tóm tắt phiên mô phỏng:

```python
from ai.operations import SimulatedComment, SimulatedEvent, summarize_session

summary = summarize_session(
    comments=[
        SimulatedComment("c1", "What is the shipping policy?"),
        SimulatedComment("c2", "What is the shipping policy?"),
        SimulatedComment("c3", "The item arrived damaged"),
    ],
    events=[
        SimulatedEvent("e1", "stream_interruption", "Synthetic pause event"),
    ],
)

print(summary.overview)
print(summary.frequent_questions)
print(summary.recommendations)
```

`summary.recommendations` là gợi ý cần operator xem xét, có `sources`; package không thực hiện hành động.

## Cài đặt và sử dụng Extension YouTube Live

Extension chạy ngầm (Headless - không giao diện, **không tự động trả lời vào chat**) trên trình duyệt (Chrome, Brave, Cốc Cốc, Edge) tự động lắng nghe và thu thập bình luận theo thời gian thực từ luồng chat YouTube Live đồng bộ về AI Backend.

### 1. Cài đặt vào trình duyệt

1. Mở trình duyệt Chrome hoặc Brave, truy cập:
   ```text
   chrome://extensions/
   ```
2. Bật công tắc "Developer mode" (Chế độ cho nhà phát triển) ở góc trên bên phải.
3. Nhấn nút "Load unpacked" (Tải tiện ích đã giải nén).
4. Chọn thư mục `extension` trong thư mục dự án, ví dụ:
   ```text
    D:\project_2\extension
   ```

### 2. Khởi động Backend AI

Từ thư mục gốc repository, cài dependencies và chạy API. Không cần API key hoặc kết nối LLM; model intent local được train từ corpus synthetic khi khởi động:

```powershell
py -m pip install -e ".[server]"
py -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Kiểm tra backend tại `http://localhost:8000/health`. API nhận bình luận tại `POST /api/plugin/comments`; rule classifier chạy trước, model TF-IDF character n-gram + Logistic Regression chỉ làm fallback khi rules chưa nhận diện intent. Câu trả lời vẫn dựa trên retrieval/rules có source; thiếu evidence thì `needs_review`. Local model train trên 260 mẫu synthetic song ngữ và được đo trên holdout độc lập 65 mẫu trong `backend/intent_holdout_data.json`. Chạy `py -m backend.local_model` để train, evaluate và sinh `docs/LOCAL_MODEL_EVALUATION.md` cùng `reports/local_intent_model_report.json`. Lần chạy hiện tại: accuracy 75.4%, macro-F1 77.7%, answerable coverage 76.7%, selective accuracy 95.7%; đây không phải kết quả production. Model là classifier nhỏ, không phải model sinh ngôn ngữ. Ngưỡng local classifier cấu hình bằng `LOCAL_INTENT_CONFIDENCE_THRESHOLD` (mặc định `0.10`). Backend giới hạn mặc định 5 comment/10 giây cho mỗi viewer/member (`CHAT_RATE_LIMIT_MAX`, `CHAT_RATE_LIMIT_WINDOW_SECONDS`) và trả HTTP 429 khi vượt ngưỡng. Bộ lọc mặc định chặn một số từ tục tiếng Anh/Việt và comment có hơn 2 URL; có thể bổ sung cụm từ qua `CHAT_BLOCKED_TERMS` (cách nhau bằng dấu phẩy). Comment bị chặn có `moderation_status=blocked`; moderation chỉ đánh dấu trong API, không xóa comment khỏi YouTube. Draft được trả cùng bình luận và lưu trong danh sách qua `GET /api/plugin/comments`; thống kê có tại `GET /api/plugin/summary`. Dữ liệu catalog là synthetic/demo, tối đa 1000 bình luận gần nhất được giữ trong bộ nhớ và sẽ mất khi backend khởi động lại. Rate limit cũng chỉ nằm trong bộ nhớ và được áp dụng trong một tiến trình backend. Backend không có xác thực; chỉ bind vào loopback như lệnh trên, không expose trực tiếp ra mạng.

### 3. Vận hành trên YouTube Live

1. Mở một video livestream YouTube bất kỳ có khung Live Chat (hoặc YouTube Live Studio).
2. Extension tự động nhận diện khung chat (`yt-live-chat-item-list-renderer`) và kết nối trực tiếp ở chế độ chỉ đọc (Read-only).
3. Khi khán giả bình luận:
   - Extension tự động trích xuất người gửi, nội dung bình luận, loại tài khoản (`viewer`, `member`, `moderator`).
   - In log chi tiết trong Console DevTools (`F12`).
   - Gửi dữ liệu bình luận về AI Backend (`/api/plugin/comments`) để xử lý thống kê hoặc phân loại ý định.
   - **Tuyệt đối không tự động gõ hay gửi bất kỳ tin nhắn nào vào khung chat**.

### 4. Cập nhật khi sửa code

Khi chỉnh sửa code trong thư mục `extension/`, vào `chrome://extensions/` và nhấn nút Reload (xoay tròn) trên thẻ tiện ích để cập nhật ngay.

## Dữ liệu mô phỏng và an toàn

Các file `products.json`, `faqs.json`, `policies.json` được gắn nhãn synthetic/demo. Tên sản phẩm, giá, tồn kho, thời hạn vận chuyển/đổi trả và policy chỉ phục vụ phát triển/kiểm thử; **không phải dữ liệu hoặc cam kết kinh doanh thật**. Không có dữ liệu khách hàng thật.

Classifier và injection checks dựa trên keyword/pattern tiếng Anh, không bảo đảm nhận diện mọi cách diễn đạt hoặc prompt injection. Dữ liệu/comment/event là input không tin cậy. Refund, thay đổi giá, đơn hàng, payment, và thao tác consequential khác không bao giờ được AI thực hiện. Khi thiếu bằng chứng, có conflict, hoặc ngoài phạm vi, cần người thật xử lý.

## Tài liệu

- [Kiến trúc AI](docs/AI_ARCHITECTURE.md)
- [Contract tích hợp Member 2](docs/AI_INTEGRATION_CONTRACT.md)
- [Báo cáo evaluation](docs/EVALUATION_REPORT.md)
- [Báo cáo train/evaluation local model](docs/LOCAL_MODEL_EVALUATION.md)
- [Báo cáo bàn giao chi tiết](AI_HANDOFF_REPORT.md)
- [Kế hoạch Extension YouTube Live](docs/YOUTUBE_LIVE_EXTENSION_PLAN.md)