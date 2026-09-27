"""Multi-day sealed inputs yield only exact-day TOP observations."""
from unittest import TestCase

from .contracts import AnalysisContractError
from . import market_dynamics_v2 as service
from ai_assistant.test_business_promotion_market_runtime_contract import inputs, source


class MarketObservationV2Tests(TestCase):
    def test_interval_sources_select_only_corresponding_days(self):
        current, baseline, _, _ = inputs()
        result = service.rank_entry_exit(*current[:3], *baseline[:3],
            "2026-09-03", "2026-08-31")
        self.assertEqual(result["algorithmVersion"], service.ALGORITHM_VERSION)
        self.assertEqual(result["observationCoverage"]["bothDatesPresent"], True)
        rows = {row["skuId"]: row for row in result["rows"]}
        self.assertEqual(rows["A"]["status"], "both_observed")
        self.assertEqual(rows["A"]["rankImprovement"], 0)
        self.assertNotIn("B", rows)  # B was observed only on another current day.
        self.assertNotIn("C", rows)  # C was observed only on another baseline day.
        self.assertFalse(result["authorityVerified"])
        self.assertFalse(result["ownProductIdentityVerified"])

    def test_top_absence_is_not_zero_and_missing_date_is_distinct(self):
        current = source("current", ("A", "B"))
        baseline = source("previous", ("C", "D"))
        result = service.rank_entry_exit(*current[:3], *baseline[:3],
            "2026-09-03", "2026-08-31")
        rows = {row["skuId"]: row for row in result["rows"]}
        self.assertEqual(rows["A"]["status"], "entered_observed_top_sample")
        self.assertEqual(rows["A"]["baseline"]["status"], "not_observed_in_top_sample")
        self.assertIsNone(rows["A"]["baseline"]["metrics"])
        self.assertEqual(rows["C"]["status"], "left_observed_top_sample")
        self.assertIsNone(rows["C"]["current"]["rank"])
        no_baseline_day = source("previous", ("Z",), observe_last=False)
        missing = service.rank_entry_exit(*current[:3], *no_baseline_day[:3],
            "2026-09-03", "2026-08-31")
        self.assertFalse(missing["observationCoverage"]["bothDatesPresent"])
        absent = next(row for row in missing["rows"] if row["skuId"] == "A")
        self.assertEqual(absent["status"], "insufficient_date_coverage")
        self.assertEqual(absent["baseline"]["status"], "date_not_covered")
        self.assertIsNone(absent["baseline"]["metrics"])

    def test_wrong_observation_pair_grain_and_source_scope_reject(self):
        current, baseline, _, _ = inputs()
        with self.assertRaises(AnalysisContractError):
            service.rank_entry_exit(*current[:3], *baseline[:3],
                "2026-09-03", "2026-08-30")
        spu = source("previous", ("A",), dimension="SPU")
        with self.assertRaises(AnalysisContractError):
            service.rank_entry_exit(*current[:3], *spu[:3],
                "2026-09-03", "2026-08-31")
