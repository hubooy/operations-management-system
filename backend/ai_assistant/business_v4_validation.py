"""Resumable, internal v4 candidate validation; parent sealing is disabled.

Each immutable segment replays exactly sixteen or fewer real chunks and signed
tool receipts. A purpose-separated HMAC authenticates the persisted progress
across processes. Finance's physical write/revision fence is not yet proven,
so this module never claims source authority or changes parent status.
"""
from __future__ import annotations

import hashlib
import hmac
from itertools import zip_longest
import json
import re
import time

from django.conf import settings
from django.utils import timezone

from business_analysis import (business_promotion_v4_plan, evidence_v4,
    finance_collection_state_v4 as finance_state, promotion_views)
from business_analysis.contracts import (AnalysisContractError, PageReconciler,
    comparison_periods, coverage)

from . import (business_daily_collection_v3 as daily_identity,
    business_evidence_v3 as actor_service,
    business_v4_finance_collection as finance_collector,
    business_v4_netshop_promotion as promotion_collector,
    business_v4_promotion_replay as promotion_replay, models as m)
from .policy import AiError, authorize_owner, canonical, digest, identifier, integer, mutation, uid

ATTEMPT_SCHEMA = "business-v4-validation-attempt-candidate-v1"
PROGRESS_SCHEMA = "business-v4-validation-progress-candidate-v1"
PURPOSE = b"teruisi:business-v4:resumable-validation:v1\x00"
SEGMENT_PAGES = 16
MAX_ATTEMPTS = 4
MAX_SOURCE_SEGMENTS = 1024
MAX_RUN_SEGMENTS = 4096
MAX_PROGRESS_BYTES = 32_768
MAX_SEGMENT_SECONDS = 120
ZERO = "0" * 64


def _reject(message="v4候选分段证明与真实来源不一致", code="conflict", status=409):
    raise AiError(message, code, status)


def _key():
    secret = getattr(settings, "DJANGO_INTERNAL_SECRET", None)
    if type(secret) is not str or len(secret) < 32:
        _reject("内部候选证明签名密钥不可用", "service_unavailable", 503)
    key = hmac.new(secret.encode("utf-8"), PURPOSE, hashlib.sha256).digest()
    return key, hashlib.sha256(key).hexdigest()[:16]


def _mac(key, value):
    return hmac.new(key, canonical(value).encode("utf-8"), hashlib.sha256).hexdigest()


