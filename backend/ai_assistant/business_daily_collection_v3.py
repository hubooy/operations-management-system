"""Owner-bound daily source replay and explicit signed one-page advance.

A completed daily source remains one member of a collecting mixed parent.
No public route, scheduler, parent seal, model or report path is registered.
"""
from __future__ import annotations

import json
from django.utils import timezone

from business_analysis.contracts import AnalysisContractError, PageReconciler
from . import business_daily_continuation_v3 as continuation, business_v3_catalog as catalog
from . import business_evidence_store as store, business_v3_tool_receipts as receipts, models as m, transport
from .business_sealed import Reader
from .datasets import _result
from .policy import AiError, authorize_owner, canonical, cas, digest, identifier, integer, mutation, passive, uid

DAILY = frozenset(("sales", "netshop", "market"))
PAGE_LIMIT = 100
PAGE_BYTES = 131_072
MAX_LIVE_PAGES = 64
INITIAL_TOOL = "get_business_source_page"


class _Identity(Reader):
    """Reuse v2 owning page identity rules without v2-only constructor/seal."""
    def __init__(self):
        self._limit = PAGE_LIMIT


def _reject(message="v3日来源完整页链与真实检查点不一致"):
    raise AiError(message, "conflict", 409)


def inspect(run_id, source_key, principal, *, allow_sealed=False):
    row, built, records, actor = catalog.load(run_id, principal, allow_sealed=allow_sealed)
    key = identifier(source_key)
    selected = next((item for item in records if item["source_key"] == key), None)
    if selected is None or selected["domain"] not in DAILY:
        raise AiError("v3日来源不存在", "not_found", 404)
    query = next(item["query"] for item in built["entries"] if item["key"] == key)
    chunks = m.AiBusinessEvidenceChunk.objects.filter(run_id=row.id, source_key=key).order_by("sequence")
    verifier, size, count, metadata = PageReconciler(), 0, 0, None
    try:
        checkpoint = json.loads(selected["checkpoint_json"])
        if selected["page_count"] == 0:
            if checkpoint != {}:
                raise AnalysisContractError("零页检查点不可声称已开始")
        else:
            if (type(checkpoint) is not dict or set(checkpoint) != {"pageCount", "verifier", "metadata"}
                    or type(checkpoint["metadata"]) is not dict or type(checkpoint["verifier"]) is not dict):
                raise AnalysisContractError("日来源检查点字段无效")
            metadata = checkpoint["metadata"]
        identity = _Identity()
        for chunk in chunks.iterator(chunk_size=10):
            count += 1
            raw = chunk.payload_json
            if (chunk.sequence != count or count > 1999 or type(raw) is not str
                    or len(raw.encode("utf-8")) > PAGE_BYTES or digest(raw) != chunk.payload_digest):
                raise AnalysisContractError("日来源事实块顺序或摘要变化")
            page = json.loads(raw)
            if raw != canonical(page):
                raise AnalysisContractError("日来源事实块非规范JSON")
            # First-page coverage and semantics are saved in the immutable
            # checkpoint, then checked against that page and every later page.
            if metadata is None:
                raise AnalysisContractError("已落地日页缺失来源元数据")
            identity._identity({"domain": selected["domain"], "query": query}, page, metadata, count == 1)
            verifier.consume(page, request_cursor=verifier.expected_cursor)
            size += len(raw.encode("utf-8"))
        if count != selected["page_count"] or size != selected["stored_bytes"]:
            raise AnalysisContractError("日来源事实数量或字节不符")
        if count:
            if (checkpoint["pageCount"] != count or canonical(checkpoint["verifier"]) != canonical(verifier.__dict__)
                    or selected["row_count"] != verifier.rows or selected["finished"] != verifier.finished):
                raise AnalysisContractError("日来源检查点不能从实际块重建")
            if verifier.finished:
                verifier.result()
    except (AnalysisContractError, KeyError, TypeError, ValueError, AttributeError, RecursionError) as error:
        raise AiError("v3日来源实际事实未通过完整核验", "conflict", 409) from error
    catalog.unchanged(row, actor, records, principal)
    next_args = (None if verifier.finished else {"domain": selected["domain"], **query, "limit": PAGE_LIMIT,
                 **({"cursor": verifier.expected_cursor} if verifier.expected_cursor else {})})
    return {"runId": row.id, "runVersion": row.version, "sourceId": selected["id"],
            "sourceKey": key, "sourceVersion": selected["version"],
            "checkpointDigest": digest(selected["checkpoint_json"]),
            "pageCount": count, "rowCount": verifier.rows, "storedBytes": size,
            "finished": verifier.finished, "nextArguments": next_args,
            "reconciliation": verifier.result() if verifier.finished else None,
            "persistentEvidenceVerified": False, "reportGenerationSupported": False}


