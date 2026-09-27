"""Pending human review preparation with short commit-time state recheck."""
from unittest.mock import patch

from django import test as djtest
from django.db.models import F

from . import business_promotion_review as service
from . import business_promotion_content as content
from . import models as m
from . import test_business_promotion_content as fixtures
from .policy import AiError, mutation


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionReviewTests(djtest.TransactionTestCase):
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

    def prepare(self, report):
        with patch.object(content.runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.prepare_review(report.id, self.admin)

    def revalidate(self, prepared):
        with patch.object(content.runtime.transport, "catalog", side_effect=AssertionError(
                "short review check cannot call central catalog")):
            return service.revalidate_review(prepared, self.admin)

    def test_full_outside_check_and_short_commit_check_do_not_approve(self):
        report = self.five_completed()
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            prepared = self.prepare(report)
            with patch.object(service.content, "build", side_effect=AssertionError(
                    "commit check cannot recompute facts")), mutation(self.admin):
                proof = self.revalidate(prepared)
        self.assertEqual(proof["reportId"], report.id)
        self.assertTrue(proof["humanReviewPending"])
        self.assertFalse(proof["approved"])
        self.assertFalse(proof["filePublicationGranted"])
        self.assertEqual(m.AiWorkflowRuns.objects.get(pk=report.workflow_id).status,
            "waiting_review")
        model.assert_not_called(); remote.assert_not_called()
        with mutation(self.admin), self.assertRaises(AiError):
            service.revalidate_review({"approved": True}, self.admin)

    def test_unresolved_independent_review_and_changed_version_fail_closed(self):
        blocked = self.five_completed(review_approved=False)
        with self.assertRaises(AiError): self.prepare(blocked)
        report = self.five_completed()
        prepared = self.prepare(report)
        with mutation(self.admin):
            m.AiWorkflowRuns.objects.filter(pk=report.workflow_id).update(
                version=F("version")+1)
        with mutation(self.admin), self.assertRaises(AiError):
            self.revalidate(prepared)

    def test_late_current_account_revocation_blocks_prepared_token(self):
        report = self.five_completed()
        prepared = self.prepare(report)
        from access_control.models import AppUser
        AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with mutation(), self.assertRaises(AiError):
            self.revalidate(prepared)
