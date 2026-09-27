import json
from copy import deepcopy
from unittest.mock import patch
from django.test import TestCase, override_settings
from . import business_reports as reports, business_evidence as evidence, business_diagnosis, workflows, models as m
from .test_business_evidence import BusinessEvidenceTests
from . import tests as fixtures
from .policy import AiError, canonical


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessReportTests(TestCase):
    user = BusinessEvidenceTests.user
    call = BusinessEvidenceTests.call
    execute = BusinessEvidenceTests.execute
    collect = BusinessEvidenceTests.collect

    def setUp(self):
        BusinessEvidenceTests.setUp(self)
        self.evidence_id = evidence.create(self.body, self.admin)["item"]["id"]
        self.collect(self.evidence_id, 1)
        self.collect(self.evidence_id, 2)
        evidence.finish(self.evidence_id, {"expectedVersion": 3, "action": "seal"}, self.admin)
        self.request = {"clientRequestId": "business-report", "evidenceRunId": self.evidence_id, "question": "分析销售并说明缺口", "dryRun": False}
        self.tools = [fixtures.CATALOG[0], *[{**fixtures.CATALOG[0], "name": name} for name in sorted(reports.TOOLS)]]
        row = evidence.analysis_table(self.evidence_id, {"sourceKey": "sales", "dimension": "shop"}, self.admin)["rows"][0]
        self.diagnosis = {"summary": "仅有ERP合成数据", "findings": [{"id": "fact-1", "kind": "observation", "title": "净销售核对",
            "explanation": "根据完整销售记录核对。", "references": [{"sourceKey": "sales", "dimension": "shop", "rowIndex": 0,
            "rowId": row["id"], "metric": "netSalesCents", "field": "value"}]}]}

    def create(self, dry=False):
        with patch("ai_assistant.transport.catalog", return_value=self.tools):
            return reports.create({**self.request, "dryRun": dry}, self.admin)["item"]

    def test_creation_is_owner_bound_idempotent_and_dry_run_uses_no_model(self):
        item = self.create(True)
        self.assertEqual(item["id"], self.create(True)["id"])
        with self.assertRaises(AiError):
            reports.create({**self.request, "dryRun": True}, self.viewer)
        with patch("ai_assistant.provider.turn") as model:
            for _ in range(9):
                workflows.workflow_tick()
            model.assert_not_called()
        flow = m.AiWorkflowRuns.objects.get(pk=item["workflowId"])
        self.assertEqual(flow.status, "completed")
        self.assertEqual(flow.nodes.count() if hasattr(flow, "nodes") else m.AiWorkflowNodeRuns.objects.filter(run=flow).count(), 6)
        self.assertFalse(m.AiAgentJobs.objects.exists())

    def test_references_are_resolved_and_actions_require_observation_and_rollback(self):
        parsed = business_diagnosis.validate(self.diagnosis, self.evidence_id, self.admin)
        self.assertEqual(parsed["findings"][0]["facts"][0]["value"], 120000)
        for field, value in (("rowId", "other"), ("rowIndex", 1), ("metric", "imaginary"), ("field", "changeRate")):
            changed = deepcopy(self.diagnosis)
            changed["findings"][0]["references"][0][field] = value
            with self.assertRaises(AiError):
                business_diagnosis.validate(changed, self.evidence_id, self.admin)
        action = deepcopy(self.diagnosis)
        action["findings"][0]["kind"] = "action"
        with self.assertRaises(AiError):
            business_diagnosis.validate(action, self.evidence_id, self.admin)
        action["findings"][0]["action"] = {"object": "本店", "change": "核对退款", "prerequisites": "确认日期完整",
            "successMetric": "退款率", "observationDays": 14, "rollback": "口径不一致则停止比较", "priority": "high", "ownerRole": "店铺运营", "budgetImpact": "暂不调整投放预算"}
        self.assertEqual(business_diagnosis.validate(action, self.evidence_id, self.admin)["findings"][0]["action"]["observationDays"], 14)

    def test_specialists_and_reviewer_use_real_queue_and_same_evidence(self):
        item = self.create()
        flow = m.AiWorkflowRuns.objects.get(pk=item["workflowId"])
        calls = []
        def turn(model, frames, system, entries):
            self.assertEqual({entry["name"] for entry in entries}, reports.TOOLS)
            job = m.AiAgentJobs.objects.get(status="running")
            calls.append(job.workflow_node_key)
            if not any(frame.get("role") == "tool" for frame in frames):
                args = {"runId": self.evidence_id}
                return {"text": "", "calls": [{"id": "shared", "name": "get_business_analysis_evidence", "arguments": args}],
                    "frame": {"role": "assistant", "content": None, "tool_calls": [{"id": "shared", "type": "function", "function": {"name": "get_business_analysis_evidence", "arguments": canonical(args)}}]}}
            value = ({"approved": True, "conflicts": [], "limitations": ["合成数据不等于业务效果验收"]} if job.workflow_node_key == "independent_review"
                else {"sections": [{"title": title, "body": "合成演练；缺失来源不能推断。"} for title in reports.SECTIONS], "diagnosis": self.diagnosis} if job.workflow_node_key == "report" else self.diagnosis)
            answer = canonical(value)
            return {"text": answer, "calls": [], "frame": {"role": "assistant", "content": answer}}
        def tool(name, args, principal, **kwargs):
            self.assertEqual(args, {"runId": self.evidence_id})
            return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": evidence.mapping(evidence.get_run(self.evidence_id, principal))}
        with patch("ai_assistant.transport.catalog", return_value=self.tools), patch("ai_assistant.provider.turn", side_effect=turn), patch("ai_assistant.transport.execute_tool", side_effect=tool):
            for _ in range(60):
                workflows.workflow_tick()
                workflows.agent_tick()
                flow.refresh_from_db()
                if flow.status in {"waiting_review", "failed"}:
                    break
        self.assertEqual(flow.status, "waiting_review", list(m.AiAgentJobs.objects.values("status", "error_code")))
        self.assertEqual(set(calls), {"commerce", "promotion", "market_b2b", "independent_review", "report"})
        report = m.AiReportRun.objects.select_related("workflow").get(pk=item["id"])
        self.assertTrue(reports.validate_review(report, self.admin)["diagnosis"]["factsVerified"])
        job = m.AiAgentJobs.objects.filter(workflow_run_id=flow.id).first()
        for call in ({"name": "get_data_freshness", "arguments": {}}, {"name": "get_business_analysis_evidence", "arguments": {"runId": "other"}}):
            with self.assertRaises(AiError):
                reports.validate_call(job, call)
        review = m.AiWorkflowNodeRuns.objects.get(run=flow, node_key="human_review")
        workflows.review(flow.id, review.node_key, {"expectedVersion": review.version, "decision": "approve"}, self.admin)
        workflows.workflow_tick()
        flow.refresh_from_db()
        self.assertEqual(flow.status, "completed")
        self.assertEqual(m.AiAgentProviderDispatches.objects.count(), 10)
        self.assertEqual(m.AiAgentToolResults.objects.count(), 5)

    def test_unknown_model_result_is_not_replayed(self):
        item = self.create()
        with patch("ai_assistant.transport.catalog", return_value=self.tools), patch("ai_assistant.provider.turn", side_effect=AiError("unknown", "provider_timeout", 503)) as provider:
            for _ in range(6):
                workflows.workflow_tick()
                workflows.agent_tick()
        self.assertEqual(provider.call_count, 3)
        self.assertEqual(m.AiAgentProviderDispatches.objects.filter(state="unknown").count(), 3)
        self.assertEqual(m.AiAgentProviderDispatches.objects.values("job_id").distinct().count(), 3)
        flow = m.AiWorkflowRuns.objects.get(pk=item["workflowId"])
        self.assertEqual(flow.status, "failed")
        self.assertFalse(flow.retryable)
        with self.assertRaises(AiError):
            workflows.control(flow.id, {"expectedVersion": flow.version}, self.admin, "resume", True)

    def test_failed_specialist_does_not_skip_siblings_waiting_for_capacity(self):
        with patch("ai_assistant.transport.catalog", return_value=self.tools):
            for index in range(7):
                workflows.create({"clientRequestId": f"capacity-{index}", "task": "合成队列占位"}, self.admin)
        item = self.create()
        workflows.workflow_tick()
        jobs = m.AiAgentJobs.objects.filter(workflow_run_id=item["workflowId"])
        self.assertEqual(jobs.count(), 1)
        first = jobs.get()
        with patch("ai_assistant.transport.catalog", side_effect=AiError("offline", "service_unavailable", 503)):
            workflows.agent_tick(job_id=first.id)
        workflows.workflow_tick()
        self.assertEqual(jobs.count(), 2)
        flow = m.AiWorkflowRuns.objects.get(pk=item["workflowId"])
        self.assertEqual(flow.status, "running")
