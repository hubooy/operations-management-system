"""Internal read-only v4 seal-admission candidate; never changes parent state.

The 0037 SECURITY DEFINER probe locks both owning revision rows in one fixed
order while this transaction verifies the immutable 0036 segment witnesses.
It grants neither a seal nor an Agent/report/file permission.
"""
from __future__ import annotations

from itertools import zip_longest
import json
import re
import time

from django.conf import settings
from django.db import connection, transaction

from business_analysis import finance_collection_state_v4 as finance_state
from business_analysis.contracts import AnalysisContractError, PageReconciler

from . import business_evidence_v3 as actor_service, business_v4_validation as validation
from . import models as m
from .policy import AiError, canonical, digest, identifier

SCHEMA = "business-v4-seal-admission-candidate-v1"
GUARD_VERSION = "business-v4-source-write-fence-read-v1"
MAX_RESPONSE_BYTES = 38_000
MAX_INSPECT_SECONDS = 180


def _reject(message="v4封存准入候选缺少可信完整来源证明", code="conflict", status=409):
    raise AiError(message, code, status)


def _lock_source_revisions():
    if connection.vendor != "postgresql":
        _reject("v4封存准入要求 PostgreSQL 固定锁源事务", "service_unavailable", 503)
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5000ms'")
        if settings.DJANGO_PROCESS_ROLE == "ai_writer":
            cursor.execute("SELECT set_config('teruisi.ai_epoch',%s,true),"
                "set_config('teruisi.ai_cutover',%s,true)",
                [settings.AI_WRITE_AUTHORITY_EPOCH, settings.AI_WRITE_CUTOVER_ID])
        cursor.execute("SELECT finance_revision,finance_digest,netshop_revision,"
            "netshop_digest,guard_version,guard_installed_at FROM "
            "public.ai_v4_lock_source_revisions_for_admission()")
        records = cursor.fetchall()
    if len(records) != 1 or records[0][4] != GUARD_VERSION or records[0][5] is None:
        _reject("v4封存准入写源门禁版本不可用")
    finance_revision, finance_digest, netshop_revision, netshop_digest, _, cutoff = records[0]
    for number, value in ((finance_revision, finance_digest),
                          (netshop_revision, netshop_digest)):
        if (type(number) is not int or not 0 <= number <= 9_007_199_254_740_991
                or type(value) is not str
                or re.fullmatch(r"[0-9a-f]{64}", value) is None):
            _reject("v4封存准入写源修订格式无效")
    return ({"finance": f"{finance_revision}:{finance_digest}",
        "netshop": f"{netshop_revision}:{netshop_digest[:12]}"}, cutoff)


