import importlib
import time
from types import ModuleType
from urllib.parse import urlencode
from unittest.mock import patch

from django.core import signing
from django.db import connection
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import include, path

from business_analysis.contracts import digest
from netshop import analysis_options as options
from netshop.errors import NetshopApiError
from netshop.models import NetshopDataRevision, NetshopImportBatch
from sales.auth import Principal
from sales.tests.factories import TEST_SECRET, signed_headers


class AnalysisOptionsTests(TestCase):
    def setUp(self):
        NetshopDataRevision.objects.update_or_create(domain="netshop", defaults={"revision": 6, "source_digest": "b"*64})
        self.principal = Principal("admin@example.test", "Test", "admin", None)
        self.sequence = 0
        self.fixture_revision_published = False

    def batch(self, shop="精确店", **overrides):
        self.sequence += 1
        values = dict(id=f"options-{self.sequence}", platform="京东", shop_name=shop,
            source="jd_promotion", dataset="ad", status="completed", row_count=2,
            file_name="不应泄露的文件.xlsx", file_size_bytes=100, file_hash=f"{self.sequence:064x}",
            raw_file_hash="b"*64, content_hash="c"*64, scope_key="d"*64,
            date_min="2026-08-01", date_max="2026-08-03", snapshot_date=None,
            created_at="2026-09-17", completed_at="2026-09-17", actor_email="private@example.test",
            warnings_json=["原始客户信息"], totals_json={"secret": 777})
        values.update(overrides)
        batch = NetshopImportBatch.objects.create(**values)
        if not self.fixture_revision_published:
            NetshopDataRevision.objects.filter(domain="netshop").update(
                revision=7, source_digest="a"*64)
            self.fixture_revision_published = True
        return batch

    def read(self, **params):
        query, cursor = options.validate_request(QueryDict(urlencode(params)))
        return options.read_page(self.principal, query, cursor)

    def test_completed_exact_source_metadata_only_and_no_fact_query(self):
        self.batch()
        self.batch(date_min="2026-07-01", date_max="2026-07-04")
        self.batch("精确店", platform="天猫", source="tmall_promotion", dataset="promotion_daily")
        self.batch("失败店", status="failed")
        self.batch("待发布店", status="processing")
        self.batch("错误组合", source="jd_b2b", dataset="ad")
        self.batch("无关来源", source="inv_selfop", dataset="inventory")
        with CaptureQueriesContext(connection) as captured:
            result = self.read()
        self.assertEqual(len(result["items"]), 2)
        text = str(result)
        for secret in ("不应泄露", "private@example", "原始客户", "777", "失败店", "待发布店", "错误组合", "无关来源"):
            self.assertNotIn(secret, text)
        sql = " ".join(item["sql"] for item in captured.captured_queries).lower()
        self.assertNotIn('"netshop_rows"', sql)
        self.assertNotIn("totals_json", sql)
        self.assertIn("limit 21", sql)
        self.assertIn("group by", sql)
        jd = next(item for item in result["items"] if item["identity"]["platform"] == "京东")
        self.assertEqual(jd["dateMetadata"]["firstDate"], "2026-07-01")
        self.assertEqual(jd["dateMetadata"]["lastDate"], "2026-08-03")
        self.assertFalse(jd["dateMetadata"]["coverageVerified"])
        self.assertEqual(result["pageDigest"], digest({k: v for k, v in result.items() if k != "pageDigest"}))

    def test_platform_shop_dataset_and_search_are_exact_no_alias(self):
        self.batch("精确店")
        self.batch("精确店（直营）")
        self.batch("精确店", source="jd_sku_daily", dataset="spu_daily")
        self.batch("精确店", platform="天猫", source="tmall_promotion", dataset="promotion_daily")
        self.assertEqual(len(self.read(platform="京东", shop="精确店")["items"]), 2)
        self.assertEqual(len(self.read(platform="京东", shop="精确店", dataset="promotion")["items"]), 1)
        self.assertEqual(len(self.read(q="直营")["items"]), 1)
        self.assertEqual(self.read(q="%_")["items"], [])

    def test_keyset_complete_20_plus_3_without_duplicates(self):
        shops = [f"店{i:02}" for i in range(21)] + ["𐀀店", "\ue000店"]
        for shop in reversed(shops):
            self.batch(shop)
        first = self.read(limit="20")
        self.assertEqual(len(first["items"]), 20)
        second = self.read(cursor=first["pagination"]["nextCursor"])
        self.assertEqual(len(second["items"]), 3)
        self.assertFalse(second["pagination"]["hasMore"])
        self.assertIsNone(second["pagination"]["nextCursor"])
        names = [item["identity"]["shop"] for item in first["items"]+second["items"]]
        self.assertEqual(names, sorted(shops))

    def test_cursor_rejects_changed_query_principal_revision_signature_and_expiry(self):
        for i in range(21):
            self.batch(f"店{i:02}")
        token = self.read()["pagination"]["nextCursor"]
        for query in ({"q": "店"}, {"platform": "京东"}, {"platform": "京东", "shop": "店00"}):
            with self.subTest(query=query), self.assertRaises(NetshopApiError):
                options.read_page(self.principal, query, token)
        with self.assertRaises(NetshopApiError):
            options.read_page(Principal("other@example.test", "Other", "admin", None), {}, token)
        with self.assertRaises(NetshopApiError):
            self.read(cursor=token+"x")
        with patch("django.core.signing.time.time", return_value=time.time()+3602), self.assertRaises(NetshopApiError):
            self.read(cursor=token)
        NetshopDataRevision.objects.filter(domain="netshop").update(revision=8)
        with self.assertRaises(NetshopApiError):
            self.read(cursor=token)

    def test_revision_fence_rejects_mid_read_and_missing_revision(self):
        self.batch()
        with patch.object(options, "revision_value", side_effect=["1:a", "2:b"]), self.assertRaises(NetshopApiError):
            self.read()
        # The published global revision is now physically non-deletable.
        # Simulate an unavailable revision at the read boundary instead.
        with patch.object(options, "revision_value",
                side_effect=NetshopApiError("网店数据版本不可用",
                    code="service_unavailable", status=503)), \
                self.assertRaises(NetshopApiError):
            self.read()

    def test_snapshot_and_unknown_dates_do_not_claim_historical_coverage(self):
        self.batch(source="jd_product_master", dataset="product_master", snapshot_date="2026-09-16")
        self.batch("未知日期店", date_min=None, date_max=None)
        result = self.read()
        master = next(item for item in result["items"] if item["identity"]["dataset"] == "master")
        self.assertEqual(master["dateMetadata"], {"kind": "published_import_envelope", "firstDate": None,
            "lastDate": None, "snapshotDate": "2026-09-16", "coverageVerified": False})
        missing = next(item for item in result["items"] if item["identity"]["shop"] == "未知日期店")
        self.assertIsNone(missing["dateMetadata"]["firstDate"])

    def test_invalid_metadata_refused_and_empty_valid_catalog_distinct(self):
        self.assertEqual(self.read()["items"], [])
        row = self.batch(date_min="2026-99-01")
        with self.assertRaises(NetshopApiError):
            self.read()
        row.date_min, row.date_max = "2026-09-01", "2026-08-01"
        row.save(update_fields=["date_min", "date_max"])
        with self.assertRaises(NetshopApiError):
            self.read()

    def test_control_characters_and_bad_published_identity_fail_closed(self):
        for char in ("\x00", "\x1f", "\x7f", "\x85", "\ud800"):
            with self.subTest(char=repr(char)), self.assertRaises(NetshopApiError):
                options.read_page(self.principal, {"q": "店"+char})
        row = self.batch("店\x7f")
        with self.assertRaises(NetshopApiError) as caught:
            self.read()
        self.assertEqual((caught.exception.code, caught.exception.status), ("invalid_source_metadata", 409))
        row.shop_name = " 店 "
        row.save(update_fields=["shop_name"])
        with self.assertRaises(NetshopApiError) as caught:
            self.read()
        self.assertEqual(caught.exception.status, 409)

    def test_utf8_capacity_rejects_entire_page_never_clips_identity(self):
        self.batch("中"*100)
        baseline = self.read()
        import json
        encoded = json.dumps(baseline, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertGreater(len(encoded.encode()), len(encoded))
        with patch.object(options, "MAX_BYTES", len(encoded)), self.assertRaises(NetshopApiError) as caught:
            self.read()
        self.assertEqual(caught.exception.status, 413)
        self.assertEqual(baseline["items"][0]["identity"]["shop"], "中"*100)

    def test_unknown_duplicate_invalid_and_internal_parameter_shapes(self):
        for raw in ("platform=京东&platform=天猫", "shop=店", "sql=SELECT", "limit=1", "limit=020",
                    "dataset=nope", "platform=天猫&dataset=sku", "q=", "q=%20店", "cursor=", "offset=0"):
            with self.subTest(raw=raw), self.assertRaises(NetshopApiError):
                options.validate_request(QueryDict(raw))
        with self.assertRaises(NetshopApiError):
            options.read_page(self.principal, {"shop": ["bad"]})

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_signed_get_permission_no_store_and_strict_method(self):
        self.batch()
        url = "/api/netshop/analysis-options?platform="+"%E4%BA%AC%E4%B8%9C"
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["X-Netshop-Data-Revision"], response.json()["revision"])
        self.assertIn(self.client.get(url).status_code, (401, 403))
        for role in ("viewer", "analyst", "operator"):
            self.assertEqual(self.client.get(url, headers=signed_headers(url, role=role)).status_code, 403)
        scoped = {"platforms": ["京东"], "warehouses": [], "channels": []}
        self.assertEqual(self.client.get(url, headers=signed_headers(url, scope=scoped)).status_code, 403)
        self.assertEqual(self.client.post(url).status_code, 405)
        duplicate = url+"&platform=%E5%A4%A9%E7%8C%AB"
        self.assertEqual(self.client.get(duplicate, headers=signed_headers(duplicate)).status_code, 400)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_production_reader_only_route(self):
        import netshop.urls
        url = "/api/netshop/analysis-options"
        try:
            for role, status in (("netshop_reader", 200), ("netshop_writer", 404)):
                with self.subTest(role=role), override_settings(DJANGO_PROCESS_ROLE=role):
                    module = ModuleType("isolated_analysis_options_"+role)
                    module.urlpatterns = [path("api/netshop/", include(importlib.reload(netshop.urls).urlpatterns))]
                    with override_settings(ROOT_URLCONF=module):
                        self.assertEqual(self.client.get(url, headers=signed_headers(url)).status_code, status)
        finally:
            importlib.reload(netshop.urls)
