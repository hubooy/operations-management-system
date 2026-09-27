"""Internal read-only verification of one sealed v4 parent and its real ledger.

This endpoint is deliberately unregistered. It cannot grant an ai_reader,
Agent, renderer, model or public route permission.
"""
from __future__ import annotations

import hashlib
from importlib import import_module
from itertools import zip_longest
import json
import re
import time

from django.conf import settings
from django.db import connection, transaction

from business_analysis import business_promotion_v4_plan, evidence_seal_v4, evidence_v4
from business_analysis.contracts import AnalysisContractError

from . import (business_evidence_v3 as actor_service,
    business_v4_finance_collection as finance_collector,
    business_v4_netshop_promotion as promotion_collector,
    business_v4_seal_admission as admission,
    business_v4_seal_hmac as seal_hmac,
    business_v4_validation as validation, models as m)
from .policy import AiError, authorize_owner, canonical, digest, identifier

SCHEMA = "business-v4-internal-seal-verified-v1"
MAX_VERIFY_SECONDS = 600
MAX_RESPONSE_BYTES = 38_000


def _reject(message="v4封存来源或应用签名复核失败", code="conflict", status=409):
    raise AiError(message, code, status)


def _directory(run_id, principal):
    actor = actor_service._actor(principal)
    parent = m.AiBusinessV4Run.objects.filter(pk=identifier(run_id)).first()
    if parent is None:
        raise AiError("v4封存父任务不存在", "not_found", 404)
    authorize_owner(parent, principal)
    try:
        if (parent.status != "sealed" or parent.collection_status != "manual"
                or parent.scope_json != "null" or parent.owner_email != actor["email"]
                or parent.version != parent.page_count + 2
                or type(parent.plan_json) is not str
                or len(parent.plan_json.encode("utf-8")) > 131_072):
            _reject("v4父任务尚无可验证的精确封存状态")
        plan = json.loads(parent.plan_json)
        business_promotion_v4_plan._plan(plan)
        if (parent.plan_json != canonical(plan)
                or parent.plan_digest != digest(parent.plan_json)
                or parent.run_identity_digest != plan["runIdentityDigest"]
                or plan["runCapacitySupported"] is not True):
            _reject("v4封存容量计划身份变化")
        sources = list(m.AiBusinessV4Source.objects.filter(run_id=parent.id)
            .order_by("ordinal")[:5])
        if not 2 <= len(sources) <= 4 or len(sources) != plan["sourceCount"]:
            _reject("v4封存目录不属于京东推广加唯一财报范围")
        finance = [source for source in sources if source.domain == "finance"]
        promotion = [source for source in sources if source.domain == "netshop"]
        if len(finance) != 1 or not 1 <= len(promotion) <= 3:
            _reject("v4封存缺京东推广或唯一财报来源")
        finance_collector._plan(parent, finance[0])
        current = next((source for source in promotion if json.loads(
            source.query_json).get("window") == "current"), None)
        if current is None:
            _reject("v4封存缺京东推广本期来源")
        selected = promotion_collector.selection(plan,
            json.loads(current.query_json), current.source_key)
        actual_windows = {json.loads(source.query_json)["window"]
            for source in promotion}
        selected_windows = {window for window, item in
            selected["selectedWindows"].items()
            if item["status"] == "selected_in_capacity_plan"}
        if actual_windows != selected_windows:
            _reject("v4封存比较窗口与固定计划不一致")
        for source in sources:
            query = json.loads(source.query_json)
            entry = plan["sourcePlans"][source.ordinal - 1]
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
                    or type(source.source_ref) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", source.source_ref) is None):
                _reject("v4封存来源目录含未完成或变更的真实来源")
        if (sum(item.page_count for item in sources) != parent.page_count
                or sum(item.row_count for item in sources) != parent.row_count
                or sum(item.stored_bytes for item in sources) != parent.stored_bytes):
            _reject("v4封存父子页行字节不守恒")
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
        raise AiError("v4封存目录无法独立重建", "conflict", 409) from error


