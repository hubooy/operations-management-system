from __future__ import annotations

import hashlib
import uuid

from django.test import RequestFactory, TestCase
from django.utils import timezone

from inventory.errors import InventoryApiError
from inventory.import_service import _business_content_hash, import_inventory_payload
from inventory.models import (
    InventoryDataRevision,
    InventoryImportAttempt,
    InventoryImportBatch,
    InventoryStockLine,
    InventoryWriteAuthority,
)
from inventory.query import _latest_batch
from inventory.uploads import begin_upload, claim_upload, finish_upload, receive_chunk
from inventory.views import _replay_write
from inventory.warehouse_mapping_service import mapping_payload, update_mapping
from sales.auth import Principal


def stock_row(
    code: str,
    source_row: int,
    *,
    warehouse: str = "华东仓",
    available: int = 10,
    unit_cost: int = 500,
) -> dict[str, object]:
    return {
        "sourceRowNumber": source_row,
        "rowKey": f"{warehouse}\x1f{code}",
        "snapshotDate": "2026-09-01",
        "warehouse": warehouse,
        "warehouseType": "owned",
        "warehouseCategory": "selfOperated",
        "includeInInventory": True,
        "productCode": code,
        "productName": f"货品 {code}",
        "brand": "品牌甲",
        "supplier": "规格供应商甲",
        "specification": "标准装",
        "barcode": f"BAR-{code}",
        "category": "厨房电器",
        "onHandQuantity": available + 2,
        "availableQuantity": available,
        "lockedQuantity": 2,
        "inTransitQuantity": 3,
        "unitCostCents": unit_cost,
        "inventoryAgeDays": 12,
        "sales7dQuantity": 4,
        "sales30dQuantity": 18,
    }


def stock_payload(
    *rows: dict[str, object],
    raw_seed: str = "raw-1",
    excluded: int = 1,
) -> dict[str, object]:
    return {
        "dataset": "stock",
        "file": {
            "name": "分仓库存.xlsx",
            "sizeBytes": 4096,
            "rawFileHash": hashlib.sha256(raw_seed.encode()).hexdigest(),
            "sheetName": "分仓库存查询",
        },
        "snapshotDate": "2026-09-01",
        "sourceRowCount": len(rows) + excluded,
        "excludedCount": excluded,
        "rows": list(rows),
        "warnings": [],
        "totals": {"excludedBrushWarehouseRows": excluded},
    }


def age_payload(warehouse: str) -> dict[str, object]:
    return {
        "dataset": "age",
        "file": {
            "name": "库龄分析.xlsx",
            "sizeBytes": 2048,
            "rawFileHash": hashlib.sha256(warehouse.encode()).hexdigest(),
            "sheetName": "库龄分析",
        },
        "snapshotDate": "2026-09-01",
        "sourceRowCount": 1,
        "excludedCount": 0,
        "rows": [{
            "sourceRowNumber": 2,
            "warehouse": warehouse,
            "warehouseType": "owned",
            "productCode": "AGE-P1",
            "productName": "库龄货品",
            "specification": "标准装",
            "category": "厨房电器",
            "availableQuantity": 10,
            "inventoryAgeDays": 30,
            "sales7dQuantity": 1,
            "sales30dQuantity": 3,
            "unitCostCents": 500,
            "stockValueCents": 5000,
        }],
        "warnings": [],
        "totals": {},
    }


