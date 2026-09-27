"""Test-only owning preflight for a linked report and real v4 seal.

It reads the protected 0071 link under an isolated test administrator, verifies
the current application HMAC with the existing v4 verifier, and returns only a
blocked authorization candidate. No route, Agent, renderer or file calls it.
"""
from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.db import connection

from business_analysis import report_v4_authorization_preflight_v1 as pure
from business_analysis.contracts import AnalysisContractError, digest

from . import (business_v4_report_stream_owning_candidate as source_owner,
    business_v4_report_link_sql as link_sql)
from .policy import AiError, canonical, identifier


SCHEMA = "business-report-v4-owning-authorization-preflight-v1"


def _need(ok, message="v4同报告授权前置证据或当前权限变化"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _test_only(enabled):
    _need(enabled is True, "v4报告授权前置拥有方默认关闭")
    if (settings.DJANGO_ENVIRONMENT != "test" or
            settings.DJANGO_PROCESS_ROLE != "development"):
        raise AiError("v4报告授权前置仅供隔离测试管理员",
            "access_denied", 403)


def _link(report_id):
    """Read the SQL-owned row and intent in test DB; never as a reader grant."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT row_to_json(item) FROM " + link_sql.LINKS +
            " item WHERE item.report_id=%s", [identifier(report_id)])
        found = cursor.fetchone()
        cursor.execute("SELECT row_to_json(item) FROM " + link_sql.INTENTS +
            " item WHERE item.report_id=%s", [report_id])
        issued = cursor.fetchone()
    _need(found is not None and issued is not None)
    row, intent = found[0], issued[0]
    _need(type(row) is dict and type(intent) is dict
        and row["report_id"] == intent["report_id"] == report_id
        and row["v4_run_id"] == intent["v4_run_id"]
        and row["owner_email"] == intent["owner_email"]
        and row["actor_version"] == intent["actor_version"]
        and row["v2_run_id"] == intent["v2_run_id"]
        and row["v2_sealed_digest"] == intent["v2_sealed_digest"]
        and row["v4_sealed_digest"] == intent["v4_sealed_digest"]
        and row["source_bindings_digest"] == intent[
            "source_bindings_digest"]
        and row["created_txid"] == intent["issued_txid"]
        and row["authority_verified"] is False
        and row["report_generation_supported"] is False)
    raw = row["source_bindings_json"]
    _need(type(raw) is str and len(raw.encode("utf-8")) <= 16_384
        and row["source_bindings_digest"] == hashlib.sha256(
            raw.encode("utf-8")).hexdigest())
    try:
        bindings = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise AiError("0071来源绑定无法解析", "conflict", 409) from error
    value = {"schemaVersion": "business-v4-report-link-read-candidate-v1",
        "reportId": row["report_id"],
        "v2EvidenceRunId": row["v2_run_id"],
        "v2EvidenceVersion": row["v2_evidence_version"],
        "v2SealedDigest": row["v2_sealed_digest"],
        "v4RunId": row["v4_run_id"],
        "v4EvidenceVersion": row["v4_evidence_version"],
        "v4PlanDigest": row["v4_plan_digest"],
        "v4SealedDigest": row["v4_sealed_digest"],
        "reportSnapshotDigest": row["report_snapshot_digest"],
        "workflowInputDigest": row["workflow_input_digest"],
        "sourceBindingsDigest": row["source_bindings_digest"],
        "sourceBindings": bindings,
        "creationTimeLinkPersisted": True,
        "appHmacVerified": False, "authorityVerified": False,
        "reportGenerationSupported": False,
        "agentDispatchSupported": False,
        "rendererRegistered": False, "downloadSupported": False}
    return row, value


def inspect_candidate(report_id, v4_run_id, principal, *, enabled=False):
    """Return why the real linked source cannot yet enter the 13-table report."""
    _test_only(enabled)
    report_id, v4_run_id = identifier(report_id), identifier(v4_run_id)
    report_context = source_owner._report(report_id, principal)
    v4_context = source_owner._v4(v4_run_id, principal)
    report, snapshot, evidence, fixed, request, scope = report_context
    _, parent, _, directory, verified, body, plan, _, seal_state = v4_context
    row, link = _link(report_id)
    _need(row["owner_email"] == principal.email.lower() == report.owner_email
        and row["v4_run_id"] == parent.id == v4_run_id
        and row["v2_run_id"] == evidence.id
        and row["v2_sealed_digest"] == snapshot["sealedDigest"]
        and row["v4_sealed_digest"] == verified["sealedDigest"]
        and row["report_snapshot_digest"] == hashlib.sha256(
            report.snapshot_json.encode("utf-8")).hexdigest()
        and row["workflow_input_digest"] == hashlib.sha256(
            report.workflow.input_json.encode("utf-8")).hexdigest())
    report_proof = {"reportId": report.id,
        "ownerEmail": report.owner_email, "scope": principal.scope,
        "reportSnapshotDigest": row["report_snapshot_digest"],
        "workflowInputDigest": row["workflow_input_digest"],
        "v2EvidenceRunId": evidence.id,
        "v2EvidenceVersion": evidence.version,
        "v2SealedDigest": snapshot["sealedDigest"],
        "shop": scope["shop"],
        "originalPeriod": {"startDate": scope["startDate"],
            "endDate": scope["endDate"]},
        "analysisRequestDigest": digest(request)}
    try:
        decision = pure.prepare_candidate(report_proof, link, plan,
            verified, canonical(body), enabled=True)
    except (AnalysisContractError, KeyError, TypeError, ValueError) as error:
        raise AiError("v4来源不能进入同报告授权前置候选",
            "conflict", 409) from error
    current_report = source_owner._report(report_id, principal)
    current_v4 = source_owner._v4(v4_run_id, principal)
    current_row, current_link = _link(report_id)
    _need(current_report[0].snapshot_json == report.snapshot_json
        and current_report[0].workflow.input_json ==
            report.workflow.input_json
        and current_report[2].version == evidence.version
        and current_v4[1].version == parent.version
        and current_v4[3] == directory
        and current_v4[4] == verified
        and current_v4[8] == seal_state
        and current_row == row and current_link == link)
    value = {"schemaVersion": SCHEMA,
        "status": decision["status"],
        "reportId": report_id,
        "v4RunId": v4_run_id,
        "shop": report_proof["shop"],
        "originalPeriod": report_proof["originalPeriod"],
        "decisionDigest": decision["resultDigest"],
        "v2SealedDigest": decision["v2SealedDigest"],
        "v4SealedDigest": decision["v4SealedDigest"],
        "periodPlanDigest": decision["periodPlanDigest"],
        "sourceBindingsDigest": decision["sourceBindingsDigest"],
        "sqlCreationTimeLinkRechecked": True,
        "currentV4ApplicationHmacRechecked": True,
        "legacySealCanGenerateReport": False,
        "v4RowsReferencableIn13Tables": False,
        "v4VolumesPublishable": False,
        "agentCitationSupported": False,
        "downloadSupported": False}
    return {**value, "resultDigest": digest(value)}
