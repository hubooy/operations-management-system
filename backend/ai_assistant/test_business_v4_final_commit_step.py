"""Isolated real-role call path from an already claimed v4 ticket to 0052."""
from django.test import TransactionTestCase

from business_analysis.contracts import AnalysisContractError
from business_analysis.v4_final_commit_step import (
    commit_claimed_seal, recover_claimed_seal)

from . import business_v4_seal_hmac as seal_hmac
from . import business_v4_seal_verify as verifier
from . import models as m
from . import test_business_v4_commit_consumption as fixture
from .policy import digest


class BusinessV4FinalCommitStepTests(TransactionTestCase):
    prior = fixture.BusinessV4CommitConsumptionTests
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

    def test_claimed_caller_consumes_once_and_recovers_by_exact_receipt(self):
        prepared = self._prepare()
        attempt_id, actor, _, nonce, claim, raw, signature, request = prepared
        with self._role_connection("teruisi_ai_seal_writer") as db:
            committed = commit_claimed_seal(db, seal_hmac._key()[0],
                run_id=self.parent.id, attempt_id=attempt_id,
                actor_email=actor.email, actor_version=actor.version,
                nonce=nonce, claim=claim, issued_request_digest=request,
                canonical_body=raw, body_mac=signature["bodyMac"],
                enabled=True)
            db.execute("COMMIT")
        self.assertEqual(committed["status"], "consumed_receipt")
        self.assertFalse(committed["authorityVerified"])
        self.assertEqual(committed["bodyDigest"], digest(raw))
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.id).status,
                         "sealed")
        self.assertEqual(verifier.verify_seal(self.parent.id,
            self.principal)["sealedDigest"], digest(raw))
        with self._role_connection("teruisi_ai_seal_writer") as db:
            recovered = recover_claimed_seal(db,
                run_id=self.parent.id, attempt_id=attempt_id,
                issued_request_digest=request, nonce=nonce, claim=claim,
                expected_body_digest=digest(raw),
                expected_evidence_version=committed["evidenceVersion"],
                enabled=True)
            db.execute("COMMIT")
        self.assertEqual(recovered, committed)
        self.assertEqual(m.AiBusinessV4Seal.objects.filter(
            run_id=self.parent.id).count(), 1)
        self.assertEqual(m.AiBusinessV4SealConsumption.objects.filter(
            run_id=self.parent.id).count(), 1)

    def test_wrong_ticket_digest_stops_before_commit(self):
        prepared = self._prepare()
        attempt_id, actor, _, nonce, claim, raw, signature, _ = prepared
        with self._role_connection("teruisi_ai_seal_writer") as db:
            with self.assertRaises(AnalysisContractError):
                commit_claimed_seal(db, seal_hmac._key()[0],
                    run_id=self.parent.id, attempt_id=attempt_id,
                    actor_email=actor.email, actor_version=actor.version,
                    nonce=nonce, claim=claim,
                    issued_request_digest="0" * 64,
                    canonical_body=raw, body_mac=signature["bodyMac"],
                    enabled=True)
            db.execute("ROLLBACK")
        self.assertFalse(m.AiBusinessV4Seal.objects.filter(
            run_id=self.parent.id).exists())
