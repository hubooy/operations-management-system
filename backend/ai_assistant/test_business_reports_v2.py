"""V2 workflow admission and known-result fencing; no real model dispatch."""
import json
from copy import deepcopy
from unittest.mock import patch
from django.test import TestCase, override_settings
from . import business_reports as reports, business_evidence as evidence, business_files, workflows, models as m
from . import test_business_evidence_v2 as evidence_fixtures
from . import tests as fixtures
from .policy import AiError, canonical, digest, uid


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessReportV2Tests(TestCase):
    user = evidence_fixtures.BusinessEvidenceV2Tests.user
    call = evidence_fixtures.BusinessEvidenceV2Tests.call
    request_sources = evidence_fixtures.BusinessEvidenceV2Tests.request
    execute = evidence_fixtures.BusinessEvidenceV2Tests.execute
    collect = evidence_fixtures.BusinessEvidenceV2Tests.collect

    def setUp(self):
        evidence_fixtures.BusinessEvidenceV2Tests.setUp(self)
        self.evidence_id = evidence.create(self.request_sources(), self.admin)["item"]["id"]
        self.collect(self.evidence_id)
        evidence.finish(self.evidence_id, {"expectedVersion": 2, "action": "seal"}, self.admin)
        self.body_report = {"clientRequestId": "report-v2", "evidenceRunId": self.evidence_id, "question": "分析范围与销售", "dryRun": False}
        self.tools = [{**deepcopy(fixtures.CATALOG[0]), "name": name,
            "execution": {**deepcopy(fixtures.CATALOG[0]["execution"]), "allowedSurfaces": [reports.V2_SURFACE], "maxCallsPerRequest": 8}}
            for name in sorted(reports.V2_TOOLS)]

    def create(self, *, dry=False, **values):
        with patch("ai_assistant.transport.catalog", return_value=self.tools):
            return reports.create({**self.body_report, "dryRun": dry, **values}, self.admin)

    def test_profile_admission_binds_light_reference_and_exact_surface(self):
        with patch("ai_assistant.transport.catalog", return_value=self.tools) as catalog, patch("ai_assistant.provider.turn") as provider:
            item = reports.create(self.body_report, self.admin)["item"]
            catalog.assert_called_once_with(self.admin, reports.V2_SURFACE)
            provider.assert_not_called()
        report = m.AiReportRun.objects.get(pk=item["id"])
        flow = m.AiWorkflowRuns.objects.get(pk=item["workflowId"])
        reference, snapshot = json.loads(flow.input_json), json.loads(report.snapshot_json)
        self.assertNotIn("sources", reference)
        self.assertLessEqual(len(flow.input_json.encode()), 8000)
        self.assertEqual(snapshot["executionProfile"], reports.V2_PROFILE)
        self.assertEqual(snapshot["evidenceProtocol"], "reference-v2")
        self.assertEqual(flow.tool_policy_digest, digest(self.tools))
        for key in ("evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest", "sourceCount"):
            self.assertEqual(reference[key], snapshot[key])
        with patch("ai_assistant.transport.catalog", side_effect=AssertionError("replay cannot re-admit")):
            self.assertTrue(reports.create(self.body_report, self.admin)["replayed"])
        for field in ("executionProfile", "executionSurface", "surface"):
            with self.assertRaises(AiError):
                workflows.create({"clientRequestId": uid("forged"), "task": "不能选择内部执行身份", field: reports.V2_PROFILE}, self.admin)
        self.assertEqual(m.AiWorkflowRuns.objects.count(), 1)

    def test_report_insert_failure_rolls_back_workflow_guidance_and_nodes(self):
        with patch.object(m.AiReportRun.objects, "create", side_effect=AiError("synthetic report failure")), self.assertRaises(AiError):
            self.create()
        self.assertFalse(m.AiWorkflowRuns.objects.exists())
        self.assertFalse(m.AiWorkflowNodeRuns.objects.exists())
        self.assertFalse(m.AiExecutionGuidance.objects.exists())

    def test_invalid_budget_unbound_previous_and_legacy_file_mode_rejected(self):
        for values in ({"budgetPlan": {}}, {"previousReportId": "previous"}):
            with self.assertRaises(AiError):
                self.create(**values)
        self.assertFalse(m.AiWorkflowRuns.objects.exists())
        item = self.create()["item"]
        with self.assertRaisesMessage(AiError, "v2证据文件交付尚未接入"):
            business_files.create(item["id"], {"draft": True}, self.admin)
        self.assertFalse(m.AiBusinessFileRun.objects.exists())

    def test_dry_run_has_no_model_or_catalog_dispatch(self):
        with patch("ai_assistant.transport.catalog") as catalog, patch("ai_assistant.provider.turn") as provider:
            item = reports.create({**self.body_report, "dryRun": True}, self.admin)["item"]
            for _ in range(9):
                workflows.workflow_tick()
            catalog.assert_not_called()
            provider.assert_not_called()
        self.assertEqual(m.AiWorkflowRuns.objects.get(pk=item["workflowId"]).status, "completed")
        self.assertFalse(m.AiAgentJobs.objects.exists())

    def test_runtime_snapshot_binding_and_directory_first_are_enforced(self):
        item = self.create()["item"]
        workflows.workflow_tick()
        job = m.AiAgentJobs.objects.filter(workflow_run_id=item["workflowId"]).first()
        self.assertEqual(reports.execution_surface(job, self.admin), reports.V2_SURFACE)
        with self.assertRaises(AiError):
            reports.validate_call(job, {"name": reports.V2_TABLE_TOOL,
                "arguments": {"runId": self.evidence_id, "sourceKey": "sales", "dimension": "shop"}})
        reports.validate_call(job, {"name": reports.V2_DIRECTORY_TOOL, "arguments": {"runId": self.evidence_id}})
        for args in ({"runId": self.evidence_id, "offset": True}, {"runId": self.evidence_id, "offset": 1},
                     {"runId": "other", "offset": 0}, {"runId": self.evidence_id, "limit": 20}):
            with self.assertRaises(AiError):
                reports.validate_call(job, {"name": reports.V2_DIRECTORY_TOOL, "arguments": args})
        original = job.input_json
        for field, value in (("sealedDigest", "b"*64), ("sourceCount", True)):
            changed = json.loads(original)
            changed["workflowInput"][field] = value
            job.input_json = canonical(changed)
            with self.assertRaises(AiError):
                reports.execution_surface(job, self.admin)
        job.input_json = original
        snapshot = reports.context(job)
        for field, value in (("evidenceVersion", True), ("catalogDigest", "c"*64), ("sourceCount", 2)):
            with self.assertRaises(AiError):
                reports.bound_reference({**snapshot, field: value}, self.admin)

    def test_early_known_answer_is_saved_and_failed_without_unknown_or_replay(self):
        item = self.create()["item"]
        workflows.workflow_tick()
        job = m.AiAgentJobs.objects.filter(workflow_run_id=item["workflowId"]).first()
        answer = canonical({"summary": "未读目录不得交付", "findings": []})
        reply = {"text": answer, "calls": [], "frame": {"role": "assistant", "content": answer}}
        with patch("ai_assistant.transport.catalog", return_value=self.tools), patch("ai_assistant.provider.turn", return_value=reply) as model:
            result = workflows.agent_tick(job_id=job.id)
            self.assertEqual(result["status"], "failed")
            job.refresh_from_db()
            self.assertEqual(job.error_code, "conflict")
            self.assertFalse(job.retryable)
            dispatch = m.AiAgentProviderDispatches.objects.get(job_id=job.id)
            self.assertEqual(dispatch.state, "succeeded")
            self.assertEqual(json.loads(m.AiAgentProviderResults.objects.get(dispatch_id=dispatch.id).response_json)["text"], answer)
            self.assertEqual(workflows.agent_tick(job_id=job.id)["status"], "idle")
            model.assert_called_once()
        with self.assertRaises(AiError):
            workflows.control(job.id, {"expectedVersion": job.version}, self.admin, "resume")

    def test_legacy_admission_digest_is_unchanged(self):
        legacy_catalog = deepcopy(fixtures.CATALOG)
        body = {"clientRequestId": "old-profile", "task": "原普通Agent", "input": {"inputMode": "reference-v2"}}
        with patch("ai_assistant.transport.catalog", return_value=legacy_catalog) as catalog:
            item = workflows.create(body, self.admin)["item"]
        catalog.assert_called_once_with(self.admin, "ai_agent")
        row = m.AiAgentJobs.objects.get(pk=item["id"])
        admitted = {"model_id": row.model_id, "model_version": row.model_version,
            "allowed_tools_json": canonical([e["name"] for e in legacy_catalog]), "tool_policy_digest": digest(legacy_catalog)}
        self.assertEqual(row.request_digest, digest({"payload": body, "admission": admitted}))
        self.assertEqual(reports.execution_surface(row, self.admin), "ai_agent")

    def test_local_output_validation_exception_keeps_known_provider_receipt(self):
        item = self.create()["item"]
        workflows.workflow_tick()
        job = m.AiAgentJobs.objects.filter(workflow_run_id=item["workflowId"]).first()
        reply = {"text": "{}", "calls": [], "frame": {"role": "assistant", "content": "{}"}}
        with patch("ai_assistant.transport.catalog", return_value=self.tools), patch("ai_assistant.provider.turn", return_value=reply), patch("ai_assistant.business_reports.validate_output", side_effect=ValueError("synthetic decoder error")):
            result = workflows.agent_tick(job_id=job.id)
        self.assertEqual(result["errorCode"], "business_output_validation_failed")
        self.assertEqual(m.AiAgentProviderDispatches.objects.get(job_id=job.id).state, "succeeded")
        self.assertEqual(m.AiAgentProviderResults.objects.filter(dispatch__job_id=job.id).count(), 1)

    def test_bad_saved_directory_is_rejected_before_next_model_dispatch(self):
        item = self.create()["item"]
        workflows.workflow_tick()
        job = m.AiAgentJobs.objects.filter(workflow_run_id=item["workflowId"]).first()
        args = {"runId": self.evidence_id, "offset": 0}
        reply = {"text": "", "calls": [{"id": "directory", "name": reports.V2_DIRECTORY_TOOL, "arguments": args}],
            "frame": {"role": "assistant", "content": None, "tool_calls": [{"id": "directory", "type": "function",
                "function": {"name": reports.V2_DIRECTORY_TOOL, "arguments": canonical(args)}}]}}
        page = evidence.directory(self.evidence_id, {"offset": "0", "limit": "20"}, self.admin)
        page["catalogDigest"] = "e"*64
        bad = {"toolName": reports.V2_DIRECTORY_TOOL, "ok": True, "auditStatus": "recorded", "data": page}
        with patch("ai_assistant.transport.catalog", return_value=self.tools), patch("ai_assistant.provider.turn", return_value=reply) as provider, patch("ai_assistant.transport.execute_tool", return_value=bad):
            self.assertEqual(workflows.agent_tick(job_id=job.id)["status"], "checkpoint")
            self.assertEqual(workflows.agent_tick(job_id=job.id)["status"], "checkpoint")
            saved = m.AiAgentToolResults.objects.get(tool_dispatch__job_id=job.id)
            self.assertEqual(json.loads(saved.result_json), bad)
            self.assertEqual(workflows.agent_tick(job_id=job.id)["status"], "failed")
            provider.assert_called_once()
        self.assertEqual(m.AiAgentProviderDispatches.objects.filter(job_id=job.id).count(), 1)
        self.assertEqual(m.AiAgentToolDispatches.objects.get(job_id=job.id).state, "succeeded")
        saved.refresh_from_db()
        self.assertEqual(json.loads(saved.result_json), bad)

    def test_model_context_and_round_budgets_preflight_before_any_model_call(self):
        self.model.max_tool_rounds = 2
        self.model.save()
        with patch("ai_assistant.provider.turn") as provider, self.assertRaises(AiError):
            self.create()
        provider.assert_not_called()
        self.assertFalse(m.AiWorkflowRuns.objects.exists())
        self.model.max_tool_rounds = 6
        self.model.generation_options_json = canonical({"contextWindowTokens": 8192})
        self.model.save()
        with patch("ai_assistant.provider.turn") as provider, self.assertRaises(AiError):
            self.create()
        provider.assert_not_called()
        self.assertFalse(m.AiReportRun.objects.exists())

    def test_real_wide_catalog_is_measured_as_all_pages_not_first_page(self):
        from business_analysis.evidence_v2 import build_catalog
        # Exercise real serialization/escaping with a trusted synthetic sealed
        # projection; only storage lookups are mocked, not directory generation.
        sources = [{"key": "wide-"+str(i), "domain": "market", "query": {
            "platform": "京东", "category": chr(0x20000+i)*200, "scope": chr(0x20100+i)*200,
            "rankingDimension": "SKU", "priceBandFilter": chr(0x20200+i)*200,
            "startDate": "2026-08-01", "endDate": "2026-08-01", "window": "current"}} for i in range(48)]
        built = build_catalog(sources)
        real_reference, _ = reports._reference(evidence.get_run(self.evidence_id, self.admin), self.body_report["question"])
        reference = {**real_reference, "catalogDigest": built["header"]["catalogDigest"], "evidencePlanDigest": built["planDigest"], "sourceCount": 48}
        with patch("ai_assistant.business_reports._reference", return_value=(reference, sources)), patch("ai_assistant.transport.catalog", return_value=self.tools), patch("ai_assistant.provider.turn") as provider:
            with self.assertRaises(AiError) as caught:
                reports.create(self.body_report, self.admin)
        self.assertIn(caught.exception.code, {"transcript_limit_exceeded", "ai_context_budget_exceeded", "tool_limit_exceeded"})
        provider.assert_not_called()
        self.assertFalse(m.AiWorkflowRuns.objects.exists())

    def test_analysis_transcript_reservation_bounds_utf8_and_nested_escaping(self):
        from types import SimpleNamespace
        from . import provider
        limit = reports.V2_ANALYSIS_RESPONSE_BYTES
        for char in ('"', '\\', '中', '\n', 'x'):
            # Fill the compact JSON to its byte boundary, not character count.
            unit = len(canonical(char).encode())-2
            data = {"x": char*((limit-len(canonical({"x": ""}).encode()))//unit)}
            size = len(canonical(data).encode())
            self.assertLessEqual(size, limit)
            self.assertLess(limit-size, unit)
            for protocol in ("openai_compatible", "anthropic"):
                call = {"id": "c"*200, "name": reports.V2_TABLE_TOOL, "arguments": {"runId": "r"*160, "sourceKey": "s"*160, "dimension": "shop"}}
                response = {"ok": True, "auditStatus": "recorded", "toolName": call["name"], "data": data}
                frames = provider.tool_frames(SimpleNamespace(protocol=protocol), [call], [response])
                actual = len(canonical(frames).encode())+len(canonical(call).encode())
                self.assertLess(actual, reports.V2_ANALYSIS_TRANSCRIPT_RESERVE)
