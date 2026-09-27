"""Complete four-tool required-content capacity, without runtime authority."""
from copy import deepcopy
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from . import business_diagnostic_screening as screening
from . import business_promotion_creation as creation
from . import business_promotion_preflight as service
from . import business_screening_store as store, models as m
from . import test_business_promotion_admission as catalog_fixtures
from . import test_business_promotion_creation as fixtures
from . import test_business_screening_preflight as model_fixtures
from .policy import AiError, canonical, mutation


class PromotionPreflightPureTests(unittest.TestCase):
    def test_four_tool_catalog_and_promotion_ref_enter_both_context_formats(self):
        roles = {role: [{"pagination": {"offset": 0}, "role": role,
            "data": "完整页"}] for role in service.screening_package.ROLES}
        reference = {"reportId": "report-1", "evidenceRunId": "run-1",
            "screeningIntent": {"id": "screen-1"},
            "promotionRef": {"promotionSelector": {"sourceKey": "ads",
                "views": ["keyword_sku", "keyword_sku_context"]}}}
        model = model_fixtures.model()
        entries = catalog_fixtures.catalog()
        measured = service._measure(roles, [], reference, model, "", entries)
        self.assertTrue(measured["fits"])
        self.assertEqual(len(measured["nodes"]), 6)
        self.assertEqual(set(measured["nodes"][0]["protocols"]), {"openai_compatible", "anthropic"})
        self.assertEqual(measured["nodes"][0]["packagePages"], 1)
        self.assertEqual(measured["nodes"][0]["requiredToolCalls"], 1)
        self.assertEqual(measured["nodes"][0]["promotionPagesReserved"], 0)
        self.assertEqual(measured["nodes"][0]["optionalAnalysisPagesReserved"], 0)
        with patch.object(service.model_capabilities, "fit_context", wraps=service.model_capabilities.fit_context) as fit:
            service._measure(roles, [], reference, model, "", entries)
        self.assertTrue(fit.called)
        for call in fit.call_args_list:
            self.assertEqual(len(call.args[3]), 4)
            self.assertIn("promotionRef", call.args[1][0]["content"])
            if call.args[0].protocol == "anthropic":
                self.assertEqual(call.args[1][1]["content"][0]["type"], "tool_use")
            else:
                self.assertEqual(call.args[1][1]["tool_calls"][0]["type"], "function")
        tight = SimpleNamespace(**{**vars(model), "max_tokens": 7000,
            "generation_options_json": canonical({"contextWindowTokens": 8192})})
        self.assertFalse(service._measure(roles, [], reference, tight, "", entries)["fits"])

    def test_public_proof_cannot_be_recovered_as_internal_candidate(self):
        for value in ({}, "{}", SimpleNamespace()):
            with self.assertRaises(AiError): service.revalidate(value, None)
        with self.assertRaises(AiError): service.PreparedCapacity(None, "report-1", "owner@example.invalid", {}, {})
        candidate = service.PreparedCapacity(service._TOKEN, "report-1", "owner@example.invalid", {}, {"x": 1})
        candidate.proof["x"] = 2
        self.assertEqual(candidate.proof["x"], 1)
        with self.assertRaises(AttributeError): candidate._proof_json = "{}"
        object.__setattr__(candidate, "_proof_json", "{}")
        with self.assertRaises(AiError): _ = candidate.proof


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionPreflightPersistedTests(djtest.TransactionTestCase):
    user = fixtures.PromotionCreationTests.user
    call = fixtures.PromotionCreationTests.call
    collect_body = fixtures.PromotionCreationTests.collect_body
    bundle = fixtures.PromotionCreationTests.bundle
    input_for = fixtures.PromotionCreationTests.input_for
    insert = fixtures.PromotionCreationTests.insert
    seed = fixtures.PromotionCreationTests.seed
    setUp = fixtures.PromotionCreationTests.setUp
    request_body = fixtures.PromotionCreationTests.request_body

    def current_catalog(self, *_):
        self.assertFalse(connection.in_atomic_block)
        return catalog_fixtures.catalog()

    def create_published(self, *, budget=False):
        with mutation(self.admin):
            m.AiModels.objects.filter(pk=self.model.pk).update(
                max_tool_rounds=20, max_total_tool_calls=40)
        with patch.object(creation.transport, "catalog", side_effect=self.current_catalog):
            created = creation.create(self.request_body(budget=budget), self.admin)
        report = m.AiReportRun.objects.select_related("workflow").get(pk=created["item"]["id"])
        store.publish(screening.prepare_for_report(report.id, self.admin), self.admin)
        return report

    def test_actual_published_five_roles_with_and_without_budget_have_closed_proof(self):
        for with_budget in (False, True):
            report = self.create_published(budget=with_budget)
            with patch.object(service.transport, "catalog", side_effect=self.current_catalog), patch(
                    "ai_assistant.provider.turn", side_effect=AssertionError("paid model call")), patch(
                    "ai_assistant.transport.execute_tool", side_effect=AssertionError("tool dispatch")), CaptureQueriesContext(connection) as queries:
                candidate = service.prepare(report.id, self.admin)
                proof = candidate.proof
                self.assertTrue(proof["capacityVerified"])
                self.assertFalse(proof["runtimeAdmissionGranted"])
                self.assertFalse(proof["modelDispatched"])
                self.assertFalse(proof["agentReadVerified"])
                self.assertTrue(proof["requiredContentOnly"])
                self.assertTrue(any("可选调用未预留" in item for item in proof["limitations"]))
                self.assertTrue(all(node["promotionPagesReserved"] == 0
                    for node in proof["measurements"]))
                self.assertEqual(len(proof["packageDigests"]), 5)
                self.assertEqual(len(proof["measurements"]), 6)
                self.assertEqual(proof["budgetPages"] > 0, with_budget)
                self.assertEqual(service.revalidate(candidate, self.admin), proof)
            for query in queries:
                self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")))

    def test_unpublished_and_wrong_actor_are_denied(self):
        with patch.object(creation.transport, "catalog", side_effect=self.current_catalog):
            created = creation.create(self.request_body(), self.admin)
        report_id = created["item"]["id"]
        with patch.object(service.transport, "catalog", side_effect=self.current_catalog):
            with self.assertRaises(AiError): service.prepare(report_id, self.admin)
        report = self.create_published()
        outsider = self.user("other-promotion-preflight@example.invalid", "admin", None)
        with patch.object(service.transport, "catalog", side_effect=self.current_catalog):
            with self.assertRaises(AiError): service.prepare(report.id, outsider)
