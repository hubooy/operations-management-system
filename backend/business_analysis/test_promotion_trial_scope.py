from copy import deepcopy
import unittest

from .contracts import AnalysisContractError
from .promotion_trial_scope import tables


def fixture():
    sources = [
        {"key": key, "domain": "netshop", "query": {
            "platform": "京东", "shop": "合成店", "dataset": "promotion",
            "startDate": "2026-08-01", "endDate": "2026-08-03", "window": window}}
        for key, window in (("promotion-current", "current"),
                            ("promotion-previous", "previous"))]
    infos = {}
    for key in ("promotion-current", "promotion-previous"):
        infos[key] = {"pageCount": 1, "metadata": {
            "sourceRevision": "synthetic-revision",
            "coverage": {"status": "missing_dates",
                "presentDates": ["2026-08-01"],
                "missingDates": ["2026-08-02", "2026-08-03"]}},
            "expected": {"sourceRef": "synthetic-source", "rowCount": 1,
                "reconciled": True, "evidenceDigest": "a" * 64,
                "metrics": {"adCost": {"value": None, "presentRows": 0,
                    "missingRows": 1}}}}
    return sources, infos


class PromotionTrialScopeTests(unittest.TestCase):
    def test_actual_comparison_dates_and_missing_values_are_visible(self):
        sources, infos = fixture()
        scope, boundaries = tables(sources, infos)
        self.assertEqual(scope.row_count, 2)
        self.assertEqual([row[0] for row in scope.rows],
            ["promotion-current", "promotion-previous"])
        self.assertEqual(scope.rows[0][8:10], ["2026-08-01", "2026-08-03"])
        self.assertEqual(scope.rows[1][8:10], ["2026-07-29", "2026-07-31"])
        self.assertEqual(scope.rows[0][15], "2026-08-02、2026-08-03")
        self.assertEqual(scope.rows[0][16], "adCost缺1行")
        self.assertEqual(scope.rows[0][12], 1)
        self.assertEqual(boundaries.row_count, 6)
        self.assertTrue(any("不能相加" in row[2] for row in boundaries.rows))
        self.assertTrue(any("未交付" in row[1] for row in boundaries.rows))

    def test_no_source_facts_are_added_or_filled_with_zero(self):
        sources, infos = fixture()
        infos["promotion-current"]["metadata"]["coverage"].pop("missingDates")
        scope, _ = tables(sources, infos)
        self.assertEqual(scope.rows[0][15], "未提供")
        self.assertEqual(scope.rows[0][16], "adCost缺1行")
        self.assertNotIn("0", scope.rows[0][16])

    def test_duplicate_identity_and_incomplete_reconciliation_reject(self):
        sources, infos = fixture()
        duplicate = deepcopy(sources)
        duplicate[1]["key"] = duplicate[0]["key"]
        with self.assertRaises(AnalysisContractError):
            tables(duplicate, infos)
        infos["promotion-current"]["expected"]["reconciled"] = False
        with self.assertRaises(AnalysisContractError):
            tables(sources, infos)


if __name__ == "__main__":
    unittest.main()
