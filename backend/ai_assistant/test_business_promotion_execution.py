"""Final-answer preparation over one actual running Agent, with no dispatch."""
from unittest.mock import patch

from django import test as djtest

from . import business_promotion_execution as service
from . import business_promotion_full_receipts as reads
from . import models as m
from . import test_business_promotion_full_receipts as fixtures
from . import test_business_screening_diagnosis as answers
from .policy import AiError, canonical, mutation


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionFinalExecutionTests(djtest.TransactionTestCase):
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

    def validate(self, job, answer):
        with patch.object(reads.tools.runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.validate_final(job, answer, self.admin)

    def check(self, validated, job, answer):
        with patch.object(reads.tools.runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.check(validated, job, answer, self.admin)

    def test_complete_required_reads_and_gap_answer_yield_process_local_token(self):
        report = self.create_fixed_report()
        job = self.actual_job(report, role="promotion")
        answer = canonical(answers.answer("promotion"))
        with self.assertRaises(AiError): self.validate(job, answer)
        self.package(report, job)
        before = (m.AiAgentProviderDispatches.objects.count(),
            m.AiAgentToolDispatches.objects.count(), m.AiWorkflowEvents.objects.count())
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            validated = self.validate(job, answer)
            proof = self.check(validated, job, answer)
        self.assertTrue(proof["fullAgentReadComplete"])
        self.assertTrue(proof["answerValidated"])
        self.assertFalse(proof["runtimeAdmissionGranted"])
        self.assertFalse(proof["allAgentsReadVerified"])
        self.assertFalse(proof["independentReviewApproved"])
        self.assertEqual(validated.diagnosis["role"], "promotion")
        self.assertEqual(before, (m.AiAgentProviderDispatches.objects.count(),
            m.AiAgentToolDispatches.objects.count(), m.AiWorkflowEvents.objects.count()))
        model.assert_not_called(); remote.assert_not_called()
        with self.assertRaises(AiError): service.check(proof, job, answer, self.admin)

    def test_exact_promotion_reference_is_resolved_from_same_job_receipt(self):
        report = self.create_fixed_report()
        job = self.actual_job(report, role="promotion")
        self.package(report, job)
        page = self.promotion(report, job)
        row = page["table"]["rows"][0]
        ref = {"kind":"promotion_keyword_sku", "sourceKey":"ads", "view":"keyword_sku",
            "rowIndex":row["rowIndex"], "rowId":row["id"],
            "tableBindingDigest":page["binding"]["tableBindingDigest"],
            "metric":"spendCents", "field":"value"}
        answer = canonical(answers.answer("promotion", ref))
        validated = self.validate(job, answer)
        facts = validated.diagnosis["findings"][0]["facts"]
        self.assertEqual(len(facts), 1)
        self.assertTrue(facts[0]["verification"]["referenceReadVerified"])
        self.assertEqual(self.check(validated, job, answer)["role"], "promotion")
        invalid = canonical(answers.answer("promotion", {**ref, "rowId":"f"*64}))
        with self.assertRaises(AiError): self.validate(job, invalid)

    def test_changed_answer_or_lease_cannot_reuse_token(self):
        report = self.create_fixed_report()
        job = self.actual_job(report, role="promotion")
        self.package(report, job)
        answer = canonical(answers.answer("promotion"))
        token = self.validate(job, answer)
        with self.assertRaises(AiError): self.check(token, job, answer+" ")
        with mutation(self.admin):
            m.AiAgentJobs.objects.filter(pk=job.id).update(lease_epoch=job.lease_epoch+1)
        with self.assertRaises(AiError): self.check(token, job, answer)

    def test_late_job_change_after_diagnosis_does_not_return_validation(self):
        report = self.create_fixed_report()
        job = self.actual_job(report, role="promotion")
        self.package(report, job)
        answer = canonical(answers.answer("promotion"))
        original = service.diagnosis.validate

        def changed(*args):
            result = original(*args)
            with mutation(self.admin):
                m.AiAgentJobs.objects.filter(pk=job.id).update(lease_epoch=job.lease_epoch+1)
            return result

        with patch.object(service.diagnosis, "validate", side_effect=changed):
            with self.assertRaises(AiError): self.validate(job, answer)
