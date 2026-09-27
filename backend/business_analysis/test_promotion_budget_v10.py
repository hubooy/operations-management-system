from copy import deepcopy
from unittest import TestCase

from . import budget, budget_excel, budget_reference, promotion_budget_v10 as renderer10
from . import volume_files, volume_plan
from .contracts import AnalysisContractError, canonical, digest
from .test_budget import fixture


SHA = "a" * 64


def roots(*, with_budget=True):
    plan, baselines = fixture()
    result = budget.calculate(plan, baselines)
    binding = budget_reference.make_binding(plan, report_id="promotion-1",
        owner_email="owner@example.com", scope=None, evidence_run_id="evidence-1",
        evidence_version=3, evidence_plan_digest="b" * 64,
        catalog_digest="c" * 64, sealed_digest="d" * 64)
    result.update(evidenceRunId="evidence-1", evidenceVersion=3,
        evidencePlanDigest="b" * 64)
    reference = budget_reference.make_reference("budget-1", binding)
    approved = {"reportId": "promotion-1", "evidenceRunId": "evidence-1",
        "evidenceVersion": 3, "sealedDigest": "d" * 64,
        "ownerEmail": "owner@example.com", "scope": None,
        "humanReview": {"status": "approved", "reviewDigest": "e" * 64}}
    report = {**{key: approved[key] for key in ("reportId", "evidenceRunId",
        "evidenceVersion", "sealedDigest", "ownerEmail", "scope")},
        "role": "admin", "budgetRef": deepcopy(reference) if with_budget else None}
    if with_budget:
        approved["budgetPlanDigest"] = binding["planDigest"]
    trial = {"schemaVersion": "business-promotion-trial-file-proof-v2",
        "rendererVersion": 9, "reportId": "promotion-1",
        "contentDtoDigest": SHA, "humanReviewDigest": "e" * 64,
        "promotionFileProofDigest": "f" * 64,
        "sealedSourcesDigest": "1" * 64,
        "sourceDescriptorDigest": "2" * 64,
        "tableSchemaDigest": "3" * 64,
        "actionTableKey": "promotion-approved-actions-v1", "actionRowCount": 1,
        "actionRowDigest": "4" * 64,
        "scopeTableKeys": ["promotion-trial-source-scope", "promotion-trial-boundaries"],
        "promotionTableKeys": ["promotion-keyword_sku", "promotion-keyword_sku_context"],
        "budgetDelivered": False}
    trial["proofDigest"] = digest(trial)
    return {"trial_proof": trial, "approved_binding": approved,
        "report_binding": report, "approved_dto_digest": SHA,
        "budget_material": {"binding": binding, "reference": reference,
            "result": result} if with_budget else None}


