# End-to-End & Long-Session Verification Report

Báo cáo kiểm thử tự động xác minh luồng thu thập bình luận liên tục trong phiên livestream dài, khả năng đọc/ghi qua MongoDB Repository (`StoreDatabaseFacade`), cơ chế chống trùng lặp khi DOM YouTube Live render lại, bộ lọc an toàn/rate-limit, và tính chính xác của nhãn giao diện Extension.

## 1. Kết quả kiểm thử phiên livestream dài (Long-Session Stress & Zero-Loss)

| Chỉ số kiểm chứng | Kết quả đo được |
| --- | ---: |
| Số bình luận duy nhất gửi liên tục (`POST /api/plugin/comments`) | 300 / 300 (HTTP 200) |
| Số bản tin gửi lặp ID (`DOM re-render deduplication`) | 25 |
| Số bình luận lưu trong MongoDB Repository (`LivestreamCommentRepository`) | 300 |
| Số bình luận đọc qua `GET /api/plugin/comments` | 300 |
| Số bình luận khôi phục khi khởi động lại (`Cold-Start Repository Read`) | 300 |
| Số bình luận bị mất (`Lost comments`) | **0** |
| Số bản ghi bị nhân đôi (`Duplicate records`) | **0** |
| Độ trễ xử lý trung bình (`Mean latency`) | 3.65 ms |
| Độ trễ phân vị `p50 / p95 / p99` | 3.51 ms / 8.53 ms / 10.97 ms |

## 2. Xác minh Database Design (`livestream_sales_db` - 13 Collections & 7 Redis Key Patterns)

| Hạng mục | Kết quả |
| --- | --- |
| Tên cơ sở dữ liệu MongoDB (`database_name`) | `livestream_sales_db` |
| Số lượng MongoDB Collections (`total_mongodb_collections`) | `13 / 13` |
| Số lượng mẫu khóa Redis (`total_redis_key_patterns`) | `7 / 7` |
| Chế độ lưu trữ (`storage_mode`) | `mongodb` |
| Danh mục sản phẩm đọc từ Repository (`GET /api/store/products`) | `12` sản phẩm |
| Đơn hàng khởi tạo ban đầu (`seeded_orders_count`) | `6` đơn hàng |
| Đơn hàng tự động lên đơn từ bình luận (`auto_orders_created_in_session`) | `18` đơn hàng |
| Tổng số đơn hàng lưu trong Repository sau phiên (`GET /api/store/orders`) | `24` đơn hàng |
| Session metrics (`total_comments / drafts_generated / drafts_approved`) | `300 / 218 / 1` |
| Cập nhật trạng thái duyệt nháp qua thread (`record_seller_action`) | `edited` |
| Đồng bộ dữ liệu từ Repository khi khởi động lại (`Cold-Start Hydration`) | `True` |

## 3. Kiểm chứng Rate Limit, Moderation & Phân loại Ý định

- **Kiểm thử Burst Rate Limit (6 bình luận liên tiếp từ 1 viewer):** `[200, 200, 200, 200, 200, 429]` (5 bình luận đầu `200 OK`, bình luận thứ 6 bị chặn `429 Too Many Requests`).
- **Số bình luận vi phạm bị chặn (`moderation_status=blocked`):** `12`
- **Số bình luận chuyển người vận hành (`needs_human_review`):** `136`
- **Số khách hàng tiềm năng cao (`hot_lead_count`):** `108`

## 4. Kiểm chứng Multi-Agent, Vector RAG & Nhãn giao diện Extension

- **Multi-Agent Orchestrator:** `langgraph` (5 agents, 8 function-calling tools).
- **Vector RAG Backend:** `faiss` (top hit mẫu: `SYN-FAQ-009`).
- **Nhãn gây hiểu nhầm `Auto Replied` trong Extension:** `0` (đã thay thế hoàn toàn bằng `1` nhãn `Draft Ready`).
- **Đảm bảo chế độ Draft (`is_draft=True`, không tự gửi vào YouTube Live Chat):** `True`.

## 5. Lệnh chạy tái lập kết quả

```powershell
python -m backend.e2e_verification
python -m unittest discover -s tests -v
```
