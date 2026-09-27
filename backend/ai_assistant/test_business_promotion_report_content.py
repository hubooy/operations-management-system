"""Report detail uses the new profile's real pending and approved content."""
from unittest.mock import patch

from django import test as djtest

from . import business_reports as service, models as m
from . import business_promotion_runtime as runtime
from . import test_business_promotion_approved_content as fixtures
from .policy import AiError


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionReportContentTests(djtest.TransactionTestCase):
    user = fixtures.PromotionApprovedContentTests.user
    call = fixtures.PromotionApprovedContentTests.call
    collect_body = fixtures.PromotionApprovedContentTests.collect_body
    bundle = fixtures.PromotionApprovedContentTests.bundle
    input_for = fixtures.PromotionApprovedContentTests.input_for
    insert = fixtures.PromotionApprovedContentTests.insert
    seed = fixtures.PromotionApprovedContentTests.seed
    setUp = fixtures.PromotionApprovedContentTests.setUp
    request_body = fixtures.PromotionApprovedContentTests.request_body
    current_catalog = fixtures.PromotionApprovedContentTests.current_catalog
    create_fixed_report = fixtures.PromotionApprovedContentTests.create_fixed_report
    base = fixtures.PromotionApprovedContentTests.base
    read = fixtures.PromotionApprovedContentTests.read
    append = fixtures.PromotionApprovedContentTests.append
    package = fixtures.PromotionApprovedContentTests.package
    promotion = fixtures.PromotionApprovedContentTests.promotion
    complete = fixtures.PromotionApprovedContentTests.complete
    running_job = fixtures.PromotionApprovedContentTests.running_job
    five_completed = fixtures.PromotionApprovedContentTests.five_completed
    approved = fixtures.PromotionApprovedContentTests.approved

    def content(self, report):
        fresh = m.AiReportRun.objects.select_related("workflow").get(pk=report.id)
        with patch.object(runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.content(fresh, self.admin)

    def test_waiting_review_and_approved_are_each_rebuilt_from_actual_ledgers(self):
        report = self.five_completed(promotion_reference=True)
        pending = self.content(report)
        self.assertEqual(len(pending["sections"]), 5)
        self.assertTrue(pending["independentReview"]["approved"])
        self.assertTrue(pending["screening"]["readProofs"])
        self.approved(report)
        formal = self.content(report)
        self.assertEqual(formal["sections"], pending["sections"])
        self.assertEqual(formal["diagnosis"]["summary"], pending["diagnosis"]["summary"])

    def test_unfinished_new_report_has_no_content(self):
        report = self.create_fixed_report()
        with self.assertRaises(AiError): self.content(report)
