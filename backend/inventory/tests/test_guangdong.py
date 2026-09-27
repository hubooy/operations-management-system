from datetime import date, timedelta
import json
import uuid
from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase, TestCase, RequestFactory
from django.utils import timezone

from inventory import guangdong as gd
from inventory import guangdong_views as views
from inventory.models import GuangdongMonitorItem, GuangdongSupplierCycle, GuangdongMonitorAudit, InventoryAgeLine, InventoryWriteAuthority, InventoryImportBatch, InventoryStockLine, ReplenishmentPlanItem
from inventory.errors import InventoryApiError
from sales.models import ErpProductMaster
from sales.auth import Principal
from sales.tests.factories import signed_headers, TEST_SECRET, make_line
from sales.models import SalesOrderLine


class RiskTests(SimpleTestCase):
    def fields(self, **updates):
        args = dict(available=100, sales30=300, lead=10, buffer=7, snapshot=date(2026, 9, 8))
        args.update(updates)
        return gd.risk_fields(**args)

    def test_inclusive_thresholds_and_order_date(self):
        with patch.object(gd.timezone, "localdate", return_value=date(2026, 9, 9)):
            self.assertEqual(self.fields()["risk"], "urgent")
            self.assertEqual(self.fields(available=169)["risk"], "warning")
            self.assertEqual(self.fields(available=170)["risk"], "healthy")
            self.assertEqual(self.fields(available=171)["risk"], "healthy")
            self.assertEqual(self.fields(available=170)["latestOrderDate"], "2026-09-09")
            self.assertEqual(self.fields(available=100)["latestOrderDate"], "2026-09-09")
            self.assertEqual(self.fields(available=270)["latestOrderDate"], "2026-09-18")

    def test_missing_is_not_zero_and_all_risk_reasons_remain(self):
        self.assertEqual(self.fields(available=None)["risk"], "unknown")
        self.assertEqual(self.fields(available=0)["risk"], "no_stock")
        self.assertEqual(self.fields(available=-5)["risk"], "no_stock")
        result = self.fields(available=1801, lead=200)
        self.assertEqual(result["risk"], "stale")
        self.assertIn("库存周转大于180天", result["riskReasons"])
        self.assertIn("销售周转不超过生产周期", result["riskReasons"])

    def test_missing_cycle_zero_sales_and_unmatched_sales(self):
        self.assertEqual(self.fields(lead=None)["risk"], "unknown")
        self.assertEqual(self.fields(sales30=None)["risk"], "unknown")
        self.assertEqual(self.fields(sales30=0)["risk"], "unknown")
        self.assertEqual(self.fields(available=1800)["risk"], "healthy")
        self.assertEqual(self.fields(available=1801)["risk"], "stale")


