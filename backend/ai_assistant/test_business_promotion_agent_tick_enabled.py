"""Flagged promotion microsteps with synthetic provider and owning edge only."""
import json
from unittest.mock import patch

from django import test as djtest
from django.utils import timezone

from . import business_promotion_dispatch_tool as dispatch_bridge
from . import business_promotion_runtime_contract as contract
from . import models as m, workflows
from . import test_business_promotion_microstep as fixtures
from . import test_business_screening_diagnosis as answers
from .policy import canonical, mutation


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test",
    AI_PROMOTION_AGENT_RUNTIME_ENABLED=True)
class PromotionEnabledTickTests(djtest.TransactionTestCase):
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
    read = fixtures.PromotionMicrostepTests.read
    append = fixtures.PromotionMicrostepTests.append
    package = fixtures.PromotionMicrostepTests.package

    def queue(self, job):
        with mutation(self.admin):
            m.AiAgentJobs.objects.filter(pk=job.id).update(status="queued", phase="queued",
                lease_token="", lease_expires_at=None, next_run_at=timezone.now())

    def tick(self, job, *, provider_turn=None, edge=None):
        with (patch.object(workflows.transport, "catalog", side_effect=self.current_catalog),
                patch("ai_assistant.provider.turn", side_effect=provider_turn or AssertionError("no provider")) as paid,
                patch("ai_assistant.transport.execute_tool", side_effect=edge or AssertionError("no edge")) as remote,
                patch.object(workflows, "dispatch_budget", return_value=None)):
            result = workflows.agent_tick(job_id=job.id)
        return result, paid, remote

    def test_first_package_call_uses_dispatch_primary_key_and_owning_edge(self):
        report = self.create_fixed_report()
        job = self.job(report)
        self.queue(job)
        args = {**self.base(report), "role":"promotion", "offset":0}
        def provider_result(*_):
            call = {"id":"package-call-1", "name":contract.PACKAGE_TOOL,
                "arguments":args}
            return {"text":"", "calls":[call],
                "frame":{"role":"assistant", "content":"", "tool_calls":[call]}}
        first, paid, edge = self.tick(job, provider_turn=provider_result)
        self.assertEqual(first["status"], "checkpoint")
        self.assertEqual(m.AiAgentProviderDispatches.objects.filter(job_id=job.id,
            state="succeeded").count(), 1)
        self.assertFalse(m.AiAgentToolDispatches.objects.filter(job_id=job.id).exists())
        paid.assert_called_once(); edge.assert_not_called()
        seen = []
        def owning_edge(name, arguments, principal, *, surface, request_id,
                provider_call_id, policy_digest):
            self.assertEqual(surface, contract.SURFACE)
            saved = m.AiAgentToolDispatches.objects.get(pk=request_id)
            self.assertEqual(saved.tool_name, name)
            seen.append(saved.id)
            result = dispatch_bridge.read(request_id, name, arguments,
                provider_call_id, principal)
            return {"toolName":name, "ok":True, "auditStatus":"recorded", "data":result}
        second, paid2, remote2 = self.tick(job, edge=owning_edge)
        self.assertEqual(second["status"], "checkpoint")
        self.assertEqual(len(seen), 1)
        self.assertEqual(m.AiAgentToolDispatches.objects.get(pk=seen[0]).state, "succeeded")
        self.assertEqual(m.AiAgentToolResults.objects.filter(tool_dispatch_id=seen[0]).count(), 1)
        paid2.assert_not_called(); remote2.assert_called_once()

    def test_known_final_is_saved_before_validation_and_can_complete(self):
        report = self.create_fixed_report()
        job = self.job(report)
        self.package(report, job)
        self.queue(job)
        answer = canonical(answers.answer("promotion"))
        def final(*_):
            return {"text":answer, "calls":[], "frame":{"role":"assistant", "content":answer}}
        result, paid, edge = self.tick(job, provider_turn=final)
        self.assertEqual(result["status"], "completed")
        paid.assert_called_once(); edge.assert_not_called()
        saved = m.AiAgentProviderDispatches.objects.filter(job_id=job.id).order_by(
            "-dispatch_ordinal").first()
        self.assertEqual(saved.state, "succeeded")
        self.assertEqual(json.loads(m.AiAgentProviderResults.objects.get(dispatch=saved).response_json)["text"], answer)
        self.assertEqual(m.AiAgentJobs.objects.get(pk=job.id).status, "completed")

    def test_parallel_sibling_completion_during_known_provider_call_keeps_result(self):
        report = self.create_fixed_report()
        job = self.job(report, role="promotion")
        sibling = self.job(report, role="commerce")
        self.package(report, job)
        self.queue(job)
        answer = canonical(answers.answer("promotion"))
        def sibling_finishes(*_):
            output = canonical({"answer":canonical(answers.answer("commerce"))})
            with mutation(self.admin):
                m.AiAgentJobs.objects.filter(pk=sibling.id).update(status="completed",
                    phase="completed", output_json=output)
                node = m.AiWorkflowNodeRuns.objects.get(run_id=report.workflow_id,
                    node_key="commerce")
                node.status = "completed"
                node.output_json = output
                node.version += 1
                node.save()
                flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
                flow.status = "running"
                flow.version += 1
                flow.save()
            return {"text":answer, "calls":[], "frame":{"role":"assistant", "content":answer}}
        result, paid, edge = self.tick(job, provider_turn=sibling_finishes)
        self.assertEqual(result["status"], "completed")
        paid.assert_called_once(); edge.assert_not_called()
        provider = m.AiAgentProviderDispatches.objects.filter(job_id=job.id).order_by(
            "-dispatch_ordinal").first()
        self.assertEqual(provider.state, "succeeded")
        self.assertTrue(m.AiAgentProviderResults.objects.filter(dispatch=provider).exists())
        self.assertEqual(m.AiAgentJobs.objects.get(pk=job.id).status, "completed")

    def test_invalid_known_final_retains_receipt_and_unknown_provider_never_replays(self):
        report = self.create_fixed_report()
        job = self.job(report)
        self.queue(job)
        answer = canonical(answers.answer("promotion"))
        result, _, _ = self.tick(job, provider_turn=lambda *_: {"text":answer,
            "calls":[], "frame":{"role":"assistant", "content":answer}})
        self.assertEqual(result["status"], "failed")
        saved = m.AiAgentProviderDispatches.objects.get(job_id=job.id)
        self.assertEqual(saved.state, "succeeded")
        self.assertTrue(m.AiAgentProviderResults.objects.filter(dispatch=saved).exists())
        other = self.create_fixed_report()
        uncertain = self.job(other)
        self.queue(uncertain)
        failed, paid, edge = self.tick(uncertain,
            provider_turn=RuntimeError("synthetic uncertain provider result"))
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(m.AiAgentProviderDispatches.objects.get(job_id=uncertain.id).state, "unknown")
        self.assertFalse(m.AiAgentProviderResults.objects.filter(dispatch__job_id=uncertain.id).exists())
        self.assertEqual(m.AiAgentJobs.objects.get(pk=uncertain.id).retryable, 0)
        edge.assert_not_called()

    def test_unknown_tool_and_late_revocation_fail_closed(self):
        report = self.create_fixed_report()
        job = self.job(report)
        self.queue(job)
        args = {**self.base(report), "role":"promotion", "offset":0}
        call = {"id":"unknown-tool-call", "name":contract.PACKAGE_TOOL, "arguments":args}
        self.tick(job, provider_turn=lambda *_: {"text":"", "calls":[call],
            "frame":{"role":"assistant", "content":"", "tool_calls":[call]}})
        failed, _, edge = self.tick(job, edge=RuntimeError("synthetic uncertain tool result"))
        self.assertEqual(failed["status"], "failed")
        saved = m.AiAgentToolDispatches.objects.get(job_id=job.id)
        self.assertEqual(saved.state, "unknown")
        self.assertFalse(m.AiAgentToolResults.objects.filter(tool_dispatch=saved).exists())
        other = self.create_fixed_report()
        revoked = self.job(other)
        self.queue(revoked)
        from access_control.models import AppUser
        def late(*_):
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return {"text":"", "calls":[], "frame":{"role":"assistant", "content":"late"}}
        late_result, _, _ = self.tick(revoked, provider_turn=late)
        self.assertEqual(late_result["status"], "failed")
        dispatch = m.AiAgentProviderDispatches.objects.get(job_id=revoked.id)
        self.assertEqual(dispatch.state, "unknown")
