from __future__ import annotations

from unittest.mock import patch
from urllib.parse import urlencode

from django.test import TestCase

from netshop.models import NetshopDataRevision, NetshopImportBatch, NetshopPromotionShopDaily, NetshopRow
from netshop.promotion_diagnostic import SHOP_NAME
from sales.tests.factories import TEST_SECRET, signed_headers


PATH = "/api/netshop/promotion-diagnostic"
OUTLET = f"京东\x1f{SHOP_NAME}"


class PromotionDiagnosticApiTests(TestCase):
    def setUp(self) -> None:
        NetshopDataRevision.objects.update_or_create(
            domain="netshop", defaults={"revision": 7, "source_digest": "a" * 64}
        )

    def request(self, *, role="admin", scope=None, **query):
        params = {
            "platform": "京东", "outlet": OUTLET,
            "startDate": "2026-09-20", "endDate": "2026-09-21", **query,
        }
        url = f"{PATH}?{urlencode(params)}"
        return self.client.get(url, headers=signed_headers(url, role=role, scope=scope))

    def add_day(self, day: str, rows: list[dict], *, shop: str = SHOP_NAME) -> None:
        batch_id = f"batch-{shop}-{day}"
        NetshopImportBatch.objects.create(
            id=batch_id, source="jd_promotion", dataset="ad", platform="京东", shop_name=shop,
            file_name="推广.csv", file_size_bytes=100, file_hash=day.replace("-", ""),
            raw_file_hash="a" * 64, content_hash="b" * 64, scope_key="c" * 64,
            status="completed", row_count=len(rows), inserted_count=len(rows),
            date_min=day, date_max=day, created_at="2026-09-22T00:00:00Z",
            completed_at="2026-09-22T00:00:00Z",
        )
        for index, values in enumerate(rows):
            metrics = values.get("metrics", {})
            NetshopRow.objects.create(
                source_row_key=f"{shop}-{day}-{index}", source_row_hash="f" * 64,
                first_import_batch_id=batch_id, last_import_batch_id=batch_id,
                source_row_number=index + 2, source="jd_promotion", dataset="ad", platform="京东",
                shop_name=shop, business_date=day, sku_id=values.get("sku", "SKU-1"),
                product_name=values.get("product", "商用设备"),
                raw_json=values.get("raw", {}), metrics_json=metrics,
                spend_cents=values.get("spend", 0), impressions=values.get("impressions", 0),
                clicks=values.get("clicks", 0), net_orders=values.get("orders", 0),
                net_transaction_amount_cents=values.get("gmv", 0),
                created_at="2026-09-22T00:00:00Z", updated_at="2026-09-22T00:00:00Z",
            )
        NetshopPromotionShopDaily.objects.create(
            platform="京东", shop_name=shop, business_date=day, source="jd_promotion",
            spend_cents=sum(item.get("spend", 0) for item in rows),
            impressions=sum(item.get("impressions", 0) for item in rows),
            clicks=sum(item.get("clicks", 0) for item in rows),
            net_orders=sum(item.get("orders", 0) for item in rows),
            net_transaction_amount_cents=sum(item.get("gmv", 0) for item in rows),
            source_row_count=len(rows), source_batch_id=batch_id, source_batch_count=1,
            rebuilt_at="2026-09-22T00:00:00Z",
        )

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_complete_scope_reconciles_and_preserves_unknown_groups(self) -> None:
        self.add_day("2026-09-20", [
            {"spend": 100, "impressions": 1000, "clicks": 20, "orders": 1, "gmv": 600,
             "metrics": {"spendCents": 100, "impressions": 1000, "clicks": 20,
                         "netOrders": 1, "netTransactionAmountCents": 600},
             "raw": {"账户昵称": "来源子账号", "计划ID": "P1", "推广计划": "主计划", "关键词": "商用设备", "搜索词": "商用设备多少钱"}},
            {"spend": 50, "impressions": 500, "clicks": 10, "orders": 0, "gmv": 0,
             "metrics": {"spendCents": 50, "impressions": 500, "clicks": 10},
             "sku": "SKU-2", "raw": {"搜索词": "设备价格", "总订单金额": "N/A"}},
        ])
        self.add_day("2026-09-21", [
            {"spend": 80, "impressions": 700, "clicks": 14, "orders": 2, "gmv": 900,
             "metrics": {"spendCents": 80, "impressions": 700, "clicks": 14,
                         "netOrders": 2, "netTransactionAmountCents": 900},
             "raw": {"计划ID": "P1", "推广计划": "主计划新版", "关键词": "商用设备", "搜索词": "商用设备多少钱"}},
        ])
        self.add_day("2026-09-20", [{"spend": 99}], shop="其他京东店")

        response = self.request()
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()
        self.assertEqual(result["schemaVersion"], "jd-promotion-diagnostic-v1")
        self.assertEqual(result["identity"], {"platform": "京东", "shopName": SHOP_NAME})
        self.assertEqual(result["coverage"]["rowCount"], 3)
        self.assertTrue(result["coverage"]["complete"])
        self.assertTrue(result["coverage"]["aggregateReconciled"])
        self.assertTrue(result["coverage"]["batchOwnershipReconciled"])
        self.assertEqual(result["summary"]["spendCents"], 230)
        self.assertIsNone(result["summary"]["reportedGmvCents"])
        self.assertEqual(result["metricAvailability"]["reportedGmvCents"]["presentRows"], 2)
        self.assertEqual(len(result["groups"]["products"]), 2)
        self.assertEqual(len(result["groups"]["plans"]), 2)
        self.assertEqual(next(item for item in result["groups"]["plans"] if item["planId"] == "P1")["rowCount"], 2)
        self.assertEqual(sum(item["rowCount"] for item in result["groups"]["keywordSku"]), 3)
        self.assertEqual(sum(item["rowCount"] for item in result["groups"]["planSku"]), 3)
        self.assertEqual(sum(item["rowCount"] for item in result["groups"]["planKeyword"]), 3)
        self.assertEqual(sum(item["rowCount"] for item in result["groups"]["searchTermSku"]), 3)
        self.assertTrue(any(item["planKey"] == '["P1"]' and item["skuId"] == "SKU-1"
                            for item in result["groups"]["planSku"]))
        self.assertTrue(any(item["keyword"] is None for item in result["groups"]["keywords"]))
        self.assertEqual(len(result["sourceBatches"]), 2)
        self.assertEqual(result["sourceBatches"][0]["accountNicknames"], ["来源子账号"])
        self.assertEqual(result["sourceBatches"][0]["accountPresentRows"], 1)
        self.assertEqual(result["sourceBatches"][0]["rowCount"], 2)
        self.assertEqual(result["sourceBatches"][0]["ownership"][0]["status"], "completed")
        self.assertEqual(result["sourceBatches"][0]["ownership"][0]["shopName"], SHOP_NAME)
        self.assertEqual(result["sourceBatches"][0]["ownership"][0]["warningCount"], 0)
        self.assertEqual(result["sourceBatches"][0]["aggregateOwnership"]["warningCount"], 0)
        self.assertEqual(result["sourceBatches"][1]["ownership"][0]["dateMax"], "2026-09-21")
        self.assertEqual(result["sourceRevision"], "7:aaaaaaaaaaaa")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_missing_day_is_explicit_and_aggregate_mismatch_fails_closed(self) -> None:
        self.add_day("2026-09-20", [{"spend": 20, "metrics": {"spendCents": 20}}])
        response = self.request()
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["missingDates"], ["2026-09-21"])
        self.assertEqual(result["daily"][1]["rowCount"], 0)
        self.assertIsNone(result["daily"][1]["metrics"]["spendCents"])

        NetshopPromotionShopDaily.objects.update(spend_cents=21)
        mismatch = self.request()
        self.assertEqual(mismatch.status_code, 503, mismatch.content)
        self.assertEqual(mismatch.json()["code"], "service_unavailable")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_oversized_response_is_rejected_without_truncation(self) -> None:
        self.add_day("2026-09-20", [{"spend": 20, "metrics": {"spendCents": 20}}])
        with patch("netshop.promotion_diagnostic.MAX_RESPONSE_BYTES", 100):
            response = self.request()
        self.assertEqual(response.status_code, 413, response.content)
        self.assertEqual(response.json()["code"], "response_too_large")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_missing_or_wrong_batch_ownership_fails_closed(self) -> None:
        self.add_day("2026-09-20", [{"spend": 20, "metrics": {"spendCents": 20}}])
        owner = NetshopImportBatch.objects.get(shop_name=SHOP_NAME)
        for changes in (
            {"status": "failed"},
            {"shop_name": "其他京东店"},
            {"source": "tmall_promotion"},
            {"date_min": "2026-09-21"},
            {"row_count": 0},
        ):
            with self.subTest(changes=changes):
                NetshopImportBatch.objects.filter(pk=owner.pk).update(**changes)
                response = self.request()
                self.assertEqual(response.status_code, 503, response.content)
                NetshopImportBatch.objects.filter(pk=owner.pk).update(
                    status="completed", shop_name=SHOP_NAME, source="jd_promotion",
                    date_min="2026-09-20", row_count=1,
                )
        NetshopPromotionShopDaily.objects.update(source_batch_id="unrelated-batch")
        self.assertEqual(self.request().status_code, 503)
        NetshopPromotionShopDaily.objects.update(source_batch_id=owner.pk)
        owner.delete()
        missing = self.request()
        self.assertEqual(missing.status_code, 503, missing.content)
        self.assertEqual(missing.json()["code"], "service_unavailable")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_distinct_aggregate_publication_batch_is_valid_and_separately_checked(self) -> None:
        self.add_day("2026-09-20", [{"spend": 20, "metrics": {"spendCents": 20}}])
        owner = NetshopImportBatch.objects.get(shop_name=SHOP_NAME)
        row_owner_id = owner.pk
        owner.pk = "publication-2026-09-20"
        owner.file_hash = "publication-2026-09-20"
        owner.warning_count = 2
        owner.save(force_insert=True)
        NetshopPromotionShopDaily.objects.update(source_batch_id=owner.pk)

        valid = self.request()
        self.assertEqual(valid.status_code, 200, valid.content)
        day = valid.json()["sourceBatches"][0]
        self.assertEqual(day["batchIds"], [row_owner_id])
        self.assertEqual(day["aggregateBatchId"], owner.pk)
        self.assertEqual(day["ownership"][0]["warningCount"], 0)
        self.assertEqual(day["aggregateOwnership"]["warningCount"], 2)
        self.assertTrue(valid.json()["coverage"]["batchOwnershipReconciled"])

        for changes in (
            {"status": "failed"},
            {"shop_name": "其他京东店"},
            {"dataset": "sku_daily"},
            {"date_max": "2026-09-19"},
            {"row_count": 0},
            {"warning_count": -1},
        ):
            with self.subTest(changes=changes):
                NetshopImportBatch.objects.filter(pk=owner.pk).update(**changes)
                self.assertEqual(self.request().status_code, 503)
                NetshopImportBatch.objects.filter(pk=owner.pk).update(
                    status="completed", shop_name=SHOP_NAME, dataset="ad",
                    date_max="2026-09-20", row_count=1, warning_count=2,
                )

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_admin_scope_shop_and_period_are_fenced(self) -> None:
        self.assertEqual(self.request(role="analyst").status_code, 403)
        limited = {"warehouses": [], "channels": [], "platforms": ["天猫"]}
        self.assertEqual(self.request(scope=limited).status_code, 403)
        self.assertEqual(self.request(outlet="京东\x1f其他京东店").status_code, 400)
        self.assertEqual(self.request(platform="天猫").status_code, 400)
        self.assertEqual(self.request(endDate="2026-10-21").status_code, 400)