def _directory(run_id, principal):
    actor = actor_service._actor(principal)
    parent = m.AiBusinessV4Run.objects.filter(pk=identifier(run_id)).first()
    if parent is None:
        raise AiError("v4候选任务不存在", "not_found", 404)
    authorize_owner(parent, principal)
    try:
        if (parent.status != "collecting" or parent.collection_status != "manual"
                or parent.scope_json != "null" or parent.owner_email != actor["email"]
                or parent.version != parent.page_count + 1
                or type(parent.plan_json) is not str
                or len(parent.plan_json.encode("utf-8")) > 131_072):
            _reject("v4父任务不在候选分段验证状态")
        plan = json.loads(parent.plan_json)
        business_promotion_v4_plan._plan(plan)
        if (parent.plan_json != canonical(plan)
                or parent.plan_digest != digest(parent.plan_json)
                or parent.run_identity_digest != plan["runIdentityDigest"]
                or plan["runCapacitySupported"] is not True):
            _reject("v4父任务容量计划摘要变化")
        sources = list(m.AiBusinessV4Source.objects.filter(run_id=parent.id)
            .order_by("ordinal")[:5])
        if not 2 <= len(sources) <= 4 or len(sources) != plan["sourceCount"]:
            _reject("v4候选封存目录只能有推广和一个财报来源")
        finance = [source for source in sources if source.domain == "finance"]
        promotion = [source for source in sources if source.domain == "netshop"]
        if len(finance) != 1 or not 1 <= len(promotion) <= 3:
            _reject("v4候选目录缺少精确推广或唯一财报上下文")
        finance_collector._plan(parent, finance[0])
        current = next((source for source in promotion if json.loads(
            source.query_json).get("window") == "current"), None)
        if current is None:
            _reject("v4候选目录缺少京东推广本期")
        current_query = json.loads(current.query_json)
        selected = promotion_collector.selection(plan, current_query, current.source_key)
        selected_windows = {window for window, item in
            selected["selectedWindows"].items()
            if item["status"] == "selected_in_capacity_plan"}
        if ({json.loads(source.query_json)["window"] for source in promotion} != selected_windows
                or {item["sourceKey"] for item in selected["selectedWindows"].values()
                    if item["status"] == "selected_in_capacity_plan"} !=
                    {source.source_key for source in promotion}):
            _reject("v4候选目录的同店比较窗口不完整或重复")
        for source in sources:
            entry = plan["sourcePlans"][source.ordinal - 1]
            query = json.loads(source.query_json)
            if (not source.finished or source.version != source.page_count + 1
                    or not 1 <= source.page_count <= evidence_v4.MAX_SOURCE_PAGES
                    or not 0 < source.stored_bytes <= evidence_v4.MAX_SOURCE_BYTES
                    or source.query_json != canonical(query)
                    or source.query_digest != digest(source.query_json)
                    or entry["sourceKey"] != source.source_key
                    or entry["queryDigest"] != source.query_digest
                    or entry["sourceIdentityDigest"] != source.source_identity_digest
                    or source.temporal_role != ("monthly_context" if source.domain == "finance"
                        else "daily_fact")
                    or (source.domain == "netshop" and
                        (query.get("platform") != "京东" or query.get("dataset") != "promotion"))):
                _reject("v4候选目录仍有未完成或不受支持来源")
        if (sum(item.page_count for item in sources) != parent.page_count
                or sum(item.row_count for item in sources) != parent.row_count
                or sum(item.stored_bytes for item in sources) != parent.stored_bytes):
            _reject("v4候选父子页行字节不守恒")
        snapshot = [{"sourceId": source.id, "sourceKey": source.source_key,
            "ordinal": source.ordinal, "domain": source.domain,
            "queryDigest": source.query_digest, "sourceVersion": source.version,
            "sourceRef": source.source_ref, "sourceRevision": source.source_revision,
            "pageCount": source.page_count, "rowCount": source.row_count,
            "storedBytes": source.stored_bytes,
            "checkpointDigest": digest(source.checkpoint_json)} for source in sources]
        return actor, parent, sources, digest(snapshot)
    except (AnalysisContractError, KeyError, StopIteration, TypeError,
            ValueError, IndexError, AttributeError, RecursionError) as error:
        raise AiError("v4候选封存目录或来源身份无法重建", "conflict", 409) from error


def start_attempt(run_id, expected_version, principal):
    """Create or return one immutable candidate attempt after all sources finish."""
    integer(expected_version, "expectedVersion")
    actor, parent, sources, directory_digest = _directory(run_id, principal)
    if parent.version != expected_version:
        raise AiError("v4候选父版本变化", "version_conflict", 409)
    key, key_id = _key()
    with mutation(principal):
        if actor_service._actor(principal) != actor:
            _reject("候选验证前账号权限变化", "access_denied", 403)
        live = m.AiBusinessV4Run.objects.select_for_update().get(pk=parent.id)
        _, checked, _, current_digest = _directory(live.id, principal)
        if (checked.version != expected_version or current_digest != directory_digest):
            raise AiError("候选验证前来源目录CAS变化", "version_conflict", 409)
        existing = list(m.AiBusinessV4ValidationAttempt.objects.filter(run=live)
            .order_by("created_at", "id")[:MAX_ATTEMPTS + 1])
        if existing:
            recent = existing[-1]
            if (recent.run_version == live.version
                    and recent.directory_digest == directory_digest
                    and recent.actor_email == actor["email"]
                    and recent.actor_version == actor["version"]
                    and recent.key_id == key_id):
                return {"attemptId": recent.id, "runId": live.id,
                    "sourceCount": len(sources), "sourceAuthorityVerified": False,
                    "financeRevisionWriteFenceVerified": False, "sealed": False}
        if len(existing) >= MAX_ATTEMPTS:
            raise AiError("v4候选重试次数达到不可变账本上限", "payload_too_large", 413)
        attempt = m.AiBusinessV4ValidationAttempt.objects.create(
            id=uid("v4-attempt"), run=live, run_version=live.version,
            plan_digest=live.plan_digest, directory_digest=directory_digest,
            actor_email=actor["email"], actor_version=actor["version"],
            key_id=key_id)
    return {"attemptId": attempt.id, "runId": parent.id,
        "sourceCount": len(sources), "sourceAuthorityVerified": False,
        "financeRevisionWriteFenceVerified": False, "sealed": False}


