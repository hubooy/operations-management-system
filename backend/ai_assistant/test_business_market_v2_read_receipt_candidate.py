"""Isolated PG negative probes: no market read without a real model chain."""
from contextlib import contextmanager

from django import test as djtest
from django.db import DatabaseError, connection, transaction

from . import business_market_v2_read_attestation as service
from . import models as m
from . import test_business_market_v2_context_proof as fixture
from .business_market_v2_read_catalog import verify as verify_catalog
from .test_business_market_v2_material_role_bridge import session_role
from .policy import AiError


@contextmanager
def read_attestor():
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION AUTHORIZATION teruisi_ai_market_read_attestor")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2ReadReceiptCandidateTests(djtest.TransactionTestCase):
    user = fixture.MarketV2ContextProofTests.user
    request_body = fixture.MarketV2ContextProofTests.request_body
    current_catalog = fixture.MarketV2ContextProofTests.current_catalog
    create_fixed_report = fixture.MarketV2ContextProofTests.create_fixed_report
    planned_evidence_body = fixture.MarketV2ContextProofTests.planned_evidence_body
    selector = fixture.MarketV2ContextProofTests.selector
    parked_id = fixture.MarketV2ContextProofTests.parked_id
    _attest_as_role = staticmethod(fixture.MarketV2ContextProofTests._attest_as_role)
    admitted = fixture.MarketV2ContextProofTests.admitted
    setUp = fixture.MarketV2ContextProofTests.setUp
    body = fixture.MarketV2ContextProofTests.body
    create_plan = fixture.MarketV2ContextProofTests.create_plan
    attested = fixture.MarketV2ContextProofTests.attested

    def test_genuine_context_does_not_fabricate_job_provider_or_read(self):
        with connection.cursor() as cursor:
            verify_catalog(cursor)
        _, _, created = self.create_plan()
        context = self.attested(created["reportId"])
        self.assertTrue(context["proofPersisted"])
        self.assertFalse(context["agentReadPersisted"])
        self.assertFalse(m.AiAgentJobs.objects.filter(
            workflow_run_id=created["workflowId"]).exists())
        with self.assertRaises(AiError):
            service.attest("claimed-tool-1")
        with read_attestor():
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_read_dispatch_missing"):
                service.attest("claimed-tool-1")
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT receipt_digest FROM public."
                        "ai_business_market_v2_read_receipts LIMIT 1")
        with session_role("teruisi_ai_reader"):
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_read_dispatch_missing"):
                service.read("claimed-tool-1", self.admin)
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT public.ai_market_v2_read_claim(%s)",
                        ["claimed-tool-1"])
        with session_role("teruisi_ai_writer"):
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT receipt_digest FROM public."
                        "ai_business_market_v2_read_receipts LIMIT 1")
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT public.ai_market_v2_attest_read(%s)",
                        ["claimed-tool-1"])
        self.assertEqual(m.AiBusinessMarketV2ReadReceipt.objects.count(), 0)

    def test_old_paused_root_cannot_gain_job_for_a_receipt(self):
        _, _, created = self.create_plan()
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiAgentJobs.objects.create(id="market-v2-fake-read-job",
                owner_email=self.admin.email.lower(), scope_json="null",
                client_request_id="market-v2-fake-read-job",
                request_digest="a"*64, task="not-a-model-call",
                workflow_run_id=created["workflowId"],
                workflow_node_key="market_b2b", status="paused", phase="paused")
        self.assertEqual(m.AiBusinessMarketV2ReadReceipt.objects.count(), 0)
