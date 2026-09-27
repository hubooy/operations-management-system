"""Pure, non-authorizing shape of a persisted v6 market source page ticket.

The ticket is a bounded pointer into an independently attested market sample.
It is neither an Agent read receipt nor evidence for own-shop/B2B sales.
"""
from __future__ import annotations

import hashlib
import json
import re

from business_analysis.contracts import AnalysisContractError, canonical, digest


SCHEMA = "business-market-v6-source-page-ticket-v1"
VIEWS = ("rank_entry_exit",)
MAX_PAGE_BYTES = 38_000
MAX_TICKETS_PER_REPORT = 64
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DAY = re.compile(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}\Z")


def _need(ok):
    if not ok:
        raise AnalysisContractError("市场 v6 同报告来源页票据未获验证")


def _sha(value):
    _need(type(value) is str and _SHA.fullmatch(value) is not None)


def _identity(value):
    _need(type(value) is str and _ID.fullmatch(value) is not None)


def validate(raw):
    """Validate a detached SQL projection, never grant read/dispatch authority."""
    _need(type(raw) is dict and set(raw) == {
        "schemaVersion", "ticketId", "reportId", "workflowId", "jobId",
        "role",
        "ownerEmail", "ownerVersion", "sourceExecutionReportId",
        "admittedReportId", "parkedReportId", "sealedSourceReportId",
        "evidenceRunId", "evidenceVersion", "sealedDigest",
        "topologySnapshotDigest", "sourcePlanDigest", "selectorDigest",
        "admissionDigest", "marketContextDigest",
        "marketManifestDigest", "view", "pageIndex", "maxPageBytes",
        "rowCount", "pageCount", "tableBindingDigest", "ndjsonSha256",
        "currentSourceKey", "baselineSourceKey",
        "currentObservationDate", "baselineObservationDate",
        "marketStatus", "shopStatus", "shopSalesStatus", "b2bStatus",
        "yearAgoStatus", "providerCallsAllowed", "toolDispatchAllowed",
        "agentReadPersisted", "numericCitationAllowed", "reportPublishAuthorized",
        "pageBytesVerified", "ticketDigest"})
    _need(raw["schemaVersion"] == SCHEMA)
    for name in ("ticketId", "reportId", "workflowId", "jobId",
            "sourceExecutionReportId", "admittedReportId", "parkedReportId",
            "sealedSourceReportId", "evidenceRunId", "currentSourceKey",
            "baselineSourceKey"):
        _identity(raw[name])
    _need(raw["currentSourceKey"] != raw["baselineSourceKey"])
    _need(len({raw[name] for name in ("reportId", "sourceExecutionReportId",
        "admittedReportId", "parkedReportId", "sealedSourceReportId")}) == 5)
    _need(type(raw["ownerEmail"]) is str and "@" in raw["ownerEmail"]
        and raw["ownerEmail"] == raw["ownerEmail"].lower()
        and len(raw["ownerEmail"]) <= 320)
    for name in ("sealedDigest", "topologySnapshotDigest",
            "sourcePlanDigest", "selectorDigest", "admissionDigest",
            "marketContextDigest", "marketManifestDigest",
            "tableBindingDigest", "ndjsonSha256", "ticketDigest"):
        _sha(raw[name])
    _need(type(raw["ownerVersion"]) is int and 1 <= raw["ownerVersion"] <= 2**53-1
        and type(raw["evidenceVersion"]) is int and raw["evidenceVersion"] >= 1
        and raw["role"] == "market_b2b" and raw["view"] in VIEWS
        and type(raw["pageIndex"]) is int and 0 <= raw["pageIndex"] < 20_000
        and raw["maxPageBytes"] == MAX_PAGE_BYTES
        and type(raw["rowCount"]) is int and 0 <= raw["rowCount"] <= 200_000
        and type(raw["pageCount"]) is int and 1 <= raw["pageCount"] <= 20_000
        and raw["pageIndex"] < raw["pageCount"])
    for name in ("currentObservationDate", "baselineObservationDate"):
        _need(type(raw[name]) is str and _DAY.fullmatch(raw[name]) is not None)
    _need(raw["currentObservationDate"] != raw["baselineObservationDate"]
        and raw["marketStatus"] == "bound_selected_top_sample"
        and all(raw[name] == "unknown_not_supplied" for name in (
            "shopStatus", "shopSalesStatus", "b2bStatus", "yearAgoStatus"))
        and all(raw[name] is False for name in (
            "providerCallsAllowed", "toolDispatchAllowed", "agentReadPersisted",
            "numericCitationAllowed", "reportPublishAuthorized",
            "pageBytesVerified")))
    without_digest = {key: value for key, value in raw.items()
        if key != "ticketDigest"}
    _need(digest(without_digest) == raw["ticketDigest"])
    _need(len(canonical(raw).encode("utf-8")) <= 8_192)
    return raw


