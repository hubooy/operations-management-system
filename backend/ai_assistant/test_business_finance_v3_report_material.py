"""Sealed v3 report-intent finance pages become complete natural-month material."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from django.db import connection, transaction
from django.test import TestCase as DjangoTestCase, override_settings

from access_control.models import AppUser
from business_analysis import finance_source
from sales.auth import Principal

from . import business_finance_v3_report_material as service
from . import business_v3_report_intent as intent
from . import business_v3_seal as seal
from . import business_v3_source_read as bridge
from . import models as m, transport
from .policy import AiError, digest
from .test_business_v3_seal import BusinessV3SealTests as fixture


class FinanceV3MonthlyComparisonTests(TestCase):
    def test_amount_previous_yoy_rate_delta_and_missing_null_do_not_fill_zero(self):
        def month(name, value, *, status="present", rate=None):
            metrics = {key: {"status": "missing_subject", "value": None,
                "unit": "CNY_cent" if kind == "amount" else "basis_point"}
                for key, kind in finance_source.CORE_METRICS.items()}
            metrics["net_sales"] = {"status": status,
                "value": value if status == "present" else None,
                "unit": "CNY_cent"}
            if rate is not None:
                metrics["gross_margin"] = {"status": "present",
                    "value": rate, "unit": "basis_point"}
            return {"month": name, "metrics": metrics}
        coverage = [month("2025-08", 0), month("2026-07", 100, rate=5000),
            month("2026-08", 200, rate=6000),
            month("2026-09", None, status="missing_month"),
            month("2026-10", None, status="missing_value")]
        compared = service._comparisons(coverage)
        self.assertEqual(compared["2026-08"]["net_sales"]["previous"]
            ["growthRateBps"], 10000)
        self.assertEqual(compared["2026-08"]["net_sales"]["yearAgo"]
            ["status"], "zero_baseline")
        self.assertEqual(compared["2026-08"]["gross_margin"]["previous"]
            ["difference"], 1000)
        self.assertIsNone(compared["2026-08"]["gross_margin"]["previous"]
            ["growthRateBps"])
        self.assertEqual(compared["2026-09"]["net_sales"]["previous"]
            ["currentStatus"], "missing_month")
        self.assertIsNone(compared["2026-09"]["net_sales"]["previous"]
            ["difference"])
        self.assertEqual(compared["2026-10"]["net_sales"]["previous"]
            ["currentStatus"], "missing_value")


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class FinanceV3ReportMaterialTests(DjangoTestCase):
    setUp = fixture.setUp
    signed_page = fixture.signed_page
    collect = fixture.collect

    def report_intent(self):
        self.collect()
        sealed = seal.finish(self.run_id, 3, self.principal)
        return intent.create({"schemaVersion": intent.REQUEST_SCHEMA,
            "clientRequestId": "finance-material", "executionProfile":
                "business-agent-reference-v3-candidate",
            "evidenceRunId": self.run_id,
            "expectedEvidenceVersion": sealed["version"],
            "expectedSealDigest": sealed["seal"]["sealedDigest"]},
            self.principal)["item"]["id"]

    def test_real_signed_bridge_replays_all_rows_month_counts_and_missing_value(self):
        report_id = self.report_intent()
        before = (m.AiBusinessEvidenceChunk.objects.count(),
            m.AiBusinessSourceToolReceipt.objects.count(),
            m.AiReportRun.objects.count(), m.AiAgentJobs.objects.count())
        with patch.object(transport, "execute_tool") as remote:
            material = service.prepare(report_id, "finance", self.principal).value
        remote.assert_not_called()
        self.assertEqual(material["intentId"], report_id)
        self.assertEqual(material["evidenceRunId"], self.run_id)
        self.assertEqual(material["sourceKey"], "finance")
        self.assertEqual(material["naturalMonths"], ["2026-08", "2026-09"])
        self.assertEqual(material["rowCount"], len(material["rows"]))
        self.assertEqual(material["monthlyCoverage"][0]["metrics"]["net_sales"]
            ["value"], 100000)
        self.assertEqual(material["monthlyCoverage"][1]["metrics"]["net_sales"]
            ["status"], "missing_subject")
        self.assertEqual(material["comparisons"]["2026-09"]["net_sales"]
            ["previous"]["currentStatus"], "missing_subject")
        self.assertFalse(material["financeDailyProrationAllowed"])
        self.assertFalse(material["financeSkuProfitAttributionAllowed"])
        self.assertFalse(material["sumOverlappingErpB2bAdsAllowed"])
        self.assertFalse(material["agentReadReceiptRecorded"])
        self.assertEqual(material["resultDigest"], digest({key: value for key, value
            in material.items() if key != "resultDigest"}))
        self.assertEqual(before, (m.AiBusinessEvidenceChunk.objects.count(),
            m.AiBusinessSourceToolReceipt.objects.count(),
            m.AiReportRun.objects.count(), m.AiAgentJobs.objects.count()))

    def test_missing_published_month_remains_gap_and_not_zero(self):
        page = deepcopy(self.finance_page)
        page["publication"]["months"] = page["publication"]["months"][:1]
        selected_batch_ids = {item["batch_id"] for item in page["publication"]["months"]}
        page["publication"]["batches"] = [item for item in
            page["publication"]["batches"] if item["id"] in selected_batch_ids]
        page["publication"]["missingMonths"] = ["2026-09"]
        page["pageDigest"] = digest({key: value for key, value in page.items()
            if key != "pageDigest"})
        self.finance_page = page
        report_id = self.report_intent()
        material = service.prepare(report_id, "finance", self.principal).value
        self.assertEqual(material["missingMonths"], ["2026-09"])
        cell = material["monthlyCoverage"][1]["metrics"]["net_sales"]
        self.assertEqual(cell["status"], "missing_month")
        self.assertIsNone(cell["value"])
        self.assertIsNone(material["comparisons"]["2026-09"]["net_sales"]
            ["previous"]["difference"])

    def test_wrong_actor_source_revision_slice_and_bridge_capacity_reject(self):
        report_id = self.report_intent()
        wrong = Principal("other-finance-v3@example.invalid", "Other", "admin", None)
        with self.assertRaises(AiError):
            service.prepare(report_id, "finance", wrong)
        with self.assertRaises(AiError):
            service.prepare(report_id, "sales", self.principal)
        with patch.object(bridge, "MAX_PREPARED_BYTES", 0), \
                self.assertRaises(AiError) as cap:
            service.prepare(report_id, "finance", self.principal)
        self.assertEqual(cap.exception.status, 413)
        original = bridge.page
        def changed(*args, **kwargs):
            value = original(*args, **kwargs)
            if value["sourceKey"] == "finance":
                value["sourceRevision"] = "forged"
            return value
        with patch.object(bridge, "page", side_effect=changed), \
                self.assertRaises(AiError):
            service.prepare(report_id, "finance", self.principal)
        def incomplete(*args, **kwargs):
            value = original(*args, **kwargs)
            if value["sourceKey"] == "finance":
                value["returned"] = 0
            return value
        with patch.object(bridge, "page", side_effect=incomplete), \
                self.assertRaises(AiError):
            service.prepare(report_id, "finance", self.principal)

    def test_late_revocation_before_final_report_fence_rejects(self):
        report_id = self.report_intent()
        original = intent.inspect
        calls = []
        def revoke(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                AppUser.objects.filter(email=self.principal.email).update(status="inactive")
            return original(*args, **kwargs)
        with transaction.atomic():
            with patch.object(intent, "inspect", side_effect=revoke), \
                    self.assertRaises(AiError):
                service.prepare(report_id, "finance", self.principal)
            transaction.set_rollback(True)
        self.assertEqual(len(calls), 2)