def _segment_payload(segment):
    return {"schemaVersion": ATTEMPT_SCHEMA,
        "attemptId": segment.attempt_id, "runId": segment.run_id,
        "sourceId": segment.source_id, "segmentIndex": segment.segment_index,
        "startSequence": segment.start_sequence, "endSequence": segment.end_sequence,
        "sourceVersion": segment.source_version,
        "sourceRef": segment.source_ref,
        "sourceRevision": segment.source_revision,
        "previousSegmentDigest": segment.previous_segment_digest,
        "progressDigest": segment.progress_digest,
        "keyId": segment.attempt.key_id}


def _verified_segment(segment, key, key_id):
    try:
        if (type(segment.progress_json) is not str
                or len(segment.progress_json.encode("utf-8")) > MAX_PROGRESS_BYTES):
            _reject("v4候选上一分段状态容量无效")
        progress = json.loads(segment.progress_json)
        if (segment.attempt.key_id != key_id
                or segment.progress_digest != digest(segment.progress_json)
                or segment.progress_json != canonical(progress)
                or segment.proof_digest != digest(_segment_payload(segment))
                or not hmac.compare_digest(segment.proof_mac,
                    _mac(key, _segment_payload(segment)))
                or type(progress) is not dict
                or progress.get("schemaVersion") != PROGRESS_SCHEMA
                or progress.get("pageCount") != segment.end_sequence
                or progress.get("sourceRef") != segment.source_ref
                or progress.get("sourceRevision") != segment.source_revision):
            _reject("v4候选上一分段HMAC或末状态不一致")
        return progress
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise AiError("v4候选上一分段不是规范可恢复状态", "conflict", 409) from error


def _initial_progress(source):
    return {"schemaVersion": PROGRESS_SCHEMA, "sourceKey": source.source_key,
        "domain": source.domain, "sourceRef": source.source_ref,
        "sourceRevision": source.source_revision, "pageCount": 0,
        "rowCount": 0, "storedBytes": 0, "lastChunkDigest": None,
        "receiptChainDigest": digest([]),
        "domainState": (None if source.domain == "finance" else {
            "verifier": PageReconciler().__dict__, "metadata": None,
            "observedDates": []})}