def bind_replayed_page(ticket, manifest, pages):
    """Reconcile every typed NDJSON page to its attested table root."""
    fixed = validate(ticket)
    _need(type(manifest) is dict
        and manifest.get("manifestDigest") == fixed["marketManifestDigest"]
        and digest({key: value for key, value in manifest.items()
            if key != "manifestDigest"}) == fixed["marketManifestDigest"]
        and manifest.get("reportBinding", {}).get("reportId") ==
            fixed["sealedSourceReportId"]
        and manifest.get("observationDates") == {
            "current": fixed["currentObservationDate"],
            "baseline": fixed["baselineObservationDate"]}
        and manifest.get("rankCurrentSourceKey") ==
            fixed["currentSourceKey"]
        and manifest.get("rankBaselineKey") == fixed["baselineSourceKey"])
    specs = manifest.get("tables")
    _need(type(specs) is list and len(specs) == 3)
    spec = next((item for item in specs if item.get("view") == fixed["view"]),
        None)
    _need(type(spec) is dict and spec.get("rowCount") == fixed["rowCount"]
        and spec.get("pageCount") == fixed["pageCount"]
        and spec.get("sourceTableDigest") == fixed["tableBindingDigest"]
        and spec.get("ndjsonSha256") == fixed["ndjsonSha256"])
    all_sha = hashlib.sha256()
    total_rows = total_bytes = page_count = 0
    selected_rows = selected_raw = None
    for raw in pages:
        _need(type(raw) is bytes and len(raw) <= MAX_PAGE_BYTES
            and page_count < fixed["pageCount"])
        all_sha.update(raw)
        total_bytes += len(raw)
        _need(total_bytes <= 64 * 1024 * 1024)
        rows = []
        for line in raw.splitlines(keepends=True):
            _need(line.endswith(b"\n"))
            try:
                row = json.loads(line.decode("utf-8"))
            except (UnicodeError, ValueError):
                _need(False)
            _need(type(row) is dict and line ==
                (canonical(row)+"\n").encode("utf-8")
                and row.get("rowIndex") == total_rows)
            rows.append(row)
            total_rows += 1
            _need(total_rows <= 200_000)
        if page_count == fixed["pageIndex"]:
            selected_rows, selected_raw = rows, raw
        page_count += 1
    _need(page_count == fixed["pageCount"]
        and total_rows == fixed["rowCount"]
        and total_bytes == spec.get("ndjsonBytes")
        and all_sha.hexdigest() == fixed["ndjsonSha256"]
        and selected_raw is not None)
    return {"schemaVersion": "business-market-v6-replayed-source-page-v1",
        "ticketId": fixed["ticketId"], "ticketDigest": fixed["ticketDigest"],
        "pageDigest": hashlib.sha256(selected_raw).hexdigest(),
        "rows": selected_rows, "pageBytesVerified": False,
        "agentReadPersisted": False, "numericCitationAllowed": False}
