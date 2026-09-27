"""Pure bounded owning-page byte measurement; no DB or platform calls."""
from unittest import TestCase

from . import evidence_v4, promotion_views
from . import v4_promotion_page_capacity as measured
from .contracts import (AnalysisContractError, SCHEMA_VERSION, canonical,
    comparison_periods, coverage, digest)


QUERY = {"platform": "京东", "shop": "合成店", "dataset": "promotion",
    "startDate": "2026-08-16", "endDate": "2026-08-17",
    "window": "current"}
SOURCE = {"key": "promotion-current", "domain": "netshop", "query": QUERY}


def row(index):
    day = "2026-08-16" if index == 1 else "2026-08-17"
    return {"rowId": str(index), "sourceRowHash": f"{index:064x}",
        "batchId": "batch-one", "platform": "京东", "shopName": "合成店",
        "date": day, "snapshotDate": None,
        "skuId": "SKU-1", "spuId": "SPU-1", "productCode": "M-1",
        "productName": "合成商品", "category": "商用设备",
        "dimensions": {key: "词" for key in promotion_views.DIMENSIONS},
        "metrics": {key: 1 for key in promotion_views.METRICS}}


def pages(*, split=False):
    periods = comparison_periods(QUERY["startDate"], QUERY["endDate"])
    first_rows = [row(1)] if split else [row(1), row(2)]
    later_rows = [row(2)] if split else []
    result = []
    for index, items in enumerate([first_rows, later_rows] if split else [first_rows]):
        first = index == 0
        page = {"schemaVersion": SCHEMA_VERSION,
            "sourceRef": "a" * 64, "sourceRevision": "7:" + "b" * 64,
            "filters": {"platform": "京东", "shop": "合成店",
                "dataset": "promotion", "window": "current", "periods": periods},
            "source": "jd_promotion", "sourceDataset": "ad",
            "monetaryUnit": "CNY_CENT",
            "consistency": "revision_fenced_pages_not_cross_domain_snapshot",
            "metricSemantics": {"attributionWindow": "unknown"},
            "control": ({"rowCount": 2,
                "typedTotals": {key: 2 for key in promotion_views.BASE_METRICS},
                "note": "synthetic"} if first else None),
            "coverage": coverage(periods["current"],
                ["2026-08-16", "2026-08-17"]) if first else None,
            "availableDates": {"firstDate": "2026-08-16",
                "lastDate": "2026-08-17"} if first else None,
            "items": items,
            "pageEvidence": {"sha256": digest(items), "rowCount": len(items)},
            "pagination": {"hasMore": split and first,
                "nextCursor": "cursor-1" if split and first else None,
                "limit": 100}}
        result.append(page)
    return result


def raw_pages(*, split=False):
    return [canonical(page).encode("utf-8") for page in pages(split=split)]


def calls(*, split=False):
    return [{"requestCursor": None if index == 0 else "cursor-1",
             "rawPage": raw}
        for index, raw in enumerate(raw_pages(split=split))]