def advance_daily_source(run_id, source_key, expected_version, principal, request_id):
    """Read exactly one signed owning page, then append under parent/source CAS."""
    identifier(run_id)
    identifier(source_key)
    identifier(request_id, "requestId")
    if len(request_id) > 128:
        raise AiError("v3日来源内部请求标识超过签名桥容量")
    integer(expected_version, "expectedVersion")
    actor = catalog.plan._actor(principal)
    # Cheap refusal before replaying up to the old 1,999-page physical ledger.
    early = m.AiBusinessEvidenceSource.objects.filter(run_id=run_id, source_key=source_key,
        domain__in=DAILY).values("page_count").first()
    if early is not None and early["page_count"] >= MAX_LIVE_PAGES:
        raise AiError("当前日来源全链验证页数达到受控上限", "payload_too_large", 413)
    prepared = inspect(run_id, source_key, principal)
    if prepared["runVersion"] != expected_version:
        raise AiError("v3父任务版本已变化", "version_conflict", 409)
    if prepared["finished"]:
        _reject("已完成日来源不可再次采集")
    if prepared["pageCount"] >= MAX_LIVE_PAGES:
        raise AiError("当前日来源全链验证页数达到受控上限", "payload_too_large", 413)
    row, built, records, _ = catalog.load(run_id, principal)
    source = m.AiBusinessEvidenceSource.objects.filter(pk=prepared["sourceId"], run_id=run_id).first()
    descriptor = next((item for item in built["entries"] if item["key"] == source_key), None)
    if (source is None or descriptor is None or descriptor["domain"] not in DAILY
            or source.version != prepared["sourceVersion"]
            or digest(source.checkpoint_json) != prepared["checkpointDigest"]
            or source.query_json != canonical(descriptor["query"])):
        raise AiError("v3日来源领取时身份或检查点变化", "version_conflict", 409)
    continued = continuation.prepare(row, source, descriptor["query"], prepared, principal) if prepared["pageCount"] else None
    tool = continued.tool if continued else INITIAL_TOOL
    arguments = continued.arguments() if continued else prepared["nextArguments"]
    if type(arguments) is not dict:
        _reject("v3日来源无下一页参数")
    entries = transport.catalog(principal, "business_collection")
    matches = [item for item in entries if item.get("name") == tool]
    if (len(matches) != 1 or matches[0].get("risk") != "read_only"
            or matches[0].get("allowedRoles") != ["admin"]
            or matches[0].get("scopePolicy") != "unscoped_only"
            or matches[0].get("execution", {}).get("mode") != "direct"
            or matches[0].get("execution", {}).get("allowedSurfaces") != ["business_collection"]):
        raise AiError("v3日来源只读工具策略不可用", "access_denied", 403)
    with transport.request_budget(30):
        page = _result(transport.execute_tool(tool, arguments, principal,
            surface="business_collection", request_id=request_id, policy_digest=digest(entries)), tool)
    if continued:
        continuation.check(continued, principal)
        if (page.get("sourceRef") != arguments["expectedSourceRef"]
                or page.get("sourceRevision") != arguments["expectedRevision"]):
            _reject("v3续读页与原来源版本不符")
    try:
        checkpoint = None if source.checkpoint_json == "{}" else json.loads(source.checkpoint_json)
        state = PageReconciler()
        if checkpoint is not None:
            if type(checkpoint) is not dict or set(checkpoint) != {"pageCount", "verifier", "metadata"}:
                raise AnalysisContractError("检查点形状无效")
            state.__dict__.update(checkpoint["verifier"])
        first = checkpoint is None
        metadata = (checkpoint["metadata"].copy() if checkpoint is not None else {
            "sourceRevision": page["sourceRevision"],
            "coverage": page.get("coverage"), "excludedOverlappingPeriodRows": page.get("excludedOverlappingPeriodRows"),
            "identityCheck": page.get("identityCheck"), "availableDates": page.get("availableDates"),
            "metricSemantics": page.get("metricSemantics"), "freshness": None,
            "firstCollectedAt": timezone.now().isoformat()})
        _Identity()._identity({"domain": descriptor["domain"], "query": descriptor["query"]}, page, metadata, first)
        state.consume(page, request_cursor=state.expected_cursor)
        if state.finished:
            state.result()
        metadata["lastCollectedAt"] = timezone.now().isoformat()
        new_checkpoint = {"pageCount": source.page_count + 1, "verifier": state.__dict__, "metadata": metadata}
        saved = canonical(passive(new_checkpoint, 32768))
        encoded = canonical(passive(page, PAGE_BYTES))
    except (AnalysisContractError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise AiError("v3日来源签名页未通过精确身份、控制总量或页链核验", "conflict", 409) from error
    size = len(encoded.encode("utf-8"))
    audit = receipts.audit_for_page(principal, request_id=request_id, tool_name=tool, encoded=encoded)
    with mutation(principal):
        if catalog.plan._actor(principal) != actor:
            raise AiError("v3日来源采集期间账号权限变化", "access_denied", 403)
        live = authorize_owner(m.AiBusinessEvidenceRun.objects.select_for_update().get(pk=run_id), principal)
        cas(live, expected_version)
        if live.status != "collecting" or live.collection_status != "manual" or live.state_json != "{}":
            _reject("v3父任务不可追加日来源")
        record = m.AiBusinessEvidenceSource.objects.select_for_update().get(pk=prepared["sourceId"], run_id=run_id)
        if (record.domain != descriptor["domain"] or record.source_key != source_key or record.finished
                or record.version != prepared["sourceVersion"]
                or digest(record.checkpoint_json) != prepared["checkpointDigest"]
                or record.query_json != canonical(descriptor["query"])):
            raise AiError("v3日来源CAS或精确查询变化", "version_conflict", 409)
        if continued:
            continuation.check(continued, principal)
        if (store.progress(live)["pageCount"] >= catalog.MAX_DATA_PAGES
                or live.stored_bytes + size > catalog.MAX_DATA_BYTES):
            raise AiError("v3共享事实容量已满，原检查点保留", "payload_too_large", 413)
        store.check_quota(principal, size + len(saved.encode("utf-8")) - len(record.checkpoint_json.encode("utf-8")),
                          64 * 1024 * 1024)
        chunk = m.AiBusinessEvidenceChunk.objects.create(id=uid("evidence-chunk"), run=live,
            source_key=source_key, sequence=record.page_count + 1,
            payload_json=encoded, payload_digest=digest(encoded))
        receipts.bind_page(parent=live, source=record, chunk=chunk, principal=principal,
            request_id=request_id, tool_name=tool, page=page, encoded=encoded, audit=audit)
        record.page_count += 1
        record.stored_bytes += size
        record.row_count = state.rows
        record.finished = state.finished
        record.checkpoint_json = saved
        record.checkpoint_run_version = live.version + 1
        record.version += 1
        record.updated_at = timezone.now()
        record.save(update_fields=["page_count", "stored_bytes", "row_count", "finished", "checkpoint_json",
                                   "checkpoint_run_version", "version", "updated_at"])
        live.stored_bytes += size
        live.version += 1
        live.save(update_fields=["stored_bytes", "version"])
        if catalog.plan._actor(principal) != actor:
            raise AiError("v3日来源落地期间账号权限变化", "access_denied", 403)
    return inspect(run_id, source_key, principal)
