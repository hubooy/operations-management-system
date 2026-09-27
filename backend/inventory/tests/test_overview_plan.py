from __future__ import annotations

from datetime import date
import json
import uuid
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from inventory.models import InventoryImportScopeHead, InventoryWriteAuthority, ReplenishmentPlanItem
from inventory.plans import plan_payload, upsert_plan
from inventory.query import (
    _filtered_overview,
    _health,
    _health_distribution_items,
    _mapping_samples,
    _metrics,
    _sales_period,
)
from inventory.warehouse_mapping import classify_warehouse
from inventory.views import replenishment, replenishment_import
from sales.auth import Principal


def overview_item(
    warehouse: str,
    *,
    available: int,
    sales: int | None,
    in_transit: int = 0,
    warehouse_category: str | None = None,
    included: bool = True,
    product_sales: int | None = None,
) -> dict[str, object]:
    return {
        "key": f"{warehouse}\x1fP1",
        "productCode": "P1",
        "productName": "测试货品",
        "brand": "品牌甲",
        "category": "净水设备",
        "supplier": "供应商甲",
        "specification": "",
        "warehouse": warehouse,
        "warehouseType": "jd_rdc" if "京东" in warehouse else "owned",
        "warehouseCategory": warehouse_category or ("jd" if "京东" in warehouse else "guangdong"),
        "includedInInventory": included,
        "availableQuantity": available,
        "totalInTransitQuantity": in_transit,
        "knownStockValueCents": available * 500,
        "sales30d": sales,
        "productSales30d": sales if product_sales is None else product_sales,
        "coverageDays": available / (sales / 30) if sales else None,
        "suggestedQuantity": 4 if sales else None,
        "status": "warning" if sales else "stale",
        "statusLabel": "补货预警" if sales else "积压风险",
        "reason": "测试原因",
        "inDraftPlan": False,
    }


