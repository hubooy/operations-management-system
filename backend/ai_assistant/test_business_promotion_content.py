"""Five completed role ledgers become a bounded pending-review content DTO."""
from datetime import timedelta
import json
from unittest.mock import patch

from django import test as djtest
from django.utils import timezone

from . import business_promotion_content as service
from . import business_promotion_content_contract as contract
from . import models as m
from . import test_business_promotion_completed_receipts as fixtures
from . import test_business_screening_diagnosis as answers
from .policy import AiError, canonical, digest, mutation, uid


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionContentTests(djtest.TransactionTestCase):
    user = fixtures.PromotionCompletedReceiptTests.user
    call = fixtures.PromotionCompletedReceiptTests.call
    collect_body = fixtures.PromotionCompletedReceiptTests.collect_body
    bundle = fixtures.PromotionCompletedReceiptTests.bundle
    input_for = fixtures.PromotionCompletedReceiptTests.input_for
    insert = fixtures.PromotionCompletedReceiptTests.insert
    seed = fixtures.PromotionCompletedReceiptTests.seed
    setUp = fixtures.PromotionCompletedReceiptTests.setUp
    request_body = fixtures.PromotionCompletedReceiptTests.request_body
    current_catalog = fixtures.PromotionCompletedReceiptTests.current_catalog
    create_fixed_report = fixtures.PromotionCompletedReceiptTests.create_fixed_report
    base = fixtures.PromotionCompletedReceiptTests.base
    read = fixtures.PromotionCompletedReceiptTests.read
    append = fixtures.PromotionCompletedReceiptTests.append
    package = fixtures.PromotionCompletedReceiptTests.package
    promotion = fixtures.PromotionCompletedReceiptTests.promotion
    complete = fixtures.PromotionCompletedReceiptTests.complete

    def running_job(self, report, role):
        flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
        node = m.AiWorkflowNodeRuns.objects.get(run=flow, node_key=role)
        deps = json.loads(node.depends_on_json)
        parents = {key: m.AiWorkflowNodeRuns.objects.get(run=flow, node_key=key)
            for key in deps}
        payload = canonical({"workflowInput": json.loads(flow.input_json),
            "dependencies": {key: json.loads(parent.output_json)
                for key, parent in parents.items()}})
        with mutation(self.admin):
            job = m.AiAgentJobs.objects.create(id=uid("promotion-content-job"),
                owner_email=flow.owner_email, scope_json=flow.scope_json,
                client_request_id=uid("promotion-content-client"),
                request_digest=digest(payload), task=node.instruction, input_json=payload,
                workflow_run_id=flow.id, workflow_node_key=role,
                model_id=flow.model_id, model_version=flow.model_version,
                allowed_tools_json=flow.allowed_tools_json,
                tool_policy_digest=flow.tool_policy_digest,
                status="running", phase="executing",
                lease_token=uid("promotion-content-lease"), lease_epoch=1,
                lease_expires_at=timezone.now()+timedelta(minutes=8))
            node.agent_job = job
            node.status = "running"
            node.input_json = payload
            node.started_at = timezone.now()
            node.version += 1
            node.save()
        return job

    def five_completed(self, *, promotion_reference=False, native_reference=False,
                       review_approved=True):
        report = self.create_fixed_report()
        for role in contract.ROLES:
            job = self.running_job(report, role)
            self.package(report, job)
            answer = answers.answer(role)
            if role == "commerce" and native_reference:
                args = {**self.base(report), "mode": "native", "dimension": "sku",
                    "sourceKey": "ads", "offset": 0}
                page = self.read(job, service.promotion_contract.TABLE_TOOL, args)
                self.append(job, service.promotion_contract.TABLE_TOOL, args, page)
                row = page["table"]["rows"][0]
                answer = answers.answer(role, {"sourceKey": "ads", "dimension": "sku",
                    "rowIndex": row["rowIndex"], "rowId": row["id"],
                    "metric": "spendCents", "field": "value"})
            if role == "promotion" and promotion_reference:
                page = self.promotion(report, job)
                row = page["table"]["rows"][0]
                answer = answers.answer(role, {"kind": "promotion_keyword_sku",
                    "sourceKey": "ads", "view": "keyword_sku",
                    "rowIndex": row["rowIndex"], "rowId": row["id"],
                    "tableBindingDigest": page["binding"]["tableBindingDigest"],
                    "metric": "spendCents", "field": "value"})
            if role == "independent_review" and not review_approved:
                answer = {"approved": False, "conflicts": ["专业结论尚未核清"],
                    "limitations": []}
            self.complete(job, canonical(answer))
        with mutation(self.admin):
            flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
            flow.status = "waiting_review"
            flow.current_node_key = "human_review"
            flow.version += 1
            flow.save()
            human = m.AiWorkflowNodeRuns.objects.get(run=flow, node_key="human_review")
            human.status = "waiting_review"
            human.version += 1
            human.save()
        return report

    def content(self, report):
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.build(report.id, self.admin)

    def test_five_independent_completed_ledgers_form_pending_review_dto(self):
        report = self.five_completed(promotion_reference=True, native_reference=True)
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            dto = self.content(report)
        self.assertEqual(contract.check(dto), dto)
        self.assertFalse(dto["authorityVerified"])
        self.assertFalse(dto["registered"])
        self.assertEqual(dto["binding"]["humanReview"],
            {"status": "pending", "reviewDigest": None})
        self.assertEqual(set(dto["binding"]["jobs"]), set(contract.ROLES))
        self.assertEqual(set(dto["content"]["screening"]["readProofs"]), set(contract.ROLES))
        native = dto["content"]["professionalAnalyses"]["commerce"]["findings"][0]["facts"][0]
        promotion = dto["content"]["professionalAnalyses"]["promotion"]["findings"][0]["facts"][0]
        self.assertTrue(native["verification"]["referenceReadVerified"])
        self.assertTrue(promotion["verification"]["completedAgentReadVerified"])
        model.assert_not_called(); remote.assert_not_called()

    def test_unresolved_independent_review_blocks_content(self):
        report = self.five_completed(review_approved=False)
        with self.assertRaises(AiError): self.content(report)

    def test_missing_role_and_late_current_account_revocation_fail_closed(self):
        report = self.five_completed()
        with mutation(self.admin):
            m.AiAgentJobs.objects.filter(workflow_run_id=report.workflow_id,
                workflow_node_key="market_b2b").update(status="failed", phase="failed")
        with self.assertRaises(AiError): self.content(report)
        fresh = self.five_completed()
        from access_control.models import AppUser
        original = service.contract.prepare

        def revoke(*args):
            result = original(*args)
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return result

        with patch.object(service.contract, "prepare", side_effect=revoke), self.assertRaises(AiError):
            self.content(fresh)
