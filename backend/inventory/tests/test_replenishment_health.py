from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Event
from unittest.mock import patch
import uuid

from django.apps import apps
from django.db import connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from inventory import guangdong as gd
from inventory import query
from inventory.import_service import import_inventory_payload
from inventory.models import (GuangdongMonitorItem, GuangdongSupplierCycle,
                              InventoryOperatingSettings, InventoryStockLine,
                              InventoryWriteAuthority, ReplenishmentPlanItem)
from inventory.plans import upsert_plan, update_plan
from inventory.replenishment_health import waiting_for_stock
from inventory.tests.test_imports import stock_payload, stock_row
from inventory.warehouse_mapping import classify_warehouse
from inventory.write_requests import lock_active_authority
from sales.auth import Principal


class ReplenishmentHealthTests(TestCase):
    def setUp(self):
        InventoryWriteAuthority.objects.filter(id=1).update(
            status="postgres", authority_epoch=uuid.uuid4(), cutover_id="health-test",
            migration_verify_run_id="health-test", activated_at=timezone.now(),
        )
        self.today = timezone.localdate()
        self.principal = Principal("health@example.invalid", "Health", "admin", None)
        GuangdongMonitorItem.objects.create(product_code="P", updated_by="test")
        GuangdongSupplierCycle.objects.create(supplier="规格供应商甲", lead_days=10, updated_by="test")
        self.publish(100)

    def publish(self, quantity, *, day=0, warehouse="广东仓", available=None, code="P"):
        row = stock_row(code, 2, warehouse=warehouse, available=quantity if available is None else available)
        mapping = classify_warehouse(warehouse)
        row.update(warehouseType=mapping.warehouse_type, warehouseCategory=mapping.category,
                   includeInInventory=mapping.include_in_inventory)
        row["onHandQuantity"] = quantity
        row["snapshotDate"] = (self.today + timedelta(days=day)).isoformat()
        payload = stock_payload(row, raw_seed=uuid.uuid4().hex)
        payload["snapshotDate"] = row["snapshotDate"]
        return import_inventory_payload(payload, self.principal.email)

    def plan(self, quantity=30, warehouse="广东仓", status="draft"):
        return upsert_plan({
            "sourceBatchId": query._latest_batch("stock").id, "productCode": "P", "productName": "测试",
            "warehouse": warehouse, "status": status, "plannedQuantity": quantity,
            "suggestedQuantity": 30, "reason": "test", "orderDate": self.today,
        }, self.principal.email)

    def sales(self, _principal, payload):
        if payload["operation"] == "freshness":
            return {"dataStartDate": (self.today - timedelta(days=120)).isoformat(), "dataCutoffDate": self.today.isoformat()}
        row = {"productCode": "P", "warehouseKey": query._warehouse_key("广东仓"), "salesQuantity": 300,
               "sales7dQuantity": 70, "sales15dQuantity": 150, "sales30dQuantity": 300}
        return {"asOfDate": self.today.isoformat(), "dataStartDate": (self.today - timedelta(days=120)).isoformat(),
                "truncated": False, "rows": [row]}

    def read_both(self):
        with patch.object(gd, "_sales_query", side_effect=self.sales), patch.object(query, "_sales_query", side_effect=self.sales):
            monitor = gd.monitor(self.principal, {})
            overview = query.inventory_overview(self.principal, {})
        return monitor, overview

    def test_plan_health_distribution_reasons_and_overview_agree(self):
        self.plan()
        monitor, overview = self.read_both()
        item = monitor["items"][0]
        self.assertEqual((item["risk"], item["riskSource"], item["autoRisk"]), ("healthy", "备货跟进", "urgent"))
        self.assertIn("首次增加", item["riskReason"])
        self.assertEqual(next(x for x in monitor["distribution"] if x["risk"] == "healthy")["itemCount"], 1)
        self.assertEqual(overview["items"][0]["status"], "healthy")
        self.assertEqual(overview["health"]["healthy"], 1)

    def test_zero_cost_inventory_has_complete_zero_value_and_remains_replenishable(self):
        InventoryStockLine.objects.filter(product_code="P", warehouse="广东仓").update(unit_cost_cents=0)
        InventoryOperatingSettings.objects.filter(id=1).update(auto_replenishment=True)

        with patch.object(query, "_sales_query", side_effect=self.sales):
            overview = query.inventory_overview(self.principal, {})

        item = overview["items"][0]
        self.assertEqual(item["unitCostCents"], 0)
        self.assertEqual(item["stockValueCents"], 0)
        self.assertEqual(item["costCoverageRate"], 1)
        self.assertTrue(overview["metrics"]["stockValueComplete"])
        self.assertEqual(overview["recommendations"][0]["productCode"], "P")
        self.assertGreater(overview["recommendations"][0]["suggestedQuantity"], 0)

    def test_first_increase_after_decline_releases_once_not_initial_baseline(self):
        plan = self.plan()
        timestamp = plan.updated_at
        self.publish(50)
        plan.refresh_from_db()
        self.assertTrue(waiting_for_stock(plan))
        self.publish(51)
        plan.refresh_from_db()
        self.assertFalse(waiting_for_stock(plan))
        self.assertEqual(plan.updated_at, timestamp)
        released = plan.guangdong_health
        self.publish(10)
        plan.refresh_from_db()
        self.assertEqual(plan.guangdong_health, released)
        monitor, overview = self.read_both()
        self.assertEqual(monitor["items"][0]["risk"], "urgent")
        self.assertEqual(overview["items"][0]["status"], "urgent")

    def test_unchanged_duplicate_and_available_unlock_do_not_release(self):
        plan = self.plan()
        result = self.publish(100)
        self.assertEqual(result["status"], "duplicate")
        self.publish(100, available=120)
        plan.refresh_from_db()
        self.assertTrue(waiting_for_stock(plan))

    def test_missing_initial_stock_sets_baseline_and_overview_nearby_warehouse_is_unchanged(self):
        self.publish(999, warehouse="广东仓-欧洲站")
        plan = self.plan()
        self.assertIsNone(plan.guangdong_health["quantity"])
        monitor, overview = self.read_both()
        self.assertEqual(monitor["items"][0]["risk"], "healthy")
        self.assertNotEqual(overview["items"][0]["status"], "healthy")
        self.publish(100)
        plan.refresh_from_db()
        self.assertTrue(waiting_for_stock(plan))
        self.publish(101)
        plan.refresh_from_db()
        self.assertFalse(waiting_for_stock(plan))

    def test_other_warehouse_other_sku_missing_and_historical_import_do_not_release(self):
        plan = self.plan()
        self.publish(999, day=-1)
        self.publish(999, warehouse="广东仓-欧洲站")
        self.publish(999, code="OTHER")
        plan.refresh_from_db()
        self.assertTrue(waiting_for_stock(plan))
        self.publish(101)
        plan.refresh_from_db()
        self.assertFalse(waiting_for_stock(plan))

    def test_only_quantity_increase_rearms_and_cancellation_or_zero_removes_hold(self):
        plan = self.plan()
        self.publish(101)
        plan = update_plan(plan.id, "draft", 30)
        self.assertFalse(waiting_for_stock(plan))
        plan = update_plan(plan.id, "draft", 31)
        self.assertTrue(waiting_for_stock(plan))
        plan = update_plan(plan.id, "draft", 0)
        self.assertFalse(waiting_for_stock(plan))
        plan = update_plan(plan.id, "draft", 32)
        self.assertTrue(waiting_for_stock(plan))
        plan = update_plan(plan.id, "cancelled", None)
        self.assertFalse(waiting_for_stock(plan))

    def test_confirm_or_complete_does_not_rearm_consumed_order(self):
        plan = self.plan()
        self.publish(101)
        plan = update_plan(plan.id, "confirmed", None)
        self.assertFalse(waiting_for_stock(plan))
        plan = update_plan(plan.id, "completed", None)
        self.assertFalse(waiting_for_stock(plan))

    def test_transaction_failure_rolls_back_stock_and_cycle_together(self):
        plan = self.plan()
        original = plan.guangdong_health
        with patch("inventory.import_service.bump_revision", side_effect=RuntimeError("rollback")):
            with self.assertRaises(RuntimeError):
                self.publish(200)
        plan.refresh_from_db()
        self.assertEqual(plan.guangdong_health, original)
        self.assertEqual(InventoryStockLine.objects.get(warehouse="广东仓").on_hand_quantity, 100)

    def test_latest_plan_and_manual_risk_yield_to_order_then_restore(self):
        GuangdongMonitorItem.objects.filter(product_code="P").update(risk_override="warning", risk_reason_override="人工说明")
        self.plan(status="confirmed")
        self.assertEqual(self.read_both()[0]["items"][0]["risk"], "healthy")
        self.publish(101)
        self.assertEqual(self.read_both()[0]["items"][0]["risk"], "warning")

    def manual_healthy(self):
        GuangdongMonitorItem.objects.filter(product_code="P").update(
            risk_override="healthy", risk_reason_override="已手动备货",
            buyer_override="采购甲", notes="保留备注",
        )

    def test_manual_healthy_resets_on_increase_and_expires_old_hold(self):
        from inventory.models import GuangdongMonitorAudit
        for quantity in (101, 102):
            self.manual_healthy()
            plan = self.plan(quantity=quantity)
            timestamp = plan.updated_at
            self.publish(quantity)
            item = GuangdongMonitorItem.objects.get(product_code="P")
            self.assertIsNone(item.risk_override)
            self.assertIsNone(item.risk_reason_override)
            self.assertEqual((item.buyer_override, item.notes), ("采购甲", "保留备注"))
            plan.refresh_from_db()
            self.assertFalse(waiting_for_stock(plan))
            self.assertEqual(plan.updated_at, timestamp)
            monitor, overview = self.read_both()
            self.assertEqual(monitor["items"][0]["risk"], monitor["items"][0]["autoRisk"])
            self.assertEqual(overview["items"][0]["status"], monitor["items"][0]["autoRisk"])
        self.assertEqual(GuangdongMonitorAudit.objects.filter(action="stock_risk_reset").count(), 2)

    def test_manual_healthy_survives_declines_then_resets_on_first_increase(self):
        from inventory.models import GuangdongMonitorAudit
        self.manual_healthy()
        original = GuangdongMonitorItem.objects.get(product_code="P")
        for quantity in (50, 20, 0):
            self.publish(quantity)
            item = GuangdongMonitorItem.objects.get(product_code="P")
            self.assertEqual((item.risk_override, item.risk_reason_override), ("healthy", "已手动备货"))
            self.assertEqual((item.updated_at, item.updated_by), (original.updated_at, original.updated_by))
            monitor, overview = self.read_both()
            self.assertEqual((monitor["items"][0]["risk"], monitor["items"][0]["riskSource"]), ("healthy", "型号设置"))
            self.assertEqual(overview["items"][0]["status"], "healthy")
            self.assertFalse(GuangdongMonitorAudit.objects.filter(action="stock_risk_reset").exists())
        self.publish(1)
        item.refresh_from_db()
        self.assertIsNone(item.risk_override)
        self.assertIsNone(item.risk_reason_override)
        audit = GuangdongMonitorAudit.objects.get(action="stock_risk_reset")
        self.assertEqual((audit.result["previousOnHandQuantity"], audit.result["onHandQuantity"]), (0, 1))
        monitor, overview = self.read_both()
        self.assertEqual((monitor["items"][0]["risk"], monitor["items"][0]["riskSource"]), ("urgent", "系统判定"))
        self.assertEqual(overview["items"][0]["status"], "urgent")
        self.publish(0)
        item.refresh_from_db()
        self.assertIsNone(item.risk_override)
        self.assertEqual(GuangdongMonitorAudit.objects.filter(action="stock_risk_reset").count(), 1)

    def test_manual_healthy_decline_preserves_pending_plan_until_increase(self):
        from inventory.models import GuangdongMonitorAudit
        self.manual_healthy()
        plan = self.plan()
        timestamp = plan.updated_at
        for quantity in (50, 49):
            self.publish(quantity)
            plan.refresh_from_db()
            self.assertTrue(waiting_for_stock(plan))
            self.assertEqual(plan.updated_at, timestamp)
            self.assertEqual(GuangdongMonitorItem.objects.get(product_code="P").risk_override, "healthy")
            self.assertFalse(GuangdongMonitorAudit.objects.filter(action="stock_risk_reset").exists())
        self.publish(50)
        plan.refresh_from_db()
        self.assertFalse(waiting_for_stock(plan))
        self.assertEqual(plan.updated_at, timestamp)
        self.assertIsNone(GuangdongMonitorItem.objects.get(product_code="P").risk_override)
        self.assertEqual(GuangdongMonitorAudit.objects.filter(action="stock_risk_reset").count(), 1)

    def test_manual_healthy_ignores_duplicates_available_changes_and_historical_import(self):
        self.manual_healthy()
        self.publish(100)
        self.publish(100, available=120)
        self.publish(999, day=-1)
        self.assertEqual(GuangdongMonitorItem.objects.get(product_code="P").risk_override, "healthy")

    def test_manual_healthy_missing_or_other_stock_does_not_mean_zero(self):
        self.manual_healthy()
        self.publish(999, warehouse="广东仓-欧洲站")
        self.publish(999, code="OTHER")
        self.publish(200)
        self.assertEqual(GuangdongMonitorItem.objects.get(product_code="P").risk_override, "healthy")
        self.publish(199)
        self.assertEqual(GuangdongMonitorItem.objects.get(product_code="P").risk_override, "healthy")
        self.publish(200)
        self.assertIsNone(GuangdongMonitorItem.objects.get(product_code="P").risk_override)

    def test_manual_healthy_reset_and_audit_roll_back_with_import(self):
        from inventory.models import GuangdongMonitorAudit
        self.manual_healthy()
        plan = self.plan()
        with patch("inventory.import_service.bump_revision", side_effect=RuntimeError("rollback")):
            with self.assertRaises(RuntimeError):
                self.publish(101)
        self.assertEqual(GuangdongMonitorItem.objects.get(product_code="P").risk_override, "healthy")
        self.assertFalse(GuangdongMonitorAudit.objects.filter(action="stock_risk_reset").exists())
        plan.refresh_from_db()
        self.assertTrue(waiting_for_stock(plan))

    def test_overview_uses_supplier_and_item_rules_without_watchlist_filter(self):
        GuangdongMonitorItem.objects.all().delete()
        monitor, overview = self.read_both()
        self.assertEqual(monitor["items"], [])
        self.assertEqual(overview["items"][0]["status"], "urgent")
        GuangdongSupplierCycle.objects.update(lead_days=1, buffer_days=1)
        self.assertEqual(self.read_both()[1]["items"][0]["status"], "healthy")
        GuangdongMonitorItem.objects.create(product_code="P", lead_days_override=15, buffer_days_override=3, updated_by="test")
        self.assertEqual(self.read_both()[1]["items"][0]["status"], "urgent")

    def test_migration_baselines_existing_positive_plans_without_changing_timestamps(self):
        plan = self.plan(status="confirmed")
        other = self.plan(warehouse="京东仓")
        ReplenishmentPlanItem.objects.update(guangdong_health={})
        migration = import_module("inventory.migrations.0010_replenishment_health")
        with connection.schema_editor() as editor:
            migration.baseline_existing_plans(apps, editor)
        timestamp = plan.updated_at
        plan.refresh_from_db(); other.refresh_from_db()
        self.assertTrue(waiting_for_stock(plan))
        self.assertEqual(plan.updated_at, timestamp)
        self.assertEqual(other.guangdong_health, {})
        self.publish(101)
        with connection.schema_editor() as editor:
            migration.baseline_existing_plans(apps, editor)
        plan.refresh_from_db()
        self.assertFalse(waiting_for_stock(plan))


