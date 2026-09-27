from __future__ import annotations

import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from sales.models import SalesOrderLine

from .factories import TEST_SECRET, install_fixture, make_line, signed_headers


URL = "/api/sales/consumers/query"


def encoded(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class SalesConsumerApiTests(TestCase):
    def setUp(self) -> None:
        cache.clear()
        install_fixture()

    def post(self, payload: object, *, scope=None, role: str = "admin"):
        body = encoded(payload)
        return self.client.post(
            URL,
            data=body,
            content_type="application/json",
            headers=signed_headers(
                URL, scope=scope, role=role, method="POST", body=body
            ),
        )

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_freshness_inventory_and_product_queries_are_scoped_and_revisioned(self) -> None:
        freshness = self.post({"operation": "freshness"})
        self.assertEqual(freshness.status_code, 200, freshness.content)
        self.assertEqual(
            freshness.json(),
            {
                "operation": "freshness",
                "data": {
                    "dataStartDate": "2026-08-01",
                    "dataCutoffDate": "2026-08-02",
                    "latestBatch": {
                        "id": "batch-1",
                        "fileName": "sales.xlsx",
                        "completedAt": "2026-08-01 11:01:00",
                        "rowCount": 5,
                    },
                },
            },
        )
        self.assertEqual(freshness["X-Sales-Data-Revision"], "7:3")
        self.assertEqual(freshness["X-Sales-Source-Revision"], "7:3")

        inventory = self.post(
            {
                "operation": "inventory_demand",
                "startDate": "2026-08-01",
                "endDate": "2026-08-03",
                "productCodes": ["P1", "P4"],
                "limit": 100,
            }
        )
        self.assertEqual(inventory.status_code, 200, inventory.content)
        inventory_data = inventory.json()["data"]
        self.assertFalse(inventory_data["truncated"])
        self.assertEqual(
            inventory_data["rows"],
            [
                {
                    "productCode": "P1",
                    "warehouseKey": "主",
                    "productName": "饮水机",
                    "salesQuantity": 2,
                    "absoluteQuantity": 3,
                    "absoluteCostCents": 8_500,
                }
            ],
        )

        SalesOrderLine.objects.bulk_create(
            [make_line(
                6, "L6", quantity=5,
                ship_time="2026-07-24 10:00:00",
                line_ship_time="2026-07-24 10:00:00",
            )]
        )

        inbound = self.post(
            {
                "operation": "inventory_inbound_windows",
                "asOfDate": "2026-08-02",
                "productCodes": ["P1", "P4"],
                "limit": 100,
            }
        )
        self.assertEqual(inbound.status_code, 200, inbound.content)
        inbound_data = inbound.json()["data"]
        self.assertEqual(inbound_data["asOfDate"], "2026-08-02")
        self.assertEqual(inbound_data["dataCutoffDate"], "2026-08-02")
        self.assertFalse(inbound_data["truncated"])
        self.assertEqual(
            inbound_data["rows"],
            [
                {
                    "productCode": "P1",
                    "warehouseKey": "主",
                    "productName": "饮水机",
                    "sales7dQuantity": 2,
                    "sales15dQuantity": 7,
                    "sales30dQuantity": 7,
                    "sales90dQuantity": 7,
                }
            ],
        )
        SalesOrderLine.objects.filter(source_line_key="L6").delete()

        product = self.post(
            {
                "operation": "product_performance",
                "startDate": "2026-08-01",
                "endDate": "2026-08-03",
                "platforms": ["京东"],
                "productCodes": ["P1"],
                "limit": 100,
            }
        )
        self.assertEqual(product.status_code, 200, product.content)
        product_data = product.json()["data"]
        self.assertFalse(product_data["truncated"])
        self.assertEqual(product_data["dataStartDate"], "2026-08-01")
        self.assertEqual(product_data["dataCutoffDate"], "2026-08-02")
        self.assertEqual(product_data["rows"][0]["productCode"], "P1")
        self.assertEqual(product_data["rows"][0]["netSalesCents"], 8_000)
        self.assertEqual(product_data["rows"][0]["absoluteQuantity"], 3)
        self.assertEqual(product_data["rows"][0]["outlets"][0]["shopName"], "京东一店")

        restricted = self.post(
            {"operation": "freshness"},
            scope={"warehouses": [], "channels": [], "platforms": ["天猫"]},
        )
        self.assertEqual(restricted.status_code, 200)
        self.assertIsNone(restricted.json()["data"]["dataCutoffDate"])
        self.assertIsNone(restricted.json()["data"]["latestBatch"])

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_summary_customer_netshop_and_market_contracts(self) -> None:
        summary = self.post(
            {
                "operation": "summary",
                "range": "custom",
                "startDate": "2026-08-01",
                "endDate": "2026-08-03",
                "platforms": ["京东"],
            },
            scope={"warehouses": [], "channels": [], "platforms": ["京东"]},
        )
        self.assertEqual(summary.status_code, 200, summary.content)
        self.assertEqual(summary.json()["data"]["current"]["netSalesCents"], 14_000)
        self.assertNotIn("filterOptions", summary.json()["data"])
        self.assertNotIn("latestBatch", summary.json()["data"])

        SalesOrderLine.objects.filter(product_code="P1").update(online_spec_code="SPEC-1")
        customer = self.post(
            {
                "operation": "customer_service_products",
                "onlineSpecCodes": ["SPEC-1"],
                "limit": 100,
            }
        )
        self.assertEqual(customer.status_code, 200, customer.content)
        self.assertEqual(customer.json()["data"]["rows"][0]["onlineSpecCode"], "SPEC-1")
        self.assertTrue(all(row["productCode"] == "P1" for row in customer.json()["data"]["rows"]))

        make_line(
            6,
            "L6",
            channel="京东-直营网",
            shop_name="原始店铺",
            online_spec_code="JD-SKU-1",
            product_code="ERP-1",
            quantity=2,
            allocated_amount_cents=4_000,
            cost_amount_cents=2_000,
            gross_profit_cents=2_000,
            ship_time="2026-08-02 15:00:00",
        ).save(force_insert=True)
        netshop = self.post(
            {
                "operation": "netshop_product_metrics",
                "identities": [
                    {
                        "platform": "京东",
                        "canonicalShopName": "规范店铺",
                        "rawShopName": "原始店铺",
                        "rawChannel": "京东-直营网",
                        "salesProductCode": "JD-SKU-1",
                    },
                    {
                        "platform": "京东",
                        "canonicalShopName": "规范店铺",
                        "rawShopName": "原始店铺",
                        "rawChannel": None,
                        "salesProductCode": "JD-SKU-1",
                    },
                ],
                "outletScopes": [
                    {
                        "platform": "京东",
                        "canonicalShopName": "规范店铺",
                        "rawShopName": "原始店铺",
                        "rawChannel": "京东-直营网",
                    }
                ],
                "startDate": "2026-08-01",
                "endDate": "2026-08-03",
                "allowedChannels": ["京东-直营网"],
            }
        )
        self.assertEqual(netshop.status_code, 200, netshop.content)
        self.assertEqual(netshop.json()["data"]["dataCutoffDate"], "2026-08-02")
        self.assertEqual(netshop.json()["data"]["rows"][0]["shopName"], "规范店铺")
        self.assertEqual(netshop.json()["data"]["rows"][0]["netSalesCents"], 4_000)

        market = self.post(
            {
                "operation": "market_product_metrics",
                "productCodes": ["P1", "missing", "P4"],
                "startDate": "2026-08-01",
                "endDate": "2026-08-03",
            }
        )
        self.assertEqual(market.status_code, 200, market.content)
        market_rows = {row["productCode"]: row for row in market.json()["data"]["rows"]}
        self.assertEqual(market_rows["P1"], {"productCode": "P1", "owned": True, "ownSalesCents": 8_000})
        self.assertEqual(market_rows["missing"], {"productCode": "missing", "owned": False, "ownSalesCents": 0})
        self.assertEqual(market_rows["P4"], {"productCode": "P4", "owned": False, "ownSalesCents": 0})

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_search_batch_and_category_operations_preserve_authorization(self) -> None:
        orders = self.post(
            {"operation": "order_search", "query": "order-1", "page": 1, "pageSize": 10}
        )
        self.assertEqual(orders.status_code, 200, orders.content)
        self.assertEqual(orders.json()["data"]["total"], 1)
        self.assertEqual(orders.json()["data"]["items"][0]["amountCents"], 10_000)

        batches = self.post(
            {"operation": "import_batch_search", "query": "sales", "page": 1, "pageSize": 10}
        )
        self.assertEqual(batches.status_code, 200, batches.content)
        self.assertEqual(batches.json()["data"]["items"][0]["id"], "batch-1")
        denied = self.post(
            {"operation": "import_batch_search", "query": "sales", "page": 1, "pageSize": 10},
            role="viewer",
        )
        self.assertEqual(denied.status_code, 403)

        categories = self.post({"operation": "category_options", "limit": 100})
        self.assertEqual(categories.status_code, 200, categories.content)
        self.assertNotIn("排除品类", categories.json()["data"]["categories"])
        self.assertIn("旧类目", categories.json()["data"]["categories"])

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_unknown_fields_wrong_types_and_unbounded_inputs_fail_closed(self) -> None:
        invalid = [
            {"operation": "freshness", "sql": "SELECT * FROM sales_order_lines"},
            {"operation": "inventory_demand", "productCodes": "P1"},
            {"operation": "inventory_demand", "productCodes": ["P"] * 501},
            {"operation": "inventory_inbound_windows", "productCodes": []},
            {"operation": "inventory_inbound_windows", "productCodes": ["P"] * 5_001},
            {
                "operation": "product_performance",
                "startDate": "2026-08-03",
                "endDate": "2026-08-03",
            },
            {
                "operation": "summary",
                "range": "custom",
                "startDate": "2026-08-03",
                "endDate": "2026-08-03",
            },
            {
                "operation": "summary",
                "range": "custom",
                "startDate": "2026-08-03",
            },
            {"operation": "category_options", "limit": True},
            {
                "operation": "netshop_product_metrics",
                "identities": [{"platform": "京东", "canonicalShopName": "店", "rawShopName": "店", "salesProductCode": "P", "sql": "x"}],
                "outletScopes": [],
            },
        ]
        for payload in invalid:
            response = self.post(payload)
            self.assertEqual(response.status_code, 400, (payload, response.content))

        duplicate = '{"operation":"freshness","operation":"summary"}'
        response = self.client.post(
            URL,
            data=duplicate,
            content_type="application/json",
            headers=signed_headers(URL, method="POST", body=duplicate),
        )
        self.assertEqual(response.status_code, 400)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    @patch("sales.views.revision_token", side_effect=["7:3", "8:3", "8:3", "9:3"])
    def test_consumer_response_fails_when_revision_keeps_changing(self, _revision) -> None:
        response = self.post({"operation": "freshness"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "sales_overview_revision_changed")
        self.assertEqual(response["Retry-After"], "1")
