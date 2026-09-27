"""PostgreSQL target checks for the claim-bound, non-authorizing replay ledger."""
import json
from importlib import import_module

import psycopg
from django.db import connection, transaction
from django.test import TransactionTestCase

from . import models as m
from .policy import canonical, digest
from .v4_replay_progress_catalog import verify as verify_catalog
from . import test_business_v4_seal_ticket as ticket_fixture


class BusinessV4SealerReplayProgressTests(TransactionTestCase):
    promotion_owner = ticket_fixture.BusinessV4SealTicketTests.promotion_owner
    rebuild_plan = ticket_fixture.BusinessV4SealTicketTests.rebuild_plan
    finance_owner = ticket_fixture.BusinessV4SealTicketTests.finance_owner
    owner = ticket_fixture.BusinessV4SealTicketTests.owner
    collect = ticket_fixture.BusinessV4SealTicketTests.collect
    complete_mixed = ticket_fixture.BusinessV4SealTicketTests.complete_mixed
    attempt = ticket_fixture.BusinessV4SealTicketTests.attempt
    database = ticket_fixture.BusinessV4SealTicketTests.database
    _identity = ticket_fixture.BusinessV4SealTicketTests._identity
    _role_connection = ticket_fixture.BusinessV4SealTicketTests._role_connection
    issue = ticket_fixture.BusinessV4SealTicketTests.issue
    claim = ticket_fixture.BusinessV4SealTicketTests.claim
    setUp = ticket_fixture.BusinessV4SealTicketTests.setUp
    tearDown = ticket_fixture.BusinessV4SealTicketTests.tearDown

    def test_frozen_catalog_and_closed_function_acl(self):
        latest = {"finance_enabled": True, "read_cast_enabled": True,
                  "prior_claim_qualified": True}
        with connection.cursor() as cursor:
            verify_catalog(cursor, **latest)
            verify_catalog(cursor, RuntimeError, **latest)
        migration = import_module(
            "ai_assistant.migrations.0047_business_v4_sealer_replay_progress")
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("REVOKE EXECUTE ON FUNCTION " + migration.WRITE +
                " FROM teruisi_ai_seal_writer")
            with self.assertRaisesRegex(ValueError, "function ACL drift"):
                verify_catalog(cursor, **latest)
            transaction.set_rollback(True)

    def _candidate(self, attempt_id, ticket_id, source, index=1):
        segment = m.AiBusinessV4ValidationSegment.objects.get(
            attempt_id=attempt_id, source=source, segment_index=index)
        attempt = m.AiBusinessV4ValidationAttempt.objects.get(pk=attempt_id)
        with connection.cursor() as cursor:
            cursor.execute("SELECT source_root FROM public.ai_business_v4_seal_tickets "
                "WHERE id=%s", [ticket_id])
            root = cursor.fetchone()[0]
        progress = json.loads(segment.progress_json)
        finite = {key: progress[key] for key in ("pageCount", "rowCount",
            "storedBytes", "lastChunkDigest", "receiptChainDigest")}
        finite["verifier"] = progress["domainState"]["verifier"]
        finite["observedDates"] = progress["domainState"]["observedDates"]
        body = {"schemaVersion":
            "business-v4-sealer-promotion-segment-candidate-v2",
            "runId": self.parent.id, "attemptId": attempt_id,
            "sourceId": source.id, "sourceRoot": root,
            "sourceKey": source.source_key, "sourceRef": source.source_ref,
            "sourceRevision": source.source_revision,
            "sourceVersion": source.version, "keyId": attempt.key_id,
            "segmentIndex": index, "endSequence": segment.end_sequence,
            "segmentProofDigest": segment.proof_digest, "progress": finite,
            "previousCandidateDigest": "0" * 64,
            "candidateOnly": True, "authorityVerified": False,
            "financeReplayed": False, "sourceMetadataVerified": False,
            "upstreamSignatureVerified": False, "sealCommitted": False}
        return {**body, "candidateDigest": digest(body)}

    def _record(self, attempt_id, source_id, candidate, nonce, token, *,
                index=1, role="teruisi_ai_seal_writer"):
        parent, _, actor = self._identity(attempt_id)
        with self._role_connection(role) as db:
            try:
                row = db.execute("SELECT * FROM "
                    "public.ai_v4_sealer_record_replay_progress(" +
                    ",".join(["%s"] * 9) + ")",
                    [parent.id, attempt_id, source_id, index, actor.email,
                     actor.version, nonce, token, canonical(candidate)]).fetchone()
                db.execute("COMMIT")
                return row
            except Exception:
                db.execute("ROLLBACK")
                raise

    def test_exact_single_segment_is_idempotent_and_still_unsealed(self):
        attempt_id = self.attempt()
        ticket_id, nonce, _ = self.issue(attempt_id)
        _, token, _ = self.claim(attempt_id, nonce)
        source = self.sources["promotion-current"]
        source.refresh_from_db()
        candidate = self._candidate(attempt_id, ticket_id, source)
        first = self._record(attempt_id, source.id, candidate, nonce, token)
        self.assertEqual(first[0], candidate["candidateDigest"])
        self.assertEqual(self._record(attempt_id, source.id, candidate,
            nonce, token), first)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM "
                "public.ai_business_v4_sealer_replay_progress")
            self.assertEqual(cursor.fetchone()[0], 1)
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).status,
            "collecting")
        self.assertFalse(m.AiBusinessV4Seal.objects.filter(run=self.parent).exists())
        migration = import_module(
            "ai_assistant.migrations.0047_business_v4_sealer_replay_progress")
        with connection.schema_editor() as editor:
            with self.assertRaisesRegex(RuntimeError, "cannot discard"):
                migration.uninstall(None, editor)

    def test_tamper_conflict_and_roles_are_closed(self):
        attempt_id = self.attempt()
        ticket_id, nonce, _ = self.issue(attempt_id)
        _, token, _ = self.claim(attempt_id, nonce)
        source = self.sources["promotion-current"]
        source.refresh_from_db()
        candidate = self._candidate(attempt_id, ticket_id, source)
        changed = json.loads(canonical(candidate))
        changed["progress"]["rowCount"] += 1
        changed["candidateDigest"] = digest({k: v for k, v in changed.items()
            if k != "candidateDigest"})
        with self.assertRaisesRegex(psycopg.Error, "progress_mismatch"):
            self._record(attempt_id, source.id, changed, nonce, token)
        wrong_chain = {**candidate, "previousCandidateDigest": "f" * 64}
        wrong_chain["candidateDigest"] = digest({k: v for k, v in
            wrong_chain.items() if k != "candidateDigest"})
        with self.assertRaisesRegex(psycopg.Error, "candidate_chain_invalid"):
            self._record(attempt_id, source.id, wrong_chain, nonce, token)
        missing_digest = {**candidate, "candidateDigest": None}
        with self.assertRaisesRegex(psycopg.Error, "candidate_invalid"):
            self._record(attempt_id, source.id, missing_digest, nonce, token)
        self._record(attempt_id, source.id, candidate, nonce, token)
        altered = {**candidate, "financeReplayed": True}
        altered["candidateDigest"] = digest({k: v for k, v in altered.items()
            if k != "candidateDigest"})
        with self.assertRaises(psycopg.Error):
            self._record(attempt_id, source.id, altered, nonce, token)
        for role in ("teruisi_ai_reader", "teruisi_ai_writer"):
            with connection.cursor() as cursor:
                cursor.execute("SELECT has_table_privilege(%s,"
                    "'public.ai_business_v4_sealer_replay_progress',"
                    "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE'),"
                    "has_function_privilege(%s,'public.ai_v4_sealer_record_replay_progress("
                    "text,text,text,integer,text,bigint,text,text,text)','EXECUTE')",
                    [role, role])
                self.assertEqual(cursor.fetchone(), (False, False))
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolcanlogin,has_table_privilege("
                "'teruisi_ai_seal_writer',"
                "'public.ai_business_v4_sealer_replay_progress',"
                "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE'),has_function_privilege("
                "'teruisi_ai_seal_writer','public.ai_v4_commit_seal("
                "text,text,bigint,text,text,text,text)','EXECUTE') "
                "FROM pg_catalog.pg_roles WHERE rolname='teruisi_ai_seal_writer'")
            self.assertEqual(cursor.fetchone(), (False, False, False))