def _expect_source(body_item, proof, source):
    query = json.loads(source.query_json)
    comparable = {key: proof[key] for key in evidence_seal_v4.COMMON
        if key not in {"revisionFreshness", "liveRevision"}}
    if source.domain == "finance":
        comparable.update(scope=query["scope"],
            analysisPeriod=query["analysisPeriod"],
            missingMonths=proof["missingMonths"])
    else:
        comparable.update(window=query["window"], coverage=proof["coverage"])
    actual = {key: value for key, value in body_item.items()
        if key not in {"revisionFreshness", "liveRevision"}}
    if canonical(actual) != canonical(comparable):
        _reject("v4封存正文来源与真实终段事实不一致")


def _raw_scan(source, cutoff, deadline):
    """Stream actual page bytes outside any business revision row lock."""
    chunks = m.AiBusinessV4Chunk.objects.filter(run_id=source.run_id,
        source_id=source.id).order_by("sequence").iterator(chunk_size=4)
    receipts = m.AiBusinessV4ToolReceipt.objects.filter(run_id=source.run_id,
        source_id=source.id).select_related("audit").order_by(
            "sequence").iterator(chunk_size=4)
    count, rows, size = 0, 0, 0
    limit = (validation.finance_state.PAGE_BYTES if source.domain == "finance"
        else validation.promotion_collector.MAX_PAGE_BYTES)
    for chunk, receipt in zip_longest(chunks, receipts, fillvalue=None):
        count += 1
        if time.monotonic() > deadline:
            _reject("v4封存原字节全链超过600秒，需版本化可恢复读取证明；未通过本次验证",
                "verification_requires_resume", 413)
        if (chunk is None or receipt is None or count > source.page_count
                or chunk.sequence != count or receipt.sequence != count
                or chunk.run_id != source.run_id or chunk.source_id != source.id
                or receipt.run_id != source.run_id or receipt.source_id != source.id
                or receipt.chunk_id != chunk.id
                or receipt.audit_id is None
                or type(chunk.payload_json) is not str):
            _reject("v4封存原字节页或收据缺失、跨来源或乱序")
        encoded = chunk.payload_json.encode("utf-8")
        page_bytes = len(encoded)
        actual_digest = hashlib.sha256(encoded).hexdigest()
        if (not 0 < page_bytes <= limit
                or chunk.payload_digest != actual_digest
                or chunk.source_ref != source.source_ref
                or chunk.source_revision != source.source_revision
                or receipt.payload_bytes != page_bytes
                or receipt.response_digest != actual_digest
                or receipt.audit.response_digest != actual_digest):
            _reject("v4封存当前页原始UTF-8字节与摘要、收据不一致")
        rows += chunk.row_count
        size += page_bytes
        if size > source.stored_bytes:
            _reject("v4封存原字节累计超过固定来源容量")
    if count != source.page_count or rows != source.row_count or size != source.stored_bytes:
        _reject("v4封存原字节全链页行字节未完整覆盖")
    return {"pageCount": count, "rowCount": rows, "storedBytes": size}


