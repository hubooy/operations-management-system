from django.test import SimpleTestCase

from sales.summary import _align_to_cutoff, _custom_period, _period_for


class SalesComparisonPeriodTests(SimpleTestCase):
    def test_same_month_custom_range_compares_previous_month_dates(self) -> None:
        self.assertEqual(
            _custom_period("2026-09-05", "2026-09-20"),
            {
                "startDate": "2026-09-05",
                "endDate": "2026-09-20",
                "previousStartDate": "2026-08-05",
                "previousEndDate": "2026-08-20",
            },
        )

    def test_single_day_custom_range_compares_previous_day(self) -> None:
        period = _custom_period("2026-09-20", "2026-09-20")
        self.assertEqual(period["previousStartDate"], "2026-09-19")
        self.assertEqual(period["previousEndDate"], "2026-09-19")

    def test_complete_custom_month_compares_complete_previous_month(self) -> None:
        period = _custom_period("2026-09-01", "2026-09-30")
        self.assertEqual(period["previousStartDate"], "2026-08-01")
        self.assertEqual(period["previousEndDate"], "2026-08-31")

    def test_cross_month_custom_range_compares_previous_equal_length_period(self) -> None:
        period = _custom_period("2026-08-25", "2026-09-05")
        self.assertEqual(period["previousStartDate"], "2026-08-13")
        self.assertEqual(period["previousEndDate"], "2026-08-24")

    def test_missing_previous_month_dates_clamp_to_month_end(self) -> None:
        period = _custom_period("2026-03-30", "2026-03-31")
        self.assertEqual(period["previousStartDate"], "2026-02-28")
        self.assertEqual(period["previousEndDate"], "2026-02-28")

    def test_cutoff_keeps_calendar_aligned_custom_comparison(self) -> None:
        requested = _custom_period("2026-09-01", "2026-09-30")
        period, adjusted = _align_to_cutoff("custom", requested, "2026-09-20")
        self.assertTrue(adjusted)
        self.assertEqual(period["startDate"], "2026-09-01")
        self.assertEqual(period["endDate"], "2026-09-20")
        self.assertEqual(period["previousStartDate"], "2026-08-01")
        self.assertEqual(period["previousEndDate"], "2026-08-20")

    def test_last_30_days_remains_a_rolling_comparison(self) -> None:
        self.assertEqual(
            _period_for("last30", "2026-09-30"),
            {
                "startDate": "2026-09-01",
                "endDate": "2026-09-30",
                "previousStartDate": "2026-08-02",
                "previousEndDate": "2026-08-31",
            },
        )

    def test_completed_current_month_compares_complete_previous_month(self) -> None:
        period = _period_for("month", "2026-09-30")
        self.assertEqual(period["previousStartDate"], "2026-08-01")
        self.assertEqual(period["previousEndDate"], "2026-08-31")

    def test_month_to_date_clamps_at_shorter_previous_month_end(self) -> None:
        period = _period_for("month", "2026-03-30")
        self.assertEqual(period["previousStartDate"], "2026-02-01")
        self.assertEqual(period["previousEndDate"], "2026-02-28")
