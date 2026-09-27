"""No-DB fake-driver tests for one-shot 0057 + 0058 orchestration."""
import hashlib
import json
from unittest import TestCase
from unittest.mock import patch

from business_analysis import promotion_budget_v10_publish_request as request
from business_analysis import test_promotion_budget_attestation_v10 as pure_fixture
from business_analysis.promotion_budget_attestation_v10 import compose
from business_analysis.contracts import AnalysisContractError

from . import business_promotion_budget_v10_publish_step as step


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
        if name == "ai_budget_v10_attest_staged":
            if self.db.attest_unknown:
                raise OSError("driver lost attestation result")
            value = self.db.attestation_id
            if self.db.attest_wrong:
                value = "0" * 64
        elif name == "ai_budget_v10_publish":
            self.db.publish_count += 1
            if self.db.publish_unknown:
                raise OSError("driver lost publication result")
            value = self.db.publication(args[5])
            if self.db.publish_wrong:
                value = {**value, "version": 999}
        elif name == "ai_budget_v10_publish_outcome":
            if self.db.outcome_unknown:
                raise OSError("driver lost outcome")
            value = (self.db.publication(args[2]) if self.db.outcome == "committed"
                else {"status": "not_committed", "runId": self.db.run_id,
                    "attempt": self.db.attempt, "version": self.db.run_version,
                    "requestDigest": args[2], "attestationId": self.db.attestation_id})
        else:
            raise AssertionError("unexpected DB function: " + name)
        self.rows = [(value,)]

    def fetchmany(self, count):
        return self.rows[:count]


class FakeDB:
    autocommit = True

    def __init__(self, prepared):
        body = json.loads(prepared["attestationText"])
        self.run_id = prepared["runId"]
        self.attempt = prepared["attempt"]
        self.run_version = prepared["runVersion"]
        self.full_sha = body["fullManifestSha256"]
        self.attestation_id = hashlib.sha256(
            f"{self.run_id}:{self.attempt}".encode()).hexdigest()
        self.calls = []
        self.publish_count = 0
        self.attest_unknown = False
        self.attest_wrong = False
        self.publish_unknown = False
        self.publish_wrong = False
        self.outcome_unknown = False
        self.outcome = "committed"

    def cursor(self):
        return Cursor(self)

    def publication(self, request_digest):
        return {"schemaVersion": step.PUBLICATION_SCHEMA,
            "status": "committed", "runId": self.run_id,
            "attempt": self.attempt, "version": self.run_version + 1,
            "requestDigest": request_digest,
            "attestationId": self.attestation_id,
            "manifestFileSha256": self.full_sha}


def fixture():
    args = pure_fixture.PromotionBudgetAttestationV10Tests().fixture(budget=True)
    built = compose(**args)
    prepared = {"schemaVersion": step.PREFLIGHT_SCHEMA,
        "runId": args["run_id"], "attempt": args["attempt"],
        "runVersion": args["run_version"],
        "bindingDigest": args["binding_digest"],
        "attestationText": built["attestationText"],
        "attestationSha256": built["attestationSha256"],
        "owningVerificationDigest": built["owningVerificationDigest"],
        "publicationFenceDigest": built["publicationFenceDigest"],
        "candidateOnly": True,
        "databaseCanIndependentlyVerifyProcessAssertions": False,
        "readyAuthorized": False}
    return prepared, FakeDB(prepared)


