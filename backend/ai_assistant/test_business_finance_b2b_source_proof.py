"""Isolated PG owning sources stay separate in the proof-table adapter."""
from copy import deepcopy
from unittest.mock import patch

from access_control.models import AppUser
from business_analysis import mapping_plan
from business_analysis.contracts import digest
from django import test as djtest
from django.db import connection, transaction

from . import business_finance_b2b_source_proof as adapter
from . import business_b2b_report_material as b2b_owner
from . import models as m
from . import test_business_b2b_report_material as b2b_fixture
from . import test_business_finance_v3_report_material as finance_fixture
from .control_models import AiDataRevision, AiWriteAuthority
from .policy import AiError


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class FinanceB2bSourceProofOwningTests(djtest.TransactionTestCase):
    # v3 finance and v2 B2B fixtures use different source protocols and
    # default accounts. Stage finance first, then build a new B2B report under
    # the same surviving admin. The projection never equates their report IDs.
    user = b2b_fixture.BusinessB2bReportMaterialTests.user
    call = b2b_fixture.BusinessB2bReportMaterialTests.call
    bundle = b2b_fixture.BusinessB2bReportMaterialTests.bundle
    input_for = b2b_fixture.BusinessB2bReportMaterialTests.input_for
    insert = b2b_fixture.BusinessB2bReportMaterialTests.insert
    seed = b2b_fixture.BusinessB2bReportMaterialTests.seed
    collect_body = b2b_fixture.BusinessB2bReportMaterialTests.collect_body
    add_b2b = b2b_fixture.BusinessB2bReportMaterialTests.add_b2b
    report_with_b2b = b2b_fixture.BusinessB2bReportMaterialTests.report_with_b2b
    signed_page = finance_fixture.FinanceV3ReportMaterialTests.signed_page
    collect = finance_fixture.FinanceV3ReportMaterialTests.collect
    report_intent = finance_fixture.FinanceV3ReportMaterialTests.report_intent

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated sealed v3 finance and v2 B2B")
        # TransactionTestCase flushes migration data before each test. Restore
        # the same development authority seeds used by AiDomainTests before
        # invoking the v3 TestCase fixture, which normally inherits them.
        AiDataRevision.objects.get_or_create(domain="ai-assistant")
        AiWriteAuthority.objects.get_or_create(id=1)
        finance_fixture.FinanceV3ReportMaterialTests.setUp(self)
        self.finance_intent = self.report_intent()
        self.finance_actor = self.principal
        b2b_fixture.BusinessB2bReportMaterialTests.setUp(self)
        # The existing v2 fixture's initial evidence belongs to its separate
        # admin. Subsequent source/report creation uses the same v3 actor.
        self.admin = self.finance_actor

    def test_same_admin_reads_two_real_owning_sources_without_same_report_claim(self):
        report = self.report_with_b2b()
        before = (m.AiBusinessEvidenceChunk.objects.count(),
            m.AiBusinessSourceToolReceipt.objects.count(),
            m.AiAgentJobs.objects.count())
        result = adapter.prepare(self.finance_actor,
            finance_intent_id=self.finance_intent,
            finance_source_key="finance", b2b_report_id=report.id,
            shop=self.query["shop"], enabled=True)
        candidate = result["candidate"]
        self.assertEqual((candidate["financeIntentId"],
            candidate["b2bReportId"]), (self.finance_intent, report.id))
        self.assertNotEqual(self.finance_intent, report.id)
        self.assertEqual(result["table"].row_count, candidate["rowCount"])
        self.assertTrue(any(row["domain"] == "finance" and
            row["sourceStatus"] == "published_month" for row in candidate["rows"]))
        self.assertTrue(any(row["domain"] == "b2b" and
            row["sourceStatus"] == "selected_sealed_source"
            for row in candidate["rows"]))
        self.assertFalse(result["sameReportAuthorityVerified"])
        self.assertFalse(result["agentReadPersisted"])
        self.assertFalse(result["registeredRenderer"])
        self.assertFalse(candidate["financeDailyProrationAllowed"])
        self.assertFalse(candidate["crossDomainAmountsAdded"])
        self.assertIsNone(candidate["b2bIncrementalSalesCents"])
        self.assertEqual(candidate["b2bIncludedInErpSales"], "unknown")
        self.assertEqual(candidate["candidateDigest"], digest({key: value
            for key, value in candidate.items() if key != "candidateDigest"}))
        self.assertEqual((m.AiBusinessEvidenceChunk.objects.count(),
            m.AiBusinessSourceToolReceipt.objects.count(),
            m.AiAgentJobs.objects.count()), before)

    def test_erp_and_b2b_same_sealed_report_but_finance_remains_monthly_context(self):
        report = self.report_with_b2b()
        pair_key = self.plan["plan"]["pairs"][0]["pairKey"]
        result = adapter.prepare(self.finance_actor,
            finance_intent_id=self.finance_intent,
            finance_source_key="finance", b2b_report_id=report.id,
            shop=self.query["shop"], erp_pair_key=pair_key,
            erp_sales_key="sales", erp_master_key="master", enabled=True)
        rows = result["candidate"]["rows"]
        erp_rows = [row for row in rows if row["domain"] == "erp"]
        self.assertEqual(len(erp_rows), 1)
        self.assertEqual(erp_rows[0]["periodRole"], "current")
        self.assertEqual(erp_rows[0]["sourceStatus"], "selected_sealed_source")
        self.assertEqual(erp_rows[0]["scopeOrShop"], self.query["shop"])
        self.assertTrue(result["erpB2bSameReportOwningVerified"])
        self.assertTrue(result["candidate"]["erpB2bSameReportBindingVerified"])
        self.assertFalse(result["sameReportAuthorityVerified"])
        self.assertFalse(result["candidate"]["sameShopIdentityVerified"])
        self.assertEqual(result["candidate"]["b2bIncludedInErpSales"], "unknown")
        self.assertIsNone(result["candidate"]["b2bIncrementalSalesCents"])
        self.assertFalse(result["candidate"]["crossDomainAmountsAdded"])
        self.assertEqual(result["table"].row_count, len(rows))
        for bad in ({"erp_pair_key": "0" * 64},
                    {"erp_sales_key": "ads"},
                    {"shop": "另一京东店"},
                    {"erp_master_key": None}):
            kwargs = {"finance_intent_id": self.finance_intent,
                "finance_source_key": "finance", "b2b_report_id": report.id,
                "shop": self.query["shop"], "erp_pair_key": pair_key,
                "erp_sales_key": "sales", "erp_master_key": "master",
                "enabled": True}
            kwargs.update(bad)
            with self.subTest(bad=bad), self.assertRaises(AiError):
                adapter.prepare(self.finance_actor, **kwargs)

    def test_wrong_finance_source_report_shop_and_actor_refuse(self):
        report = self.report_with_b2b()
        for change in (
            {"finance_source_key": "sales"},
            {"b2b_report_id": self.finance_intent},
            {"shop": "另一京东店"},
        ):
            kwargs = {"finance_intent_id": self.finance_intent,
                "finance_source_key": "finance", "b2b_report_id": report.id,
                "shop": self.query["shop"], "enabled": True}
            kwargs.update(change)
            with self.subTest(change=change), self.assertRaises(AiError):
                adapter.prepare(self.finance_actor, **kwargs)
        with self.assertRaises(AiError):
            adapter.prepare(self.viewer, finance_intent_id=self.finance_intent,
                finance_source_key="finance", b2b_report_id=report.id,
                shop=self.query["shop"], enabled=True)

    def test_same_admin_finance_present_b2b_unselected_remains_null(self):
        body = deepcopy(self.evidence_body)
        body["clientRequestId"] = "finance-b2b-catalogue-gap"
        self.sources = body["sources"]
        self.parent = self.collect_body(body)
        self.plan = mapping_plan.build(self.sources,
            [{"salesKey": "sales", "masterKey": "master"}])
        report = self.seed()[0]
        result = adapter.prepare(self.finance_actor,
            finance_intent_id=self.finance_intent,
            finance_source_key="finance", b2b_report_id=report.id,
            shop=self.query["shop"], enabled=True)
        rows = result["candidate"]["rows"]
        self.assertTrue(any(row["domain"] == "finance" and
            row["sourceStatus"] == "published_month" for row in rows))
        missing = [row for row in rows if row["domain"] == "b2b"]
        self.assertEqual(len(missing), 3)
        self.assertTrue(all(row["sourceStatus"] == "catalogue_only_missing_source"
            and row["rowCount"] is None and row["sourceRef"] is None
            for row in missing))
        self.assertFalse(result["sameReportAuthorityVerified"])

    def test_default_closed_and_account_revocation_before_final_return(self):
        report = self.report_with_b2b()
        with patch.object(adapter.finance_owner, "prepare",
                side_effect=AssertionError("must remain closed")) as finance_call:
            with self.assertRaises(AiError):
                adapter.prepare(self.finance_actor,
                    finance_intent_id=self.finance_intent,
                    finance_source_key="finance", b2b_report_id=report.id,
                    shop=self.query["shop"])
            finance_call.assert_not_called()
        original = adapter.projection.build_candidate
        def revoke(*args, **kwargs):
            value = original(*args, **kwargs)
            AppUser.objects.filter(email=self.finance_actor.email).update(
                status="inactive")
            return value
        with transaction.atomic():
            with patch.object(adapter.projection, "build_candidate",
                    side_effect=revoke), self.assertRaises(AiError):
                adapter.prepare(self.finance_actor,
                    finance_intent_id=self.finance_intent,
                    finance_source_key="finance", b2b_report_id=report.id,
                    shop=self.query["shop"], enabled=True)
            transaction.set_rollback(True)


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class B2bCatalogueGapProofOwningTests(djtest.TransactionTestCase):
    user = b2b_fixture.BusinessB2bReportMaterialTests.user
    call = b2b_fixture.BusinessB2bReportMaterialTests.call
    bundle = b2b_fixture.BusinessB2bReportMaterialTests.bundle
    input_for = b2b_fixture.BusinessB2bReportMaterialTests.input_for
    insert = b2b_fixture.BusinessB2bReportMaterialTests.insert
    seed = b2b_fixture.BusinessB2bReportMaterialTests.seed
    collect_body = b2b_fixture.BusinessB2bReportMaterialTests.collect_body
    setUp = b2b_fixture.BusinessB2bReportMaterialTests.setUp

    def test_missing_b2b_in_sealed_catalogue_is_null_not_zero(self):
        report = self.seed()[0]
        result = adapter.prepare(self.admin, b2b_report_id=report.id,
            shop=self.query["shop"], enabled=True)
        rows = [row for row in result["candidate"]["rows"]
            if row["domain"] == "b2b"]
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["sourceStatus"] for row in rows},
            {"catalogue_only_missing_source"})
        self.assertTrue(all(row["rowCount"] is None and row["sourceRef"] is None
            for row in rows))
        self.assertFalse(result["candidate"]["sameReportAuthorityVerified"])
        self.assertIsNone(result["candidate"]["b2bShareOfErpSales"])
        self.assertIsNone(result["candidate"]["b2bIncrementalSalesCents"])
