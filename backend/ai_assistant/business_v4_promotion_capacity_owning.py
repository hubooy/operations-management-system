"""Default-closed owning bridge from immutable v4 pages to capacity diagnostics.

The first pass uses the existing full replay with signed internal tool-audit
receipts. A second streaming pass binds the exact persisted canonical page
bytes and reconstructed request cursors to that immutable receipt chain.
Neither pass proves an independent JD upstream signature or authorizes seal,
Agent, capacity admission, file publication, or production use.
"""
from __future__ import annotations

from itertools import zip_longest
import json
import time

from business_analysis import evidence_v4, period_bound_plan_v1
from business_analysis import v4_promotion_page_capacity as capacity
from business_analysis.contracts import AnalysisContractError

from . import business_v4_promotion_replay as replay
from . import business_v4_netshop_promotion as collector
from . import business_evidence_v3 as actor_service
from . import models as m
from .policy import AiError, authorize_owner, canonical, digest, identifier


SCHEMA = "business-v4-promotion-owning-capacity-candidate-v1"
THREE_SCHEMA = "business-v4-three-window-owning-capacity-candidate-v1"
MAX_TWO_PASS_SECONDS = 1200
MAX_THREE_WINDOW_SECONDS = 3600


def _need(value, message="v4推广容量页与当前拥有方审计或重放证明不一致"):
    if not value:
        raise AiError(message, "conflict", 409)


def _receipt_pages(parent, source, query, actor, proof, checkpoint, deadline):
    """Yield one DB-persisted UTF-8 page with its exact audited request cursor."""
    chunks = m.AiBusinessV4Chunk.objects.filter(run_id=parent.id,
        source_id=source.id).order_by("sequence").iterator(chunk_size=1)
    receipts = m.AiBusinessV4ToolReceipt.objects.filter(run_id=parent.id,
        source_id=source.id).select_related("audit").order_by(
        "sequence").iterator(chunk_size=1)
    marker = object()
    cursor = None
    last_id = None
    receipt_chain = digest([])
    count = 0
    for chunk, receipt in zip_longest(chunks, receipts, fillvalue=marker):
        _need(time.monotonic() < deadline,
            "v4容量第二遍页间测量超时，不能准入")
        count += 1
        _need(chunk is not marker and receipt is not marker
            and count <= source.page_count
            and chunk.sequence == receipt.sequence == count
            and chunk.run_id == receipt.run_id == parent.id
            and chunk.source_id == receipt.source_id == source.id
            and receipt.chunk_id == chunk.id
            and receipt.actor_email == actor["email"]
            and receipt.audit_id is not None
            and receipt.audit.actor_email == actor["email"]
            and receipt.audit.actor_role == "admin"
            and receipt.audit.surface == receipt.surface == "business_collection"
            and receipt.audit.tool_name == receipt.tool_name
            and receipt.audit.request_id == receipt.request_id
            and receipt.audit.invocation_id == receipt.invocation_id
            and receipt.audit.status == "succeeded"
            and receipt.audit.error_code is None
            and receipt.audit.created_at <= chunk.created_at <= receipt.created_at,
            "v4容量页缺精确成功工具收据")
        tool = collector.FIRST_TOOL if count == 1 else collector.CONTINUATION_TOOL
        arguments = ({"domain": "netshop", **query, "limit": 100}
            if count == 1 else {**query, "limit": 100,
                "cursor": cursor, "expectedSourceRef": source.source_ref,
                "expectedRevision": source.source_revision,
                "expectedLastId": last_id})
        _need(receipt.tool_name == tool
            and receipt.audit.arguments_json == canonical({
                "argumentsDigest": digest(arguments)}),
            "v4容量请求游标未被同页签名审计绑定")
        raw = chunk.payload_json
        _need(type(raw) is str)
        encoded = raw.encode("utf-8")
        _need(0 < len(encoded) <= capacity.MAX_PAGE_BYTES
            and digest(raw) == chunk.payload_digest
            and receipt.response_digest == chunk.payload_digest
            and receipt.audit.response_digest == chunk.payload_digest
            and receipt.payload_bytes == len(encoded)
            and chunk.source_ref == source.source_ref
            and chunk.source_revision == source.source_revision,
            "v4容量原文与来源修订或审计响应不同")
        try:
            page = json.loads(raw)
            _need(raw == canonical(page))
            next_cursor = page["pagination"]["nextCursor"]
            next_last_id = (int(page["items"][-1]["rowId"])
                if next_cursor else None)
        except (ValueError, TypeError, KeyError, IndexError,
                UnicodeError, RecursionError) as error:
            raise AiError("v4容量页原文游标不能重建", "conflict", 409) from error
        receipt_chain = digest([receipt_chain, count, chunk.payload_digest,
            receipt.audit_id, receipt.invocation_id, chunk.source_ref,
            chunk.source_revision])
        if checkpoint is not None:
            checkpoint({"stage": "v4_owning_capacity", "phase": "page",
                "sourceKey": source.source_key, "sequence": count})
        yield {"requestCursor": cursor, "rawPage": encoded}
        cursor, last_id = next_cursor, next_last_id
    _need(count == source.page_count
        and cursor is None
        and receipt_chain == proof["receiptChainDigest"],
        "v4容量双遍收据链或终页不同")


