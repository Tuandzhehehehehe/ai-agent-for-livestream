# AI hỗ trợ phiên livestream bán hàng

Mô-đun AI/Data cho đồ án tốt nghiệp “Xây dựng AI Agent hỗ trợ phiên livestream bán hàng”. Package cung cấp dữ liệu mô phỏng, tìm kiếm có cấu trúc, bản nháp trả lời khách hàng và hỗ trợ tổng hợp vận hành. Đây là thư viện Python độc lập để Member 2 tích hợp vào backend; repo này không chứa frontend hay API backend chính.

## Mục tiêu và phạm vi

- Trả lời câu hỏi dựa trên catalog, FAQ và policy mô phỏng, kèm source ID.
- Chuyển câu hỏi thiếu bằng chứng, mâu thuẫn, vượt phạm vi hoặc yêu cầu thao tác quan trọng sang người vận hành.
- Phân loại bình luận, tổng hợp câu hỏi lặp, issue và sự kiện phiên livestream được cung cấp.
- Đánh giá hành vi bằng các golden case tổng hợp, tái lập được và chạy offline.

Package không kết nối nền tảng livestream/e-commerce, không lưu đơn hàng, không xử lý thanh toán/hoàn tiền, không cung cấp màn hình phê duyệt và không tự thực hiện thao tác kinh doanh. Member 2 sở hữu backend, giao diện, simulator, xác thực và tích hợp luồng phê duyệt.

## Khả năng theo phase

- **Phase 0:** Python package metadata, cấu hình test và smoke test.
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
- `tests`: kiểm thử `unittest` cho data, Q&A, operations và evaluation.

## Cấu trúc thư mục

```text
ai/
  customer_qa/       # schemas, intent, safety, retrieval, answer service
  data/              # models, loading, access, search, synthetic JSON
  evaluation/        # scenarios.json, runner.py, metrics.py
  operations/        # schemas, classification, aggregation, summary
  __init__.py
docs/                # kiến trúc, contract tích hợp, evaluation report
reports/             # evaluation_report.json
tests/
    test_customer_qa.py
    test_data.py
    test_evaluation.py
    test_operations.py
    test_smoke.py
AI_HANDOFF_REPORT.md
pyproject.toml
```

## Môi trường và chạy trên Windows

Yêu cầu Python **3.11 trở lên**. Môi trường đã xác minh dùng Python 3.13.5 thông qua Windows launcher `py`. Package không có runtime dependency bên ngoài; không cần cài `pytest`. Build backend trong `pyproject.toml` khai báo `setuptools>=68`.

Mở PowerShell tại thư mục gốc repository (thư mục chứa `pyproject.toml`) và chạy:

```powershell
py --version
py -m unittest discover -s tests -v
py -m compileall -q ai tests
py -m ai.evaluation.runner
```

Lệnh evaluator tạo/cập nhật `reports/evaluation_report.json` và `docs/EVALUATION_REPORT.md`. Có thể chạy tests trực tiếp từ source checkout; nếu đóng gói thành wheel, cần xác nhận các file `ai/data/synthetic/*.json` được đưa vào package.

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

## Dữ liệu mô phỏng và an toàn

Các file `products.json`, `faqs.json`, `policies.json` được gắn nhãn synthetic/demo. Tên sản phẩm, giá, tồn kho, thời hạn vận chuyển/đổi trả và policy chỉ phục vụ phát triển/kiểm thử; **không phải dữ liệu hoặc cam kết kinh doanh thật**. Không có dữ liệu khách hàng thật.

Classifier và injection checks dựa trên keyword/pattern tiếng Anh, không bảo đảm nhận diện mọi cách diễn đạt hoặc prompt injection. Dữ liệu/comment/event là input không tin cậy. Refund, thay đổi giá, đơn hàng, payment, và thao tác consequential khác không bao giờ được AI thực hiện. Khi thiếu bằng chứng, có conflict, hoặc ngoài phạm vi, cần người thật xử lý.

## Tài liệu

- [Kiến trúc AI](docs/AI_ARCHITECTURE.md)
- [Contract tích hợp Member 2](docs/AI_INTEGRATION_CONTRACT.md)
- [Báo cáo evaluation](docs/EVALUATION_REPORT.md)
- [Báo cáo bàn giao chi tiết](AI_HANDOFF_REPORT.md)