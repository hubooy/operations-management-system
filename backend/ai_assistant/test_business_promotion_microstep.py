"""Read-only promotion step checks over actual running job and saved prefix."""
import json
from unittest.mock import patch

from django import test as djtest
from django.db import connection

from . import business_promotion_microstep as service
from . import business_promotion_runtime_contract as contract
from . import models as m, report_library, workflows
from . import test_business_promotion_admission as admission_fixtures
from . import test_business_promotion_full_receipts as fixtures
from .policy import AiError, canonical, mutation


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionMicrostepTests(djtest.TransactionTestCase):
    user = fixtures.PromotionFullReceiptTests.user
    call = fixtures.PromotionFullReceiptTests.call
    collect_body = fixtures.PromotionFullReceiptTests.collect_body
    bundle = fixtures.PromotionFullReceiptTests.bundle
    input_for = fixtures.PromotionFullReceiptTests.input_for
    insert = fixtures.PromotionFullReceiptTests.insert
    seed = fixtures.PromotionFullReceiptTests.seed
    setUp = fixtures.PromotionFullReceiptTests.setUp
    request_body = fixtures.PromotionFullReceiptTests.request_body
    create_fixed_report = fixtures.PromotionFullReceiptTests.create_fixed_report
    actual_job = fixtures.PromotionFullReceiptTests.actual_job
    base = fixtures.PromotionFullReceiptTests.base
    read = fixtures.PromotionFullReceiptTests.read
    append = fixtures.PromotionFullReceiptTests.append
    package = fixtures.PromotionFullReceiptTests.package

    def current_catalog(self, *_):
        self.assertFalse(connection.in_atomic_block, "catalog network under mutation")
        entries = admission_fixtures.catalog()
        self.assertEqual(len(entries), 4)
        return entries

    def job(self, report, role="promotion"):
        job = self.actual_job(report, role=role)
        with mutation(self.admin):
            report_library.pin(job.id, job.task, None, [], parent_id=report.workflow_id)
        return job

    def prepare_step(self, job):
        with patch.object(service.transport, "catalog", side_effect=self.current_catalog):
            return service.prepare(job, self.admin)

    def call_checked(self, prepared, name, args):
        with patch.object(service.transport, "catalog", side_effect=self.current_catalog):
            return service.validate_call(prepared,
                {"id":"call-1", "name":name, "arguments":args}, self.admin)

    def test_empty_prefix_package_first_exact_fit_and_short_check(self):
        report = self.create_fixed_report()
        job = self.job(report)
        prepared = self.prepare_step(job)
        self.assertFalse(prepared.proof["fullAgentReadComplete"])
        self.assertFalse(prepared.proof["runtimeAdmissionGranted"])
        args = {**self.base(report), "role":"promotion", "offset":0}
        checked = self.call_checked(prepared, contract.PACKAGE_TOOL, args)
        self.assertFalse(checked["runtimeAdmissionGranted"])
        for name, params in ((contract.TABLE_TOOL, {**self.base(report), "mode":"native",
                "dimension":"sku", "sourceKey":"ads"}),
                (contract.BUDGET_TOOL, self.base(report)),
                (contract.PROMOTION_TOOL, {"reportId":report.id, "sourceKey":"ads", "view":"keyword_sku"})):
            with self.assertRaises(AiError): self.call_checked(prepared, name, params)
        model = workflows.resolve_model(job.model_id)
        frames = [{"role":"user", "content":job.task+"\n<task_input>"
            +job.input_json.replace("<", "\\u003c")+"</task_input>"}]
        with patch.object(service.transport, "catalog", side_effect=self.current_catalog), patch(
                "ai_assistant.provider.turn", side_effect=AssertionError("no paid provider")) as paid:
            result = service.validate_provider_turn(prepared, frames,
                self.current_catalog(), model, self.admin)
        self.assertTrue(result["fits"])
        self.assertFalse(result["runtimeAdmissionGranted"])
        paid.assert_not_called()
        for changed in ([*frames, {"role":"user", "content":"forged"}],
                [{"role":"user", "content":"forged"}]):
            with patch.object(service.transport, "catalog", side_effect=self.current_catalog), self.assertRaises(AiError):
                service.validate_provider_turn(prepared, changed, self.current_catalog(), model, self.admin)
        with (patch.object(service.transport, "catalog", side_effect=AssertionError("no network in short check")),
                mutation(self.admin)):
            self.assertEqual(service.check(prepared, job, self.admin)["jobId"], job.id)

    def test_saved_package_budget_and_promotion_offsets_have_no_dispatch_grant(self):
        report = self.create_fixed_report(budget=True)
        job = self.job(report)
        self.package(report, job)
        prepared = self.prepare_step(job)
        self.assertTrue(prepared.proof["package"]["complete"])
        self.assertTrue(prepared.proof["budget"]["required"])
        self.assertFalse(prepared.proof["fullAgentReadComplete"])
        base = self.base(report)
        with self.assertRaises(AiError):
            self.call_checked(prepared, contract.PACKAGE_TOOL, {**base, "role":"promotion", "offset":0})
        self.assertTrue(self.call_checked(prepared, contract.BUDGET_TOOL, {**base, "offset":0})["callValidated"])
        self.assertTrue(self.call_checked(prepared, contract.TABLE_TOOL,
            {**base, "mode":"native", "dimension":"sku", "sourceKey":"ads", "offset":0})["callValidated"])
        self.assertTrue(self.call_checked(prepared, contract.PROMOTION_TOOL,
            {"reportId":report.id, "sourceKey":"ads", "view":"keyword_sku", "offset":0})["callValidated"])
        for name, args in ((contract.BUDGET_TOOL, {**base, "offset":1}),
                (contract.TABLE_TOOL, {**base, "mode":"mapped", "dimension":"category",
                    "pairKey":"a"*64}),
                (contract.PROMOTION_TOOL, {"reportId":report.id, "sourceKey":"ads",
                    "view":"keyword_sku", "offset":1})):
            with self.assertRaises(AiError): self.call_checked(prepared, name, args)

    def test_unknown_prefix_cross_role_and_late_lease_or_model_change_rejected(self):
        report = self.create_fixed_report()
        job = self.job(report)
        base = self.base(report)
        self.append(job, contract.PACKAGE_TOOL, {**base, "role":"promotion"}, state="calling")
        with self.assertRaises(AiError): self.prepare_step(job)
        other = self.create_fixed_report()
        commerce = self.job(other, role="commerce")
        prepared = self.prepare_step(commerce)
        with self.assertRaises(AiError): self.call_checked(prepared, contract.PROMOTION_TOOL,
            {"reportId":other.id, "sourceKey":"ads", "view":"keyword_sku"})
        with mutation(self.admin):
            m.AiAgentJobs.objects.filter(pk=commerce.id).update(lease_token="changed")
        with self.assertRaises(AiError): service.check(prepared, commerce, self.admin)
        third = self.create_fixed_report()
        pending = self.job(third)
        step = self.prepare_step(pending)
        with mutation(self.admin):
            m.AiModels.objects.filter(pk=pending.model_id).update(version=pending.model_version+1)
        with self.assertRaises(AiError): service.check(step, pending, self.admin)
