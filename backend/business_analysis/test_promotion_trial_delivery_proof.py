"""Pure v9 proof checks independent of owning database state."""
from copy import deepcopy
from unittest import TestCase

from .contracts import AnalysisContractError, digest
from .volume_delivery import trial_proof


def example():
    action = {"key": "promotion-approved-actions-v1", "rowCount": 2,
        "rowDigest": "a"*64}
    manifest = {"reportId": "report", "sourceDescriptorDigest": "b"*64,
        "tableSchemaDigest": "1"*64,
        "promotionFileProof": {"contentDtoDigest": "c"*64,
            "humanReviewDigest": "d"*64, "proofDigest": "e"*64},
        "tables": [action,
            {"key": "promotion-trial-source-scope"},
            {"key": "promotion-trial-boundaries"},
            {"key": "promotion-keyword_sku"},
            {"key": "promotion-keyword_sku_context"}],
        "volumes": [{"nativeBudgetSheets": 0, "offlineBudgetEnabled": False}]}
    proof = {"schemaVersion": "business-promotion-trial-file-proof-v2",
        "rendererVersion": 9, "reportId": "report",
        "contentDtoDigest": "c"*64, "humanReviewDigest": "d"*64,
        "promotionFileProofDigest": "e"*64,
        "sealedSourcesDigest": "f"*64,
        "sourceDescriptorDigest": "b"*64,
        "tableSchemaDigest": "1"*64,
        "actionTableKey": action["key"], "actionRowCount": 2,
        "actionRowDigest": action["rowDigest"],
        "scopeTableKeys": ["promotion-trial-source-scope", "promotion-trial-boundaries"],
        "promotionTableKeys": ["promotion-keyword_sku", "promotion-keyword_sku_context"],
        "budgetDelivered": False}
    proof["proofDigest"] = digest(proof)
    return proof, manifest


class TrialProofTests(TestCase):
    def test_exact_action_approval_source_and_budget(self):
        proof, manifest = example()
        trial_proof(proof, manifest)
        for field, replacement in (("schemaVersion", "business-promotion-trial-file-proof-v1"),
                ("actionRowDigest", "0"*64),
                ("humanReviewDigest", "0"*64), ("sourceDescriptorDigest", "0"*64),
                ("tableSchemaDigest", "0"*64),
                ("budgetDelivered", True)):
            changed = deepcopy(proof)
            changed[field] = replacement
            changed["proofDigest"] = digest({key: value for key, value in changed.items()
                if key != "proofDigest"})
            with self.subTest(field=field), self.assertRaises(AnalysisContractError):
                trial_proof(changed, manifest)
        manifest["volumes"][0]["nativeBudgetSheets"] = 3
        with self.assertRaises(AnalysisContractError):
            trial_proof(proof, manifest)
