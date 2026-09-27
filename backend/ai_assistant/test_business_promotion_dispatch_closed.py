"""New profile reaches current microstep checks but cannot reserve paid work."""
from unittest.mock import patch

from django import test as djtest
from django.utils import timezone

from . import business_promotion_microstep as microstep
from . import business_promotion_runtime_contract as contract
from . import business_reports, models as m, workflows
from . import test_business_promotion_microstep as fixtures
from .policy import AiError, mutation


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionDispatchClosedTests(djtest.TransactionTestCase):
    user = fixtures.PromotionMicrostepTests.user
    call = fixtures.PromotionMicrostepTests.call
    collect_body = fixtures.PromotionMicrostepTests.collect_body
    bundle = fixtures.PromotionMicrostepTests.bundle
    input_for = fixtures.PromotionMicrostepTests.input_for
    insert = fixtures.PromotionMicrostepTests.insert
    seed = fixtures.PromotionMicrostepTests.seed
    setUp = fixtures.PromotionMicrostepTests.setUp
    request_body = fixtures.PromotionMicrostepTests.request_body
    current_catalog = fixtures.PromotionMicrostepTests.current_catalog
    create_fixed_report = fixtures.PromotionMicrostepTests.create_fixed_report
    actual_job = fixtures.PromotionMicrostepTests.actual_job
    job = fixtures.PromotionMicrostepTests.job
    base = fixtures.PromotionMicrostepTests.base
    prepare_step = fixtures.PromotionMicrostepTests.prepare_step

    def test_new_profile_dispatch_functions_require_explicit_permission(self):
        report = self.create_fixed_report()
        job = self.job(report)
        step = self.prepare_step(job)
        self.assertTrue(business_reports.has_v2_profile(job))
        with self.assertRaises(AiError) as missing:
            business_reports.validate_call(job,
                {"id":"call-1", "name":contract.PACKAGE_TOOL,
                    "arguments":{**self.base(report), "role":"promotion", "offset":0}}, self.admin)
        self.assertEqual(missing.exception.code, "promotion_runtime_not_ready")
        with patch.object(microstep.transport, "catalog", side_effect=self.current_catalog), self.assertRaises(AiError) as denied:
            business_reports.validate_call(job,
                {"id":"call-1", "name":contract.PACKAGE_TOOL,
                    "arguments":{**self.base(report), "role":"promotion", "offset":0}},
                self.admin, promotion_step=step)
        self.assertEqual(denied.exception.code, "promotion_runtime_not_ready")
        with self.assertRaises(AiError) as provider_denied:
            business_reports.validate_provider_turn(job, self.admin, promotion_step=step)
        self.assertEqual(provider_denied.exception.code, "promotion_runtime_not_ready")
        with self.assertRaises(AiError) as output_denied:
            business_reports.validate_output(job, "{}", self.admin, promotion_step=step)
        self.assertEqual(output_denied.exception.code, "promotion_runtime_not_ready")

    def test_agent_tick_closes_before_provider_or_tool_reservation(self):
        report = self.create_fixed_report()
        job = self.job(report)
        with mutation(self.admin):
            m.AiAgentJobs.objects.filter(pk=job.id).update(status="queued", phase="queued",
                lease_token="", lease_expires_at=None, next_run_at=timezone.now())
        before = (m.AiAgentProviderDispatches.objects.filter(job_id=job.id).count(),
            m.AiAgentToolDispatches.objects.filter(job_id=job.id).count())
        with (patch.object(workflows.transport, "catalog", side_effect=self.current_catalog),
                patch("ai_assistant.provider.turn", side_effect=AssertionError("no paid provider")) as paid,
                patch("ai_assistant.transport.execute_tool", side_effect=AssertionError("no edge tool")) as edge,
                patch.object(workflows, "dispatch_budget", side_effect=AssertionError("no quota reservation")) as quota):
            result = workflows.agent_tick(job_id=job.id)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errorCode"], "promotion_runtime_not_ready")
        self.assertEqual(before, (m.AiAgentProviderDispatches.objects.filter(job_id=job.id).count(),
            m.AiAgentToolDispatches.objects.filter(job_id=job.id).count()))
        paid.assert_not_called(); edge.assert_not_called(); quota.assert_not_called()
