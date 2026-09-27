"""Exact JD promotion windows from the prospective v4 capacity plan."""
from copy import deepcopy
from unittest import TestCase

from . import business_promotion_v4_plan as service, evidence_v4
from .contracts import AnalysisContractError, digest
from .test_evidence_v4 import sources, measurements


def plan(windows=("current", "previous", "yearAgo")):
    entries, request = sources(windows)
    return evidence_v4.build_plan(client_request_id="promotion-v4-exact-plan",
        sources=entries, measurements=measurements(windows),
        analysis_request=request)


def selected(previous="promotion-previous", year_ago="promotion-yearAgo"):
    return {"currentSourceKey": "promotion-current",
        "previousSourceKey": previous, "yearAgoSourceKey": year_ago}


class BusinessPromotionV4PlanTests(TestCase):
    def test_three_exact_windows_use_existing_equal_length_and_clamped_year_rule(self):
        given = plan()
        frozen = deepcopy(given)
        result = service.prepare_candidate(given, selected())
        self.assertEqual(given, frozen)
        self.assertEqual(result["schemaVersion"], service.SCHEMA)
        self.assertEqual(result["shop"], "测试店")
        self.assertEqual(result["requestedWindows"], list(service.WINDOWS))
        self.assertEqual(result["periods"]["previous"]["startDate"], "2026-07-21")
        self.assertEqual(result["periods"]["previous"]["endDate"], "2026-08-19")
        self.assertEqual(result["periods"]["yearAgo"]["startDate"], "2025-08-20")
        self.assertEqual(result["selectedWindows"]["previous"]["sourceKey"],
            "promotion-previous")
        self.assertTrue(result["runCapacitySupported"])
        self.assertEqual(result["selectedWindows"]["previous"]["capacityStatus"],
            "supported")
        self.assertTrue(all(not row["numericComparisonAvailable"]
            for row in result["selectedWindows"].values()))
        self.assertFalse(result["sourceAuthorityVerified"])
        self.assertFalse(result["agentDispatchSupported"])
        self.assertEqual(result["selectionDigest"], digest({key: value for key, value
            in result.items() if key != "selectionDigest"}))

    def test_optional_baselines_are_explicit_missing_sources_not_zeros(self):
        current_only = service.prepare_candidate(plan(("current",)),
            selected(previous=None, year_ago=None))
        self.assertEqual(current_only["selectedWindows"]["previous"]["status"],
            "missing_source_not_requested")
        self.assertIsNone(current_only["selectedWindows"]["yearAgo"]["sourceKey"])
        self.assertEqual(current_only["selectedWindows"]["yearAgo"]["capacityStatus"],
            "not_requested")
        self.assertFalse(current_only["selectedWindows"]["previous"]["numericComparisonAvailable"])
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(plan(("current",)), selected(
                previous="promotion-previous", year_ago=None))

    def test_requested_missing_extra_duplicate_and_swapped_window_reject(self):
        given = plan()
        missing = service.prepare_candidate(given, selected(previous=None))
        self.assertEqual(missing["selectedWindows"]["previous"]["status"],
            "missing_requested_source")
        self.assertIsNone(missing["selectedWindows"]["previous"]["sourceKey"])
        self.assertFalse(missing["selectedWindows"]["previous"]["numericComparisonAvailable"])
        for choice in ({**selected(), "currentSourceKey": None},
                       selected(previous="promotion-current"),
                       selected(previous="promotion-yearAgo", year_ago="promotion-previous"),
                       {**selected(), "surprise": True}):
            with self.subTest(choice=choice), self.assertRaises(AnalysisContractError):
                service.prepare_candidate(given, choice)
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(plan(("current", "previous")), selected())

    def test_other_shop_same_window_cannot_be_baseline(self):
        entries, request = sources(("current", "previous", "yearAgo"))
        data = measurements(("current", "previous", "yearAgo"))
        for window in service.WINDOWS:
            entries.append({"key": "other-"+window, "domain": "netshop",
                "query": {"platform": "京东", "shop": "另一店", "dataset": "promotion",
                    "startDate": "2026-08-20", "endDate": "2026-09-18", "window": window}})
            data.append({"sourceKey": "other-"+window, "measuredRowCount": 1,
                "maxRowUtf8Bytes": 100, "pageEnvelopeUtf8Bytes": 2048,
                "sourceRevisionHint": "1:other"})
        mixed = evidence_v4.build_plan(client_request_id="two-shops",
            sources=entries, measurements=data, analysis_request=request)
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(mixed, selected(previous="other-previous"))

    def test_rehashed_plan_query_and_capacity_field_still_need_normative_rebuild(self):
        given = plan()
        changed = deepcopy(given)
        chosen = next(item for item in changed["sourcePlans"]
            if item["sourceKey"] == "promotion-previous")
        chosen["query"]["shop"] = "另一店"
        chosen["queryDigest"] = digest(chosen["query"])
        changed["planDigest"] = digest({key: value for key, value in changed.items()
            if key != "planDigest"})
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(changed, selected())

    def test_leap_day_year_ago_clamps_and_unsupported_capacity_stays_unapproved(self):
        entries, request = sources(("current", "yearAgo"))
        data = measurements(("current", "yearAgo"), width=4000)
        for item in entries:
            if item["domain"] == "finance":
                item["query"]["months"] = ["2024-02"]
                item["query"]["analysisPeriod"] = {"startDate": "2024-02-29",
                    "endDate": "2024-02-29"}
            else:
                item["query"]["startDate"] = "2024-02-29"
                item["query"]["endDate"] = "2024-02-29"
        given = evidence_v4.build_plan(client_request_id="leap-year-plan",
            sources=entries, measurements=data, analysis_request=request)
        result = service.prepare_candidate(given,
            selected(previous=None, year_ago="promotion-yearAgo"))
        self.assertEqual(result["periods"]["yearAgo"]["startDate"], "2023-02-28")
        self.assertEqual(result["selectedWindows"]["previous"]["status"],
            "missing_source_not_requested")
        self.assertEqual(result["selectedWindows"]["yearAgo"]["capacityStatus"],
            "supported" if given["runCapacitySupported"] else "unsupported")
        changed = deepcopy(given)
        next(item for item in changed["sourcePlans"] if item["sourceKey"] ==
            "promotion-current")["estimatedPageCount"] = 1
        changed["planDigest"] = digest({key: value for key, value in changed.items()
            if key != "planDigest"})
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(changed, selected())
