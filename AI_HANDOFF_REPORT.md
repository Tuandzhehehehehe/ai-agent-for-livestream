# Báo cáo bàn giao AI/Data

## Trạng thái bàn giao

Các module và test AI/Data từ Phase 0 đến Phase 5 đã được kiểm tra trong workspace hiện tại. Tài liệu hiện có tại `docs/AI_ARCHITECTURE.md`, `docs/AI_INTEGRATION_CONTRACT.md`, `docs/EVALUATION_REPORT.md`; báo cáo máy đọc được tại `reports/evaluation_report.json`. Package này không bao gồm backend, frontend, livestream simulator, database hay xác thực. Chưa có tích hợp backend thực tế; việc đó thuộc Member 2.

Tài liệu kiến trúc ghi rõ các module ở phần directory layout là đề xuất. Trong code hiện tại, schema và retrieval của Q&A nằm trong `ai.customer_qa`; chưa có package `ai.providers`. Member 2 nên dựa vào các Python interface trong integration contract, không coi mọi mục kiến trúc đề xuất là module đã triển khai.

Trong audit cuối đã tái hiện và sửa một lỗi tạo báo cáo: JSON sắp xếp metric theo key, còn Markdown trước đó dùng thứ tự chèn. Renderer Markdown hiện sắp xếp cùng thứ tự; test `test_checked_in_reports_match_current_evaluation` kiểm tra cả hai artifact khớp với fixture hiện tại. Không thay đổi hành vi runtime AI hay public API.

## Kết quả xác minh

Các lệnh đã chạy trên workspace:

| Lệnh | Kết quả thực tế |
| --- | --- |
| `py -m unittest discover -s tests -v` | **78 tests passed** |
| `py -m compileall -q ai tests` | Hoàn tất thành công; exit code 0, không có output |
| `py -m ai.evaluation.runner` | Hoàn tất thành công; tạo lại JSON và Markdown reports |

Chạy evaluator lặp lại tạo SHA-256 giống hệt cho cả hai report. Test `test_checked_in_reports_match_current_evaluation` xác nhận JSON đã lưu bằng kết quả đánh giá mới và Markdown bằng nội dung renderer tạo từ JSON đó.

Evaluation gồm 19 customer Q&A cases, 13 comment-classification cases và 6 operations cases; tất cả expected outcomes đều khớp.

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

Đây là kết quả trên các golden cases tổng hợp do nhóm biên soạn, không phải ước lượng hiệu năng production. Phase này chưa đặt threshold. Unsupported-answer rate chỉ tính trên 11 case đã gắn nhãn rõ là thiếu bằng chứng.

## Public API

### Data

- `ai.data.load_synthetic_data(data_dir=None) -> SyntheticDataset`
- `load_products(path=None) -> tuple[Product, ...]`
- `load_faqs(path=None, *, known_product_ids=None) -> tuple[FAQ, ...]`
- `load_policies(path=None) -> tuple[SalesPolicy, ...]`
- `get_product_by_id(products, product_id) -> Product | None`
- `get_faqs(faqs, *, category=None, product_id=None) -> tuple[FAQ, ...]`
- `get_effective_policies(policies) -> tuple[SalesPolicy, ...]`
- `search_records(keyword, *, products, faqs, policies) -> tuple[SearchResult, ...]`

Records là frozen dataclass có type. JSON đi kèm chỉ chứa sản phẩm, FAQ và policy hư cấu phục vụ demo/kiểm thử.

### Customer Q&A

Từ `ai.customer_qa`: `CustomerQuestionRequest`, `IntentClassification`, `SourceReference`, `CustomerAnswerDraft`, `classify_intent(message, *, product_names=())`, `retrieve_evidence(question, *, intent=None, dataset=None)` và `draft_customer_answer(request, *, dataset=None)`.

`CustomerQuestionRequest` có `session_id: str`, `text: str`, và `comment_id: str | None` tùy chọn. `CustomerAnswerDraft` gồm `answer`, `intent`, `sources`, `status`, `needs_human_review`, `reason`, `is_draft`. `status` là `answered` hoặc `needs_review`. Mỗi Q&A source có `source_type` (`product`, `faq`, `policy`), `source_id`, `title`, `excerpt`.

Các giá trị `Intent` hiện hỗ trợ là `prompt_injection`, `refund_request`, `price_change_request`, `consequential_action`, `complaint`, `spam_or_irrelevant`, `promotion_question`, `shipping_policy`, `return_policy`, `stock_question`, `price_question`, `product_information`, `unknown`.

### Evaluation