def _source_progress(attempt, source, key, key_id, cutoff, deadline):
    query = json.loads(source.query_json)
    expected = (source.page_count + validation.SEGMENT_PAGES - 1) // validation.SEGMENT_PAGES
    if not 1 <= expected <= validation.MAX_SOURCE_SEGMENTS:
        _reject("v4封存准入来源分段数量超过固定容量")
    chunks = m.AiBusinessV4Chunk.objects.filter(run_id=source.run_id,
        source_id=source.id).order_by("sequence").iterator(chunk_size=64)
    receipts = m.AiBusinessV4ToolReceipt.objects.filter(run_id=source.run_id,
        source_id=source.id).select_related("audit").order_by(
            "sequence").iterator(chunk_size=64)
    ledger = iter(zip_longest(chunks, receipts, fillvalue=None))
    ledger_rows, ledger_bytes, ledger_chain = 0, 0, digest([])
    segments = m.AiBusinessV4ValidationSegment.objects.filter(attempt=attempt,
        source=source).select_related("attempt").order_by("segment_index").iterator(
            chunk_size=16)
    prior = validation._initial_progress(source)
    prior_digest, count = validation.ZERO, 0
    for segment in segments:
        count += 1
        if time.monotonic() > deadline:
            _reject("v4封存准入逐段复核超过固定时间", "timeout", 408)
        start = (count - 1) * validation.SEGMENT_PAGES + 1
        end = min(count * validation.SEGMENT_PAGES, source.page_count)
        if (count > expected or segment.segment_index != count
                or segment.start_sequence != start or segment.end_sequence != end
                or segment.run_id != source.run_id or segment.source_id != source.id
                or segment.source_version != source.version
                or segment.source_ref != source.source_ref
                or segment.source_revision != source.source_revision
                or segment.previous_segment_digest != prior_digest
                or segment.created_at < cutoff or segment.created_at < attempt.created_at):
            _reject("v4封存准入分段范围、来源修订或前段链断裂")
        state = validation._verified_segment(segment, key, key_id)
        for sequence in range(start, end + 1):
            chunk, receipt = next(ledger, (None, None))
            if (chunk is None or receipt is None
                    or chunk.sequence != sequence or receipt.sequence != sequence
                    or chunk.run_id != source.run_id or chunk.source_id != source.id
                    or receipt.run_id != source.run_id or receipt.source_id != source.id
                    or receipt.chunk_id != chunk.id
                    or chunk.source_ref != source.source_ref
                    or chunk.source_revision != source.source_revision
                    or receipt.audit_id is None
                    or receipt.actor_email != attempt.actor_email
                    or receipt.surface != "business_collection"
                    or receipt.tool_name != (validation.finance_collector.TOOL
                        if source.domain == "finance" else
                        validation.promotion_collector.FIRST_TOOL if sequence == 1
                        else validation.promotion_collector.CONTINUATION_TOOL)
                    or receipt.audit.actor_email != attempt.actor_email
                    or receipt.audit.actor_role != "admin"
                    or receipt.audit.surface != "business_collection"
                    or receipt.audit.tool_name != receipt.tool_name
                    or receipt.audit.request_id != receipt.request_id
                    or receipt.audit.invocation_id != receipt.invocation_id
                    or receipt.audit.status != "succeeded"
                    or receipt.audit.error_code is not None
                    or receipt.response_digest != chunk.payload_digest
                    or receipt.audit.response_digest != chunk.payload_digest
                    or receipt.audit.created_at > chunk.created_at
                    or chunk.created_at > receipt.created_at
                    or chunk.created_at <= cutoff
                    or receipt.audit.created_at <= cutoff
                    or not 0 <= chunk.row_count <= 100
                    or not 0 < receipt.payload_bytes <=
                        (finance_state.PAGE_BYTES if source.domain == "finance"
                            else validation.promotion_collector.MAX_PAGE_BYTES)):
                _reject("v4封存准入当前事实与不可变工具收据身份不连续")
            try:
                arguments = json.loads(receipt.audit.arguments_json)
                if (receipt.audit.arguments_json != canonical(arguments)
                        or type(arguments) is not dict
                        or set(arguments) != {"argumentsDigest"}
                        or type(arguments["argumentsDigest"]) is not str
                        or re.fullmatch(r"[0-9a-f]{64}",
                            arguments["argumentsDigest"]) is None):
                    _reject("v4封存准入工具请求摘要格式无效")
            except (TypeError, ValueError, RecursionError) as error:
                raise AiError("v4封存准入工具请求摘要不可解析", "conflict", 409) from error
            ledger_rows += chunk.row_count
            ledger_bytes += receipt.payload_bytes
            ledger_chain = digest([ledger_chain, sequence, chunk.payload_digest,
                receipt.audit_id, receipt.invocation_id,
                chunk.source_ref, chunk.source_revision])
        if (state.get("sourceKey") != source.source_key
                or state.get("domain") != source.domain
                or state.get("pageCount") != end
                or type(state.get("rowCount")) is not int
                or type(state.get("storedBytes")) is not int
                or not prior["rowCount"] <= state["rowCount"] <= source.row_count
                or not prior["storedBytes"] < state["storedBytes"] <= source.stored_bytes
                or type(state.get("receiptChainDigest")) is not str
                or re.fullmatch(r"[0-9a-f]{64}", state["receiptChainDigest"]) is None
                or state["receiptChainDigest"] == prior["receiptChainDigest"]
                or state["rowCount"] != ledger_rows
                or state["storedBytes"] != ledger_bytes
                or state["receiptChainDigest"] != ledger_chain):
            _reject("v4封存准入分段前后页行字节或收据链不连续")
        if source.domain == "finance":
            finance_state._state(state["domainState"], query)
        else:
            domain_state = state["domainState"]
            if (type(domain_state) is not dict
                    or set(domain_state) != {"verifier", "metadata", "observedDates"}
                    or type(domain_state["verifier"]) is not dict
                    or set(domain_state["verifier"]) != set(PageReconciler().__dict__)
                    or type(domain_state["metadata"]) is not dict
                    or type(domain_state["observedDates"]) is not list
                    or len(domain_state["observedDates"]) > 93
                    or domain_state["observedDates"] != sorted(set(domain_state["observedDates"]))):
                _reject("v4封存准入推广有限状态无效")
        prior, prior_digest = state, segment.proof_digest
    if (count != expected or prior["pageCount"] != source.page_count
            or next(ledger, None) is not None
            or ledger_rows != source.row_count
            or ledger_bytes != source.stored_bytes):
        _reject("v4封存准入未覆盖来源所有页")
    validation._complete(prior, source, query)
    coverage = (finance_state.result(prior["domainState"],
        trusted_query=query)["coverage"] if source.domain == "finance"
        else prior["domainState"]["metadata"]["coverage"])
    return {"sourceKey": source.source_key, "domain": source.domain,
        "window": query.get("window") if source.domain == "netshop" else None,
        "sourceVersion": source.version, "queryDigest": source.query_digest,
        "sourceRef": source.source_ref, "sourceRevision": source.source_revision,
        "pageCount": source.page_count, "rowCount": source.row_count,
        "storedBytes": source.stored_bytes, "segmentCount": expected,
        "terminalSegmentDigest": prior_digest,
        "receiptChainDigest": prior["receiptChainDigest"],
        "coverage": coverage,
        "missingMonths": (prior["domainState"]["publication"]["missingMonths"]
            if source.domain == "finance" else None),
        "financeScope": query["scope"] if source.domain == "finance" else None,
        "financeMonths": query["months"] if source.domain == "finance" else None}


