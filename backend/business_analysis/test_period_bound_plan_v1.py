"""No-DB vectors for a new, non-authorizing three-window date envelope."""
from copy import deepcopy
from unittest import TestCase

from . import evidence_v4, period_bound_plan_v1 as period
from .contracts import AnalysisContractError, digest
from .test_evidence_v4 import sources, measurements


WINDOWS = ("current", "previous", "yearAgo")


def plan(*, start="2026-08-16", end="2026-09-14", windows=WINDOWS):
    entries, request = sources(windows)
    values = measurements(windows)
    for entry in entries:
        if entry["domain"] == "finance":
            entry["query"]["analysisPeriod"] = {"startDate": start,
                "endDate": end}
            first, last = start[:7], end[:7]
            all_months = []
            year, month = int(first[:4]), int(first[5:])
            while f"{year:04d}-{month:02d}" <= last:
                all_months.append(f"{year:04d}-{month:02d}")
                month += 1
                if month == 13:
                    year, month = year + 1, 1
            entry["query"]["months"] = all_months
        else:
            entry["query"].update(startDate=start, endDate=end)
    return evidence_v4.build_plan(client_request_id="period-v1-test",
        sources=entries, measurements=values, analysis_request=request)


class PeriodBoundPlanV1Tests(TestCase):
    def test_exact_30_day_three_windows_are_pinned_per_daily_source(self):
        original = plan()
        untouched = deepcopy(original)
        result = period.prepare_candidate(original)
        self.assertEqual(original, untouched)
        self.assertEqual(result["schemaVersion"], period.SCHEMA)
        self.assertEqual(result["basePlanDigest"], original["planDigest"])
        expected = {"current": ("2026-08-16", "2026-09-14"),
            "previous": ("2026-07-17", "2026-08-15"),
            "yearAgo": ("2025-08-16", "2025-09-14")}
        self.assertEqual(len(result["dailySources"]), 3)
        for row in result["dailySources"]:
            start, end = expected[row["window"]]
            self.assertEqual((row["resolvedPeriod"]["startDate"],
                row["resolvedPeriod"]["endDate"]), (start, end))
            self.assertEqual(row["resolvedPeriod"]["days"], 30)
            self.assertEqual(row["originalQueryStartDate"], "2026-08-16")
            self.assertEqual(row["originalQueryEndDate"], "2026-09-14")
            self.assertEqual(row["expectedDayCount"], 30)
            self.assertEqual(row["expectedDayDigest"], digest({
                "schemaVersion": period.DAY_SCHEMA,
                "sourceKey": row["sourceKey"], "window": row["window"],
                "dates": period._days(row["resolvedPeriod"])}))
            self.assertFalse(row["observedDailyCoverageVerified"])
            self.assertFalse(row["zeroDayCertificationVerified"])
        self.assertFalse(result["agentCitationSupported"])
        self.assertFalse(result["registeredRenderer"])
        self.assertEqual(period.validate_candidate(original, result), result)

    def test_leap_day_clamps_year_ago_without_adding_unauthorized_cutoff(self):
        result = period.prepare_candidate(plan(start="2024-02-29",
            end="2024-02-29"))
        by_window = {item["window"]: item for item in result["dailySources"]}
        self.assertEqual(by_window["previous"]["resolvedPeriod"]["startDate"],
            "2024-02-28")
        self.assertEqual(by_window["yearAgo"]["resolvedPeriod"]["startDate"],
            "2023-02-28")
        self.assertEqual(by_window["yearAgo"]["expectedDayCount"], 1)
        for changed in ({"cutoff_date": "2024-02-28"},
                        {"comparison_rule": "sales_custom_calendar_month_v1"}):
            with self.subTest(changed=changed), self.assertRaises(
                    AnalysisContractError):
                period.prepare_candidate(plan(), **changed)

    def test_missing_duplicate_or_wrong_window_refuses_even_rehashed(self):
        with self.assertRaises(AnalysisContractError):
            period.prepare_candidate(plan(windows=("current", "previous")))
        original = plan()
        for field, value in (("window", "previous"),
                             ("startDate", "2026-08-17"),
                             ("shop", "另一店")):
            changed = deepcopy(original)
            item = next(row for row in changed["sourcePlans"]
                if row["sourceKey"] == "promotion-yearAgo")
            item["query"][field] = value
            item["queryDigest"] = digest(item["query"])
            changed["planDigest"] = digest({key: value for key, value
                in changed.items() if key != "planDigest"})
            with self.subTest(field=field), self.assertRaises(
                    AnalysisContractError):
                period.prepare_candidate(changed)

    def test_separate_natural_month_finance_has_no_daily_claim(self):
        result = period.prepare_candidate(plan())
        finance = result["financeContext"]
        self.assertEqual(finance["months"], ["2026-08", "2026-09"])
        self.assertEqual(finance["analysisPeriod"], {"startDate": "2026-08-16",
            "endDate": "2026-09-14"})
        self.assertEqual(finance["temporalRole"], "monthly_context")
        self.assertFalse(finance["dailyProrationAllowed"])
        self.assertFalse(finance["shopIdentityMappingVerified"])
        self.assertNotIn("resolvedPeriod", finance)
        self.assertNotIn("expectedDayDigest", finance)
        self.assertFalse(result["observedDailyCoverageVerified"])
        self.assertFalse(result["zeroDayCertificationVerified"])

    def test_extra_shop_or_nonpromotion_daily_source_is_outside_first_slice(self):
        entries, request = sources(WINDOWS)
        values = measurements(WINDOWS)
        for window in WINDOWS:
            entries.append({"key": "other-" + window, "domain": "netshop",
                "query": {"platform": "京东", "shop": "另一店",
                    "dataset": "promotion", "startDate": "2026-08-20",
                    "endDate": "2026-09-18", "window": window}})
            values.append({"sourceKey": "other-" + window,
                "measuredRowCount": 1, "maxRowUtf8Bytes": 100,
                "pageEnvelopeUtf8Bytes": 2048,
                "sourceRevisionHint": "1:other"})
        given = evidence_v4.build_plan(client_request_id="two-shops",
            sources=entries, measurements=values, analysis_request=request)
        with self.assertRaises(AnalysisContractError):
            period.prepare_candidate(given)
        entries, request = sources(WINDOWS)
        values = measurements(WINDOWS)
        for entry in entries:
            if entry["domain"] == "netshop":
                entry["query"]["dataset"] = "sku"
        given = evidence_v4.build_plan(client_request_id="mixed-dataset",
            sources=entries, measurements=values, analysis_request=request)
        with self.assertRaises(AnalysisContractError):
            period.prepare_candidate(given)

    def test_candidate_field_date_and_digest_tamper_all_refuse(self):
        original = plan()
        result = period.prepare_candidate(original)
        for change in ("period", "daily_digest", "finance_month", "status"):
            tampered = deepcopy(result)
            if change == "period":
                tampered["dailySources"][0]["resolvedPeriod"]["startDate"] = "2026-08-15"
            elif change == "daily_digest":
                tampered["dailySources"][0]["expectedDayDigest"] = "0" * 64
            elif change == "finance_month":
                tampered["financeContext"]["months"] = ["2026-09"]
            else:
                tampered["zeroDayCertificationVerified"] = True
            tampered["periodPlanDigest"] = digest({key: value for key, value
                in tampered.items() if key != "periodPlanDigest"})
            with self.subTest(change=change), self.assertRaises(
                    AnalysisContractError):
                period.validate_candidate(original, tampered)
        with self.assertRaises(AnalysisContractError):
            period.validate_candidate(original, {"unexpected": float("nan")})

    def test_wrong_source_key_and_temporal_role_do_not_become_new_identity(self):
        original = plan()
        for change in ("unknown_key", "finance_as_daily"):
            altered = deepcopy(original)
            item = next(row for row in altered["sourcePlans"]
                if row["sourceKey"] == ("promotion-previous" if change == "unknown_key"
                    else "finance-months"))
            if change == "unknown_key":
                item["sourceKey"] = "unknown-previous"
            else:
                item["temporalRole"] = "daily_fact"
            altered["planDigest"] = digest({key: value for key, value
                in altered.items() if key != "planDigest"})
            with self.subTest(change=change), self.assertRaises(
                    AnalysisContractError):
                period.prepare_candidate(altered)