class PromotionBudgetV10Tests(TestCase):
    def test_missing_budget_is_one_visible_gap_with_no_money_or_formula(self):
        candidate = renderer10.project(**roots(with_budget=False))
        self.assertEqual(candidate.proof["status"], "missing_fixed_budget")
        self.assertEqual(candidate.proof["nativeBudgetSheets"], 0)
        self.assertFalse(candidate.proof["editableAllocation"])
        self.assertIsNone(candidate.offline_budget)
        self.assertIsNone(candidate.excel_budget)
        self.assertEqual([table.key for table in candidate.tables],
            ["promotion-budget-gap-v1"])
        self.assertNotIn("10000", canonical(candidate.proof))

    def test_fixed_budget_is_recomputed_and_binds_both_tables_and_excel_inputs(self):
        source = roots()
        before = deepcopy(source)
        candidate = renderer10.project(**source)
        self.assertEqual(source, before)
        self.assertEqual(candidate.proof["status"], "reconciled_fixed_budget_candidate")
        self.assertTrue(candidate.proof["candidateOnly"])
        self.assertEqual(candidate.proof["nativeBudgetSheets"], 3)
        self.assertEqual(candidate.proof["tableRowCounts"], [2, 4, 2])
        self.assertEqual(candidate.tables[0].rows[0][15], 6000)
        self.assertEqual(candidate.tables[1].rows[0][11], 30000)
        self.assertEqual(candidate.offline_budget["planDigest"],
            source["approved_binding"]["budgetPlanDigest"])
        self.assertTrue(candidate.excel_budget["excelEnabled"])
        native, native_proof = budget_excel.build(candidate.excel_budget,
            budget_excel.TITLES, formula_version=2)
        self.assertEqual(len(native), 3)
        self.assertEqual(native_proof["reportId"], "promotion-1")
        self.assertEqual(candidate.proof["proofDigest"], digest({key: value
            for key, value in candidate.proof.items() if key != "proofDigest"}))
        request = volume_files.request_for(candidate.tables,
            report_id="promotion-1", evidence_digest="d" * 64,
            renderer_version=10)
        plan = volume_plan.build(request, native_budget_sheets=3,
            max_tables=4)
        self.assertTrue(volume_plan.verify(plan, request,
            native_budget_sheets=3, max_tables=4))
        self.assertEqual(plan["volumes"][0]["nativeBudgetSheets"], 3)
        self.assertEqual(plan["volumes"][0]["kind"], "data_and_budget")

    def test_missing_fixed_record_cannot_downgrade_to_gap(self):
        source = roots()
        source["budget_material"] = None
        with self.assertRaises(AnalysisContractError):
            renderer10.project(**source)
        source = roots(with_budget=False)
        source["budget_material"] = roots()["budget_material"]
        with self.assertRaises(AnalysisContractError):
            renderer10.project(**source)

    def test_cross_report_evidence_owner_and_review_are_rejected(self):
        for part, path, value in (
            ("report_binding", "reportId", "other"),
            ("report_binding", "sealedDigest", "f" * 64),
            ("report_binding", "ownerEmail", "other@example.com"),
            ("approved_binding", "budgetPlanDigest", "f" * 64),
            ("approved_dto_digest", None, "f" * 64),
        ):
            source = roots()
            if path is None:
                source[part] = value
            else:
                source[part][path] = value
            with self.subTest(part=part, path=path), self.assertRaises(AnalysisContractError):
                renderer10.project(**source)
        source = roots()
        source["approved_binding"]["humanReview"]["status"] = "pending"
        with self.assertRaises(AnalysisContractError):
            renderer10.project(**source)

    def test_tampered_trial_and_budget_result_are_rejected(self):
        source = roots()
        source["trial_proof"]["sourceDescriptorDigest"] = "f" * 64
        with self.assertRaises(AnalysisContractError):
            renderer10.project(**source)
        for change in ("plan", "allocation", "row", "reference"):
            source = roots()
            if change == "plan":
                source["budget_material"]["result"]["plan"]["totalBudgetCents"] += 1
            elif change == "allocation":
                source["budget_material"]["result"]["allocation"]["allocatedCents"] += 1
            elif change == "row":
                source["budget_material"]["result"]["scenarios"][0]["rows"][0]["budgetCents"] += 1
            else:
                source["budget_material"]["reference"]["id"] = "other-budget"
            with self.subTest(change=change), self.assertRaises(AnalysisContractError):
                renderer10.project(**source)
        source = roots()
        source["budget_material"]["result"]["scenarios"] = []
        with self.assertRaises(AnalysisContractError):
            renderer10.project(**source)

    def test_missing_metric_preserves_null_and_mixed_reporting_basis_is_not_totalled(self):
        source = roots()
        result = source["budget_material"]["result"]
        plan = result["plan"]
        baselines = [row["baseline"] for row in result["scenarios"][0]["rows"]]
        baselines[0]["metrics"]["reportedGmvCents"] = None
        baselines[0]["source"] = "jd_ads"
        baselines[1]["source"] = "other_ads"
        updated = budget.calculate(plan, baselines)
        updated.update(evidenceRunId="evidence-1", evidenceVersion=3,
            evidencePlanDigest="b" * 64)
        source["budget_material"]["result"] = updated
        candidate = renderer10.project(**source)
        self.assertIsNone(candidate.tables[0].rows[0][11])
        self.assertIsNone(candidate.tables[1].rows[0][11])
        self.assertIsNone(updated["scenarios"][0]["summary"]["projectedAttributedGmvCents"])
        self.assertEqual(candidate.tables[1].rows[0][14], "unavailable")
        self.assertEqual(candidate.tables[0].rows[0][-1], "jd_ads")
        self.assertTrue(candidate.tables[2].rows[0][1])
        self.assertIsNone(candidate.tables[2].rows[0][4])


if __name__ == "__main__":
    import unittest
    unittest.main()
