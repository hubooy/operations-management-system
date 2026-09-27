import json
import uuid
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from sales.auth import Principal
from sales.tests.factories import TEST_SECRET, signed_headers
from market.analysis import read_page
from market.errors import MarketApiError
from market.models import MarketRankingEntry, MarketDataRevision, MarketWriteAuthority
from business_analysis.contracts import PageReconciler
from business_analysis.results import build_table


class MarketAnalysisTests(TestCase):
    def setUp(self):
        self.admin = Principal("admin@example.test", "Fixture", "admin", None)
        MarketDataRevision.objects.update_or_create(domain="market", defaults={"revision": 7, "source_digest": "a"*64})
        self.query = {"operation": "analysis_records", "platform": "京东", "category": "饮水机", "scope": "POP", "priceBandFilter": "全部",
            "rankingDimension": "SKU", "startDate": "2026-09-01", "endDate": "2026-09-01", "limit": 1}
        self.seed("a", "2026-09-01", 100, 200)
        self.seed("b", "2026-09-01", 300, 400)
        self.seed("a", "2026-08-31", 500, 800)
        self.seed("a", "2025-09-01", 50, 80)
        self.seed("monthly", "2026-09-30", 9999, 9999, period_start="2026-09-01")
        self.seed("other-band", "2026-09-01", 9999, 9999, price_band_filter="0-500")

    def seed(self, sku, day, low, high, **overrides):
        return MarketRankingEntry.objects.create(**{"natural_key": str(uuid.uuid4()), "source_row_number": 1,
            "period_start": day, "period_end": day, "category": "饮水机", "scope": "POP", "ranking_dimension": "SKU",
            "price_band_filter": "全部", "sku_code": sku, "product_name": "合成产品", "brand": "合成品牌", "rank": 1,
            "gmv_cents": 987654, "gmv_low_cents": low, "gmv_high_cents": high,
            "quantity_low": 1, "quantity_high": 3, "last_import_batch_id": "synthetic", **overrides})

    def pages(self, **overrides):
        result, proof, cursor = [], PageReconciler(), None
        while True:
            page = read_page(self.admin, {**self.query, **overrides, "cursor": cursor})
            proof.consume(page, request_cursor=cursor)
            result.append(page)
            cursor = page["pagination"]["nextCursor"]
            if cursor is None:
                break
        return result, proof.result()

    def test_complete_daily_sample_bounds_and_overlapping_exclusion(self):
        pages, proof = self.pages()
        self.assertEqual(proof["rowCount"], 2)
        self.assertEqual(proof["metrics"]["sampleGmvLowerCents"]["value"], 400)
        self.assertEqual(proof["metrics"]["sampleGmvUpperCents"]["value"], 600)
        self.assertIsNone(proof["metrics"]["productDayVisitorsLower"]["value"])
        self.assertEqual(pages[0]["excludedOverlappingPeriodRows"], 1)
        self.assertEqual(pages[0]["items"][0]["shopName"], "")
        self.assertNotIn("987654", json.dumps(pages))
        self.assertEqual(build_table(pages, "brand", proof)["rows"][0]["entity"]["brand"], "合成品牌")

    def test_interval_comparison_never_turns_midpoint_into_actual_growth(self):
        pages, proof = self.pages()
        baseline, before = self.pages(window="previous")
        table = build_table(pages, "category", proof, baseline_pages=baseline, baseline_expected=before)
        row = table["rows"][0]
        self.assertIsNone(row["comparisons"]["sampleGmvLowerCents"]["changeRate"])
        self.assertEqual(row["sampleComparisons"]["gmv"]["lowerChangeRate"], -.5)
        self.assertAlmostEqual(row["sampleComparisons"]["gmv"]["upperChangeRate"], .2)
        self.assertEqual(self.pages(window="yearAgo")[1]["rowCount"], 1)

    def test_identity_revision_range_and_cursor_fail_closed(self):
        page = read_page(self.admin, self.query)
        cursor = page["pagination"]["nextCursor"]
        for query in ({"scope": "其他"}, {"priceBandFilter": "0-500"}, {"rankingDimension": "SPU"}, {"limit": 2}, {"cursor": cursor+"bad"}):
            with self.assertRaises(MarketApiError):
                read_page(self.admin, {**self.query, "cursor": cursor, **query})
        MarketDataRevision.objects.filter(domain="market").update(revision=8)
        with self.assertRaises(MarketApiError):
            read_page(self.admin, {**self.query, "cursor": cursor})
        self.seed("bad", "2026-09-01", 50, 10)
        with self.assertRaises(MarketApiError):
            self.pages()

    def test_native_spu_and_empty_history_are_explicit(self):
        self.seed("native-spu", "2026-09-01", None, None, ranking_dimension="SPU")
        pages, proof = self.pages(rankingDimension="SPU")
        self.assertIsNone(pages[0]["items"][0]["skuId"])
        self.assertEqual(pages[0]["items"][0]["spuId"], "native-spu")
        self.assertIsNone(proof["metrics"]["sampleGmvLowerCents"]["value"])
        empty, _ = self.pages(category="无记录品类")
        self.assertEqual(empty[0]["coverage"]["status"], "no_records")
        with self.assertRaises(MarketApiError):
            read_page(Principal(self.admin.email, "Scoped", "admin", {"platforms": ["京东"]}), self.query)

    def test_bulk_page_byte_budget_keeps_cursor_and_control_complete(self):
        for i in range(105):
            self.seed(f"bulk-{i}", "2026-09-01", 1, 2, product_name="测"*400)
        pages, proof = self.pages(limit=100)
        self.assertLess(len(pages[0]["items"]), 100)
        self.assertGreater(len(pages), 2)
        self.assertEqual(proof["rowCount"], 107)
        self.assertEqual(proof["metrics"]["sampleGmvLowerCents"]["value"], 505)
        ids = [row["rowId"] for page in pages for row in page["items"]]
        self.assertEqual(len(set(ids)), 107)
        self.assertTrue(all(len(json.dumps(page, ensure_ascii=False).encode()) <= 131072 for page in pages))

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    @override_settings(MARKET_WRITE_AUTHORITY_EPOCH="11111111-1111-4111-8111-111111111111", MARKET_WRITE_CUTOVER_ID="analysis-test")
    def test_signed_owning_consumer_and_role_guards(self):
        MarketWriteAuthority.objects.filter(pk=1).update(status="postgres", authority_epoch=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            cutover_id="analysis-test", migration_verify_run_id="test", activated_at=timezone.now())
        path, raw = "/api/market/consumers/query", json.dumps(self.query, ensure_ascii=False).encode()
        response = self.client.post(path, data=raw, content_type="application/json", headers=signed_headers(path, method="POST", body=raw))
        self.assertEqual(response.status_code, 200, response.content)
        for role in ("viewer", "analyst", "operator"):
            denied = self.client.post(path, data=raw, content_type="application/json", headers=signed_headers(path, method="POST", body=raw, role=role))
            self.assertEqual(denied.status_code, 403)
