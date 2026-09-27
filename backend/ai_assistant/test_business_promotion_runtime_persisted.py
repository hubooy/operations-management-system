"""Actual 0026 report roots; no provider dispatch or public runtime switch."""
from copy import deepcopy
import json
from unittest.mock import patch

from django import test as djtest
from django.db import connection, transaction, DatabaseError
from django.test.utils import CaptureQueriesContext

from . import business_promotion_creation as creation
from . import business_promotion_runtime as service
from . import models as m
from . import test_business_promotion_creation as fixtures
from .policy import AiError, canonical, digest, mutation


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionPersistedRuntimeTests(djtest.TransactionTestCase):
    user = fixtures.PromotionCreationTests.user
    call = fixtures.PromotionCreationTests.call
    collect_body = fixtures.PromotionCreationTests.collect_body
    bundle = fixtures.PromotionCreationTests.bundle
    input_for = fixtures.PromotionCreationTests.input_for
    insert = fixtures.PromotionCreationTests.insert
    seed = fixtures.PromotionCreationTests.seed
    setUp = fixtures.PromotionCreationTests.setUp
    request_body = fixtures.PromotionCreationTests.request_body
    current_catalog = fixtures.PromotionCreationTests.current_catalog

    def create_report(self, *, budget=False, mapping=False):
        body = self.request_body(budget=budget, mapping=mapping)
        with patch.object(creation.transport, "catalog", side_effect=self.current_catalog):
            result = creation.create(body, self.admin)
        return m.AiReportRun.objects.select_related("workflow", "budget_plan").get(pk=result["item"]["id"])

    def bound(self, report, principal=None):
        with patch.object(service.transport, "catalog", side_effect=self.current_catalog):
            return service.bound_persisted(report.id, principal or self.admin)

    def test_actual_persisted_report_and_optional_budget_are_prepared_not_ready(self):
        reports = [self.create_report(), self.create_report(budget=True, mapping=True)]
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            for report in reports:
                result = self.bound(report)
                self.assertEqual(result["reportId"], report.id)
                self.assertEqual(result["workflowId"], report.workflow_id)
                self.assertEqual(result["executionProfile"], service.contract.PROFILE)
                self.assertEqual(result["screeningStatus"], "prepared_but_not_ready")
                self.assertFalse(result["contentReady"])
                self.assertIsNone(result["screeningReference"])
                self.assertFalse(result["runtimeRegistered"])
                self.assertEqual(result["snapshotDigest"], digest(report.snapshot_json))
                self.assertEqual(result["workflowInputDigest"], digest(report.workflow.input_json))
                self.assertEqual(result["graphDigest"], report.workflow.graph_digest)
                self.assertEqual(result["rootBindings"]["evidenceRunId"], self.parent.id)
                self.assertGreater(result["rootBindings"]["sourceCount"], 0)
        model.assert_not_called(); remote.assert_not_called()
        self.assertTrue(queries.captured_queries)
        for query in queries:
            self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")))
            self.assertNotIn("netshop_rows", query["sql"].lower())
            self.assertNotIn("sales_order_lines", query["sql"].lower())

    def test_old_report_wrong_actor_and_changed_catalog_or_model_are_denied(self):
        report = self.create_report()
        with self.assertRaises(AiError): self.bound(self.report)
        with self.assertRaises(AiError): self.bound(report, self.viewer)
        outsider = self.user("other-promotion-bound@example.invalid", "admin", None)
        with self.assertRaises(AiError): self.bound(report, outsider)
        changed = fixtures.shape_fixtures.catalog()
        changed[0]["description"] = "changed central tool"
        with patch.object(service.transport, "catalog", return_value=changed), self.assertRaises(AiError):
            service.bound_persisted(report.id, self.admin)
        with mutation(self.admin):
            m.AiModels.objects.filter(pk=report.workflow.model_id).update(
                version=report.workflow.model_version + 1)
        with self.assertRaises(AiError): self.bound(report)

    def test_immutable_fields_and_late_revocation_never_return_ready(self):
        from access_control.models import AppUser
        report = self.create_report(budget=True, mapping=True)
        snapshot = json.loads(report.snapshot_json)
        snapshot["question"] = "篡改固定问题"
        reference = json.loads(report.workflow.input_json)
        reference["question"] = "篡改工作流输入"
        graph = json.loads(report.workflow.graph_json)
        graph["nodes"][0]["instruction"] += "篡改"
        for name, target, values in (("report snapshot", m.AiReportRun.objects.filter(pk=report.id),
                    {"snapshot_json":canonical(snapshot)}),
                ("workflow input", m.AiWorkflowRuns.objects.filter(pk=report.workflow_id),
                    {"input_json":canonical(reference)}),
                ("workflow graph", m.AiWorkflowRuns.objects.filter(pk=report.workflow_id),
                    {"graph_json":canonical(graph)})):
            with self.subTest(field=name), self.assertRaises(DatabaseError), transaction.atomic():
                target.update(**values)
        calls = 0
        entries = fixtures.shape_fixtures.catalog()
        def catalog(*_):
            nonlocal calls
            calls += 1
            if calls == 2:
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return deepcopy(entries)
        with patch.object(service.transport, "catalog", side_effect=catalog), self.assertRaises(AiError):
            service.bound_persisted(report.id, self.admin)
        self.assertEqual(calls, 2)
