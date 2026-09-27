"""Actual five-Agent ledger with synthetic provider/edge and signed review."""
import json
import re
from unittest.mock import patch

from django import test as djtest

from sales.tests.factories import signed_headers, TEST_SECRET
from . import business_promotion_activation as activation
from . import business_promotion_approved_content as approved
from . import business_promotion_dispatch_tool as dispatch_bridge
from . import business_promotion_file_proof as file_proof
from . import business_promotion_formal_export as formal
from . import business_promotion_readiness as readiness
from . import business_promotion_runtime as runtime
from . import business_promotion_runtime_contract as contract
from . import models as m, workflows
from . import test_business_promotion_activation as fixtures
from .test_business_screening_http import process_role
from . import test_business_screening_diagnosis as answers
from .policy import canonical, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test",
    AI_PROMOTION_AGENT_RUNTIME_ENABLED=True)
class PromotionFiveAgentIntegrationTests(djtest.TransactionTestCase):
    user = fixtures.PromotionActivationTests.user
    call = fixtures.PromotionActivationTests.call
    collect_body = fixtures.PromotionActivationTests.collect_body
    bundle = fixtures.PromotionActivationTests.bundle
    input_for = fixtures.PromotionActivationTests.input_for
    insert = fixtures.PromotionActivationTests.insert
    seed = fixtures.PromotionActivationTests.seed
    setUp = fixtures.PromotionActivationTests.setUp
    request_body = fixtures.PromotionActivationTests.request_body
    current_catalog = fixtures.PromotionActivationTests.current_catalog
    create_published = fixtures.PromotionActivationTests.create_published

    def test_real_five_role_chain_signed_review_and_unregistered_formal_pair(self):
        # The inherited fixture includes one older queued report. Keep global
        # oldest-first workflow selection focused on this new sealed report.
        old = m.AiWorkflowRuns.objects.get(pk=self.report.workflow_id)
        workflows.control(old.id, {"expectedVersion":old.version}, self.admin,
            "cancel", workflow=True)
        report = self.create_published()
        parked = readiness.advance(report.workflow, self.admin)
        self.assertEqual(parked["status"], "screening_published_awaiting_admission")
        with patch.object(runtime.transport, "catalog", side_effect=self.current_catalog):
            prepared = activation.prepare(report.id, self.admin)
            activated = activation.activate(prepared, self.admin)
        self.assertEqual(activated["specialistsStarted"], 3)
        for node in contract.graph(False)["nodes"]:
            if node["type"] == "agent":
                self.assertIn("本人固定角色为" + node["key"], node["instruction"])
        fixed = json.loads(report.snapshot_json)
        base = {"runId":fixed["evidenceRunId"], "reportId":report.id,
            "screeningId":fixed["screeningIntent"]["id"]}
        states = {role:{"nextOffset":0, "complete":False, "providerCalls":0,
            "toolCalls":0} for role in ("commerce", "promotion", "market_b2b",
                "independent_review", "report")}

        def fake_provider(_model, frames, _system, _entries):
            task = frames[0]["content"]
            match = re.search(r"本人固定角色为(commerce|promotion|market_b2b|independent_review|report)", task)
            self.assertIsNotNone(match, "实际Agent instruction必须固定角色")
            role = match.group(1)
            state = states[role]
            state["providerCalls"] += 1
            if not state["complete"]:
                args = {**base, "role":role, "offset":state["nextOffset"]}
                call = {"id":f"package-{role}-{state['providerCalls']}",
                    "name":contract.PACKAGE_TOOL, "arguments":args}
                return {"text":"", "calls":[call],
                    "frame":{"role":"assistant", "content":"", "tool_calls":[call]}}
            answer = canonical(answers.answer(role))
            return {"text":answer, "calls":[],
                "frame":{"role":"assistant", "content":answer}}

        def fake_edge(name, arguments, principal, *, surface, request_id,
                provider_call_id, policy_digest):
            self.assertEqual(surface, contract.SURFACE)
            self.assertEqual(name, contract.PACKAGE_TOOL)
            dispatch = m.AiAgentToolDispatches.objects.get(pk=request_id)
            self.assertEqual(dispatch.provider_call_id, provider_call_id)
            role = dispatch.job.workflow_node_key
            self.assertEqual(arguments["role"], role)
            result = dispatch_bridge.read(request_id, name, arguments,
                provider_call_id, principal)
            states[role]["toolCalls"] += 1
            states[role]["nextOffset"] = result["pagination"]["nextOffset"]
            states[role]["complete"] = result["pagination"]["nextOffset"] is None
            return {"toolName":name, "ok":True, "auditStatus":"recorded", "data":result}

        def finish_running_role(role):
            job = m.AiAgentJobs.objects.get(workflow_run_id=report.workflow_id,
                workflow_node_key=role)
            for step in range(24):
                result = workflows.agent_tick(job_id=job.id)
                self.assertIn(result["status"], {"checkpoint", "completed"},
                    f"role={role} step={step} result={result}")
                if result["status"] == "completed":
                    break
            else:
                self.fail(f"{role} did not finish bounded microsteps")
            job.refresh_from_db()
            self.assertEqual(job.status, "completed")
            self.assertGreaterEqual(states[role]["toolCalls"], 1)
            self.assertTrue(m.AiAgentCheckpoints.objects.filter(job_id=job.id,
                kind="completed").exists())
            self.assertTrue(m.AiAgentProviderResults.objects.filter(dispatch__job_id=job.id).exists())
            self.assertTrue(m.AiAgentToolResults.objects.filter(tool_dispatch__job_id=job.id).exists())

        with (patch.object(runtime.transport, "catalog", side_effect=self.current_catalog),
                patch("ai_assistant.provider.turn", side_effect=fake_provider) as provider,
                patch("ai_assistant.transport.execute_tool", side_effect=fake_edge) as edge,
                patch.object(workflows, "dispatch_budget", return_value=None)):
            for role in ("commerce", "promotion", "market_b2b"):
                finish_running_role(role)
            for role in ("independent_review", "report"):
                progressed = workflows.workflow_tick()
                self.assertEqual(progressed["status"], "running", progressed)
                self.assertTrue(m.AiAgentJobs.objects.filter(workflow_run_id=report.workflow_id,
                    workflow_node_key=role).exists())
                finish_running_role(role)
            review_wait = workflows.workflow_tick()
            self.assertEqual(review_wait["status"], "waiting_review", review_wait)
            self.assertEqual(m.AiAgentJobs.objects.filter(workflow_run_id=report.workflow_id).count(), 5)

            human = m.AiWorkflowNodeRuns.objects.get(run_id=report.workflow_id,
                node_key="human_review")
            url = f"/api/ai/workflow-runs/{report.workflow_id}/nodes/human_review/review"
            body = canonical({"expectedVersion":human.version, "decision":"approve",
                "comment":"已核对五角色来源、缺口与观察条件"}).encode("utf-8")
            headers = signed_headers(url, email=self.admin.email, role=self.admin.role,
                scope=self.admin.scope, method="POST", body=body)
            with (process_role("ai_writer"),
                    patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET":TEST_SECRET}),
                    djtest.override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET,
                        DJANGO_PROCESS_ROLE="ai_writer")):
                reviewed = self.client.post(url, data=body,
                    content_type="application/json", headers=headers)
                self.assertEqual(reviewed.status_code, 200, reviewed.content)
                self.assertEqual(workflows.workflow_tick()["status"], "completed")
                content = approved.build(report.id, self.admin)
                self.assertEqual(content["binding"]["humanReview"]["status"], "approved")
                self.assertEqual(content["content"]["independentReview"]["approved"], True)
                with formal.open_files(report.id, self.admin) as pair:
                    manifest = pair.manifest
                    self.assertEqual(manifest["rendererVersion"], 7)
                    self.assertFalse(manifest["registeredRenderer"])
                    self.assertFalse(manifest["deliveryAuthorized"])
                    self.assertFalse(manifest["authorityVerified"])
                    self.assertEqual(manifest["fileProof"]["schemaVersion"], file_proof.SCHEMA)
                    self.assertEqual(manifest["fileProof"]["proofDigest"],
                        digest({key:value for key,value in manifest["fileProof"].items()
                            if key != "proofDigest"}))
                    self.assertGreater(pair.path("xlsx").stat().st_size, 0)
                    self.assertGreater(pair.path("html").stat().st_size, 0)
        self.assertGreaterEqual(provider.call_count, 10)
        self.assertGreaterEqual(edge.call_count, 5)
        self.assertEqual(m.AiWorkflowRuns.objects.get(pk=report.workflow_id).status,
            "completed")
