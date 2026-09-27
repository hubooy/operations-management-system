"""An isolated PostgreSQL owner replay, without model or Agent persistence."""
from unittest.mock import patch

from django import test as djtest

from . import business_market_v2_cost_admission as costs
from . import business_market_v2_tool_observation_v6 as service
from . import models as m
from . import test_business_market_v2_cost_admission as fixture
from . import test_business_market_v2_execution_plan as plan_fixture
from .policy import AiError
from .test_business_market_v2_material_role_bridge import session_role


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class MarketV2ToolObservationV6Tests(djtest.TransactionTestCase):
    user = fixture.MarketV2CostAdmissionTests.user
    request_body = fixture.MarketV2CostAdmissionTests.request_body
    current_catalog = fixture.MarketV2CostAdmissionTests.current_catalog
    create_fixed_report = fixture.MarketV2CostAdmissionTests.create_fixed_report
    planned_evidence_body = fixture.MarketV2CostAdmissionTests.planned_evidence_body
    selector = fixture.MarketV2CostAdmissionTests.selector
    parked_id = fixture.MarketV2CostAdmissionTests.parked_id
    _attest_as_role = staticmethod(
        fixture.MarketV2CostAdmissionTests._attest_as_role)
    admitted = fixture.MarketV2CostAdmissionTests.admitted
    setUp = fixture.MarketV2CostAdmissionTests.setUp
    body = plan_fixture.MarketV2ExecutionPlanTests.body
    create_plan = plan_fixture.MarketV2ExecutionPlanTests.create_plan
    attested = plan_fixture.MarketV2ExecutionPlanTests.attested
    prepared = fixture.MarketV2CostAdmissionTests.prepared
    plan_and_model = fixture.MarketV2CostAdmissionTests.plan_and_model
    input = fixture.MarketV2CostAdmissionTests.input

    def funded_requirement_only(self):
        created, plan_id, model = self.plan_and_model()
        tariff, jobs, at = self.input(model)
        with session_role("teruisi_ai_reader"), djtest.override_settings(
                AI_MARKET_V2_COST_CANDIDATE_ENABLED=True):
            candidate = costs.prepare(created["reportId"], tariff, jobs, 5,
                "b" * 64, self.admin, at_utc=at)
        with fixture.cost_attestor(), djtest.override_settings(
                AI_MARKET_V2_COST_CANDIDATE_ENABLED=True):
            costs.record(plan_id, candidate["candidateJson"])
        return created

    def test_actual_sealed_summary_and_page_are_observed_without_dispatch(self):
        created = self.funded_requirement_only()
        before = tuple(table.objects.count() for table in (
            m.AiAgentJobs, m.AiAgentProviderDispatches,
            m.AiAgentProviderResults, m.AiAgentToolDispatches,
            m.AiAgentToolResults, m.AiBusinessMarketV2ReadReceipt))
        flags = {"AI_MARKET_V2_READ_PLAN_V6_ENABLED": True,
            "AI_MARKET_V2_TOOL_OBSERVATION_V6_ENABLED": True}
        with session_role("teruisi_ai_reader"), djtest.override_settings(
                **flags), patch("ai_assistant.provider.turn") as provider, \
                patch("ai_assistant.transport.execute_tool") as remote:
            summary = service.observe(created["reportId"],
                "market-v6-observation-report", "market-v6-observation-flow",
                "market_b2b", {"mode": "summary"}, self.admin)
            page = service.observe(created["reportId"],
                "market-v6-observation-report", "market-v6-observation-flow",
                "market_b2b", {"mode": "page", "view": "price_band",
                    "offset": 0, "limit": 20}, self.admin)
            provider.assert_not_called()
            remote.assert_not_called()
        self.assertEqual(summary["mode"], "summary")
        self.assertEqual(page["mode"], "page")
        self.assertGreater(page["observedRowCount"], 0)
        self.assertTrue(page["sourceReplayedByOwner"])
        self.assertTrue(page["sameResultOnSecondReplay"])
        self.assertEqual(page["proposedJobId"], summary["proposedJobId"])
        for value in (summary, page):
            for field in ("providerResponseAuthenticated", "jobPersisted",
                    "toolDispatchPersisted", "observationPersisted",
                    "agentReadPersisted", "numericCitationAllowed",
                    "humanReviewApproved", "reportPublishAuthorized"):
                self.assertFalse(value[field], field)
            self.assertTrue(value["candidateOnly"])
        self.assertEqual(tuple(table.objects.count() for table in (
            m.AiAgentJobs, m.AiAgentProviderDispatches,
            m.AiAgentProviderResults, m.AiAgentToolDispatches,
            m.AiAgentToolResults, m.AiBusinessMarketV2ReadReceipt)), before)

    def test_default_off_wrong_role_and_cross_account_never_observe(self):
        created = self.funded_requirement_only()
        outsider = self.user("v6-observer-outsider@example.invalid",
            "admin", None)
        with session_role("teruisi_ai_reader"):
            with self.assertRaisesRegex(AiError, "未启用"):
                service.observe(created["reportId"], "unwritten-report",
                    "unwritten-flow", "market_b2b", {"mode": "summary"},
                    self.admin)
            with djtest.override_settings(
                    AI_MARKET_V2_READ_PLAN_V6_ENABLED=True,
                    AI_MARKET_V2_TOOL_OBSERVATION_V6_ENABLED=True):
                with self.assertRaises(AiError):
                    service.observe(created["reportId"], "unwritten-report",
                        "unwritten-flow", "commerce", {"mode": "summary"},
                        self.admin)
                with self.assertRaises(AiError):
                    service.observe(created["reportId"], "unwritten-report",
                        "unwritten-flow", "market_b2b", {"mode": "summary"},
                        outsider)
        self.assertFalse(m.AiAgentToolDispatches.objects.exists())
        self.assertFalse(m.AiBusinessMarketV2ReadReceipt.objects.exists())
