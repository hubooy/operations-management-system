"""Isolated real-role bridge from 0042/0049 to one candidate replay receipt."""
from django.test import TransactionTestCase

from business_analysis.contracts import AnalysisContractError
from business_analysis.v4_sealer_step_core import replay_one_claimed_segment
from . import business_v4_validation as validation, models as m
from . import test_business_v4_sealer_source_bridge as source_fixture


class BusinessV4SealerStepCoreTests(TransactionTestCase):
    fixture = source_fixture.BusinessV4SealerSourceBridgeTests
    promotion_owner = fixture.promotion_owner
    rebuild_plan = fixture.rebuild_plan
    finance_owner = fixture.finance_owner
    owner = fixture.owner
    collect = fixture.collect
    complete_mixed = fixture.complete_mixed
    attempt = fixture.attempt
    database = fixture.database
    _identity = fixture._identity
    _role_connection = fixture._role_connection
    issue = fixture.issue
    claim = fixture.claim
    setUp = fixture.setUp
    tearDown = fixture.tearDown

    def test_real_claimed_promotion_and_finance_segments_remain_unsealed(self):
        attempt_id = self.attempt()
        _, nonce, _ = self.issue(attempt_id)
        _, token, _ = self.claim(attempt_id, nonce)
        source = self.sources["promotion-current"]
        actor = self._identity(attempt_id)[2]
        derived_key, _ = validation._key()
        args = dict(run_id=self.parent.id, attempt_id=attempt_id,
                    source_id=source.id, segment_index=1,
                    actor_email=actor.email, actor_version=actor.version,
                    nonce=nonce, claim=token, enabled=True)
        with self._role_connection("teruisi_ai_seal_writer") as db:
            self.assertEqual(db.execute("SELECT session_user,current_user").fetchone(),
                             ("teruisi_ai_seal_writer", "teruisi_ai_seal_writer"))
            first = replay_one_claimed_segment(db, derived_key, **args)
            second = replay_one_claimed_segment(db, derived_key, **args)
            finance = self.sources["finance-context"]
            finance_result = replay_one_claimed_segment(db, derived_key,
                **{**args, "source_id": finance.id})
            db.execute("COMMIT")
        self.assertEqual(first["status"], "recorded_candidate")
        self.assertEqual(second["status"], "existing_candidate")
        self.assertEqual(first["candidateDigest"], second["candidateDigest"])
        self.assertFalse(first["authorityVerified"])
        self.assertEqual(finance_result["status"], "recorded_candidate")
        self.assertFalse(finance_result["authorityVerified"])
        self.assertEqual(m.AiBusinessV4SealerReplayProgress.objects.filter(
            run_id=self.parent.id).count(), 2)
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.id).status,
                         "collecting")
        self.assertFalse(m.AiBusinessV4Seal.objects.filter(run_id=self.parent.id).exists())

    def test_wrong_derived_key_cannot_record_candidate(self):
        attempt_id = self.attempt()
        _, nonce, _ = self.issue(attempt_id)
        _, token, _ = self.claim(attempt_id, nonce)
        source = self.sources["promotion-current"]
        actor = self._identity(attempt_id)[2]
        with self._role_connection("teruisi_ai_seal_writer") as db:
            with self.assertRaises(AnalysisContractError):
                replay_one_claimed_segment(db, b"0" * 32,
                    run_id=self.parent.id, attempt_id=attempt_id,
                    source_id=source.id, segment_index=1,
                    actor_email=actor.email, actor_version=actor.version,
                    nonce=nonce, claim=token, enabled=True)
            db.execute("ROLLBACK")
        self.assertFalse(m.AiBusinessV4SealerReplayProgress.objects.filter(
            run_id=self.parent.id).exists())
