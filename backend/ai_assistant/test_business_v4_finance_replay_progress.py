"""Target PostgreSQL checks for 0048 over the real one-page finance fixture."""
import json
from importlib import import_module

import psycopg
from django.db import connection, transaction
from django.test import TransactionTestCase

from . import models as m
from .policy import digest
from .v4_replay_progress_catalog import verify as verify_catalog
from . import test_business_v4_sealer_replay_progress as fixture


class BusinessV4FinanceReplayProgressTests(TransactionTestCase):
    promotion_owner = fixture.BusinessV4SealerReplayProgressTests.promotion_owner
    rebuild_plan = fixture.BusinessV4SealerReplayProgressTests.rebuild_plan
    finance_owner = fixture.BusinessV4SealerReplayProgressTests.finance_owner
    owner = fixture.BusinessV4SealerReplayProgressTests.owner
    collect = fixture.BusinessV4SealerReplayProgressTests.collect
    complete_mixed = fixture.BusinessV4SealerReplayProgressTests.complete_mixed
    attempt = fixture.BusinessV4SealerReplayProgressTests.attempt
    database = fixture.BusinessV4SealerReplayProgressTests.database
    _identity = fixture.BusinessV4SealerReplayProgressTests._identity
    _role_connection = fixture.BusinessV4SealerReplayProgressTests._role_connection
    issue = fixture.BusinessV4SealerReplayProgressTests.issue
    claim = fixture.BusinessV4SealerReplayProgressTests.claim
    _record = fixture.BusinessV4SealerReplayProgressTests._record
    _promotion_candidate = fixture.BusinessV4SealerReplayProgressTests._candidate
    setUp = fixture.BusinessV4SealerReplayProgressTests.setUp
    tearDown = fixture.BusinessV4SealerReplayProgressTests.tearDown

    def test_finance_frozen_catalog_rejects_old_writer_body(self):
        latest = {"finance_enabled": True, "read_cast_enabled": True,
                  "prior_claim_qualified": True}
        with connection.cursor() as cursor:
            verify_catalog(cursor, **latest)
            verify_catalog(cursor, RuntimeError, **latest)
        previous = import_module(
            "ai_assistant.migrations.0047_business_v4_sealer_replay_progress")
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(previous.RECORD.replace(
                "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
            with self.assertRaisesRegex(ValueError, "function body"):
                verify_catalog(cursor, **latest)
            transaction.set_rollback(True)

    def _finance_candidate(self, attempt_id, ticket_id):
        source = self.sources["finance-context"]
        source.refresh_from_db()
        segment = m.AiBusinessV4ValidationSegment.objects.get(
            attempt_id=attempt_id, source=source, segment_index=1)
        attempt = m.AiBusinessV4ValidationAttempt.objects.get(pk=attempt_id)
        with connection.cursor() as cursor:
            cursor.execute("SELECT source_root FROM public.ai_business_v4_seal_tickets "
                "WHERE id=%s", [ticket_id])
            root = cursor.fetchone()[0]
        progress = json.loads(segment.progress_json)
        # This state came from the fixture's imported August/September finance
        # batches and the real finance owner tool, not a fabricated empty month.
        self.assertEqual(progress["domain"], "finance")
        self.assertGreater(progress["rowCount"], 0)
        self.assertEqual(source.page_count, 1)
        self.assertEqual(progress["pageCount"], source.page_count)
        self.assertEqual(progress["domainState"]["queryDigest"],
            source.query_digest)
        finite = {key: progress[key] for key in ("pageCount", "rowCount",
            "storedBytes", "lastChunkDigest", "receiptChainDigest")}
        finite["financeState"] = progress["domainState"]
        body = {"schemaVersion":
            "business-v4-sealer-finance-segment-candidate-v2",
            "runId": self.parent.id, "attemptId": attempt_id,
            "sourceId": source.id, "sourceRoot": root,
            "sourceKey": source.source_key, "sourceRef": source.source_ref,
            "sourceRevision": source.source_revision,
            "sourceVersion": source.version, "keyId": attempt.key_id,
            "segmentIndex": 1, "endSequence": segment.end_sequence,
            "segmentProofDigest": segment.proof_digest, "progress": finite,
            "previousCandidateDigest": "0" * 64,
            "candidateOnly": True, "authorityVerified": False,
            "financeReplayed": True, "upstreamSignatureVerified": False,
            "sealCommitted": False}
        return source, {**body, "candidateDigest": digest(body)}

    @staticmethod
    def _redigest(candidate):
        candidate["candidateDigest"] = digest({key: value for key, value
            in candidate.items() if key != "candidateDigest"})
        return candidate

    def test_real_finance_page_records_once_and_keeps_seal_closed(self):
        attempt_id = self.attempt()
        ticket_id, nonce, _ = self.issue(attempt_id)
        _, token, _ = self.claim(attempt_id, nonce)
        source, candidate = self._finance_candidate(attempt_id, ticket_id)
        first = self._record(attempt_id, source.id, candidate, nonce, token)
        self.assertEqual(first[0], candidate["candidateDigest"])
        self.assertEqual(self._record(attempt_id, source.id, candidate,
            nonce, token), first)
        promotion = self.sources["promotion-current"]
        promotion.refresh_from_db()
        promotion_candidate = self._promotion_candidate(
            attempt_id, ticket_id, promotion)
        self.assertEqual(self._record(attempt_id, promotion.id,
            promotion_candidate, nonce, token)[0],
            promotion_candidate["candidateDigest"])
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM "
                "public.ai_business_v4_sealer_replay_progress")
            self.assertEqual(cursor.fetchone()[0], 2)
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).status,
            "collecting")
        self.assertFalse(m.AiBusinessV4Seal.objects.filter(run=self.parent).exists())
        migration = import_module(
            "ai_assistant.migrations.0048_business_v4_finance_replay_progress")
        with connection.schema_editor() as editor:
            with self.assertRaisesRegex(RuntimeError, "cannot discard finance"):
                migration.uninstall(None, editor)

    def test_finance_state_null_mismatch_flags_and_domain_rejected(self):
        attempt_id = self.attempt()
        ticket_id, nonce, _ = self.issue(attempt_id)
        _, token, _ = self.claim(attempt_id, nonce)
        source, candidate = self._finance_candidate(attempt_id, ticket_id)
        cases = (
            (lambda c: c["progress"].update(financeState=None), "mismatch"),
            (lambda c: c["progress"]["financeState"].update(queryDigest=None),
             "mismatch"),
            (lambda c: c["progress"].update(rowCount=None), "mismatch"),
            (lambda c: c.update(financeReplayed=False), "candidate_invalid"),
            (lambda c: c.update(authorityVerified=True), "candidate_invalid"),
            (lambda c: c.update(sealCommitted=True), "candidate_invalid"),
            (lambda c: c.update(previousCandidateDigest="f" * 64),
             "candidate_chain_invalid"),
            (lambda c: c.update(schemaVersion=
                "business-v4-sealer-promotion-segment-candidate-v2"),
             "candidate_invalid"),
        )
        for mutate, error in cases:
            changed = json.loads(json.dumps(candidate, ensure_ascii=False))
            mutate(changed)
            self._redigest(changed)
            with self.subTest(error=error, changed=changed), self.assertRaisesRegex(
                    psycopg.Error, error):
                self._record(attempt_id, source.id, changed, nonce, token)
        self._record(attempt_id, source.id, candidate, nonce, token)
        conflict = json.loads(json.dumps(candidate, ensure_ascii=False))
        conflict["progress"]["financeState"]["checkpointDigest"] = "f" * 64
        self._redigest(conflict)
        with self.assertRaisesRegex(psycopg.Error, "mismatch"):
            self._record(attempt_id, source.id, conflict, nonce, token)
