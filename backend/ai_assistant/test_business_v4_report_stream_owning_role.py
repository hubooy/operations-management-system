"""Isolated PostgreSQL target for real sealed-v4 ORM source pages."""
from django.db import connection
from django.test import TransactionTestCase, override_settings
import psycopg
from business_analysis import evidence_seal_v4
from business_analysis.v4_final_commit_step import commit_claimed_seal
from . import business_v4_report_stream_owning_candidate as owning
from . import business_v4_seal_hmac as seal_hmac
from . import business_v4_seal_verify as verifier
from . import models as m
from .policy import AiError
from .test_business_v4_final_commit_step import BusinessV4FinalCommitStepTests as role_fixture

@override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class V4ReportOwnerRealRoleTarget(TransactionTestCase):
    """Run only on the parent's isolated PG harness; no production writes."""
    prior = role_fixture
    promotion_owner = prior.promotion_owner
    rebuild_plan = prior.rebuild_plan
    finance_owner = prior.finance_owner
    owner = prior.owner
    collect = prior.collect
    complete_mixed = prior.complete_mixed
    attempt = prior.attempt
    database = prior.database
    _identity = prior._identity
    _role_connection = prior._role_connection
    claim = prior.claim
    body = prior.body
    _prepare = prior._prepare
    setUp = prior.setUp
    tearDown = prior.tearDown

    def test_actual_sealed_page_and_tool_receipt_are_read_without_report_grant(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated PostgreSQL v4 seal roles")
        prepared = self._prepare()
        attempt_id, actor, _, nonce, claim, raw, signature, request = prepared
        with self._role_connection("teruisi_ai_seal_writer") as db:
            commit_claimed_seal(db, seal_hmac._key()[0],
                run_id=self.parent.id, attempt_id=attempt_id,
                actor_email=actor.email, actor_version=actor.version,
                nonce=nonce, claim=claim,
                issued_request_digest=request,
                canonical_body=raw, body_mac=signature["bodyMac"],
                enabled=True)
            db.execute("COMMIT")
        verified = verifier.verify_seal(self.parent.id, self.principal)
        owner, parent, sources, _ = verifier._directory(self.parent.id,
            self.principal)
        source = next(item for item in sources if item.source_key ==
            "promotion-current")
        envelopes = list(owning._pages(parent, source, owner))
        self.assertEqual(len(envelopes), source.page_count)
        self.assertEqual(sum(item["rowCount"] for item in envelopes),
            source.row_count)
        self.assertEqual(sum(len(item["payloadJson"].encode("utf-8"))
            for item in envelopes), source.stored_bytes)
        self.assertEqual([item["sequence"] for item in envelopes],
            list(range(1, source.page_count + 1)))
        self.assertTrue(all(item["payloadDigest"] ==
            item["receiptResponseDigest"] == item["auditResponseDigest"]
            for item in envelopes))
        seal = m.AiBusinessV4Seal.objects.get(run_id=parent.id)
        body = evidence_seal_v4.read(seal.body_json)
        seal_item = next(item for item in body["sources"] if item[
            "sourceKey"] == source.source_key)
        manifest = owning._source_manifest({"candidateDigest": "a" * 64},
            source, seal_item)
        self.assertEqual(manifest["receiptChainDigest"], seal_item[
            "receiptChainDigest"])
        self.assertEqual(verified["sealedDigest"], seal.body_digest)
        self.assertFalse(verified["reportGenerationSupported"])
        self.assertFalse(m.AiReportRun.objects.exists())
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_reader")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT payload_json FROM "
                    "public.ai_business_v4_chunks LIMIT 1")
        class Sink:
            def __init__(self): self.calls = []
            def abort(self): self.calls.append("abort")
            def stage(self, *args): self.calls.append("stage")
            def complete(self, *args): self.calls.append("complete")
        sink = Sink()
        with self.assertRaises(AiError):
            owning.stage_unbound("missing-report", parent.id, "current",
                self.principal, sink)
        self.assertEqual(sink.calls, [])
        with override_settings(DJANGO_PROCESS_ROLE="ai_reader"), \
                self.assertRaises(AiError):
            owning.stage_unbound("missing-report", parent.id, "current",
                self.principal, sink, enabled=True)
        self.assertEqual(sink.calls, [])
