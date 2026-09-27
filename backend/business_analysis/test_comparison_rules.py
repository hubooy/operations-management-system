from copy import deepcopy
from unittest import TestCase

from .comparison_rules import (
    PREVIOUS_EQUAL_LENGTH_V1,
    SALES_CUSTOM_CALENDAR_MONTH_V1,
    resolve_periods,
    validate_resolved_periods,
)
from .contracts import AnalysisContractError, comparison_periods, digest


def resolved(first="2026-09-05", last="2026-09-20", *, rule=SALES_CUSTOM_CALENDAR_MONTH_V1, cutoff=None):
    return resolve_periods(first, last, comparison_rule=rule, cutoff_date=cutoff)


class ComparisonRulesTests(TestCase):
    def test_same_month_partial_has_explicitly_different_baselines(self):
        calendar_rule = resolved()
        equal_rule = resolved(rule=PREVIOUS_EQUAL_LENGTH_V1)
        self.assertEqual((calendar_rule["previous"]["startDate"], calendar_rule["previous"]["endDate"]),
                         ("2026-08-05", "2026-08-20"))
        self.assertEqual((equal_rule["previous"]["startDate"], equal_rule["previous"]["endDate"]),
                         ("2026-08-20", "2026-09-04"))
        self.assertEqual((calendar_rule["current"]["days"], calendar_rule["previous"]["days"]), (16, 16))

    def test_complete_month_uses_complete_previous_month_and_reveals_day_count(self):
        result = resolved("2026-09-01", "2026-09-30")
        self.assertEqual((result["previous"]["startDate"], result["previous"]["endDate"],
                          result["previous"]["endExclusive"]),
                         ("2026-08-01", "2026-08-31", "2026-09-01"))
        self.assertEqual((result["current"]["days"], result["previous"]["days"]), (30, 31))
        self.assertEqual(resolved("2026-09-01", "2026-09-30", rule=PREVIOUS_EQUAL_LENGTH_V1)["previous"]["startDate"],
                         "2026-08-02")

    def test_same_month_shorter_previous_month_clamps_both_endpoints(self):
        result = resolved("2026-03-30", "2026-03-31")
        self.assertEqual((result["previous"]["startDate"], result["previous"]["endDate"],
                          result["previous"]["days"]), ("2026-02-28", "2026-02-28", 1))
        leap = resolved("2024-03-30", "2024-03-31")
        self.assertEqual((leap["previous"]["startDate"], leap["previous"]["endDate"]),
                         ("2024-02-29", "2024-02-29"))

    def test_cross_month_range_uses_previous_equal_length_for_both_rules(self):
        for rule in (SALES_CUSTOM_CALENDAR_MONTH_V1, PREVIOUS_EQUAL_LENGTH_V1):
            with self.subTest(rule=rule):
                result = resolved("2026-08-25", "2026-09-05", rule=rule)
                self.assertEqual((result["previous"]["startDate"], result["previous"]["endDate"],
                                  result["previous"]["days"]), ("2026-08-13", "2026-08-24", 12))

    def test_single_day_uses_previous_day_for_both_rules(self):
        for rule in (SALES_CUSTOM_CALENDAR_MONTH_V1, PREVIOUS_EQUAL_LENGTH_V1):
            self.assertEqual(resolved("2026-09-20", "2026-09-20", rule=rule)["previous"]["startDate"],
                             "2026-09-19")

    def test_cutoff_recalculates_both_baselines_and_preserves_requested(self):
        result = resolved("2026-09-01", "2026-09-30", cutoff="2026-09-20")
        self.assertTrue(result["periodAdjustedToDataCutoff"])
        self.assertEqual((result["requested"]["endDate"], result["current"]["endDate"]),
                         ("2026-09-30", "2026-09-20"))
        self.assertEqual((result["previous"]["startDate"], result["previous"]["endDate"]),
                         ("2026-08-01", "2026-08-20"))
        self.assertEqual((result["yearAgo"]["startDate"], result["yearAgo"]["endDate"]),
                         ("2025-09-01", "2025-09-20"))
        self.assertEqual(resolved("2026-09-01", "2026-09-30", cutoff="2026-10-01")["periodAdjustedToDataCutoff"],
                         False)

    def test_leap_year_yoy_clamps_endpoints_and_exposes_unequal_days(self):
        result = resolved("2024-02-29", "2024-03-01")
        self.assertEqual((result["yearAgo"]["startDate"], result["yearAgo"]["endDate"],
                          result["yearAgo"]["days"]), ("2023-02-28", "2023-03-01", 2))
        self.assertEqual(result["current"]["days"], 2)
        full_february = resolved("2024-02-01", "2024-02-29")
        self.assertEqual((full_february["current"]["days"], full_february["yearAgo"]["days"]), (29, 28))

    def test_exact_schema_and_complete_windows_are_stable(self):
        result = resolved(cutoff="2026-09-18")
        self.assertEqual(set(result), {"schemaVersion", "timezone", "comparisonRule", "requested",
                                       "dataCutoffDate", "periodAdjustedToDataCutoff", "current", "previous", "yearAgo"})
        for window in ("requested", "current", "previous", "yearAgo"):
            self.assertEqual(set(result[window]), {"startDate", "endDate", "endExclusive", "days"})
        self.assertEqual(validate_resolved_periods(result), result)

    def test_unknown_rules_dates_and_unavailable_cutoff_fail_closed(self):
        cases = [
            ("2026-09-05", "2026-09-20", "changed_rule", None),
            ("20260905", "2026-09-20", SALES_CUSTOM_CALENDAR_MONTH_V1, None),
            ("2026-09-20", "2026-09-05", SALES_CUSTOM_CALENDAR_MONTH_V1, None),
            ("2026-01-01", "2026-04-04", SALES_CUSTOM_CALENDAR_MONTH_V1, None),
            ("2026-09-05", "2026-09-20", SALES_CUSTOM_CALENDAR_MONTH_V1, "2026-09-04"),
            ("2026-09-05", "2026-09-20", SALES_CUSTOM_CALENDAR_MONTH_V1, "2026-09-xx"),
        ]
        for first, last, rule, cutoff in cases:
            with self.subTest(first=first, last=last, rule=rule, cutoff=cutoff):
                with self.assertRaises(AnalysisContractError):
                    resolve_periods(first, last, comparison_rule=rule, cutoff_date=cutoff)

    def test_persisted_resolution_rejects_missing_or_changed_fields(self):
        original = resolved()
        for mutation in (
            lambda value: value["previous"].update(endDate="2026-08-21"),
            lambda value: value["current"].update(days=True),
            lambda value: value.update(comparisonRule=PREVIOUS_EQUAL_LENGTH_V1),
            lambda value: value["requested"].pop("endExclusive"),
            lambda value: value.update(extra="silent"),
            lambda value: value.update(dataCutoffDate="2026-09-01"),
        ):
            with self.subTest(mutation=mutation):
                altered = deepcopy(original)
                mutation(altered)
                with self.assertRaises(AnalysisContractError):
                    validate_resolved_periods(altered)

    def test_legacy_period_contract_and_digest_do_not_change(self):
        legacy = comparison_periods("2026-09-05", "2026-09-20")
        self.assertEqual(legacy["comparisonRule"], "previous_equal_length_and_previous_year_clamped")
        self.assertEqual(legacy["previous"]["startDate"], "2026-08-20")
        self.assertEqual(digest(legacy), "22f06992318057fa9991713296851fd5c25d6739bf2d12ced78eb5fe6f6c2415")
        modern = resolved(rule=PREVIOUS_EQUAL_LENGTH_V1)
        for window in ("current", "previous", "yearAgo"):
            self.assertEqual(modern[window], legacy[window])