def _scan_page(progress, source, query, chunk, receipt, sequence):
    if (chunk.sequence != sequence or receipt.sequence != sequence
            or chunk.run_id != source.run_id or chunk.source_id != source.id
            or receipt.run_id != source.run_id or receipt.source_id != source.id
            or receipt.chunk_id != chunk.id
            or receipt.actor_email != source.run.owner_email
            or receipt.surface != "business_collection"
            or receipt.audit_id is None
            or receipt.audit.actor_email != source.run.owner_email
            or receipt.audit.actor_role != "admin"
            or receipt.audit.surface != "business_collection"
            or receipt.audit.tool_name != receipt.tool_name
            or receipt.audit.request_id != receipt.request_id
            or receipt.audit.invocation_id != receipt.invocation_id
            or receipt.audit.status != "succeeded"
            or receipt.audit.error_code is not None
            or receipt.audit.created_at > chunk.created_at
            or chunk.created_at > receipt.created_at):
        _reject("v4候选分段缺页或签名工具收据不完整")
    raw = chunk.payload_json
    size = len(raw.encode("utf-8"))
    limit = finance_state.PAGE_BYTES if source.domain == "finance" else promotion_collector.MAX_PAGE_BYTES
    if (size > limit or chunk.payload_digest != digest(raw)
            or chunk.source_ref != source.source_ref
            or chunk.source_revision != source.source_revision
            or receipt.response_digest != chunk.payload_digest
            or receipt.audit.response_digest != chunk.payload_digest
            or receipt.payload_bytes != size):
        _reject("v4候选页原字节与不可变审计不一致")
    page = json.loads(raw)
    if raw != canonical(page):
        _reject("v4候选事实块不是规范JSON")
    if source.domain == "finance":
        state = progress["domainState"]
        expected = ({"query": query, "offset": 0, "afterId": 0} if state is None
            else finance_state.next_arguments(state, trusted_query=query))
        if receipt.tool_name != finance_collector.TOOL:
            _reject("v4财报候选工具名称不一致")
        state = finance_state.consume(state, page, trusted_query=query)
        count = len(page["rows"])
        progress["domainState"] = state
    else:
        state = progress["domainState"]
        old = PageReconciler()
        old.__dict__.update(state["verifier"])
        cursor = old.expected_cursor
        expected = ({"domain": "netshop", **query, "limit": 100} if sequence == 1
            else {**query, "limit": 100, "cursor": cursor,
                "expectedSourceRef": source.source_ref,
                "expectedRevision": source.source_revision,
                "expectedLastId": old.last_id})
        if receipt.tool_name != (promotion_collector.FIRST_TOOL if sequence == 1
                else promotion_collector.CONTINUATION_TOOL):
            _reject("v4推广候选工具名称不一致")
        promotion_replay._promotion_page(page, first=sequence == 1)
        metadata = state["metadata"]
        if metadata is None:
            # The source checkpoint is the claimed first-page metadata. The
            # owning identity reader must compare every original field back
            # to page 1; deriving metadata from that page would be tautological.
            metadata = json.loads(source.checkpoint_json)["metadata"]
        daily_identity._Identity()._identity({"domain": "netshop", "query": query},
            page, metadata, sequence == 1)
        old.consume(page, request_cursor=cursor)
        observed = set(state["observedDates"])
        observed.update(item["date"] for item in page["items"])
        state.update(verifier=old.__dict__, metadata=metadata,
            observedDates=sorted(observed))
        count = len(page["items"])
        progress["domainState"] = state
    if (receipt.audit.arguments_json != canonical({"argumentsDigest": digest(expected)})
            or chunk.row_count != count):
        _reject("v4候选工具请求参数或事实行数不一致")
    progress["pageCount"] += 1
    progress["rowCount"] += count
    progress["storedBytes"] += size
    progress["lastChunkDigest"] = chunk.payload_digest
    progress["receiptChainDigest"] = digest([progress["receiptChainDigest"], sequence,
        chunk.payload_digest, receipt.audit_id, receipt.invocation_id,
        chunk.source_ref, chunk.source_revision])