class V4PromotionPageCapacityTests(TestCase):
    def test_complete_one_page_measures_exact_bytes_without_authority(self):
        raw = raw_pages()
        result = measured.measure_complete_pages(SOURCE, iter(calls()),
            expected_row_count=2)
        self.assertEqual((result["pageCount"], result["rowCount"],
            result["storedBytes"]), (1, 2, len(raw[0])))
        self.assertEqual(result["maxRowUtf8Bytes"], max(
            len(canonical(row(index)).encode()) for index in (1, 2)))
        self.assertEqual(result["maxPageEnvelopeUtf8Bytes"],
            len(raw[0])-len(canonical([row(1), row(2)]).encode())+2)
        self.assertIsNone(result["v4Measurement"])
        self.assertFalse(result["capacityPlanMeasurementAvailable"])
        self.assertTrue(result["suppliedRequestCursorChainConsistent"])
        self.assertFalse(result["signedRequestCursorAuditVerified"])
        self.assertFalse(result["blockingReadDeadlineVerified"])
        self.assertFalse(result["upstreamAllocationBoundVerified"])
        self.assertTrue(result["capacityArithmeticSupported"])
        self.assertFalse(result["owningSourceAuthorityVerified"])
        self.assertFalse(result["signedToolAuditVerified"])
        self.assertFalse(result["sealerOrReportAuthorityGranted"])
        self.assertEqual(result["measurementDigest"], digest({key: value
            for key, value in result.items() if key != "measurementDigest"}))
        finance = {"key": "finance-months", "domain": "finance", "query": {
            "months": ["2026-08"], "scope": {"scope_key": "shop:合成店",
                "scope_type": "shop", "scope_name": "合成店",
                "group_name": "京东组"},
            "analysisPeriod": {"startDate": QUERY["startDate"],
                "endDate": QUERY["endDate"]}}}
        with self.assertRaises(AnalysisContractError):
            evidence_v4.build_plan(client_request_id="capacity-only-test",
                sources=[SOURCE, finance],
                measurements=[result["v4Measurement"], {"sourceKey": "finance-months",
                    "measuredRowCount": 0, "maxRowUtf8Bytes": 0,
                    "pageEnvelopeUtf8Bytes": 1024,
                    "sourceRevisionHint": "unknown-finance"}],
                analysis_request={"schemaVersion": "business-analysis-request-v1",
                    "question": "合成容量测量", "requestedDimensions": ["shop"],
                    "requestedWindows": ["current"]})

    def test_actual_page_count_above_formula_estimate_is_explicit_gap(self):
        result = measured.measure_complete_pages(SOURCE, iter(calls(split=True)))
        self.assertEqual(result["pageCount"], 2)
        self.assertEqual(result["estimatedPageCount"], 1)
        self.assertTrue(result["estimatedUnderstatesObserved"])
        self.assertFalse(result["capacityArithmeticSupported"])
        self.assertIsNone(result["v4Measurement"])

    def test_missing_reordered_cross_shop_noncanonical_or_wrong_count_reject(self):
        supplied = calls(split=True)
        for candidate in (supplied[:1], list(reversed(supplied)),
                [supplied[0], supplied[0]],
                [{"requestCursor": None,
                  "rawPage": supplied[0]["rawPage"] + b" "}]):
            with self.subTest(length=len(candidate)), self.assertRaises(
                    AnalysisContractError):
                measured.measure_complete_pages(SOURCE, iter(candidate))
        broken = calls(split=True)
        broken[1]["requestCursor"] = None
        with self.assertRaises(AnalysisContractError):
            measured.measure_complete_pages(SOURCE, broken)
        changed = pages()
        changed[0]["items"][0]["shopName"] = "另一店"
        changed[0]["pageEvidence"]["sha256"] = digest(changed[0]["items"])
        with self.assertRaises(AnalysisContractError):
            measured.measure_complete_pages(SOURCE,
                [{"requestCursor": None,
                  "rawPage": canonical(changed[0]).encode("utf-8")}])
        with self.assertRaises(AnalysisContractError):
            measured.measure_complete_pages(SOURCE, iter(calls()),
                expected_row_count=575_095)

    def test_large_historical_row_counts_are_arithmetic_only_not_measured(self):
        # Historical row/width estimates cannot be fed to measure_complete_pages
        # as an owning stream; this separate estimate is deliberately provisional.
        entry = {"key": "promotion-current", "ordinal": 1,
            "domain": "netshop", "temporalRole": "daily_fact",
            "query": QUERY, "queryDigest": digest(QUERY)}
        current = evidence_v4._estimate(entry, {"measuredRowCount": 281_759,
            "maxRowUtf8Bytes": 2063, "pageEnvelopeUtf8Bytes": 8192,
            "sourceRevisionHint": "historical-only",
            "sampleRowCount": 0, "sampleMaxUtf8Bytes": 0})
        previous = evidence_v4._estimate({**entry,
            "key": "promotion-previous"}, {"measuredRowCount": 293_336,
            "maxRowUtf8Bytes": 2061, "pageEnvelopeUtf8Bytes": 8192,
            "sourceRevisionHint": "historical-only",
            "sampleRowCount": 0, "sampleMaxUtf8Bytes": 0})
        self.assertEqual(current["estimatedPageCount"], 4776)
        self.assertEqual(previous["estimatedPageCount"], 4972)
        self.assertLess(current["estimatedBytesUpperBound"], evidence_v4.MAX_SOURCE_BYTES)
        self.assertLess(previous["estimatedBytesUpperBound"], evidence_v4.MAX_SOURCE_BYTES)
        self.assertRaises(AnalysisContractError,
            measured.measure_complete_pages, SOURCE, [],
            expected_row_count=575_095)

    def test_invalid_hash_bool_metric_control_and_available_dates_fail(self):
        for change in ("rowId", "hash", "metric", "control", "available"):
            altered = pages()
            if change == "rowId": altered[0]["items"][0]["rowId"] = "01"
            elif change == "hash": altered[0]["items"][0]["sourceRowHash"] = "X" * 64
            elif change == "metric": altered[0]["items"][0]["metrics"]["clicks"] = True
            elif change == "control": altered[0]["control"]["typedTotals"]["clicks"] = True
            else: altered[0]["availableDates"]["firstDate"] = "not-a-date"
            altered[0]["pageEvidence"]["sha256"] = digest(altered[0]["items"])
            with self.subTest(change=change), self.assertRaises(AnalysisContractError):
                measured.measure_complete_pages(SOURCE, [{"requestCursor": None,
                    "rawPage": canonical(altered[0]).encode("utf-8")}])
