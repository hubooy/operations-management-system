from __future__ import annotations

import uuid
from datetime import datetime, timezone as datetime_timezone
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from market.annotations import _review, _select_filtered, _update_review
from market.errors import MarketApiError
from market.models import (
    MarketAnnotationCommitReceipt, MarketAnnotationItem, MarketAnnotationJob,
    MarketAnnotationPromptVersion, MarketDataRevision, MarketMasterAuditLog,
    MarketImageCache, MarketPriceSnapshot, MarketRankingEntry, MarketSkuAnnotation,
    MarketSubcategoryTaxonomy, MarketWriteAuthority, MarketWriteRequestReceipt,
)
from sales.tests.factories import TEST_SECRET, signed_headers
from sales.auth import Principal

from .factories import body_bytes
from .test_api import AUTHORITY_EPOCH, CUTOVER_ID


@override_settings(MARKET_WRITE_AUTHORITY_EPOCH=AUTHORITY_EPOCH, MARKET_WRITE_CUTOVER_ID=CUTOVER_ID)
@patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
class MarketAnnotationCommitReceiptTests(TestCase):
    def setUp(self):
        MarketWriteAuthority.objects.filter(id=1).update(
            status="postgres", authority_epoch=uuid.UUID(AUTHORITY_EPOCH), cutover_id=CUTOVER_ID,
            migration_verify_run_id="market-test-migration", activated_at=timezone.now(),
        )
        MarketSubcategoryTaxonomy.objects.create(id="receipt-taxonomy", category="净水", subcategory="台式")
        MarketAnnotationPromptVersion.objects.create(
            id="receipt-prompt", category="净水", version=1, source="manual", status="active",
            segments_json=["台式"], prompt_body="fixture", created_by="admin@example.test",
        )
        MarketAnnotationJob.objects.create(
            id="receipt-job", category="净水", prompt_version_id="receipt-prompt", executor="cloud",
            status="running", created_by="admin@example.test",
        )
        self.identity = dict(category="净水", scope="全部", sku_code="receipt-sku", ranking_dimension="SKU", image_content_sha256="a" * 64)
        self.old_time = datetime(2026, 8, 1, 2, 3, 4, 123456, tzinfo=datetime_timezone.utc)
        self.annotation = MarketSkuAnnotation.objects.create(
            id="existing-annotation", **self.identity, segment="旧分类", image_price_cents=None,
            source_job_item_id="historical-item", prompt_version_id="historical-prompt",
            reviewed_by="historical@example.test", reviewed_at=self.old_time, version=4,
        )
        MarketSkuAnnotation.objects.filter(pk=self.annotation.pk).update(created_at=self.old_time, updated_at=self.old_time)
        self.before = MarketSkuAnnotation.objects.values().get(pk=self.annotation.pk)
        # Different monthly candidates update the same formal image annotation.
        for month in ("2026-08", "2026-09"):
            MarketPriceSnapshot.objects.create(id=month, **self.identity, month=month)
            MarketRankingEntry.objects.create(
                natural_key=month, category="净水", scope="全部", sku_code="receipt-sku",
                period_start=month + "-01", period_end=month + "-12", source_row_number=1,
                subcategory="旧分类", last_import_batch_id="fixture",
            )
            MarketAnnotationItem.objects.create(
                id=month, job_id="receipt-job", **self.identity, month=month,
                status="approved", selected=True, version=1, reviewed_segment="台式",
                reviewed_image_price_cents=19900, reviewed_price_type="标准售价",
            )

    def commit(self, request_id="receipt-request", *, key="receipt-batch", role="admin"):
        path = "/api/market/commands"
        body = body_bytes({"contractVersion": "market-command-v1", "domain": "annotations", "command": {
            "action": "commit_selected", "aggregateJobs": True, "idempotencyKey": key,
        }})
        return self.client.post(path, data=body, content_type="application/json", headers=signed_headers(
            path, method="POST", body=body, request_id=request_id, role=role,
        ))

    def test_existing_annotation_dates_are_preserved_and_commit_replays_without_updates(self):
        response = self.commit()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["result"]["committed"], 2)
        first = MarketAnnotationCommitReceipt.objects.get(job_item_id="2026-08")
        expected = {key: value.isoformat().replace("+00:00", "Z") if isinstance(value, datetime) else value for key, value in self.before.items()}
        self.assertEqual(first.before_json, expected)
        self.assertIsNone(first.before_json["image_price_cents"])
        self.assertEqual(first.before_json["version"], 4)
        second = MarketAnnotationCommitReceipt.objects.get(job_item_id="2026-09")
        self.assertEqual(second.before_json["version"], 5)
        self.assertEqual(second.before_json["image_price_cents"], 19900)
        self.assertEqual(second.before_json["created_at"], expected["created_at"])
        self.assertTrue(second.before_json["reviewed_at"].endswith("Z"))
        self.annotation.refresh_from_db()
        self.assertEqual(self.annotation.version, 6)
        revision = MarketDataRevision.objects.values().get(domain="market")
        replay = self.commit()
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        duplicate = self.commit("receipt-business-replay")
        self.assertEqual(duplicate.status_code, 200, duplicate.content)
        self.assertTrue(duplicate.json()["result"]["duplicate"])
        self.annotation.refresh_from_db()
        self.assertEqual(self.annotation.version, 6)
        self.assertEqual(MarketDataRevision.objects.values().get(domain="market"), revision)
        self.assertEqual(MarketAnnotationCommitReceipt.objects.count(), 3)
        self.assertEqual(MarketAnnotationItem.objects.filter(status="committed", selected=False).count(), 2)
        self.assertEqual(MarketPriceSnapshot.objects.filter(confirmed_market_price_cents=19900).count(), 2)

    def test_receipt_failure_rolls_back_entire_batch_and_same_request_can_retry(self):
        revision = MarketDataRevision.objects.values().get(domain="market")
        create = MarketAnnotationCommitReceipt.objects.create

        def fail_second(**kwargs):
            if kwargs["job_item_id"] == "2026-09":
                raise RuntimeError("synthetic receipt write failure")
            return create(**kwargs)

        with patch("market.annotations.MarketAnnotationCommitReceipt.objects.create", side_effect=fail_second):
            response = self.commit()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(MarketSkuAnnotation.objects.values().get(pk=self.annotation.pk), self.before)
        self.assertEqual(MarketAnnotationItem.objects.filter(status="approved", selected=True, version=1).count(), 2)
        self.assertEqual(MarketPriceSnapshot.objects.filter(confirmed_market_price_cents__isnull=True).count(), 2)
        self.assertEqual(MarketRankingEntry.objects.filter(subcategory="旧分类").count(), 2)
        self.assertFalse(MarketAnnotationCommitReceipt.objects.exists())
        self.assertFalse(MarketWriteRequestReceipt.objects.filter(request_id="receipt-request").exists())
        self.assertFalse(MarketMasterAuditLog.objects.exists())
        self.assertEqual(MarketDataRevision.objects.values().get(domain="market"), revision)
        retry = self.commit()
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertEqual(retry.json()["result"]["committed"], 2)

    def test_operator_cannot_commit_existing_annotation(self):
        response = self.commit(role="operator")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(MarketSkuAnnotation.objects.values().get(pk=self.annotation.pk), self.before)
        self.assertFalse(MarketAnnotationCommitReceipt.objects.exists())

    def test_changed_image_does_not_block_valid_selected_candidate_or_replay(self):
        MarketPriceSnapshot.objects.filter(pk="2026-08").update(image_content_sha256="b" * 64)
        stale_before = MarketAnnotationItem.objects.values().get(pk="2026-08")
        response = self.commit()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["result"]["committed"], 1)
        self.assertEqual(response.json()["result"]["skippedStaleCount"], 1)
        self.assertFalse(response.json()["result"]["hasMore"])
        self.assertEqual(MarketAnnotationItem.objects.values().get(pk="2026-08"), stale_before)
        self.assertIsNone(MarketPriceSnapshot.objects.get(pk="2026-08").confirmed_market_price_cents)
        self.assertEqual(MarketPriceSnapshot.objects.get(pk="2026-09").confirmed_market_price_cents, 19900)
        self.assertFalse(MarketAnnotationCommitReceipt.objects.filter(job_item_id="2026-08").exists())
        self.assertEqual(self.commit("stale-business-replay").json()["result"]["skippedStaleCount"], 1)
        self.annotation.refresh_from_db()
        self.assertEqual(self.annotation.version, 5)

    def test_all_stale_is_bounded_noop_with_receipt_and_preserves_candidates(self):
        MarketPriceSnapshot.objects.all().update(image_content_sha256="b" * 64)
        before = list(MarketAnnotationItem.objects.order_by("id").values())
        response = self.commit()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["result"]["committed"], 0)
        self.assertEqual(response.json()["result"]["skippedStaleCount"], 2)
        self.assertFalse(response.json()["result"]["hasMore"])
        self.assertEqual(list(MarketAnnotationItem.objects.order_by("id").values()), before)
        self.assertEqual(MarketSkuAnnotation.objects.values().get(pk=self.annotation.pk), self.before)
        self.assertEqual(self.commit("all-stale-replay").json()["result"]["skippedStaleCount"], 2)

    def test_snapshot_validity_is_shared_by_rows_counts_selection_and_review(self):
        principal = Principal("admin@example.test", "test", "admin", None)
        MarketPriceSnapshot.objects.filter(pk="2026-08").update(image_content_sha256="b" * 64)
        review = _review({"aggregateJobs": True})
        self.assertEqual(review["selection"]["scopeSelectedCount"], 1)
        self.assertEqual(review["selection"]["filteredReviewableCount"], 1)
        self.assertEqual(review["selection"]["staleSelectedCount"], 1)
        self.assertEqual({r["id"]: r["snapshotValid"] for r in review["items"]}, {"2026-08": False, "2026-09": True})
        _select_filtered({"aggregateJobs": True, "selected": False}, principal)
        self.assertFalse(MarketAnnotationItem.objects.filter(selected=True).exists())
        self.assertEqual(_select_filtered({"aggregateJobs": True, "selected": True}, principal)["changed"], 1)
        stale = MarketAnnotationItem.objects.get(pk="2026-08")
        with self.assertRaises(MarketApiError) as caught:
            _update_review({"jobId": stale.job_id, "updates": [{"id": stale.id, "version": stale.version, "segment": "台式", "selected": True}]}, principal)
        self.assertEqual(caught.exception.status, 409)

    def test_missing_snapshot_or_ranking_does_not_admit_other_month(self):
        MarketPriceSnapshot.objects.filter(pk="2026-08").delete()
        MarketRankingEntry.objects.filter(period_end__startswith="2026-09").delete()
        response = self.commit()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["result"]["committed"], 0)
        self.assertEqual(response.json()["result"]["skippedStaleCount"], 2)

    def test_ready_cache_hash_wins_but_empty_cache_hash_uses_snapshot(self):
        MarketPriceSnapshot.objects.filter(pk="2026-08").update(image_url="https://example.test/current.png")
        MarketImageCache.objects.create(source_url="https://example.test/current.png", status="ready", content_sha256="b" * 64)
        self.assertEqual(_review({"aggregateJobs": True})["selection"]["filteredReviewableCount"], 1)
        MarketImageCache.objects.all().update(content_sha256="")
        self.assertEqual(_review({"aggregateJobs": True})["selection"]["filteredReviewableCount"], 2)

    def test_image_change_after_batch_selection_is_skipped_without_rolling_back_valid_work(self):
        from market.annotations import _current_snapshot

        def change_during_commit(item, **kwargs):
            if item.id == "2026-09":
                MarketPriceSnapshot.objects.filter(pk=item.id).update(image_content_sha256="b" * 64)
            return _current_snapshot(item, **kwargs)

        with patch("market.annotations._current_snapshot", side_effect=change_during_commit):
            response = self.commit()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["result"]["committed"], 1)
        self.assertEqual(response.json()["result"]["skippedStaleCount"], 1)
        self.assertEqual(MarketAnnotationItem.objects.get(pk="2026-09").status, "approved")