def _complete(progress, source, query):
    raw = source.checkpoint_json
    if type(raw) is not str or len(raw.encode("utf-8")) > 32_768:
        _reject("v4候选来源完成检查点容量无效")
    saved = json.loads(raw)
    if (progress["pageCount"] != source.page_count
            or progress["rowCount"] != source.row_count
            or progress["storedBytes"] != source.stored_bytes
            or progress["lastChunkDigest"] != saved["lastChunkDigest"]
            or saved["finished"] is not True):
        _reject("v4候选最终状态与来源完成检查点不一致")
    if source.domain == "finance":
        state = progress["domainState"]
        if (state["finished"] is not True
                or canonical(state) != canonical(saved["financeState"])):
            _reject("v4财报候选真实月度状态与来源检查点不一致")
        finance_state.result(state, trusted_query=query)
    else:
        state = progress["domainState"]
        verifier = PageReconciler()
        verifier.__dict__.update(state["verifier"])
        result = verifier.result()
        period = comparison_periods(query["startDate"], query["endDate"])[query["window"]]
        if (set(result["metrics"]) != (promotion_views.METRICS if progress["rowCount"]
                else promotion_views.BASE_METRICS)
                or canonical(verifier.__dict__) != canonical(saved["verifier"])
                or saved["metadata"].get("coverage") != coverage(period,
                    set(state["observedDates"]))
                or canonical(state["metadata"]) != canonical(saved["metadata"])):
            _reject("v4推广候选控制总额或真实缺日不一致")


