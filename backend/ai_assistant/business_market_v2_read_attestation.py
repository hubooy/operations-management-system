"""Internal SQL-owned read-receipt entry; 0060 still makes it ineligible."""
import json

from django.db import connection, transaction

from access_control.models import AppUser
from . import business_market_v2_read_receipt_contract as contract
from .policy import AiError, current_principal, identifier


ROLE = "teruisi_ai_market_read_attestor"


def attest(tool_dispatch_id):
    """Record only an independently observed generic job/provider/tool chain."""
    tool_dispatch_id = identifier(tool_dispatch_id, "toolDispatchId")
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT session_user")
        if cursor.fetchone()[0] != ROLE:
            raise AiError("市场已读证明仅能由独立角色提交", "access_denied", 403)
        cursor.execute("SELECT public.ai_market_v2_attest_read(%s)",
            [tool_dispatch_id])
        receipt_digest = cursor.fetchone()[0]
    return {"toolDispatchId": tool_dispatch_id,
        "receiptDigest": receipt_digest, "numericCitationAllowed": False,
        "agentExecutionAuthorized": False}


def read(tool_dispatch_id, principal):
    """No row can pass until a later explicit activation creates real jobs."""
    tool_dispatch_id = identifier(tool_dispatch_id, "toolDispatchId")
    actor = current_principal(principal, admin=True)
    row = AppUser.objects.filter(email=actor.email.lower(), status="active").first()
    if row is None:
        raise AiError("市场已读证明账号失效", "access_denied", 403)
    with connection.cursor() as cursor:
        cursor.execute("SELECT public.ai_market_v2_read_receipt(%s,%s,%s)",
            [tool_dispatch_id, actor.email.lower(), row.version])
        raw = cursor.fetchone()[0]
    value = json.loads(raw) if type(raw) is str else raw
    fixed = contract.receipt(value)
    if fixed["ownerEmail"] != actor.email.lower():
        raise AiError("市场已读证明不属于当前账号", "access_denied", 403)
    return fixed
