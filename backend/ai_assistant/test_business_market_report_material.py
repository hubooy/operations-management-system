"""Real sealed promotion-v2 market material, without Agent or file writes."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from access_control.models import AppUser

from . import business_market_report_material as service
from . import business_market_composite_export as composite
from . import test_business_promotion_market_admission as fixtures
from .policy import AiError, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessMarketReportMaterialTests(djtest.TransactionTestCase):
    user = fixtures.PromotionMarketAdmissionTests.user
    request_body = fixtures.PromotionMarketAdmissionTests.request_body
    current_catalog = fixtures.PromotionMarketAdmissionTests.current_catalog
    create_fixed_report = fixtures.PromotionMarketAdmissionTests.create_fixed_report
    planned_evidence_body = fixtures.PromotionMarketAdmissionTests.planned_evidence_body
    setUp = fixtures.PromotionMarketAdmissionTests.setUp
    selector = fixtures.PromotionMarketAdmissionTests.selector

    def test_three_complete_typed_tables_use_same_sealed_report_without_live_market(self):
        with patch("ai_assistant.transport.execute_tool") as remote, \
                CaptureQueriesContext(connection) as queries:
            with service.prepare(self.report.id, self.selector(), self.admin) as material:
                manifest, summary = material.manifest, material.summary
                tables = material.tables
                rows = [list(table.rows) for table in tables]
        remote.assert_not_called()
        self.assertFalse(any("market_ranking_entries" in row["sql"].lower()
            for row in queries))
        self.assertEqual(summary["reportId"], self.report.id)
        self.assertEqual(summary["marketManifestDigest"], manifest["manifestDigest"])
        self.assertEqual([spec["view"] for spec in manifest["tables"]],
            ["price_band_summary", "price_band_members", "rank_entry_exit"])
        self.assertEqual([len(part) for part in rows],
            [spec["rowCount"] for spec in manifest["tables"]])
        self.assertTrue(summary["selectedSealedSourcesFullyReplayed"])
        self.assertTrue(summary["typedMarketRowsVerified"])
        self.assertFalse(summary["authorityVerified"])
        self.assertFalse(summary["marketAndOwnSalesAdditive"])
        self.assertFalse(summary["registeredAgentTool"])
        self.assertFalse(summary["registeredRenderer"])
        self.assertEqual(summary["summaryDigest"], digest({key: value
            for key, value in summary.items() if key != "summaryDigest"}))
        with self.assertRaises(AiError):
            _ = material.tables

    def test_wrong_report_actor_source_day_and_capacity_fail_closed(self):
        outside = self.user("market-material-outside@example.invalid", "admin", None)
        for report_id, selector, actor in (
                (self.report.id, self.selector(), outside),
                (self.report.id, self.selector(rankBaselineKey="sales"), self.admin),
                (self.report.id, self.selector(currentObservationDate="2026-08-02"), self.admin),
                ("missing-report", self.selector(), self.admin)):
            with self.subTest(report=report_id, actor=actor.email), \
                    self.assertRaises(AiError):
                with service.prepare(report_id, selector, actor):
                    pass
        with self.assertRaises(AiError):
            with service.prepare(self.report.id, self.selector(), self.admin,
                    limits={"maxRows": 1}):
                pass

    def test_changed_market_manifest_and_late_revocation_reject(self):
        original = composite.prepare
        def wrong_source(*args, **kwargs):
            prepared = original(*args, **kwargs)
            changed = deepcopy(prepared.manifest)
            changed["sourceDescriptors"]["rankBaseline"]["queryDigest"] = "0"*64
            changed["sourceDescriptorsDigest"] = digest(changed["sourceDescriptors"])
            changed["manifestDigest"] = digest({key:value for key,value in
                changed.items() if key != "manifestDigest"})
            return SimpleNamespace(manifest=changed, ndjson_pages=prepared.ndjson_pages)
        with patch.object(service.composite, "prepare", side_effect=wrong_source), \
                self.assertRaises(AiError):
            with service.prepare(self.report.id, self.selector(), self.admin):
                pass
        with self.assertRaises(AiError):
            with service.prepare(self.report.id, self.selector(), self.admin):
                AppUser.objects.filter(email=self.admin.email).update(status="inactive")
