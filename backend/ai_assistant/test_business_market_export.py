"""Actual PostgreSQL owning reader -> fixed report -> complete market materials."""
from copy import deepcopy
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

from django import test as djtest
from django.db import connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from access_control.models import AppUser
from market import analysis as market_analysis, analysis_continuation
from market.models import MarketDataRevision, MarketRankingEntry
from business_analysis.market_report_tables import tables
from . import business_market_export as export
from . import test_business_integrated_guard as fixtures
from . import test_business_promotion_views as collection_fixtures
from .policy import AiError


BANDS = [{"key": "low", "lowerCents": 0, "upperExclusiveCents": 200},
    {"key": "high", "lowerCents": 200, "upperExclusiveCents": None}]


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessMarketExportTests(djtest.TransactionTestCase):
    user = fixtures.BusinessIntegratedGuardTests.user
    call = fixtures.BusinessIntegratedGuardTests.call
    bundle = fixtures.BusinessIntegratedGuardTests.bundle
    input_for = fixtures.BusinessIntegratedGuardTests.input_for
    insert = fixtures.BusinessIntegratedGuardTests.insert
    seed = fixtures.BusinessIntegratedGuardTests.seed
    collect_body = collection_fixtures.BusinessPromotionViewTests.collect_body

    def setUp(self):
        fixtures.BusinessIntegratedGuardTests.setUp(self)
        MarketDataRevision.objects.update_or_create(domain="market", defaults={"revision": 7, "source_digest": "a"*64})
        for index, (day, sku) in enumerate([
                ("2026-08-01", "A"), ("2026-08-01", "B"),
                ("2026-07-31", "B"), ("2026-07-31", "C")]):
            MarketRankingEntry.objects.create(natural_key=str(uuid4()), source_row_number=index+1,
                period_start=day, period_end=day, category="合成市场", scope="POP", ranking_dimension="SKU",
                price_band_filter="全部", sku_code=sku, product_name="Synthetic", rank=index+1,
                price_low_cents=100, price_high_cents=100, price_estimated=False,
                gmv_low_cents=10, gmv_high_cents=20, quantity_low=1, quantity_high=2,
                last_import_batch_id="synthetic-market")
        old = self.source_execute
        def execute(name, args, actor, **kwargs):
            if name == "get_business_market_continuation_page":
                data = analysis_continuation.read_page(actor, QueryDict(urlencode(args)))
            elif args.get("domain") == "market":
                data = market_analysis.read_page(actor, {"operation": "analysis_records",
                    **{key:value for key,value in args.items() if key != "domain"}})
            else: return old(name, args, actor, **kwargs)
            return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": data}
        self.source_execute = execute
        self.source_tools.append({**self.source_tools[0], "name": "get_business_market_continuation_page"})
        query = {"platform": "京东", "category": "合成市场", "scope": "POP", "rankingDimension": "SKU",
            "priceBandFilter": "全部", "startDate": "2026-08-01", "endDate": "2026-08-01", "window": "current"}
        body = deepcopy(self.evidence_body)
        body.update(clientRequestId="market-export-fixed", sources=deepcopy(self.sources))
        body["sources"].extend([{"key": "market", "domain": "market", "query": query},
            {"key": "market-prior", "domain": "market", "query": {**query, "window": "previous"}}])
        self.parent = self.collect_body(body)
        self.report, _ = self.seed()

    def test_complete_real_sealed_materials_typed_tables_and_no_live_market_query(self):
        with patch("ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            result = export.prepare(self.report.id, "market", "market-prior", self.admin, bands=BANDS)
        remote.assert_not_called()
        self.assertFalse(any("market_ranking_entries" in q["sql"].lower() for q in queries))
        manifest = result.manifest
        self.assertEqual([spec["rowCount"] for spec in manifest["tables"]], [1, 2, 3])
        with tables(manifest, {view: result.ndjson_pages(view) for view in export.VIEWS}) as (summary, sheet_tables):
            self.assertFalse(summary["authorityVerified"])
            rows = [list(table.rows) for table in sheet_tables]
            self.assertEqual([len(part) for part in rows], [1, 2, 3])
            self.assertEqual(rows[0][0][4], 2)
            by_sku = {row[3]:row for row in rows[2]}
            self.assertEqual(by_sku["A"][5], "entered_observed_top_sample")
            self.assertIsNone(by_sku["A"][15])

    def test_final_permission_revocation_blocks_complete_materials(self):
        original = export.owning.report_binding._revalidate
        calls = 0
        def revoke(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 5:
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return original(*args, **kwargs)
        with patch.object(export.owning.report_binding, "_revalidate", side_effect=revoke), self.assertRaises(AiError):
            export.prepare(self.report.id, "market", "market-prior", self.admin, bands=BANDS)
        self.assertEqual(calls, 5)
