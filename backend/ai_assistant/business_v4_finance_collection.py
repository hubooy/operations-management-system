"""Internal one-page signed monthly finance collection on the v4 physical ledger.

No caller page, public route, scheduler, parent seal, Agent, model or report.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from django.utils import timezone

from business_analysis import (business_promotion_v4_plan, evidence_v4,
    finance_collection_state_v4 as verifier)
from business_analysis.contracts import AnalysisContractError

from . import business_evidence_v3 as actor_service, models as m, transport
from .datasets import _result
from .policy import (AiError, authorize_owner, canonical, digest, identifier,
    integer, mutation, passive, uid)

TOOL = "get_business_finance_source_page"
CHECKPOINT_SCHEMA = "business-v4-checkpoint-v1"
MAX_CHECKPOINT_BYTES = 32_768  # Exact 0035 SOURCE_GUARD UTF-8 cap, including v4 envelope.


def _reject(message="v4财报自然月来源、检查点或签名页不一致"):
    raise AiError(message, "conflict", 409)


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
    arguments_json: str
    expected_last_chunk_digest: str | None

    def arguments(self):
        return json.loads(self.arguments_json)


def _plan(parent, source):
    if (type(parent.plan_json) is not str or len(parent.plan_json.encode("utf-8")) > 131_072
            or type(source.query_json) is not str
            or len(source.query_json.encode("utf-8")) > 4096):
        _reject("v4财报固定计划或自然月查询超过容量")
    try:
        plan = json.loads(parent.plan_json)
        query = json.loads(source.query_json)
        entries = plan["sourcePlans"]
        # The candidate module's _plan validates the generic v4 capacity
        # envelope from stored measurements; its promotion selector is not used.
        business_promotion_v4_plan._plan(plan)
        selected = next(item for item in entries if item["sourceKey"] == source.source_key)
        normalized, _ = verifier._query(query)
        if (parent.plan_json != canonical(plan)
                or parent.plan_digest != digest(parent.plan_json)
                or parent.run_identity_digest != plan["runIdentityDigest"]
                or plan["runCapacitySupported"] is not True
                or plan["sourceAuthorityVerified"] is not False
                or plan["reportGenerationSupported"] is not False
                or source.query_json != canonical(query) or query != normalized
                or source.query_digest != digest(source.query_json)
                or source.domain != "finance"
                or source.temporal_role != "monthly_context"
                or selected["ordinal"] != source.ordinal
                or selected["query"] != query
                or selected["queryDigest"] != source.query_digest
                or selected["sourceIdentityDigest"] != source.source_identity_digest
                or selected["sourceCapacitySupported"] is not True):
            _reject("v4财报来源不属于固定自然月容量计划")
        return query
    except (AnalysisContractError, KeyError, StopIteration, TypeError,
            ValueError, AttributeError, RecursionError) as error:
        raise AiError("v4财报固定计划、自然月或精确范围不能重建", "conflict", 409) from error


def _source(parent, source_key, principal):
    actor = actor_service._actor(principal)
    authorize_owner(parent, principal)
    source = m.AiBusinessV4Source.objects.filter(run_id=parent.id,
        source_key=source_key).first()
    if source is None:
        raise AiError("v4财报来源不存在", "not_found", 404)
    if (parent.status != "collecting" or parent.collection_status != "manual"
            or parent.scope_json != "null" or parent.owner_email != principal.email.lower()
            or parent.version != parent.page_count + 1
            or source.version != source.page_count + 1 or source.finished):
        _reject("v4财报来源不在可追加的精确状态")
    if (source.page_count >= evidence_v4.MAX_SOURCE_PAGES
            or parent.page_count >= evidence_v4.MAX_RUN_PAGES
            or source.stored_bytes >= evidence_v4.MAX_SOURCE_BYTES
            or parent.stored_bytes >= evidence_v4.MAX_RUN_BYTES):
        raise AiError("v4财报完整事实容量达到硬上限", "payload_too_large", 413)
    return actor, source, _plan(parent, source)


def _checkpoint(source, query):
    if source.page_count == 0:
        if (source.checkpoint_json != "{}" or source.source_ref
                or source.source_revision or source.row_count or source.stored_bytes
                or source.version != 1):
            _reject("v4财报来源初始检查点不干净")
        return None, None
    try:
        raw = source.checkpoint_json
        if type(raw) is not str or len(raw.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
            _reject("v4财报检查点超过固定容量")
        saved = json.loads(raw)
        if (type(saved) is not dict or set(saved) != {"schemaVersion", "sourceRef",
                "sourceRevision", "lastChunkDigest", "pageCount", "rowCount",
                "storedBytes", "finished", "financeState"}
                or raw != canonical(saved) or saved["schemaVersion"] != CHECKPOINT_SCHEMA
                or saved["sourceRef"] != source.source_ref
                or saved["sourceRevision"] != source.source_revision
                or saved["pageCount"] != source.page_count
                or saved["rowCount"] != source.row_count
                or saved["storedBytes"] != source.stored_bytes
                or saved["finished"] != source.finished or source.finished
                or saved["financeState"] != verifier._state(saved["financeState"], query)):
            _reject("v4财报检查点与来源计数不一致")
        state = saved["financeState"]
        if (state["pageCount"] != source.page_count
                or state["rowsRead"] != source.row_count
                or state["storedBytes"] != source.stored_bytes
                or state["sourceRef"] != source.source_ref
                or state["sourceRevision"] != source.source_revision
                or state["finished"] is not False):
            _reject("v4财报纯状态与来源计数不一致")
        last = m.AiBusinessV4Chunk.objects.filter(run_id=source.run_id,
            source_id=source.id, sequence=source.page_count).values(
                "payload_json", "payload_digest", "source_ref", "source_revision",
                "row_count").first()
        if (last is None or last["payload_digest"] != saved["lastChunkDigest"]
                or last["payload_digest"] != digest(last["payload_json"])
                or last["source_ref"] != source.source_ref
                or last["source_revision"] != source.source_revision):
            _reject("v4财报末块摘要与来源检查点不一致")
        page = json.loads(last["payload_json"])
        if (last["payload_json"] != canonical(page)
                or page["pageDigest"] != state["lastPageDigest"]
                or page["sourceRef"] != source.source_ref
                or page["sourceRevision"] != source.source_revision
                or page["pagination"]["nextOffset"] != state["nextOffset"]
                or page["pagination"]["nextLastId"] != state["lastId"]
                or page["pageEvidence"]["rowCount"] != last["row_count"]):
            _reject("v4财报续读末块与原真实行边界不一致")
        return state, last["payload_digest"]
    except (AnalysisContractError, KeyError, IndexError, TypeError, ValueError,
            AttributeError, RecursionError) as error:
        raise AiError("v4财报末块不是可恢复的真实来源页", "conflict", 409) from error


def prepare(run_id, source_key, expected_version, principal):
    identifier(run_id); identifier(source_key)
    integer(expected_version, "expectedVersion")
    parent = m.AiBusinessV4Run.objects.filter(pk=run_id).first()
    if parent is None:
        raise AiError("v4财报任务不存在", "not_found", 404)
    actor, source, query = _source(parent, source_key, principal)
    if parent.version != expected_version:
        raise AiError("v4财报父任务版本已变化", "version_conflict", 409)
    state, last_digest = _checkpoint(source, query)
    arguments = ({"query": query, "offset": 0, "afterId": 0} if state is None
        else verifier.next_arguments(state, trusted_query=query))
    return Prepared(parent.id, parent.version, source.id, source.version,
        source.source_key, source.checkpoint_json, source.query_json,
        canonical(actor), canonical(arguments), last_digest)


def _unchanged(prepared, principal):
    if canonical(actor_service._actor(principal)) != prepared.actor_json:
        raise AiError("v4财报读取期间账号权限变化", "access_denied", 403)
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
        raise AiError("v4财报读取期间来源或父任务CAS变化", "version_conflict", 409)


def _audit(principal, request_id, arguments, encoded, started_at):
    records = list(m.AiToolAuditLogs.objects.filter(request_id=request_id,
        actor_email=principal.email.lower(), actor_role="admin",
        surface="business_collection", tool_name=TOOL, status="succeeded",
        response_digest=digest(encoded), error_code__isnull=True,
        arguments_json=canonical({"argumentsDigest": digest(arguments)}),
        created_at__gte=started_at).values("id", "invocation_id")[:2])
    if len(records) != 1:
        _reject("v4财报页缺少唯一、当前调用的完整请求成功审计")
    return records[0]


def advance(run_id, source_key, expected_version, principal, request_id):
    """Fetch and persist one exact signed monthly page; never accept page input."""
    identifier(request_id, "requestId")
    if len(request_id) > 128:
        _reject("v4财报内部请求ID超过签名桥容量")
    prepared = prepare(run_id, source_key, expected_version, principal)
    entries = transport.catalog(principal, "business_collection")
    matches = [item for item in entries if item.get("name") == TOOL]
    if (len(matches) != 1 or matches[0].get("risk") != "read_only"
            or matches[0].get("allowedRoles") != ["admin"]
            or matches[0].get("scopePolicy") != "unscoped_only"
            or matches[0].get("execution", {}).get("mode") != "direct"
            or matches[0].get("execution", {}).get("allowedSurfaces") != ["business_collection"]):
        raise AiError("v4财报签名只读工具策略不可用", "access_denied", 403)
    started_at = timezone.now()
    arguments = prepared.arguments()
    with transport.request_budget(30):
        page = _result(transport.execute_tool(TOOL, arguments, principal,
            surface="business_collection", request_id=request_id,
            policy_digest=digest(entries)), TOOL)
    _unchanged(prepared, principal)
    source = m.AiBusinessV4Source.objects.get(pk=prepared.source_id)
    query = json.loads(prepared.source_query_json)
    state, last_digest = _checkpoint(source, query)
    if last_digest != prepared.expected_last_chunk_digest:
        raise AiError("v4财报末块在签名读取期间变化", "version_conflict", 409)
    try:
        next_state = verifier.consume(state, page, trusted_query=query)
        encoded = canonical(passive(page, verifier.PAGE_BYTES))
        size = len(encoded.encode("utf-8"))
        saved = {"schemaVersion": CHECKPOINT_SCHEMA,
            "sourceRef": next_state["sourceRef"],
            "sourceRevision": next_state["sourceRevision"],
            "lastChunkDigest": digest(encoded),
            "pageCount": next_state["pageCount"],
            "rowCount": next_state["rowsRead"],
            "storedBytes": next_state["storedBytes"],
            "finished": next_state["finished"],
            "financeState": next_state}
        saved_json = canonical(passive(saved, MAX_CHECKPOINT_BYTES))
        if (next_state["pageCount"] != source.page_count + 1
                or next_state["rowsRead"] != source.row_count + len(page["rows"])
                or next_state["storedBytes"] != source.stored_bytes + size):
            raise AnalysisContractError("v4财报页与父子计数不一致")
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            AttributeError, RecursionError) as error:
        raise AiError("v4财报签名页未通过自然月、批次或行链校验", "conflict", 409) from error
    audit = _audit(principal, request_id, arguments, encoded, started_at)
    with mutation(principal):
        if canonical(actor_service._actor(principal)) != prepared.actor_json:
            raise AiError("v4财报落地前账号权限变化", "access_denied", 403)
        parent = authorize_owner(m.AiBusinessV4Run.objects.select_for_update().get(
            pk=prepared.run_id), principal)
        record = m.AiBusinessV4Source.objects.select_for_update().get(
            pk=prepared.source_id, run_id=parent.id)
        if (parent.version != prepared.run_version or parent.status != "collecting"
                or parent.collection_status != "manual"
                or record.version != prepared.source_version or record.finished
                or record.checkpoint_json != prepared.source_checkpoint_json
                or record.query_json != prepared.source_query_json
                or record.page_count != source.page_count
                or record.row_count != source.row_count
                or record.stored_bytes != source.stored_bytes):
            raise AiError("v4财报父任务或自然月来源CAS变化", "version_conflict", 409)
        if (record.page_count >= evidence_v4.MAX_SOURCE_PAGES
                or parent.page_count >= evidence_v4.MAX_RUN_PAGES
                or record.stored_bytes + size > evidence_v4.MAX_SOURCE_BYTES
                or parent.stored_bytes + size > evidence_v4.MAX_RUN_BYTES):
            raise AiError("v4财报完整事实容量达到硬上限", "payload_too_large", 413)
        chunk = m.AiBusinessV4Chunk.objects.create(id=uid("v4-chunk"), run=parent,
            source=record, sequence=record.page_count + 1,
            payload_json=encoded, payload_digest=digest(encoded),
            source_ref=page["sourceRef"], source_revision=page["sourceRevision"],
            row_count=len(page["rows"]))
        m.AiBusinessV4ToolReceipt.objects.create(chunk=chunk, audit_id=audit["id"],
            run=parent, source=record, sequence=chunk.sequence,
            actor_email=principal.email.lower(), request_id=request_id,
            invocation_id=audit["invocation_id"], tool_name=TOOL,
            surface="business_collection", response_digest=chunk.payload_digest,
            payload_bytes=size)
        record.source_ref = page["sourceRef"]
        record.source_revision = page["sourceRevision"]
        record.checkpoint_json = saved_json
        record.page_count += 1
        record.row_count = next_state["rowsRead"]
        record.stored_bytes += size
        record.finished = next_state["finished"]
        record.version += 1
        record.updated_at = timezone.now()
        record.save(update_fields=["source_ref", "source_revision", "checkpoint_json",
            "page_count", "row_count", "stored_bytes", "finished", "version",
            "updated_at"])
        parent.page_count += 1
        parent.row_count += len(page["rows"])
        parent.stored_bytes += size
        parent.version += 1
        parent.save(update_fields=["page_count", "row_count", "stored_bytes", "version"])
        if canonical(actor_service._actor(principal)) != prepared.actor_json:
            raise AiError("v4财报落地后账号权限变化", "access_denied", 403)
    return {"runId": parent.id, "runVersion": parent.version,
        "sourceKey": record.source_key, "sourceVersion": record.version,
        "pageCount": record.page_count, "rowCount": record.row_count,
        "storedBytes": record.stored_bytes, "finished": record.finished,
        "sourceRef": record.source_ref, "sourceRevision": record.source_revision,
        "sourceAuthorityVerified": False, "persistentEvidenceVerified": False,
        "reportGenerationSupported": False}
