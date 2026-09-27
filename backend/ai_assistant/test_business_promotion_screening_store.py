"""New profile screening publication uses real sealed rows and its own intent."""
from copy import deepcopy
import json
from unittest.mock import patch

from django import test as djtest

from . import business_diagnostic_screening as screening
from . import business_promotion_creation as creation
from . import business_promotion_runtime as promotion_runtime
from . import business_screening_store as store
from . import models as m
from . import test_business_promotion_creation as fixtures
from .policy import AiError, canonical


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionScreeningStoreTests(djtest.TransactionTestCase):
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
        with patch.object(creation.transport, "catalog", side_effect=self.current_catalog):
            result = creation.create(self.request_body(budget=budget, mapping=mapping), self.admin)
        return m.AiReportRun.objects.select_related("workflow").get(pk=result["item"]["id"])

    def test_real_new_profile_scan_publish_replay_and_page_chain(self):
        report = self.create_report(mapping=True)
        with patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote:
            preview = screening.describe_for_report(report.id, self.admin)
            self.assertTrue(preview["plan"]["canScreen"])
            verified = screening.prepare_for_report(report.id, self.admin)
            first = store.publish(verified, self.admin)
            second = store.publish(verified, self.admin)
            self.assertFalse(first["replayed"])
            self.assertTrue(second["replayed"])
            self.assertEqual(first["reference"], second["reference"])
            saved = store.describe(first["reference"]["id"], self.admin)
            self.assertEqual(saved["reference"]["reportId"], report.id)
            self.assertEqual(saved["reference"]["id"], json.loads(report.snapshot_json)["screeningIntent"]["id"])
            for group in saved["manifest"]["groups"]:
                offset = 0
                while True:
                    if group["kind"] == "coverage":
                        actual = store.read_coverage(saved["reference"]["id"], self.admin, offset=offset)
                        expected = screening.coverage_page(verified, self.admin, offset=offset)
                    else:
                        actual = store.read_candidates(saved["reference"]["id"], self.admin,
                            group["partitionKey"], offset=offset)
                        expected = screening.candidate_page(verified, self.admin,
                            group["partitionKey"], offset=offset)
                    self.assertEqual(actual, expected)
                    offset = actual["pagination"]["nextOffset"]
                    if offset is None:
                        break
            with patch.object(promotion_runtime.transport, "catalog", side_effect=self.current_catalog):
                bound = promotion_runtime.bound_persisted(report.id, self.admin)
            self.assertEqual(bound["screeningStatus"], "ready")
            self.assertTrue(bound["contentReady"])
            self.assertEqual(bound["screeningReference"], saved["reference"])
        model.assert_not_called()
        remote.assert_not_called()
        self.assertEqual(m.AiBusinessScreeningRun.objects.filter(report=report).count(), 1)

    def test_budget_path_is_metadata_only_and_wrong_actor_or_page_is_rejected(self):
        report = self.create_report(budget=True, mapping=True)
        with patch("ai_assistant.business_budget.resolve", side_effect=AssertionError("budget facts rescan")):
            verified = screening.prepare_for_report(report.id, self.admin)
            reference = store.publish(verified, self.admin)["reference"]
            store.describe(reference["id"], self.admin)
        other = self.user("other-promotion-scan@example.invalid", "admin", None)
        for actor in (other, self.viewer):
            with self.subTest(actor=actor.email), self.assertRaises(AiError):
                store.read_coverage(reference["id"], actor)
        original = store._record

        def changed(page):
            value = deepcopy(original(page))
            value["payloadJson"] = canonical({"untrusted": "changed"})
            value["payloadDigest"] = store.contract.raw_digest(value["payloadJson"])
            return value

        with patch.object(store, "_record", changed), self.assertRaises(AiError):
            store.read_coverage(reference["id"], self.admin)

    def test_existing_profile_binding_does_not_accept_new_report_intent(self):
        report = self.create_report()
        verified = screening.prepare_for_report(report.id, self.admin)
        original = screening._load

        def changed(report_id, principal):
            loaded = original(report_id, principal)
            binding = {**loaded[0], "executionProfile": "business-agent-screening-reference-v1"}
            return (binding, *loaded[1:])

        with patch.object(screening, "_load", changed), self.assertRaises(AiError):
            store.publish(verified, self.admin)
        self.assertFalse(m.AiBusinessScreeningRun.objects.filter(report=report).exists())