def advance_segment(attempt_id, source_key, expected_index, principal):
    """Replay one fixed 16-page segment and append only a candidate witness."""
    identifier(attempt_id); identifier(source_key)
    integer(expected_index, "expectedIndex")
    if not 1 <= expected_index <= MAX_SOURCE_SEGMENTS:
        _reject("v4候选分段数量超过固定上限", "payload_too_large", 413)
    key, key_id = _key()
    attempt = m.AiBusinessV4ValidationAttempt.objects.filter(pk=attempt_id).first()
    if attempt is None:
        raise AiError("v4候选验证尝试不存在", "not_found", 404)
    actor, parent, sources, directory_digest = _directory(attempt.run_id, principal)
    source = next((item for item in sources if item.source_key == source_key), None)
    if source is None:
        raise AiError("v4候选来源不存在", "not_found", 404)
    latest_attempt = m.AiBusinessV4ValidationAttempt.objects.filter(run=parent).order_by(
        "-created_at", "-id").first()
    if (latest_attempt is None or latest_attempt.id != attempt.id
            or attempt.run_version != parent.version
            or attempt.plan_digest != parent.plan_digest
            or attempt.directory_digest != directory_digest
            or attempt.actor_email != actor["email"]
            or attempt.actor_version != actor["version"]
            or attempt.key_id != key_id):
        _reject("v4候选尝试的来源、账号或签名密钥已变化")
    query = json.loads(source.query_json)
    previous = m.AiBusinessV4ValidationSegment.objects.filter(attempt=attempt,
        source=source, segment_index=expected_index - 1).select_related("attempt").first()
    if expected_index == 1:
        if previous is not None:
            _reject("v4候选首段不能继承历史进度")
        progress, previous_digest = _initial_progress(source), ZERO
    else:
        if previous is None:
            _reject("v4候选续段缺少上一真实证明")
        progress = _verified_segment(previous, key, key_id)
        previous_digest = previous.proof_digest
    if (progress["pageCount"] != (expected_index - 1) * SEGMENT_PAGES
            or progress["sourceKey"] != source.source_key
            or progress["domain"] != source.domain
            or progress["sourceRef"] != source.source_ref
            or progress["sourceRevision"] != source.source_revision):
        _reject("v4候选续段未承接精确前段")
    start = progress["pageCount"] + 1
    end = min(start + SEGMENT_PAGES - 1, source.page_count)
    if start > end:
        _reject("v4候选来源已完整分段")
    deadline = time.monotonic() + MAX_SEGMENT_SECONDS
    chunks = m.AiBusinessV4Chunk.objects.filter(run=parent, source=source,
        sequence__gte=start, sequence__lte=end).order_by("sequence").iterator(chunk_size=16)
    receipts = m.AiBusinessV4ToolReceipt.objects.filter(run=parent, source=source,
        sequence__gte=start, sequence__lte=end).select_related("audit").order_by(
            "sequence").iterator(chunk_size=16)
    marker = object()
    try:
        for offset, (chunk, receipt) in enumerate(zip_longest(chunks, receipts,
                fillvalue=marker)):
            if chunk is marker or receipt is marker or offset >= SEGMENT_PAGES:
                _reject("v4候选分段有缺页、额外页或缺收据")
            if time.monotonic() > deadline:
                _reject("v4候选单段重放超过固定时间", "timeout", 408)
            _scan_page(progress, source, query, chunk, receipt, start + offset)
        if progress["pageCount"] != end:
            _reject("v4候选分段没有覆盖真实完整范围")
        if end == source.page_count:
            _complete(progress, source, query)
        encoded = canonical(progress)
        if len(encoded.encode("utf-8")) > MAX_PROGRESS_BYTES:
            _reject("v4候选分段状态超过固定持久容量", "payload_too_large", 413)
    except (AnalysisContractError, KeyError, IndexError, TypeError, ValueError,
            AttributeError, UnicodeError, RecursionError) as error:
        raise AiError("v4候选完整页链或月度状态重放失败", "conflict", 409) from error
    actor_after, parent_after, source_after, digest_after = _directory(parent.id, principal)
    if (actor_after != actor or parent_after.version != parent.version
            or digest_after != directory_digest
            or next(item for item in source_after if item.id == source.id).version != source.version):
        raise AiError("v4候选分段重放期间账号或来源版本变化", "version_conflict", 409)
    payload = {"schemaVersion": ATTEMPT_SCHEMA, "attemptId": attempt.id,
        "runId": parent.id, "sourceId": source.id,
        "segmentIndex": expected_index, "startSequence": start,
        "endSequence": end, "sourceVersion": source.version,
        "sourceRef": source.source_ref,
        "sourceRevision": source.source_revision,
        "previousSegmentDigest": previous_digest,
        "progressDigest": digest(encoded), "keyId": key_id}
    with mutation(principal):
        if actor_service._actor(principal) != actor:
            _reject("v4候选分段入库前账号权限变化", "access_denied", 403)
        locked = authorize_owner(m.AiBusinessV4Run.objects.select_for_update().get(
            pk=parent.id), principal)
        selected = m.AiBusinessV4Source.objects.select_for_update().get(
            pk=source.id, run=locked)
        if (locked.status != "collecting" or locked.version != parent.version
                or selected.version != source.version
                or selected.checkpoint_json != source.checkpoint_json
                or m.AiBusinessV4ValidationSegment.objects.filter(attempt=attempt,
                    source=selected, segment_index=expected_index).exists()):
            raise AiError("v4候选分段入库CAS或序号变化", "version_conflict", 409)
        current_previous = m.AiBusinessV4ValidationSegment.objects.filter(
            attempt=attempt, source=selected).order_by("-segment_index").first()
        if (current_previous is None and expected_index != 1
                or current_previous is not None and
                (current_previous.segment_index != expected_index - 1
                 or current_previous.proof_digest != previous_digest)):
            raise AiError("v4候选分段有并发续接", "version_conflict", 409)
        record = m.AiBusinessV4ValidationSegment.objects.create(
            id=uid("v4-segment"), attempt=attempt, run=locked, source=selected,
            segment_index=expected_index, start_sequence=start,
            end_sequence=end, source_version=source.version,
            source_ref=source.source_ref, source_revision=source.source_revision,
            previous_segment_digest=previous_digest, progress_json=encoded,
            progress_digest=payload["progressDigest"],
            proof_digest=digest(payload), proof_mac=_mac(key, payload))
        if actor_service._actor(principal) != actor:
            _reject("v4候选分段入库后账号权限变化", "access_denied", 403)
    return {"attemptId": attempt.id, "sourceKey": source.source_key,
        "segmentId": record.id, "segmentIndex": expected_index,
        "startSequence": start, "endSequence": end,
        "sourceCompleteCandidate": end == source.page_count,
        "sourceAuthorityVerified": False,
        "financeRevisionWriteFenceVerified": False,
        "sealed": False, "reportGenerationSupported": False}
