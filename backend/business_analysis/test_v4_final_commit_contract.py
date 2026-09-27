"""Pure vectors for the future, currently non-authoritative v4 commit intent."""
import hashlib
import hmac
from unittest import TestCase

from .contracts import AnalysisContractError, canonical, digest
from .evidence_seal_v4 import make
from .test_evidence_seal_v4 import EvidenceSealV4Tests
from .v4_final_commit_contract import (OPERATION, PARENT_SEAL_PURPOSE,
    commit_seal_request_digest, derive_parent_seal_key,
    verify_parent_seal_body)


class V4FinalCommitContractTests(TestCase):
    def fixture(self):
        candidate, queries = EvidenceSealV4Tests().candidate()
        body_json = make(candidate, queries)["bodyJson"]
        master = "test-only-secret-material-0123456789abcdef"
        key = derive_parent_seal_key(master)
        mac = hmac.new(key, body_json.encode("utf-8"), hashlib.sha256).hexdigest()
        args = {"run_id": "run-one", "attempt_id": "attempt-one",
            "actor_email": "operator@example.test", "actor_version": 1,
            "parent_version": 5, "plan_digest": "e" * 64,
            "directory_digest": "f" * 64, "body_json": body_json,
            "body_mac": mac, "key_id": "a" * 16,
            "derived_key": key}
        return master, args

    def test_purpose_and_canonical_request_digest_match_independent_vector(self):
        master, args = self.fixture()
        self.assertEqual(derive_parent_seal_key(master), hmac.new(
            master.encode("utf-8"), PARENT_SEAL_PURPOSE,
            hashlib.sha256).digest())
        self.assertEqual(PARENT_SEAL_PURPOSE,
            b"teruisi:business-v4:parent-seal:v1\x00")
        body_digest = hashlib.sha256(args["body_json"].encode("utf-8")).hexdigest()
        self.assertEqual(verify_parent_seal_body(args["body_json"],
            args["body_mac"], args["derived_key"], args["key_id"]), body_digest)
        request = {"operation": OPERATION, "runId": args["run_id"],
            "attemptId": args["attempt_id"], "actorEmail": args["actor_email"],
            "actorVersion": args["actor_version"],
            "parentVersion": args["parent_version"],
            "planDigest": args["plan_digest"],
            "directoryDigest": args["directory_digest"],
            "bodyDigest": body_digest, "bodyMac": args["body_mac"],
            "keyId": args["key_id"]}
        self.assertEqual(commit_seal_request_digest(**args), digest(request))
        self.assertEqual(digest(request), hashlib.sha256(
            canonical(request).encode("utf-8")).hexdigest())

    def test_actor_email_is_bound_without_changing_body_mac(self):
        _, args = self.fixture()
        original = commit_seal_request_digest(**args)
        changed = {**args, "actor_email": "other@example.test"}
        self.assertNotEqual(commit_seal_request_digest(**changed), original)
        for bad in ("", " operator@example.test", "operator@example.test\n"):
            with self.subTest(bad=repr(bad)), self.assertRaises(AnalysisContractError):
                commit_seal_request_digest(**{**args, "actor_email": bad})

    def test_body_identity_and_request_fields_are_bound(self):
        _, args = self.fixture()
        for field, value in (("run_id", "another-run"),
                             ("attempt_id", "another-attempt"),
                             ("actor_version", 2),
                             ("parent_version", 4),
                             ("plan_digest", "0" * 64),
                             ("directory_digest", "0" * 64),
                             ("key_id", "0" * 16)):
            with self.subTest(field=field), self.assertRaises(AnalysisContractError):
                commit_seal_request_digest(**{**args, field: value})
        for field, value in (("actor_version", True),
                             ("parent_version", 0),
                             ("run_id", "bad id"),
                             ("plan_digest", "A" * 64)):
            with self.subTest(field=field, malformed=True), self.assertRaises(AnalysisContractError):
                commit_seal_request_digest(**{**args, field: value})

    def test_exact_canonical_body_mac_and_derived_key_fail_closed(self):
        _, args = self.fixture()
        changes = [
            {"body_mac": "0" * 64},
            {"derived_key": b"0" * 32},
            {"derived_key": b"0" * 31},
            {"body_json": args["body_json"] + " "},
            {"body_json": args["body_json"].replace('"runId":"run-one"',
                '"runId":"run-two"')},
            {"body_mac": args["body_mac"].upper()},
        ]
        for change in changes:
            with self.subTest(change=list(change)), self.assertRaises(AnalysisContractError):
                commit_seal_request_digest(**{**args, **change})
        with self.assertRaises(AnalysisContractError):
            derive_parent_seal_key("short")

    def test_changed_canonical_body_with_valid_mac_changes_request_digest(self):
        _, args = self.fixture()
        baseline = commit_seal_request_digest(**args)
        import json
        body = json.loads(args["body_json"])
        body["sources"][0]["coverage"]["status"] = "complete"
        changed_json = canonical(body)
        changed_mac = hmac.new(args["derived_key"], changed_json.encode("utf-8"),
            hashlib.sha256).hexdigest()
        changed = {**args, "body_json": changed_json, "body_mac": changed_mac}
        self.assertNotEqual(commit_seal_request_digest(**changed), baseline)