def _require_consumption(parent, attempt, seal):
    frozen = import_module(
        "ai_assistant.migrations.0043_business_v4_seal_consumption_candidate")
    with connection.cursor() as cursor:
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,"
            "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
            "WHERE p.oid=to_regprocedure('public.ai_v4_verify_seal_consumption("
            "text,text,bigint,text)')")
        verifier = cursor.fetchone()
        if (verifier is None or verifier[0] != frozen.VERIFY_CONSUMPTION.split("$$")[1]
                or verifier[1] is not True
                or {item.replace(" ", "") for item in (verifier[2] or [])}
                != {"search_path=pg_catalog,public"}
                or verifier[3] in {"teruisi_ai_reader", "teruisi_ai_writer",
                                   "teruisi_ai_seal_writer"}):
            _reject("v4封存消费门禁缺失，不能作为当前权威")
        cursor.execute("SELECT t.tgtype,t.tgdeferrable,t.tginitdeferred,"
            "t.tgenabled,t.tgfoid=to_regprocedure("
            "'public.ai_v4_seal_requires_consumption()'),"
            "p.prosrc,p.prosecdef,p.proconfig,"
            "pg_catalog.pg_get_userbyid(p.proowner) "
            "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p "
            "ON p.oid=t.tgfoid WHERE t.tgrelid="
            "'public.ai_business_v4_seals'::regclass AND "
            "t.tgname='ai_v4_seal_consumption_required'")
        trigger = cursor.fetchone()
        if (trigger is None or trigger[:5] != (5, True, True, "O", True)
                or trigger[5] != frozen.REQUIRE_CONSUMPTION.split("$$")[1]
                or trigger[6] is not True
                or {item.replace(" ", "") for item in (trigger[7] or [])}
                != {"search_path=pg_catalog,public"}
                or trigger[8] in {"teruisi_ai_reader", "teruisi_ai_writer",
                                   "teruisi_ai_seal_writer"}):
            _reject("v4封存消费延期约束缺失，不能作为当前权威")
        cursor.execute("SELECT public.ai_v4_verify_seal_consumption(%s,%s,%s,%s)",
            [parent.id, attempt.id, seal.evidence_version,
             seal.body_digest])
        if cursor.fetchone() != (True,):
            _reject("v4旧封存缺同事务消费回执，不能作为当前权威")


