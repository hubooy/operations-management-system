"""Versioned answer shape and actual same-job promotion numeric references."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from django import test as djtest

from . import business_promotion_diagnosis as service
from . import business_promotion_runtime_contract as contract
from . import business_promotion_tools
from . import test_business_promotion_claims as fixtures
from . import test_business_screening_diagnosis as old_fixtures
from .policy import AiError, canonical


def promotion_ref():
    return {"kind":"promotion_keyword_sku", "sourceKey":"ads", "view":"keyword_sku",
        "rowIndex":0, "rowId":"a"*64, "tableBindingDigest":"b"*64,
        "metric":"spendCents", "field":"value"}


class PromotionAnswerShapeTests(unittest.TestCase):
    def test_old_five_role_shapes_and_section_action_limits_are_preserved(self):
        for role in ("commerce", "promotion", "market_b2b", "independent_review", "report"):
            source = old_fixtures.answer(role)
            self.assertEqual(service.validate_answer(role, canonical(source)), source)
        report = old_fixtures.answer("report", promotion_ref())
        self.assertEqual(service.validate_answer("report", canonical(report)), report)
        report["sections"].reverse()
        with self.assertRaises(AiError): service.validate_answer("report", canonical(report))
        action = old_fixtures.diagnosis(promotion_ref(), "action")
        action["findings"][0]["action"] = {key:"待观察" for key in service.old.contract.ACTION_FIELDS}
        action["findings"][0]["action"].update(observationDays=7, priority="high")
        service.validate_answer("promotion", canonical(action))
        action["findings"][0]["action"]["observationDays"] = 91
        with self.assertRaises(AiError): service.validate_answer("promotion", canonical(action))

    def test_typed_reference_role_policy_and_review_answer_stays_three_fields(self):
        ref = promotion_ref()
        for role in contract.PROMOTION_ROLES:
            self.assertEqual(service.validate_reference(role, ref), ref)
        for role in ("commerce", "market_b2b"):
            with self.assertRaises(AiError): service.validate_reference(role, ref)
            with self.assertRaises(AiError):
                service.validate_answer(role, canonical(old_fixtures.answer(role, ref)))
        review = old_fixtures.answer("independent_review")
        with self.assertRaises(AiError):
            service.validate_answer("independent_review", canonical({**review, "references":[ref]}))
        for changed in ({**ref, "number":3}, {**ref, "kind":"native"},
                {**ref, "rowId":"short"}, {**ref, "field":"ratio"}):
            with self.assertRaises(AiError): service.validate_reference("promotion", changed)

    def test_mixed_old_refs_no_extra_fields_or_duplicate_json_keys(self):
        native = {"sourceKey":"ads", "dimension":"sku", "rowIndex":0,
            "rowId":"c"*64, "metric":"spendCents", "field":"value"}
        candidate = {"candidateId":"d"*64, "metric":"spendCents", "field":"value"}
        body = old_fixtures.diagnosis(promotion_ref(), "observation")
        body["findings"][0]["references"].extend([native, candidate])
        self.assertEqual(service.validate_answer("promotion", canonical(body)), body)
        with self.assertRaises(AiError):
            service.validate_answer("promotion", '{"summary":"a","summary":"b","findings":[]}')


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionDiagnosisPgTests(djtest.TransactionTestCase):
    user = fixtures.PromotionClaimTests.user
    call = fixtures.PromotionClaimTests.call
    collect_body = fixtures.PromotionClaimTests.collect_body
    bundle = fixtures.PromotionClaimTests.bundle
    input_for = fixtures.PromotionClaimTests.input_for
    insert = fixtures.PromotionClaimTests.insert
    seed = fixtures.PromotionClaimTests.seed
    setUp = fixtures.PromotionClaimTests.setUp
    request_body = fixtures.PromotionClaimTests.request_body
    current_catalog = fixtures.PromotionClaimTests.current_catalog
    create_report = fixtures.PromotionClaimTests.create_report
    actual_job = fixtures.PromotionClaimTests.actual_job
    read = fixtures.PromotionClaimTests.read
    append = fixtures.PromotionClaimTests.append

    def verify(self, job, answer):
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.validate(job, canonical(answer), self.admin)

    def test_same_job_promotion_and_native_numbers_are_recomputed_without_full_read_claim(self):
        report = self.create_report()
        job = self.actual_job(report)
        args = {"reportId":report.id, "sourceKey":"ads", "view":"keyword_sku"}
        page = self.read(job, args)
        self.append(job, args, page)
        row = page["table"]["rows"][0]
        ref = {"kind":"promotion_keyword_sku", "sourceKey":"ads", "view":"keyword_sku",
            "rowIndex":row["rowIndex"], "rowId":row["id"],
            "tableBindingDigest":page["binding"]["tableBindingDigest"],
            "metric":"spendCents", "field":"value"}
        native_args = {"runId":self.parent.id, "reportId":report.id,
            "screeningId":json.loads(report.snapshot_json)["screeningIntent"]["id"],
            "mode":"native", "dimension":"sku", "sourceKey":"ads", "offset":0}
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog):
            native_page = business_promotion_tools.read(job.id, "analysis", native_args, self.admin)
        native_row = native_page["table"]["rows"][0]
        native = {"sourceKey":"ads", "dimension":"sku", "rowIndex":native_row["rowIndex"],
            "rowId":native_row["id"], "metric":"spendCents", "field":"value"}
        answer = old_fixtures.answer("promotion", ref)
        answer["findings"][0]["references"].append(native)
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            result = self.verify(job, answer)
        self.assertEqual(result["role"], "promotion")
        self.assertEqual(len(result["findings"][0]["facts"]), 2)
        self.assertTrue(result["findings"][0]["facts"][0]["verification"]["referenceReadVerified"])
        self.assertFalse(result["completeAgentReadingVerified"])
        self.assertFalse(result["independentReviewApproved"])
        model.assert_not_called(); remote.assert_not_called()

    def test_no_promotion_receipt_rejects_and_review_shape_stays_exact(self):
        report = self.create_report()
        job = self.actual_job(report)
        args = {"reportId":report.id, "sourceKey":"ads", "view":"keyword_sku"}
        page = self.read(job, args)
        row = page["table"]["rows"][0]
        ref = {"kind":"promotion_keyword_sku", "sourceKey":"ads", "view":"keyword_sku",
            "rowIndex":row["rowIndex"], "rowId":row["id"],
            "tableBindingDigest":page["binding"]["tableBindingDigest"],
            "metric":"spendCents", "field":"value"}
        with self.assertRaises(AiError): self.verify(job, old_fixtures.answer("promotion", ref))
        with self.assertRaises(AiError):
            service.validate_answer("independent_review", canonical({**old_fixtures.answer("independent_review"),
                "references":[ref]}))
