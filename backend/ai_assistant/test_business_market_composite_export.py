"""Real sealed interval market pages become three complete typed materials."""
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from access_control.models import AppUser
from business_analysis import market_report_tables_v2
from . import business_market_composite_export as service
from . import test_business_market_observation as fixtures
from .policy import AiError


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketCompositeExportTests(djtest.TransactionTestCase):
    user = fixtures.BusinessMarketObservationTests.user
    call = fixtures.BusinessMarketObservationTests.call
    bundle = fixtures.BusinessMarketObservationTests.bundle
    input_for = fixtures.BusinessMarketObservationTests.input_for
    insert = fixtures.BusinessMarketObservationTests.insert
    seed = fixtures.BusinessMarketObservationTests.seed
    collect_body = fixtures.BusinessMarketObservationTests.collect_body
    market_row = fixtures.BusinessMarketObservationTests.market_row
    setUp = fixtures.BusinessMarketObservationTests.setUp

    def prepared(self, current_day="2026-08-03", baseline_day="2026-07-31", **kwargs):
        from .test_business_market_dynamics import BANDS
        return service.prepare(self.report.id, "market", "market-prior",
            current_day, baseline_day, self.admin, bands=BANDS, **kwargs)

    def test_interval_price_and_two_day_rank_materials_are_distinct_complete_tables(self):
        with (patch("ai_assistant.transport.execute_tool") as remote,
                CaptureQueriesContext(connection) as queries):
            prepared = self.prepared()
        remote.assert_not_called()
        self.assertFalse(any("market_ranking_entries" in item["sql"].lower()
            for item in queries))
        manifest = prepared.manifest
        self.assertEqual(manifest["schemaVersion"], service.SCHEMA)
        self.assertEqual(manifest["reportBinding"]["reportId"], self.report.id)
        self.assertEqual(manifest["priceBandSourceKey"], manifest["rankCurrentSourceKey"])
        self.assertNotEqual(manifest["tables"][0]["sourceTableDigest"],
            manifest["tables"][2]["sourceTableDigest"])
        self.assertEqual(manifest["tables"][0]["sourceTableDigest"],
            manifest["tables"][1]["sourceTableDigest"])
        self.assertFalse(manifest["authority"]["priceSummaryAndMembersAdditive"])
        self.assertFalse(manifest["authority"]["marketAndOwnSalesAdditive"])
        with market_report_tables_v2.tables(manifest, {view: prepared.ndjson_pages(view)
                for view in service.VIEWS}) as (summary, tables):
            self.assertFalse(summary["authorityVerified"])
            rows = [list(table.rows) for table in tables]
            self.assertEqual([len(part) for part in rows],
                [spec["rowCount"] for spec in manifest["tables"]])
            by_sku = {row[3]: row for row in rows[2]}
            self.assertEqual(by_sku["market-77"][5], "entered_observed_top_sample")
            self.assertIsNone(by_sku["market-77"][18])
            self.assertEqual(len(by_sku["market-0"][17]), 64)
            self.assertEqual(len(by_sku["market-0"][18]), 64)

    def test_missing_baseline_observation_keeps_null_metrics_and_not_top_exit(self):
        prepared = self.prepared("2026-08-01", "2026-07-29")
        self.assertFalse(prepared.manifest["rankObservationCoverage"]["bothDatesPresent"])
        with market_report_tables_v2.tables(prepared.manifest,
                {view: prepared.ndjson_pages(view) for view in service.VIEWS}) as (_, tables):
            rank = list(tables[2].rows)
            self.assertTrue(rank)
            self.assertTrue(all(row[5] == "insufficient_date_coverage"
                and row[7] == "date_not_covered"
                and row[15] is None and row[16] is None and row[18] is None for row in rank))

    def test_wrong_report_source_day_quota_and_late_revocation_reject(self):
        for current, baseline, day, prior in (("sales", "market-prior", "2026-08-03", "2026-07-31"),
                ("market", "missing", "2026-08-03", "2026-07-31"),
                ("market", "market", "2026-08-03", "2026-07-31"),
                ("market", "market-prior", "2026-08-03", "2026-07-30")):
            with self.assertRaises(AiError):
                service.prepare(self.report.id, current, baseline, day, prior,
                    self.admin, bands=[{"key": "all", "lowerCents": 0,
                        "upperExclusiveCents": None}])
        with self.assertRaises(AiError):
            service.prepare(self.old_report.id, "market", "market-prior",
                "2026-08-03", "2026-07-31", self.admin,
                bands=[{"key": "all", "lowerCents": 0,
                    "upperExclusiveCents": None}])
        with self.assertRaises(AiError):
            self.prepared(limits={"maxRows": 1})
        original = service.bands_owning.report_binding._revalidate
        calls = 0
        def revoke(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return original(*args, **kwargs)
        with (patch.object(service.bands_owning.report_binding, "_revalidate",
                side_effect=revoke), self.assertRaises(AiError)):
            self.prepared()
        self.assertEqual(calls, 3)
