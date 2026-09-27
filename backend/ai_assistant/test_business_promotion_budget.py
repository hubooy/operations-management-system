"""Actual sealed new-profile budgets; no provider or dispatch authority."""
import json
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from . import business_promotion_budget as service
from . import business_promotion_creation as creation
from . import test_business_promotion_creation as fixtures
from .policy import AiError, canonical, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionBudgetTests(djtest.TransactionTestCase):
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

    def make_report(self, *, budget):
        with patch.object(creation.transport, "catalog", side_effect=self.current_catalog):
            result = creation.create(self.request_body(budget=budget), self.admin)
        return result["item"]["id"]

    def page(self, report_id, principal=None, *, offset=0):
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.read_page(report_id, principal or self.admin, offset=offset)

    def test_budget_pages_are_complete_read_only_and_bound_to_new_workflow(self):
        report_id = self.make_report(budget=True)
        pages, offset = [], 0
        with patch("ai_assistant.provider.turn", side_effect=AssertionError("paid call")), patch(
                "ai_assistant.transport.execute_tool", side_effect=AssertionError("tool call")), CaptureQueriesContext(connection) as queries:
            while True:
                result = self.page(report_id, offset=offset)
                self.assertEqual(result["schemaVersion"], service.ENVELOPE_SCHEMA)
                self.assertEqual(result["schemaVersion"], "business-screening-budget-v1")
                self.assertEqual(result["budget"]["schemaVersion"], service.budget_reference.PAGE_SCHEMA)
                self.assertEqual(result["pageDigest"], digest({key: value for key, value in result.items() if key != "pageDigest"}))
                self.assertEqual(result["budget"]["pageDigest"], digest({key: value for key, value in result["budget"].items() if key != "pageDigest"}))
                self.assertEqual(result["reference"]["promotionRef"]["promotionSelector"]["sourceKey"], "ads")
                self.assertLessEqual(len(canonical(result).encode()), service.MAX_RESPONSE_BYTES)
                pages.append(result)
                offset = result["budget"]["pagination"]["nextOffset"]
                if offset is None:
                    break
                self.assertLess(len(pages), 10)
        self.assertEqual(sum(len(page["budget"]["rows"]) for page in pages), pages[0]["budget"]["pagination"]["total"])
        self.assertTrue(queries.captured_queries)
        for query in queries:
            self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")))

    def test_missing_budget_wrong_actor_and_out_of_range_reject(self):
        without = self.make_report(budget=False)
        with self.assertRaises(AiError): self.page(without)
        report_id = self.make_report(budget=True)
        with self.assertRaises(AiError): self.page(report_id, self.viewer)
        outsider = self.user("other-promotion-budget@example.invalid", "admin", None)
        with self.assertRaises(AiError): self.page(report_id, outsider)
        for invalid in (-1, 100, 1.0, True):
            with self.subTest(offset=invalid), self.assertRaises(AiError):
                self.page(report_id, offset=invalid)
        with self.assertRaises(AiError): self.page(report_id, offset=99)

    def test_late_revalidation_rejects_changed_budget_result(self):
        report_id = self.make_report(budget=True)
        original = service._roots
        calls = 0
        def changed(*args):
            nonlocal calls
            calls += 1
            result = original(*args)
            if calls == 2:
                result["rowDigest"] = "0" * 64
            return result
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog), patch.object(
                service, "_roots", side_effect=changed), self.assertRaises(AiError):
            service.read_page(report_id, self.admin)
        self.assertEqual(calls, 2)
