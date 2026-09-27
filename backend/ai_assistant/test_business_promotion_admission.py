"""The candidate checks live policy, but cannot authorize profile execution."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from . import business_promotion_admission as service
from . import business_promotion_creation as creation
from . import test_business_promotion_creation as fixtures
from . import test_business_promotion_creation_contract as catalog_fixtures
from .policy import AiError, canonical, digest


def catalog():
    """Exact current four schemas with harmless local descriptions."""
    result = catalog_fixtures.catalog()
    for entry in result:
        entry["inputSchema"] = service._schema(entry["name"])
        if entry["name"] == service.contract.PROMOTION_TOOL:
            entry["execution"]["maxResultCharacters"] = 38000
    return result


class PromotionAdmissionPolicyTests(unittest.TestCase):
    def test_all_four_tool_schemas_are_pinned_beyond_name_and_digest(self):
        entries = catalog()
        flow = SimpleNamespace(allowed_tools_json=canonical(list(service.contract.TOOL_ORDER)),
            tool_policy_digest=digest(entries))
        self.assertEqual(service._catalog(entries, flow), entries)
        changes = []
        for index in range(4):
            changed = deepcopy(entries)
            changed[index]["inputSchema"]["properties"]["reportId"]["pattern"] = "^.*$"
            changes.append(changed)
        changed = deepcopy(entries)
        changed[3]["execution"]["maxResultCharacters"] = 40000
        changes += [changed, list(reversed(entries)), entries[:3], entries + [entries[0]]]
        for changed in changes:
            with self.subTest(changed=[e["name"] for e in changed]), self.assertRaises(AiError):
                service._catalog(changed, SimpleNamespace(
                    allowed_tools_json=canonical([e["name"] for e in changed]),
                    tool_policy_digest=digest(changed)))

    def test_public_dict_and_forged_candidate_do_not_become_permission(self):
        for value in ({}, "{}", SimpleNamespace()):
            with self.assertRaises(AiError): service.require_permission(value, None)
        with self.assertRaises(AiError): service.Candidate(None, "report-1", "owner@example.invalid", {})
        candidate = service.Candidate(service._TOKEN, "report-1", "owner@example.invalid", {"value": 1})
        candidate.proof["value"] = 2
        self.assertEqual(candidate.proof["value"], 1)
        with self.assertRaises(AttributeError): candidate._fixed_json = "{}"
        object.__setattr__(candidate, "_fixed_json", "{}")
        with self.assertRaises(AiError): _ = candidate.proof

    def test_outer_transaction_refused_before_any_network_or_read(self):
        with patch.object(service, "connection", SimpleNamespace(in_atomic_block=True)), patch.object(
                service, "_current") as read:
            with self.assertRaises(AiError): service.inspect("report-1", None)
        read.assert_not_called()


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionAdmissionPersistedTests(djtest.TransactionTestCase):
    user = fixtures.PromotionCreationTests.user
    call = fixtures.PromotionCreationTests.call
    collect_body = fixtures.PromotionCreationTests.collect_body
    bundle = fixtures.PromotionCreationTests.bundle
    input_for = fixtures.PromotionCreationTests.input_for
    insert = fixtures.PromotionCreationTests.insert
    seed = fixtures.PromotionCreationTests.seed
    setUp = fixtures.PromotionCreationTests.setUp
    request_body = fixtures.PromotionCreationTests.request_body

    def make_report(self):
        with patch.object(creation.transport, "catalog", return_value=catalog()):
            result = creation.create(self.request_body(), self.admin)
        return result["item"]["id"]

    def test_real_queued_profile_can_only_form_a_closed_candidate(self):
        report_id = self.make_report()
        with patch.object(service.transport, "catalog", return_value=catalog()), patch(
                "ai_assistant.provider.turn", side_effect=AssertionError("paid call")), patch(
                "ai_assistant.transport.execute_tool", side_effect=AssertionError("tool call")), CaptureQueriesContext(connection) as queries:
            candidate = service.inspect(report_id, self.admin)
            proof = candidate.proof
            self.assertEqual(proof["reportId"], report_id)
            self.assertEqual(proof["schemaVersion"], service.SCHEMA)
            self.assertFalse(proof["contentReady"])
            self.assertFalse(proof["capacityVerified"])
            self.assertFalse(proof["runtimeAdmissionGranted"])
            self.assertEqual(service.revalidate(candidate, self.admin), proof)
            with self.assertRaises(AiError) as error: service.require_permission(candidate, self.admin)
            self.assertEqual(error.exception.code, "promotion_runtime_not_ready")
        self.assertTrue(queries.captured_queries)
        for query in queries:
            self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")))

    def test_actor_catalog_and_late_change_are_rejected(self):
        report_id = self.make_report()
        entries = catalog()
        with patch.object(service.transport, "catalog", return_value=entries):
            candidate = service.inspect(report_id, self.admin)
            outsider = self.user("other-promotion-admission@example.invalid", "admin", None)
            with self.assertRaises(AiError): service.revalidate(candidate, outsider)
        changed = deepcopy(entries)
        changed[3]["inputSchema"]["properties"]["view"]["enum"] = ["keyword_sku"]
        with patch.object(service.transport, "catalog", return_value=changed), self.assertRaises(AiError):
            service.revalidate(candidate, self.admin)
        with patch.object(service.transport, "catalog", return_value=entries):
            bound = service.runtime.bound_persisted(report_id, self.admin)
            with patch.object(service.runtime, "bound_persisted", return_value={**bound, "executionProfile": None}), self.assertRaises(AiError):
                service.inspect(report_id, self.admin)
            late = {**bound, "screeningStatus": "ready", "contentReady": True,
                    "screeningReference": {"schemaVersion": "late-publication"}}
            with patch.object(service.runtime, "bound_persisted", side_effect=[bound, late]), self.assertRaises(AiError):
                service.inspect(report_id, self.admin)