Từ `ai.evaluation.runner`: `EvaluationDataError`, `load_scenarios(path=SCENARIOS_PATH)`, `run_evaluation(path=SCENARIOS_PATH)` và `write_evaluation_reports(report=None, *, json_path=..., markdown_path=...)`. Fixture sai hoặc thiếu phát sinh `EvaluationDataError`. Lệnh `py -m ai.evaluation.runner` chạy đánh giá và cập nhật hai report theo đường dẫn mặc định.

### Operations

Từ `ai.operations`: `SimulatedComment`, `SimulatedEvent`, `CommentClassification`, `FrequentQuestion`, `RecurringIssue`, `OperationsRecommendation`, `OperationsSummary`, `classify_comment`, `aggregate_frequent_questions`, `aggregate_recurring_issues`, `aggregate_recurring_event_issues`, `summarize_session`.

`SimulatedComment` gồm `comment_id`, `text`. `SimulatedEvent` gồm `event_id`, `event_type`, `description`, và `related_comment_ids` tùy chọn. `summarize_session(comments=None, events=None, *, dataset=None, minimum_question_frequency=2)` trả về counts, classifications, câu hỏi đã chuẩn hóa và nhóm lặp, recommendations, cùng sources. Chỉ `summarize_session` nhận mapping: `{comment_id, text}` cho comment và `{event_id, event_type, description, related_comment_ids?}` cho event.

## Tích hợp cho Member 2

Member 2 cần truyền đúng typed request và kiểm tra status/review flag trước khi hiển thị câu trả lời:

```python
from ai.customer_qa import CustomerQuestionRequest, draft_customer_answer

request = CustomerQuestionRequest(
    session_id="session-123",
    comment_id="comment-456",
    text="What is the shipping policy?",
)
draft = draft_customer_answer(request)

if draft.status == "needs_review" or draft.needs_human_review:
    # Member 2 chuyển draft/reason/sources tới quy trình người vận hành.
    pass
else:
    # Member 2 kiểm tra source IDs và trình bày dưới dạng draft.
    pass
```

`answered` có nghĩa AI tạo được draft có source; không phải chỉ thị tự gửi. `needs_review` bắt buộc để người thật quyết định. Kết quả review có thể không có source; nếu có, hãy giữ lại source IDs và `reason`. Package không thực hiện refund, sửa đơn, thay đổi giá, thanh toán hay hành động kinh doanh nào. Phê duyệt và thực thi thuộc Member 2.

Ví dụ operations:

```python
from ai.operations import SimulatedComment, SimulatedEvent, summarize_session

summary = summarize_session(
    comments=[SimulatedComment("c1", "The item arrived damaged")],
    events=[SimulatedEvent("e1", "out_of_stock", "Synthetic stock alert")],
)
for recommendation in summary.recommendations:
    # Member 2 hiển thị draft cùng source references comment/event.
    pass
```

Bắt `OperationsInputError` cho record sai định dạng, duplicate ID có dữ liệu mâu thuẫn, related-comment ID bị lặp hoặc event tham chiếu comment không tồn tại. Không âm thầm bỏ record lỗi. Record trùng hoàn toàn được tính một lần. Data loading có thể phát sinh `DataValidationError` hoặc `FileNotFoundError`; khi tải FAQ tùy chỉnh phải truyền `known_product_ids`.

## Hạn chế và rủi ro

- `pyproject.toml` chưa khai báo tường minh `ai/data/synthetic/*.json` là package data. Import/tải dữ liệu từ source workspace đã được xác minh, nhưng chưa kiểm tra wheel. Trước khi cài package dưới dạng wheel, cần xác nhận JSON được đóng gói hoặc cấu hình package data.
- `draft_customer_answer` yêu cầu một instance `CustomerQuestionRequest`. Service kiểm tra `text` nhưng không kiểm tra object request; Member 2 cần tạo đúng type ở boundary.
- `session_id` và `comment_id` chỉ được truyền qua, không được lưu hoặc đối chiếu với backend. Persistence, identity, approvals và transport thuộc Member 2.
- Intent và injection detection là baseline keyword/pattern tiếng Anh. Kết quả synthetic không chứng minh độ chính xác tổng quát hay khả năng chống prompt injection toàn diện.
- FAQ được nhóm theo text chuẩn hóa chính xác, không semantic deduplication. Recommendation operations giới hạn trong rule issue hiện tại và event-type allowlist.
- Q&A source IDs thuộc product/FAQ/policy; operations source IDs thuộc comment/event. Backend cần giữ `source_type` cùng với ID.

Không cần dependency hoặc service bên ngoài. Test runner đã xác minh là Python `unittest` tích hợp sẵn, chạy qua Windows launcher `py`.

## Tài liệu liên quan

- [README dự án](README.md)
- [Kiến trúc AI](docs/AI_ARCHITECTURE.md)
- [Contract tích hợp](docs/AI_INTEGRATION_CONTRACT.md)
- [Báo cáo evaluation](docs/EVALUATION_REPORT.md)