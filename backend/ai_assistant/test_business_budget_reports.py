"""Real sealed budget -> five Agent ledgers -> review -> persistent files.

Only provider/network boundaries are simulated; no paid model or live data.
"""
import base64
from copy import deepcopy
import hashlib
import json
from threading import Barrier, Lock
from unittest.mock import patch

from django.db import connection
from django.test import TransactionTestCase, override_settings
from sales.tests.factories import signed_headers, TEST_SECRET
from business_analysis import volume_delivery
from . import business_budget_store as store, business_evidence as evidence, business_reports as reports
from . import business_parallel, business_files, business_volume_files, models as m, workflows, tests as fixtures
from .test_business_budget_store import seed_fixed_report
from .policy import AiError, canonical


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessBudgetReportTests(TransactionTestCase):
    user = fixtures.AiDomainTests.user
    call = fixtures.AiDomainTests.call

    def setUp(self):
        from netshop.models import NetshopDataRevision
        NetshopDataRevision.objects.get_or_create(domain="netshop", defaults={"revision": 0, "source_digest": "0"*64})
        fixtures.AiDomainTests.setUp(self)
        self.admin = self.user("budget-execution@example.invalid", "admin", None)
        self.seed, self.prepared = seed_fixed_report(self.admin)
        self.run_id = self.prepared.binding["evidenceRunId"]
        self.body = {"clientRequestId": "budget-execution", "evidenceRunId": self.run_id,
            "question": "合成固定预算验证", "dryRun": False, "budgetPlan": self.prepared.plan}
        self.tools = [{**deepcopy(fixtures.CATALOG[0]), "name": name,
            "execution": {**deepcopy(fixtures.CATALOG[0]["execution"]), "allowedSurfaces": [reports.BUDGET_SURFACE], "maxCallsPerRequest": 8}}
            for name in sorted(reports.BUDGET_TOOLS)]

    def create(self, **values):
        with patch("ai_assistant.transport.catalog", return_value=self.tools):
            return reports.create({**self.body, **values}, self.admin)

    def test_atomic_reference_admission_replay_and_new_version(self):
        item = self.create()["item"]
        report = m.AiReportRun.objects.get(pk=item["id"])
        snapshot = json.loads(report.snapshot_json)
        self.assertEqual(snapshot["executionProfile"], reports.BUDGET_PROFILE)
        self.assertNotIn("budgetPlan", snapshot)
        self.assertEqual(reports.bound_reference(snapshot, self.admin)["budgetRef"], store.load(report, self.admin).reference)
        counts = (m.AiBusinessBudgetPlan.objects.count(), m.AiWorkflowRuns.objects.count())
        with patch("ai_assistant.transport.catalog", side_effect=AssertionError("no readmission")):
            self.assertTrue(reports.create(self.body, self.admin)["replayed"])
        self.assertEqual(counts, (m.AiBusinessBudgetPlan.objects.count(), m.AiWorkflowRuns.objects.count()))
        changed = self.prepared.plan; changed["totalBudgetCents"] = 15000
        with self.assertRaises(AiError): self.create(budgetPlan=changed)
        newer = self.create(clientRequestId="budget-version-2", previousReportId=report.id, budgetPlan=changed)["item"]
        next_report = m.AiReportRun.objects.get(pk=newer["id"])
        self.assertNotEqual(report.budget_plan_id, next_report.budget_plan_id)
        self.assertEqual(store.load(report, self.admin).plan["totalBudgetCents"], 10000)
        self.assertEqual(store.load(next_report, self.admin).plan["totalBudgetCents"], 15000)
        self.assertEqual(json.loads(next_report.snapshot_json)["previousReportId"], report.id)

    def test_capacity_and_insert_failures_leave_no_orphan_or_dispatch(self):
        counts = (m.AiBusinessBudgetPlan.objects.count(), m.AiWorkflowRuns.objects.count())
        self.model.max_tool_rounds = 2; self.model.save(update_fields=["max_tool_rounds"])
        with patch("ai_assistant.provider.turn") as provider, self.assertRaises(AiError): self.create()
        provider.assert_not_called()
        self.assertEqual(counts, (m.AiBusinessBudgetPlan.objects.count(), m.AiWorkflowRuns.objects.count()))
        self.model.max_tool_rounds = 6; self.model.save(update_fields=["max_tool_rounds"])
        with patch.object(m.AiReportRun.objects, "create", side_effect=AiError("synthetic report insertion failure")), self.assertRaises(AiError): self.create()
        self.assertEqual(counts, (m.AiBusinessBudgetPlan.objects.count(), m.AiWorkflowRuns.objects.count()))
        self.assertFalse(m.AiAgentProviderDispatches.objects.exists())

    def test_budget_reference_route_is_exact_read_only_and_owner_bound(self):
        item = self.create()["item"]
        path = f"/api/ai/reports/{item['id']}/budget-reference"
        def get(query, actor=None):
            actor = actor or self.admin
            url = path+query
            with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}), override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET, DJANGO_PROCESS_ROLE="ai_reader"), patch("ai_assistant.views.authority"):
                return self.client.get(url, headers=signed_headers(url, email=actor.email, role=actor.role))
        response = get(f"?runId={self.run_id}&offset=0&limit=20")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertLessEqual(len(response.content), 38000)
        for query in ("", "?runId=other", f"?runId={self.run_id}&offset=01", f"?runId={self.run_id}&offset=100",
                      f"?runId={self.run_id}&limit=1", f"?runId={self.run_id}&extra=1"):
            self.assertGreaterEqual(get(query).status_code, 400)
        self.assertGreaterEqual(get(f"?runId={self.run_id}", self.viewer).status_code, 400)
        self.assertGreaterEqual(self.call(path, principal=self.admin).status_code, 400)

    def test_five_agents_own_receipts_review_and_persistent_budget_files(self):
        from . import business_budget_builder
        choices = business_budget_builder.targets(self.run_id, {"sourceKey": "ads", "dimension": "sku"}, self.admin)
        selected_plan = self.prepared.plan
        for target, choice in zip(selected_plan["targets"], choices["rows"]):
            target.update(rowId=choice["id"], rowIndex=choice["rowIndex"])
        trial = business_budget_builder.preview(self.run_id, {"evidenceBinding": choices["evidenceBinding"], "budgetPlan": selected_plan}, self.admin)
        self.assertTrue(trial["previewOnly"])
        self.body["budgetPlan"] = trial["budget"]["plan"]
        item = self.create()["item"]
        report = m.AiReportRun.objects.get(pk=item["id"])
        flow = m.AiWorkflowRuns.objects.get(pk=item["workflowId"])
        args = {"sourceKey": "ads", "dimension": "sku"}
        actual = evidence.analysis_table(self.run_id, args, self.admin)["rows"][0]
        diagnosis = {"summary": "合成预算分析，预测依赖假设。", "findings": [{"id": "spend", "kind": "observation",
            "title": "推广支出核验", "explanation": "推广归因金额不等于店铺真实销售额。",
            "references": [{**args, "rowIndex": 0, "rowId": actual["id"], "metric": "spendCents", "field": "value"}]}]}
        barrier, mutex = Barrier(3, timeout=15), Lock()
        stages, roles, active, peak = {}, set(), 0, 0
        def turn(model, frames, system, entries):
            nonlocal active, peak
            self.assertFalse(connection.in_atomic_block)
            job = next(job for job in m.AiAgentJobs.objects.filter(workflow_run_id=flow.id, status="running")
                if frames[0]["content"].startswith(job.task+"\n<task_input>"))
            with mutex:
                stage = stages.get(job.id, 0); stages[job.id] = stage+1; roles.add(job.workflow_node_key)
            required = job.workflow_node_key in {"promotion", "independent_review", "report"}
            if stage == 0:
                if job.workflow_node_key in {"commerce", "promotion", "market_b2b"}:
                    with mutex: active += 1; peak = max(peak, active)
                    barrier.wait()
                    with mutex: active -= 1
                name, arguments = reports.BUDGET_DIRECTORY_TOOL, {"runId": self.run_id, "offset": 0}
            elif stage == 1 and required:
                name, arguments = reports.BUDGET_REFERENCE_TOOL, {"runId": self.run_id, "reportId": report.id, "offset": 0}
            elif stage == (2 if required else 1):
                name, arguments = reports.BUDGET_TABLE_TOOL, {"runId": self.run_id, **args}
            else:
                value = {"approved": True, "conflicts": [], "limitations": ["仅合成数据"]} if job.workflow_node_key == "independent_review" else (
                    {"sections": [{"title": title, "body": "以固定参数生成条件预算；真实经营效果待验收。"} for title in reports.SECTIONS], "diagnosis": diagnosis}
                    if job.workflow_node_key == "report" else diagnosis)
                text = canonical(value)
                return {"text": text, "calls": [], "frame": {"role": "assistant", "content": text}}
            call = {"id": f"call-{stage}", "name": name, "arguments": arguments}
            return {"text": "", "calls": [call], "frame": {"role": "assistant", "content": None,
                "tool_calls": [{"id": call["id"], "type": "function", "function": {"name": name, "arguments": canonical(arguments)}}]}}
        def execute(name, arguments, principal, **kwargs):
            self.assertEqual(kwargs["surface"], reports.BUDGET_SURFACE)
            if name == reports.BUDGET_DIRECTORY_TOOL:
                data = evidence.directory(self.run_id, {"offset": str(arguments["offset"]), "limit": "20"}, principal)
            elif name == reports.BUDGET_REFERENCE_TOOL:
                data = store.page(report, principal, offset=arguments["offset"], limit=20)
            else:
                data = evidence.analysis_table(self.run_id, {k: str(v) for k,v in arguments.items() if k != "runId"}, principal)
            return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": data}
        with patch("ai_assistant.transport.catalog", return_value=self.tools), patch("ai_assistant.provider.turn", side_effect=turn), patch("ai_assistant.transport.execute_tool", side_effect=execute):
            for _ in range(60):
                workflows.workflow_tick(); business_parallel.agent_queue_tick(); flow.refresh_from_db()
                if flow.status in {"waiting_review", "failed", "paused"}: break
        self.assertEqual(flow.status, "waiting_review", list(m.AiAgentJobs.objects.values("workflow_node_key", "status", "error_code")))
        self.assertEqual((len(roles), peak), (5, 3))
        self.assertEqual(m.AiAgentToolResults.objects.filter(tool_dispatch__job__workflow_run_id=flow.id,
            tool_dispatch__tool_name=reports.BUDGET_REFERENCE_TOOL).count(), 3)
        report.refresh_from_db(); reports.validate_review(report, self.admin)
        node = m.AiWorkflowNodeRuns.objects.get(run=flow, node_key="human_review")
        workflows.review(flow.id, node.node_key, {"expectedVersion": node.version, "decision": "approve"}, self.admin)
        workflows.workflow_tick(); report.refresh_from_db()
        self.assertEqual(report.workflow.status, "completed")
        file_id = business_files.create(report.id, {"deliveryMode": "volumes", "expectedPrincipalKey": evidence.principal_key(self.admin)}, self.admin)["item"]["id"]
        with patch("ai_assistant.provider.turn") as provider, patch("ai_assistant.transport.execute_tool") as transport:
            result = business_files.tick()
            self.assertEqual(result["status"], "ready", result)
            provider.assert_not_called(); transport.assert_not_called()
        saved = business_files.get(file_id, self.admin); compact = json.loads(saved.manifest_json)
        downloaded = {}
        for descriptor in [*compact["files"], compact["manifestFile"]]:
            raw = b"".join(base64.b64decode(business_volume_files.chunk(file_id, str(descriptor["volumeIndex"]), descriptor["format"],
                {"sequence": str(i)}, self.admin)["base64"]) for i in range(1, descriptor["chunkCount"]+1))
            self.assertEqual(hashlib.sha256(raw).hexdigest(), descriptor["sha256"])
            downloaded[descriptor["volumeIndex"], descriptor["format"]] = raw
        complete = volume_delivery.verify_full(compact, downloaded[0,"json"], binding_digest=saved.binding_digest,
            attempt=saved.attempt, draft=False, report_id=report.id,
            evidence_digest=self.prepared.binding["sealedDigest"],
            renderer_version=saved.renderer_version)
        self.assertEqual(complete["budgetPlanDigest"], self.prepared.reference["planDigest"])
        self.assertEqual(store.load(report, self.admin).result["allocation"]["allocatedCents"], 9000)
        self.assertFalse(m.AiAgentProviderDispatches.objects.filter(state="unknown").exists())
