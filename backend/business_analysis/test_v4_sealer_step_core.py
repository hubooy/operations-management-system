"""Pure fake-driver checks for the disabled v4 sealer segment step."""
import copy
import hashlib
import hmac
import unittest

from .contracts import AnalysisContractError, canonical, digest
from .test_v4_sealer_segment_replay import V4PromotionSegmentReplayTests
from .test_v4_sealer_finance_segment_replay import V4FinanceSegmentReplayTests
from .v4_sealer_mac import segment_key_id
from .v4_sealer_segment_replay import ATTEMPT_SCHEMA, ZERO
from . import v4_sealer_step_core as step


KEY = b"k" * 32


class Cursor:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, args):
        name = sql.split("public.", 1)[1].split("(", 1)[0]
        self.db.calls.append(name)
        if name == "ai_v4_sealer_record_replay_progress":
            if self.db.write_unknown:
                raise OSError("connection lost after send")
            candidate = step._json(args[-1], 48_000)
            self.db.saved = (args[-1], candidate["candidateDigest"], "at")
            self.rows = [(candidate["candidateDigest"], "at")]
            return
        if name == "ai_v4_sealer_replay_progress":
            row = self.db.saved if args[3] == 1 else None
            if self.db.readback_wrong and self.db.saved:
                row = ("{}", self.db.saved[1], self.db.saved[2])
            self.rows = [row] if row else []
            return
        mapping = {
            "ai_v4_sealer_ticket_context": (step.CONTEXT, self.db.context),
            "ai_v4_sealer_ticket_source": (step.SOURCE, self.db.source),
            "ai_v4_sealer_ticket_segment": (step.SEGMENT, self.db.segment),
            "ai_v4_sealer_ticket_page": (step.PAGE,
                self.db.pages.get(args[3])),
        }
        fields, data = mapping[name]
        self.rows = [tuple(data.get(field) for field in fields)] if data else []

    def fetchmany(self, size):
        return self.rows[:size]


class FakeDB:
    autocommit = True

    def __init__(self, count=2):
        identity, segment, pages = V4PromotionSegmentReplayTests().fixture(count=count)
        identity["keyId"] = segment_key_id(KEY)
        proof = {"schemaVersion": ATTEMPT_SCHEMA,
                 "attemptId": identity["attemptId"], "runId": identity["runId"],
                 "sourceId": identity["sourceId"], "segmentIndex": 1,
                 "startSequence": 1, "endSequence": count,
                 "sourceVersion": identity["sourceVersion"],
                 "sourceRef": identity["sourceRef"],
                 "sourceRevision": identity["sourceRevision"],
                 "previousSegmentDigest": ZERO,
                 "progressDigest": segment["progress_digest"],
                 "keyId": identity["keyId"]}
        segment["proof_digest"] = digest(proof)
        segment["proof_mac"] = hmac.new(KEY, canonical(proof).encode(),
                                         hashlib.sha256).hexdigest()
        self.segment, self.pages = segment, {p["page_sequence"]: p for p in pages}
        plan = {"sourceCount": 2, "sourcePlans": [
            {"sourceKey": identity["sourceKey"], "ordinal": 1,
             "domain": "netshop", "temporalRole": "daily_fact",
             "query": identity["query"], "queryDigest": digest(identity["query"]),
             "sourceIdentityDigest": "a" * 64,
             "sourceRevisionHint": "hint"}, {}]}
        plan_raw = canonical(plan)
        self.context = dict(zip(step.CONTEXT, (
            identity["runId"], identity["attemptId"], "collecting", 3,
            plan_raw, hashlib.sha256(plan_raw.encode()).hexdigest(),
            "d" * 64, identity["keyId"], 2, None, None, None, True)))
        self.source = dict(zip(step.SOURCE, (
            identity["sourceId"], identity["sourceKey"], 1, "netshop",
            "daily_fact", canonical(identity["query"]),
            digest(identity["query"]), "a" * 64, "hint",
            identity["sourceVersion"], identity["sourceRef"],
            identity["sourceRevision"], identity["sourcePageCount"],
            identity["sourceRowCount"], identity["sourceStoredBytes"],
            identity["sourceRoot"], identity["keyId"], "b" * 64,
            pages[-1]["payload_digest"], "{}", None, True)))
        self.calls = []
        self.saved = None
        self.write_unknown = False
        self.readback_wrong = False

    def cursor(self):
        return Cursor(self)


