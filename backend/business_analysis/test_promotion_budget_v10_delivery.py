"""Pure v10 candidate proof checks; no DB/file-run authorization."""
from copy import deepcopy
import io
from unittest import TestCase

from . import budget_excel, promotion_budget_v10, volume_delivery, volume_files, volume_plan
from .contracts import AnalysisContractError, digest
from .report_files import Column, Table
from .test_promotion_budget_v10 import roots


def example(*, with_budget):
    source = roots(with_budget=with_budget)
    candidate = promotion_budget_v10.project(**source)
    trial = source["trial_proof"]
    tables = [{"key": "promotion-approved-actions-v1", "rowCount": 1,
        "rowDigest": trial["actionRowDigest"]},
        {"key": "promotion-trial-source-scope"},
        {"key": "promotion-trial-boundaries"}]
    tables.extend({"key": key, "rowCount": count, "rowDigest": sha}
        for key, count, sha in zip(candidate.proof["tableKeys"],
            candidate.proof["tableRowCounts"],
            candidate.proof["tableRowDigests"]))
    tables += [{"key": key} for key in trial["promotionTableKeys"]]
    first = {"nativeBudgetSheets": candidate.proof["nativeBudgetSheets"],
        "offlineBudgetEnabled": candidate.proof["offlineBudgetEnabled"]}
    if with_budget:
        first["budgetCalculator"] = budget_excel.build(candidate.excel_budget,
            budget_excel.TITLES, formula_version=2)[1]
    manifest = {"reportId": "promotion-1", "evidenceDigest": "d" * 64,
        "tableSchemaDigest": "9" * 64,
        "promotionFileProof": {"contentDtoDigest": trial["contentDtoDigest"],
            "humanReviewDigest": trial["humanReviewDigest"],
            "proofDigest": trial["promotionFileProofDigest"]},
        "promotionTrialProof": trial, "tables": tables, "volumes": [first]}
    if with_budget:
        manifest["budgetPlanDigest"] = candidate.proof["budgetPlanDigest"]
    return candidate.proof, manifest


class PromotionBudgetV10DeliveryTests(TestCase):
    def test_renderer9_still_rejects_any_budget_payload(self):
        table = Table("one", "原表", "原值", (Column("value", "原值", "integer"),),
            [[1]], 1)
        request = volume_files.request_for([table], report_id="promotion-1",
            evidence_digest="d" * 64, renderer_version=9)
        plan = volume_plan.build(request, native_budget_sheets=3)
        streams = [volume_files.VolumeStreams(io.BytesIO(), io.BytesIO())]
        with self.assertRaises(AnalysisContractError):
            volume_files.render([table], streams, report_id="promotion-1",
                evidence_digest="d" * 64, renderer_version=9, plan=plan,
                title="旧版", metadata={"promotionFileProof": {},
                    "promotionTrialProof": {}}, offline_budget={}, excel_budget={})
        self.assertEqual(streams[0].xlsx.getvalue(), b"")
        self.assertEqual(streams[0].html.getvalue(), b"")

    def test_gap_and_budget_proofs_bind_exact_rows_and_sheets(self):
        for present in (False, True):
            proof, manifest = example(with_budget=present)
            with self.subTest(present=present):
                volume_delivery.budget_candidate_proof(proof, manifest)

    def test_rejects_row_drift_budget_spoof_and_missing_native_calculator(self):
        for present in (False, True):
            proof, manifest = example(with_budget=present)
            changed = deepcopy(manifest)
            changed["tables"][-3]["rowDigest"] = "0" * 64
            with self.subTest(present=present, case="row"), self.assertRaises(AnalysisContractError):
                volume_delivery.budget_candidate_proof(proof, changed)
            changed = deepcopy(proof)
            changed["promotionTrialProofDigest"] = "0" * 64
            changed["proofDigest"] = digest({k:v for k,v in changed.items()
                if k != "proofDigest"})
            with self.subTest(present=present, case="lineage"), self.assertRaises(AnalysisContractError):
                volume_delivery.budget_candidate_proof(changed, manifest)
            changed = deepcopy(manifest)
            changed["volumes"][0]["nativeBudgetSheets"] = 0 if present else 3
            with self.subTest(present=present, case="sheets"), self.assertRaises(AnalysisContractError):
                volume_delivery.budget_candidate_proof(proof, changed)
        proof, manifest = example(with_budget=True)
        del manifest["volumes"][0]["budgetCalculator"]
        with self.assertRaises(AnalysisContractError):
            volume_delivery.budget_candidate_proof(proof, manifest)
