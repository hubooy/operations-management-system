"""Approved content must come from the actual human row and five ledgers."""
from unittest.mock import patch
import json

from django import test as djtest
from django.utils import timezone

from . import business_promotion_approved_content as service
from . import business_promotion_content_contract as contract
from . import models as m, workflows
from . import test_business_promotion_content as fixtures
from .policy import AiError, canonical, mutation


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionApprovedContentTests(djtest.TransactionTestCase):
    user = fixtures.PromotionContentTests.user
    call = fixtures.PromotionContentTests.call
    collect_body = fixtures.PromotionContentTests.collect_body
    bundle = fixtures.PromotionContentTests.bundle
    input_for = fixtures.PromotionContentTests.input_for
    insert = fixtures.PromotionContentTests.insert
    seed = fixtures.PromotionContentTests.seed
    setUp = fixtures.PromotionContentTests.setUp
    request_body = fixtures.PromotionContentTests.request_body
    current_catalog = fixtures.PromotionContentTests.current_catalog
    create_fixed_report = fixtures.PromotionContentTests.create_fixed_report
    base = fixtures.PromotionContentTests.base
    read = fixtures.PromotionContentTests.read
    append = fixtures.PromotionContentTests.append
    package = fixtures.PromotionContentTests.package
    promotion = fixtures.PromotionContentTests.promotion
    complete = fixtures.PromotionContentTests.complete
    running_job = fixtures.PromotionContentTests.running_job
    five_completed = fixtures.PromotionContentTests.five_completed

    def approved(self, report, *, decision="approve", event=True,
                 reviewer=True):
        with mutation(self.admin):
            flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
            human = m.AiWorkflowNodeRuns.objects.get(run=flow, node_key="human_review")
            human.status = "completed" if decision == "approve" else "rejected"
            human.output_json = canonical({"decision": decision, "comment": "已人工核对"})
            human.reviewer_email = self.admin.email if reviewer else None
            human.reviewed_at = timezone.now()
            human.completed_at = timezone.now()
            human.version += 1
            human.save()
            flow.status = "queued" if decision == "approve" else "failed"
            flow.current_node_key = None if decision == "approve" else human.node_key
            flow.completed_at = None if decision == "approve" else timezone.now()
            flow.retryable = 0
            flow.version += 1
            flow.save()
            if event:
                workflows.event(flow, self.admin, "review_approved",
                    "waiting_review", human.node_key)

    def complete_flow(self, report):
        with mutation(self.admin):
            flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
            nodes = list(m.AiWorkflowNodeRuns.objects.filter(run=flow).order_by("position"))
            flow.output_json = canonical({node.node_key: json.loads(node.output_json)
                for node in nodes})
            flow.status = "completed"
            flow.completed_at = timezone.now()
            flow.version += 1
            flow.save()

    def build(self, report):
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.build(report.id, self.admin)

    def test_actual_approval_rebuilds_five_jobs_for_queued_and_completed_flow(self):
        report = self.five_completed(promotion_reference=True, native_reference=True)
        with self.assertRaises(AiError): self.build(report)
        self.approved(report)
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            queued = self.build(report)
            self.complete_flow(report)
            finished = self.build(report)
        self.assertEqual(contract.check(queued), queued)
        self.assertEqual(contract.check(finished), finished)
        self.assertEqual(queued["binding"]["humanReview"]["status"], "approved")
        self.assertEqual(queued["binding"]["humanReview"]["reviewDigest"],
            finished["binding"]["humanReview"]["reviewDigest"])
        self.assertFalse(queued["authorityVerified"])
        self.assertFalse(queued["registered"])
        self.assertEqual(set(queued["binding"]["jobs"]), set(contract.ROLES))
        self.assertTrue(queued["content"]["professionalAnalyses"]["promotion"]["findings"][0]
            ["facts"][0]["verification"]["completedAgentReadVerified"])
        model.assert_not_called(); remote.assert_not_called()

    def test_rejected_or_unattributed_decision_never_becomes_approved_content(self):
        rejected = self.five_completed()
        self.approved(rejected, decision="reject")
        with self.assertRaises(AiError): self.build(rejected)
        missing_event = self.five_completed()
        self.approved(missing_event, event=False)
        with self.assertRaises(AiError): self.build(missing_event)
        missing_reviewer = self.five_completed()
        self.approved(missing_reviewer, reviewer=False)
        with self.assertRaises(AiError): self.build(missing_reviewer)

    def test_late_revocation_prevents_approved_dto_return(self):
        report = self.five_completed()
        self.approved(report)
        from access_control.models import AppUser
        original = service.content_contract.prepare

        def revoke(*args):
            result = original(*args)
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return result

        with patch.object(service.content_contract, "prepare", side_effect=revoke), self.assertRaises(AiError):
            self.build(report)
