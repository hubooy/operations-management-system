from datetime import timedelta
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from inventory import guangdong as gd
from inventory import guangdong_replenishment as remaining
from inventory.errors import InventoryApiError
from inventory.import_service import import_inventory_payload
from inventory.models import (GuangdongMonitorItem, InventoryImportBatch,
                              InventoryStockLine, InventoryWriteAuthority, ReplenishmentPlanItem)
from inventory.query import _latest_batch
from inventory.tests.test_imports import stock_payload, stock_row
from inventory.warehouse_mapping import classify_warehouse
from sales.auth import Principal


class GuangdongRemainingTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        InventoryWriteAuthority.objects.filter(id=1).update(
            status="postgres", authority_epoch=uuid.uuid4(), cutover_id="remaining-test",
            migration_verify_run_id="remaining-test", activated_at=timezone.now(),
        )

    def publish(self, quantity, day=0, warehouse="广东仓", code="P", available=None):
        row = stock_row(code, 2, warehouse=warehouse, available=quantity if available is None else available)
        mapping = classify_warehouse(warehouse)
        row.update(warehouseType=mapping.warehouse_type, warehouseCategory=mapping.category,
                   includeInInventory=mapping.include_in_inventory, onHandQuantity=quantity,
                   snapshotDate=(self.today + timedelta(days=day)).isoformat())
        payload = stock_payload(row, raw_seed=uuid.uuid4().hex)
        payload["snapshotDate"] = row["snapshotDate"]
        return import_inventory_payload(payload, "remaining@example.invalid")

    def item(self, order_day=-4, quantity=100, code="P"):
        return {"productCode": code, "replenishmentQuantity": quantity,
                "latestReplenishmentOrderDate": (self.today + timedelta(days=order_day)).isoformat()}

    def calculate(self, item):
        latest = _latest_batch("stock")
        remaining.add_remaining_quantities([item], latest)
        return item

    def test_sums_each_increase_without_subtracting_declines_or_available_unlocks(self):
        for day, quantity in [(-4, 100), (-3, 90), (-2, 120), (-1, 110), (0, 140)]:
            self.publish(quantity, day, available=1000)
        item = self.calculate(self.item())
        self.assertEqual(item["replenishmentStockIncreaseQuantity"], 60)
        self.assertEqual(item["replenishmentRemainingQuantity"], 40)
        self.assertEqual(item["replenishmentRemainingReason"], "")

    def test_same_day_is_baseline_and_over_receipt_stays_negative(self):
        self.publish(100, -1)
        item = self.calculate(self.item(order_day=-1, quantity=20))
        self.assertEqual(item["replenishmentRemainingQuantity"], 20)
        self.publish(125)
        item = self.calculate(self.item(order_day=-1, quantity=20))
        self.assertEqual(item["replenishmentRemainingQuantity"], -5)

    def test_missing_baseline_missing_day_and_nearby_warehouse_never_become_zero(self):
        self.publish(100, -2)
        self.publish(120)
        self.assertIsNone(self.calculate(self.item(order_day=-3))["replenishmentRemainingQuantity"])
        self.assertIsNone(self.calculate(self.item(order_day=-2))["replenishmentRemainingQuantity"])
        self.publish(110, -1, warehouse="广东仓-欧洲站")
        self.assertIsNone(self.calculate(self.item(order_day=-2))["replenishmentRemainingQuantity"])
        self.publish(110, -1)
        self.assertEqual(self.calculate(self.item(order_day=-2))["replenishmentRemainingQuantity"], 80)

    def test_same_day_replacement_and_duplicate_do_not_double_count(self):
        self.publish(100, -2)
        self.publish(110, -1)
        self.publish(120, -1)
        self.assertEqual(self.publish(120, -1)["status"], "duplicate")
        self.publish(115)
        item = self.calculate(self.item(order_day=-2))
        self.assertEqual(item["replenishmentStockIncreaseQuantity"], 20)
        self.assertEqual(item["replenishmentRemainingQuantity"], 80)

    def test_failed_batch_and_duplicate_identity_fail_closed(self):
        self.publish(100, -1)
        self.publish(110)
        batch = InventoryImportBatch.objects.get(id=_latest_batch("stock").id)
        InventoryStockLine.objects.create(
            batch_id=batch.id, snapshot_date=self.today, row_key="duplicate-P",
            source_row_number=3, warehouse="广东仓", product_code="P", on_hand_quantity=1000,
        )
        self.assertIsNone(self.calculate(self.item(order_day=-1))["replenishmentRemainingQuantity"])
        InventoryStockLine.objects.filter(row_key="duplicate-P").delete()
        batch.status = "rejected"
        batch.save(update_fields=["status"])
        item = self.item(order_day=-1)
        remaining.add_remaining_quantities([item], batch)
        self.assertIsNone(item["replenishmentRemainingQuantity"])

    def test_unavailable_plan_date_and_snapshot_have_explanations(self):
        self.publish(100)
        items = [self.item(quantity=None), self.item(order_day=1), self.item(order_day=-367),
                 {**self.item(), "latestReplenishmentOrderDate": None}]
        remaining.add_remaining_quantities(items, _latest_batch("stock"))
        for item in items:
            self.assertIsNone(item["replenishmentRemainingQuantity"])
            self.assertTrue(item["replenishmentRemainingReason"])
        item = self.item(order_day=0)
        remaining.add_remaining_quantities([item], None)
        self.assertIsNone(item["replenishmentRemainingQuantity"])

    def test_history_query_is_bounded_and_read_only(self):
        self.publish(100, -1)
        self.publish(101)
        latest = _latest_batch("stock")
        with self.assertNumQueries(1):
            item = self.item(order_day=-1)
            remaining.add_remaining_quantities([item], latest)
        with patch.object(remaining, "MAX_HISTORY_ROWS", 1), self.assertRaises(InventoryApiError):
            remaining.add_remaining_quantities([self.item(order_day=-1)], latest)

    def test_monitor_and_export_use_latest_non_cancelled_exact_warehouse_order(self):
        for day, quantity in [(-2, 100), (-1, 120), (0, 130)]:
            self.publish(quantity, day)
        GuangdongMonitorItem.objects.create(product_code="P", updated_by="test")
        for identifier, day, quantity, warehouse, status in [
            ("old", -2, 100, "广东仓", "draft"),
            ("latest", -1, 40, "广东仓", "confirmed"),
            ("cancelled", 0, 500, "广东仓", "cancelled"),
            ("other", 0, 900, "广东仓-欧洲站", "draft"),
        ]:
            ReplenishmentPlanItem.objects.create(
                id=identifier, source_batch_id="test", product_code="P", product_name="Test",
                warehouse=warehouse, status=status, planned_quantity=quantity, suggested_quantity=quantity,
                order_date=self.today + timedelta(days=day),
            )
        principal = Principal("remaining@example.invalid", "Test", "admin", None)
        sales = {"rows": [], "truncated": False, "asOfDate": None, "dataStartDate": None}
        with patch.object(gd, "_sales_query", return_value=sales):
            result = gd.monitor(principal, {})
            exported = gd.monitor(principal, {"version": result["version"]}, export=True)
        item = result["items"][0]
        self.assertEqual((item["replenishmentQuantity"], item["replenishmentStockIncreaseQuantity"],
                          item["replenishmentRemainingQuantity"]), (40, 10, 30))
        self.assertEqual(exported["items"][0]["replenishmentRemainingQuantity"], 30)
