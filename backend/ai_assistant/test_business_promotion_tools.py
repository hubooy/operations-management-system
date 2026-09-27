"""First three model tools over actual new-profile sealed and screened roots."""
import json
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from . import business_diagnostic_screening as screening
from . import business_promotion_creation as creation
from . import business_promotion_tools as service
from . import business_screening_packages as packages, business_screening_store as store
from . import models as m
from . import test_business_promotion_agent_tool as fixtures
from .policy import AiError, canonical


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionModelToolsTests(djtest.TransactionTestCase):
    user = fixtures.PromotionAgentToolTests.user
    call = fixtures.PromotionAgentToolTests.call
    collect_body = fixtures.PromotionAgentToolTests.collect_body
    bundle = fixtures.PromotionAgentToolTests.bundle
    input_for = fixtures.PromotionAgentToolTests.input_for
    insert = fixtures.PromotionAgentToolTests.insert
    seed = fixtures.PromotionAgentToolTests.seed
    setUp = fixtures.PromotionAgentToolTests.setUp
    request_body = fixtures.PromotionAgentToolTests.request_body
    current_catalog = fixtures.PromotionAgentToolTests.current_catalog
    actual_job = fixtures.PromotionAgentToolTests.actual_job

    def create_fixed_report(self, *, mapping=False, budget=False):
        body = self.request_body(mapping=mapping, budget=budget)
        with patch.object(creation.transport, "catalog", side_effect=self.current_catalog):
            created = creation.create(body, self.admin)
        report = m.AiReportRun.objects.select_related("workflow").get(pk=created["item"]["id"])
        verified = screening.prepare_for_report(report.id, self.admin)
        store.publish(verified, self.admin)
        return report

    def invoke(self, job, operation, params):
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.read(job.id, operation, params, self.admin)

    def base(self, report):
        snapshot = json.loads(report.snapshot_json)
        return {"runId":snapshot["evidenceRunId"], "reportId":report.id,
            "screeningId":snapshot["screeningIntent"]["id"]}

    def test_actual_role_package_and_native_analysis_are_read_only(self):
        report = self.create_fixed_report()
        commerce = self.actual_job(report, role="commerce")
        promotion = self.actual_job(report, role="promotion")
        args = self.base(report)
        before = (m.AiAgentToolDispatches.objects.count(), m.AiAgentToolResults.objects.count())
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            package = self.invoke(commerce, "package", {**args, "role":"commerce", "offset":0})
            analysis = self.invoke(promotion, "analysis", {**args, "mode":"native",
                "dimension":"sku", "sourceKey":"ads", "offset":0})
        self.assertEqual(package["role"], "commerce")
        self.assertEqual(package["reportId"], report.id)
        self.assertFalse(package["authorityVerified"])
        self.assertEqual(analysis["reference"], json.loads(report.workflow.input_json))
        self.assertEqual(analysis["mode"], "native")
        self.assertGreater(analysis["table"]["total"], 0)
        self.assertEqual(before, (m.AiAgentToolDispatches.objects.count(), m.AiAgentToolResults.objects.count()))
        model.assert_not_called(); remote.assert_not_called()
        for query in queries:
            self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")))

    def test_mapped_analysis_optional_budget_and_strict_errors(self):
        report = self.create_fixed_report(mapping=True, budget=True)
        job = self.actual_job(report, role="promotion")
        args = self.base(report)
        snapshot = json.loads(report.snapshot_json)
        pair = snapshot["mappingPlan"]["pairs"][0]["pairKey"]
        mapped = self.invoke(job, "analysis", {**args, "mode":"mapped", "dimension":"sku", "pairKey":pair})
        budget = self.invoke(job, "budget", args)
        self.assertEqual(mapped["mode"], "mapped")
        self.assertEqual(mapped["selector"]["pairKey"], pair)
        self.assertEqual(budget["reference"]["budgetRef"], snapshot["budgetRef"])
        self.assertGreaterEqual(budget["budget"]["pagination"]["returned"], 1)
        for operation, params in (("package", {**args, "role":"commerce"}),
                ("package", {**args, "role":"promotion", "offset":True}),
                ("analysis", {**args, "mode":"native", "dimension":"sku", "sourceKey":"missing"}),
                ("analysis", {**args, "mode":"native", "dimension":"sku", "sourceKey":"ads", "baselineKey":"sales"}),
                ("analysis", {**args, "mode":"mapped", "dimension":"sku", "pairKey":"0"*64}),
                ("analysis", {**args, "mode":"mapped", "dimension":"category", "pairKey":pair}),
                ("analysis", {**args, "mode":"mapped", "dimension":"sku", "pairKey":{}}),
                ("analysis", {**args, "mode":"wrong", "dimension":"sku"}),
                ("budget", {**args, "screeningId":"other"}),
                ("budget", {**args, "offset":100}),
                ("analysis", {**args, "mode":"native", "dimension":"sku", "sourceKey":"ads", "extra":1})):
            with self.subTest(operation=operation, params=params), self.assertRaises(AiError):
                self.invoke(job, operation, params)

    def test_budget_absent_and_late_revocation_fail_without_a_page(self):
        from access_control.models import AppUser
        report = self.create_fixed_report()
        job = self.actual_job(report, role="promotion")
        args = self.base(report)
        with self.assertRaises(AiError): self.invoke(job, "budget", args)
        original = service.old_format._table_page
        def revoked(*parts):
            value = original(*parts)
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return value
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog), patch.object(
                service.old_format, "_table_page", side_effect=revoked), self.assertRaises(AiError):
            service.read(job.id, "analysis", {**args, "mode":"native", "dimension":"sku", "sourceKey":"ads"}, self.admin)
