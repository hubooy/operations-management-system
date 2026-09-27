"""Pure private v4 page-flow checks; no DB link or customer data."""
from copy import deepcopy
from datetime import date, timedelta
import json
from unittest import TestCase

from . import promotion_views, report_v4_promotion_stream_candidate as stream
from .contracts import (AnalysisContractError, PageReconciler, canonical,
    comparison_periods, digest)
from .test_report_v4_source_bridge_candidate import fixture as bridge_fixture
from . import report_v4_source_bridge_candidate as bridge_contract


class Sink:
    def __init__(self):
        self.rows = []
        self.final = None
        self.aborted = False

    def stage(self, source_key, index, raw):
        self.rows.append((source_key, index, raw))

    def complete(self, receipt):
        self.final = receipt

    def abort(self):
        self.rows.clear()
        self.final = None
        self.aborted = True


def fixture(*, omit_day=None):
    report, plan, seal = bridge_fixture()
    bound = bridge_contract.prepare_candidate(report, plan, seal,
        enabled=True)
    window = next(row for row in bound["promotionWindows"] if row[
        "window"] == "current")
    first = date(2026, 8, 16)
    days = [(first + timedelta(days=offset)).isoformat()
        for offset in range(30) if offset != omit_day]
    items = [{"rowId": str(index + 1),
        "sourceRowHash": f"{index + 1:064x}", "batchId": "synthetic",
        "platform": "京东", "shopName": bound["shop"], "date": day,
        "snapshotDate": day, "skuId": "sku-1", "spuId": "spu-1",
        "productCode": "code-1", "productName": "synthetic",
        "category": "synthetic",
        "dimensions": {key: None for key in promotion_views.DIMENSIONS},
        "metrics": {key: 0 for key in promotion_views.METRICS}}
        for index, day in enumerate(days)]
    query = {"platform": "京东", "shop": bound["shop"],
        "dataset": "promotion", **bound["originalPeriod"],
        "window": "current"}
    page = {"schemaVersion": "business-analysis-v1",
        "source": "jd_promotion", "sourceDataset": "ad",
        "sourceRef": window["sourceRef"],
        "sourceRevision": window["sourceRevision"],
        "monetaryUnit": "CNY_CENT",
        "filters": {"platform": "京东", "shop": bound["shop"],
            "dataset": "promotion", "window": "current",
            "periods": comparison_periods(query["startDate"],
                query["endDate"])},
        "control": {"rowCount": len(items),
            "typedTotals": {key: 0 for key in promotion_views.BASE_METRICS}},
        "items": items,
        "pageEvidence": {"rowCount": len(items), "sha256": digest(items)},
        "pagination": {"hasMore": False, "nextCursor": None,
            "limit": 100}}
    raw = canonical(page)
    audit = {"sequence": 1, "payloadJson": raw,
        "payloadDigest": digest(raw), "sourceRef": window["sourceRef"],
        "sourceRevision": window["sourceRevision"],
        "rowCount": len(items), "receiptResponseDigest": digest(raw),
        "auditResponseDigest": digest(raw),
        "requestArgumentsDigest": digest({"domain": "netshop",
            **query, "limit": 100}),
        "toolName": "get_business_source_page",
        "auditSucceeded": True, "auditId": "audit-1",
        "invocationId": "invocation-1"}
    verifier = PageReconciler()
    verifier.consume(page)
    chain = digest([digest([]), 1, digest(raw), "audit-1",
        "invocation-1", window["sourceRef"], window["sourceRevision"]])
    source = {"schemaVersion": stream.SOURCE_SCHEMA,
        "bridgeCandidateDigest": bound["candidateDigest"],
        "sourceKey": window["sourceKey"],
        "queryDigest": window["queryDigest"],
        "sourceIdentityDigest": window["sourceIdentityDigest"],
        "sourceRef": window["sourceRef"],
        "sourceRevision": window["sourceRevision"],
        "pageCount": 1, "rowCount": len(items),
        "storedBytes": len(raw.encode("utf-8")),
        "evidenceDigest": verifier.result()["evidenceDigest"],
        "receiptChainDigest": chain}
    source["manifestDigest"] = digest(source)
    return bound, source, audit


class V4PromotionStreamCandidateTests(TestCase):
    def test_double_pass_private_rows_never_gain_report_authority(self):
        bridge, source, page = fixture()
        sink = Sink()
        reads = []
        def fresh_pages():
            reads.append(True)
            return iter([deepcopy(page)])
        with self.assertRaises(AnalysisContractError):
            stream.stage_candidate(bridge, "current", source, fresh_pages,
                sink, lambda *_: True)
        self.assertEqual(reads, [])
        value = stream.stage_candidate(bridge, "current", source,
            fresh_pages, sink, lambda *_: True, enabled=True)
        self.assertEqual(len(reads), 2)
        self.assertEqual(len(sink.rows), 30)
        self.assertEqual([row[1] for row in sink.rows], list(range(30)))
        self.assertEqual(value["rowCount"], 30)
        self.assertEqual(value["pageCount"], 1)
        self.assertTrue(value["twoCompletePassesEqual"])
        self.assertTrue(value["stagedUnpublished"])
        for key in ("persistedSameReportLinkVerified",
                    "sourceAuthorityVerified", "registeredRenderer",
                    "publishable"):
            self.assertIs(value[key], False)
        self.assertEqual(value, sink.final)

    def test_unknown_day_and_mutated_second_pass_abort_private_rows(self):
        bridge, source, page = fixture(omit_day=10)
        sink = Sink()
        with self.assertRaises(AnalysisContractError):
            stream.stage_candidate(bridge, "current", source,
                lambda: iter([deepcopy(page)]), sink, lambda *_: True,
                enabled=True)
        self.assertTrue(sink.aborted)
        self.assertEqual(sink.rows, [])
        bridge, source, page = fixture()
        count = 0
        def drift():
            nonlocal count
            count += 1
            changed = deepcopy(page)
            if count == 2:
                changed["payloadJson"] = changed["payloadJson"].replace(
                    "synthetic", "changed", 1)
            return iter([changed])
        sink = Sink()
        with self.assertRaises(AnalysisContractError):
            stream.stage_candidate(bridge, "current", source, drift,
                sink, lambda *_: True, enabled=True)
        self.assertEqual(count, 2)
        self.assertTrue(sink.aborted)
        self.assertEqual(sink.rows, [])

    def test_wrong_window_request_audit_or_stale_source_refuses(self):
        bridge, source, page = fixture()
        for changed in (
                {"requestArgumentsDigest": "f" * 64},
                {"sourceRevision": "8:" + "a" * 64},
                {"receiptResponseDigest": "f" * 64}):
            bad = {**page, **changed}
            sink = Sink()
            with self.subTest(changed=changed), self.assertRaises(
                    AnalysisContractError):
                stream.stage_candidate(bridge, "current", source,
                    lambda: iter([bad]), sink, lambda *_: True,
                    enabled=True)
            self.assertTrue(sink.aborted)
        sink = Sink()
        with self.assertRaises(AnalysisContractError):
            stream.stage_candidate(bridge, "previous", source,
                lambda: iter([page]), sink, lambda *_: True,
                enabled=True)
        self.assertTrue(sink.aborted)
