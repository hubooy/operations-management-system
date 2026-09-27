"""Completed-job receipt reconstruction from real sealed pages and saved rows."""
import json
from unittest.mock import patch

from django import test as djtest
from django.utils import timezone

from . import business_promotion_completed_receipts as service
from . import business_promotion_runtime as runtime
from . import models as m
from . import test_business_promotion_full_receipts as fixtures
from . import test_business_screening_diagnosis as answers
from .policy import AiError, canonical, digest, mutation, uid


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionCompletedReceiptTests(djtest.TransactionTestCase):
    user = fixtures.PromotionFullReceiptTests.user
    call = fixtures.PromotionFullReceiptTests.call
    collect_body = fixtures.PromotionFullReceiptTests.collect_body
    bundle = fixtures.PromotionFullReceiptTests.bundle
    input_for = fixtures.PromotionFullReceiptTests.input_for
    insert = fixtures.PromotionFullReceiptTests.insert
    seed = fixtures.PromotionFullReceiptTests.seed
    setUp = fixtures.PromotionFullReceiptTests.setUp
    request_body = fixtures.PromotionFullReceiptTests.request_body
    current_catalog = fixtures.PromotionFullReceiptTests.current_catalog
    create_fixed_report = fixtures.PromotionFullReceiptTests.create_fixed_report
    actual_job = fixtures.PromotionFullReceiptTests.actual_job
    base = fixtures.PromotionFullReceiptTests.base
    read = fixtures.PromotionFullReceiptTests.read
    append = fixtures.PromotionFullReceiptTests.append
    package = fixtures.PromotionFullReceiptTests.package
    promotion = fixtures.PromotionFullReceiptTests.promotion

    def proof(self, job):
        with patch.object(runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.progress(job, self.admin)

    def resolve(self, job, reference):
        with patch.object(runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.resolve_promotion_reference(job, reference, self.admin)

    def complete(self, job, answer=None, *, final_text=None, checkpoint=True,
                 node_output=None):
        answer = answer or canonical(answers.answer(job.workflow_node_key))
        output = canonical({"answer": answer})
        final = {"text": answer if final_text is None else final_text,
            "calls": [], "frame": {"role": "assistant", "content": answer}}
        with mutation(self.admin):
            flow = m.AiWorkflowRuns.objects.get(pk=job.workflow_run_id)
            flow.status = "running"
            flow.version += 1
            flow.save()
            ordinal = m.AiAgentProviderDispatches.objects.filter(job=job).count() + 1
            provider = m.AiAgentProviderDispatches.objects.create(
                id=uid("completed-provider"), job=job, dispatch_ordinal=ordinal,
                owner_email=job.owner_email, actor_role="admin",
                model_id=job.model_id, model_version=job.model_version,
                tool_policy_digest=job.tool_policy_digest, request_digest=digest(final),
                lease_epoch=job.lease_epoch, state="succeeded")
            m.AiAgentProviderResults.objects.create(dispatch=provider,
                response_json=canonical(final), response_digest=digest(final))
            job.status = "completed"
            job.phase = "completed"
            job.output_json = output
            job.lease_token = ""
            job.lease_expires_at = None
            job.completed_at = timezone.now()
            job.step_index += 1
            job.version += 1
            job.provider_round_count = ordinal
            job.save()
            node = m.AiWorkflowNodeRuns.objects.get(run_id=flow.id,
                node_key=job.workflow_node_key)
            node.status = "completed"
            node.output_json = output if node_output is None else node_output
            node.completed_at = timezone.now()
            node.version += 1
            node.save()
            if checkpoint:
                m.AiAgentCheckpoints.objects.create(id=uid("completed-checkpoint"),
                    job=job, ordinal=job.step_index, kind="completed",
                    state_json=job.state_json, output_digest=digest({"answer": answer}))
        job.refresh_from_db()
        return answer

    def test_completed_package_and_promotion_row_are_independently_reconstructed(self):
        report = self.create_fixed_report()
        job = self.actual_job(report, role="promotion")
        self.package(report, job)
        page = self.promotion(report, job)
        self.complete(job)
        row = page["table"]["rows"][0]
        reference = {"kind": "promotion_keyword_sku", "sourceKey": "ads",
            "view": "keyword_sku", "rowIndex": row["rowIndex"], "rowId": row["id"],
            "tableBindingDigest": page["binding"]["tableBindingDigest"],
            "metric": "spendCents", "field": "value"}
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            proof = self.proof(job)
            fact = self.resolve(job, reference)
        self.assertTrue(proof["completedAgentReadVerified"])
        self.assertFalse(proof["allAgentsReadVerified"])
        self.assertEqual(proof["jobId"], job.id)
        self.assertEqual(proof["package"]["pages"],
            proof["toolCounts"][service.contract.PACKAGE_TOOL])
        self.assertEqual(fact["jobId"], job.id)
        self.assertTrue(fact["verification"]["completedAgentReadVerified"])
        self.assertTrue(fact["verification"]["referenceReadVerified"])
        model.assert_not_called(); remote.assert_not_called()
        with self.assertRaises(AiError):
            self.resolve(job, {**reference, "rowId": "f"*64})

    def test_missing_budget_checkpoint_or_final_model_text_fails_closed(self):
        report = self.create_fixed_report(budget=True)
        job = self.actual_job(report, role="promotion")
        self.package(report, job)
        self.complete(job)
        with self.assertRaises(AiError): self.proof(job)
        without_checkpoint = self.create_fixed_report()
        second = self.actual_job(without_checkpoint, role="promotion")
        self.package(without_checkpoint, second)
        self.complete(second, checkpoint=False)
        with self.assertRaises(AiError): self.proof(second)
        mismatch = self.create_fixed_report()
        third = self.actual_job(mismatch, role="promotion")
        self.package(mismatch, third)
        self.complete(third, final_text="别的模型回答")
        with self.assertRaises(AiError): self.proof(third)

    def test_completed_budget_and_native_analysis_pages_replay_without_running_adapter(self):
        report = self.create_fixed_report(budget=True)
        job = self.actual_job(report, role="promotion")
        self.package(report, job)
        offset = 0
        while offset is not None:
            args = {**self.base(report), "offset": offset}
            page = self.read(job, service.contract.BUDGET_TOOL, args)
            self.append(job, service.contract.BUDGET_TOOL, args, page)
            offset = page["budget"]["pagination"]["nextOffset"]
        args = {**self.base(report), "mode": "native", "dimension": "sku",
            "sourceKey": "ads", "offset": 0}
        page = self.read(job, service.contract.TABLE_TOOL, args)
        self.append(job, service.contract.TABLE_TOOL, args, page)
        self.complete(job)
        with patch.object(service.format_reader, "read",
                side_effect=AssertionError("completed proof must not use running adapter")):
            proof = self.proof(job)
        self.assertTrue(proof["budget"]["required"])
        self.assertTrue(proof["budget"]["complete"])
        self.assertEqual(proof["toolCounts"][service.contract.TABLE_TOOL], 1)
        self.assertEqual(len(proof["analysisSelectors"]), 1)

    def test_unknown_tool_and_node_output_mismatch_fail_closed(self):
        report = self.create_fixed_report()
        job = self.actual_job(report, role="promotion")
        args = {**self.base(report), "role": "promotion"}
        self.append(job, service.contract.PACKAGE_TOOL, args, state="calling")
        self.complete(job)
        with self.assertRaises(AiError): self.proof(job)
        another = self.create_fixed_report()
        second = self.actual_job(another, role="promotion")
        self.package(another, second)
        self.complete(second, node_output=canonical({"answer":"不同节点输出"}))
        with self.assertRaises(AiError): self.proof(second)

    def test_completed_commerce_role_requires_own_package_and_cannot_claim_promotion(self):
        report = self.create_fixed_report()
        job = self.actual_job(report, role="commerce")
        self.package(report, job)
        self.complete(job)
        proof = self.proof(job)
        self.assertEqual(proof["role"], "commerce")
        self.assertTrue(proof["completedAgentReadVerified"])
        self.assertEqual(sum(part["pages"] for part in proof["promotionViews"].values()), 0)

    def test_post_review_queued_needs_actual_approved_human_and_five_jobs(self):
        from . import test_business_promotion_content as content_fixture

        self.running_job = content_fixture.PromotionContentTests.running_job.__get__(self)
        report = content_fixture.PromotionContentTests.five_completed(self)
        job = m.AiAgentJobs.objects.get(workflow_run_id=report.workflow_id,
            workflow_node_key="promotion")
        with mutation(self.admin):
            flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
            flow.status = "queued"
            flow.current_node_key = None
            flow.version += 1
            flow.save()
        with self.assertRaises(AiError): self.proof(job)
        with mutation(self.admin):
            flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
            human = m.AiWorkflowNodeRuns.objects.get(run=flow,
                node_key="human_review")
            human.status = "completed"
            human.output_json = canonical({"decision":"approve","comment":"已人工核验"})
            human.reviewer_email = self.admin.email
            human.reviewed_at = timezone.now()
            human.completed_at = timezone.now()
            human.version += 1
            human.save()
            flow.version += 1
            flow.save()
            from . import workflows
            workflows.event(flow, self.admin, "review_approved", "waiting_review",
                human.node_key)
        self.assertTrue(self.proof(job)["completedAgentReadVerified"])