class GuangdongTests(TestCase):
    def setUp(self):
        InventoryWriteAuthority.objects.filter(id=1).update(status="postgres", authority_epoch=uuid.uuid4(), cutover_id="gd-test", migration_verify_run_id="gd-test-verify", activated_at=timezone.now())
        self.principal = Principal(email="gd-test@example.invalid", display_name="Test", role="admin", scope=None)
        self.today = timezone.localdate()
        for code in ["00123", "B", "MISSING"]:
            ErpProductMaster.objects.create(product_code=code, product_name="测试产品" + code, specification="型号" + code, supplier="供应商甲", source_row_number=1, last_import_batch_id="test", created_at="", updated_at="")

    def save_rows(self, rows):
        preview = gd.preview(rows)
        return gd.mutate({"action": "import", "rows": rows, "version": preview["version"], "contentHash": preview["contentHash"], "source": "test"}, self.principal.email)

    def batch(self):
        batch = InventoryImportBatch.objects.create(id="gd-stock", dataset="stock", source="test", file_name="test.xlsx", file_size_bytes=1, file_hash="a" * 64, raw_file_hash="b" * 64, content_hash="c" * 64, scope_key="test", sheet_name="test", snapshot_date=self.today, status="completed", completed_at=timezone.now())
        for code, warehouse, quantity in [("00123", "广东仓", 100), ("00123", "广东仓-欧洲站", 999), ("00123", "京东仓", 999), ("B", "广东仓", 0)]:
            InventoryStockLine.objects.create(batch_id=batch.id, row_key=code + warehouse, source_row_number=1, snapshot_date=self.today, warehouse=warehouse, product_code=code, available_quantity=quantity, in_transit_quantity=1000, unit_cost_cents=500, inventory_age_days=10)
        return batch

    def sales(self, **updates):
        result = {"asOfDate": self.today.isoformat(), "dataStartDate": (self.today - timedelta(days=120)).isoformat(), "truncated": False, "rows": [
            {"productCode": "00123", "warehouseKey": gd._warehouse_key("广东仓"), "sales7dQuantity": 70, "sales15dQuantity": 150, "sales30dQuantity": 300, "sales90dQuantity": 900},
            {"productCode": "00123", "warehouseKey": gd._warehouse_key("广东仓-欧洲站"), "sales7dQuantity": 7000, "sales15dQuantity": 15000, "sales30dQuantity": 30000, "sales90dQuantity": 90000},
        ]}
        result.update(updates)
        return result

    def age_batch(self, age=45):
        batch = InventoryImportBatch.objects.create(id="gd-age", dataset="age", source="test", file_name="age.xlsx", file_size_bytes=1, file_hash="d" * 64, raw_file_hash="e" * 64, content_hash="f" * 64, scope_key="age", sheet_name="test", snapshot_date=self.today - timedelta(days=1), status="completed", completed_at=timezone.now())
        InventoryAgeLine.objects.create(batch_id=batch.id, row_key="00123广东仓", source_row_number=1, snapshot_date=batch.snapshot_date, warehouse="广东仓", product_code="00123", inventory_age_days=age)
        InventoryAgeLine.objects.create(batch_id=batch.id, row_key="00123广东仓-欧洲站", source_row_number=2, snapshot_date=batch.snapshot_date, warehouse="广东仓-欧洲站", product_code="00123", inventory_age_days=999)
        return batch

    def test_import_preview_atomic_errors_and_leading_zero(self):
        self.save_rows([{"productCode": "00123", "active": True, "notes": "首条"}])
        bad = gd.preview([{"productCode": "B"}, {"productCode": "unknown"}])
        self.assertFalse(bad["valid"])
        with self.assertRaises(InventoryApiError):
            gd.mutate({"action": "import", "rows": [{"productCode": "B"}, {"productCode": "unknown"}], "version": bad["version"], "contentHash": bad["contentHash"]}, self.principal.email)
        self.assertFalse(GuangdongMonitorItem.objects.filter(product_code="B").exists())
        self.assertTrue(GuangdongMonitorItem.objects.filter(product_code="00123").exists())
        self.assertEqual(GuangdongMonitorAudit.objects.filter(status="rejected").count(), 1)
        self.assertFalse(gd.preview([{"productCode": 123}])["valid"])
        self.assertFalse(gd.preview([{"productCode": "B", "active": True}, {"productCode": "B", "active": False}])["valid"])

    def test_semantic_idempotency_and_concurrent_pause(self):
        rows = [{"productCode": "00123", "notes": "a"}, {"productCode": "B"}]
        old = gd.preview(rows)
        self.save_rows(rows)
        revision = gd.version()
        self.assertEqual(self.save_rows(list(reversed(rows)))["status"], "unchanged")
        self.assertEqual(revision, gd.version())
        self.save_rows([{"productCode": "00123", "active": False, "notes": "a"}])
        with self.assertRaises(InventoryApiError) as caught:
            gd.mutate({"action": "import", "rows": rows, "version": old["version"], "contentHash": old["contentHash"]}, self.principal.email)
        self.assertEqual(caught.exception.status, 409)
        self.assertFalse(GuangdongMonitorItem.objects.get(product_code="00123").active)
        self.assertTrue(GuangdongMonitorItem.objects.get(product_code="B").active)

    def test_cycles_validation_and_supplier_change(self):
        self.save_rows([{"productCode": "00123"}])
        for lead, buffer in [(0, 7), (366, 7), (True, 7), (5, -1), (5, 366)]:
            with self.assertRaises(InventoryApiError):
                gd.mutate({"action": "supplier", "supplier": "供应商甲", "leadDays": lead, "bufferDays": buffer, "version": gd.version()}, self.principal.email)
        gd.mutate({"action": "supplier", "supplier": "供应商甲", "leadDays": 10, "version": gd.version()}, self.principal.email)
        self.assertEqual(GuangdongSupplierCycle.objects.get(supplier="供应商甲").buffer_days, 7)
        self.batch()
        InventoryStockLine.objects.filter(warehouse="广东仓", product_code="00123").update(supplier="供应商乙")
        with patch.object(gd, "_sales_query", return_value=self.sales()):
            item = gd.monitor(self.principal, {})["items"][0]
        self.assertEqual(item["supplier"], "供应商乙")
        self.assertIsNone(item["leadDays"])

    def test_exact_warehouse_distribution_export_and_in_transit(self):
        self.save_rows([{"productCode": code} for code in ["00123", "B", "MISSING"]])
        self.batch()
        self.age_batch()
        GuangdongSupplierCycle.objects.create(supplier="供应商甲", lead_days=10, updated_by="test")
        ReplenishmentPlanItem.objects.create(id="gd-plan", source_batch_id="gd-stock", product_code="00123", product_name="测试产品00123", warehouse="广东仓", order_date=self.today - timedelta(days=2), suggested_quantity=40, planned_quantity=35, operator_name="运营甲", buyer="采购甲")
        with patch.object(gd, "_sales_query", return_value=self.sales()):
            result = gd.monitor(self.principal, {"pageSize": 1})
            self.assertEqual(result["metrics"]["availableQuantity"], 100)
            self.assertEqual(result["metrics"]["missingStockCount"], 1)
            self.assertEqual(sum(row["itemCount"] for row in result["distribution"]), 3)
            self.assertEqual(sum(row["quantity"] for row in result["distribution"]), 100)
            full = gd.monitor(self.principal, {"version": result["version"], "pageSize": 1}, export=True)
            self.assertEqual(len(full["items"]), 3)
            item = next(row for row in full["items"] if row["productCode"] == "00123")
            self.assertEqual(item["risk"], "urgent")
            self.assertEqual(item["turnoverDays"], 10)
            self.assertEqual(item["inTransitQuantity"], 1000)
            self.assertEqual(item["outbound15dQuantity"], 150)
            self.assertEqual(item["inventoryAgeDays"], 45)
            self.assertEqual(item["replenishmentQuantity"], 35)
            self.assertEqual(item["latestReplenishmentOrderDate"], (self.today - timedelta(days=2)).isoformat())
            self.assertEqual(item["operatorName"], "运营甲")
            self.assertEqual(item["buyer"], "采购甲")
            self.assertIsNone(item["leadDaysOverride"])
            self.assertEqual(item["cycleSource"], "供应商设置")
            self.assertEqual(item["operatorNameSource"], "最新备货计划")
            self.assertEqual(item["buyerSource"], "最新备货计划")
            self.assertEqual(full["sync"]["inventoryAgeAsOf"], (self.today - timedelta(days=1)).isoformat())
            with self.assertRaises(InventoryApiError): gd.monitor(self.principal, {"version": "old"}, export=True)

    def test_item_overrides_and_default_sources(self):
        self.save_rows([{"productCode": "00123"}]); self.batch()
        GuangdongSupplierCycle.objects.create(supplier="供应商甲", lead_days=12, buffer_days=8, updated_by="test")
        ReplenishmentPlanItem.objects.create(id="gd-plan-defaults", source_batch_id="gd-stock", product_code="00123", product_name="测试产品00123", warehouse="广东仓", order_date=self.today, suggested_quantity=20, planned_quantity=20, operator_name="运营默认", buyer="采购默认")
        with patch.object(gd, "_sales_query", return_value=self.sales()):
            initial = gd.monitor(self.principal, {})["items"][0]
        self.assertEqual((initial["leadDays"], initial["bufferDays"], initial["cycleSource"]), (12, 8, "供应商设置"))
        self.assertEqual((initial["supplierLeadDays"], initial["supplierBufferDays"]), (12, 8))
        self.assertEqual((initial["operatorName"], initial["operatorNameSource"]), ("运营默认", "最新备货计划"))
        self.assertEqual((initial["planOperatorName"], initial["planBuyer"]), ("运营默认", "采购默认"))

        before_version = gd.version()
        payload = {"action": "item", "productCode": "00123", "leadDays": 20, "bufferDays": 5, "operatorName": "运营覆盖", "buyer": "采购覆盖", "risk": "healthy", "riskReason": "已核实现货可持续供应", "version": before_version}
        self.assertEqual(gd.mutate(payload, self.principal.email)["status"], "saved")
        self.assertEqual(gd.mutate(payload, self.principal.email)["status"], "unchanged")
        with patch.object(gd, "_sales_query", return_value=self.sales()):
            overridden = gd.monitor(self.principal, {})["items"][0]
        self.assertEqual((overridden["leadDays"], overridden["bufferDays"], overridden["cycleSource"]), (20, 5, "型号设置"))
        self.assertEqual((overridden["operatorName"], overridden["operatorNameSource"]), ("运营覆盖", "型号设置"))
        self.assertEqual((overridden["buyer"], overridden["buyerSource"]), ("采购覆盖", "型号设置"))
        self.assertEqual((overridden["risk"], overridden["riskSource"]), ("healthy", "型号设置"))
        self.assertEqual(overridden["autoRisk"], "urgent")
        self.assertIn("人工设置：已核实现货可持续供应", overridden["riskReasons"])
        self.assertIn("系统原判：紧急补货", overridden["riskReasons"][1])

        with self.assertRaises(InventoryApiError):
            gd.mutate({**payload, "leadDays": None, "bufferDays": 5, "version": gd.version()}, self.principal.email)
        with self.assertRaises(InventoryApiError):
            gd.mutate({**payload, "riskReason": "", "version": gd.version()}, self.principal.email)
        with self.assertRaises(InventoryApiError) as caught:
            gd.mutate({**payload, "buyer": "另一采购", "version": before_version}, self.principal.email)
        self.assertEqual(caught.exception.status, 409)
        cleared = {"action": "item", "productCode": "00123", "leadDays": None, "bufferDays": None, "operatorName": "", "buyer": "", "risk": "", "riskReason": "", "version": gd.version()}
        gd.mutate(cleared, self.principal.email)
        with patch.object(gd, "_sales_query", return_value=self.sales()):
            restored = gd.monitor(self.principal, {})["items"][0]
        self.assertEqual((restored["leadDays"], restored["bufferDays"], restored["cycleSource"]), (12, 8, "供应商设置"))
        self.assertEqual((restored["operatorName"], restored["buyer"]), ("运营默认", "采购默认"))
        self.assertEqual((restored["risk"], restored["riskSource"]), ("urgent", "系统判定"))

    def test_pause_missing_coverage_and_snapshot_age_while_zero_cost_remains_valid(self):
        self.save_rows([{"productCode": "00123"}, {"productCode": "B", "active": False}])
        batch = self.batch()
        InventoryStockLine.objects.filter(warehouse="广东仓", product_code="00123").update(unit_cost_cents=0)
        with patch.object(gd, "_sales_query", return_value=self.sales(dataStartDate=self.today.isoformat())):
            result = gd.monitor(self.principal, {})
        self.assertEqual(result["watchCount"], 1)
        item = result["items"][0]
        self.assertIsNone(item["outbound30dQuantity"])
        self.assertIsNone(item["turnoverDays"])
        self.assertFalse(item["costMissing"])
        self.assertEqual((item["unitCostCents"], item["knownStockValueCents"]), (0, 0))
        self.assertEqual(item["risk"], "unknown")
        batch.snapshot_date = self.today - timedelta(days=4); batch.save()
        GuangdongSupplierCycle.objects.create(supplier="供应商甲", lead_days=1, buffer_days=0, updated_by="test")
        with patch.object(gd, "_sales_query", return_value=self.sales()): result = gd.monitor(self.principal, {})
        self.assertTrue(result["sync"]["inventoryStale"])
        self.assertEqual(result["items"][0]["risk"], "unknown")

    def test_sales_response_truncation_duplicates_and_revision_fence(self):
        self.save_rows([{"productCode": "00123"}]); self.batch()
        for sales in [self.sales(truncated=True), self.sales(rows=self.sales()["rows"] * 2), self.sales(rows=[{"productCode": "outsider", "warehouseKey": "广东"}])]:
            with patch.object(gd, "_sales_query", return_value=sales), self.assertRaises(InventoryApiError): gd.monitor(self.principal, {})
        with patch.object(gd, "_sales_query", return_value=self.sales()), patch.object(gd, "version", side_effect=["a", "b"]), self.assertRaises(InventoryApiError): gd.monitor(self.principal, {})

    def test_http_role_scope_and_invalid_parameters(self):
        factory = RequestFactory()
        viewer = Principal(email="viewer@example.invalid", display_name="Test", role="viewer", scope=None)
        with patch("inventory.views.verify_principal", return_value=viewer):
            response = views.imports(factory.post("/api/inventory/guangdong-monitor/import", data=json.dumps({}), content_type="application/json"))
            self.assertEqual(response.status_code, 403)
            response = views.items(factory.patch("/api/inventory/guangdong-monitor/items", data=json.dumps({}), content_type="application/json"))
            self.assertEqual(response.status_code, 403)
            self.assertEqual(views.monitor(factory.get("/api/inventory/guangdong-monitor?warehouse=京东仓")).status_code, 400)
        restricted = Principal(email="scope@example.invalid", display_name="Test", role="admin", scope={"platformNames": ["京东"]})
        with patch("inventory.views.verify_principal", return_value=restricted):
            self.assertEqual(views.watchlist(factory.get("/api/inventory/guangdong-monitor/watchlist")).status_code, 403)

    def test_postgres_constraints(self):
        if connection.vendor != "postgresql": self.skipTest("PostgreSQL镜像约束验证")
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            GuangdongSupplierCycle.objects.create(supplier="非法", lead_days=0, updated_by="test")
        with self.assertRaises(IntegrityError), transaction.atomic():
            GuangdongMonitorItem.objects.create(product_code="invalid-pair", lead_days_override=1, buffer_days_override=None, updated_by="test")
        with self.assertRaises(IntegrityError), transaction.atomic():
            GuangdongMonitorItem.objects.create(product_code="invalid-risk", risk_override="healthy", risk_reason_override=None, updated_by="test")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_signed_http_preview_commit_replay_and_export_header(self):
        base = "/api/inventory/guangdong-monitor"
        body = json.dumps({"rows": [{"productCode": "00123"}]})
        response = self.client.post(base + "/preview", data=body, content_type="application/json", headers=signed_headers(base + "/preview", method="POST", body=body))
        self.assertEqual(response.status_code, 200, response.content)
        preview = response.json()
        self.assertRegex(response["X-Inventory-Data-Revision"], r"^\d+:[a-f0-9]{12}$")
        body = json.dumps({"action": "import", "rows": [{"productCode": "00123"}], "version": preview["version"], "contentHash": preview["contentHash"]})
        headers = signed_headers(base + "/import", method="POST", body=body, request_id="gd-write-request")
        first = self.client.post(base + "/import", data=body, content_type="application/json", headers=headers)
        self.assertEqual(first.status_code, 200, first.content)
        replay = self.client.post(base + "/import", data=body, content_type="application/json", headers=headers)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        response = self.client.get(base, headers=signed_headers(base))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertRegex(response["X-Inventory-Data-Revision"], r"^\d+:[a-f0-9]{12}$")
        item_body = json.dumps({"action": "item", "productCode": "00123", "leadDays": 18, "bufferDays": 6, "operatorName": "运营覆盖", "buyer": "采购覆盖", "risk": "warning", "riskReason": "HTTP人工调整", "version": response.json()["version"]})
        item_headers = signed_headers(base + "/items", method="PATCH", body=item_body, request_id="gd-item-write-request")
        item_response = self.client.patch(base + "/items", data=item_body, content_type="application/json", headers=item_headers)
        self.assertEqual(item_response.status_code, 200, item_response.content)
        item_replay = self.client.patch(base + "/items", data=item_body, content_type="application/json", headers=item_headers)
        self.assertEqual(item_replay["X-Teruisi-Write-Replay"], "1")
        response = self.client.get(base, headers=signed_headers(base))
        self.assertEqual(response.json()["items"][0]["operatorName"], "运营覆盖")
        self.assertEqual(response.json()["items"][0]["cycleSource"], "型号设置")
        self.assertEqual(response.json()["items"][0]["riskSource"], "型号设置")
        self.assertEqual(response.json()["items"][0]["riskReasonOverride"], "HTTP人工调整")
        url = base + "/export?kind=monitor&version=" + response.json()["version"]
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()["items"]), 1)
        bad = signed_headers(base); bad["X-Teruisi-Signature"] = "v1=" + "0" * 64
        self.assertEqual(self.client.get(base, headers=bad).status_code, 401)

    def test_real_sales_consumer_excludes_refunds_and_other_warehouses(self):
        self.save_rows([{"productCode": "00123"}]); self.batch()
        rows = []
        for index, days, warehouse, quantity in [(1, 100, "广东仓", 10), (2, 0, "广东仓", 30), (3, 0, "广东仓", -20), (4, 0, "广东仓-欧洲站", 900)]:
            when = (self.today - timedelta(days=days)).isoformat() + " 10:00:00"
            rows.append(make_line(index, f"gd-sale-{index}", product_code="00123", warehouse=warehouse, quantity=quantity, ship_time=when, line_ship_time=when, business_type="退款" if quantity < 0 else "销售"))
        SalesOrderLine.objects.bulk_create(rows)
        item = gd.monitor(self.principal, {})["items"][0]
        self.assertEqual(item["outbound30dQuantity"], 30)
        self.assertEqual(item["turnoverDays"], 100)
