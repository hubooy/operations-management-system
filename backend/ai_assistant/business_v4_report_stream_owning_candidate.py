"""Test-only owning read of sealed v4 pages beside a sealed-v2 report.

There is no durable same-report link. Matching shop, periods and HMAC seal
prove only an *unbound* candidate; this module is unregistered and cannot
create AiBusinessFileRun, Agent evidence, report tables or download grants.
"""
from __future__ import annotations

from itertools import zip_longest
import json

from django.conf import settings

from business_analysis import (evidence_seal_v4, period_bound_plan_v1,
    report_v4_promotion_stream_candidate as stream)
from business_analysis.contracts import AnalysisContractError

from . import (business_erp_rollup_materials as v2_owner,
    business_v4_seal_verify as v4_owner, models as m)
from .policy import AiError, canonical, digest, identifier


SCHEMA = "business-report-v4-unbound-owning-candidate-v1"
RESULT_SCHEMA = "business-report-v4-unbound-stream-result-v1"


def _need(ok, message="v4封存来源与v2报告同店、日期或只读回执不一致"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _test_only(enabled):
    _need(enabled is True, "v4同报告拥有方试验默认关闭")
    if (settings.DJANGO_ENVIRONMENT != "test" or
            settings.DJANGO_PROCESS_ROLE not in {"development", "ai_writer"}):
        raise AiError("v4同报告拥有方试验仅允许隔离测试AI写入环境",
            "access_denied", 403)


def _report(report_id, principal):
    report, snapshot, evidence, _, fixed = v2_owner._bound(
        identifier(report_id), principal)
    _need(report.scope_json == "null" and principal.scope is None
        and snapshot["evidenceRunId"] == evidence.id
        and snapshot["evidenceVersion"] == evidence.version
        and snapshot["sealedDigest"] == fixed["sealedDigest"])
    try:
        header = json.loads(evidence.plan_json)
        request = header["analysisRequest"]
        scope = snapshot["scope"]
        _need(type(scope) is dict and
            set(scope) == {"platform", "shop", "startDate", "endDate"}
            and scope["platform"] == "京东"
            and scope["shop"] not in {"多店铺", "市场样本"}
            and type(request) is dict and
            request["requestedWindows"] == ["current", "previous", "yearAgo"]
            and snapshot["question"] == request["question"])
    except (KeyError, ValueError, TypeError) as error:
        raise AiError("v2报告无精确三期同店原始意图", "conflict", 409) from error
    return report, snapshot, evidence, fixed, request, scope


def _v4(run_id, principal):
    verified = v4_owner.verify_seal(identifier(run_id), principal)
    actor, parent, sources, directory = v4_owner._directory(run_id,
        principal)
    seal = m.AiBusinessV4Seal.objects.filter(run_id=parent.id).first()
    _need(seal is not None and seal.body_digest == verified["sealedDigest"]
        and parent.version == verified["evidenceVersion"]
        and parent.id == verified["runId"]
        and verified["internalSealVerified"] is True
        and verified["segmentHmacVerified"] is True
        and verified["reportGenerationSupported"] is False
        and verified["agentDispatchSupported"] is False)
    try:
        body = evidence_seal_v4.read(seal.body_json)
        plan = json.loads(parent.plan_json)
        period = period_bound_plan_v1.prepare_candidate(plan)
    except (AnalysisContractError, KeyError, TypeError,
            ValueError, UnicodeError) as error:
        raise AiError("v4三期封存计划或正文无法重建", "conflict", 409) from error
    _need(body["runId"] == parent.id
        and body["evidenceVersion"] == parent.version
        and body["directoryDigest"] == directory
        and body["planDigest"] == parent.plan_digest
        and len(body["sources"]) == len(sources)
        and [row["sourceKey"] for row in body["sources"]] ==
            [row.source_key for row in sources]
        and verified["sourceRefs"] == [{"sourceKey": row.source_key,
            "sourceRef": row.source_ref,
            "sourceRevision": row.source_revision,
            "liveRevision": row.source_revision,
            "verificationFreshness": "current_revision"}
            for row in sources],
        "v4封存来源已有新修订、目录变化或不完整")
    seal_state = {"body_digest": seal.body_digest,
        "body_json": seal.body_json, "body_mac": seal.body_mac,
        "key_id": seal.key_id}
    return actor, parent, sources, directory, verified, body, plan, period, seal_state


def _unbound(report_context, v4_context):
    report, snapshot, evidence, fixed, request, scope = report_context
    actor, parent, sources, directory, verified, body, plan, period, _ = (
        v4_context)
    _need(report.owner_email == parent.owner_email == actor["email"]
        and report.scope_json == parent.scope_json == "null"
        and plan["analysisRequest"] == request
        and plan["clientRequestId"] == parent.client_request_id
        and period["comparisonRule"] == "previous_equal_length_v1")
    current = next(row for row in period["dailySources"] if row[
        "window"] == "current")
    _need(scope == {"platform": "京东", "shop": current["shop"],
        "startDate": current["originalQueryStartDate"],
        "endDate": current["originalQueryEndDate"]})
    by_key = {row.source_key: row for row in sources}
    refs = {row["sourceKey"]: row for row in verified["sourceRefs"]}
    _need(len(refs) == len(sources) and set(refs) == set(by_key)
        and all(refs[key]["sourceRef"] == row.source_ref
            and refs[key]["sourceRevision"] == row.source_revision
            and refs[key]["liveRevision"] == row.source_revision
            and refs[key]["verificationFreshness"] == "current_revision"
            for key, row in by_key.items()))
    windows = []
    for name in ("current", "previous", "yearAgo"):
        row = next(item for item in period["dailySources"] if item[
            "window"] == name)
        physical = by_key[row["sourceKey"]]
        _need(physical.query_digest == row["queryDigest"]
            and physical.source_identity_digest ==
                row["sourceIdentityDigest"])
        windows.append({"window": name, "sourceKey": row["sourceKey"],
            "queryDigest": row["queryDigest"],
            "sourceIdentityDigest": row["sourceIdentityDigest"],
            "sourceRef": physical.source_ref,
            "sourceRevision": physical.source_revision,
            "resolvedPeriod": row["resolvedPeriod"],
            "expectedDayDigest": row["expectedDayDigest"]})
    value = {"schemaVersion": SCHEMA,
        "reportId": report.id,
        "reportSnapshotDigest": digest(report.snapshot_json),
        "workflowInputDigest": fixed["workflowInputDigest"],
        "v2EvidenceRunId": evidence.id,
        "v2EvidenceVersion": evidence.version,
        "v2SealedDigest": snapshot["sealedDigest"],
        "v4RunId": parent.id,
        "v4EvidenceVersion": parent.version,
        "v4PlanDigest": plan["planDigest"],
        "v4SealedDigest": verified["sealedDigest"],
        "v4SealProofDigest": verified["proofDigest"],
        "periodPlanDigest": period["periodPlanDigest"],
        "analysisRequestDigest": digest(request),
        "shop": scope["shop"], "platform": "京东",
        "originalPeriod": {"startDate": scope["startDate"],
            "endDate": scope["endDate"]},
        "promotionWindows": windows,
        "candidateOnly": True,
        "persistedSameReportLinkVerified": False,
        "v4RowsReadableForReport": False,
        "agentCitationSupported": False,
        "registeredRenderer": False, "publishable": False}
    return {**value, "candidateDigest": digest(value)}


def _source_manifest(bridge, source, seal_item):
    try:
        checkpoint = json.loads(source.checkpoint_json)
        expected = checkpoint["verifier"]["evidence_digest"]
    except (ValueError, TypeError, KeyError) as error:
        raise AiError("v4推广终段检查点缺来源摘要", "conflict", 409) from error
    _need(seal_item["sourceKey"] == source.source_key
        and seal_item["queryDigest"] == source.query_digest
        and seal_item["sourceRef"] == source.source_ref
        and seal_item["sourceRevision"] == source.source_revision
        and seal_item["pageCount"] == source.page_count
        and seal_item["rowCount"] == source.row_count
        and seal_item["storedBytes"] == source.stored_bytes)
    body = {"schemaVersion": stream.SOURCE_SCHEMA,
        "bridgeCandidateDigest": bridge["candidateDigest"],
        "sourceKey": source.source_key,
        "queryDigest": source.query_digest,
        "sourceIdentityDigest": source.source_identity_digest,
        "sourceRef": source.source_ref,
        "sourceRevision": source.source_revision,
        "pageCount": source.page_count,
        "rowCount": source.row_count,
        "storedBytes": source.stored_bytes,
        "evidenceDigest": expected,
        "receiptChainDigest": seal_item["receiptChainDigest"]}
    return {**body, "manifestDigest": digest(body)}


def _pages(parent, source, actor):
    chunks = m.AiBusinessV4Chunk.objects.filter(run_id=parent.id,
        source_id=source.id).order_by("sequence").iterator(chunk_size=16)
    receipts = m.AiBusinessV4ToolReceipt.objects.filter(run_id=parent.id,
        source_id=source.id).select_related("audit").order_by(
            "sequence").iterator(chunk_size=16)
    for sequence, (chunk, receipt) in enumerate(zip_longest(chunks,
            receipts), 1):
        _need(chunk is not None and receipt is not None
            and chunk.sequence == receipt.sequence == sequence
            and chunk.run_id == receipt.run_id == parent.id
            and chunk.source_id == receipt.source_id == source.id
            and receipt.chunk_id == chunk.id
            and receipt.actor_email == actor["email"]
            and receipt.audit_id is not None
            and receipt.audit.actor_email == actor["email"]
            and receipt.audit.actor_role == "admin"
            and receipt.audit.surface == receipt.surface ==
                "business_collection"
            and receipt.audit.tool_name == receipt.tool_name
            and receipt.audit.request_id == receipt.request_id
            and receipt.audit.invocation_id == receipt.invocation_id
            and receipt.audit.status == "succeeded"
            and receipt.audit.error_code is None
            and receipt.audit.created_at <= chunk.created_at <=
                receipt.created_at
            and receipt.payload_bytes == len(chunk.payload_json.encode("utf-8")))
        try:
            arguments = json.loads(receipt.audit.arguments_json)
            _need(receipt.audit.arguments_json == canonical(arguments)
                and type(arguments) is dict
                and set(arguments) == {"argumentsDigest"})
        except (TypeError, ValueError) as error:
            raise AiError("v4推广页请求审计摘要无效", "conflict", 409) from error
        yield {"sequence": sequence,
            "payloadJson": chunk.payload_json,
            "payloadDigest": chunk.payload_digest,
            "sourceRef": chunk.source_ref,
            "sourceRevision": chunk.source_revision,
            "rowCount": chunk.row_count,
            "receiptResponseDigest": receipt.response_digest,
            "auditResponseDigest": receipt.audit.response_digest,
            "requestArgumentsDigest": arguments["argumentsDigest"],
            "toolName": receipt.tool_name,
            "auditSucceeded": True,
            "auditId": receipt.audit_id,
            "invocationId": receipt.invocation_id}


def stage_unbound(report_id, v4_run_id, window, principal, sink, *,
                  enabled=False, checkpoint=None):
    """Test-only real-ORM source replay; result is blocked from any report."""
    _test_only(enabled)
    report_context = _report(report_id, principal)
    v4_context = _v4(v4_run_id, principal)
    bridge = _unbound(report_context, v4_context)
    actor, parent, sources, directory, verified, body, _, _, seal_state = (
        v4_context)
    _need(window in {"current", "previous", "yearAgo"})
    selected = next(row for row in bridge["promotionWindows"] if row[
        "window"] == window)
    source = next(row for row in sources if row.source_key == selected[
        "sourceKey"])
    seal_item = next(row for row in body["sources"] if row[
        "sourceKey"] == source.source_key)
    manifest = _source_manifest(bridge, source, seal_item)

    def current(_bridge, _manifest):
        report, snapshot, evidence, fixed, request, scope = _report(
            report_id, principal)
        fresh_actor, fresh_parent, fresh_sources, fresh_directory = (
            v4_owner._directory(v4_run_id, principal))
        fresh_seal = m.AiBusinessV4Seal.objects.filter(run_id=parent.id).values(
            "body_digest", "body_json", "body_mac", "key_id").first()
        return (report.snapshot_json == report_context[0].snapshot_json
            and report.workflow.input_json ==
                report_context[0].workflow.input_json
            and evidence.version == report_context[2].version
            and fixed == report_context[3]
            and fresh_actor == actor
            and fresh_parent.version == parent.version
            and fresh_directory == directory
            and [(row.id, row.version, row.source_ref,
                row.source_revision, row.checkpoint_json) for row in
                fresh_sources] == [(row.id, row.version, row.source_ref,
                row.source_revision, row.checkpoint_json) for row in sources]
            and fresh_seal == seal_state)

    try:
        streamed = stream.stage_candidate(bridge, window, manifest,
            lambda: _pages(parent, source, actor), sink, current,
            enabled=True, checkpoint=checkpoint)
        final = v4_owner.verify_seal(v4_run_id, principal)
        _need(final == verified and current(bridge, manifest),
            "v4双遍页流后封存HMAC或同店报告变化")
    except (AnalysisContractError, KeyError, ValueError, TypeError,
            UnicodeError) as error:
        sink.abort()
        raise AiError("v4已封存页流不能作为同报告材料", "conflict", 409) from error
    except Exception:
        sink.abort()
        raise
    result = {"schemaVersion": RESULT_SCHEMA,
        "status": "blocked_unbound",
        "bridgeCandidateDigest": bridge["candidateDigest"],
        "v2SealedDigest": bridge["v2SealedDigest"],
        "v4SealedDigest": bridge["v4SealedDigest"],
        "sourceManifestDigest": manifest["manifestDigest"],
        "streamResultDigest": streamed["resultDigest"],
        "persistedSameReportLinkVerified": False,
        "sameReportCompositionSupported": False,
        "fileDownloadSupported": False}
    return {**result, "resultDigest": digest(result)}
