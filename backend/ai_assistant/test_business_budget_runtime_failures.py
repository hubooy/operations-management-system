"""Real new-profile runtime failure fences; synthetic provider/network only."""
import json
from unittest.mock import patch

from access_control.models import AppUser
from django.test import TransactionTestCase, override_settings

from . import business_budget, business_budget_store as store, business_evidence as evidence
from . import business_reports as reports, models as m, workflows
from .policy import AiError, canonical
from . import test_business_budget_reports as fixtures


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessBudgetRuntimeFailureTests(TransactionTestCase):
    user = fixtures.BusinessBudgetReportTests.user
    call = fixtures.BusinessBudgetReportTests.call
    create = fixtures.BusinessBudgetReportTests.create

    def setUp(self):
        fixtures.BusinessBudgetReportTests.setUp(self)
        item = self.create()["item"]
        self.report = m.AiReportRun.objects.get(pk=item["id"])
        self.flow_id = item["workflowId"]
        # The shared fixture also has an inert dry workflow; advance using the
        # real oldest-first scheduler until our independent jobs are created.
        for _ in range(20):
            if m.AiAgentJobs.objects.filter(workflow_run_id=self.flow_id).exists():
                break
            workflows.workflow_tick()
        self.jobs = {job.workflow_node_key: job for job in m.AiAgentJobs.objects.filter(workflow_run_id=self.flow_id)}
        self.assertEqual(set(self.jobs), {"commerce", "promotion", "market_b2b"})

    def read_directory(self, job):
        args = {"runId": self.run_id, "offset": 0}
        name = reports.BUDGET_DIRECTORY_TOOL
        call = {"id": "directory-call", "name": name, "arguments": args}
        reply = {"text": "", "calls": [call], "frame": {"role": "assistant", "content": None,
            "tool_calls": [{"id": call["id"], "type": "function", "function": {"name": name, "arguments": canonical(args)}}]}}
        def execute(tool_name, arguments, principal, **kwargs):
            self.assertEqual((tool_name, arguments, kwargs["surface"]), (name, args, reports.BUDGET_SURFACE))
            page = evidence.directory(self.run_id, {"offset": "0", "limit": "20"}, principal)
            self.assertIsNone(page["nextOffset"])
            return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": page}
        with patch("ai_assistant.transport.catalog", return_value=self.tools), patch("ai_assistant.provider.turn", return_value=reply) as provider, patch("ai_assistant.transport.execute_tool", side_effect=execute) as transport:
            self.assertEqual(workflows.agent_tick(job_id=job.id)["status"], "checkpoint")
            self.assertEqual(workflows.agent_tick(job_id=job.id)["status"], "checkpoint")
            provider.assert_called_once(); transport.assert_called_once()
        self.assertEqual(m.AiAgentToolResults.objects.filter(tool_dispatch__job_id=job.id).count(), 1)

    def final_without_required_proof(self, job):
        answer = canonical({"summary": "合成提前结束，不能交付。", "findings": []})
        reply = {"text": answer, "calls": [], "frame": {"role": "assistant", "content": answer}}
        with patch("ai_assistant.transport.catalog", return_value=self.tools), patch("ai_assistant.provider.turn", return_value=reply) as provider, patch("ai_assistant.transport.execute_tool") as transport:
            outcome = workflows.agent_tick(job_id=job.id)
            self.assertEqual(outcome["status"], "failed", outcome)
            job.refresh_from_db()
            self.assertEqual(job.error_code, "budget_read_incomplete")
            self.assertFalse(job.retryable)
            dispatch = m.AiAgentProviderDispatches.objects.filter(job_id=job.id).order_by("dispatch_ordinal").last()
            self.assertEqual(dispatch.state, "succeeded")
            saved = m.AiAgentProviderResults.objects.get(dispatch_id=dispatch.id)
            self.assertEqual(json.loads(saved.response_json)["text"], answer)
            self.assertEqual(workflows.agent_tick(job_id=job.id)["status"], "idle")
            provider.assert_called_once(); transport.assert_not_called()
        self.assertFalse(m.AiAgentProviderDispatches.objects.filter(job_id=job.id, state="unknown").exists())
        with self.assertRaises(AiError):
            workflows.control(job.id, {"expectedVersion": job.version}, self.admin, "resume")
        saved.refresh_from_db()
        self.assertEqual(json.loads(saved.response_json)["text"], answer)

    def test_promotion_early_final_after_directory_keeps_known_result_without_budget_or_replay(self):
        promotion = self.jobs["promotion"]
        self.read_directory(promotion)
        self.final_without_required_proof(promotion)
        self.assertEqual(m.AiAgentProviderResults.objects.filter(dispatch__job_id=promotion.id).count(), 2)
        self.assertFalse(m.AiAgentToolDispatches.objects.filter(job_id=promotion.id, tool_name=reports.BUDGET_REFERENCE_TOOL).exists())

    def test_sibling_directory_receipt_does_not_authorize_another_agent_final(self):
        commerce, promotion = self.jobs["commerce"], self.jobs["promotion"]
        self.read_directory(commerce)
        receipt = m.AiAgentToolResults.objects.get(tool_dispatch__job_id=commerce.id)
        original = (receipt.result_json, receipt.result_digest)
        self.final_without_required_proof(promotion)
        self.assertFalse(m.AiAgentToolDispatches.objects.filter(job_id=promotion.id).exists())
        receipt.refresh_from_db()
        self.assertEqual((receipt.result_json, receipt.result_digest), original)
        commerce.refresh_from_db()
        self.assertNotEqual(commerce.status, "failed")

    def test_revoked_owner_stops_after_saved_receipt_before_any_new_external_call(self):
        promotion = self.jobs["promotion"]
        self.read_directory(promotion)
        before = (m.AiAgentProviderResults.objects.filter(dispatch__job_id=promotion.id).count(),
                  m.AiAgentToolResults.objects.filter(tool_dispatch__job_id=promotion.id).count())
        AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with patch("ai_assistant.provider.turn") as provider, patch("ai_assistant.transport.execute_tool") as transport, patch("ai_assistant.transport.catalog") as catalog:
            result = workflows.agent_tick(job_id=promotion.id)
            self.assertEqual(result["status"], "failed", result)
            provider.assert_not_called(); transport.assert_not_called(); catalog.assert_not_called()
        promotion.refresh_from_db()
        self.assertEqual(promotion.error_code, "owner_authorization_changed")
        self.assertFalse(promotion.retryable)
        self.assertEqual((m.AiAgentProviderResults.objects.filter(dispatch__job_id=promotion.id).count(),
            m.AiAgentToolResults.objects.filter(tool_dispatch__job_id=promotion.id).count()), before)
        with self.assertRaises(AiError):
            store.page(self.report, self.admin, limit=20)

    def test_missing_parameter_read_fails_closed_without_legacy_budget_fallback(self):
        # DB FK/immutability forbids actual removal. Simulate storage corruption
        # at this one lookup, keeping the real report and all service guards.
        with patch.object(m.AiBusinessBudgetPlan.objects, "filter") as lookup, patch("ai_assistant.business_budget.resolve") as calculate:
            lookup.return_value.first.return_value = None
            with self.assertRaisesMessage(AiError, "固定预算参数缺失"):
                business_budget.for_report(self.report, self.admin)
            calculate.assert_not_called()
        self.assertIsNotNone(m.AiBusinessBudgetPlan.objects.filter(pk=self.report.budget_plan_id).first())
