"""The receipt is a MAC over a freshly verified 0067 proof, never a SHA claim."""
from __future__ import annotations

import hashlib
import hmac
import json
import unittest

from .contracts import AnalysisContractError, canonical
from . import promotion_budget_verifier_receipt_v11 as receipt


class BudgetV11ProtectedReceiptPureTests(unittest.TestCase):
    def setUp(self):
        self.assertion = {"schemaVersion":
            "business-promotion-budget-v11-staged-attestation-v1",
            "runId": "synthetic-run", "attempt": 1, "runVersion": 7,
            "bindingDigest": "a" * 64,
            "files": {"files": [{"format": "html", "sha256": "b" * 64},
                                  {"format": "xlsx", "sha256": "c" * 64}],
                      "manifestFile": {"format": "json", "sha256": "d" * 64}}}
        for name in ("compactJsonSha256", "fullManifestSha256",
                     "fullManifestDigest", "approvedContentDigest",
                     "humanReviewDigest", "budgetProofDigest",
                     "slimProofDigest", "fileByteVerificationDigest",
                     "htmlRowsDigest", "xlsxOpcFormulaDigest",
                     "owningVerificationDigest", "reportSnapshotSha256",
                     "workflowInputSha256"):
            self.assertion[name] = "e" * 64

    def payload(self):
        raw = canonical(self.assertion)
        return receipt.body(key_id="synthetic-key-1", run_id="synthetic-run",
            attempt=1, report_id="synthetic-report",
            owner_email="synthetic@example.invalid",
            attestation_id="f" * 64, attestation_text=raw)

    def test_mac_is_purpose_bound_and_covers_files_and_process_claims(self):
        value = self.payload()
        self.assertEqual(value["purpose"], receipt.PURPOSE)
        self.assertEqual(value["attestation"]["files"]["files"][1]["sha256"],
            "c" * 64)
        raw = canonical(value)
        secret = b"synthetic-only-32-byte-secret-value"
        signature = receipt._mac(secret, raw)
        self.assertEqual(signature, hmac.new(secret, receipt.DOMAIN + raw.encode(),
            hashlib.sha256).hexdigest())
        self.assertNotEqual(signature, receipt._mac(b"another-synthetic-secret-32-bytes", raw))
        value["attestation"]["xlsxOpcFormulaDigest"] = "0" * 64
        self.assertNotEqual(signature, receipt._mac(secret, canonical(value)))
        value = self.payload()
        value["attestation"]["files"]["files"][0]["sha256"] = "0" * 64
        self.assertNotEqual(signature, receipt._mac(secret, canonical(value)))
        self.assertNotIn("readyAuthorized", value)

    def test_caller_digest_shape_does_not_make_body_signed(self):
        value = self.payload()
        self.assertEqual(value["attestationSha256"], hashlib.sha256(
            canonical(self.assertion).encode()).hexdigest())
        with self.assertRaises(AnalysisContractError):
            receipt.body(key_id="synthetic-key-1", run_id="synthetic-run",
                attempt=1, report_id="synthetic-report",
                owner_email="synthetic@example.invalid",
                attestation_id="f" * 64,
                attestation_text=json.dumps(self.assertion))
        with self.assertRaises(AnalysisContractError):
            receipt._mac(b"weak", canonical(value))


if __name__ == "__main__":
    unittest.main()
