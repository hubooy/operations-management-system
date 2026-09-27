from unittest import skipUnless
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from market.errors import MarketApiError
from market.models import (MarketRankingEntry, MarketPriceSnapshot, MarketPriceBandVersion,
                           MarketPriceBandItem, MarketNetshopProjection, MarketNetshopProjectionControl)
from market.query import overview, filter_options
from .test_query import PRINCIPAL


def sales(_principal, request):
    return {"rows": [{"productCode": code, "owned": code == "own", "ownSalesCents": 123 if code == "own" else 0}
                     for code in request["productCodes"]]}, "11:7"


class RankingPaginationTests(TestCase):
    def test_optional_global_filters_do_not_block_ranking_and_keep_default_contract(self):
        self.entry()
        expected = self.query()
        with patch("market.query.filter_options", side_effect=AssertionError("duplicate global scan")):
            result = overview(PRINCIPAL, {"operation": "overview", "view": "ranking", "page": 1,
                "pageSize": 10, "filters": None, "includeFilterOptions": False}, sales_loader=sales)
        self.assertEqual(result["items"], expected["items"])
        self.assertEqual(result["summary"], expected["summary"])
        self.assertEqual(result["filters"]["categories"], [])
        self.assertTrue(expected["filters"]["categories"])
        with self.assertRaises(MarketApiError):
            overview(PRINCIPAL, {"operation": "overview", "view": "ranking", "page": 1,
                "pageSize": 10, "filters": None, "includeFilterOptions": "false"}, sales_loader=sales)

    def entry(self, code="sku", **values):
        values = {"natural_key": str(MarketRankingEntry.objects.count()), "source_row_number": 1,
                  "period_start": "2026-08-01", "period_end": "2026-08-31", "category": "净水",
                  "scope": "全部", "sku_code": code, "ranking_dimension": "SKU", "rank": 1,
                  "gmv_cents": 100, "quantity": 2, "last_import_batch_id": "fixture", **values}
        return MarketRankingEntry.objects.create(**values)

    def query(self, view="ranking", page=1, filters=None):
        return overview(PRINCIPAL, {"operation": "overview", "view": view, "page": page,
                                   "pageSize": 10, "filters": filters}, sales_loader=sales)

    def test_page_matches_report_dedup_identity_price_rank_and_projection(self):
        self.entry("own", period_start="2026-07-01", period_end="2026-07-31", rank=5)
        self.entry("own", price_band_filter="200以上", gmv_cents=99999, rank=9)
        own = self.entry("own", gmv_cents=2)
        self.entry("own", period_start="2026-08-15", rank=4, gmv_cents=999)
        self.entry("own", category="跨类目", rank=None)
        self.entry("own", scope="其他榜单", rank=7)
        self.entry("own", ranking_dimension="SPU", rank=8)
        for i in range(22):
            self.entry(f"sku-{i:02}", rank=i % 3, gmv_cents=i, brand="" if i % 2 else "测试品牌")
        MarketPriceSnapshot.objects.create(id="price", category=own.category, scope=own.scope,
            sku_code=own.sku_code, month="2026-08", confirmation_status="confirmed", ai_price_type="到手价",
            image_content_sha256="a" * 64, confirmed_market_price_cents=10000, ai_image_price_cents=12000)
        MarketPriceBandVersion.objects.create(id="bands", category="净水", version=1, status="published")
        MarketPriceBandItem.objects.create(id="low", version_id="bands", label="低价", min_cents=0, max_cents=20000)
        MarketNetshopProjectionControl.objects.update_or_create(id=1, defaults={"active_revision": "r"})
        MarketNetshopProjection.objects.create(projection_revision="r", projection_key="m", kind="metric",
            dataset="sku_daily", source="jd_sku_daily", sku_id="own", business_date="2026-08-01", transaction_amount_cents=800)
        for filters in (None, {"priceBands": ["低价"]}, {"priceBands": ["未确认价格"]},
                        {"rankingDimensions": ["SPU"]}, {"query": "own", "categories": ["净水"]},
                        {"startDate": "2026-07-15", "endDate": "2026-08-01"}):
            with self.subTest(filters=filters):
                report = self.query("full", filters=filters)
                pages = [self.query(page=p, filters=filters) for p in range(1, report["pagination"]["pageCount"] + 1)]
                expected = [item for p in range(1, report["pagination"]["pageCount"] + 1)
                            for item in self.query("full", page=p, filters=filters)["items"]]
                self.assertEqual([item for result in pages for item in result["items"]], expected)
                for name in ("productCount", "categoryCount", "brandCount", "activeSkuCount", "pendingAiCount"):
                    self.assertEqual(pages[0]["summary"][name], report["summary"][name], name)
                self.assertEqual(pages[0]["priceBands"], report["priceBands"])
        self.assertEqual(self.query(page=100)["items"], [])

    def test_price_boundaries_invalid_confirmation_and_category_precedence(self):
        for category, label, minimum, maximum in (("*", "通用", 0, None), ("净水", "专属", 10000, 20000)):
            MarketPriceBandVersion.objects.create(id=category, category=category, version=1, status="published")
            MarketPriceBandItem.objects.create(id=category, version_id=category, label=label, min_cents=minimum, max_cents=maximum)
        for i, (amount, price_type, digest, status) in enumerate(((10000, "标准售价", "a"*64, "confirmed"),
                (20000, "券后价", "a"*64, "confirmed"), (5000, "到手价", "a"*64, "confirmed"),
                (15000, "定金", "a"*64, "confirmed"), (15000, "标准售价", "bad", "confirmed"),
                (15000, "标准售价", "a"*64, "pending"), (15000, "标准售价", "a"*63+"\n", "confirmed"))):
            row = self.entry(str(i))
            MarketPriceSnapshot.objects.create(id=str(i), category=row.category, scope=row.scope, sku_code=row.sku_code,
                month="2026-08", confirmation_status=status, ai_price_type=price_type,
                image_content_sha256=digest, confirmed_market_price_cents=amount)
        for band, codes in (("专属", ["0"]), ("通用", ["1", "2"]), ("未确认价格", ["3", "4", "5", "6"])):
            self.assertEqual([item["skuCode"] for item in self.query(filters={"priceBands": [band]})["items"]], codes)
        self.assertEqual(self.query(filters={"query": "%' OR 1=1 --"})["pagination"]["total"], 0)
        self.assertEqual(self.query(filters={"categories": ["不存在"]})["summary"]["productCount"], 0)
        with self.assertRaises(MarketApiError):
            self.query(page=0)
        self.assertIn("净水", [item["value"] for item in filter_options()["categories"]])

    @skipUnless(connection.vendor == "postgresql", "Real scale verification requires PostgreSQL")
    def test_more_than_250000_rows_uses_bounded_page_and_keeps_report_guard(self):
        self.entry("seed")
        fields = [field.column for field in MarketRankingEntry._meta.concrete_fields if field.column != "id"]
        quote = connection.ops.quote_name
        values = ["'scale-' || g::text" if field in ("natural_key", "sku_code") else "g" if field == "rank" else "s." + quote(field) for field in fields]
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO market_ranking_entries (" + ",".join(map(quote, fields)) + ") SELECT " +
                           ",".join(values) + " FROM market_ranking_entries s CROSS JOIN generate_series(1,250001) g WHERE s.sku_code='seed'")
        with patch("market.query._preferred_rows", side_effect=AssertionError("Unbounded ranking load")), patch(
                "market.query._sales_metrics", wraps=__import__("market.query", fromlist=["_sales_metrics"])._sales_metrics) as metrics:
            result = self.query()
        self.assertEqual(result["pagination"]["total"], 250002)
        self.assertEqual(result["summary"]["activeSkuCount"], 250002)
        self.assertEqual(len(result["items"]), 10)
        self.assertEqual(len(metrics.call_args.args[1]), 10)
        self.assertEqual(len(self.query(page=2)["items"]), 10)
        pending = self.query(filters={"priceBands": ["未确认价格"]})
        self.assertEqual(pending["pagination"]["total"], 250002)
        self.assertEqual([row["id"] for row in pending["items"]], [row["id"] for row in result["items"]])
        with self.assertRaises(MarketApiError) as raised:
            self.query("full")
        self.assertEqual(raised.exception.status, 413)
        self.assertEqual(filter_options()["categories"], [{"value": "净水", "count": 250002}])

    def test_confirmed_price_without_matching_band_keeps_price_and_pending_count(self):
        row = self.entry("outside-bands")
        MarketPriceSnapshot.objects.create(id="outside", category=row.category, scope=row.scope,
            sku_code=row.sku_code, month="2026-08", confirmation_status="confirmed", ai_price_type="到手价",
            image_content_sha256="b" * 64, confirmed_market_price_cents=30000)
        self.entry("no-price")
        filters = {"priceBands": ["未确认价格"]}
        ranking, report = self.query(filters=filters), self.query("full", filters=filters)
        self.assertEqual(ranking["items"], report["items"])
        self.assertEqual(ranking["pagination"]["total"], 2)
        self.assertEqual(ranking["summary"]["pendingAiCount"], 1)

    @skipUnless(connection.vendor == "postgresql", "Query planning needs PostgreSQL statistics")
    def test_correlated_price_states_keep_full_scale_read_within_statement_budget(self):
        # Real imports correlate confirmed state, price type, positive amount
        # and hash validity. Independent selectivity estimates can collapse
        # this substantial matching set to one row and create a quadratic join.
        seed = self.entry("planner-seed")
        MarketPriceSnapshot.objects.create(id="planner-seed", category=seed.category, scope=seed.scope,
            sku_code=seed.sku_code, month="2026-08", confirmation_status="confirmed", ai_price_type="到手价",
            image_content_sha256="a"*64, confirmed_market_price_cents=10000)
        quote = connection.ops.quote_name
        with connection.cursor() as cursor:
            for model, overrides in (
                (MarketRankingEntry, {"natural_key":"'planner-'||g::text", "sku_code":"'planner-'||g::text", "rank":"g"}),
                (MarketPriceSnapshot, {"id":"'planner-'||g::text", "sku_code":"'planner-'||g::text",
                  "confirmation_status":"CASE WHEN g%4=0 THEN 'confirmed' ELSE 'source_table' END",
                  "ai_price_type":"CASE WHEN g%4=0 THEN '到手价' ELSE '' END",
                  "confirmed_market_price_cents":"CASE WHEN g%4=0 THEN 10000 ELSE NULL END",
                  "image_content_sha256":"CASE WHEN g%20=0 THEN 'invalid' ELSE repeat('a',64) END"}),
            ):
                fields = [f.column for f in model._meta.concrete_fields if not (model is MarketRankingEntry and f.column=="id")]
                values = [overrides.get(f, "s."+quote(f)) for f in fields]
                cursor.execute("INSERT INTO "+quote(model._meta.db_table)+" ("+",".join(map(quote,fields))+") SELECT "+
                    ",".join(values)+" FROM "+quote(model._meta.db_table)+" s CROSS JOIN generate_series(1,100000) g WHERE s.sku_code='planner-seed'")
            cursor.execute("ANALYZE market_ranking_entries")
            cursor.execute("ANALYZE market_price_snapshots")
            cursor.execute("SET LOCAL statement_timeout='6s'")
        result = self.query()
        self.assertEqual(result["pagination"]["total"], 100001)
        self.assertEqual(result["summary"]["pendingAiCount"], 80000)
        self.assertEqual(len(result["items"]), 10)

    def test_rank_boundary_keeps_all_ties_and_projects_history_outside_candidates(self):
        MarketNetshopProjectionControl.objects.update_or_create(id=1, defaults={"active_revision": "r"})
        for i in range(35):
            code = f"tie-{i}"
            self.entry(code, rank=1 if i<25 else None, gmv_cents=100+i)
            self.entry(code, rank=50, period_start="2026-07-01", period_end="2026-07-31")
            # Another period with the same end date: projection must also order
            # historical ties, even when their rank is outside page candidates.
            self.entry(code, rank=50, period_start="2026-07-15", period_end="2026-07-31", gmv_cents=999)
            for month in ("07", "08"):
                MarketNetshopProjection.objects.create(projection_revision="r", projection_key=f"{i}-{month}",
                    kind="metric", dataset="sku_daily", source="jd_sku_daily", sku_id=code,
                    business_date=f"2026-{month}-01", transaction_amount_cents=10000 if i==0 else 10*i)
        for page in (1,2,3,8,11,99):
            with self.subTest(page=page):
                ranking, report = self.query(page=page), self.query("full",page=page)
                self.assertEqual(ranking["items"], report["items"])
        self.assertEqual(self.query()["items"][0]["skuCode"], "tie-0")

    def test_dedup_source_preference_and_filtered_rows_match_report(self):
        for code, sources in (("all", ["", "z", "a", "全部"]), ("empty", ["", "a"]), ("other", ["z", "a"])):
            for band in sources:
                self.entry(code, price_band_filter=band, brand="before" if band=="全部" else "after")
        for filters in (None, {"brands": ["after"]}, {"query": "other"}, {"priceBands": ["未确认价格"]}):
            self.assertEqual(self.query(filters=filters)["items"], self.query("full",filters=filters)["items"])

    def test_global_facets_match_independent_counts_including_empty_values(self):
        from market.query import _database_options
        for i in range(12):
            self.entry(str(i), category=f"类目{i%3}", brand="" if i%4==0 else f"品牌{i%2}",
                       operation_mode="" if i%2 else "自营", subcategory=f"细分{i%4}")
        result = filter_options()
        for name, field in (("categories","category"), ("scopes","scope"), ("brands","brand"),
                            ("rankingDimensions","ranking_dimension"), ("operationModes","operation_mode"),
                            ("subcategories","subcategory")):
            self.assertEqual(result[name], _database_options(MarketRankingEntry.objects.all(),field))

    def test_paging_keeps_valid_long_identity_and_brand_inputs(self):
        # Covering the long brand plus the complete source key can exceed the
        # PostgreSQL B-tree tuple limit. The query must keep accepting fields
        # within the existing import limits without a new covering index.
        import random
        rng = random.Random(42)
        text = lambda length: ''.join(chr(rng.randrange(0x4e00,0x9fff)) for _ in range(length))
        row = self.entry(text(200),category=text(200),scope=text(200),price_band_filter=text(200),brand=text(300))
        result = self.query()
        self.assertEqual(result['items'][0]['id'], row.id)
        self.assertEqual(result['items'][0]['brand'], row.brand)