def measure_window(run_id, source_key, principal, *, enabled=False,
                   checkpoint=None):
    """Measure one complete current owning window; never export a v4 plan input."""
    _need(enabled is True, "v4拥有方容量测量默认关闭")
    started = time.monotonic()
    actor, parent, source, query, saved, fixed_parent, fixed_source = (
        replay._loaded(identifier(run_id), identifier(source_key), principal))
    proof = replay.inspect(parent.id, source.source_key, principal,
        checkpoint=checkpoint)
    _need(proof["fullSourceReplayVerified"] is True
        and proof["internalToolAuditBound"] is True
        and proof["requestCursorAuditVerified"] is True
        and proof["upstreamSignatureVerified"] is False
        and proof["sourceId"] == source.id
        and proof["queryDigest"] == source.query_digest
        and proof["sourceRef"] == source.source_ref
        and proof["sourceRevision"] == source.source_revision
        and proof["sourceVersion"] == source.version
        and proof["runVersion"] == parent.version
        and proof["proofDigest"] == digest({key: value for key, value
            in proof.items() if key != "proofDigest"}),
        "v4拥有方完整重放证明不是当前来源")
    _need(time.monotonic() - started < MAX_TWO_PASS_SECONDS)
    try:
        measured = capacity.measure_complete_pages({"key": source.source_key,
            "domain": "netshop", "query": query},
            _receipt_pages(parent, source, query, actor, proof, checkpoint,
                started + MAX_TWO_PASS_SECONDS),
            expected_row_count=source.row_count, checkpoint=checkpoint)
    except AnalysisContractError as error:
        raise AiError("v4拥有方容量页未通过纯字节与容量复核",
            "conflict", 409) from error
    _need(time.monotonic() - started < MAX_TWO_PASS_SECONDS
        and measured["completeSuppliedPageStreamReconciled"] is True
        and measured["suppliedRequestCursorChainConsistent"] is True
        and measured["signedRequestCursorAuditVerified"] is False
        and measured["v4Measurement"] is None
        and measured["sourceKey"] == source.source_key
        and measured["queryDigest"] == source.query_digest
        and measured["sourceRef"] == source.source_ref
        and measured["sourceRevision"] == source.source_revision
        and (measured["pageCount"], measured["rowCount"],
             measured["storedBytes"]) ==
            (proof["pageCount"], proof["rowCount"], proof["storedBytes"])
        and measured["controlEvidenceDigest"] ==
            proof["reconciliation"]["evidenceDigest"]
        and measured["coverage"] == proof["coverage"],
        "v4容量双遍页数、原字节或控制汇总不一致")
    replay._final_fence(actor, parent, source, fixed_parent, fixed_source,
        principal)
    value = {"schemaVersion": SCHEMA, "runId": parent.id,
        "runVersion": parent.version, "sourceId": source.id,
        "sourceKey": source.source_key, "sourceVersion": source.version,
        "sourceRef": source.source_ref,
        "sourceRevision": source.source_revision,
        "sourceCheckpointDigest": digest(source.checkpoint_json),
        "queryDigest": source.query_digest,
        "shop": query["shop"], "window": query["window"],
        "sourceReplayProofDigest": proof["proofDigest"],
        "sourceReceiptChainDigest": proof["receiptChainDigest"],
        "capacityMeasurementDigest": measured["measurementDigest"],
        "measuredPageCount": measured["pageCount"],
        "measuredRowCount": measured["rowCount"],
        "measuredStoredBytes": measured["storedBytes"],
        "maxRowUtf8Bytes": measured["maxRowUtf8Bytes"],
        "maxPageEnvelopeUtf8Bytes": measured["maxPageEnvelopeUtf8Bytes"],
        "estimatedUnderstatesObserved": measured["estimatedUnderstatesObserved"],
        "capacityArithmeticSupported": measured["capacityArithmeticSupported"],
        "internalSignedToolAuditBound": True,
        "requestCursorAuditBound": True,
        "databasePersistedCanonicalBytesMeasured": True,
        "rawUpstreamHttpBytesVerified": False,
        "upstreamSignatureVerified": False,
        "crossDomainCapacityVerified": False,
        "v4PlanMeasurementAvailable": False,
        "sealerOrReportAuthorityGranted": False,
        "blockingReadDeadlineVerified": False}
    return {**value, "owningCapacityDigest": digest(value)}


