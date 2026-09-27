"""Isolated SQL-only persistence of a synthetic same-job/provider chain."""
import json

from django.conf import settings
from django.db import connection, transaction

from .policy import AiError


ROLE = "teruisi_ai_market_synthetic_attestor"


def create(plan_id):
    """No model client, network request, Agent resume, or read grant."""
    if (getattr(settings, "AI_MARKET_V2_SYNTHETIC_ENABLED", False) is not True
            or settings.DJANGO_ENVIRONMENT != "test"):
        raise AiError("市场合成执行仅能在隔离测试启用", "synthetic_disabled", 409)
    if (type(plan_id) is not str or len(plan_id) != 64
            or any(char not in "0123456789abcdef" for char in plan_id)):
        raise AiError("市场合成计划 ID 无效", "invalid_request", 400)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT session_user")
        if cursor.fetchone()[0] != ROLE:
            raise AiError("市场合成执行只能由独立证明角色创建", "access_denied", 403)
        cursor.execute("SELECT public.ai_market_v2_create_synthetic_chain(%s)",
            [plan_id])
        raw = cursor.fetchone()[0]
    value = json.loads(raw) if type(raw) is str else raw
    if (type(value) is not dict or value.get("syntheticOnly") is not True
            or value.get("externalProviderCalled") is not False
            or value.get("persistedRead") is not False
            or value.get("numericCitationAllowed") is not False
            or value.get("paidCostCents") != 0):
        raise AiError("合成持久链回执无效", "conflict", 409)
    return value
