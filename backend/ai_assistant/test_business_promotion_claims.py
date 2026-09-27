"""Numeric promotion claims require the same Agent's durable tool read."""
from unittest.mock import patch

from django import test as djtest

from . import business_promotion_claims as service
from . import business_promotion_read_receipts as receipts
from . import test_business_promotion_read_receipts as fixtures
from .policy import AiError


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionClaimTests(djtest.TransactionTestCase):
    user = fixtures.PromotionReadReceiptTests.user
    call = fixtures.PromotionReadReceiptTests.call
    collect_body = fixtures.PromotionReadReceiptTests.collect_body
    bundle = fixtures.PromotionReadReceiptTests.bundle
    input_for = fixtures.PromotionReadReceiptTests.input_for
    insert = fixtures.PromotionReadReceiptTests.insert
    seed = fixtures.PromotionReadReceiptTests.seed
    setUp = fixtures.PromotionReadReceiptTests.setUp
    request_body = fixtures.PromotionReadReceiptTests.request_body
    current_catalog = fixtures.PromotionReadReceiptTests.current_catalog
    create_report = fixtures.PromotionReadReceiptTests.create_report
    actual_job = fixtures.PromotionReadReceiptTests.actual_job
    read = fixtures.PromotionReadReceiptTests.read
    append = fixtures.PromotionReadReceiptTests.append

    def claim(self, job, reference):
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.resolve(job, reference, self.admin)

    def test_same_job_durable_page_and_current_numeric_fact(self):
        report = self.create_report()
        job = self.actual_job(report)
        args = {"reportId": report.id, "sourceKey": "ads", "view": "keyword_sku", "offset": 0}
        page = self.read(job, args)
        self.append(job, args, page)
        row = next(item for item in page["table"]["rows"]
            if item["metrics"] and item["metrics"]["spendCents"]["value"] is not None)
        reference = {"kind": "promotion_keyword_sku", "sourceKey": "ads", "view": "keyword_sku",
            "rowIndex": row["rowIndex"], "rowId": row["id"],
            "tableBindingDigest": page["binding"]["tableBindingDigest"],
            "metric": "spendCents", "field": "value"}
        fact = self.claim(job, reference)
        self.assertEqual(fact["value"], row["metrics"]["spendCents"]["value"])
        self.assertTrue(fact["verification"]["referenceReadVerified"])
        self.assertFalse(fact["verification"]["completeAgentReadingVerified"])
        self.assertEqual(fact["actionableKeywordSku"], row["identityQualified"])
        for changed in ({**reference, "tableBindingDigest": "0"*64},
                {**reference, "rowIndex": row["rowIndex"]+1},
                {**reference, "baselineKey": "ads"},
                {**reference, "field": "difference"},
                {**reference, "value": 123}):
            with self.subTest(changed=changed), self.assertRaises(AiError):
                self.claim(job, changed)

    def test_no_receipt_or_other_job_receipt_cannot_prove_a_number(self):
        report = self.create_report()
        job = self.actual_job(report)
        args = {"reportId": report.id, "sourceKey": "ads", "view": "keyword_sku"}
        page = self.read(job, args)
        row = page["table"]["rows"][0]
        reference = {"kind": "promotion_keyword_sku", "sourceKey": "ads", "view": "keyword_sku",
            "rowIndex": row["rowIndex"], "rowId": row["id"],
            "tableBindingDigest": page["binding"]["tableBindingDigest"],
            "metric": "spendCents", "field": "value"}
        with self.assertRaises(AiError): self.claim(job, reference)
        other = self.actual_job(self.create_report())
        # Even a valid receipt for another report must not authorize this job.
        self.append(job, args, page)
        original = receipts.progress
        with patch.object(receipts, "progress", side_effect=lambda target, principal:
                {**original(target, principal), "jobId": other.id}), self.assertRaises(AiError):
            self.claim(job, reference)

    def test_late_role_revocation_fails_after_owning_read(self):
        from access_control.models import AppUser
        report = self.create_report()
        job = self.actual_job(report)
        args = {"reportId": report.id, "sourceKey": "ads", "view": "keyword_sku"}
        page = self.read(job, args)
        self.append(job, args, page)
        row = page["table"]["rows"][0]
        reference = {"kind": "promotion_keyword_sku", "sourceKey": "ads", "view": "keyword_sku",
            "rowIndex": row["rowIndex"], "rowId": row["id"],
            "tableBindingDigest": page["binding"]["tableBindingDigest"],
            "metric": "spendCents", "field": "value"}
        original = service.owning.read_row
        def revoked(*args, **kwargs):
            value = original(*args, **kwargs)
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return value
        with patch.object(service.owning, "read_row", side_effect=revoked), self.assertRaises(AiError):
            self.claim(job, reference)
