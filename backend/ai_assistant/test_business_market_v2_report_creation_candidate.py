"""Default-closed market-v2 report bytes over one genuine sealed promotion report."""
from unittest.mock import patch

from django import test as djtest
from django.db import transaction

from access_control.models import AppUser

from . import business_market_v2_report_creation_candidate as service
from . import business_promotion_market_runtime_v2_contract as runtime
from . import models as m, transport
from . import test_business_promotion_market_admission as fixtures
from .policy import AiError, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2ReportCreationCandidateTests(djtest.TransactionTestCase):
    user = fixtures.PromotionMarketAdmissionTests.user
    request_body = fixtures.PromotionMarketAdmissionTests.request_body
    current_catalog = fixtures.PromotionMarketAdmissionTests.current_catalog
    create_fixed_report = fixtures.PromotionMarketAdmissionTests.create_fixed_report
    planned_evidence_body = fixtures.PromotionMarketAdmissionTests.planned_evidence_body
    setUp = fixtures.PromotionMarketAdmissionTests.setUp
    selector = fixtures.PromotionMarketAdmissionTests.selector

    def prepare(self, **kwargs):
        return service.prepare(self.report.id, "market-target-report",
            self.selector(), self.admin, **kwargs)

    def test_same_sealed_screening_and_complete_market_material_pin_exact_candidate(self):
        before = (m.AiReportRun.objects.count(), m.AiWorkflowRuns.objects.count(),
            m.AiAgentJobs.objects.count(), m.AiAgentToolDispatches.objects.count(),
            m.AiAgentToolResults.objects.count())
        with patch.object(transport, "execute_tool") as remote:
            candidate = self.prepare()
            again = self.prepare()
        remote.assert_not_called()
        self.assertEqual(candidate, again)
        self.assertEqual(candidate["candidateDigest"], digest({key:value
            for key,value in candidate.items() if key != "candidateDigest"}))
        self.assertEqual(candidate["snapshot"]["sourceRoot"]["sourceReportId"],
            self.report.id)
        self.assertEqual(candidate["snapshot"]["reportId"], "market-target-report")
        self.assertEqual(candidate["workflowInput"]["market"]["selector"],
            candidate["snapshot"]["market"]["selector"])
        self.assertEqual(candidate["workflowGraphDigest"],
            digest(candidate["workflowGraph"]))
        self.assertEqual(candidate["allowedTools"], list(runtime.TOOL_ORDER))
        self.assertEqual([node["key"] for node in candidate["workflowGraph"]["nodes"]
            if node["type"] == "agent"], list(runtime.ROLES))
        self.assertTrue(candidate["marketMaterialRowsVerified"])
        self.assertFalse(candidate["reportCreateSupported"])
        self.assertFalse(candidate["agentDispatchSupported"])
        self.assertFalse(candidate["renderer8Supported"])
        self.assertFalse(candidate["registered"])
        self.assertTrue(candidate["humanReviewRequired"])
        self.assertEqual(before, (m.AiReportRun.objects.count(),
            m.AiWorkflowRuns.objects.count(), m.AiAgentJobs.objects.count(),
            m.AiAgentToolDispatches.objects.count(),
            m.AiAgentToolResults.objects.count()))

    def test_wrong_actor_target_source_date_and_capacity_are_rejected(self):
        outsider = self.user("market-create-outsider@example.invalid", "admin", None)
        for report_id, target, selector, actor in (
                (self.report.id, self.report.id, self.selector(), self.admin),
                (self.report.id, "market-target-report", self.selector(), outsider),
                (self.report.id, "market-target-report",
                    self.selector(rankBaselineKey="sales"), self.admin),
                (self.report.id, "market-target-report",
                    self.selector(currentObservationDate="2026-08-02"), self.admin)):
            with self.subTest(report=report_id, target=target), \
                    self.assertRaises(AiError):
                service.prepare(report_id, target, selector, actor)
        with self.assertRaises(AiError):
            self.prepare(limits={"maxRows": 1})

    def test_revocation_at_final_fence_and_target_collision_fail_closed(self):
        with self.assertRaises(AiError):
            service.prepare(self.report.id, self.report.id, self.selector(), self.admin)
        original = service.admission.require_observed
        calls = []
        def revoke(*args, **kwargs):
            calls.append(1)
            if len(calls) == 3:
                AppUser.objects.filter(email=self.admin.email).update(status="inactive")
            return original(*args, **kwargs)
        with transaction.atomic():
            with patch.object(service.admission, "require_observed", side_effect=revoke), \
                    self.assertRaises(AiError):
                self.prepare()
            transaction.set_rollback(True)
        self.assertGreaterEqual(len(calls), 3)
