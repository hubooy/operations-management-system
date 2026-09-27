"""New profile creation tests; PostgreSQL writer cases require migration 0026."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from django import test as djtest
from django.db import connection

from . import business_promotion_creation as service
from . import business_promotion_runtime_contract as contract
from . import models as m, workflows
from . import test_business_promotion_creation_contract as shape_fixtures
from . import test_business_screening_creation as fixtures
from .policy import AiError, canonical


class CreationInputTests(unittest.TestCase):
    def test_generated_ids_and_strict_input(self):
        actor = SimpleNamespace(email="owner@example.invalid", scope=None)
        body = {"clientRequestId":"request-1", "evidenceRunId":"run-1",
            "question":"词货诊断", "sourceKey":"ads"}
        self.assertEqual(service._request(body, "report-1", "screen-1"),
            {"reportId":"report-1", "screeningId":"screen-1", "question":"词货诊断", "sourceKey":"ads"})
        with patch.object(service, "evidence_service") as evidence:
            evidence.principal_key.return_value = "principal-key"
            self.assertEqual(service._body({**body, "expectedPrincipalKey":"principal-key"}, actor),
                {**body, "expectedPrincipalKey":"principal-key"})
            for changed in ({**body, "reportId":"forged"}, {**body, "screeningId":"forged"},
                    {**body, "executionProfile":contract.PROFILE}, {**body, "dryRun":True},
                    {**body, "expectedPrincipalKey":"wrong"}):
                with self.subTest(changed=changed), self.assertRaises(AiError):
                    service._body(changed, actor)
        with patch.object(service, "connection", SimpleNamespace(in_atomic_block=True)), self.assertRaises(AiError):
            service.create(body, actor)


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionCreationTests(djtest.TransactionTestCase):
    user = fixtures.ScreeningCreationTests.user
    call = fixtures.ScreeningCreationTests.call
    collect_body = fixtures.ScreeningCreationTests.collect_body
    bundle = fixtures.ScreeningCreationTests.bundle
    input_for = fixtures.ScreeningCreationTests.input_for
    insert = fixtures.ScreeningCreationTests.insert
    seed = fixtures.ScreeningCreationTests.seed
    setUp = fixtures.ScreeningCreationTests.setUp

    def request_body(self, *, budget=False, mapping=False):
        self.serial += 1
        result = {"clientRequestId":"promotion-create-"+str(self.serial),
            "evidenceRunId":self.parent.id, "question":"词货分析", "sourceKey":"ads",
            "expectedPrincipalKey":service.evidence_service.principal_key(self.admin)}
        if budget: result["budgetPlan"] = deepcopy(self.budget_plan)
        if mapping: result["mappingPairs"] = [{"salesKey":"sales", "masterKey":"master"}]
        return result

    @staticmethod
    def counts():
        return tuple(model.objects.count() for model in (m.AiWorkflowRuns, m.AiWorkflowNodeRuns,
            m.AiReportRun, m.AiBusinessBudgetPlan, m.AiWorkflowEvents, m.AiBusinessScreeningRun))

    def current_catalog(self, *_):
        self.assertFalse(connection.in_atomic_block, "central catalog retrieval under mutation lock")
        entries = shape_fixtures.catalog()
        self.assertIsInstance(entries, list)
        self.assertEqual(len(entries), 4)
        return entries

    def test_real_queued_creation_and_replay_without_model_dispatch(self):
        body = self.request_body(mapping=True, budget=True)
        with patch.object(service.transport, "catalog", side_effect=self.current_catalog), patch(
                "ai_assistant.provider.turn", side_effect=AssertionError("no paid model call")):
            result = service.create(body, self.admin)
            self.assertFalse(result["replayed"])
            row = m.AiReportRun.objects.select_related("workflow").get(pk=result["item"]["id"])
            snapshot = json.loads(row.snapshot_json)
            self.assertEqual(snapshot["executionProfile"], contract.PROFILE)
            self.assertEqual(snapshot["promotionSelector"],
                {"sourceKey":"ads", "views":["keyword_sku", "keyword_sku_context"]})
            self.assertEqual(row.workflow.status, "queued")
            self.assertEqual(row.workflow.input_json,
                canonical({**json.loads(row.workflow.input_json)}))
            self.assertEqual(row.workflow.allowed_tools_json, canonical(list(contract.TOOL_ORDER)))
            self.assertEqual(m.AiWorkflowNodeRuns.objects.filter(run=row.workflow).count(), 6)
            self.assertEqual(m.AiBusinessScreeningRun.objects.filter(report=row).count(), 0)
            self.assertTrue(row.budget_plan_id)
            before = self.counts()
            replay = service.create(body, self.admin)
            self.assertTrue(replay["replayed"])
            self.assertEqual(replay["item"], result["item"])
            self.assertEqual(self.counts(), before)
            with self.assertRaises(AiError): service.create({**body, "question":"别的请求"}, self.admin)

    def test_late_failure_rolls_back_every_new_row_and_retry_stays_explicit(self):
        body = self.request_body(mapping=True, budget=True)
        before = self.counts()
        with patch.object(service.transport, "catalog", side_effect=self.current_catalog), patch.object(
                workflows, "event", side_effect=RuntimeError("publication interrupted")), self.assertRaises(RuntimeError):
            service.create(body, self.admin)
        self.assertEqual(self.counts(), before)
        with patch.object(service.transport, "catalog", side_effect=self.current_catalog):
            result = service.create(body, self.admin)
        self.assertFalse(result["replayed"])

    def test_wrong_actor_source_catalog_model_and_quota_leave_no_new_report(self):
        body = self.request_body()
        before = self.counts()
        with patch.object(service.transport, "catalog", side_effect=self.current_catalog):
            for changed in ({**body, "sourceKey":"sales"}, {**body, "sourceKey":"missing"}):
                with self.assertRaises(AiError): service.create(changed, self.admin)
            outsider = self.user("other-promotion-create@example.invalid", "admin", None)
            with self.assertRaises(AiError): service.create(body, outsider)
        altered = shape_fixtures.catalog(); altered.pop()
        with patch.object(service.transport, "catalog", return_value=altered), self.assertRaises(AiError):
            service.create(body, self.admin)
        self.assertEqual(self.counts(), before)
