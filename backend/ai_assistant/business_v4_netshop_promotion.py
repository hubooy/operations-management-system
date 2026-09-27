"""Internal one-page JD promotion collector on the isolated v4 physical ledger.

No public route, scheduler, Agent or seal imports this module. Every invocation
fetches exactly one signed owning page; no caller-supplied page can be stored.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from django.utils import timezone

from business_analysis import business_promotion_v4_plan, evidence_v4
from business_analysis.contracts import AnalysisContractError, PageReconciler

from . import business_daily_collection_v3 as daily_identity
from . import business_evidence_v3 as actor_service
from . import models as m, transport
from .datasets import _result
from .policy import AiError, authorize_owner, canonical, digest, identifier, integer, mutation, passive, uid

FIRST_TOOL = "get_business_source_page"
CONTINUATION_TOOL = "get_business_netshop_continuation_page"
CHECKPOINT_SCHEMA = "business-v4-checkpoint-v1"
MAX_CHECKPOINT_BYTES = 32_768
MAX_PAGE_BYTES = 131_072
WINDOW_KEYS = {"current": "currentSourceKey", "previous": "previousSourceKey",
    "yearAgo": "yearAgoSourceKey"}


def _reject(message="v4京东推广来源检查点或拥有方页不一致", code="conflict", status=409):
    raise AiError(message, code, status)


@dataclass(frozen=True, slots=True)
class Prepared:
    run_id: str
    run_version: int
    source_id: str
    source_version: int
    source_key: str
    source_checkpoint_json: str
    source_query_json: str
    actor_json: str
    tool: str
    arguments_json: str
    expected_last_chunk_digest: str | None

    def arguments(self):
        return json.loads(self.arguments_json)


def selection(plan, query, source_key):
    """Choose the exact fixed-run peer sources; this is not owning authority."""
    if (type(query) is not dict or type(query.get("window")) is not str
            or query["window"] not in WINDOW_KEYS
            or query.get("platform") != "京东" or query.get("dataset") != "promotion"):
        _reject("v4京东推广窗口不在固定三窗口目录")
    base = {key: value for key, value in query.items() if key != "window"}
    peers = {}
    try:
        for entry in plan["sourcePlans"]:
            other = entry["query"]
            if (entry["domain"] == "netshop" and type(other) is dict
                    and other.get("dataset") == "promotion"
                    and {key: value for key, value in other.items()
                        if key != "window"} == base):
                window = other.get("window")
                if window not in WINDOW_KEYS or window in peers:
                    _reject("v4京东推广同店同窗口来源重复或非法")
                peers[window] = entry["sourceKey"]
        candidate = business_promotion_v4_plan.prepare_candidate(plan,
            {field: peers.get(window) for window, field in WINDOW_KEYS.items()})
        selected = candidate["selectedWindows"][query["window"]]
        if (selected["status"] != "selected_in_capacity_plan"
                or selected["sourceKey"] != source_key
                or selected["capacityStatus"] != "supported"):
            _reject("v4京东推广来源不属于固定计划的精确比较窗口")
        return candidate
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            AttributeError, RecursionError) as error:
        raise AiError("v4京东推广三窗口候选目录不能重建", "conflict", 409) from error


def _source(row, source_key, principal):
    actor = actor_service._actor(principal)
    authorize_owner(row, principal)
    source = m.AiBusinessV4Source.objects.filter(run_id=row.id, source_key=source_key).first()
    if source is None:
        raise AiError("v4来源不存在", "not_found", 404)
    if (source.domain != "netshop" or source.temporal_role != "daily_fact"
            or row.status != "collecting" or row.collection_status != "manual"
            or row.scope_json != "null" or row.owner_email != principal.email.lower()
            or source.version != source.page_count + 1 or row.version != row.page_count + 1
            or source.finished
            or source.query_digest != digest(source.query_json)
            or row.plan_digest != digest(row.plan_json)):
        _reject("v4京东推广来源不在可追加的精确状态")
    if (source.page_count >= evidence_v4.MAX_SOURCE_PAGES
            or row.page_count >= evidence_v4.MAX_RUN_PAGES
            or source.stored_bytes >= evidence_v4.MAX_SOURCE_BYTES
            or row.stored_bytes >= evidence_v4.MAX_RUN_BYTES):
        _reject("v4完整事实容量达到硬上限，原检查点保留", "payload_too_large", 413)
    try:
        plan = json.loads(row.plan_json)
        query = json.loads(source.query_json)
        entry = next(item for item in plan["sourcePlans"] if item["sourceKey"] == source.source_key)
        if (row.plan_json != canonical(plan) or source.query_json != canonical(query)
                or plan["runIdentityDigest"] != row.run_identity_digest
                or plan["schemaVersion"] != evidence_v4.PLAN_SCHEMA
                or plan["capacityProfile"] != evidence_v4.CAPACITY_PROFILE
                or plan["runCapacitySupported"] is not True
                or plan["sourceAuthorityVerified"] is not False
                or plan["reportGenerationSupported"] is not False
                or entry["ordinal"] != source.ordinal or entry["domain"] != source.domain
                or entry["temporalRole"] != source.temporal_role
                or entry["queryDigest"] != source.query_digest
                or entry["sourceIdentityDigest"] != source.source_identity_digest
                or entry["query"] != query or entry["sourceCapacitySupported"] is not True
                or query != {"platform": "京东", "shop": query.get("shop"),
                    "dataset": "promotion", "startDate": query.get("startDate"),
                    "endDate": query.get("endDate"), "window": query.get("window")}
                or query["window"] not in WINDOW_KEYS
                or type(query["shop"]) is not str or not query["shop"]):
            _reject("v4仅接受固定京东推广精确窗口来源")
        selection(plan, query, source.source_key)
    except (KeyError, ValueError, TypeError, StopIteration, RecursionError) as error:
        raise AiError("v4京东推广计划或精确查询无法重建", "conflict", 409) from error
    return actor, source, query


def _checkpoint(source):
    if source.page_count == 0:
        if (source.checkpoint_json != "{}" or source.source_ref or source.source_revision
                or source.row_count or source.stored_bytes or source.version != 1):
            _reject("v4来源初始检查点不干净")
        return None, PageReconciler(), None, None
    try:
        saved = json.loads(source.checkpoint_json)
        if (type(saved) is not dict or source.checkpoint_json != canonical(saved)
                or set(saved) != {"schemaVersion", "sourceRef",
                "sourceRevision", "lastChunkDigest", "pageCount", "rowCount", "storedBytes",
                "finished", "verifier", "metadata"}
                or saved["schemaVersion"] != CHECKPOINT_SCHEMA
                or saved["sourceRef"] != source.source_ref
                or saved["sourceRevision"] != source.source_revision
                or saved["pageCount"] != source.page_count
                or saved["rowCount"] != source.row_count
                or saved["storedBytes"] != source.stored_bytes
                or saved["finished"] is not False
                or type(saved["verifier"]) is not dict
                or set(saved["verifier"]) != set(PageReconciler().__dict__)
                or type(saved["metadata"]) is not dict):
            _reject("v4来源检查点形状与真实计数不一致")
        last = m.AiBusinessV4Chunk.objects.filter(run_id=source.run_id, source_id=source.id,
            sequence=source.page_count).values("payload_json", "payload_digest",
                "source_ref", "source_revision", "row_count").first()
        if (last is None or last["payload_digest"] != saved["lastChunkDigest"]
                or last["payload_digest"] != digest(last["payload_json"])
                or last["source_ref"] != source.source_ref
                or last["source_revision"] != source.source_revision):
            _reject("v4末块摘要与来源检查点不一致")
        page = json.loads(last["payload_json"])
        verifier = PageReconciler()
        verifier.__dict__.update(saved["verifier"])
        last_id = verifier.last_id
        cursor = verifier.expected_cursor
        items = page["items"]
        if type(items) is not list or not items:
            _reject("v4续读末页缺少真实行")
        tail = items[-1]["rowId"]
        if (type(cursor) is not str or not 1 <= len(cursor) <= 1600
                or type(last_id) is not int or last_id < 1
                or len(items) != last["row_count"]
                or page["sourceRef"] != source.source_ref
                or page["sourceRevision"] != source.source_revision
                or page["pagination"]["hasMore"] is not True
                or page["pagination"]["nextCursor"] != cursor
                or page["pagination"]["limit"] != 100
                or page["pageEvidence"]["rowCount"] != len(items)
                or page["pageEvidence"]["sha256"] != digest(items)
                or int(tail) != last_id or verifier.rows != source.row_count
                or verifier.source_ref != source.source_ref or verifier.finished is not False
                or saved["metadata"]["sourceRevision"] != source.source_revision):
            _reject("v4续读末块与原签名游标或实际行号不一致")
        return saved, verifier, saved["metadata"].copy(), last["payload_digest"]
    except (KeyError, IndexError, ValueError, TypeError, AttributeError, RecursionError) as error:
        raise AiError("v4末块不是可恢复的真实来源页", "conflict", 409) from error


def prepare(run_id, source_key, expected_version, principal):
    identifier(run_id); identifier(source_key)
    integer(expected_version, "expectedVersion")
    row = m.AiBusinessV4Run.objects.filter(pk=run_id).first()
    if row is None:
        raise AiError("v4证据任务不存在", "not_found", 404)
    actor, source, query = _source(row, source_key, principal)
    if row.version != expected_version:
        raise AiError("v4父任务版本已变化", "version_conflict", 409)
    saved, verifier, _, last_digest = _checkpoint(source)
    if saved is None:
        tool = FIRST_TOOL
        arguments = {"domain": "netshop", **query, "limit": 100}
    else:
        tool = CONTINUATION_TOOL
        arguments = {**query, "limit": 100, "cursor": verifier.expected_cursor,
            "expectedSourceRef": source.source_ref,
            "expectedRevision": source.source_revision,
            "expectedLastId": verifier.last_id}
    return Prepared(row.id, row.version, source.id, source.version, source.source_key,
        source.checkpoint_json, source.query_json, canonical(actor), tool,
        canonical(arguments), last_digest)


def _unchanged(prepared, principal):
    if canonical(actor_service._actor(principal)) != prepared.actor_json:
        raise AiError("v4采集期间账号权限变化", "access_denied", 403)
    parent = m.AiBusinessV4Run.objects.filter(pk=prepared.run_id).values(
        "version", "status", "collection_status").first()
    source = m.AiBusinessV4Source.objects.filter(pk=prepared.source_id,
        run_id=prepared.run_id).values("version", "checkpoint_json", "query_json",
            "finished").first()
    if (parent != {"version": prepared.run_version, "status": "collecting",
            "collection_status": "manual"}
            or source != {"version": prepared.source_version,
                "checkpoint_json": prepared.source_checkpoint_json,
                "query_json": prepared.source_query_json, "finished": False}):
        raise AiError("v4采集期间来源或父任务CAS变化", "version_conflict", 409)


def _audit(principal, request_id, tool, arguments, encoded, started_at):
    records = list(m.AiToolAuditLogs.objects.filter(request_id=request_id,
        actor_email=principal.email.lower(), actor_role="admin",
        surface="business_collection", tool_name=tool, status="succeeded",
        response_digest=digest(encoded), error_code__isnull=True,
        arguments_json=canonical({"argumentsDigest": digest(arguments)}),
        created_at__gte=started_at).values("id", "invocation_id")[:2])
    if len(records) != 1:
        _reject("v4来源页缺少唯一、当前调用的成功签名工具审计")
    return records[0]


def advance(run_id, source_key, expected_version, principal, request_id):
    """Fetch and commit exactly one JD promotion page; no caller page parameter."""
    identifier(request_id, "requestId")
    if len(request_id) > 128:
        raise AiError("v4内部请求ID超过签名桥容量")
    prepared = prepare(run_id, source_key, expected_version, principal)
    entries = transport.catalog(principal, "business_collection")
    matches = [item for item in entries if item.get("name") == prepared.tool]
    if (len(matches) != 1 or matches[0].get("risk") != "read_only"
            or matches[0].get("allowedRoles") != ["admin"]
            or matches[0].get("scopePolicy") != "unscoped_only"
            or matches[0].get("execution", {}).get("mode") != "direct"
            or matches[0].get("execution", {}).get("allowedSurfaces") != ["business_collection"]):
        raise AiError("v4拥有方签名工具策略不可用", "access_denied", 403)
    started_at = timezone.now()
    arguments = prepared.arguments()
    with transport.request_budget(30):
        page = _result(transport.execute_tool(prepared.tool, arguments, principal,
            surface="business_collection", request_id=request_id,
            policy_digest=digest(entries)), prepared.tool)
    _unchanged(prepared, principal)
    query = json.loads(prepared.source_query_json)
    source = m.AiBusinessV4Source.objects.get(pk=prepared.source_id)
    saved, verifier, metadata, last_digest = _checkpoint(source)
    first = saved is None
    if last_digest != prepared.expected_last_chunk_digest:
        raise AiError("v4末块在签名读取期间变化", "version_conflict", 409)
    if metadata is None:
        metadata = {"sourceRevision": page["sourceRevision"],
            "coverage": page.get("coverage"),
            "excludedOverlappingPeriodRows": page.get("excludedOverlappingPeriodRows"),
            "identityCheck": page.get("identityCheck"),
            "availableDates": page.get("availableDates"),
            "metricSemantics": page.get("metricSemantics"), "freshness": None,
            "firstCollectedAt": timezone.now().isoformat()}
    try:
        daily_identity._Identity()._identity({"domain": "netshop", "query": query},
            page, metadata, first)
        verifier.consume(page, request_cursor=verifier.expected_cursor)
        if verifier.finished:
            verifier.result()
        metadata["lastCollectedAt"] = timezone.now().isoformat()
        encoded = canonical(passive(page, MAX_PAGE_BYTES))
        size = len(encoded.encode("utf-8"))
        if (size > MAX_PAGE_BYTES or not 0 <= len(page["items"]) <= 100
                or verifier.rows != source.row_count + len(page["items"])):
            raise AnalysisContractError("v4推广来源页容量或行数变化")
        checkpoint = {"schemaVersion": CHECKPOINT_SCHEMA,
            "sourceRef": page["sourceRef"], "sourceRevision": page["sourceRevision"],
            "lastChunkDigest": digest(encoded), "pageCount": source.page_count + 1,
            "rowCount": verifier.rows, "storedBytes": source.stored_bytes + size,
            "finished": verifier.finished, "verifier": verifier.__dict__,
            "metadata": metadata}
        saved_json = canonical(passive(checkpoint, MAX_CHECKPOINT_BYTES))
    except (AnalysisContractError, KeyError, TypeError, ValueError, AttributeError,
            RecursionError) as error:
        raise AiError("v4拥有方推广页未通过完整过滤器、修订或行链校验", "conflict", 409) from error
    audit = _audit(principal, request_id, prepared.tool, arguments, encoded, started_at)
    with mutation(principal):
        if canonical(actor_service._actor(principal)) != prepared.actor_json:
            raise AiError("v4采集落地前账号权限变化", "access_denied", 403)
        parent = authorize_owner(m.AiBusinessV4Run.objects.select_for_update().get(pk=prepared.run_id), principal)
        record = m.AiBusinessV4Source.objects.select_for_update().get(pk=prepared.source_id, run_id=parent.id)
        if (parent.version != prepared.run_version or parent.status != "collecting"
                or parent.collection_status != "manual" or record.version != prepared.source_version
                or record.finished or record.checkpoint_json != prepared.source_checkpoint_json
                or record.query_json != prepared.source_query_json
                or record.page_count != source.page_count
                or record.stored_bytes != source.stored_bytes
                or record.row_count != source.row_count):
            raise AiError("v4父任务或来源检查点CAS变化", "version_conflict", 409)
        if (record.page_count >= evidence_v4.MAX_SOURCE_PAGES
                or parent.page_count >= evidence_v4.MAX_RUN_PAGES
                or record.stored_bytes + size > evidence_v4.MAX_SOURCE_BYTES
                or parent.stored_bytes + size > evidence_v4.MAX_RUN_BYTES):
            _reject("v4完整事实容量达到硬上限，原检查点保留", "payload_too_large", 413)
        chunk = m.AiBusinessV4Chunk.objects.create(id=uid("v4-chunk"), run=parent,
            source=record, sequence=record.page_count + 1,
            payload_json=encoded, payload_digest=digest(encoded),
            source_ref=page["sourceRef"], source_revision=page["sourceRevision"],
            row_count=len(page["items"]))
        m.AiBusinessV4ToolReceipt.objects.create(chunk=chunk, audit_id=audit["id"],
            run=parent, source=record, sequence=chunk.sequence,
            actor_email=principal.email.lower(), request_id=request_id,
            invocation_id=audit["invocation_id"], tool_name=prepared.tool,
            surface="business_collection", response_digest=chunk.payload_digest,
            payload_bytes=size)
        record.source_ref = page["sourceRef"]
        record.source_revision = page["sourceRevision"]
        record.checkpoint_json = saved_json
        record.page_count += 1
        record.row_count = verifier.rows
        record.stored_bytes += size
        record.finished = verifier.finished
        record.version += 1
        record.updated_at = timezone.now()
        record.save(update_fields=["source_ref", "source_revision", "checkpoint_json",
            "page_count", "row_count", "stored_bytes", "finished", "version", "updated_at"])
        parent.page_count += 1
        parent.row_count += len(page["items"])
        parent.stored_bytes += size
        parent.version += 1
        parent.save(update_fields=["page_count", "row_count", "stored_bytes", "version"])
        if canonical(actor_service._actor(principal)) != prepared.actor_json:
            raise AiError("v4采集落地后账号权限变化", "access_denied", 403)
    return {"runId": parent.id, "runVersion": parent.version,
        "sourceKey": record.source_key, "sourceVersion": record.version,
        "pageCount": record.page_count, "rowCount": record.row_count,
        "storedBytes": record.stored_bytes, "finished": record.finished,
        "sourceRef": record.source_ref, "sourceRevision": record.source_revision,
        "sourceAuthorityVerified": False, "persistentEvidenceVerified": False,
        "reportGenerationSupported": False}
