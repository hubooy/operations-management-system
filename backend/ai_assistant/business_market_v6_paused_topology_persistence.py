"""Isolated, default-closed client for SQL-owned paused v6 topology.

This process never calls a provider or tool. An unknown CREATE/CANCEL reply is
observed through OUTCOME; it is never automatically repeated.
"""
from __future__ import annotations

import os

from django.conf import settings

from business_analysis.contracts import canonical, digest

from . import business_market_v2_execution_snapshot_contract as execution
from . import business_market_v6_paused_topology_contract as contract
from .business_market_v6_paused_topology_sql import ROLE
from .policy import AiError


CREATE = "SELECT public.ai_market_v6_create_paused_topology(%s,%s,%s)"
CANCEL = "SELECT public.ai_market_v6_cancel_paused_topology(%s)"
OUTCOME = "SELECT public.ai_market_v6_paused_topology_outcome(%s,%s,%s,%s)"


def _closed(db, port: int):
    if (getattr(settings, "AI_MARKET_V6_PAUSED_TOPOLOGY_RECORD_ENABLED",
            False) is not True or settings.DJANGO_ENVIRONMENT != "test"
            or os.getenv("TERUISI_DJANGO_ENVIRONMENT") != "test"
            or type(port) is not int or not 55440 <= port <= 55999
            or getattr(db, "autocommit", None) is not True):
        raise AiError("市场 v6 持久暂停拓扑未启用",
            "market_v6_paused_topology_disabled", 409)
    with db.cursor() as cursor:
        cursor.execute("SELECT session_user,current_user,"
            "(SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=session_user),"
            "current_database(),COALESCE(inet_server_addr()::text,''),"
            "inet_server_port(),"
            "(SELECT r.rolcanlogin AND NOT r.rolinherit AND NOT r.rolsuper "
            "AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND "
            "NOT r.rolreplication AND NOT r.rolbypassrls FROM "
            "pg_catalog.pg_roles r WHERE r.rolname=session_user),"
            "(SELECT count(*) FROM pg_catalog.pg_auth_members m WHERE "
            "m.roleid=session_user::regrole OR m.member=session_user::regrole)")
        row = cursor.fetchone()
    if (row is None or row[:4] != (ROLE, ROLE, False,
            "test_teruisi_ai_rehearsal") or row[4] not in
            ("127.0.0.1", "127.0.0.1/32", "::1", "::1/128")
            or row[5:] != (port, True, 0)):
        raise AiError("市场 v6 签发连接身份不符", "access_denied", 403)


def _one(db, statement, params):
    with db.cursor() as cursor:
        cursor.execute(statement, params)
        rows = cursor.fetchmany(2)
    if len(rows) != 1 or len(rows[0]) != 1 or type(rows[0][0]) is not dict:
        raise AiError("市场 v6 受保护结果形状不符", "conflict", 409)
    return contract.outcome(rows[0][0])


def _unknown(built, phase):
    snapshot = built["snapshot"]
    return {"schemaVersion": contract.OUTCOME_SCHEMA, "status": "unknown",
        "phase": phase, "reportId": snapshot["reportId"],
        "workflowId": snapshot["workflowId"],
        "ownerEmail": snapshot["ownerEmail"],
        "clientRequestId": snapshot["clientRequestId"],
        "requestDigest": built["intentDigest"], "modelId": "",
        "providerCallsAllowed": False, "agentReadPersisted": False,
        "numericCitationAllowed": False, "durablePaidReservation": False,
        "retryAllowed": False, "candidateOnly": True}


def create_once(db, built: dict, *, port: int):
    _closed(db, port)
    if (type(built) is not dict or built.get("candidateOnly") is not True
            or built.get("providerCallsAllowed") is not False
            or built.get("intentJson") != canonical(built.get("intent"))
            or built.get("snapshotJson") != canonical(built.get("snapshot"))
            or built.get("intentDigest") != digest(built["intent"])
            or built.get("snapshotDigest") != digest(built["snapshot"])):
        raise AiError("市场 v6 待写入证明不规范", "conflict", 409)
    graph = execution.graph(built["snapshot"]["withBudget"])
    graph_text = canonical(graph)
    if (digest(graph) != built["snapshot"]["graphDigest"] or
            not 1 <= len(graph_text.encode("utf-8")) <= 32768):
        raise AiError("市场 v6 固定图已变化", "conflict", 409)
    try:
        value = _one(db, CREATE, [built["intentJson"],
            built["snapshotJson"], graph_text])
    except Exception:
        return _unknown(built, "create_reply")
    if (value["status"] != "committed_paused" or value["reportId"] !=
            built["snapshot"]["reportId"] or value["requestDigest"] !=
            built["intentDigest"]):
        return _unknown(built, "create_shape")
    return {**value, "retryAllowed": False, "candidateOnly": True}


def cancel_once(db, request: dict, *, port: int):
    _closed(db, port)
    if (type(request) is not dict or request.get("candidateOnly") is not True
            or request.get("requestJson") != canonical(request.get("request"))
            or request.get("requestDigest") != digest(request["request"])):
        raise AiError("市场 v6 取消请求不规范", "conflict", 409)
    try:
        value = _one(db, CANCEL, [request["requestJson"]])
    except Exception:
        return {"status": "unknown", "phase": "cancel_reply",
            "reportId": request["request"]["reportId"],
            "ownerEmail": request["request"]["ownerEmail"],
            "requestDigest": request["requestDigest"],
            "retryAllowed": False, "candidateOnly": True}
    return {**value, "retryAllowed": False, "candidateOnly": True}


def outcome(db, *, owner_email: str, source_report_id: str,
            client_request_id: str, request_digest: str, port: int):
    _closed(db, port)
    value = _one(db, OUTCOME, [owner_email, source_report_id,
        client_request_id, request_digest])
    return {**value, "retryAllowed": False, "candidateOnly": True}
