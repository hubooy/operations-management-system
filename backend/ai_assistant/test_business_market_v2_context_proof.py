"""Real-role isolated PG tests for SQL-derived market context authority."""
from contextlib import contextmanager
from unittest.mock import patch

from django import test as djtest
from django.db import DatabaseError, connection, transaction

from access_control.models import AppUser
from . import business_market_v2_context_attestation as service
from .business_market_v2_context_catalog import verify as verify_catalog
from . import business_market_v2_execution_snapshot as plan_service
from . import models as m
from . import test_business_market_v2_execution_snapshot as fixture
from .test_business_market_v2_material_role_bridge import session_role
from .policy import AiError


@contextmanager
def context_attestor():
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION AUTHORIZATION teruisi_ai_market_context_attestor")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2ContextProofTests(djtest.TransactionTestCase):
    user = fixture.MarketV2ExecutionSnapshotTests.user
    request_body = fixture.MarketV2ExecutionSnapshotTests.request_body
    current_catalog = fixture.MarketV2ExecutionSnapshotTests.current_catalog
    create_fixed_report = fixture.MarketV2ExecutionSnapshotTests.create_fixed_report
    planned_evidence_body = fixture.MarketV2ExecutionSnapshotTests.planned_evidence_body
    selector = fixture.MarketV2ExecutionSnapshotTests.selector
    parked_id = fixture.MarketV2ExecutionSnapshotTests.parked_id
    _attest_as_role = staticmethod(fixture.MarketV2ExecutionSnapshotTests._attest_as_role)
    admitted = fixture.MarketV2ExecutionSnapshotTests.admitted
    setUp = fixture.MarketV2ExecutionSnapshotTests.setUp
    body = fixture.MarketV2ExecutionSnapshotTests.body
    create_plan = fixture.MarketV2ExecutionSnapshotTests.create_plan

    def attested(self, report_id):
        with context_attestor():
            return service.attest(report_id)

    def test_independent_attestor_and_reader_get_sql_derived_bounded_receipt(self):
        with connection.cursor() as cursor:
            verify_catalog(cursor)
        _, admitted, created = self.create_plan()
        report_id = created["reportId"]
        with self.assertRaises(AiError):
            service.attest(report_id)
        first = self.attested(report_id)
        self.assertEqual(first, self.attested(report_id))
        self.assertEqual(m.AiBusinessMarketV2ContextProof.objects.count(), 1)
        with session_role("teruisi_ai_reader"):
            value = service.read(report_id, self.admin)
            self.assertEqual(value["reportId"], report_id)
            self.assertEqual(value["admittedReportId"], admitted["reportId"])
            self.assertEqual(value["proofDigest"], first["proofDigest"])
            self.assertTrue(value["proofPersisted"])
            self.assertFalse(value["agentReadPersisted"])
            self.assertFalse(value["executionReady"])
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT proof_digest FROM public."
                        "ai_business_market_v2_context_proofs LIMIT 1")
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT public.ai_market_v2_attest_context(%s)",
                        [report_id])
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT public.ai_market_v2_context_claim(%s)",
                        [report_id])
        with session_role("teruisi_ai_writer"):
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT public.ai_market_v2_context_receipt(%s,%s,%s)",
                        [report_id, self.admin.email.lower(), 1])
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT proof_digest FROM public."
                        "ai_business_market_v2_context_proofs LIMIT 1")
        self.assertFalse(m.AiAgentJobs.objects.filter(
            workflow_run_id=created["workflowId"]).exists())

    def test_claimed_sha_cross_account_and_direct_writes_fail(self):
        _, admitted, _, _ = self.admitted()
        original = plan_service._root
        def forged(*args, **kwargs):
            value = original(*args, **kwargs)
            value["marketContextDigest"] = "f"*64
            return value
        with patch.object(plan_service, "_root", side_effect=forged), patch.object(
                plan_service.transport, "catalog", return_value=fixture.frozen_catalog()):
            created = plan_service.create(self.body(admitted["reportId"],
                "forged-context-sha"), self.admin)
        with context_attestor():
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_context_digest_mismatch"):
                service.attest(created["reportId"])
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO public.ai_business_market_v2_context_proofs "
                        "(execution_report_id,proof_json,proof_digest,recorded_at) "
                        "VALUES (%s,'{}',%s,now())",
                        [created["reportId"], "a"*64])
        self.assertEqual(m.AiBusinessMarketV2ContextProof.objects.count(), 0)

        _, _, good = self.create_plan()
        self.attested(good["reportId"])
        outside = self.user("context-outside@example.invalid", "admin", None)
        version = AppUser.objects.get(email=outside.email).version
        actual_version = AppUser.objects.get(email=self.admin.email).version
        with session_role("teruisi_ai_reader"):
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_context_receipt_mismatch"):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT public.ai_market_v2_context_receipt(%s,%s,%s)",
                        [good["reportId"], outside.email.lower(), version])
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_context_receipt_mismatch"):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT public.ai_market_v2_context_receipt(%s,%s,%s)",
                        [good["reportId"], self.admin.email.lower(),
                         actual_version + 1])
