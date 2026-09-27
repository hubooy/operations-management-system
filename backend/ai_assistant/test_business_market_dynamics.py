"""Actual market SQL -> sealed evidence -> internal report-bound dynamics."""
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
from sales.auth import Principal
from . import business_market_dynamics as service
from . import test_business_integrated_guard as fixtures
from . import test_business_promotion_views as collection_fixtures
from .policy import AiError, digest

BANDS = [{"key": "low", "lowerCents": 0, "upperExclusiveCents": 200},
         {"key": "high", "lowerCents": 200, "upperExclusiveCents": None}]


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessMarketDynamicsTests(djtest.TransactionTestCase):
    user = fixtures.BusinessIntegratedGuardTests.user
    call = fixtures.BusinessIntegratedGuardTests.call
    bundle = fixtures.BusinessIntegratedGuardTests.bundle
    input_for = fixtures.BusinessIntegratedGuardTests.input_for
    insert = fixtures.BusinessIntegratedGuardTests.insert
    seed = fixtures.BusinessIntegratedGuardTests.seed
    collect_body = collection_fixtures.BusinessPromotionViewTests.collect_body

    def market_row(self, number, day="2026-08-01", **overrides):
        return MarketRankingEntry.objects.create(**{"natural_key": str(uuid4()), "source_row_number": number+1,
            "period_start": day, "period_end": day, "category": "合成市场", "scope": "POP", "ranking_dimension": "SKU",
            "price_band_filter": "全部", "sku_code": "market-"+str(number), "product_name": "Synthetic",
            "rank": number+1, "price_low_cents": 100, "price_high_cents": 100, "price_estimated": False,
            "gmv_low_cents": 10, "gmv_high_cents": 20, "quantity_low": 1, "quantity_high": 2,
            "last_import_batch_id": "synthetic-market", **overrides})

    def setUp(self):
        fixtures.BusinessIntegratedGuardTests.setUp(self)
        MarketDataRevision.objects.update_or_create(domain="market", defaults={"revision": 7, "source_digest": "a"*64})
        for number in range(21): self.market_row(number)
        self.market_row(0, "2026-07-31", rank=8)
        self.market_row(90, "2026-07-31")
        self.market_row(91, price_low_cents=None, price_high_cents=None)
        self.market_row(92, "2026-08-31", period_start="2026-08-01")
        self.market_row(93, scope="SELF")
        old = self.source_execute
        def execute(name, args, actor, **kwargs):
            if name == "get_business_market_continuation_page":
                data = analysis_continuation.read_page(actor, QueryDict(urlencode(args)))
            elif args.get("domain") == "market":
                data = market_analysis.read_page(actor, {"operation": "analysis_records", **{k:v for k,v in args.items() if k != "domain"}})
            else: return old(name, args, actor, **kwargs)
            return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": data}
        self.source_execute = execute
        self.source_tools.append({**self.source_tools[0], "name": "get_business_market_continuation_page"})
        query = {"platform": "京东", "category": "合成市场", "scope": "POP", "rankingDimension": "SKU",
            "priceBandFilter": "全部", "startDate": "2026-08-01", "endDate": "2026-08-01", "window": "current"}
        body = deepcopy(self.evidence_body)
        body.update(clientRequestId="market-dynamics-fixed", sources=deepcopy(self.sources))
        body["sources"].extend([{ "key": "market", "domain": "market", "query": query},
            {"key": "market-prior", "domain": "market", "query": {**query, "window": "previous"}},
            {"key": "market-other", "domain": "market", "query": {**query, "scope": "SELF"}}])
        self.fixed_body = body
        self.parent = self.collect_body(body)
        self.report, _ = self.seed()

    def page(self, **kwargs):
        params = {"sourceKey": "market", "view": "price_band", "bands": deepcopy(BANDS), **kwargs}
        if params["view"] == "rank_entry_exit": params.pop("bands")
        return service.page(self.report.id, params, self.admin)

    def test_real_daily_prices_and_rank_presence_with_unallocated_bucket(self):
        result = self.page()
        groups = {row["bandKey"]: row for row in result["table"]["rows"]}
        self.assertEqual(groups["low"]["metrics"]["sampleGmvLowerCents"]["value"], 210)
        self.assertEqual(len(groups["unallocated_missing"]["members"]), 1)
        self.assertEqual(result["table"]["sourceMetadata"][0]["excludedOverlappingPeriodRows"], 1)
        self.assertTrue(result["authority"]["completeSourceTraversalForSelectedSources"])
        self.assertFalse(result["authority"]["wholeMarketCoverageVerified"])
        self.assertFalse(result["authority"]["ownProductIdentityVerified"])
        first = self.page(view="rank_entry_exit", baselineKey="market-prior")
        offset = first["table"]["pagination"]["nextOffset"]
        self.assertEqual(offset, 20)
        last = self.page(view="rank_entry_exit", baselineKey="market-prior", offset=offset)
        self.assertIsNone(last["table"]["pagination"]["nextOffset"])
        rows = {r["skuId"]: r for r in first["table"]["rows"] + last["table"]["rows"]}
        self.assertEqual(rows["market-0"]["rankImprovement"], 7)
        self.assertIsNone(rows["market-90"]["current"]["metrics"])
        self.assertEqual(rows["market-90"]["current"]["status"], "not_observed_in_top_sample")

    def test_complete_multiple_saved_pages_without_live_market_queries_or_models(self):
        for index in range(100,205): self.market_row(index)
        body = deepcopy(self.fixed_body); body["clientRequestId"] = "market-dynamics-wide"
        self.parent = self.collect_body(body); self.report, _ = self.seed()
        original = service.report_binding.Reader.pages
        started, finished, count = [], [], []
        def tracking(reader, key):
            started.append(key)
            for page in original(reader, key): count.append(key); yield page
            finished.append(key)
        with (patch.object(service.report_binding.Reader, "pages", tracking), patch("ai_assistant.transport.execute_tool") as remote,
                patch("ai_assistant.provider.turn") as model, CaptureQueriesContext(connection) as queries):
            result = self.page(view="rank_entry_exit", baselineKey="market-prior")
        self.assertEqual(started, ["market", "market-prior"]); self.assertEqual(finished, started)
        self.assertGreater(count.count("market"), 1)
        self.assertEqual(result["table"]["rowCount"], 128)
        model.assert_not_called(); remote.assert_not_called()
        self.assertFalse(any("market_ranking_entries" in q["sql"].lower() for q in queries))
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))

    def test_exact_row_reference_and_changed_band_binding(self):
        result = self.page(); row = result["table"]["rows"][0]
        read = service.read_row(self.report.id, "market", "price_band", row["rowIndex"], row["rowId"], self.admin, bands=BANDS)
        self.assertEqual(read["row"], row)
        self.assertEqual(read["bindingDigest"], result["bindingDigest"])
        self.assertEqual(read["responseDigest"], digest({k:v for k,v in read.items() if k != "responseDigest"}))
        for index, row_id, bands in ((True, row["rowId"], BANDS), (0, "0"*64, BANDS),
            (0, row["rowId"], [{"key": "other", "lowerCents": 0, "upperExclusiveCents": None}])):
            with self.assertRaises(AiError): service.read_row(self.report.id, "market", "price_band", index, row_id, self.admin, bands=bands)

    def test_wrong_source_baseline_role_scope_and_types_are_denied(self):
        for params in ({"sourceKey": "ads"}, {"sourceKey": "sales"}, {"sourceKey": "missing"}, {"view": "sku"},
            {"view": "rank_entry_exit", "baselineKey": "market"}, {"view": "rank_entry_exit", "baselineKey": "market-other"},
            {"offset": True}, {"limit": 10}, {"extra": 1}):
            with self.subTest(params=params), self.assertRaises(AiError): self.page(**params)
        for actor in (self.viewer, self.user("market-other@example.invalid", "admin", None),
            Principal(self.admin.email, "Scoped", "admin", {"shops": ["other"]})):
            with self.assertRaises(AiError): service.page(self.report.id, {"sourceKey": "market", "view": "price_band", "bands": BANDS}, actor)

    def test_tampered_page_or_table_digest_and_late_stream_error_fail_closed(self):
        original = service.report_binding.Reader.pages
        def tampered(reader, key):
            for page in original(reader, key):
                page = deepcopy(page); page["items"][0]["metrics"]["sampleGmvLowerCents"] = 999
                yield page
        with patch.object(service.report_binding.Reader, "pages", tampered), self.assertRaises(AiError): self.page()
        calculate = service.market_dynamics.price_band
        def bad(*args, **kwargs):
            value = calculate(*args, **kwargs); value["tableDigest"] = "0"*64; return value
        with patch.object(service.market_dynamics, "price_band", side_effect=bad), self.assertRaises(AiError): self.page()
        def late(reader, key):
            yield from original(reader, key)
            raise AiError("synthetic stream tail failure")
        with patch.object(service.report_binding.Reader, "pages", late), self.assertRaisesRegex(AiError, "tail"): self.page()

    def test_final_revocation_never_returns_prepared_page(self):
        response = service._response
        def revoke(*args, **kwargs):
            value = response(*args, **kwargs)
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return value
        with patch.object(service, "_response", side_effect=revoke), self.assertRaises(AiError): self.page()

    def test_full_row_and_metadata_capacity_reject_not_truncate(self):
        result = self.page(); row = result["table"]["rows"][0]
        with patch.object(service, "MAX_RESPONSE_BYTES", 100), self.assertRaises(AiError) as caught: self.page()
        self.assertEqual(caught.exception.status, 413)
        with patch.object(service, "MAX_RESPONSE_BYTES", 100), self.assertRaises(AiError) as caught:
            service.read_row(self.report.id, "market", "price_band", row["rowIndex"], row["rowId"], self.admin, bands=BANDS)
        self.assertEqual(caught.exception.status, 413)
