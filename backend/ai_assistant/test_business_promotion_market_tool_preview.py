"""Internal three-table market tool preview over a real sealed promotion report."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from access_control.models import AppUser

from . import business_promotion_market_tool_preview as service
from . import business_promotion_market_admission as admission
from . import business_promotion_market_runtime_v2_contract as runtime
from . import models as m
from . import test_business_promotion_market_admission as fixtures
from .policy import AiError, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionMarketToolPreviewTests(djtest.TransactionTestCase):
    user = fixtures.PromotionMarketAdmissionTests.user
    request_body = fixtures.PromotionMarketAdmissionTests.request_body
    current_catalog = fixtures.PromotionMarketAdmissionTests.current_catalog
    create_fixed_report = fixtures.PromotionMarketAdmissionTests.create_fixed_report
    planned_evidence_body = fixtures.PromotionMarketAdmissionTests.planned_evidence_body
    setUp = fixtures.PromotionMarketAdmissionTests.setUp
    selector = fixtures.PromotionMarketAdmissionTests.selector

    def roots(self):
        selector = self.selector()
        fixed = admission.require_observed(self.report.id,selector,self.admin)
        proposal = runtime.prepare(fixed)
        return selector,fixed,proposal

    def invoke(self, selector, fixed, proposal, args, *, role="market_b2b",
               actor=None, checkpoint=None):
        return service.read(self.report.id,selector,fixed["bindingDigest"],
            role,{"reportId":self.report.id,
                "marketContextDigest":proposal["marketContextDigest"],**args},
            actor or self.admin,checkpoint=checkpoint)

    def test_summary_fully_verifies_three_tables_without_agent_receipt_or_live_market(self):
        selector,fixed,proposal = self.roots()
        before = (m.AiAgentToolDispatches.objects.count(),
            m.AiAgentToolResults.objects.count())
        with patch("ai_assistant.transport.execute_tool") as remote, \
                CaptureQueriesContext(connection) as queries:
            result = self.invoke(selector,fixed,proposal,{"mode":"summary"})
        remote.assert_not_called()
        self.assertFalse(any("market_ranking_entries" in row["sql"].lower()
            for row in queries))
        payload = result["payload"]
        self.assertEqual([table["view"] for table in payload["tables"]],
            ["price_band_summary","price_band_members","rank_entry_exit"])
        self.assertTrue(payload["observationCoverage"]["bothDatesPresent"])
        self.assertTrue(result["serverFullMarketMaterialVerified"])
        self.assertFalse(result["agentReadPersisted"])
        self.assertFalse(result["actualAgentBound"])
        self.assertFalse(result["authorityVerified"])
        self.assertFalse(payload["marketAndOwnSalesAdditive"])
        self.assertFalse(payload["priceSummaryAndMembersAdditive"])
        self.assertEqual(result["resultDigest"],digest({key:value for key,value
            in result.items() if key != "resultDigest"}))
        self.assertEqual((m.AiAgentToolDispatches.objects.count(),
            m.AiAgentToolResults.objects.count()),before)

    def test_price_and_rank_page_then_exact_row_bind_full_manifest(self):
        selector,fixed,proposal = self.roots()
        for view in ("price_band","rank_entry_exit"):
            page = self.invoke(selector,fixed,proposal,{"mode":"page",
                "view":view,"offset":0,"limit":20})
            rows = page["payload"]["rows"]
            self.assertTrue(rows)
            self.assertEqual(page["payload"]["pagination"]["offset"],0)
            first = rows[0]
            row = self.invoke(selector,fixed,proposal,{"mode":"row",
                "view":view,"rowIndex":first["rowIndex"],
                "rowId":first["rowId"]})
            self.assertEqual(row["payload"]["row"],first)
            self.assertEqual(row["marketManifestDigest"],page["marketManifestDigest"])

    def test_wrong_role_root_source_date_row_and_manifest_reject(self):
        selector,fixed,proposal = self.roots()
        for kwargs in ({"role":"commerce"},
                {"actor":self.user("market-preview-other@example.invalid","admin",None)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AiError):
                self.invoke(selector,fixed,proposal,{"mode":"summary"},**kwargs)
        with self.assertRaises(AiError):
            service.read(self.report.id,selector,"0"*64,"market_b2b",
                {"reportId":self.report.id,
                    "marketContextDigest":proposal["marketContextDigest"],
                    "mode":"summary"},self.admin)
        bad = self.selector(rankBaselineKey="market-current")
        with self.assertRaises(AiError):
            self.invoke(bad,fixed,proposal,{"mode":"summary"})
        missing = self.selector(baselineObservationDate="2026-07-30")
        with self.assertRaises(AiError):
            self.invoke(missing,fixed,proposal,{"mode":"summary"})
        with self.assertRaises(AiError):
            self.invoke(selector,fixed,proposal,{"mode":"row",
                "view":"rank_entry_exit","rowIndex":0,"rowId":"0"*64})
        original = service.composite.prepare
        def tampered(*args,**kwargs):
            actual = original(*args,**kwargs)
            changed = deepcopy(actual.manifest)
            changed["authority"]["marketAndOwnSalesAdditive"] = True
            changed["manifestDigest"] = digest({key:value for key,value
                in changed.items() if key != "manifestDigest"})
            return SimpleNamespace(manifest=changed,ndjson_pages=actual.ndjson_pages)
        with patch.object(service.composite,"prepare",side_effect=tampered), \
                self.assertRaises(AiError):
            self.invoke(selector,fixed,proposal,{"mode":"summary"})

    def test_late_revocation_refuses_result_after_full_market_replay(self):
        selector,fixed,proposal = self.roots()
        def revoke(event):
            if event == {"stage":"market_composite_export","phase":"complete"}:
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with self.assertRaises(AiError):
            self.invoke(selector,fixed,proposal,{"mode":"summary"},checkpoint=revoke)
