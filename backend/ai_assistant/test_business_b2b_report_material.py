"""Isolated PG: one sealed v2 report owns exact JD B2B candidate pages."""
from copy import deepcopy
from unittest.mock import patch

from django import test as djtest
from django.db import connection

from access_control.models import AppUser
from business_analysis import mapping_plan
from netshop.models import NetshopRow

from . import business_b2b_report_material as service, models as m
from . import test_business_integrated_guard as fixtures
from .test_business_evidence import versioned_netshop_facts
from .policy import AiError, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessB2bReportMaterialTests(djtest.TransactionTestCase):
    user = fixtures.BusinessIntegratedGuardTests.user
    call = fixtures.BusinessIntegratedGuardTests.call
    bundle = fixtures.BusinessIntegratedGuardTests.bundle
    input_for = fixtures.BusinessIntegratedGuardTests.input_for
    insert = fixtures.BusinessIntegratedGuardTests.insert
    seed = fixtures.BusinessIntegratedGuardTests.seed
    collect_body = fixtures.BusinessIntegratedGuardTests.collect_body

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires sealed v2 PostgreSQL source")
        fixtures.BusinessIntegratedGuardTests.setUp(self)

    def add_b2b(self):
        dates = (("current", "2026-08-01", 100),
                 ("previous", "2026-07-31", 50),
                 ("yearAgo", "2025-08-01", 25))
        with versioned_netshop_facts():
            for index, (_, day, amount) in enumerate(dates, 1):
                metrics = {"transactionAmountCents": amount,
                    "transactionQuantity": 1, "visitors": 2,
                    "pageViews": 3, "transactionOrders": 1}
                NetshopRow.objects.create(source_row_key=f"b2b-owned-{index}",
                    source_row_hash=digest(["b2b-owned", index]),
                    first_import_batch_id="fixture", last_import_batch_id="fixture",
                    source_row_number=index, source="jd_b2b", dataset="b2b",
                    platform="京东", shop_name=self.query["shop"], business_date=day,
                    sku_id="SKU1", spu_id="SPU1", transaction_amount_cents=amount,
                    transaction_quantity=1, visitors=2, page_views=3,
                    transaction_orders=1, metrics_json=metrics, raw_json={})
        return dates

    def report_with_b2b(self):
        dates = self.add_b2b()
        body = deepcopy(self.evidence_body)
        body["clientRequestId"] = "b2b-owned-report"
        common = {key: self.query[key] for key in
            ("platform", "startDate", "endDate")}
        body["sources"].extend({"key": f"b2b-{window}",
            "domain": "netshop", "query": {**common, "shop": self.query["shop"],
                "dataset": "b2b", "window": window}}
            for window, _, _ in dates)
        self.sources = body["sources"]
        self.parent = self.collect_body(body)
        self.plan = mapping_plan.build(self.sources,
            [{"salesKey": "sales", "masterKey": "master"}])
        return self.seed()[0]

    def test_all_three_exact_sources_replayed_daily_period_and_unknown_overlap(self):
        report = self.report_with_b2b()
        before = (report.snapshot_json, m.AiBusinessEvidenceChunk.objects.filter(
            run_id=self.parent.id).count())
        result = service.prepare(report.id, self.query["shop"], self.admin)
        self.assertEqual(result["schemaVersion"], service.SCHEMA)
        self.assertEqual(result["reportBinding"]["reportId"], report.id)
        self.assertEqual(result["catalogueMissingWindows"], [])
        self.assertTrue(result["sealedReportDirectoryVerified"])
        self.assertTrue(result["sealedSelectedSourcesFullyReplayed"])
        self.assertFalse(result["databaseGlobalB2BAbsenceVerified"])
        self.assertEqual({window: value["rowCount"] for window, value in
            result["sourceSummary"].items()}, {window: 1 for window in
                ("current", "previous", "yearAgo")})
        rows = result["material"]["windows"]
        self.assertEqual([rows[window]["periodMetrics"]["paymentCents"]["value"]
            for window in ("current", "previous", "yearAgo")], [100, 50, 25])
        self.assertEqual(result["material"]["comparisons"]["previous"]["paymentCents"]
            ["growthRateBps"], 10000)
        self.assertEqual(result["material"]["comparisons"]["yearAgo"]["paymentCents"]
            ["growthRateBps"], 30000)
        self.assertEqual(rows["current"]["daily"][0]["metrics"]["paymentCents"]
            ["value"], 100)
        self.assertEqual(result["b2bIncludedInErpSales"], "unknown")
        self.assertIsNone(result["b2bShareOfErpSales"])
        self.assertIsNone(result["b2bIncrementalSalesCents"])
        report.refresh_from_db()
        self.assertEqual((report.snapshot_json,
            m.AiBusinessEvidenceChunk.objects.filter(run_id=self.parent.id).count()),
            before)

    def test_missing_only_means_absent_from_sealed_report_catalog(self):
        report = self.seed()[0]
        self.add_b2b()  # Existing DB rows cannot retroactively enter this seal.
        result = service.prepare(report.id, self.query["shop"], self.admin)
        self.assertEqual(result["catalogueMissingWindows"],
            ["current", "previous", "yearAgo"])
        self.assertEqual(result["sourceSummary"]["current"]["status"],
            "catalogue_only_missing_source")
        self.assertIsNone(result["material"]["windows"]["current"]
            ["periodMetrics"]["paymentCents"]["value"])
        self.assertFalse(result["databaseGlobalB2BAbsenceVerified"])

    def test_wrong_shop_actor_capacity_and_late_revocation_reject(self):
        report = self.report_with_b2b()
        for shop, actor in ((self.query["shop"], self.viewer),
                            ("另一店", self.admin),
                            (self.query["shop"],
                             self.user("b2b-other@example.invalid", "admin", None))):
            with self.subTest(shop=shop, actor=actor.email), self.assertRaises(AiError):
                service.prepare(report.id, shop, actor)
        with patch.object(service, "MAX_SELECTED_PAGES", 2), self.assertRaises(AiError):
            service.prepare(report.id, self.query["shop"], self.admin)
        def revoke(event):
            if event == {"stage": "b2b_report", "phase": "complete"}:
                AppUser.objects.filter(email=self.admin.email).update(status="inactive")
        with self.assertRaises(AiError):
            service.prepare(report.id, self.query["shop"], self.admin,
                checkpoint=revoke)
