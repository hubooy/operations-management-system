"""Real owning-reader -> sealed screening -> immutable publication tests."""
from copy import deepcopy
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from . import business_diagnostic_screening as screening, business_screening_store as store, models as m
from . import test_business_diagnostic_screening as fixtures
from .policy import AiError, canonical


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class ScreeningStoreTests(djtest.TransactionTestCase):
    user = fixtures.DiagnosticScreeningTests.user
    call = fixtures.DiagnosticScreeningTests.call
    collect_body = fixtures.DiagnosticScreeningTests.collect_body
    bundle = fixtures.DiagnosticScreeningTests.bundle
    input_for = fixtures.DiagnosticScreeningTests.input_for
    insert = fixtures.DiagnosticScreeningTests.insert
    seed = fixtures.DiagnosticScreeningTests.seed
    setUp = fixtures.DiagnosticScreeningTests.setUp

    def prepared(self):
        return screening.prepare_for_report(self.report.id,self.admin)

    def test_real_publication_replays_and_all_pages_match_without_fact_rescan(self):
        verified = self.prepared()
        with patch.object(screening.Reader,"pages",side_effect=AssertionError("published page must not scan facts")), patch(
                "ai_assistant.provider.turn") as model, CaptureQueriesContext(connection) as queries:
            first = store.publish(verified,self.admin)
            second = store.publish(verified,self.admin)
            self.assertFalse(first["replayed"]); self.assertTrue(second["replayed"])
            self.assertEqual(first["reference"],second["reference"])
            saved = store.describe(first["reference"]["id"],self.admin)
            for group in saved["manifest"]["groups"]:
                offset = 0
                while True:
                    if group["kind"] == "coverage":
                        page = store.read_coverage(first["reference"]["id"],self.admin,offset=offset)
                        original = screening.coverage_page(verified,self.admin,offset=offset)
                    else:
                        page = store.read_candidates(first["reference"]["id"],self.admin,group["partitionKey"],offset=offset)
                        original = screening.candidate_page(verified,self.admin,group["partitionKey"],offset=offset)
                    self.assertEqual(page,original)
                    offset = page["pagination"]["nextOffset"]
                    if offset is None: break
        self.assertEqual(m.AiBusinessScreeningRun.objects.count(),1)
        self.assertEqual(m.AiMutationAudit.objects.filter(action="business_screening_published").count(),1)
        self.assertFalse(any("netshop_rows" in q["sql"].lower() or "sales_order_lines" in q["sql"].lower() for q in queries))
        model.assert_not_called()

    def test_json_cannot_publish_and_audit_failure_rolls_back_every_row(self):
        with self.assertRaises(AiError): store.publish({},self.admin)
        verified = self.prepared()
        with patch.object(store.AiMutationAudit.objects,"create",side_effect=RuntimeError("synthetic audit unavailable")), self.assertRaises(RuntimeError):
            store.publish(verified,self.admin)
        self.assertEqual(m.AiBusinessScreeningRun.objects.count(),0)
        self.assertEqual(m.AiBusinessScreeningPage.objects.count(),0)
        self.assertFalse(store.publish(verified,self.admin)["replayed"])

    def test_live_cross_owner_scope_disabled_account_and_late_revocation(self):
        verified = self.prepared(); reference = store.publish(verified,self.admin)["reference"]
        other = self.user("store-other@example.invalid","admin",None)
        for actor in (other,self.viewer):
            with self.assertRaises(AiError): store.describe(reference["id"],actor)
            with self.assertRaises(AiError): store.read_coverage(reference["id"],actor)
        from sales.auth import Principal
        scoped = Principal(self.admin.email,"scope","admin",{"shops":["other"]})
        with self.assertRaises(AiError): store.read_coverage(reference["id"],scoped)
        with patch.object(screening,"_revalidate",side_effect=AiError("late revoked")), self.assertRaises(AiError):
            store.read_coverage(reference["id"],self.admin)
        from access_control.models import AppUser
        AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with self.assertRaises(AiError): store.read_coverage(reference["id"],self.admin)

    def test_mutated_selector_cannot_substitute_saved_result_or_new_publish(self):
        verified = self.prepared(); reference = store.publish(verified,self.admin)["reference"]
        describe = screening._describe
        def altered(loaded):
            response = deepcopy(describe(loaded)); response["plan"]["planDigest"] = "0"*64
            return response
        with patch.object(screening,"_describe",altered):
            with self.assertRaises(AiError): store.read_coverage(reference["id"],self.admin)
            with self.assertRaises(AiError): store.publish(verified,self.admin)
        self.assertEqual(m.AiBusinessScreeningRun.objects.count(),1)

    def test_quota_and_invalid_fixed_offset_fail_without_partial_publish(self):
        verified = self.prepared()
        with patch.object(store.contract,"OWNER_ROWS",0), self.assertRaises(AiError): store.publish(verified,self.admin)
        self.assertFalse(m.AiBusinessScreeningRun.objects.exists())
        reference = store.publish(verified,self.admin)["reference"]
        for offset in (True,1.0,"0",-1,1000000,10**100):
            with self.subTest(offset=offset), self.assertRaises(AiError): store.read_coverage(reference["id"],self.admin,offset=offset)
        with self.assertRaises(AiError): store.read_candidates(reference["id"],self.admin,"unknown")

    def test_mutated_payload_is_rejected_even_when_orm_object_is_not_actual_row(self):
        verified = self.prepared(); reference = store.publish(verified,self.admin)["reference"]
        original = store._record
        def changed(row):
            value = original(row); value["payloadJson"] = canonical({"untrusted":"changed"})
            value["payloadDigest"] = store.contract.raw_digest(value["payloadJson"])
            return value
        with patch.object(store,"_record",changed), self.assertRaises(AiError): store.read_coverage(reference["id"],self.admin)
