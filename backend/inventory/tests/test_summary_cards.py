import uuid
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from inventory.models import InventoryWriteAuthority, InventoryStockLine
from inventory.import_service import import_inventory_payload
from inventory.query import inventory_age_analysis, inventory_inbound_monitor
from sales.auth import Principal
from inventory.tests.test_imports import stock_row, stock_payload
from inventory.warehouse_mapping_service import mapping_payload, update_mapping
from sales.tests.factories import TEST_SECRET, signed_headers


class InventorySummaryCardTests(TestCase):
    def setUp(self):
        InventoryWriteAuthority.objects.filter(id=1).update(status="postgres", authority_epoch=uuid.uuid4(), cutover_id="cards", migration_verify_run_id="inventory-apply-" + "1" * 32, activated_at=timezone.now())
        rows = []
        for index, (age, sales, available) in enumerate(((89, 0, 10), (90, 0, 10), (120, 2, 10), (150, None, 10), (200, 0, 0))):
            row = stock_row(f"CARD-{index}", index + 2, available=available)
            row.update(inventoryAgeDays=age, sales30dQuantity=sales or 0)
            rows.append(row)
        import_inventory_payload(stock_payload(*rows), "admin@example.test")
        InventoryStockLine.objects.filter(product_code="CARD-3").update(sales_30d_quantity=None)

    def test_card_predicates_match_metrics_before_pagination(self):
        base = inventory_age_analysis({"page": 1, "pageSize": 100})
        for key, metric in (("stagnant", "stagnantCount"), ("aged90", "aged90Count"), ("zero_sales", "zeroSalesCount")):
            result = inventory_age_analysis({"page": 1, "pageSize": 1, "cardFilter": key})
            self.assertEqual(result["pagination"]["total"], base["metrics"][metric])
            self.assertEqual(len(result["items"]), 1)
        result = inventory_age_analysis({"page": 1, "pageSize": 100, "cardFilter": "zero_sales"})
        self.assertEqual({row["productCode"] for row in result["items"]}, {"CARD-0", "CARD-1"})
        result = inventory_age_analysis({"page": 1, "pageSize": 100, "cardFilter": "aged90", "query": "CARD-2"})
        self.assertEqual(result["pagination"]["total"], 1)

    def test_age_items_disclose_the_controlled_warehouse_mapping(self):
        InventoryStockLine.objects.filter(product_code="CARD-0").update(
            warehouse="膳师傅仓库",
            warehouse_type="other",
            warehouse_category="dropship",
            include_in_inventory=True,
        )
        result = inventory_age_analysis({"page": 1, "pageSize": 100})
        item = next(row for row in result["items"] if row["productCode"] == "CARD-0")
        self.assertEqual(item["warehouseCategory"], "dropship")
        self.assertEqual(item["warehouseLabel"], "代发仓")
        self.assertTrue(item["includedInInventory"])

        current = mapping_payload()
        update_mapping({
            "expectedMappingRevision": current["mappingRevision"],
            "mappings": [{
                "warehouse": "膳师傅仓库",
                "category": "sample",
                "includeInInventory": False,
            }],
        }, "admin@example.test")
        refreshed = inventory_age_analysis({"page": 1, "pageSize": 100})
        refreshed_item = next(row for row in refreshed["items"] if row["productCode"] == "CARD-0")
        self.assertEqual(refreshed_item["warehouseCategory"], "sample")
        self.assertEqual(refreshed_item["warehouseLabel"], "样品仓")
        self.assertFalse(refreshed_item["includedInInventory"])

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_unknown_and_duplicate_card_parameters_are_rejected(self):
        for path in ("age-analysis", "inbound-monitor"):
            for query in ("cardFilter=unknown", "cardFilter=stale&cardFilter=stale"):
                url = f"/api/inventory/{path}?{query}"
                response = self.client.get(url, headers=signed_headers(url))
                self.assertEqual(response.status_code, 400, response.content)

    @patch("inventory.query._sales_revision", return_value="card-revision")
    @patch("inventory.query._sales_query", return_value={"asOfDate": "2026-09-01", "rows": [], "truncated": False})
    def test_inbound_risk_card_filters_before_pagination(self, _sales, _revision):
        InventoryStockLine.objects.update(warehouse="京东RDC仓", warehouse_type="jd_rdc")
        principal = Principal("viewer@example.test", "查看", "viewer", None)
        result = inventory_inbound_monitor(principal, {"page": 1, "pageSize": 1, "cardFilter": "stale"})
        self.assertEqual(result["pagination"]["total"], 3)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["risk"], "stale")
        self.assertEqual(inventory_inbound_monitor(principal, {"page": 1, "pageSize": 1})["pagination"]["total"], 5)