class InventoryImportTests(TestCase):
    def setUp(self) -> None:
        InventoryWriteAuthority.objects.filter(id=1).update(
            status="postgres",
            authority_epoch=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            cutover_id="inventory-test-cutover",
            migration_verify_run_id="inventory-apply-" + "1" * 32,
            activated_at=timezone.now(),
        )

    def test_import_is_atomic_business_idempotent_and_full_snapshot_replacing(self) -> None:
        first = import_inventory_payload(
            stock_payload(stock_row("P1", 2), stock_row("P2", 3)),
            "admin@example.test",
        )
        self.assertEqual(first["status"], "imported")
        self.assertEqual(InventoryStockLine.objects.count(), 2)
        stored = InventoryStockLine.objects.get(product_code="P1")
        self.assertEqual(stored.supplier, "规格供应商甲")
        self.assertEqual(stored.warehouse_category, "selfOperated")
        self.assertTrue(stored.include_in_inventory)
        attempt = InventoryImportAttempt.objects.get(outcome="imported")
        self.assertEqual(attempt.excluded_count, 1)
        self.assertEqual(InventoryDataRevision.objects.get(domain="inventory").revision, 1)

        duplicate = import_inventory_payload(
            stock_payload(
                stock_row("P2", 30),
                stock_row("P1", 20),
                raw_seed="resaved-workbook",
            ),
            "admin@example.test",
        )
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(InventoryImportBatch.objects.count(), 1)
        self.assertEqual(InventoryDataRevision.objects.get(domain="inventory").revision, 1)

        replacement = import_inventory_payload(
            stock_payload(
                stock_row("P1", 2, available=7),
                stock_row("P3", 4, available=9),
                raw_seed="changed-business-content",
            ),
            "admin@example.test",
        )
        self.assertEqual(replacement["status"], "imported")
        self.assertEqual(
            list(
                InventoryStockLine.objects.order_by("product_code").values_list(
                    "product_code", flat=True
                )
            ),
            ["P1", "P3"],
        )
        self.assertEqual(InventoryImportBatch.objects.count(), 2)
        self.assertEqual(InventoryDataRevision.objects.get(domain="inventory").revision, 2)
        self.assertEqual(str(_latest_batch("stock").id), replacement["batch"]["id"])

    def test_new_warehouse_is_added_as_pending_until_an_admin_confirms_it(self) -> None:
        result = import_inventory_payload(
            stock_payload(stock_row("P1", 2, warehouse="首次发现测试仓"), excluded=0),
            "admin@example.test",
        )

        self.assertEqual(result["status"], "imported")
        self.assertIn(
            "NEW_WAREHOUSES_PENDING_CONFIRMATION",
            {warning["code"] for warning in result["warnings"]},
        )
        pending_payload = mapping_payload()
        pending = next(
            row for row in pending_payload["rows"]
            if row["warehouse"] == "首次发现测试仓"
        )
        self.assertEqual(pending["category"], "selfOperated")
        self.assertTrue(pending["includeInInventory"])
        self.assertTrue(pending["pendingConfirmation"])
        self.assertEqual(pending_payload["rows"][0]["warehouse"], "首次发现测试仓")

        confirmed_payload = update_mapping(
            {
                "expectedMappingRevision": pending_payload["mappingRevision"],
                "mappings": [{
                    "warehouse": "首次发现测试仓",
                    "category": "sample",
                    "includeInInventory": False,
                }],
            },
            "admin@example.test",
        )
        confirmed = next(
            row for row in confirmed_payload["rows"]
            if row["warehouse"] == "首次发现测试仓"
        )
        self.assertEqual(confirmed["category"], "sample")
        self.assertFalse(confirmed["includeInInventory"])
        self.assertFalse(confirmed["pendingConfirmation"])

    def test_explicit_zero_cost_inventory_is_preserved(self) -> None:
        result = import_inventory_payload(
            stock_payload(stock_row("ZERO-COST", 2, available=6, unit_cost=0), excluded=0),
            "admin@example.test",
        )

        self.assertEqual(result["status"], "imported")
        stored = InventoryStockLine.objects.get(product_code="ZERO-COST")
        self.assertEqual((stored.available_quantity, stored.unit_cost_cents), (6, 0))
        self.assertEqual(result["batch"]["excludedCount"], 0)

    def test_age_import_also_discovers_a_new_warehouse_for_confirmation(self) -> None:
        result = import_inventory_payload(
            age_payload("库龄首次发现仓"),
            "admin@example.test",
        )

        self.assertEqual(result["status"], "imported")
        discovered = next(
            row for row in mapping_payload()["rows"]
            if row["warehouse"] == "库龄首次发现仓"
        )
        self.assertTrue(discovered["pendingConfirmation"])
        self.assertEqual(discovered["category"], "selfOperated")

    def test_failed_import_does_not_leave_a_discovered_warehouse_mapping(self) -> None:
        import_inventory_payload(stock_payload(stock_row("P1", 2)), "admin@example.test")
        InventoryStockLine.objects.filter(product_code="P1").update(available_quantity=999)

        with self.assertRaises(InventoryApiError):
            import_inventory_payload(
                stock_payload(
                    stock_row("P2", 2, warehouse="失败事务测试仓"),
                    raw_seed="failed-new-warehouse",
                    excluded=0,
                ),
                "admin@example.test",
            )

        self.assertNotIn(
            "失败事务测试仓",
            {row["warehouse"] for row in mapping_payload()["rows"]},
        )

    def test_v1_current_batch_remains_verifiable_before_the_first_supplier_aware_import(self) -> None:
        import_inventory_payload(stock_payload(stock_row("P1", 2)), "admin@example.test")
        batch = InventoryImportBatch.objects.get()
        row = InventoryStockLine.objects.get()
        v1_row = {
            "warehouse": row.warehouse,
            "warehouseType": row.warehouse_type,
            "productCode": row.product_code,
            "productName": row.product_name,
            "brand": row.brand,
            "specification": row.specification,
            "barcode": row.barcode,
            "category": row.category,
            "onHandQuantity": int(row.on_hand_quantity),
            "availableQuantity": int(row.available_quantity),
            "lockedQuantity": int(row.locked_quantity),
            "inTransitQuantity": int(row.in_transit_quantity),
            "unitCostCents": int(row.unit_cost_cents),
            "inventoryAgeDays": row.inventory_age_days,
            "sales7dQuantity": int(row.sales_7d_quantity or 0),
            "sales30dQuantity": int(row.sales_30d_quantity or 0),
        }
        batch.content_hash = _business_content_hash(
            "stock",
            batch.snapshot_date,
            [v1_row],
            version="inventory-stock-pg-v1",
        )
        batch.totals_json = {**batch.totals_json, "canonicalFormatVersion": "inventory-stock-pg-v1"}
        batch.save(update_fields=["content_hash", "totals_json"])

        upgraded = import_inventory_payload(
            stock_payload(stock_row("P1", 20), raw_seed="supplier-aware-v2"),
            "admin@example.test",
        )

        self.assertEqual(upgraded["status"], "imported")
        self.assertEqual(InventoryImportBatch.objects.count(), 2)

    def test_current_fact_corruption_fails_closed_and_is_durably_audited(self) -> None:
        payload = stock_payload(stock_row("P1", 2), stock_row("P2", 3))
        import_inventory_payload(payload, "admin@example.test")
        InventoryStockLine.objects.filter(product_code="P1").update(available_quantity=999)

        with self.assertRaises(InventoryApiError) as raised:
            import_inventory_payload(
                stock_payload(
                    stock_row("P2", 30),
                    stock_row("P1", 20),
                    raw_seed="retry-after-corruption",
                ),
                "admin@example.test",
            )

        self.assertEqual(raised.exception.code, "version_conflict")
        self.assertEqual(InventoryImportBatch.objects.count(), 1)
        failed = InventoryImportAttempt.objects.filter(outcome="failed").get()
        self.assertEqual(failed.error_code, "version_conflict")
        self.assertEqual(failed.row_count, 2)
        self.assertEqual(InventoryDataRevision.objects.get(domain="inventory").revision, 1)

    def test_excluded_warehouse_or_duplicate_business_key_is_rejected_without_fact_batch(self) -> None:
        with self.assertRaisesRegex(InventoryApiError, "刷刷仓"):
            import_inventory_payload(
                stock_payload(stock_row("P1", 2, warehouse="刷刷仓"), excluded=0),
                "admin@example.test",
            )
        with self.assertRaisesRegex(InventoryApiError, "重复"):
            import_inventory_payload(
                stock_payload(stock_row("P1", 2), stock_row("P1", 3), excluded=0),
                "admin@example.test",
            )
        self.assertEqual(InventoryImportBatch.objects.count(), 0)
        self.assertEqual(InventoryStockLine.objects.count(), 0)
        self.assertEqual(InventoryImportAttempt.objects.filter(outcome="rejected").count(), 2)

    def test_completed_upload_is_not_reused_from_metadata_only_fingerprint(self) -> None:
        payload = {
            "action": "init",
            "dataset": "stock",
            "snapshotDate": "2026-09-01",
            "fileName": "分仓库存.xlsx",
            "fileSizeBytes": 3,
            "chunkCount": 1,
            "fingerprint": "same-name-size-and-last-modified",
        }
        first = begin_upload(payload, "admin@example.test")
        first_id = str(first["upload"]["id"])
        receive_chunk(first_id, 0, b"old", "admin@example.test")
        claim = claim_upload({"action": "claim", "uploadId": first_id}, "admin@example.test")
        finish_upload(
            {
                "action": "finish",
                "uploadId": first_id,
                "ownerToken": claim["ownerToken"],
                "result": {"ok": True, "status": "imported", "batch": {"id": "batch-old"}},
            },
            "admin@example.test",
        )

        second = begin_upload(payload, "admin@example.test")
        self.assertNotEqual(str(second["upload"]["id"]), first_id)
        self.assertEqual(second["upload"]["status"], "uploading")

    def test_completed_write_replay_keeps_the_required_inventory_revision(self) -> None:
        request = RequestFactory().post(
            "/api/inventory/imports",
            data=b"{}",
            content_type="application/json",
            headers={
                "X-Teruisi-Request-Id": "inventory-replay-test",
                "X-Teruisi-Content-SHA256": "0" * 64,
            },
        )
        principal = Principal(
            email="admin@example.test",
            display_name="Admin",
            role="admin",
            scope=None,
        )
        first = _replay_write(request, principal, lambda: ({"ok": True}, 201))
        replay = _replay_write(
            request,
            principal,
            lambda: self.fail("completed request must replay instead of executing again"),
        )

        self.assertRegex(first["X-Inventory-Data-Revision"], r"^\d+:[a-f0-9]{12}$")
        self.assertRegex(replay["X-Inventory-Data-Revision"], r"^\d+:[a-f0-9]{12}$")
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