def verify_seal(run_id, principal):
    """Return a compact verified seal receipt, never a runnable report grant."""
    if settings.DJANGO_PROCESS_ROLE not in {"development", "ai_writer"}:
        _reject("v4封存验证仅供内部AI writer", "access_denied", 403)
    deadline = time.monotonic() + MAX_VERIFY_SECONDS
    # Hold each business revision pair only for the gate snapshot itself, not
    # for the potentially 8-GiB immutable-page verification below.
    with transaction.atomic():
        _, cutoff = admission._lock_source_revisions()
    actor, parent, sources, directory_digest = _directory(run_id, principal)
    seal = m.AiBusinessV4Seal.objects.filter(run_id=parent.id).select_related(
        "attempt").first()
    if seal is None:
        _reject("v4父任务仅有裸sealed状态、缺唯一封存行")
    attempt = seal.attempt
    if (seal.evidence_version != parent.version
            or attempt.run_id != parent.id
            or attempt.run_version != parent.version - 1
            or attempt.plan_digest != parent.plan_digest
            or attempt.directory_digest != directory_digest
            or attempt.actor_email != actor["email"]
            or attempt.actor_version != actor["version"]
            or attempt.created_at <= cutoff
            or seal.key_id != attempt.key_id
            or seal.body_digest != digest(seal.body_json)):
        _reject("v4封存行、验证尝试或当前账号版本不一致")
    _require_consumption(parent, attempt, seal)
    latest = (m.AiBusinessV4ValidationAttempt.objects.filter(
        run_id=parent.id).order_by("-created_at", "-id").values("id").first())
    if latest != {"id": attempt.id}:
        _reject("v4封存并非最新完成验证尝试")
    try:
        body = evidence_seal_v4.read(seal.body_json)
        key_id = seal_hmac.verify(seal.body_json, seal.body_mac,
            seal.key_id)
    except (AnalysisContractError, UnicodeError, TypeError, ValueError,
            RecursionError) as error:
        raise AiError("v4封存正文不是规范、真实应用签名", "conflict", 409) from error
    if (body["runId"] != parent.id or body["attemptId"] != attempt.id
            or body["evidenceVersion"] != parent.version
            or body["planDigest"] != parent.plan_digest
            or body["directoryDigest"] != directory_digest
            or body["actorVersion"] != actor["version"]
            or body["keyId"] != key_id
            or len(body["sources"]) != len(sources)):
        _reject("v4封存正文顶层身份不是当前真实父目录")
    key, segment_id = validation._key()
    if segment_id != key_id:
        _reject("v4封存与分段密钥版本不一致")
    try:
        for source, item in zip(sources, body["sources"]):
            proof = admission._source_progress(attempt, source, key,
                key_id, cutoff, deadline)
            _expect_source(item, proof, source)
            _raw_scan(source, cutoff, deadline)
    except AiError as error:
        if error.code == "timeout":
            _reject("v4封存全链超过600秒，需版本化可恢复读取证明；未通过本次验证",
                "verification_requires_resume", 413)
        raise
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            AttributeError, UnicodeError, RecursionError) as error:
        raise AiError("v4封存来源段链、原始UTF-8字节或正文无法重建",
            "conflict", 409) from error
    if time.monotonic() > deadline:
        _reject("v4封存原字节全链超过600秒，需版本化可恢复读取证明；未通过本次验证",
            "verification_requires_resume", 413)
    # Reacquire both revision locks only for the final actor/directory/seal
    # fence. New legal imports during the long scan make freshness historical
    # but do not mutate already sealed bytes.
    with transaction.atomic():
        live_revisions, final_cutoff = admission._lock_source_revisions()
        if final_cutoff != cutoff:
            _reject("v4封存复核期间写源门禁安装版本变化")
        final_actor, final_parent, final_sources, final_digest = _directory(
            parent.id, principal)
        final_seal = m.AiBusinessV4Seal.objects.filter(run_id=parent.id).values(
            "evidence_version", "body_json", "body_digest", "body_mac",
            "key_id", "attempt_id").first()
        if (final_actor != actor or final_parent.version != parent.version
                or final_parent.status != "sealed" or final_digest != directory_digest
                or [(item.id, item.version, item.checkpoint_json) for item in final_sources]
                    != [(item.id, item.version, item.checkpoint_json) for item in sources]
                or final_seal != {"evidence_version": seal.evidence_version,
                    "body_json": seal.body_json, "body_digest": seal.body_digest,
                    "body_mac": seal.body_mac, "key_id": seal.key_id,
                    "attempt_id": seal.attempt_id}
                or (m.AiBusinessV4ValidationAttempt.objects.filter(
                    run_id=parent.id).order_by("-created_at", "-id")
                    .values("id").first()) != {"id": attempt.id}
                or validation._key()[1] != key_id
                or actor_service._actor(principal) != actor
                or time.monotonic() > deadline):
            _reject("v4封存返回前账号、目录或密钥变化")
        try:
            seal_hmac.verify(seal.body_json, seal.body_mac, seal.key_id)
        except (AnalysisContractError, UnicodeError, TypeError, ValueError,
                RecursionError) as error:
            raise AiError("v4封存返回前应用签名或密钥变化", "conflict", 409) from error
        _require_consumption(parent, attempt, seal)
        result = {"schemaVersion": SCHEMA, "runId": parent.id,
            "evidenceVersion": parent.version,
            "sealedDigest": seal.body_digest,
            "attemptId": attempt.id, "sourceCount": len(sources),
            "sourceRefs": [{"sourceKey": item.source_key,
                "sourceRef": item.source_ref,
                "sourceRevision": item.source_revision,
                "liveRevision": live_revisions[item.domain if item.domain == "finance"
                    else "netshop"],
                "verificationFreshness": admission._freshness(item.source_revision,
                    live_revisions[item.domain if item.domain == "finance"
                        else "netshop"])}
                for item in sources],
            "internalSealVerified": True,
            "segmentHmacVerified": True,
            "upstreamSignatureVerified": False,
            "crossDomainSnapshotAtomic": False,
            "financeDailyProrationAllowed": False,
            "inferSkuProfit": False,
            "sumOverlappingErpB2bAdsAllowed": False,
            "reportGenerationSupported": False,
            "agentDispatchSupported": False}
        result["proofDigest"] = digest(result)
        if len(canonical(result).encode("utf-8")) > MAX_RESPONSE_BYTES:
            _reject("v4封存证明超过固定响应容量", "payload_too_large", 413)
        return result
