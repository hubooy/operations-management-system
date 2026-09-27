"""Fake-driver tests: no PostgreSQL connection or signing secret access."""
import hashlib
import unittest

from .contracts import AnalysisContractError
from . import test_v4_final_commit_contract as contract_fixture
from .v4_final_commit_contract import commit_seal_request_digest
from . import v4_final_commit_step as step
from .v4_sealer_step_core import CONTEXT


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
        if name == "ai_v4_sealer_ticket_context":
            if self.db.drift and self.db.calls.count(name) == 2:
                altered = {**self.db.context, "parent_version": 7}
                self.rows = [tuple(altered[k] for k in CONTEXT)]
            else:
                self.rows = [tuple(self.db.context[k] for k in CONTEXT)]
        elif name == "ai_v4_sealer_commit_with_consumption":
            self.db.commit_calls += 1
            if self.db.commit_unknown:
                raise OSError("connection lost after submit")
            self.rows = [tuple(self.db.commit[k] for k in step.COMMIT)]
        elif name == "ai_v4_sealer_consumption_result":
            if self.db.read_unknown:
                raise OSError("read connection lost")
            self.rows = [tuple(self.db.receipt[k] for k in step.CONSUMPTION)]
        else:
            raise AssertionError("unexpected DB function: " + name)

    def fetchmany(self, size):
        return self.rows[:size]


class FakeDB:
    autocommit = True

    def __init__(self, args):
        plan = '{}'
        body_digest = hashlib.sha256(args["body_json"].encode()).hexdigest()
        self.context = dict(zip(CONTEXT, (
            args["run_id"], args["attempt_id"], "collecting",
            args["parent_version"], plan,
            hashlib.sha256(plan.encode()).hexdigest(),
            args["directory_digest"], args["key_id"], 2,
            None, None, None, True)))
        self.commit = {"run_id": args["run_id"],
            "evidence_version": args["parent_version"] + 1,
            "sealed_digest": body_digest, "consumed_at": "time"}
        self.receipt = {"run_id": args["run_id"],
            "attempt_id": args["attempt_id"],
            "evidence_version": args["parent_version"] + 1,
            "body_digest": body_digest, "consumed_at": "time"}
        self.calls = []
        self.commit_calls = 0
        self.commit_unknown = False
        self.read_unknown = False
        self.drift = False

    def cursor(self):
        return Cursor(self)


class V4FinalCommitStepTests(unittest.TestCase):
    def fixture(self):
        _, original = contract_fixture.V4FinalCommitContractTests().fixture()
        args = {**original}
        args["plan_digest"] = hashlib.sha256(b'{}').hexdigest()
        body = __import__("json").loads(args["body_json"])
        body["planDigest"] = args["plan_digest"]
        from .contracts import canonical
        import hmac
        args["body_json"] = canonical(body)
        args["body_mac"] = hmac.new(args["derived_key"],
            args["body_json"].encode(), hashlib.sha256).hexdigest()
        db = FakeDB(args)
        request = commit_seal_request_digest(**args)
        kwargs = dict(run_id=args["run_id"], attempt_id=args["attempt_id"],
            actor_email=args["actor_email"],
            actor_version=args["actor_version"],
            nonce="a" * 64, claim="b" * 64,
            issued_request_digest=request,
            canonical_body=args["body_json"], body_mac=args["body_mac"],
            enabled=True)
        return db, args, kwargs

    def test_success_reads_context_twice_commits_once_and_exactly_reads_receipt(self):
        db, args, kwargs = self.fixture()
        result = step.commit_claimed_seal(db, args["derived_key"], **kwargs)
        self.assertEqual(result["status"], "consumed_receipt")
        self.assertFalse(result["authorityVerified"])
        self.assertEqual(result["bodyDigest"], db.receipt["body_digest"])
        self.assertEqual(db.commit_calls, 1)
        self.assertEqual(db.calls, ["ai_v4_sealer_ticket_context",
            "ai_v4_sealer_ticket_context",
            "ai_v4_sealer_commit_with_consumption",
            "ai_v4_sealer_consumption_result"])

    def test_disabled_bad_key_ticket_and_context_never_commit(self):
        for mode in ("disabled", "key", "request", "context", "drift",
                     "transaction"):
            db, args, kwargs = self.fixture()
            key = args["derived_key"]
            if mode == "disabled":
                kwargs["enabled"] = False
            elif mode == "key":
                key = b"0" * 32
            elif mode == "request":
                kwargs["issued_request_digest"] = "0" * 64
            elif mode == "context":
                db.context["run_id"] = "other"
            elif mode == "drift":
                db.drift = True
            else:
                db.autocommit = False
            with self.subTest(mode=mode), self.assertRaises(AnalysisContractError):
                step.commit_claimed_seal(db, key, **kwargs)
            self.assertEqual(db.commit_calls, 0)

    def test_unknown_write_has_no_retry_and_only_explicit_recovery_reads(self):
        db, args, kwargs = self.fixture()
        db.commit_unknown = True
        result = step.commit_claimed_seal(db, args["derived_key"], **kwargs)
        self.assertEqual(result["status"], "unknown_commit_result")
        self.assertFalse(result["authorityVerified"])
        self.assertEqual(db.commit_calls, 1)
        self.assertNotIn("ai_v4_sealer_consumption_result", db.calls)
        recovered = step.recover_claimed_seal(db, run_id=kwargs["run_id"],
            attempt_id=kwargs["attempt_id"],
            issued_request_digest=kwargs["issued_request_digest"],
            nonce=kwargs["nonce"], claim=kwargs["claim"],
            expected_body_digest=db.receipt["body_digest"],
            expected_evidence_version=db.receipt["evidence_version"],
            enabled=True)
        self.assertEqual(recovered["status"], "consumed_receipt")
        self.assertEqual(db.commit_calls, 1)
        self.assertEqual(db.calls.count("ai_v4_sealer_consumption_result"), 1)

    def test_readback_error_and_mismatch_fail_closed(self):
        db, args, kwargs = self.fixture()
        db.read_unknown = True
        result = step.commit_claimed_seal(db, args["derived_key"], **kwargs)
        self.assertEqual(result["status"], "unknown_readback_result")
        self.assertFalse(result["authorityVerified"])
        db, args, kwargs = self.fixture()
        db.receipt["body_digest"] = "0" * 64
        self.assertEqual(step.commit_claimed_seal(db, args["derived_key"],
            **kwargs)["status"], "unknown_readback_result")
        self.assertEqual(db.commit_calls, 1)

    def test_recovery_requires_explicit_enable_and_never_calls_commit(self):
        db, _, kwargs = self.fixture()
        args = dict(run_id=kwargs["run_id"], attempt_id=kwargs["attempt_id"],
            issued_request_digest=kwargs["issued_request_digest"],
            nonce=kwargs["nonce"], claim=kwargs["claim"],
            expected_body_digest=db.receipt["body_digest"],
            expected_evidence_version=db.receipt["evidence_version"])
        with self.assertRaises(AnalysisContractError):
            step.recover_claimed_seal(db, **args)
        self.assertEqual(db.calls, [])
        db.read_unknown = True
        self.assertEqual(step.recover_claimed_seal(db, enabled=True,
            **args)["status"], "unknown_recovery_result")
        self.assertEqual(db.commit_calls, 0)


if __name__ == "__main__":
    unittest.main()
