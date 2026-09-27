"""Independent SQL-derived context proof; no Agent execution authority."""
import json

from django.db import connection, transaction

from access_control.models import AppUser
from . import business_market_v2_context_contract as contract
from .policy import AiError, current_principal, identifier


ROLE = "teruisi_ai_market_context_attestor"


def attest(report_id):
    """Call only from the independent NOLOGIN attestor session."""
    report_id = identifier(report_id, "reportId")
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT session_user")
        if cursor.fetchone()[0] != ROLE:
            raise AiError("市场 context 证明仅能由独立证明角色提交", "access_denied", 403)
        cursor.execute("SELECT public.ai_market_v2_attest_context(%s)", [report_id])
        proof_digest = cursor.fetchone()[0]
    return {"reportId": report_id, "proofDigest": proof_digest,
        "proofPersisted": True, "agentReadPersisted": False,
        "executionReady": False}


def read(report_id, principal):
    """Return only the SQL-owned owner/selector/context receipt."""
    report_id = identifier(report_id, "reportId")
    actor = current_principal(principal, admin=True)
    row = AppUser.objects.filter(email=actor.email.lower(), status="active").first()
    if row is None:
        raise AiError("市场 context 证明账号失效", "access_denied", 403)
    with connection.cursor() as cursor:
        cursor.execute("SELECT public.ai_market_v2_context_receipt(%s,%s,%s)",
            [report_id, actor.email.lower(), row.version])
        raw = cursor.fetchone()[0]
    value = json.loads(raw) if type(raw) is str else raw
    return contract.receipt(value, execution_report_id=report_id,
        owner_email=actor.email.lower(), context_digest=value["contextDigest"])