def _freshness(captured, live):
    try:
        old_number = int(captured.split(":", 1)[0])
        new_number = int(live.split(":", 1)[0])
    except (ValueError, TypeError, AttributeError, IndexError) as error:
        raise AiError("v4封存准入来源修订格式无效", "conflict", 409) from error
    if new_number < old_number or new_number == old_number and live != captured:
        _reject("v4封存准入写源修订发生回退或同版本变更")
    return "current_revision" if live == captured else "historical_revision"


def inspect(run_id, attempt_id, principal):
    """Return a versioned, non-authorizing candidate under held source locks."""
    identifier(run_id); identifier(attempt_id)
    if settings.DJANGO_PROCESS_ROLE not in {"development", "ai_writer"}:
        _reject("v4内部准入只允许 AI writer", "access_denied", 403)
    deadline = time.monotonic() + MAX_INSPECT_SECONDS
    key, key_id = validation._key()
    with transaction.atomic():
        live_revisions, cutoff = _lock_source_revisions()
        actor, parent, sources, directory_digest = validation._directory(run_id, principal)
        attempt = m.AiBusinessV4ValidationAttempt.objects.filter(pk=attempt_id,
            run_id=parent.id).first()
        latest = (m.AiBusinessV4ValidationAttempt.objects.filter(run_id=parent.id)
            .order_by("-created_at", "-id").first())
        if (attempt is None or latest is None or latest.id != attempt.id
                or attempt.created_at <= cutoff
                or attempt.run_version != parent.version
                or attempt.plan_digest != parent.plan_digest
                or attempt.directory_digest != directory_digest
                or attempt.actor_email != actor["email"]
                or attempt.actor_version != actor["version"]
                or attempt.key_id != key_id):
            _reject("v4封存准入尝试早于写源门禁或账号目录已变化")
        proofs = []
        try:
            for source in sources:
                proof = _source_progress(attempt, source, key, key_id, cutoff, deadline)
                live = live_revisions[source.domain if source.domain == "finance"
                    else "netshop"]
                proof["revisionFreshness"] = _freshness(source.source_revision, live)
                proof["liveRevision"] = live
                proofs.append(proof)
        except (AnalysisContractError, KeyError, TypeError, ValueError,
                AttributeError, UnicodeError, RecursionError) as error:
            raise AiError("v4封存准入候选分段状态无法完整复核", "conflict", 409) from error
        after_actor, after_parent, after_sources, after_digest = (
            validation._directory(run_id, principal))
        if (after_actor != actor or after_parent.version != parent.version
                or after_digest != directory_digest
                or [(item.id, item.version, item.checkpoint_json) for item in after_sources]
                    != [(item.id, item.version, item.checkpoint_json) for item in sources]):
            raise AiError("v4封存准入期间账号或目录CAS变化", "version_conflict", 409)
        if time.monotonic() > deadline:
            _reject("v4封存准入超过固定复核时间", "timeout", 408)
        base = {"schemaVersion": SCHEMA, "runId": parent.id,
            "runVersion": parent.version, "attemptId": attempt.id,
            "planDigest": parent.plan_digest, "directoryDigest": directory_digest,
            "actorVersion": actor["version"], "keyId": key_id,
            "sources": proofs, "sourceCount": len(proofs),
            "sourceRevisionWriteFencesVerified": True,
            "segmentedReceiptAndRequestProofVerified": True,
            "crossDomainSnapshotAtomic": False,
            "financeDailyProrationAllowed": False,
            "inferSkuProfit": False,
            "sumOverlappingErpB2bAdsAllowed": False,
            "upstreamSignatureVerified": False,
            "sourceAuthorityVerified": False, "sealed": False,
            "reportGenerationSupported": False, "agentDispatchSupported": False}
        result = {**base, "candidateDigest": digest(base)}
        if len(canonical(result).encode("utf-8")) > MAX_RESPONSE_BYTES:
            _reject("v4封存准入候选响应超过固定容量", "payload_too_large", 413)
        final_latest = (m.AiBusinessV4ValidationAttempt.objects.filter(
            run_id=parent.id).order_by("-created_at", "-id").values("id").first())
        if (final_latest != {"id": attempt.id}
                or validation._key()[1] != key_id
                or actor_service._actor(principal) != actor):
            _reject("v4封存准入返回前账号、最新尝试或密钥已变化")
        return result
