"""Automated end-to-end and long-session stability tests for Livestream AI."""

from __future__ import annotations

import unittest

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

        self.assertEqual(db["storage_mode"], "mongodb")
        self.assertEqual(db["seeded_products_count"], 12)
        self.assertGreaterEqual(db["seeded_orders_count"], 1)
        self.assertEqual(db["thread_seller_action_recorded"], "edited")
        self.assertTrue(db["cold_start_repository_hydration_passed"])

        self.assertEqual(ext["auto_replied_badge_occurrences"], 0)
        self.assertGreaterEqual(ext["draft_ready_badge_occurrences"], 1)
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


if __name__ == "__main__":
    unittest.main()
