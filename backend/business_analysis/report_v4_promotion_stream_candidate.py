"""Private double-pass v4 promotion page stream, without report authority.

An owning adapter must supply actual sealed chunk/audit rows, a SQL-owned
creation-time report link, current rechecks, and an unpublished row sink. This
pure candidate checks the page stream but cannot authenticate those inputs.
It must not be registered as a report reader or used with the v2 manifest.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from . import evidence_v4, promotion_views
from .contracts import (AnalysisContractError, PageReconciler, canonical,
    comparison_periods, coverage, digest)


SCHEMA = "business-report-v4-promotion-stream-candidate-v1"
SOURCE_SCHEMA = "business-report-v4-promotion-stream-source-v1"
MAX_SECONDS = 1200
MAX_PAGE_BYTES = 131_072
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _need(ok, message="v4推广已封存页流、同报告来源或三期日期不一致"):
    if not ok:
        raise AnalysisContractError(message)


def _sha(value):
    _need(type(value) is str and _SHA.fullmatch(value) is not None)


def _bound(bridge, window, source):
    _need(type(bridge) is dict
        and bridge.get("schemaVersion") in {
            "business-report-v4-source-bridge-candidate-v1",
            "business-report-v4-unbound-owning-candidate-v1"}
        and bridge.get("candidateOnly") is True
        and all(bridge.get(key) is False for key in (
            "persistedSameReportLinkVerified", "v4RowsReadableForReport",
            "agentCitationSupported", "registeredRenderer", "publishable"))
        and bridge.get("candidateDigest") == digest({key: value for key,
            value in bridge.items() if key != "candidateDigest"})
        and window in {"current", "previous", "yearAgo"})
    rows = bridge.get("promotionWindows")
    _need(type(rows) is list and len(rows) == 3)
    selected = next((row for row in rows if type(row) is dict and
        row.get("window") == window), None)
    _need(selected is not None and type(source) is dict and set(source) == {
        "schemaVersion", "bridgeCandidateDigest", "sourceKey",
        "queryDigest", "sourceIdentityDigest", "sourceRef",
        "sourceRevision", "pageCount", "rowCount", "storedBytes",
        "evidenceDigest", "receiptChainDigest", "manifestDigest"})
    _need(source["schemaVersion"] == SOURCE_SCHEMA
        and source["bridgeCandidateDigest"] == bridge["candidateDigest"]
        and source["sourceKey"] == selected["sourceKey"]
        and all(source[key] == selected[key] for key in (
            "queryDigest", "sourceIdentityDigest", "sourceRef",
            "sourceRevision"))
        and type(source["pageCount"]) is int
        and 1 <= source["pageCount"] <= evidence_v4.MAX_SOURCE_PAGES
        and type(source["rowCount"]) is int
        and 1 <= source["rowCount"] <= evidence_v4.MAX_SOURCE_PAGES * 100
        and type(source["storedBytes"]) is int
        and 1 <= source["storedBytes"] <= evidence_v4.MAX_SOURCE_BYTES
        and source["manifestDigest"] == digest({key: value for key,
            value in source.items() if key != "manifestDigest"}))
    for key in ("evidenceDigest", "receiptChainDigest"):
        _sha(source[key])
    query = {"platform": "京东", "shop": bridge["shop"],
        "dataset": "promotion", **bridge["originalPeriod"],
        "window": window}
    _need(source["queryDigest"] == digest(query))
    periods = comparison_periods(query["startDate"], query["endDate"])
    _need(selected["resolvedPeriod"] == periods[window])
    return selected, query, periods


def _row(row, query, period):
    _need(type(row) is dict and set(row) == promotion_views.ROW_FIELDS
        and row["platform"] == "京东" and row["shopName"] == query["shop"]
        and type(row["date"]) is str
        and period["startDate"] <= row["date"] <= period["endDate"]
        and type(row["dimensions"]) is dict
        and set(row["dimensions"]) == promotion_views.DIMENSIONS
        and type(row["metrics"]) is dict
        and set(row["metrics"]) == promotion_views.METRICS)
    raw = canonical(row)
    _need(len(raw.encode("utf-16-le")) // 2 <= 32767,
        "推广完整规范行超过Excel文本单元格容量")
    return raw


def stage_candidate(bridge, window, source, pages_factory, sink,
                    verify_current, *, enabled=False, checkpoint=None):
    """Replay actual pages twice, stage private rows, never publish.

    ``pages_factory`` must open a fresh immutable owning iterator each time.
    ``sink`` exposes stage(sourceKey,rowIndex,rowJson), complete(receipt),
    abort(). Completion only means a private candidate is whole; an owning
    adapter must still create a versioned HTML/XLSX writer and prove SQL link.
    """
    _need(enabled is True, "v4同报告私有页流默认关闭")
    _need(callable(pages_factory) and callable(verify_current)
        and all(callable(getattr(sink, name, None)) for name in (
            "stage", "complete", "abort")))
    try:
        selected, query, periods = _bound(bridge, window, source)
    except Exception:
        sink.abort()
        raise
    deadline = time.monotonic() + MAX_SECONDS

    def one_pass(emit):
        _need(time.monotonic() < deadline
            and verify_current(bridge, source) is True)
        verifier = PageReconciler()
        row_root = hashlib.sha256()
        seen_dates, pages, rows, size = set(), 0, 0, 0
        cursor, last_id, receipt_chain = None, None, digest([])
        for envelope in pages_factory():
            pages += 1
            _need(time.monotonic() < deadline
                and pages <= source["pageCount"]
                and type(envelope) is dict and set(envelope) == {
                    "sequence", "payloadJson", "payloadDigest",
                    "sourceRef", "sourceRevision", "rowCount",
                    "receiptResponseDigest", "auditResponseDigest",
                    "requestArgumentsDigest", "toolName", "auditSucceeded",
                    "auditId", "invocationId"})
            raw = envelope["payloadJson"]
            _need(type(raw) is str and envelope["sequence"] == pages
                and envelope["sourceRef"] == source["sourceRef"]
                and envelope["sourceRevision"] == source["sourceRevision"])
            length = len(raw.encode("utf-8"))
            size += length
            _need(0 < length <= MAX_PAGE_BYTES
                and size <= source["storedBytes"]
                and envelope["payloadDigest"] == digest(raw)
                == envelope["receiptResponseDigest"]
                == envelope["auditResponseDigest"]
                and envelope["auditSucceeded"] is True
                and type(envelope["auditId"]) is str
                and bool(envelope["auditId"])
                and type(envelope["invocationId"]) is str
                and bool(envelope["invocationId"]))
            arguments = ({"domain": "netshop", **query, "limit": 100}
                if pages == 1 else {**query, "limit": 100,
                    "cursor": cursor,
                    "expectedSourceRef": source["sourceRef"],
                    "expectedRevision": source["sourceRevision"],
                    "expectedLastId": last_id})
            _need(envelope["toolName"] == (
                "get_business_source_page" if pages == 1
                else "get_business_netshop_continuation_page")
                and envelope["requestArgumentsDigest"] == digest(arguments))
            page = json.loads(raw)
            _need(raw == canonical(page)
                and page.get("sourceRef") == source["sourceRef"]
                and page.get("sourceRevision") == source["sourceRevision"]
                and page.get("source") == "jd_promotion"
                and page.get("sourceDataset") == "ad"
                and page.get("filters") == {
                    "platform": "京东", "shop": query["shop"],
                    "dataset": "promotion", "window": window,
                    "periods": periods}
                and type(page.get("items")) is list
                and len(page["items"]) <= 100
                and (pages == 1) == (type(page.get("control")) is dict)
                and type(envelope["rowCount"]) is int
                and envelope["rowCount"] == len(page["items"]))
            verifier.consume(page, request_cursor=cursor)
            for item in page["items"]:
                encoded = _row(item, query, periods[window])
                seen_dates.add(item["date"])
                row_root.update((canonical([source["sourceKey"], rows,
                    encoded]) + "\n").encode("utf-8"))
                if emit:
                    sink.stage(source["sourceKey"], rows, encoded)
                rows += 1
            pagination = page["pagination"]
            cursor = pagination["nextCursor"]
            last_id = int(page["items"][-1]["rowId"]) if cursor else None
            receipt_chain = digest([receipt_chain, pages,
                envelope["payloadDigest"], envelope["auditId"],
                envelope["invocationId"], source["sourceRef"],
                source["sourceRevision"]])
            if checkpoint:
                checkpoint({"stage": "v4_report_promotion_stream",
                    "window": window, "phase": "page", "sequence": pages})
        proof = verifier.result()
        observed = coverage(periods[window], seen_dates)
        _need(pages == source["pageCount"]
            and rows == source["rowCount"]
            and size == source["storedBytes"]
            and proof["evidenceDigest"] == source["evidenceDigest"]
            and receipt_chain == source["receiptChainDigest"]
            and proof["sourceRef"] == source["sourceRef"]
            and observed["missingDates"] == []
            and time.monotonic() < deadline
            and verify_current(bridge, source) is True,
            "v4推广页链不完整、期内日期未知或拥有方修订变化")
        return {"pageCount": pages, "rowCount": rows,
            "storedBytes": size, "rowDigest": row_root.hexdigest(),
            "evidenceDigest": proof["evidenceDigest"],
            "receiptChainDigest": receipt_chain,
            "coverageDigest": digest(observed)}

    try:
        first = one_pass(False)
        second = one_pass(True)
        _need(first == second, "v4推广双遍页流、行摘要或修订发生变化")
        body = {"schemaVersion": SCHEMA,
            "bridgeCandidateDigest": bridge["candidateDigest"],
            "sourceManifestDigest": source["manifestDigest"],
            "window": window, **first,
            "twoCompletePassesEqual": True,
            "stagedUnpublished": True,
            "persistedSameReportLinkVerified": False,
            "sourceAuthorityVerified": False,
            "registeredRenderer": False, "publishable": False}
        result = {**body, "resultDigest": digest(body)}
        sink.complete(result)
        return result
    except Exception:
        sink.abort()
        raise