def measure_three_windows(run_id, principal, *, enabled=False,
                          checkpoint=None):
    """Bind current/previous/yearAgo to one v4 run, without finance capacity."""
    _need(enabled is True, "v4三期容量测量默认关闭")
    started = time.monotonic()
    actor = actor_service._actor(principal)
    parent = m.AiBusinessV4Run.objects.filter(pk=identifier(run_id)).first()
    _need(parent is not None)
    authorize_owner(parent, principal)
    plan = json.loads(parent.plan_json)
    try:
        period = period_bound_plan_v1.prepare_candidate(plan)
    except (AnalysisContractError, KeyError, TypeError, ValueError) as error:
        raise AiError("v4三期推广固定日期计划不完整", "conflict", 409) from error
    _need(period["observedDailyCoverageVerified"] is False
        and period["sourceAuthorityVerified"] is False)
    current_rows = [item for item in period["dailySources"]
        if item["window"] == "current"]
    _need(len(current_rows) == 1)
    checked_actor, checked_parent, current, query, _, fixed_parent, fixed_source = (
        replay._loaded(parent.id, current_rows[0]["sourceKey"], principal))
    _need(checked_actor == actor and checked_parent.plan_json == parent.plan_json
        and query["window"] == "current")
    windows = {}
    for name in ("current", "previous", "yearAgo"):
        _need(time.monotonic() - started < MAX_THREE_WINDOW_SECONDS)
        selected = [item for item in period["dailySources"]
            if item["window"] == name]
        _need(len(selected) == 1)
        result = measure_window(parent.id, selected[0]["sourceKey"], principal,
            enabled=True, checkpoint=checkpoint)
        _need(result["runVersion"] == parent.version
            and result["shop"] == query["shop"]
            and result["queryDigest"] == selected[0]["queryDigest"])
        windows[name] = result
    replay._final_fence(actor, checked_parent, current, fixed_parent,
        fixed_source, principal)
    for item in windows.values():
        latest_actor, latest_parent, latest_source, _, _, _, _ = replay._loaded(
            parent.id, item["sourceKey"], principal)
        _need(latest_actor == actor
            and latest_parent.version == parent.version
            and latest_parent.plan_json == parent.plan_json
            and latest_source.version == item["sourceVersion"]
            and latest_source.source_ref == item["sourceRef"]
            and latest_source.source_revision == item["sourceRevision"]
            and latest_source.query_digest == item["queryDigest"]
            and digest(latest_source.checkpoint_json) ==
                item["sourceCheckpointDigest"])
    _need(time.monotonic() - started < MAX_THREE_WINDOW_SECONDS)
    totals = {"pages": sum(item["measuredPageCount"] for item in windows.values()),
        "rows": sum(item["measuredRowCount"] for item in windows.values()),
        "bytes": sum(item["measuredStoredBytes"] for item in windows.values())}
    value = {"schemaVersion": THREE_SCHEMA, "runId": parent.id,
        "runVersion": parent.version, "shop": query["shop"],
        "periodPlanDigest": period["periodPlanDigest"],
        "windows": windows, "promotionTotals": totals,
        "promotionWithinV4RunHardCaps": (totals["pages"] <= evidence_v4.MAX_RUN_PAGES
            and totals["rows"] <= evidence_v4.MAX_RUN_PAGES *
                evidence_v4.MAX_ROWS_PER_PAGE
            and totals["bytes"] <= evidence_v4.MAX_RUN_BYTES),
        "financeCapacityVerified": False,
        "shopSalesSkuSpuMarketB2bCapacityVerified": False,
        "v4PlanMeasurementAvailable": False,
        "upstreamSignatureVerified": False,
        "sealerOrReportAuthorityGranted": False}
    return {**value, "threeWindowCapacityDigest": digest(value)}