class InventoryMappingWorkbenchTests(SimpleTestCase):
    def test_health_statuses_use_six_labels_and_strict_180_day_boundary(self) -> None:
        settings = {"criticalDays": 7, "replenishDays": 30}

        self.assertEqual(_health(0, 1, 0, None, settings, 30)[:2], ("no_stock", "无库存可用"))
        self.assertEqual(_health(7, 1, 7, None, settings, 30)[:2], ("urgent", "紧急补货"))
        self.assertEqual(_health(20, 1, 20, None, settings, 30)[:2], ("warning", "补货预警"))
        self.assertEqual(_health(10, 0, None, 30, settings, 30)[:2], ("stale", "积压风险"))
        self.assertEqual(_health(180, 1, 180, None, settings, 30)[:2], ("healthy", "库存健康"))
        self.assertEqual(_health(181, 1, 181, None, settings, 30)[:2], ("slow", "低周转"))

    def test_health_distribution_only_counts_jd_tmall_guangdong_and_self_operated(self) -> None:
        statuses = ["no_stock", "urgent", "warning", "stale", "slow", "healthy"]
        categories = ["jd", "cainiao", "guangdong", "selfOperated", "dropship", "afterSales"]
        items = []
        for index, (status, category) in enumerate(zip(statuses, categories, strict=True), start=1):
            items.append({
                "warehouseCategory": category,
                "availableQuantity": index,
                "costCoverageRate": 1,
                "knownStockValueCents": index * 100,
                "averageDailySales": 1,
                "sales30d": 30,
                "suggestedQuantity": 0,
                "status": status,
            })
        scoped = _health_distribution_items(items)
        metrics, health = _metrics(
            items,
            {"recommendationsSuppressed": False, "issues": []},
            True,
            health_items=scoped,
        )

        self.assertEqual([item["warehouseCategory"] for item in scoped], ["jd", "cainiao", "guangdong", "selfOperated"])
        self.assertEqual((health["noStock"], health["urgent"], health["warning"], health["stale"]), (1, 1, 1, 1))
        self.assertEqual((health["slow"], health["healthy"]), (0, 0))
        self.assertEqual(metrics["skuWarehouseCount"], 6)
        self.assertEqual(metrics["slowMovingValueCents"], 400)

    def test_inventory_sales_window_is_always_the_latest_thirty_days(self) -> None:
        start, end, coverage = _sales_period(
            {"startDate": "2025-01-01", "endDate": "2025-01-31"},
            {"dataStartDate": "2026-01-01", "dataCutoffDate": "2026-09-03"},
        )

        self.assertEqual(start, date(2026, 8, 5))
        self.assertEqual(end, date(2026, 9, 3))
        self.assertEqual(coverage, 30)

    def test_controlled_mapping_distinguishes_supplier_jd_and_cainiao_warehouses(self) -> None:
        supplier = classify_warehouse("一个小太阳仓")
        jd = classify_warehouse("上海公共平台仓28号库-京东")
        cainiao = classify_warehouse("ZA菜鸟华中武汉黄陂标准03仓")

        self.assertEqual((supplier.category, supplier.include_in_inventory), ("dropship", False))
        self.assertEqual((jd.category, jd.warehouse_type, jd.include_in_inventory), ("jd", "jd_rdc", True))
        self.assertEqual((cainiao.category, cainiao.include_in_inventory), ("cainiao", True))

    def test_mapping_workbench_uses_filtered_scope_and_fixed_warehouse_groups(self) -> None:
        items = [
            overview_item("广东仓", available=10, sales=None, in_transit=2),
            overview_item("京东北京仓", available=20, sales=30, in_transit=3),
        ]
        filtered = _filtered_overview(items, {"warehouses": ["广东仓"]})
        samples = _mapping_samples(filtered, 30, False)

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["totalInventoryQuantity"], 10)
        self.assertEqual(samples[0]["warehouses"]["guangdong"]["inventoryQuantity"], 10)
        self.assertEqual(samples[0]["warehouses"]["jd"]["inventoryQuantity"], 0)
        self.assertIsNone(samples[0]["totalSalesQuantity"])
        self.assertEqual(samples[0]["warehouseOptions"][0]["warehouse"], "广东仓")

    def test_workbench_keeps_supplier_metrics_but_excludes_non_counted_stock_from_totals(self) -> None:
        items = [
            overview_item(
                "一个小太阳仓",
                available=40,
                sales=12,
                warehouse_category="dropship",
                included=False,
                product_sales=42,
            ),
            overview_item("京东北京仓", available=20, sales=30, product_sales=42),
        ]

        self.assertEqual(len(_filtered_overview(items, {})), 1)
        sample = _mapping_samples(items, 30, False)[0]
        self.assertEqual(sample["warehouses"]["dropship"]["inventoryQuantity"], 40)
        self.assertEqual(sample["warehouses"]["dropship"]["salesQuantity"], 12)
        self.assertEqual(sample["totalInventoryQuantity"], 20)
        self.assertEqual(sample["totalSalesQuantity"], 42)
        self.assertEqual(len(sample["warehouseOptions"]), 1)

    def test_manual_plan_can_be_saved_without_fabricating_a_system_suggestion(self) -> None:
        request = RequestFactory().post(
            "/api/inventory/replenishment",
            data={
                "key": "广东仓\x1fP1",
                "plannedQuantity": 12,
                "manual": True,
                "buyer": "采购甲",
                "orderDate": "2026-09-03",
                "expectedConsumptionDays": 45.6,
            },
            content_type="application/json",
        )
        principal = Principal("operator@example.test", "运营", "operator", None)
        overview = {
            "controls": {"autoReplenishmentEnabled": False},
            "quality": {"recommendationsSuppressed": True},
            "sync": {"inventoryStale": False, "latestInventoryBatchId": "batch-1"},
            "items": [{
                "productCode": "P1", "productName": "测试货品", "brand": "品牌甲",
                "category": "净水设备", "supplier": "供应商甲", "warehouse": "广东仓",
                "suggestedQuantity": None, "coverageDays": 11.4, "availableQuantity": 25,
                "sales30d": None, "productSales30d": 37,
                "reason": "未匹配销量",
            }],
        }
        captured: dict[str, object] = {}

        def save(data: dict[str, object], _actor: str) -> dict[str, object]:
            captured.update(data)
            return data

        with (
            patch("inventory.views._principal", return_value=principal),
            patch("inventory.views._replay_write", side_effect=lambda _request, _principal, callback: callback()),
            patch("inventory.views.inventory_overview", return_value=overview),
            patch("inventory.views.upsert_plan", side_effect=save),
            patch("inventory.views.plan_payload", side_effect=lambda plan: plan),
        ):
            payload, status = replenishment(request)

        self.assertEqual(status, 201)
        self.assertTrue(payload["ok"])
        self.assertEqual(captured["suggestedQuantity"], 0)
        self.assertEqual(captured["plannedQuantity"], 12)
        self.assertEqual(captured["buyer"], "采购甲")
        self.assertEqual(captured["sales30dQuantity"], 37)
        self.assertEqual(captured["coverageDays"], 45.6)
        self.assertIn("人工创建", captured["reason"])

    def test_manual_plan_rejects_invalid_expected_consumption_days(self) -> None:
        request = RequestFactory().post(
            "/api/inventory/replenishment",
            data={
                "key": "广东仓\x1fP1",
                "plannedQuantity": 12,
                "manual": True,
                "expectedConsumptionDays": 45.67,
            },
            content_type="application/json",
        )
        principal = Principal("operator@example.test", "运营", "operator", None)

        with (
            patch("inventory.views._principal", return_value=principal),
            patch("inventory.views._replay_write", side_effect=lambda _request, _principal, callback: callback()),
        ):
            response = replenishment(request)

        self.assertEqual(response.status_code, 400)
        self.assertIn("最多一位小数", json.loads(response.content)["error"])


