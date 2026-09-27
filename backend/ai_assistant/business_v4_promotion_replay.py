"""Read-only full replay of one completed v4 JD promotion window source.

The internal HMAC tool audit is bound to each immutable page, but it is not an
independent upstream signature. This module neither seals a parent nor grants
report, Agent, file, or public-read authority.
"""
from itertools import zip_longest
import json
import re
import time

from business_analysis import evidence_v4, promotion_views
from business_analysis.contracts import (AnalysisContractError, PageReconciler,
    MAX_SAFE_INTEGER, comparison_periods, coverage)
from business_analysis.partitioned import Checkpoint
from . import business_daily_collection_v3 as daily_identity
from . import business_evidence_v3 as actor_service, models as m
from . import business_v4_netshop_promotion as collector
from .policy import AiError, authorize_owner, canonical, digest, identifier


SCHEMA = "business-v4-jd-promotion-complete-replay-candidate-v1"
MAX_PROOF_BYTES = 38_000
MAX_REPLAY_SECONDS = 600


def _need(ok, message="v4京东推广完整来源重放失败"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _checkpoint(raw):
    _need(type(raw) is str and len(raw.encode("utf-8")) <=
        collector.MAX_CHECKPOINT_BYTES,
        "v4推广检查点字节超过固定容量，禁止解析")
    return json.loads(raw)


def _promotion_page(page, *, first):
    """Bound source row shape before PageReconciler copies metric keys."""
    _need(type(page) is dict and type(page.get("items")) is list
        and len(page["items"]) <= 100)
    control = page.get("control")
    if first:
        _need(type(control) is dict
            and type(control.get("rowCount")) is int
            and 0 <= control["rowCount"] <= evidence_v4.MAX_SOURCE_PAGES*100
            and type(control.get("typedTotals")) is dict
            and set(control["typedTotals"]) == promotion_views.BASE_METRICS
            and all(type(amount) is int and abs(amount) <= MAX_SAFE_INTEGER
                for amount in control["typedTotals"].values()),
            "v4推广首页控制总额混入未知指标")
    else:
        _need(control is None, "v4推广后续页不能重置控制总额")
    for row in page["items"]:
        _need(type(row) is dict and set(row) == promotion_views.ROW_FIELDS
            and type(row.get("rowId")) is str
            and re.fullmatch(r"[1-9][0-9]{0,19}", row["rowId"]) is not None
            and type(row.get("sourceRowHash")) is str
            and re.fullmatch(r"[0-9a-f]{64}", row["sourceRowHash"]) is not None
            and type(row.get("batchId")) is str
            and 1 <= len(row["batchId"]) <= 160
            and type(row.get("dimensions")) is dict
            and set(row["dimensions"]) == promotion_views.DIMENSIONS
            and all(value is None or type(value) is str
                and 0 < len(value) <= 240 and value == value.strip()
                for value in row["dimensions"].values())
            and type(row.get("metrics")) is dict
            and set(row["metrics"]) == promotion_views.METRICS
            and all(value is None or type(value) is int
                and abs(value) <= MAX_SAFE_INTEGER
                for value in row["metrics"].values()),
            "v4推广规范行或固定指标集合无效")


def _loaded(run_id, source_key, principal):
    actor = actor_service._actor(principal)
    parent = m.AiBusinessV4Run.objects.filter(pk=identifier(run_id)).first()
    if parent is None:
        raise AiError("v4推广父任务不存在", "not_found", 404)
    authorize_owner(parent, principal)
    source = m.AiBusinessV4Source.objects.filter(run_id=parent.id,
        source_key=identifier(source_key)).first()
    if source is None:
        raise AiError("v4推广来源不存在", "not_found", 404)
    try:
        _need(type(parent.plan_json) is str
            and len(parent.plan_json.encode("utf-8")) <= 131072
            and type(source.query_json) is str
            and len(source.query_json.encode("utf-8")) <= 4096)
        plan = json.loads(parent.plan_json)
        query = json.loads(source.query_json)
        checkpoint = _checkpoint(source.checkpoint_json)
        entries = plan["sourcePlans"]
        entry = next(item for item in entries if item["sourceKey"] == source.source_key)
        source_rows = list(m.AiBusinessV4Source.objects.filter(run_id=parent.id)
            .order_by("ordinal").values("source_key", "ordinal", "domain",
                "temporal_role", "query_json", "query_digest",
                "source_identity_digest", "page_count", "row_count",
                "stored_bytes")[:49])
        if (parent.status != "collecting" or parent.collection_status != "manual"
                or parent.owner_email != actor["email"] or parent.scope_json != "null"
                or parent.version != parent.page_count + 1
                or parent.plan_json != canonical(plan)
                or parent.plan_digest != digest(parent.plan_json)
                or plan.get("planDigest") != digest({key:value for key,value in plan.items()
                    if key != "planDigest"})
                or plan.get("schemaVersion") != evidence_v4.PLAN_SCHEMA
                or plan.get("capacityProfile") != evidence_v4.CAPACITY_PROFILE
                or plan.get("runCapacitySupported") is not True
                or plan.get("sourceAuthorityVerified") is not False
                or plan.get("measurementAuthorityVerified") is not False
                or plan.get("productionRowWidthApprovalRequired") is not True
                or plan.get("reportGenerationSupported") is not False
                or plan.get("modelDispatchSupported") is not False
                or plan.get("runIdentityDigest") != parent.run_identity_digest
                or source.domain != "netshop" or source.temporal_role != "daily_fact"
                or query != {"platform":"京东", "shop":query.get("shop"),
                    "dataset":"promotion", "startDate":query.get("startDate"),
                    "endDate":query.get("endDate"), "window":query.get("window")}
                or type(query.get("window")) is not str
                or query["window"] not in collector.WINDOW_KEYS
                or source.query_json != canonical(query)
                or source.query_digest != digest(source.query_json)
                or type(query.get("shop")) is not str or not query["shop"]
                or re.fullmatch(r"[0-9a-f]{64}", source.source_ref or "") is None
                or type(source.source_revision) is not str
                or not 1 <= len(source.source_revision) <= 128
                or entry["ordinal"] != source.ordinal
                or entry["domain"] != source.domain
                or entry["temporalRole"] != source.temporal_role
                or entry["query"] != query
                or entry["queryDigest"] != source.query_digest
                or entry["sourceIdentityDigest"] != source.source_identity_digest
                or source.version != source.page_count + 1
                or not source.finished
                or not 1 <= source.page_count <= evidence_v4.MAX_SOURCE_PAGES
                or not 0 <= source.row_count <= evidence_v4.MAX_SOURCE_PAGES*100
                or not 0 < source.stored_bytes <= evidence_v4.MAX_SOURCE_BYTES
                or len(source_rows) != len(entries)
                or not 2 <= len(entries) <= evidence_v4.MAX_SOURCES
                or [item["ordinal"] for item in entries] != list(range(1,len(entries)+1))
                or any((row["source_key"],row["ordinal"],row["domain"],
                    row["temporal_role"],row["query_json"],row["query_digest"],
                    row["source_identity_digest"]) !=
                    (item["sourceKey"],item["ordinal"],item["domain"],
                    item["temporalRole"],canonical(item["query"]),item["queryDigest"],
                    item["sourceIdentityDigest"])
                    for row,item in zip(source_rows,entries))
                or sum(row["page_count"] for row in source_rows) != parent.page_count
                or sum(row["row_count"] for row in source_rows) != parent.row_count
                or sum(row["stored_bytes"] for row in source_rows) != parent.stored_bytes):
            _need(False, "v4推广仅接受完整精确比较窗口来源及一致父目录")
        collector.selection(plan, query, source.source_key)
        _need(type(checkpoint) is dict and set(checkpoint) ==
            {"schemaVersion", "sourceRef", "sourceRevision", "lastChunkDigest",
             "pageCount", "rowCount", "storedBytes", "finished", "verifier", "metadata"}
            and checkpoint["schemaVersion"] == collector.CHECKPOINT_SCHEMA
            and source.checkpoint_json == canonical(checkpoint)
            and checkpoint["sourceRef"] == source.source_ref
            and checkpoint["sourceRevision"] == source.source_revision
            and checkpoint["pageCount"] == source.page_count
            and checkpoint["rowCount"] == source.row_count
            and checkpoint["storedBytes"] == source.stored_bytes
            and checkpoint["finished"] is True
            and type(checkpoint["verifier"]) is dict
            and set(checkpoint["verifier"]) == set(PageReconciler().__dict__)
            and type(checkpoint["metadata"]) is dict,
            "v4推广完成检查点无效")
    except (KeyError, StopIteration, ValueError, TypeError, AttributeError, UnicodeError,
            RecursionError) as error:
        raise AiError("v4推广父目录或来源检查点不可重建", "conflict", 409) from error
    fixed_parent = {key:getattr(parent,key) for key in ("version","status",
        "collection_status","plan_json","plan_digest","run_identity_digest",
        "page_count","row_count","stored_bytes")}
    fixed_source = {key:getattr(source,key) for key in ("version","finished",
        "query_json","query_digest","checkpoint_json","page_count",
        "row_count","stored_bytes","source_ref","source_revision")}
    return actor, parent, source, query, checkpoint, fixed_parent, fixed_source


def _final_fence(actor, parent, source, fixed_parent, fixed_source, principal):
    if actor_service._actor(principal) != actor:
        raise AiError("v4推广重放期间账号权限变化", "access_denied", 403)
    current_parent = m.AiBusinessV4Run.objects.filter(pk=parent.id).values(*fixed_parent).first()
    current_source = m.AiBusinessV4Source.objects.filter(pk=source.id,
        run_id=parent.id).values(*fixed_source).first()
    _need(current_parent == fixed_parent and current_source == fixed_source,
        "v4推广重放期间父任务或来源版本变化")
    if actor_service._actor(principal) != actor:
        raise AiError("v4推广重放完成时账号权限变化", "access_denied", 403)


def inspect(run_id, source_key, principal, *, checkpoint=None):
    """Replay every actual page and matching audit receipt, returning proof only."""
    deadline = time.monotonic() + MAX_REPLAY_SECONDS
    check = Checkpoint.wrap(checkpoint)
    actor, parent, source, query, saved, fixed_parent, fixed_source = (
        _loaded(run_id, source_key, principal))
    identity = daily_identity._Identity()
    verifier, pages, rows, size, last_digest, receipt_chain = (
        PageReconciler(), 0, 0, 0, None, digest([]))
    observed_dates = set()
    requested_cursor, requested_last_id = None, None
    period = comparison_periods(query["startDate"], query["endDate"])[query["window"]]
    _need(not m.AiBusinessSourceToolReceipt.objects.filter(audit_id__in=
        m.AiBusinessV4ToolReceipt.objects.filter(run_id=parent.id,
            source_id=source.id).values("audit_id")).exists(),
        "v4推广工具审计不得复用历史v3事实收据")
    chunks = m.AiBusinessV4Chunk.objects.filter(run_id=parent.id,
        source_id=source.id).order_by("sequence").iterator(chunk_size=16)
    receipts = m.AiBusinessV4ToolReceipt.objects.filter(run_id=parent.id,
        source_id=source.id).select_related("audit").order_by("sequence").iterator(chunk_size=16)
    marker = object()
    try:
        for chunk, receipt in zip_longest(chunks, receipts, fillvalue=marker):
            pages += 1
            _need(time.monotonic() <= deadline,
                "v4推广完整重放超过固定时间边界")
            if check is not None and pages % 20 == 1:
                check({"stage":"v4_promotion_replay","phase":"page","sequence":pages})
            if (chunk is marker or receipt is marker or pages > source.page_count
                    or chunk.sequence != pages or receipt.sequence != pages
                    or chunk.run_id != parent.id or chunk.source_id != source.id
                    or receipt.run_id != parent.id or receipt.source_id != source.id
                    or receipt.chunk_id != chunk.id or receipt.actor_email != actor["email"]
                    or receipt.tool_name != (collector.FIRST_TOOL if pages == 1
                        else collector.CONTINUATION_TOOL)
                    or receipt.surface != "business_collection"
                    or receipt.audit_id is None or receipt.audit.actor_email != actor["email"]
                    or receipt.audit.actor_role != "admin"
                    or receipt.audit.surface != "business_collection"
                    or receipt.audit.tool_name != receipt.tool_name
                    or receipt.audit.request_id != receipt.request_id
                    or receipt.audit.invocation_id != receipt.invocation_id
                    or receipt.audit.status != "succeeded"
                    or receipt.audit.error_code is not None
                    or receipt.audit.created_at > chunk.created_at
                    or chunk.created_at > receipt.created_at):
                _need(False, "v4推广页序或内部工具审计收据不完整")
            expected_arguments = ({"domain": "netshop", **query, "limit": 100}
                if pages == 1 else {**query, "limit": 100,
                    "cursor": requested_cursor,
                    "expectedSourceRef": source.source_ref,
                    "expectedRevision": source.source_revision,
                    "expectedLastId": requested_last_id})
            _need((pages == 1 or type(requested_cursor) is str
                and type(requested_last_id) is int and requested_last_id >= 1)
                and receipt.audit.arguments_json == canonical({
                    "argumentsDigest": digest(expected_arguments)}),
                "v4推广工具审计不属于前页签名游标和精确查询")
            raw = chunk.payload_json
            encoded_size = len(raw.encode("utf-8"))
            size += encoded_size
            _need(type(raw) is str and encoded_size <= collector.MAX_PAGE_BYTES
                and size <= evidence_v4.MAX_SOURCE_BYTES
                and digest(raw) == chunk.payload_digest
                and chunk.source_ref == source.source_ref
                and chunk.source_revision == source.source_revision
                and receipt.response_digest == chunk.payload_digest
                and receipt.audit.response_digest == chunk.payload_digest
                and receipt.payload_bytes == encoded_size,
                "v4推广事实字节与内部工具审计不一致")
            page = json.loads(raw)
            _need(raw == canonical(page), "v4推广事实块不是规范JSON")
            _promotion_page(page, first=pages == 1)
            identity._identity({"domain":"netshop","query":query}, page,
                saved["metadata"], pages == 1)
            verifier.consume(page, request_cursor=requested_cursor)
            observed_dates.update(item["date"] for item in page["items"])
            _need(chunk.row_count == len(page["items"])
                and receipt.tool_name == (collector.FIRST_TOOL if pages == 1
                    else collector.CONTINUATION_TOOL),
                "v4推广源行数或工具序列不一致")
            rows += chunk.row_count
            requested_cursor = page["pagination"]["nextCursor"]
            requested_last_id = int(page["items"][-1]["rowId"]) if requested_cursor else None
            last_digest = chunk.payload_digest
            receipt_chain = digest([receipt_chain, pages, chunk.payload_digest,
                receipt.audit_id, receipt.invocation_id,
                chunk.source_ref, chunk.source_revision])
        reconciled = verifier.result()
        _need(set(reconciled["metrics"]) ==
            (promotion_views.METRICS if rows else promotion_views.BASE_METRICS),
            "v4推广重放结果指标目录与规范事实不一致")
        _need(pages == source.page_count and rows == source.row_count
            and size == source.stored_bytes
            and last_digest == saved["lastChunkDigest"]
            and verifier.finished is True
            and reconciled["rowCount"] == rows
            and saved["metadata"].get("coverage") == coverage(period, observed_dates)
            and canonical(verifier.__dict__) == canonical(saved["verifier"]),
            "v4推广终页、完整控制汇总或检查点与实际事实不同")
    except (AnalysisContractError, KeyError, ValueError, TypeError,
            AttributeError, UnicodeError, RecursionError) as error:
        if check is not None: check.raise_if_failed()
        raise AiError("v4推广完整页链或拥有方身份核验失败", "conflict", 409) from error
    if check is not None:
        check({"stage":"v4_promotion_replay","phase":"complete"})
    _need(time.monotonic() <= deadline,
        "v4推广完整重放超过固定时间边界")
    _final_fence(actor, parent, source, fixed_parent, fixed_source, principal)
    proof = {"schemaVersion":SCHEMA, "runId":parent.id,
        "sourceId":source.id, "sourceKey":source.source_key,
        "sourceVersion":source.version, "runVersion":parent.version,
        "queryDigest":source.query_digest, "sourceRef":source.source_ref,
        "sourceRevision":source.source_revision, "pageCount":pages,
        "rowCount":rows, "storedBytes":size,
        "reconciliation":reconciled,
        "coverage":saved["metadata"].get("coverage"),
        "receiptChainDigest":receipt_chain,
        "fullSourceReplayVerified":True, "internalToolAuditBound":True,
        "payloadCursorChainVerified":True,
        "requestCursorAuditVerified":True,
        "upstreamSignatureVerified":False, "sealed":False,
        "persistentEvidenceVerified":False, "reportGenerationSupported":False,
        "registeredAgentTool":False, "registeredRenderer":False}
    proof["proofDigest"] = digest(proof)
    _need(len(canonical(proof).encode("utf-8")) <= MAX_PROOF_BYTES,
        "v4推广完整来源证明超过固定响应容量")
    _need(time.monotonic() <= deadline,
        "v4推广证明返回前超过固定时间边界")
    return proof
