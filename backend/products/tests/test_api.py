from __future__ import annotations

import hashlib
import json
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from products.errors import ProductsApiError
from products.import_service import IMPORT_VERSION, import_shipping_rates, record_rejection
from products.models import (
    ProductDataRevision,
    ProductImportAttempt,
    ProductInventoryProjection,
    ProductInventoryProjectionControl,
    ProductRawUploadChunk,
    ProductShippingRate,
    ProductShippingRateImportBatch,
    ProductWriteAuthority,
)
from products.projection import activate_sync, begin_sync, stage_page
from products.query import normalize_options, product_summary
from products.uploads import (
    CHUNK_SIZE_BYTES,
    begin_upload,
    claim_upload,
    finish_upload,
    read_chunk,
    receive_chunk,
)
from sales.auth import Principal
from sales.models import ErpProductMaster
from sales.tests.factories import TEST_SECRET, signed_headers


def payload(*rows: tuple[str, int, int], raw_seed: str = "source") -> dict[str, object]:
    return {
        "version": IMPORT_VERSION,
        "kind": "import",
        "fileName": "SKU累计.xlsx",
        "fileSizeBytes": 1024,
        "rawFileHash": hashlib.sha256(raw_seed.encode()).hexdigest(),
        "sheetName": "SKU累计",
        "sourceRowCount": len(rows),
        "duplicateCount": 0,
        "rows": [
            {
                "productCode": code,
                "shippingRatePpt": rate,
                "sourceRowNumber": source_row,
            }
            for code, rate, source_row in rows
        ],
        "warnings": [],
        "totals": {"actualAmountCents": 100_00, "shippingFeeCents": 10_00},
    }