class ReplenishmentPlanDetailsTests(TestCase):
    def setUp(self) -> None:
        InventoryWriteAuthority.objects.filter(id=1).update(
            status="postgres",
            authority_epoch=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            cutover_id="inventory-test-cutover",
            migration_verify_run_id="inventory-apply-" + "1" * 32,
            activated_at=timezone.now(),
        )

    def test_manual_plan_persists_procurement_and_arrival_details(self) -> None:
        plan = upsert_plan(
            {
                "sourceBatchId": "batch-1",
                "productCode": "P1",
                "productName": "测试货品",
                "brand": "品牌甲",
                "category": "净水设备",
                "supplier": "供应商甲",
                "warehouse": "广东仓",
                "buyer": "采购甲",
                "operatorName": "运营甲",
                "department": "电商部",
                "planType": "常规",
                "orderDate": date(2026, 9, 3),
                "expectedArrivalDate": date(2026, 9, 10),
                "requiresInspection": True,
                "currentStockQuantity": 25,
                "sales30dQuantity": 37,
                "suggestedQuantity": 0,
                "plannedQuantity": 12,
                "coverageDays": 20.3,
                "reason": "人工创建",
                "notes": "优先安排",
                "status": "draft",
            },
            "operator@example.test",
        )
        payload = plan_payload(plan)

        self.assertEqual(payload["buyer"], "采购甲")
        self.assertEqual(payload["operatorName"], "运营甲")
        self.assertEqual(payload["orderDate"], "2026-09-03")
        self.assertEqual(payload["expectedArrivalDate"], "2026-09-10")
        self.assertTrue(payload["requiresInspection"])
        self.assertEqual(payload["sales30dQuantity"], 37)
        self.assertEqual(payload["notes"], "优先安排")