class BudgetV10PublishStepTests(TestCase):
    def run_step(self, db, prepared, **changes):
        with patch.object(step, "_fresh_preflight", return_value=prepared):
            return step.attest_and_publish(db, prepared["runId"], object(),
                enabled=True, **changes)

    def test_known_success_one_each_and_exact_outcome_readback(self):
        prepared, db = fixture()
        result = self.run_step(db, prepared, expected_preflight=prepared)
        self.assertEqual(result["status"], "publication_recorded")
        self.assertFalse(result["authorityVerified"])
        self.assertFalse(result["readyAuthorized"])
        self.assertEqual(db.publish_count, 1)
        self.assertEqual(db.calls, ["ai_budget_v10_attest_staged",
            "ai_budget_v10_publish", "ai_budget_v10_publish_outcome"])
        body = json.loads(prepared["attestationText"])
        expected = request.request_digest(run_id=prepared["runId"],
            attempt=prepared["attempt"],
            expected_version=prepared["runVersion"],
            attestation_id=db.attestation_id,
            attestation_sha256=prepared["attestationSha256"],
            binding_digest=prepared["bindingDigest"],
            full_manifest_digest=body["fullManifestDigest"],
            full_manifest_sha256=body["fullManifestSha256"])
        self.assertEqual(result["requestDigest"], expected)

    def test_disabled_autocommit_and_changed_preflight_never_write(self):
        prepared, db = fixture()
        with self.assertRaises(AnalysisContractError):
            step.attest_and_publish(db, prepared["runId"], object())
        self.assertEqual(db.calls, [])
        db.autocommit = False
        with self.assertRaises(AnalysisContractError):
            self.run_step(db, prepared)
        self.assertEqual(db.calls, [])
        db.autocommit = True
        old = {**prepared, "runVersion": prepared["runVersion"] + 1}
        with self.assertRaises(AnalysisContractError):
            self.run_step(db, prepared, expected_preflight=old)
        self.assertEqual(db.calls, [])
        forged = {**prepared, "attestationSha256": "0" * 64}
        with self.assertRaises(AnalysisContractError):
            self.run_step(db, forged)
        self.assertEqual(db.calls, [])

    def test_unknown_attestation_stops_before_publish_without_guessing(self):
        prepared, db = fixture()
        db.attest_unknown = True
        result = self.run_step(db, prepared)
        self.assertEqual(result["status"], "unknown_attestation_result")
        self.assertFalse(result["authorityVerified"])
        self.assertEqual(db.calls, ["ai_budget_v10_attest_staged"])
        self.assertEqual(db.publish_count, 0)
        prepared, db = fixture()
        db.attest_wrong = True
        self.assertEqual(self.run_step(db, prepared)["status"],
            "unknown_attestation_result")
        self.assertEqual(db.publish_count, 0)

    def test_unknown_publish_has_no_auto_outcome_and_explicit_recovery_only(self):
        prepared, db = fixture()
        db.publish_unknown = True
        result = self.run_step(db, prepared)
        self.assertEqual(result["status"], "unknown_publish_result")
        self.assertEqual(db.calls, ["ai_budget_v10_attest_staged",
            "ai_budget_v10_publish"])
        self.assertEqual(db.publish_count, 1)
        kwargs = {"run_id": result["runId"], "attempt": result["attempt"],
            "expected_version": result["expectedVersion"],
            "attestation_id": result["attestationId"],
            "attestation_sha256": result["attestationSha256"],
            "binding_digest": result["bindingDigest"],
            "full_manifest_digest": result["fullManifestDigest"],
            "full_manifest_sha256": result["fullManifestSha256"],
            "original_request_digest": result["requestDigest"],
            "enabled": True}
        with self.assertRaises(AnalysisContractError):
            step.recover_publish_outcome(db, **{**kwargs,
                "original_request_digest": "0" * 64})
        db.outcome = "not_committed"
        recovered = step.recover_publish_outcome(db, **kwargs)
        self.assertEqual(recovered["status"], "not_committed")
        self.assertEqual(db.publish_count, 1)
        self.assertEqual(db.calls.count("ai_budget_v10_publish_outcome"), 1)
        db.outcome = "committed"
        recovered = step.recover_publish_outcome(db, **kwargs)
        self.assertEqual(recovered["status"], "publication_recorded")
        self.assertEqual(db.publish_count, 1)

    def test_known_publish_readback_error_keeps_unknown(self):
        prepared, db = fixture()
        db.outcome_unknown = True
        self.assertEqual(self.run_step(db, prepared)["status"],
            "unknown_outcome_readback")
        self.assertEqual(db.publish_count, 1)
        self.assertEqual(db.calls.count("ai_budget_v10_publish_outcome"), 1)
        prepared, db = fixture()
        db.publish_wrong = True
        self.assertEqual(self.run_step(db, prepared)["status"],
            "unknown_outcome_readback")
        self.assertEqual(db.publish_count, 1)