def finance_db():
    db = FakeDB()
    identity, segments, pages = V4FinanceSegmentReplayTests().fixture(count=2)
    identity["keyId"] = segment_key_id(KEY)
    segment = segments[0]
    proof = {"schemaVersion": ATTEMPT_SCHEMA,
             "attemptId": identity["attemptId"], "runId": identity["runId"],
             "sourceId": identity["sourceId"], "segmentIndex": 1,
             "startSequence": 1, "endSequence": 2,
             "sourceVersion": identity["sourceVersion"],
             "sourceRef": identity["sourceRef"],
             "sourceRevision": identity["sourceRevision"],
             "previousSegmentDigest": ZERO,
             "progressDigest": segment["progress_digest"],
             "keyId": identity["keyId"]}
    segment["proof_digest"] = digest(proof)
    segment["proof_mac"] = hmac.new(KEY, canonical(proof).encode(),
                                     hashlib.sha256).hexdigest()
    db.segment = segment
    db.pages = {p["page_sequence"]: p for p in pages}
    raw = canonical(identity["query"])
    source_plan = {"sourceKey": identity["sourceKey"], "ordinal": 1,
                   "domain": "finance", "temporalRole": "monthly_context",
                   "query": identity["query"],
                   "queryDigest": hashlib.sha256(raw.encode()).hexdigest(),
                   "sourceIdentityDigest": "a" * 64,
                   "sourceRevisionHint": "hint"}
    plan_raw = canonical({"sourceCount": 2,
                          "sourcePlans": [source_plan, {}]})
    db.context["plan_json"] = plan_raw
    db.context["plan_digest"] = hashlib.sha256(plan_raw.encode()).hexdigest()
    db.source.update(source_key=identity["sourceKey"], domain="finance",
        temporal_role="monthly_context", query_json=raw,
        query_digest=source_plan["queryDigest"],
        source_version=identity["sourceVersion"],
        source_ref=identity["sourceRef"],
        source_revision=identity["sourceRevision"],
        page_count=identity["sourcePageCount"],
        row_count=identity["sourceRowCount"],
        stored_bytes=identity["sourceStoredBytes"], metadata_json=None,
        finance_state_digest="b" * 64)
    return db


def run(db, **changes):
    params = dict(run_id="run-1", attempt_id="attempt-1",
                  source_id="source-1", segment_index=1,
                  actor_email="admin@example.test", actor_version=1,
                  nonce="opaque", claim="opaque", enabled=True)
    params.update(changes)
    return step.replay_one_claimed_segment(db, KEY, **params)


class V4SealerStepCoreTests(unittest.TestCase):
    def test_finance_single_segment_uses_same_protected_sequence(self):
        db = finance_db()
        result = run(db)
        self.assertEqual(result["status"], "recorded_candidate")
        self.assertTrue(step._json(db.saved[0], 48_000)["financeReplayed"])
        self.assertFalse(result["authorityVerified"])

    def test_success_order_and_existing_receipt_status(self):
        db = FakeDB()
        with self.assertRaises(AnalysisContractError):
            run(db, enabled=False)
        self.assertEqual(db.calls, [])
        result = run(db)
        self.assertEqual(result["status"], "recorded_candidate")
        self.assertEqual(db.calls, [
            "ai_v4_sealer_ticket_context", "ai_v4_sealer_ticket_source",
            "ai_v4_sealer_replay_progress", "ai_v4_sealer_ticket_segment",
            "ai_v4_sealer_ticket_page", "ai_v4_sealer_ticket_page",
            "ai_v4_sealer_ticket_context", "ai_v4_sealer_record_replay_progress",
            "ai_v4_sealer_replay_progress"])
        db.calls.clear()
        existing = run(db)
        self.assertEqual(existing["status"], "existing_candidate")
        self.assertFalse(existing["authorityVerified"])
        self.assertNotIn("ai_v4_sealer_ticket_page", db.calls)

    def test_wrong_account_root_query_and_missing_page_fail_before_write(self):
        for change in ("account", "root", "query", "page"):
            db = FakeDB()
            if change == "account":
                db.context["run_id"] = "other"
            elif change == "root":
                db.source["source_root"] = "x" * 64
            elif change == "query":
                db.source["query_digest"] = "0" * 64
            else:
                del db.pages[2]
            with self.subTest(change=change), self.assertRaises(AnalysisContractError):
                run(db)
            self.assertNotIn("ai_v4_sealer_record_replay_progress", db.calls)

    def test_bad_hmac_and_missing_prior_receipt(self):
        db = FakeDB()
        db.segment["proof_mac"] = "0" * 64
        with self.assertRaises(AnalysisContractError):
            run(db)
        self.assertNotIn("ai_v4_sealer_record_replay_progress", db.calls)
        db = FakeDB(count=17)
        with self.assertRaises(AnalysisContractError):
            run(db, segment_index=2)
        self.assertNotIn("ai_v4_sealer_ticket_segment", db.calls)

    def test_write_unknown_and_readback_mismatch(self):
        db = FakeDB()
        db.write_unknown = True
        result = run(db)
        self.assertEqual(result, {"status": "unknown_write_result",
                                  "candidateOnly": True,
                                  "authorityVerified": False})
        self.assertEqual(db.calls.count("ai_v4_sealer_record_replay_progress"), 1)
        self.assertEqual(db.calls.count("ai_v4_sealer_replay_progress"), 1)
        db = FakeDB()
        db.readback_wrong = True
        with self.assertRaises(AnalysisContractError):
            run(db)


if __name__ == "__main__":
    unittest.main()