class ProductsDomainTests(TestCase):
    def setUp(self) -> None:
        ProductWriteAuthority.objects.filter(id=1).update(
            status="postgres",
            authority_epoch=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            cutover_id="products-test-cutover",
            migration_verify_run_id="products-test-migration",
            activated_at=timezone.now(),
        )

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_import_api_is_atomic_business_idempotent_and_request_replay_safe(self) -> None:
        first_payload = payload(("SKU-A", 50_000_000_000, 2), ("SKU-B", 120_000_000_000, 3))
        body = json.dumps(first_payload, ensure_ascii=False, separators=(",", ":")).encode()
        response = self.client.post(
            "/api/products/imports",
            data=body,
            content_type="application/json; charset=utf-8",
            headers=signed_headers(
                "/api/products/imports",
                method="POST",
                body=body,
                request_id="products-import-1",
            ),
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["status"], "imported")
        self.assertEqual(ProductShippingRate.objects.count(), 2)
        self.assertEqual(ProductDataRevision.objects.get(domain="products").revision, 1)

        replay = self.client.post(
            "/api/products/imports",
            data=body,
            content_type="application/json; charset=utf-8",
            headers=signed_headers(
                "/api/products/imports",
                method="POST",
                body=body,
                request_id="products-import-1",
            ),
        )
        self.assertEqual(replay.status_code, 201, replay.content)
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        self.assertEqual(ProductShippingRateImportBatch.objects.count(), 1)

        duplicate = import_shipping_rates(
            payload(("SKU-B", 120_000_000_000, 30), ("SKU-A", 50_000_000_000, 20), raw_seed="resaved"),
            "admin@example.test",
        )
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(ProductShippingRateImportBatch.objects.count(), 1)

        replacement = import_shipping_rates(
            payload(("SKU-A", 80_000_000_000, 2), ("SKU-C", -200_000_000_000, 4), raw_seed="changed"),
            "admin@example.test",
        )
        self.assertEqual(replacement["status"], "imported")
        self.assertEqual(
            list(ProductShippingRate.objects.order_by("product_code").values_list("product_code", flat=True)),
            ["SKU-A", "SKU-C"],
        )
        self.assertEqual(ProductDataRevision.objects.get(domain="products").revision, 2)

    def test_prevalidation_rejection_is_audited_without_creating_fact_batch(self) -> None:
        result = record_rejection(
            {
                "version": IMPORT_VERSION,
                "kind": "rejection",
                "fileName": "bad.xlsx",
                "fileSizeBytes": 12,
                "rawFileHash": "a" * 64,
                "errors": [{"code": "INVALID_HEADER", "message": "缺少固定表头"}],
                "warnings": [],
            },
            "admin@example.test",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(ProductShippingRateImportBatch.objects.count(), 0)
        attempt = ProductImportAttempt.objects.get()
        self.assertEqual(attempt.outcome, "rejected")
        self.assertEqual(attempt.scope_key, "")

    def test_duplicate_retry_fails_closed_when_current_facts_are_corrupted(self) -> None:
        current = payload(("SKU-A", 50_000_000_000, 2), ("SKU-B", 120_000_000_000, 3))
        import_shipping_rates(current, "admin@example.test")
        ProductShippingRate.objects.filter(product_code="SKU-A").update(shipping_rate="0.99")

        with self.assertRaisesRegex(ProductsApiError, "批次与事实摘要不一致"):
            import_shipping_rates(
                payload(
                    ("SKU-B", 120_000_000_000, 30),
                    ("SKU-A", 50_000_000_000, 20),
                    raw_seed="same-content-after-corruption",
                ),
                "admin@example.test",
            )

        self.assertEqual(ProductShippingRateImportBatch.objects.count(), 1)
        self.assertEqual(
            str(ProductShippingRate.objects.get(product_code="SKU-A").shipping_rate),
            "0.990000000000",
        )
        self.assertEqual(ProductDataRevision.objects.get(domain="products").revision, 1)
        self.assertEqual(
            ProductImportAttempt.objects.order_by("-created_at").first().outcome,
            "failed",
        )

    def test_inventory_projection_is_ordered_owner_fenced_and_atomically_activated(self) -> None:
        rows = [
            {
                "productCode": "SKU-A", "brand": "A", "availableQuantity": 2,
                "knownStockValueCents": 1000, "pricedAvailableQuantity": 2,
            },
            {
                "productCode": "SKU-B", "brand": "B", "availableQuantity": 3,
                "knownStockValueCents": 600, "pricedAvailableQuantity": 1,
            },
        ]
        revision = hashlib.sha256(
            (
                "product-inventory-projection-v1\ninventory-batch-1\n2026-08-31\n"
                + json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
            ).encode()
        ).hexdigest()
        owner = "projection-owner-token"
        begin = begin_sync(
            {
                "action": "begin_sync",
                "projectionRevision": revision,
                "sourceBatchId": "inventory-batch-1",
                "snapshotDate": "2026-08-31",
                "totalRows": 2,
                "ownerToken": owner,
            },
            "admin@example.test",
        )
        self.assertEqual(begin["status"], "syncing")
        with self.assertRaisesRegex(Exception, "所有权"):
            stage_page(
                {
                    "action": "stage_page",
                    "projectionRevision": revision,
                    "ownerToken": "wrong-owner",
                    "offset": 0,
                    "rows": [{
                        "productCode": "SKU-A", "brand": "A", "availableQuantity": 2,
                        "knownStockValueCents": 1000, "pricedAvailableQuantity": 2,
                    }],
                },
                "admin@example.test",
            )
        stage_page(
            {"action": "stage_page", "projectionRevision": revision, "ownerToken": owner, "offset": 0, "rows": rows},
            "admin@example.test",
        )
        activated = activate_sync(
            {"action": "activate_sync", "projectionRevision": revision, "ownerToken": owner},
            "admin@example.test",
        )
        self.assertEqual(activated["status"], "active")
        control = ProductInventoryProjectionControl.objects.get(id=1)
        self.assertEqual(control.active_revision, revision)
        self.assertEqual(control.active_total, 2)
        self.assertEqual(control.syncing_revision, "")
        self.assertEqual(ProductInventoryProjection.objects.count(), 2)
        ProductInventoryProjection.objects.filter(product_code="SKU-A").update(
            available_quantity=99
        )
        with self.assertRaisesRegex(Exception, "已激活库存投影的事实摘要不一致"):
            begin_sync(
                {
                    "action": "begin_sync",
                    "projectionRevision": revision,
                    "sourceBatchId": "inventory-batch-1",
                    "snapshotDate": "2026-08-31",
                    "totalRows": 2,
                    "ownerToken": owner,
                },
                "admin@example.test",
            )

    def test_chunk_upload_is_actor_bound_integrity_checked_and_replayable(self) -> None:
        raw = b"x" * CHUNK_SIZE_BYTES + b"tail"
        initialized = begin_upload(
            {
                "action": "init",
                "fileName": "SKU累计.xlsx",
                "fileSizeBytes": len(raw),
                "chunkCount": 2,
                "fingerprint": "fixture-upload",
            },
            "admin@example.test",
        )
        upload_id = str(initialized["upload"]["id"])
        receive_chunk(upload_id, 0, raw[:CHUNK_SIZE_BYTES], "admin@example.test")
        receive_chunk(upload_id, 1, raw[CHUNK_SIZE_BYTES:], "admin@example.test")
        claim = claim_upload({"action": "claim", "uploadId": upload_id}, "admin@example.test")
        token = str(claim["ownerToken"])
        replayed_claim = claim_upload({"action": "claim", "uploadId": upload_id}, "admin@example.test")
        self.assertEqual(replayed_claim["ownerToken"], token)
        first, digest = read_chunk(upload_id, 0, token, "admin@example.test")
        self.assertEqual(first, raw[:CHUNK_SIZE_BYTES])
        self.assertEqual(digest, hashlib.sha256(first).hexdigest())
        with self.assertRaisesRegex(Exception, "不属于当前操作者"):
            read_chunk(upload_id, 0, token, "other@example.test")
        result = {"ok": True, "status": "imported", "batch": {"id": "batch-1"}}
        finish_upload(
            {"action": "finish", "uploadId": upload_id, "ownerToken": token, "result": result},
            "admin@example.test",
        )
        replay = finish_upload(
            {"action": "finish", "uploadId": upload_id, "ownerToken": token, "result": result},
            "admin@example.test",
        )
        self.assertEqual(replay["result"], result)
        self.assertEqual(ProductRawUploadChunk.objects.count(), 0)

    def test_inventory_projection_activation_recomputes_declared_digest(self) -> None:
        revision = "c" * 64
        owner = "projection-digest-owner"
        begin_sync(
            {
                "action": "begin_sync",
                "projectionRevision": revision,
                "sourceBatchId": "inventory-batch-tampered",
                "snapshotDate": "2026-08-31",
                "totalRows": 1,
                "ownerToken": owner,
            },
            "admin@example.test",
        )
        stage_page(
            {
                "action": "stage_page",
                "projectionRevision": revision,
                "ownerToken": owner,
                "offset": 0,
                "rows": [{
                    "productCode": "SKU-A", "brand": "A", "availableQuantity": 2,
                    "knownStockValueCents": 1000, "pricedAvailableQuantity": 2,
                }],
            },
            "admin@example.test",
        )
        with self.assertRaisesRegex(Exception, "内容摘要"):
            activate_sync(
                {"action": "activate_sync", "projectionRevision": revision, "ownerToken": owner},
                "admin@example.test",
            )
        control = ProductInventoryProjectionControl.objects.get(id=1)
        self.assertEqual(control.active_revision, "")
        self.assertEqual(control.syncing_revision, revision)

    @patch("products.query.sales_revision_token", return_value="7:3")
    @patch("products.query.execute_consumer_query")
    def test_summary_joins_postgres_authorities_and_preserves_snapshot(self, consumer, _revision) -> None:
        ErpProductMaster.objects.create(
            product_code="SKU-A",
            product_name="货品 A",
            brand="品牌 A",
            specification="标准",
            barcode="",
            category="类目 A",
            supplier="供应商 A",
            product_status="active",
            source_row_number=1,
            last_import_batch_id="erp-1",
        )
        ProductShippingRate.objects.create(
            product_code="SKU-A", shipping_rate="0.05", source_row_number=2,
            last_import_batch_id="shipping-1",
        )
        control = ProductInventoryProjectionControl.objects.get(id=1)
        control.active_revision = "c" * 64
        control.active_total = 1
        control.active_source_batch_id = "inventory-1"
        control.active_snapshot_date = "2026-08-31"
        control.save()
        ProductInventoryProjection.objects.create(
            projection_revision=control.active_revision,
            product_code="SKU-A",
            brand="库存品牌",
            available_quantity=2,
            known_stock_value_cents=1000,
            priced_available_quantity=2,
            source_batch_id="inventory-1",
            snapshot_date="2026-08-31",
        )

        def response(_principal, request):
            if request["operation"] == "freshness":
                return {
                    "dataStartDate": "2026-08-01",
                    "dataCutoffDate": "2026-08-31",
                    "latestBatch": {"id": "sales-1", "fileName": "sales.xlsx", "completedAt": "2026-08-31", "rowCount": 1},
                }
            return {
                "dataStartDate": "2026-08-01",
                "dataCutoffDate": "2026-08-31",
                "latestBatch": {"id": "sales-1", "fileName": "sales.xlsx", "completedAt": "2026-08-31", "rowCount": 1},
                "rows": [{
                    "productCode": "SKU-A", "productName": "旧货品名", "specification": "", "category": "旧类目",
                    "supplier": "", "netQuantity": 2, "grossSalesCents": 2000, "refundAmountCents": 0,
                    "netSalesCents": 2000, "costCents": 1000, "feeCents": 100, "grossProfitCents": 1000,
                    "absoluteQuantity": 2, "absoluteCostCents": 1000,
                    "outlets": [{"platform": "京东", "shopName": "测试店铺", "channel": "京东"}],
                }],
                "outletOptions": [{"platform": "京东", "shopName": "测试店铺", "channel": "京东"}],
                "truncated": False,
            }

        consumer.side_effect = response
        principal = Principal("admin@example.test", "Admin", "admin", None)
        full = product_summary(principal, {"range": "last30", "page": 1, "pageSize": 20})
        self.assertEqual(full["projection"], "full")
        self.assertEqual(full["items"][0]["productName"], "货品 A")
        self.assertEqual(full["items"][0]["shippingRate"], 0.05)
        self.assertEqual(full["items"][0]["stockValueCents"], 1000)
        self.assertEqual(full["metrics"]["grossMarginRate"], 0.5)
        page = product_summary(principal, {
            "range": "last30", "projection": "page", "page": 1, "pageSize": 20,
            "expectedSnapshotToken": full["snapshotToken"],
        })
        self.assertEqual(page["projection"], "page")
        self.assertEqual(page["snapshotToken"], full["snapshotToken"])

        # A pasted list must include late codes, not just its first eight terms.
        long_query = ",\n".join([f"ABSENT-{index:03d}" for index in range(80)] + ["SKU-A"])
        self.assertGreater(len(long_query), 100)
        self.assertLessEqual(len(long_query), 1000)
        filtered = product_summary(principal, {"query": long_query})
        self.assertEqual([item["productCode"] for item in filtered["items"]], ["SKU-A"])
        self.assertEqual(filtered["filtersApplied"]["query"], long_query)

    def test_summary_search_accepts_1000_characters_and_rejects_overflow(self) -> None:
        for length in (101, 1000):
            self.assertEqual(normalize_options({"query": "码" * length})["query"], "码" * length)
        with self.assertRaisesMessage(ProductsApiError, "1000"):
            normalize_options({"query": "码" * 1001})

    def test_explicit_last30_ignores_legacy_days_while_implicit_range_accepts_it(self) -> None:
        explicit = normalize_options({"range": "last30", "days": 90})
        implicit = normalize_options({"days": 90})
        self.assertTrue(explicit["rangeExplicit"])
        self.assertFalse(implicit["rangeExplicit"])
