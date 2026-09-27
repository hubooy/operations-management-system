from unittest import TestCase

from .contracts import AnalysisContractError, canonical, digest
from . import promotion_budget_v10_publish_request as request


def example():
    return {"run_id": "budget-run-1", "attempt": 2,
        "expected_version": 17, "attestation_id": "a" * 64,
        "attestation_sha256": "b" * 64,
        "binding_digest": "c" * 64,
        "full_manifest_digest": "d" * 64,
        "full_manifest_sha256": "e" * 64}


class BudgetV10PublishRequestTests(TestCase):
    def test_canonical_exact_versioned_fields_and_each_identity_change(self):
        value = example()
        body = request.body(**value)
        self.assertEqual(set(body), {"schemaVersion", "runId", "attempt",
            "expectedVersion", "attestationId", "attestationSha256",
            "bindingDigest", "fullManifestDigest", "fullManifestSha256"})
        self.assertEqual(body["schemaVersion"], request.SCHEMA)
        self.assertEqual(request.request_digest(**value), digest(body))
        self.assertEqual(canonical(body).encode("utf-8")[0], ord("{"))
        for key, changed in (("run_id", "other-run"), ("attempt", 3),
                ("expected_version", 18), ("attestation_id", "f" * 64),
                ("attestation_sha256", "f" * 64),
                ("binding_digest", "f" * 64),
                ("full_manifest_digest", "f" * 64),
                ("full_manifest_sha256", "f" * 64)):
            with self.subTest(key=key):
                self.assertNotEqual(request.request_digest(**value),
                    request.request_digest(**{**value, key: changed}))

    def test_bool_invalid_digest_and_out_of_range_reject(self):
        value = example()
        for key, changed in (("attempt", True), ("attempt", 0),
                ("expected_version", False), ("expected_version", 0),
                ("attestation_id", "A" * 64),
                ("binding_digest", "short"), ("run_id", "wrong/id")):
            with self.subTest(key=key), self.assertRaises(AnalysisContractError):
                request.request_digest(**{**value, key: changed})
