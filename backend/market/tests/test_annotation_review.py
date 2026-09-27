from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from market.annotations import _commit, _review, _select_filtered, _update_review, annotation_workspace
from market.models import (
    MarketAnnotationItem, MarketAnnotationJob, MarketAnnotationPromptVersion,
    MarketPriceSnapshot, MarketRankingEntry, MarketSkuAnnotation, MarketSubcategoryTaxonomy,
)
from sales.auth import Principal


class MarketAnnotationReviewTests(TestCase):
    def setUp(self):
        self.principal = Principal("admin@example.test", "test", "admin", None)
        self.prompt = MarketAnnotationPromptVersion.objects.create(
            id="review-prompt", category="净水", version=1, source="manual", status="active",
            segments_json=["台式", "立式", "已停用"], prompt_body="test", created_by=self.principal.email,
        )
        for segment in ["台式", "立式"]:
            MarketSubcategoryTaxonomy.objects.create(id=f"taxonomy-{segment}", category="净水", subcategory=segment, status="active")
        MarketSubcategoryTaxonomy.objects.create(id="taxonomy-disabled", category="净水", subcategory="已停用", status="disabled")
        self.job = self.make_job("review-job")

    def make_job(self, job_id, **kwargs):
        return MarketAnnotationJob.objects.create(
            id=job_id, category="净水", prompt_version_id=self.prompt.id, executor="cloud",
            created_by=self.principal.email, status=kwargs.pop("status", "running"),
            started_at=timezone.now(), **kwargs,
        )

    def item(self, item_id, *, job=None, **kwargs):
        job = job or self.job
        item = MarketAnnotationItem.objects.create(
            id=item_id, job_id=job.id, category=job.category, sku_code=item_id,
            scope="全部", month="2026-09", image_content_sha256="a" * 64,
            status=kwargs.pop("status", "review_pending"),
            reviewed_segment=kwargs.pop("reviewed_segment", "台式"),
            ai_segment=kwargs.pop("ai_segment", "台式"), **kwargs,
        )
        MarketPriceSnapshot.objects.create(
            id=item_id, category=job.category, scope=item.scope, sku_code=item_id,
            month=item.month, image_content_sha256=item.image_content_sha256,
        )
        MarketRankingEntry.objects.create(
            natural_key=item_id, category=job.category, scope=item.scope, sku_code=item_id,
            period_start="2026-09-01", period_end="2026-09-12", source_row_number=1, last_import_batch_id="fixture",
        )
        return item

    def test_filter_counts_and_selection_share_completed_item_rules(self):
        self.item("ai-ready")
        self.item("manual-ready", ai_segment="")
        self.item("still-working", status="inferencing")
        self.item("committed", status="committed", selected=True)
        self.item("invalid", reviewed_segment="不在枚举")
        self.item("retired", reviewed_segment="已停用")
        deleted = self.make_job("deleted-job", status="deleted")
        self.item("deleted-item", job=deleted)
        params = {"aggregateJobs": True, "recognitionSources": ["ai"], "storageStatuses": ["pending"]}
        review = _review(params)
        self.assertEqual(review["selection"]["filteredReviewableCount"], 1)
        self.assertEqual(review["selection"]["scopeSelectedCount"], 0)
        self.assertEqual(_select_filtered({**params, "selected": True}, self.principal)["changed"], 1)
        self.assertEqual(list(MarketAnnotationItem.objects.filter(status="approved", selected=True).values_list("id", flat=True)), ["ai-ready"])
        self.assertEqual(_review(params)["selection"]["filteredSelectedCount"], 1)
        # Selecting again is a no-op and does not invalidate another reviewer's version.
        version = MarketAnnotationItem.objects.get(id="ai-ready").version
        self.assertEqual(_select_filtered({**params, "selected": True}, self.principal)["changed"], 0)
        self.assertEqual(MarketAnnotationItem.objects.get(id="ai-ready").version, version)
        self.assertEqual(_select_filtered({"aggregateJobs": True, "storageStatuses": ["committed"], "selected": True}, self.principal)["changed"], 0)
        self.assertEqual(_select_filtered({"aggregateJobs": True, "recognitionSources": ["non_ai"], "selected": True}, self.principal)["changed"], 1)
        self.assertEqual(_select_filtered({"aggregateJobs": True, "recognitionSources": ["ai"], "selected": False}, self.principal)["changed"], 1)
        self.assertTrue(MarketAnnotationItem.objects.get(id="manual-ready").selected)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "running")

    def test_historical_review_has_authoritative_context_outside_recent_jobs(self):
        historical = self.item("historical")
        MarketAnnotationJob.objects.filter(id=self.job.id).update(created_at=timezone.now() - timedelta(days=1))
        for index in range(51):
            self.make_job(f"newer-job-{index}")
        workspace = annotation_workspace({"aggregateJobs": True}, self.principal, candidate_count=False)
        self.assertNotIn(self.job.id, {job["id"] for job in workspace["jobs"]})
        row = next(row for row in workspace["items"] if row["id"] == historical.id)
        self.assertTrue(row["reviewJobReady"])
        self.assertEqual(row["reviewSegments"], ["台式", "立式"])
        self.assertEqual(workspace["selection"]["filteredReviewableCount"], 1)

    def test_review_keeps_formal_price_type_when_ui_only_changes_price(self):
        item = self.item("edited", ai_price_type="标准售价", reviewed_price_type="标准售价", reviewed_price_low_cents=100)
        _update_review({"jobId": self.job.id, "updates": [{"id": item.id, "version": item.version, "segment": "台式", "imagePriceCents": 19900, "selected": True}]}, self.principal)
        item.refresh_from_db()
        self.assertEqual(item.reviewed_price_type, "标准售价")
        self.assertEqual(item.reviewed_price_low_cents, 100)
        self.assertEqual(item.reviewed_image_price_cents, 19900)

    def test_commit_uses_500_item_batches_while_other_items_keep_running(self):
        self.item("unfinished", status="queued")
        self.item("000-stale", status="approved", selected=True)
        MarketPriceSnapshot.objects.filter(pk="000-stale").update(image_content_sha256="b" * 64)
        self.item("unknown-result", status="failed", attempt_count=3, error_message="inference_result_unknown")
        preserved = list(MarketAnnotationItem.objects.filter(id__in=["000-stale", "unknown-result"]).order_by("id").values())
        items = []
        snapshots = []
        rankings = []
        for index in range(501):
            sku = f"batch-sku-{index:04}"
            items.append(MarketAnnotationItem(
                id=sku, job_id=self.job.id, category="净水", scope="全部", sku_code=sku,
                month="2026-09", image_content_sha256="a" * 64,
                status="approved", reviewed_segment="台式", selected=True,
            ))
            snapshots.append(MarketPriceSnapshot(
                id=sku, category="净水", scope="全部", sku_code=sku, month="2026-09", image_content_sha256="a" * 64,
            ))
            rankings.append(MarketRankingEntry(
                natural_key=sku, source_row_number=index + 1, category="净水", scope="全部", sku_code=sku,
                period_start="2026-09-01", period_end="2026-09-12", last_import_batch_id="fixture",
            ))
        MarketAnnotationItem.objects.bulk_create(items)
        MarketPriceSnapshot.objects.bulk_create(snapshots)
        MarketRankingEntry.objects.bulk_create(rankings)
        first_command = {"aggregateJobs": True, "idempotencyKey": "review-batch-1"}
        first = _commit(first_command, self.principal)
        self.assertEqual(first["committed"], 500)
        self.assertTrue(first["hasMore"])
        self.assertEqual(first["skippedStaleCount"], 1)
        replay = _commit(first_command, self.principal)
        self.assertEqual(replay["committed"], 0)
        self.assertEqual(replay["duplicates"], 500)
        self.assertTrue(replay["hasMore"])
        second = _commit({"aggregateJobs": True, "idempotencyKey": "review-batch-2"}, self.principal)
        self.assertEqual(second["committed"], 1)
        self.assertFalse(second["hasMore"])
        self.assertEqual(second["skippedStaleCount"], 1)
        self.assertEqual(list(MarketAnnotationItem.objects.filter(id__in=["000-stale", "unknown-result"]).order_by("id").values()), preserved)
        self.assertEqual(MarketSkuAnnotation.objects.count(), 501)
        self.assertEqual(MarketAnnotationItem.objects.get(id="unfinished").status, "queued")
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "running")
