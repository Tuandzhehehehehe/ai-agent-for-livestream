"""Automated end-to-end, order validation, Vietnamese Q&A, and long-session stability tests for Livestream AI."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from backend.database.contracts import StoreDatabaseFacade
from backend.database.embedded_engine import (
    EmbeddedMongoDatabaseManager,
    EmbeddedRedisManager,
)
from backend.database.models import TenantContext
from backend.database.seed import seed_demo_store_data
from backend.e2e_verification import (
    InProcessAppClient,
    build_repository_backed_facade,
    run_coroutine_sync,
    run_e2e_verification,
)
from backend.main import create_app


class LivestreamEndToEndTests(unittest.TestCase):
    def test_long_session_zero_loss_and_repository_read_write(self) -> None:
        report = run_e2e_verification(long_session_comment_count=300, viewer_pool_size=60)
        ls = report["long_session_stability"]
        db = report["database_repository_verification"]
        ext = report["extension_ui_contract"]

        self.assertEqual(ls["submitted_unique_comments"], 300)
        self.assertEqual(ls["persisted_in_mongodb_repository"], 300)
        self.assertEqual(ls["returned_by_list_endpoint"], 300)
        self.assertEqual(ls["returned_by_cold_start_repository_read"], 300)
        self.assertEqual(ls["lost_comments"], 0)
        self.assertEqual(ls["duplicate_records_created"], 0)

        self.assertEqual(db["database_name"], "livestream_sales_db")
        self.assertEqual(db["total_mongodb_collections"], 13)
        self.assertEqual(db["total_redis_key_patterns"], 7)
        self.assertEqual(db["storage_mode"], "mongodb")
        self.assertEqual(db["seeded_products_count"], 12)
        self.assertGreaterEqual(db["seeded_orders_count"], 1)
        # Aurora Studio Ringlight starts with stock_quantity = 18; each order buys 2 -> exactly 9 orders succeed before stock = 0
        self.assertEqual(db["auto_orders_created_in_session"], 9)
        self.assertEqual(
            db["total_orders_after_session"],
            db["seeded_orders_count"] + db["auto_orders_created_in_session"],
        )
        self.assertEqual(db["thread_seller_action_recorded"], "edited")
        self.assertTrue(db["cold_start_repository_hydration_passed"])

        self.assertEqual(ext["auto_replied_badge_occurrences"], 0)
        self.assertGreaterEqual(ext["draft_ready_badge_occurrences"], 1)
        self.assertEqual(ext["deprecated_exec_command_occurrences"], 0)
        self.assertTrue(ext["auto_reply_toggle_default_off"])
        self.assertTrue(ext["chat_send_queue_enabled"])
        self.assertTrue(ext["all_answers_marked_is_draft"])

    def test_in_memory_fallback_when_database_unavailable(self) -> None:
        facade = build_repository_backed_facade()
        app = create_app(db_facade=facade)
        with InProcessAppClient(app) as client:
            app.state.db = None
            res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "mem-comment-1",
                    "sender": "@fallback_viewer",
                    "text": "What is the shipping policy?",
                    "authorType": "viewer",
                    "timestamp": 1791568800000,
                },
            )
            self.assertEqual(res.status_code, 200)
            body = res.json()
            self.assertEqual(body["intent"], "shipping_policy")
            self.assertIsNotNone(body["answer_draft"])
            self.assertTrue(body["answer_draft"]["is_draft"])
            self.assertEqual(body["thread_messages"][0]["sender"], "AI Draft")

            summary = client.get("/api/plugin/summary").json()
            self.assertEqual(summary["storage_mode"], "in_memory")
            self.assertEqual(summary["comment_count"], 1)

    def test_draft_initially_pending_until_operator_action(self) -> None:
        facade = build_repository_backed_facade()
        ctx = TenantContext.system_context("default-store")
        run_coroutine_sync(seed_demo_store_data(facade, ctx, session_id="youtube-live"))
        app = create_app(db_facade=facade)

        with InProcessAppClient(app) as client:
            res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "draft-check-1",
                    "sender": "@buyer_check",
                    "text": "What is the return policy?",
                    "authorType": "viewer",
                    "timestamp": 1791568805000,
                },
            )
            self.assertEqual(res.status_code, 200)
            suggested_reply = res.json()["answer_draft"]["answer"]

            draft_doc = run_coroutine_sync(
                facade.answer_drafts.get_latest_for_comment(ctx, "draft-check-1")
            )
            self.assertIsNotNone(draft_doc)
            assert draft_doc is not None
            self.assertEqual(draft_doc.seller_action, "pending")
            self.assertIsNone(draft_doc.final_reply_text)

            thread_res = client.post(
                "/api/plugin/comments/draft-check-1/thread",
                json={
                    "sender": "AI Draft",
                    "authorRole": "ai",
                    "text": suggested_reply,
                    "timestamp": 1791568806000,
                },
            )
            self.assertEqual(thread_res.status_code, 200)

            updated_draft = run_coroutine_sync(
                facade.answer_drafts.get_latest_for_comment(ctx, "draft-check-1")
            )
            assert updated_draft is not None
            self.assertEqual(updated_draft.seller_action, "approved")

    def test_auto_order_stock_validation_and_live_inventory_qa(self) -> None:
        facade = build_repository_backed_facade()
        ctx = TenantContext.system_context("default-store")
        run_coroutine_sync(seed_demo_store_data(facade, ctx, session_id="youtube-live"))
        app = create_app(db_facade=facade)

        with InProcessAppClient(app) as client:
            overview = client.get("/api/database/overview").json()
            self.assertEqual(overview["database_name"], "livestream_sales_db")
            self.assertEqual(overview["total_collections"], 13)
            self.assertEqual(overview["total_redis_patterns"], 7)

            # 1. Ordering an out-of-stock item (SYN-PROD-004 Cloudstep Canvas Tote, stock=0) must be rejected
            oos_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "oos-order-1",
                    "sender": "@MizuGamingHub",
                    "text": "buy 2 cloudstep canvas tote",
                    "authorType": "viewer",
                    "timestamp": 1791568900000,
                },
            )
            self.assertEqual(oos_res.status_code, 200)
            oos_body = oos_res.json()
            self.assertIsNone(oos_body["auto_order"])
            self.assertIn("out of stock", oos_body["answer_draft"]["answer"].lower())

            # 2. Ordering more than available stock (SYN-PROD-007 Aurora Studio Ringlight has stock 18) must be rejected
            over_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "over-order-1",
                    "sender": "@StudioBuyer",
                    "text": "buy 19 Aurora Studio Ringlight",
                    "authorType": "viewer",
                    "timestamp": 1791568901000,
                },
            )
            self.assertEqual(over_res.status_code, 200)
            over_body = over_res.json()
            self.assertIsNone(over_body["auto_order"])
            self.assertTrue(over_body["needs_human_review"])
            self.assertIn("18", over_body["answer_draft"]["answer"])

            # 3. Valid order without phone/address -> creates pending + unpaid order and deducts live inventory
            valid_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "valid-order-1",
                    "sender": "@DeskBuyer",
                    "text": "buy 2 LumaNest Desk Lamp",
                    "authorType": "viewer",
                    "timestamp": 1791568902000,
                },
            )
            self.assertEqual(valid_res.status_code, 200)
            valid_body = valid_res.json()
            self.assertIsNotNone(valid_body["auto_order"])
            self.assertEqual(valid_body["auto_order"]["status"], "pending")
            self.assertEqual(valid_body["auto_order"]["payment_status"], "unpaid")
            self.assertEqual(valid_body["auto_order"]["items"][0]["product_id"], "SYN-PROD-001")
            self.assertEqual(valid_body["auto_order"]["items"][0]["quantity"], 2)

            # 4. Stock Q&A must reflect post-order live DB inventory (12 - 2 = 10 left)
            stock_qa_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "stock-qa-1",
                    "sender": "@StockChecker",
                    "text": "đèn bàn còn hàng không shop?",
                    "authorType": "viewer",
                    "timestamp": 1791568903000,
                },
            )
            self.assertEqual(stock_qa_res.status_code, 200)
            stock_qa_body = stock_qa_res.json()
            self.assertEqual(stock_qa_body["intent"], "stock_question")
            self.assertEqual(stock_qa_body["answer_draft"]["status"], "answered")
            self.assertIn("10", stock_qa_body["answer_draft"]["answer"])

            # 5. Valid order WITH phone and shipping address -> creates confirmed + cod order
            confirmed_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "confirmed-order-1",
                    "sender": "@FullInfoBuyer",
                    "text": "chốt 1 đèn bàn SĐT 0912345678 địa chỉ: 123 Nguyễn Huệ Quận 1 TP HCM",
                    "authorType": "viewer",
                    "timestamp": 1791568904000,
                },
            )
            self.assertEqual(confirmed_res.status_code, 200)
            confirmed_body = confirmed_res.json()
            self.assertIsNotNone(confirmed_body["auto_order"])
            self.assertEqual(confirmed_body["auto_order"]["status"], "confirmed")
            self.assertEqual(confirmed_body["auto_order"]["payment_status"], "cod")

    def test_false_positive_filtering_and_question_mark_closing(self) -> None:
        facade = build_repository_backed_facade()
        ctx = TenantContext.system_context("default-store")
        run_coroutine_sync(seed_demo_store_data(facade, ctx, session_id="youtube-live"))
        app = create_app(db_facade=facade)

        with InProcessAppClient(app) as client:
            false_positives = [
                ("fp-1", "don't buy LumaNest Desk Lamp"),
                ("fp-2", "đừng mua đèn bàn này nha"),
                ("fp-3", "mình chưa muốn mua đèn bàn lúc này"),
                ("fp-4", "mình đã mua đèn bàn tuần trước rồi"),
                ("fp-5", "where is my order for LumaNest Desk Lamp?"),
                ("fp-6", "take a look at the LumaNest Desk Lamp"),
            ]
            for cid, text in false_positives:
                res = client.post(
                    "/api/plugin/comments",
                    json={
                        "id": cid,
                        "sender": f"@ViewerFP_{cid}",
                        "text": text,
                        "authorType": "viewer",
                        "timestamp": 1791568910000,
                    },
                )
                self.assertEqual(res.status_code, 200)
                self.assertIsNone(
                    res.json()["auto_order"],
                    f"False positive order should not be created for: {text}",
                )

            # Closing comment with a question mark ("chốt 1 cái đèn bàn?") MUST create an order
            q_close_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "q-close-1",
                    "sender": "@PoliteBuyer",
                    "text": "chốt 1 cái đèn bàn?",
                    "authorType": "viewer",
                    "timestamp": 1791568920000,
                },
            )
            self.assertEqual(q_close_res.status_code, 200)
            q_close_body = q_close_res.json()
            self.assertIsNotNone(q_close_body["auto_order"])
            self.assertEqual(q_close_body["auto_order"]["items"][0]["product_id"], "SYN-PROD-001")
            self.assertEqual(q_close_body["auto_order"]["items"][0]["quantity"], 1)

    def test_invalid_quantities_multi_item_orders_and_deduplication(self) -> None:
        facade = build_repository_backed_facade()
        ctx = TenantContext.system_context("default-store")
        run_coroutine_sync(seed_demo_store_data(facade, ctx, session_id="youtube-live"))
        app = create_app(db_facade=facade)

        with InProcessAppClient(app) as client:
            # 1. Invalid quantity 0 -> rejected, not coerced to 1
            zero_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "qty-zero-1",
                    "sender": "@QtyTester",
                    "text": "mua 0 đèn bàn",
                    "authorType": "viewer",
                    "timestamp": 1791568930000,
                },
            )
            self.assertEqual(zero_res.status_code, 200)
            self.assertIsNone(zero_res.json()["auto_order"])
            self.assertTrue(zero_res.json()["needs_human_review"])

            # 2. Invalid quantity 25 (> 20 limit) -> rejected, not coerced to 1
            high_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "qty-high-1",
                    "sender": "@QtyTester2",
                    "text": "buy 25 Pebblebrook Ceramic Mug",
                    "authorType": "viewer",
                    "timestamp": 1791568931000,
                },
            )
            self.assertEqual(high_res.status_code, 200)
            self.assertIsNone(high_res.json()["auto_order"])
            self.assertTrue(high_res.json()["needs_human_review"])

            # 3. Multi-item order ("mua 2 đèn bàn và 3 cốc sứ") -> single order with 2 items
            multi_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "multi-item-1",
                    "sender": "@MultiBuyer",
                    "text": "mua 2 đèn bàn và 3 cốc sứ",
                    "authorType": "viewer",
                    "timestamp": 1791568932000,
                },
            )
            self.assertEqual(multi_res.status_code, 200)
            multi_body = multi_res.json()
            self.assertIsNotNone(multi_body["auto_order"])
            items = multi_body["auto_order"]["items"]
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["product_id"], "SYN-PROD-001")
            self.assertEqual(items[0]["quantity"], 2)
            self.assertEqual(items[1]["product_id"], "SYN-PROD-002")
            self.assertEqual(items[1]["quantity"], 3)

            # 4. Consecutive duplicate closing comment from the same viewer -> reuses existing pending order
            dup_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "multi-item-2",
                    "sender": "@MultiBuyer",
                    "text": "mua 2 đèn bàn và 3 cốc sứ",
                    "authorType": "viewer",
                    "timestamp": 1791568933000,
                },
            )
            self.assertEqual(dup_res.status_code, 200)
            dup_body = dup_res.json()
            self.assertIsNotNone(dup_body["auto_order"])
            self.assertEqual(
                dup_body["auto_order"]["order_id"],
                multi_body["auto_order"]["order_id"],
            )

    def test_vietnamese_qa_for_price_stock_shipping_cod_and_return_policy(self) -> None:
        facade = build_repository_backed_facade()
        ctx = TenantContext.system_context("default-store")
        run_coroutine_sync(seed_demo_store_data(facade, ctx, session_id="youtube-live"))
        app = create_app(db_facade=facade)

        with InProcessAppClient(app) as client:
            # 1. Vietnamese price question
            p_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "vi-price-1",
                    "sender": "@ViViewer1",
                    "text": "đèn bàn giá bao nhiêu shop?",
                    "authorType": "viewer",
                    "timestamp": 1791568940000,
                },
            ).json()
            self.assertEqual(p_res["intent"], "price_question")
            self.assertEqual(p_res["answer_draft"]["status"], "answered")
            self.assertFalse(p_res["needs_human_review"])
            self.assertIn("$24.50", p_res["answer_draft"]["answer"])

            # 2. Vietnamese stock question
            s_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "vi-stock-1",
                    "sender": "@ViViewer2",
                    "text": "cốc sứ còn hàng không ạ?",
                    "authorType": "viewer",
                    "timestamp": 1791568941000,
                },
            ).json()
            self.assertEqual(s_res["intent"], "stock_question")
            self.assertEqual(s_res["answer_draft"]["status"], "answered")
            self.assertFalse(s_res["needs_human_review"])
            self.assertIn("40", s_res["answer_draft"]["answer"])

            # 3. Vietnamese COD / shipping fee question
            cod_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "vi-cod-1",
                    "sender": "@ViViewer3",
                    "text": "phí ship COD bao nhiêu tiền vậy shop?",
                    "authorType": "viewer",
                    "timestamp": 1791568942000,
                },
            ).json()
            self.assertEqual(cod_res["intent"], "shipping_policy")
            self.assertEqual(cod_res["answer_draft"]["status"], "answered")
            self.assertFalse(cod_res["needs_human_review"])
            self.assertIn("COD", cod_res["answer_draft"]["answer"])
            self.assertIn("$4.99", cod_res["answer_draft"]["answer"])

            # 4. Vietnamese return policy question
            ret_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "vi-ret-1",
                    "sender": "@ViViewer4",
                    "text": "chính sách đổi trả bảo hành thế nào?",
                    "authorType": "viewer",
                    "timestamp": 1791568943000,
                },
            ).json()
            self.assertEqual(ret_res["intent"], "return_policy")
            self.assertEqual(ret_res["answer_draft"]["status"], "answered")
            self.assertFalse(ret_res["needs_human_review"])
            self.assertIn("14 ngày", ret_res["answer_draft"]["answer"])

    def test_embedded_database_disk_persistence_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "test_livestream_db.json"
            ctx = TenantContext.system_context("default-store")

            # First server process writes to disk-backed EmbeddedMongoDatabaseManager
            mongo_1 = EmbeddedMongoDatabaseManager("livestream_sales_db", persist_path=db_file)
            facade_1 = StoreDatabaseFacade(mongo_1, EmbeddedRedisManager())  # type: ignore[arg-type]
            run_coroutine_sync(seed_demo_store_data(facade_1, ctx, session_id="youtube-live"))
            app_1 = create_app(db_facade=facade_1)
            with InProcessAppClient(app_1) as client_1:
                order_res = client_1.post(
                    "/api/plugin/comments",
                    json={
                        "id": "persist-c-1",
                        "sender": "@DiskBuyer",
                        "text": "mua 1 cốc sứ",
                        "authorType": "viewer",
                        "timestamp": 1791568950000,
                    },
                ).json()
                created_order_id = order_res["auto_order"]["order_id"]

            self.assertTrue(db_file.exists())

            # Second server process loads from the same disk file and runs seed_demo_store_data on startup
            mongo_2 = EmbeddedMongoDatabaseManager("livestream_sales_db", persist_path=db_file)
            facade_2 = StoreDatabaseFacade(mongo_2, EmbeddedRedisManager())  # type: ignore[arg-type]
            run_coroutine_sync(seed_demo_store_data(facade_2, ctx, session_id="youtube-live"))
            app_2 = create_app(db_facade=facade_2)
            with InProcessAppClient(app_2) as client_2:
                comments_2 = client_2.get("/api/plugin/comments").json()
                self.assertEqual(len(comments_2), 1)
                self.assertIsNotNone(comments_2[0]["auto_order"])
                self.assertEqual(comments_2[0]["auto_order"]["order_id"], created_order_id)

                # Verify inventory was NOT reset to 40 by seed_demo_store_data (must remain 39 after 1 mug sold)
                prods_2 = client_2.get("/api/store/products").json()["items"]
                mug_prod = next(p for p in prods_2 if p["product_id"] == "SYN-PROD-002")
                self.assertEqual(mug_prod["stock_quantity"], 39)

                # Creating a new order after restart must NOT collide with created_order_id
                next_res = client_2.post(
                    "/api/plugin/comments",
                    json={
                        "id": "persist-c-2",
                        "sender": "@DiskBuyer2",
                        "text": "mua 1 đèn bàn",
                        "authorType": "viewer",
                        "timestamp": 1791568955000,
                    },
                ).json()
                self.assertIsNotNone(next_res["auto_order"])
                self.assertNotEqual(next_res["auto_order"]["order_id"], created_order_id)

    def test_echo_loop_prevention_compliment_filter_and_owner_skip(self) -> None:
        facade = build_repository_backed_facade()
        ctx = TenantContext.system_context("default-store")
        run_coroutine_sync(seed_demo_store_data(facade, ctx, session_id="youtube-live"))
        app = create_app(db_facade=facade)

        with InProcessAppClient(app) as client:
            # 1. Bot's own order confirmation read back WITHOUT emoji 🤖 (as when YouTube renders <img class="emoji" alt="🤖">)
            echo_order_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "echo-ord-1",
                    "sender": "@BotChannel",
                    "text": "@Viewer Order #ORD-LIVE-0006 created! 1x LumaNest Desk Lamp ($24.50 + $4.99 ship = $29.49). Status: pending phone & shipping address confirmation.",
                    "authorType": "viewer",
                    "timestamp": 1791568960000,
                },
            ).json()
            self.assertIsNone(echo_order_res["auto_order"])
            self.assertIsNone(echo_order_res["answer_draft"])

            # 2. Bot's own price answer read back WITHOUT emoji 🤖
            echo_price_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "echo-price-1",
                    "sender": "@BotChannel",
                    "text": "@Viewer Sản phẩm LumaNest Desk Lamp có giá niêm yết là $24.50 (còn 12 sản phẩm).",
                    "authorType": "viewer",
                    "timestamp": 1791568961000,
                },
            ).json()
            self.assertIsNone(echo_price_res["auto_order"])
            self.assertIsNone(echo_price_res["answer_draft"])

            # 3. Channel owner comment is ignored for auto-order and auto-reply
            owner_res = client.post(
                "/api/plugin/comments",
                json={
                    "id": "owner-c-1",
                    "sender": "@ChannelOwner",
                    "text": "mua 2 đèn bàn giá bao nhiêu?",
                    "authorType": "owner",
                    "timestamp": 1791568962000,
                },
            ).json()
            self.assertIsNone(owner_res["auto_order"])
            self.assertIsNone(owner_res["answer_draft"])

            # 4. Viewer compliments ("mình thấy đèn bàn đẹp quá", "quạt nhìn xịn ghê") must NOT spam product catalog replies
            for idx, comp_text in enumerate(
                ["mình thấy đèn bàn đẹp quá", "quạt nhìn xịn ghê", "son màu xinh quá"]
            ):
                comp_res = client.post(
                    "/api/plugin/comments",
                    json={
                        "id": f"compliment-{idx}",
                        "sender": f"@Complimenter_{idx}",
                        "text": comp_text,
                        "authorType": "viewer",
                        "timestamp": 1791568963000 + idx,
                    },
                ).json()
                self.assertIsNone(comp_res["auto_order"])
                self.assertIsNone(
                    comp_res["answer_draft"],
                    f"Compliment should not trigger auto-reply: {comp_text}",
                )

    def test_unaccented_vietnamese_quantity_disambiguation_and_followup_shipping(self) -> None:
        facade = build_repository_backed_facade()
        ctx = TenantContext.system_context("default-store")
        run_coroutine_sync(seed_demo_store_data(facade, ctx, session_id="youtube-live"))
        app = create_app(db_facade=facade)

        with InProcessAppClient(app) as client:
            # 1. Unaccented Vietnamese and natural order closing phrases
            unaccented_cases = [
                ("unacc-1", "chot 1 den ban", "SYN-PROD-001", 1),
                ("unacc-2", "dat 1 coc su", "SYN-PROD-002", 1),
                ("unacc-3", "lay 1 quat cam tay", "SYN-PROD-009", 1),
                ("unacc-4", "mua 1 binh giu nhiet", "SYN-PROD-012", 1),
                ("unacc-5", "cho em 1 den ban", "SYN-PROD-001", 1),
            ]
            for cid, text, expected_pid, expected_qty in unaccented_cases:
                res = client.post(
                    "/api/plugin/comments",
                    json={
                        "id": cid,
                        "sender": f"@UnaccBuyer_{cid}",
                        "text": text,
                        "authorType": "viewer",
                        "timestamp": 1791568970000,
                    },
                ).json()
                self.assertIsNotNone(res["auto_order"], f"Failed to create order for: {text}")
                self.assertEqual(res["auto_order"]["items"][0]["product_id"], expected_pid)
                self.assertEqual(res["auto_order"]["items"][0]["quantity"], expected_qty)

            # 2. Quantity disambiguation from street numbers, product codes, district numbers, and unit suffixes (2x, 3c)
            disambig_cases = [
                ("dis-1", "mua đèn bàn địa chỉ 123 Nguyễn Huệ Q1 SĐT 0912345678", "SYN-PROD-001", 1, "confirmed"),
                ("dis-2", "chốt đèn bàn giao tới 15 Lê Lợi 0912345678", "SYN-PROD-001", 1, "confirmed"),
                ("dis-3", "buy SYN-PROD-007", "SYN-PROD-007", 1, "pending"),
                ("dis-4", "mua đèn bàn ở quận 3", "SYN-PROD-001", 1, "pending"),
                ("dis-5", "mua 2x đèn bàn", "SYN-PROD-001", 2, "pending"),
                ("dis-6", "chốt 3c cốc sứ", "SYN-PROD-002", 3, "pending"),
            ]
            for cid, text, expected_pid, expected_qty, expected_status in disambig_cases:
                res = client.post(
                    "/api/plugin/comments",
                    json={
                        "id": cid,
                        "sender": f"@DisBuyer_{cid}",
                        "text": text,
                        "authorType": "viewer",
                        "timestamp": 1791568975000,
                    },
                ).json()
                self.assertIsNotNone(res["auto_order"], f"Failed on: {text}")
                self.assertEqual(res["auto_order"]["items"][0]["product_id"], expected_pid)
                self.assertEqual(
                    res["auto_order"]["items"][0]["quantity"],
                    expected_qty,
                    f"Wrong quantity parsed for: {text}",
                )
                self.assertEqual(res["auto_order"]["status"], expected_status)

            # 3. English false positives ("huge fan", "my son") must NOT create orders
            for idx, fp_en in enumerate(
                [
                    "I am a huge fan, give me a shoutout!",
                    "my son loves this livestream, I want to buy something later",
                ]
            ):
                fp_res = client.post(
                    "/api/plugin/comments",
                    json={
                        "id": f"fp-en-{idx}",
                        "sender": f"@EnViewer_{idx}",
                        "text": fp_en,
                        "authorType": "viewer",
                        "timestamp": 1791568980000 + idx,
                    },
                ).json()
                self.assertIsNone(fp_res["auto_order"], f"False positive order created for: {fp_en}")

            # 4. Comma-separated address, dot/space phone, and follow-up shipping update on pending order
            p_ord = client.post(
                "/api/plugin/comments",
                json={
                    "id": "followup-step-1",
                    "sender": "@FollowUpViewer",
                    "text": "chốt 1 cốc sứ",
                    "authorType": "viewer",
                    "timestamp": 1791568985000,
                },
            ).json()
            self.assertIsNotNone(p_ord["auto_order"])
            self.assertEqual(p_ord["auto_order"]["status"], "pending")
            pending_order_id = p_ord["auto_order"]["order_id"]

            # Follow-up standalone phone (with dots) + address (with commas) upgrades the pending order to confirmed + cod
            u_ord = client.post(
                "/api/plugin/comments",
                json={
                    "id": "followup-step-2",
                    "sender": "@FollowUpViewer",
                    "text": "SĐT 0912.345.678 địa chỉ: 123 Nguyễn Huệ, Quận 1, TP HCM",
                    "authorType": "viewer",
                    "timestamp": 1791568986000,
                },
            ).json()
            self.assertIsNotNone(u_ord["auto_order"])
            self.assertEqual(u_ord["auto_order"]["order_id"], pending_order_id)
            self.assertEqual(u_ord["auto_order"]["status"], "confirmed")
            self.assertEqual(u_ord["auto_order"]["payment_status"], "cod")
            self.assertIn("123 Nguyễn Huệ, Quận 1, TP HCM", u_ord["auto_order"]["shipping_address"])
            self.assertIn("0912345678", u_ord["auto_order"]["shipping_address"])

            # 5. Dashboard Order Status Management API (Confirm / Cancel & Restore Stock)
            cancel_res = client.post(
                f"/api/store/orders/{pending_order_id}/status",
                json={"status": "cancelled"},
            )
            self.assertEqual(cancel_res.status_code, 200)
            self.assertEqual(cancel_res.json()["order"]["status"], "cancelled")

    def test_vietnamese_homophone_quantities_and_bidirectional_address_splitting(self) -> None:
        facade = build_repository_backed_facade()
        ctx = TenantContext.system_context("default-store")
        run_coroutine_sync(seed_demo_store_data(facade, ctx, session_id="youtube-live"))
        app = create_app(db_facade=facade)

        with InProcessAppClient(app) as client:
            # 1. Vietnamese homophones ("không" in "mic không dây" / "được không", "ba" = dad, "nam" = male/South, "sau" = later)
            homophone_cases = [
                ("homo-1", "mua mic không dây", "SYN-PROD-010", 1),
                ("homo-2", "chốt đèn bàn được không shop?", "SYN-PROD-001", 1),
                ("homo-3", "mua đèn bàn tặng ba mẹ", "SYN-PROD-001", 1),
                ("homo-4", "mua khăn choàng cho nam", "SYN-PROD-005", 1),
                ("homo-5", "mua khan choang cho nam", "SYN-PROD-005", 1),
                ("homo-6", "mua đèn bàn hôm sau giao nhé", "SYN-PROD-001", 1),
                ("homo-7", "mua hai cái đèn bàn", "SYN-PROD-001", 2),
                ("homo-8", "chốt ba cái cốc sứ", "SYN-PROD-002", 3),
                ("homo-9", "mua năm cái quạt cầm tay", "SYN-PROD-009", 5),
                ("homo-10", "mua nam cai quat cam tay", "SYN-PROD-009", 5),
                ("homo-11", "dat sau cai binh giu nhiet", "SYN-PROD-012", 6),
            ]
            for cid, text, expected_pid, expected_qty in homophone_cases:
                res = client.post(
                    "/api/plugin/comments",
                    json={
                        "id": cid,
                        "sender": f"@HomoBuyer_{cid}",
                        "text": text,
                        "authorType": "viewer",
                        "timestamp": 1791568990000,
                    },
                ).json()
                self.assertIsNotNone(res["auto_order"], f"Order should be created for: {text}")
                self.assertEqual(res["auto_order"]["items"][0]["product_id"], expected_pid)
                self.assertEqual(
                    res["auto_order"]["items"][0]["quantity"],
                    expected_qty,
                    f"Homophone mis-parsed quantity for: {text}",
                )

            # 2. Address written BEFORE product/quantity ("Địa chỉ ..., chốt 2 đèn bàn", "Giao tới ... cho mình 2 cốc sứ")
            addr_first_1 = client.post(
                "/api/plugin/comments",
                json={
                    "id": "addr-first-1",
                    "sender": "@AddrFirstBuyer1",
                    "text": "Địa chỉ: 123 Nguyễn Huệ, Quận 1, TP HCM, SĐT 0912345678, chốt 2 đèn bàn",
                    "authorType": "viewer",
                    "timestamp": 1791568995000,
                },
            ).json()
            self.assertIsNotNone(addr_first_1["auto_order"])
            self.assertEqual(addr_first_1["auto_order"]["status"], "confirmed")
            self.assertEqual(addr_first_1["auto_order"]["items"][0]["product_id"], "SYN-PROD-001")
            self.assertEqual(addr_first_1["auto_order"]["items"][0]["quantity"], 2)
            self.assertEqual(
                addr_first_1["auto_order"]["shipping_address"],
                "123 Nguyễn Huệ, Quận 1, TP HCM (Phone: 0912345678)",
            )

            addr_first_2 = client.post(
                "/api/plugin/comments",
                json={
                    "id": "addr-first-2",
                    "sender": "@AddrFirstBuyer2",
                    "text": "Giao tới 15 Lê Lợi Quận 1 cho mình 2 cốc sứ và 1 đèn bàn SĐT 0912345678",
                    "authorType": "viewer",
                    "timestamp": 1791568996000,
                },
            ).json()
            self.assertIsNotNone(addr_first_2["auto_order"])
            self.assertEqual(addr_first_2["auto_order"]["status"], "confirmed")
            self.assertEqual(len(addr_first_2["auto_order"]["items"]), 2)
            self.assertEqual(addr_first_2["auto_order"]["items"][0]["product_id"], "SYN-PROD-002")
            self.assertEqual(addr_first_2["auto_order"]["items"][0]["quantity"], 2)
            self.assertEqual(addr_first_2["auto_order"]["items"][1]["product_id"], "SYN-PROD-001")
            self.assertEqual(addr_first_2["auto_order"]["items"][1]["quantity"], 1)
            self.assertEqual(
                addr_first_2["auto_order"]["shipping_address"],
                "15 Lê Lợi Quận 1 (Phone: 0912345678)",
            )

            # 3. Deferred address ("mua 1 đèn bàn, địa chỉ mình nhắn sau nhé") must stay pending (not save "mình nhắn sau nhé" as address)
            deferred_addr = client.post(
                "/api/plugin/comments",
                json={
                    "id": "addr-deferred-1",
                    "sender": "@DeferredBuyer",
                    "text": "mua 1 đèn bàn, địa chỉ mình nhắn sau nhé",
                    "authorType": "viewer",
                    "timestamp": 1791568997000,
                },
            ).json()
            self.assertIsNotNone(deferred_addr["auto_order"])
            self.assertEqual(deferred_addr["auto_order"]["status"], "pending")
            self.assertEqual(deferred_addr["auto_order"]["items"][0]["quantity"], 1)
            self.assertTrue(
                deferred_addr["auto_order"]["shipping_address"].startswith("Pending")
            )


if __name__ == "__main__":
    unittest.main()
