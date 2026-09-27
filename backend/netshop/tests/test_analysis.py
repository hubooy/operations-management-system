from unittest.mock import patch
import importlib
from types import ModuleType
from urllib.parse import urlencode

from django.test import TestCase, override_settings
from django.http import QueryDict
from django.urls import include, path

from business_analysis.contracts import PageReconciler
from netshop.analysis import read_page, validate_request
from netshop.errors import NetshopApiError
from netshop.models import NetshopDataRevision, NetshopImportBatch, NetshopRow
from sales.tests.factories import TEST_SECRET, signed_headers


class AnalysisRecordsTests(TestCase):
    def setUp(self):
        # The synthetic rows and their final revision are one TestCase
        # transaction, matching the owning importer's atomic publication.
        NetshopDataRevision.objects.update_or_create(domain="netshop", defaults={"revision": 6, "source_digest": "b"*64})
        for index, (day, amount, shop) in enumerate([
            ("2026-09-01", 123, "样例店A"), ("2026-09-03", 200, "样例店A"),
            ("2026-08-31", 10, "样例店A"), ("2025-09-01", 50, "样例店A"),
            ("2026-09-01", 999, "样例店B"),
        ], 1):
            NetshopRow.objects.create(
                source_row_key=f"analysis-{index}", source_row_hash=f"{index:064x}",
                first_import_batch_id="sample", last_import_batch_id="sample", source_row_number=index,
                source="jd_promotion", dataset="ad", platform="京东", shop_name=shop,
                business_date=day, sku_id=f"{index}", spu_id="P1", product_code="M1",
                spend_cents=amount, net_transaction_amount_cents=amount*10, clicks=2, impressions=20,
                net_orders=1, metrics_json={"spendCents": amount, "netTransactionAmountCents": amount*10,
                    "clicks": 2, "impressions": 20, "netOrders": 1},
                raw_json={"搜索词": "饮水机", "计划ID": "001", "推广计划": "原计划", "触发SKU ID": "trigger",
                    "跟单SKU ID": f"{index}", "智能投放推广SKU ID": "promoted", "直接订单金额": "1.23",
                    "客户姓名": "不应出现在响应中"}, created_at="2026-09-16", updated_at="2026-09-16",
            )
        NetshopDataRevision.objects.filter(domain="netshop").update(
            revision=7, source_digest="a"*64)
        self.params = {"platform": "京东", "shop": "样例店A", "dataset": "promotion",
                       "startDate": "2026-09-01", "endDate": "2026-09-03", "limit": "1"}

    def query(self, **overrides):
        return validate_request(QueryDict(urlencode({**self.params, **overrides})))

    def test_exact_shop_complete_pages_and_control_reconcile(self):
        first = read_page(*self.query())
        self.assertEqual(first["control"]["rowCount"], 2)
        self.assertEqual(first["control"]["typedTotals"]["spendCents"], 323)
        self.assertEqual(first["coverage"]["missingDates"], ["2026-09-02"])
        self.assertNotIn("客户姓名", str(first))
        projected = first["items"][0]
        self.assertIsNone(projected["dimensions"]["keyword"])
        self.assertEqual(projected["dimensions"]["planName"], "原计划")
        self.assertEqual(projected["dimensions"]["promotedSkuId"], "promoted")
        self.assertEqual(projected["dimensions"]["triggerSkuId"], "trigger")
        self.assertEqual(projected["metrics"]["directGmvCents"], 123)
        self.assertIsNone(projected["metrics"]["indirectGmvCents"])
        cursor = first["pagination"]["nextCursor"]
        second = read_page(*self.query(cursor=cursor))
        self.assertFalse(second["pagination"]["hasMore"])
        self.assertIsNone(second["control"])
        verifier = PageReconciler()
        verifier.consume(first)
        verifier.consume(second, request_cursor=cursor)
        self.assertEqual(verifier.result()["metrics"]["spendCents"]["value"], 323)

    def test_comparison_and_b2b_gap_do_not_fall_back_to_other_data(self):
        self.assertEqual(read_page(*self.query(window="previous"))["control"]["typedTotals"]["spendCents"], 10)
        self.assertEqual(read_page(*self.query(window="yearAgo"))["control"]["typedTotals"]["spendCents"], 50)
        b2b = read_page(*self.query(dataset="b2b"))
        self.assertEqual(b2b["control"]["rowCount"], 0)
        self.assertEqual(b2b["coverage"]["status"], "no_records")
        self.assertIsNone(b2b["availableDates"]["lastDate"])

    def test_nonempty_b2b_and_native_spu_keep_source_granularity(self):
        template = NetshopRow.objects.get(source_row_key="analysis-1")
        for index, (kind, source, dataset) in enumerate([("b2b", "jd_b2b", "b2b"), ("spu", "jd_sku_daily", "spu_daily")], 10):
            template.pk = None
            template.source_row_key = f"analysis-{index}"
            template.source_row_hash = f"{index:064x}"
            template.source, template.dataset = source, dataset
            template.sku_id = ""
            template.spu_id = "native-spu"
            template.transaction_amount_cents = 1000
            template.metrics_json = {"transactionAmountCents": 1000}
            template.raw_json = {}
            template.save()
            page = read_page(*self.query(dataset=kind))
            self.assertEqual(page["sourceDataset"], dataset)
            self.assertEqual(page["items"][0]["spuId"], "native-spu")
            self.assertIsNone(page["items"][0]["skuId"])
            self.assertEqual(page["items"][0]["metrics"]["paymentCents"], 1000)
            self.assertIsNone(page["items"][0]["metrics"]["paymentQuantity"])

    def test_master_is_latest_dated_snapshot_and_not_a_historical_mapping(self):
        for index, snapshot in enumerate(["2026-09-01", "2026-09-15", None], 20):
            batch = NetshopImportBatch.objects.create(id=f"master-{index}", source="jd_product_master", dataset="product_master",
                platform="京东", shop_name="样例店A", file_name="synthetic.xlsx", file_size_bytes=1,
                file_hash=f"{index:064x}", raw_file_hash=f"{index:064x}", content_hash=f"{index:064x}", scope_key=f"{index:064x}",
                status="completed", snapshot_date=snapshot, created_at="2026-09-16", completed_at="2026-09-16")
            row = NetshopRow.objects.get(source_row_key="analysis-1")
            row.pk = None
            row.source_row_key, row.source_row_hash = f"master-{index}", f"{index:064x}"
            row.source, row.dataset, row.last_import_batch_id = batch.source, batch.dataset, batch.id
            row.snapshot_date = snapshot
            row.raw_json = {"商家编码": "M1"}
            row.save()
        result = read_page(*self.query(dataset="master", window="yearAgo"))
        self.assertEqual(result["control"]["rowCount"], 1)
        self.assertEqual(result["coverage"]["snapshotDate"], "2026-09-15")
        self.assertFalse(result["coverage"]["historicalMapping"])
        self.assertEqual(result["items"][0]["dimensions"]["merchantCode"], "M1")

    def test_cursor_binds_filters_limit_and_revision(self):
        cursor = read_page(*self.query())["pagination"]["nextCursor"]
        for overrides in ({"shop": "样例店B"}, {"window": "previous"}, {"limit": "2"}, {"cursor": cursor+"tamper"}):
            with self.subTest(overrides=overrides), self.assertRaises(NetshopApiError):
                read_page(*self.query(cursor=cursor, **{k: v for k, v in overrides.items() if k != "cursor"})) if "cursor" not in overrides else read_page(*self.query(**overrides))
        NetshopDataRevision.objects.filter(domain="netshop").update(revision=8)
        with self.assertRaises(NetshopApiError):
            read_page(*self.query(cursor=cursor))

    def test_change_during_page_fails_without_returning_mixed_results(self):
        with patch("netshop.analysis.revision_value", side_effect=["7:old", "8:new"]), self.assertRaises(NetshopApiError):
            read_page(*self.query())

    def test_query_rejects_duplicate_unknown_and_invalid_values(self):
        for query in (urlencode(self.params)+"&shop=other", urlencode(self.params)+"&sql=SELECT", urlencode({**self.params, "limit": "101"}), urlencode({**self.params, "dataset": "secret"})):
            with self.subTest(query=query), self.assertRaises(NetshopApiError):
                validate_request(QueryDict(query))

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_signed_api_roles_scope_and_post_rejection(self):
        url = "/api/netshop/analysis-records?" + urlencode(self.params)
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Cache-Control"], "no-store")
        for role in ["viewer", "analyst", "operator"]:
            self.assertEqual(self.client.get(url, headers=signed_headers(url, role=role)).status_code, 403)
        scoped = {"platforms": ["京东"], "warehouses": [], "channels": []}
        self.assertEqual(self.client.get(url, headers=signed_headers(url, scope=scoped)).status_code, 403)
        self.assertIn(self.client.get(url).status_code, [401, 403])
        self.assertEqual(self.client.post(url).status_code, 405)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_route_exists_only_in_reader_and_rejects_writer(self):
        import netshop.urls
        url = "/api/netshop/analysis-records?" + urlencode(self.params)
        try:
            for role, status in [("netshop_reader", 200), ("netshop_writer", 404)]:
                with self.subTest(role=role), override_settings(DJANGO_PROCESS_ROLE=role):
                    module = ModuleType("isolated_analysis_urls_" + role)
                    module.urlpatterns = [path("api/netshop/", include(importlib.reload(netshop.urls).urlpatterns))]
                    with override_settings(ROOT_URLCONF=module):
                        self.assertEqual(self.client.get(url, headers=signed_headers(url)).status_code, status)
        finally:
            importlib.reload(netshop.urls)