class ReplenishmentHealthConcurrencyTests(TransactionTestCase):
    publish = ReplenishmentHealthTests.publish
    plan = ReplenishmentHealthTests.plan

    def setUp(self):
        import_module("inventory.migrations.0002_seed_control_rows").seed_control_rows(apps, None)
        ReplenishmentHealthTests.setUp(self)

    def test_stock_and_plan_writes_serialize_in_either_order(self):
        for plan_first in (True, False):
            with self.subTest(plan_first=plan_first):
                ReplenishmentPlanItem.objects.all().delete()
                self.publish(100)
                held, attempted, release, finished = Event(), Event(), Event(), Event()

                def first():
                    try:
                        with transaction.atomic():
                            lock_active_authority()
                            result = self.plan() if plan_first else self.publish(101)
                            held.set()
                            if not release.wait(10):
                                raise RuntimeError("test authority release timeout")
                            return result
                    finally:
                        connections.close_all()

                def second():
                    try:
                        attempted.set()
                        result = self.publish(101) if plan_first else self.plan()
                        finished.set()
                        return result
                    finally:
                        connections.close_all()

                with ThreadPoolExecutor(max_workers=2) as pool:
                    owner = pool.submit(first)
                    try:
                        self.assertTrue(held.wait(10))
                        follower = pool.submit(second)
                        self.assertTrue(attempted.wait(10))
                        self.assertFalse(finished.wait(0.1))
                    finally:
                        release.set()
                    owner.result(timeout=10)
                    follower.result(timeout=10)
                plan = ReplenishmentPlanItem.objects.get()
                self.assertEqual(waiting_for_stock(plan), not plan_first)
                self.assertEqual(plan.guangdong_health["quantity"], 101)