class ReplenishmentPlanImportTests(TestCase):
    def setUp(self) -> None:
        InventoryWriteAuthority.objects.filter(id=1).update(
            status="postgres",
            authority_epoch=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            cutover_id="inventory-test-cutover",
            migration_verify_run_id="inventory-apply-" + "1" * 32,
            activated_at=timezone.now(),
        )
        InventoryImportScopeHead.objects.filter(dataset="stock").update(
            status="ready",
            current_batch_id="batch-latest",
        )

    @staticmethod
    def row(product_code: str, warehouse: str, quantity: int, *, status: str = "confirmed") -> dict[str, object]:
        return {
            "productCode": product_code,
            "warehouse": warehouse,
            "plannedQuantity": quantity,
            "buyer": "采购甲",
            "operatorName": "运营甲",
            "department": "志高项目组",
            "planType": "日常备货",
            "orderDate": "2026-09-10",
            "expectedArrivalDate": "2026-09-18",
            "expectedConsumptionDays": 45.5,
            "status": status,
            "requiresInspection": True,
            "notes": "表格导入",
        }

    @staticmethod
    def sources() -> dict[str, object]:
        return {
            "latestBatchId": "batch-latest",
            "inventoryAsOf": "2026-09-10",
            "inventoryStale": False,
            "quality": {"recommendationsSuppressed": False},
            "itemsByKey": {
                "广东仓\x1fP1": overview_item("广东仓", available=20, sales=30, product_sales=40),
                "广东仓\x1fP2": {**overview_item("广东仓", available=12, sales=15, product_sales=25), "key": "广东仓\x1fP2", "productCode": "P2"},
            },
        }

    def test_imports_multiple_rows_atomically_and_returns_confirmed_ids(self) -> None:
        request = RequestFactory().post(
            "/api/inventory/replenishment/import",
            data={
                "rows": [self.row("P1", "广东仓", 10), self.row("P2", "广东仓", 8, status="draft")],
                "acknowledgeStale": False,
                "fileSha256": "a" * 64,
            },
            content_type="application/json",
        )
        principal = Principal("operator@example.test", "运营", "operator", None)
        with (
            patch("inventory.views._principal", return_value=principal),
            patch("inventory.views._replay_write", side_effect=lambda _request, _principal, callback: callback()),
            patch("inventory.views.replenishment_plan_sources", return_value=self.sources()),
        ):
            payload, status = replenishment_import(request)

        self.assertEqual(status, 201)
        self.assertEqual(payload["importedCount"], 2)
        self.assertEqual(payload["confirmedCount"], 1)
        self.assertEqual(payload["draftCount"], 1)
        self.assertEqual(len(payload["confirmedPlanIds"]), 1)
        self.assertEqual(ReplenishmentPlanItem.objects.count(), 2)
        confirmed = ReplenishmentPlanItem.objects.get(status="confirmed")
        self.assertEqual(confirmed.source_batch_id, "batch-latest")
        self.assertEqual(confirmed.sales_30d_quantity, 40)
        self.assertEqual(confirmed.coverage_days_tenths, 455)

    def test_missing_inventory_row_rejects_entire_import(self) -> None:
        request = RequestFactory().post(
            "/api/inventory/replenishment/import",
            data={
                "rows": [self.row("P1", "广东仓", 10), self.row("MISSING", "广东仓", 8)],
                "acknowledgeStale": False,
                "fileSha256": "b" * 64,
            },
            content_type="application/json",
        )
        principal = Principal("operator@example.test", "运营", "operator", None)
        with (
            patch("inventory.views._principal", return_value=principal),
            patch("inventory.views._replay_write", side_effect=lambda _request, _principal, callback: callback()),
            patch("inventory.views.replenishment_plan_sources", return_value=self.sources()),
        ):
            response = replenishment_import(request)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(ReplenishmentPlanItem.objects.count(), 0)

    def test_inventory_snapshot_change_rejects_entire_import(self) -> None:
        InventoryImportScopeHead.objects.filter(dataset="stock").update(current_batch_id="batch-newer")
        request = RequestFactory().post(
            "/api/inventory/replenishment/import",
            data={
                "rows": [self.row("P1", "广东仓", 10)],
                "acknowledgeStale": False,
                "fileSha256": "c" * 64,
            },
            content_type="application/json",
        )
        principal = Principal("operator@example.test", "运营", "operator", None)
        with (
            patch("inventory.views._principal", return_value=principal),
            patch("inventory.views._replay_write", side_effect=lambda _request, _principal, callback: callback()),
            patch("inventory.views.replenishment_plan_sources", return_value=self.sources()),
        ):
            response = replenishment_import(request)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(ReplenishmentPlanItem.objects.count(), 0)
