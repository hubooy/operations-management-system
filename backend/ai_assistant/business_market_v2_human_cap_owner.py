"""Authenticated AI-writer entry for an explicit, still non-spendable cap."""
from datetime import datetime, timezone
import hashlib
import json
import re

from django.conf import settings
from django.db import DatabaseError, connection

from access_control.models import AppUser
from . import business_market_v2_human_cap_contract as contract
from .policy import AiError, current_principal


SHA = re.compile(r"[0-9a-f]{64}\Z")
WRITER = "teruisi_ai_writer"


def _closed():
    if getattr(settings, "AI_MARKET_V2_HUMAN_CAP_ENABLED", False) is not True:
        raise AiError("市场单报告人民币上限审批尚未启用",
            "market_human_cap_disabled", 409)
    if settings.DJANGO_PROCESS_ROLE != "ai_writer":
        raise AiError("费用审批需要独立 AI writer", "access_denied", 403)
    with connection.cursor() as cursor:
        cursor.execute("SELECT session_user")
        if cursor.fetchone() != (WRITER,):
            raise AiError("费用审批数据库身份不符", "access_denied", 403)


def _actor(principal):
    current_principal(principal, admin=True, write=True)
    actor = AppUser.objects.filter(email=principal.email.lower(),
        status="active", role_id="admin", scope__isnull=True).values(
        "email", "version").first()
    if actor is None or type(actor["version"]) is not int or actor["version"] < 1:
        raise AiError("管理员账号或版本不可用", "access_denied", 403)
    return actor


def _id(value):
    if type(value) is not str or SHA.fullmatch(value) is None:
        raise AiError("费用账身份无效", "invalid_request", 400)
    return value


def _query(signature, args):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT " + signature + "(" +
                ",".join(["%s"] * len(args)) + ")", args)
            value = cursor.fetchone()[0]
    except DatabaseError as error:
        raise AiError("单报告费用上限当前不可核验", "conflict", 409) from error
    if type(value) is str:
        value = json.loads(value)
    if type(value) is not dict or value.get("providerCallsAllowed") is not False:
        raise AiError("费用上限回执未保持关闭", "conflict", 409)
    return value


def preview(ledger_id, principal):
    _closed()
    actor = _actor(principal)
    value = _query("public.ai_market_v2_human_cap_preview",
        [_id(ledger_id), actor["email"], actor["version"]])
    if value.get("ledgerId") != ledger_id or value.get("fundsReserved") is not False:
        raise AiError("费用上限预览身份漂移", "conflict", 409)
    _actor(principal)
    return value


def approve(ledger_id, payload, principal):
    _closed()
    actor = _actor(principal)
    ledger_id = _id(ledger_id)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        fixed = contract.request(payload, now_utc=now)
    except contract.HumanCapInputError as error:
        raise AiError(str(error), "invalid_request", 400) from error
    if payload["ledgerId"] != ledger_id:
        raise AiError("费用账与审批请求不一致", "conflict", 409)
    before = preview(ledger_id, principal)
    for name in ("ledgerId", "planId", "reportId", "ledgerDigest",
            "planDigest", "reportSnapshotDigest", "modelConfigDigest"):
        if payload[name] != before[name]:
            raise AiError("审批对象或来源版本已变化", "conflict", 409)
    if not (before["requiredClaimCents"] <= fixed["approvedCapCents"]
            <= before["maximumClaimCents"]):
        raise AiError("费用上限不在当前候选范围", "conflict", 409)
    value = _query("public.ai_market_v2_approve_human_cap",
        [ledger_id, fixed["canonicalJson"], actor["email"], actor["version"]])
    if (value.get("ledgerId") != ledger_id
            or value.get("requestDigest") != fixed["requestDigest"]
            or value.get("fundsReserved") is not False):
        raise AiError("费用审批回执与请求不一致", "conflict", 409)
    _actor(principal)
    return value


def revoke(ledger_id, payload, principal):
    _closed()
    actor = _actor(principal)
    if (type(payload) is not dict or set(payload) != {"approvalId", "reason"}
            or type(payload["approvalId"]) is not str
            or SHA.fullmatch(payload["approvalId"]) is None
            or type(payload["reason"]) is not str
            or not 1 <= len(payload["reason"].strip()) <= 500):
        raise AiError("撤销请求无效", "invalid_request", 400)
    current = outcome(ledger_id, principal)
    if current.get("approvalId") != payload["approvalId"]:
        raise AiError("审批回执与费用账不一致", "conflict", 409)
    reason_digest = hashlib.sha256(payload["reason"].strip().encode(
        "utf-8")).hexdigest()
    value = _query("public.ai_market_v2_revoke_human_cap",
        [payload["approvalId"], actor["email"], actor["version"],
         reason_digest])
    if value.get("approvalId") != payload["approvalId"]:
        raise AiError("撤销回执不一致", "conflict", 409)
    _actor(principal)
    return value


def outcome(ledger_id, principal):
    _closed()
    actor = _actor(principal)
    value = _query("public.ai_market_v2_human_cap_outcome",
        [_id(ledger_id), actor["email"], actor["version"]])
    if (value.get("ledgerId") != ledger_id
            or value.get("fundsReserved") is not False):
        raise AiError("费用审批状态回执无效", "conflict", 409)
    _actor(principal)
    return value
