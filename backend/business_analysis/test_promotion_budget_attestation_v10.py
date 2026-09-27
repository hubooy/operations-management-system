"""Pure, non-authorizing 0057 body vectors; no database or file grants."""
from copy import deepcopy
import hashlib
from unittest import TestCase

from . import promotion_budget_attestation_v10 as contract
from .contracts import AnalysisContractError, canonical, digest


class PromotionBudgetAttestationV10Tests(TestCase):
    def fixture(self, *, budget=False):
        approved, review = "a" * 64, "b" * 64
        plan = "c" * 64 if budget else None
        proof = {"approvedContentDigest": approved,
            "humanReviewDigest": review, "budgetPlanDigest": plan,
            "candidateOnly": True,
            "status": ("reconciled_fixed_budget_candidate" if budget else
                       "missing_fixed_budget")}
        proof["proofDigest"] = digest(proof)
        full = {"rendererVersion": 10, "status": "complete",
            "promotionBudgetProof": proof,
            "promotionFileProof": {"contentDtoDigest": approved,
                "humanReviewDigest": review}}
        if budget:
            full["budgetPlanDigest"] = plan
        full["manifestDigest"] = digest(full)
        raw_full = canonical(full).encode("utf-8")
        compact = {"bindingDigest": "d" * 64, "attempt": 1,
            "rendererVersion": 10, "files": [],
            "manifestFile": {"volumeIndex": 0, "format": "json",
                "bytes": len(raw_full), "chunkCount": 1,
                "sha256": hashlib.sha256(raw_full).hexdigest()}}
        args = {"run_id": "run-one", "attempt": 1, "run_version": 8,
            "binding_digest": "d" * 64, "compact_json": canonical(compact),
            "full_json_bytes": raw_full, "full_manifest": full,
            "approved_content_digest": approved,
            "human_review_digest": review,
            "budget_present": budget, "budget_plan_digest": plan,
            "budget_roots_digest": "e" * 64,
            "report_snapshot_sha256": "f" * 64,
            "workflow_input_sha256": "1" * 64,
            "actor_version": 3, "fresh_semantics_verified": True}
        return args

    def test_exact_0057_fields_and_process_digest_are_stable(self):
        for budget in (False, True):
            with self.subTest(budget=budget):
                args = self.fixture(budget=budget)
                result = contract.compose(**args)
                import json
                body = json.loads(result["attestationText"])
                self.assertEqual(set(body), {"schemaVersion", "runId", "attempt",
                    "bindingDigest", "compactJsonSha256", "fullManifestSha256",
                    "fullManifestDigest", "files", "approvedContentDigest",
                    "humanReviewDigest", "budgetPresent", "budgetPlanDigest",
                    "budgetProofDigest", "owningVerificationDigest",
                    "publicationFenceDigest", "verifierVersion"})
                self.assertEqual(body["schemaVersion"], contract.SCHEMA)
                self.assertEqual(body["verifierVersion"], contract.VERIFIER)
                self.assertEqual(body["budgetPresent"], budget)
                self.assertEqual(body["budgetPlanDigest"], args["budget_plan_digest"])
                self.assertEqual(result["attestationSha256"], hashlib.sha256(
                    result["attestationText"].encode()).hexdigest())
                self.assertFalse(result["databaseCanIndependentlyVerifyProcessAssertions"])

    def test_changed_version_actor_or_roots_change_only_process_digest(self):
        args = self.fixture()
        baseline = contract.compose(**args)
        for field, value in (("run_version", 9), ("actor_version", 4),
                             ("report_snapshot_sha256", "0" * 64),
                             ("workflow_input_sha256", "0" * 64)):
            changed = contract.compose(**{**args, field: value})
            self.assertNotEqual(changed["publicationFenceDigest"],
                baseline["publicationFenceDigest"])
        changed = contract.compose(**{**args, "budget_roots_digest": "0" * 64})
        self.assertNotEqual(changed["owningVerificationDigest"],
            baseline["owningVerificationDigest"])

    def test_unverified_semantics_wrong_budget_or_changed_json_fail(self):
        args = self.fixture(budget=True)
        changes = ({"fresh_semantics_verified": False},
            {"budget_plan_digest": None},
            {"approved_content_digest": "0" * 64},
            {"full_json_bytes": b"{}"},
            {"compact_json": args["compact_json"] + " "})
        for change in changes:
            with self.subTest(change=list(change)), self.assertRaises(
                    AnalysisContractError):
                contract.compose(**{**args, **change})
        forged = deepcopy(args["full_manifest"])
        forged["promotionBudgetProof"]["proofDigest"] = "0" * 64
        with self.assertRaises(AnalysisContractError):
            contract.compose(**{**args, "full_manifest": forged})
