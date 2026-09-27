from copy import deepcopy
from fractions import Fraction
import random
from unittest import TestCase
from . import budget
from .contracts import AnalysisContractError


def fixture():
    plan = {"totalBudgetCents": 10000, "reserveCents": 1000, "horizonDays": 15, "observationDays": 7,
        "reviewAfterSpendBps": 2000, "minimumClicks": 30, "minimumOrderLines": 3,
        "targets": [{"sourceKey": "ads", "dimension": "sku", "rowIndex": i, "rowId": str(i+1)*64, "weight": 2-i,
            "minBudgetCents": 0, "maxBudgetCents": 10000, "ownerRole": "投放运营", "minimumRoasBps": 40000} for i in range(2)],
        "scenarios": [{"name": "效率不变", "cpcFactorBps": 10000, "orderRateFactorBps": 10000, "orderValueFactorBps": 10000, "contributionMarginBps": 2000},
            {"name": "成本上升且订单效率下降", "cpcFactorBps": 12000, "orderRateFactorBps": 8000, "orderValueFactorBps": 9000, "contributionMarginBps": 2000}]}
    baselines = [{"rowId": target["rowId"], "entity": {"skuId": f"S{i}"}, "days": 30, "datesPresent": True,
        "metrics": {"spendCents": 3000, "clicks": 300, "reportedOrderLines": 30, "reportedGmvCents": 15000}} for i, target in enumerate(plan["targets"])]
    return plan, baselines


class BudgetTests(TestCase):
    def test_allocation_and_independent_arithmetic_for_two_scenarios(self):
        plan, bases = fixture()
        original = deepcopy(plan)
        result = budget.calculate(plan, bases)
        self.assertEqual(plan, original)
        self.assertEqual(result["allocation"], {"totalBudgetCents": 10000, "reservedCents": 1000, "allocatedCents": 9000, "unallocatedCents": 0, "targetCount": 2, "scope": "selected_targets_only"})
        stable, stress = result["scenarios"]
        self.assertEqual([r["budgetCents"] for r in stable["rows"]], [6000, 3000])
        self.assertEqual(stable["summary"]["projectedAttributedGmvCents"], 45000)
        self.assertEqual(stable["summary"]["assumedContributionAfterAdCents"], 0)
        self.assertEqual(stable["summary"]["breakEvenRoas"], 5)
        self.assertEqual(stress["summary"]["projectedAttributedGmvCents"], 27000)
        self.assertEqual(stress["summary"]["assumedContributionAfterAdCents"], -3600)
        self.assertEqual(stable["rows"][0]["equivalentBaselineSpendCents"], 1500)
        self.assertEqual(stable["rows"][0]["reviewAfterSpendCents"], 1200)

    def test_caps_reserves_remainders_and_permutations(self):
        plan, _ = fixture()
        plan["targets"][0]["maxBudgetCents"] = 2000
        self.assertEqual(budget.allocate(plan), [2000, 7000])
        plan["targets"][1]["maxBudgetCents"] = 4000
        self.assertEqual(budget.allocate(plan), [2000, 4000])
        plan["targets"][1]["minBudgetCents"] = 10000
        with self.assertRaises(AnalysisContractError):
            budget.calculate(plan, fixture()[1])
        rng = random.Random(41)
        for _ in range(100):
            plan, _ = fixture()
            plan["totalBudgetCents"] = rng.randrange(1001, 30000)
            for t in plan["targets"]:
                t["weight"] = rng.randrange(1, 20)
                t["maxBudgetCents"] = rng.randrange(0, plan["totalBudgetCents"])
            original = dict(zip((t["rowId"] for t in plan["targets"]), budget.allocate(plan)))
            plan["targets"].reverse()
            reordered = dict(zip((t["rowId"] for t in plan["targets"]), budget.allocate(plan)))
            self.assertEqual(original, reordered)
            self.assertEqual(sum(original.values()), min(plan["totalBudgetCents"]-plan["reserveCents"], sum(t["maxBudgetCents"] for t in plan["targets"])))

    def test_missing_dates_zero_spend_and_unknown_margin_are_not_zero_forecasts(self):
        for kind in ("missing", "dates", "zero"):
            plan, bases = fixture()
            if kind == "missing": bases[1]["metrics"]["reportedGmvCents"] = None
            if kind == "dates": bases[1]["datesPresent"] = False
            if kind == "zero": bases[1]["metrics"]["spendCents"] = 0
            result = budget.calculate(plan, bases)["scenarios"][0]
            self.assertIsNone(result["summary"]["projectedAttributedGmvCents"])
            self.assertEqual(result["summary"]["knownAttributedGmvCents"], 30000)
            self.assertEqual(result["summary"]["unavailableTargets"], 1)
            self.assertIsNone(result["rows"][1]["projectedClicks"])
        plan, bases = fixture()
        plan["scenarios"][0]["contributionMarginBps"] = None
        result = budget.calculate(plan, bases)["scenarios"][0]
        self.assertEqual(result["summary"]["projectedAttributedGmvCents"], 45000)
        self.assertIsNone(result["summary"]["assumedContributionAfterAdCents"])

    def test_invalid_parameters_and_low_samples_remain_explicit(self):
        for field, value in (("reserveCents", True), ("totalBudgetCents", 1.5), ("horizonDays", 0), ("unexpected", "=CMD()")):
            plan, bases = fixture(); plan[field] = value
            with self.assertRaises(AnalysisContractError): budget.calculate(plan, bases)
        plan, bases = fixture(); bases[0]["metrics"]["clicks"] = 2
        self.assertEqual(budget.calculate(plan, bases)["scenarios"][0]["rows"][0]["status"], "low_sample_scenario")
        plan, bases = fixture(); plan["targets"][1] = deepcopy(plan["targets"][0])
        with self.assertRaises(AnalysisContractError): budget.calculate(plan, bases)
        self.assertEqual(budget.rounded(Fraction(1, 2)), 1)
        self.assertEqual(budget.rounded(Fraction(-1, 2)), -1)
        self.assertEqual(budget.rounded(Fraction(1, 3), 4), .3333)

    def test_mixed_platform_reporting_bases_are_never_added_as_one_revenue(self):
        plan, bases = fixture()
        bases[0]["source"], bases[1]["source"] = "jd_promotion", "tmall_promotion"
        summary = budget.calculate(plan, bases)["scenarios"][0]["summary"]
        self.assertTrue(summary["mixedReportingBases"])
        self.assertIsNone(summary["projectedAttributedGmvCents"])
        self.assertIsNone(summary["knownAttributedGmvCents"])
        self.assertIsNone(summary["assumedContributionAfterAdCents"])
        self.assertEqual([row["knownAttributedGmvCents"] for row in summary["byReportingBasis"]], [30000, 15000])
