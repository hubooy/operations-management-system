from __future__ import annotations

import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from market.models import (
    MarketAnnotationItem,
    MarketAnnotationPromptVersion,
    MarketImageCache,
    MarketNetshopProjection,
    MarketNetshopProjectionControl,
    MarketPriceSnapshot,
    MarketSkuAnnotation,
    MarketSubcategoryTaxonomy,
    MarketWriteAuthority,
)
from sales.tests.factories import TEST_SECRET, signed_headers

from .factories import body_bytes, market_row, prepared_payload
from .test_api import AUTHORITY_EPOCH, CUTOVER_ID


@override_settings(
    MARKET_WRITE_AUTHORITY_EPOCH=AUTHORITY_EPOCH,
    MARKET_WRITE_CUTOVER_ID=CUTOVER_ID,
)
class MarketCommandContractTests(TestCase):
    def setUp(self) -> None:
        MarketWriteAuthority.objects.filter(id=1).update(
            status="postgres",
            authority_epoch=uuid.UUID(AUTHORITY_EPOCH),
            cutover_id=CUTOVER_ID,
            migration_verify_run_id="market-test-migration",
            activated_at=timezone.now(),
        )

    def post(self, path: str, payload: dict[str, object], request_id: str, *, role: str = "admin"):
        body = body_bytes(payload)
        return self.client.post(
            path,
            data=body,
            content_type="application/json; charset=utf-8",
            headers=signed_headers(path, method="POST", body=body, request_id=request_id, role=role),
        )

    def command(self, domain: str, command: dict[str, object], request_id: str, *, role: str = "admin"):
        return self.post(
            "/api/market/commands",
            {"contractVersion": "market-command-v1", "domain": domain, "command": command},
            request_id,
            role=role,
        )

    def import_fixture(self) -> None:
        response = self.post(
            "/api/market/imports",
            prepared_payload(market_row()),
            "market-command-fixture",
        )
        self.assertEqual(response.status_code, 201, response.content)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_formal_price_confirmation_is_image_fenced_and_replay_safe(self) -> None:
        self.import_fixture()
        snapshot = MarketPriceSnapshot.objects.get()
        snapshot.image_content_sha256 = "a" * 64
        snapshot.save()
        command = {
            "action": "confirm_price",
            "category": snapshot.category,
            "scope": snapshot.scope,
            "skuCode": snapshot.sku_code,
            "rankingDimension": snapshot.ranking_dimension,
            "month": snapshot.month,
            "imageContentSha256": "a" * 64,
            "priceCents": 188_800,
            "priceType": "标准售价",
            "priceLowCents": None,
            "priceHighCents": None,
            "note": "人工复核",
        }
        first = self.command("master", command, "market-confirm-price")
        self.assertEqual(first.status_code, 200, first.content)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.confirmed_market_price_cents, 188_800)
        replay = self.command("master", command, "market-confirm-price")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        wrong = {**command, "imageContentSha256": "b" * 64, "priceCents": 199_900}
        rejected = self.command("master", wrong, "market-confirm-price-wrong-image")
        self.assertEqual(rejected.status_code, 409)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.confirmed_market_price_cents, 188_800)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_annotation_commit_rechecks_exact_snapshot_and_updates_price_atomically(self) -> None:
        self.import_fixture()
        snapshot = MarketPriceSnapshot.objects.get()
        snapshot.image_content_sha256 = "c" * 64
        snapshot.save()
        MarketSubcategoryTaxonomy.objects.update_or_create(
            category=snapshot.category,
            subcategory="商用直饮机",
            defaults={
                "created_by": "test",
                "updated_by": "test",
            },
        )
        prompt = MarketAnnotationPromptVersion.objects.create(
            id="prompt-1",
            category=snapshot.category,
            version=1,
            source="manual",
            status="active",
            segments_json=["商用直饮机"],
            prompt_body="严格返回 JSON",
            created_by="admin@example.test",
        )
        created = self.command(
            "annotations",
            {
                "action": "create_job",
                "category": snapshot.category,
                "promptVersionId": prompt.id,
                "executor": "cloud",
                "modelId": "vision-model",
                "limit": 10,
            },
            "market-create-annotation-job",
            role="operator",
        )
        self.assertEqual(created.status_code, 200, created.content)
        job_id = created.json()["result"]["id"]
        run = self.command(
            "annotations",
            {"action": "set_cloud_run_state", "jobId": job_id, "state": "running"},
            "market-run-annotation-job",
            role="operator",
        )
        self.assertEqual(run.status_code, 200, run.content)
        claim = self.command(
            "annotations",
            {"action": "claim_task", "jobId": job_id, "executor": "cloud"},
            "market-claim-annotation",
            role="operator",
        )
        self.assertEqual(claim.status_code, 200, claim.content)
        task = claim.json()["result"]["task"]
        completed = self.command(
            "annotations",
            {
                "action": "complete_task",
                "itemId": task["itemId"],
                "leaseToken": task["leaseToken"],
                "result": {
                    "segment": "商用直饮机",
                    "imagePriceCents": 166_600,
                    "priceType": "标准售价",
                    "priceLowCents": None,
                    "priceHighCents": None,
                    "confidenceBps": 9200,
                    "reason": "主图完整标价",
                    "rawDigest": "d" * 64,
                    "resolvedImageUrl": snapshot.image_url,
                    "imageSource": "source",
                },
            },
            "market-complete-annotation",
            role="operator",
        )
        self.assertEqual(completed.status_code, 200, completed.content)
        item = MarketAnnotationItem.objects.get(id=task["itemId"])
        reviewed = self.command(
            "annotations",
            {
                "action": "review",
                "jobId": job_id,
                "updates": [
                    {
                        "id": item.id,
                        "version": item.version,
                        "segment": "商用直饮机",
                        "imagePriceCents": 166_600,
                        "priceType": "标准售价",
                        "priceLowCents": None,
                        "priceHighCents": None,
                        "selected": True,
                    }
                ],
            },
            "market-review-annotation",
            role="operator",
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.content)
        committed = self.command(
            "annotations",
            {
                "action": "commit",
                "jobId": job_id,
                "candidateIds": [item.id],
                "idempotencyKey": "market-annotation-idempotency-1",
            },
            "market-commit-annotation",
        )
        self.assertEqual(committed.status_code, 200, committed.content)
        self.assertEqual(MarketSkuAnnotation.objects.get().segment, "商用直饮机")
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.confirmed_market_price_cents, 166_600)
        self.assertEqual(snapshot.confirmation_status, "confirmed")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_image_cache_claim_complete_owns_only_metadata(self) -> None:
        self.import_fixture()
        created = self.command(
            "images",
            {"action": "create_image_cache_job", "batchId": ""},
            "market-image-job",
        )
        self.assertEqual(created.status_code, 200, created.content)
        job_id = created.json()["result"]["job"]["id"]
        claimed = self.command(
            "images",
            {"action": "claim_image_cache", "jobId": job_id, "limit": 4},
            "market-image-claim",
        )
        self.assertEqual(claimed.status_code, 200, claimed.content)
        result = claimed.json()["result"]
        self.assertEqual(len(result["claims"]), 1)
        claim = result["claims"][0]
        job = result["job"]
        content_hash = "e" * 64
        completed = self.command(
            "images",
            {
                "action": "complete_image_cache_claim",
                "jobId": job_id,
                "jobLeaseToken": job["leaseToken"],
                "jobEpoch": job["leaseEpoch"],
                "sourceUrl": claim["sourceUrl"],
                "claimToken": claim["claimToken"],
                "contentSha256": content_hash,
                "objectKey": f"market-images/v1/{content_hash}.jpg",
                "mimeType": "image/jpeg",
                "sizeBytes": 1200,
                "imageSource": "source",
                "errorCode": "",
                "errorMessage": "",
            },
            "market-image-complete",
        )
        self.assertEqual(completed.status_code, 200, completed.content)
        cache = MarketImageCache.objects.get()
        self.assertEqual(cache.status, "ready")
        self.assertEqual(cache.content_sha256, content_hash)
        self.assertEqual(cache.object_key, f"market-images/v1/{content_hash}.jpg")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_netshop_projection_is_paged_count_fenced_and_atomically_activated(self) -> None:
        rows = [
            {
                "projectionKey": "metric:row-1",
                "kind": "metric",
                "source": "jd_sku_daily",
                "dataset": "sku_daily",
                "platform": "京东",
                "shopName": "测试店",
                "businessDate": "2026-08-31",
                "skuId": "SKU-001",
                "spuId": "",
                "productCode": "",
                "transactionAmountCents": 123_400,
                "brand": "",
            },
            {
                "projectionKey": "brand:" + "a" * 64,
                "kind": "brand",
                "source": "",
                "dataset": "product_master",
                "platform": "",
                "shopName": "",
                "businessDate": "",
                "skuId": "",
                "spuId": "",
                "productCode": "",
                "transactionAmountCents": 0,
                "brand": "志高",
            },
        ]
        revision = "9:abcdefabcdef"
        begun = self.command(
            "projection",
            {"action": "begin_sync", "sourceRevision": revision, "total": len(rows)},
            "market-projection-begin",
        )
        self.assertEqual(begun.status_code, 200, begun.content)
        staged = self.command(
            "projection",
            {"action": "stage_page", "sourceRevision": revision, "offset": 0, "rows": rows},
            "market-projection-stage",
        )
        self.assertEqual(staged.status_code, 200, staged.content)
        self.assertEqual(staged.json()["result"]["syncingOffset"], 2)
        activated = self.command(
            "projection",
            {"action": "activate_sync", "sourceRevision": revision},
            "market-projection-activate",
        )
        self.assertEqual(activated.status_code, 200, activated.content)
        control = MarketNetshopProjectionControl.objects.get(id=1)
        self.assertEqual(control.active_revision, revision)
        self.assertEqual(control.active_total, 2)
        self.assertEqual(MarketNetshopProjection.objects.count(), 2)

        next_revision = "10:bbbbbbbbbbbb"
        self.assertEqual(
            self.command(
                "projection",
                {"action": "begin_sync", "sourceRevision": next_revision, "total": 1},
                "market-projection-next-begin",
            ).status_code,
            200,
        )
        incomplete = self.command(
            "projection",
            {"action": "activate_sync", "sourceRevision": next_revision},
            "market-projection-incomplete-activate",
        )
        self.assertEqual(incomplete.status_code, 409, incomplete.content)
        control.refresh_from_db()
        self.assertEqual(control.active_revision, revision)
        self.assertEqual(MarketNetshopProjection.objects.filter(projection_revision=revision).count(), 2)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_image_cache_job_lease_cannot_be_overwritten_by_a_second_claim(self) -> None:
        self.import_fixture()
        created = self.command(
            "images",
            {"action": "create_image_cache_job", "batchId": ""},
            "market-image-exclusive-job",
        )
        job_id = created.json()["result"]["job"]["id"]
        first = self.command(
            "images",
            {"action": "claim_image_cache", "jobId": job_id, "limit": 1},
            "market-image-exclusive-first",
        )
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(len(first.json()["result"]["claims"]), 1)
        second = self.command(
            "images",
            {"action": "claim_image_cache", "jobId": job_id, "limit": 1},
            "market-image-exclusive-second",
        )
        self.assertEqual(second.status_code, 200, second.content)
        self.assertIsNone(second.json()["result"]["job"])
