"""Isolated reader-role transport sizing over the actual admitted market root."""
from copy import deepcopy
from unittest.mock import patch

from django import test as djtest

from . import business_market_v2_transport_candidate as service
from . import business_market_v2_transport_contract as contract
from . import business_promotion_market_admission as admission
from . import business_promotion_market_runtime_v2_contract as runtime
from . import models as m
from . import test_business_market_v2_material_role_bridge as fixture
from .policy import AiError, canonical, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2TransportCandidateTests(djtest.TransactionTestCase):
    user = fixture.MarketV2MaterialRoleBridgeTests.user
    request_body = fixture.MarketV2MaterialRoleBridgeTests.request_body
    current_catalog = fixture.MarketV2MaterialRoleBridgeTests.current_catalog
    create_fixed_report = fixture.MarketV2MaterialRoleBridgeTests.create_fixed_report
    planned_evidence_body = fixture.MarketV2MaterialRoleBridgeTests.planned_evidence_body
    setUp = fixture.MarketV2MaterialRoleBridgeTests.setUp
    selector = fixture.MarketV2MaterialRoleBridgeTests.selector
    parked_id = fixture.MarketV2MaterialRoleBridgeTests.parked_id
    _attest_as_role = staticmethod(fixture.MarketV2MaterialRoleBridgeTests._attest_as_role)
    admitted = fixture.MarketV2MaterialRoleBridgeTests.admitted

    def roots(self):
        parked_id, created, snapshot, prepared = self.admitted()
        selected = self.selector()
        fixed = admission.require_observed(self.report.id, selected, self.admin)
        runtime_candidate = runtime.prepare(fixed, with_budget=snapshot["withBudget"])
        call = {"schemaVersion": service.owning.contract.SCHEMA,
            "admittedReportId": created["reportId"],
            "jobId": "future-market-job", "providerDispatchId": "future-market-provider",
            "providerCallId": "future-call", "role": "market_b2b",
            "marketManifestDigest": prepared["candidate"]["manifestDigest"],
            "marketContextDigest": runtime_candidate["marketContextDigest"]}
        return selected, call

    @staticmethod
    def arguments(call, mode, **extra):
        return {"reportId": call["admittedReportId"],
            "marketContextDigest": call["marketContextDigest"],
            "mode": mode, **extra}

    def test_actual_summary_page_row_fit_registry_envelope_without_receipt(self):
        selected, call = self.roots()
        before = (m.AiAgentJobs.objects.count(),
            m.AiAgentProviderDispatches.objects.count(),
            m.AiAgentToolDispatches.objects.count(),
            m.AiAgentToolResults.objects.count())
        with fixture.session_role("teruisi_ai_reader"), patch(
                "ai_assistant.transport.execute_tool") as remote:
            summary = service.read(contract.SURFACE, contract.PROFILE,
                contract.TOOL, call, self.arguments(call, "summary"), self.admin)
            self.assertEqual([row["view"] for row in summary["payload"]["tables"]],
                ["price_band_summary", "price_band_members", "rank_entry_exit"])
            page = service.read(contract.SURFACE, contract.PROFILE,
                contract.TOOL, call, self.arguments(call, "page",
                    view="price_band", offset=0, limit=20), self.admin)
            first = page["payload"]["rows"][0]
            row = service.read(contract.SURFACE, contract.PROFILE,
                contract.TOOL, call, self.arguments(call, "row",
                    view="price_band", rowIndex=first["rowIndex"],
                    rowId=first["rowId"]), self.admin)
            remote.assert_not_called()
        for result in (summary, page, row):
            raw = canonical(result)
            self.assertLessEqual(len(raw.encode("utf-8")), 38000)
            self.assertLessEqual(len(raw.encode("utf-16-le")) // 2, 38000)
            self.assertFalse(result["persistedRead"])
            self.assertFalse(result["sameJobProviderPersisted"])
            self.assertFalse(result["registeredTool"])
            self.assertEqual(result["resultDigest"], digest({key: value for key,
                value in result.items() if key != "resultDigest"}))
        self.assertEqual(row["citationBases"][0]["rowId"], first["rowId"])
        self.assertEqual(row["numericReferenceRequiredFields"], ["metric", "field"])
        self.assertEqual((m.AiAgentJobs.objects.count(),
            m.AiAgentProviderDispatches.objects.count(),
            m.AiAgentToolDispatches.objects.count(),
            m.AiAgentToolResults.objects.count()), before)

    def test_wrong_surface_timeout_and_oversize_all_fail_without_dispatch(self):
        selected, call = self.roots()
        request = self.arguments(call, "summary")
        with self.assertRaises(AiError):
            service.read("business_agent_screening_promotion_v1", contract.PROFILE,
                contract.TOOL, call, request, self.admin)
        fake = {"admittedReportId": call["admittedReportId"],
            "sourceReportId": self.report.id, "role": call["role"],
            "mode": "summary", "marketManifestDigest": call["marketManifestDigest"],
            "payload": {"tables": []}, "citationBases": [],
            "serverFullMarketMaterialVerified": True,
            "sameJobProviderPersisted": False, "persistedRead": False,
            "registeredTool": False, "authorityVerified": False}
        fake["resultDigest"] = digest(fake)
        ticks = iter((0.0, 0.0, 12.1))
        with fixture.session_role("teruisi_ai_reader"), patch.object(
                service.owning, "read", return_value=fake), self.assertRaises(AiError) as caught:
            service.read(contract.SURFACE, contract.PROFILE, contract.TOOL,
                call, request, self.admin, clock=lambda: next(ticks))
        self.assertEqual(caught.exception.code, "timeout")
        huge = deepcopy(fake)
        huge["payload"] = {"tables": [], "text": "中" * 13000}
        huge["resultDigest"] = digest({key: value for key, value in huge.items()
            if key != "resultDigest"})
        with fixture.session_role("teruisi_ai_reader"), patch.object(
                service.owning, "read", return_value=huge), self.assertRaises(AiError) as caught:
            service.read(contract.SURFACE, contract.PROFILE, contract.TOOL,
                call, request, self.admin, clock=lambda: 0.0)
        self.assertEqual(caught.exception.code, "payload_too_large")
        self.assertFalse(m.AiAgentToolDispatches.objects.exists())
