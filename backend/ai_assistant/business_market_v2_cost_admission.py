"""Default-closed market model cost requirement, never a paid-call grant."""
import json
from datetime import datetime, timezone

from django.conf import settings
from django.db import connection, transaction

from access_control.models import AppUser
from . import business_market_v2_cost_candidate as contract
from . import business_market_v2_execution_plan as plan_service
from . import models as m
from .policy import AiError, canonical, current_principal, identifier


ROLE = "teruisi_ai_market_cost_attestor"


def prepare(execution_report_id, tariff, jobs, cap_claim_cents,
            approval_claim_digest, principal, *, at_utc=None,
            chargeable_tools=False):
    """Reader-side arithmetic only; CNY source/approval remain unverified."""
    if getattr(settings,"AI_MARKET_V2_COST_CANDIDATE_ENABLED",False) is not True:
        raise AiError("市场模型费用候选尚未启用","market_cost_disabled",409)
    execution_report_id=identifier(execution_report_id,"executionReportId")
    current_principal(principal,admin=True)
    plan=plan_service.read(execution_report_id,principal)
    model_id=tariff.get("modelId") if type(tariff) is dict else None
    model=m.AiModels.objects.filter(pk=model_id).values(
        "id", "version", "status", "model_type", "protocol", "max_tokens",
        "max_tool_rounds", "max_total_tool_calls").first()
    if model is None:
        raise AiError("模型配置或费率缺失","conflict",409)
    fixed={"id":model["id"],"version":model["version"],
        "status":model["status"],"modelType":model["model_type"],
        "protocol":model["protocol"],"maxTokens":model["max_tokens"],
        "maxToolRounds":model["max_tool_rounds"],
        "maxTotalToolCalls":model["max_total_tool_calls"]}
    at_utc=at_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result=contract.build(plan["planId"],fixed,tariff,jobs,at_utc=at_utc,
        cap_claim_cents=cap_claim_cents,
        approval_claim_digest=approval_claim_digest,
        chargeable_tools=chargeable_tools)
    return {"candidate":result,"candidateJson":canonical(result),
        "providerCallsAllowed":False,"fundsReserved":False}


def record(plan_id,candidate_json):
    """Only an independent NOLOGIN role can atomically record required/zero."""
    if getattr(settings,"AI_MARKET_V2_COST_CANDIDATE_ENABLED",False) is not True:
        raise AiError("市场模型费用候选尚未启用","market_cost_disabled",409)
    if (type(plan_id) is not str or len(plan_id)!=64
            or any(char not in "0123456789abcdef" for char in plan_id)
            or type(candidate_json) is not str
            or len(candidate_json.encode("utf-8"))>65536):
        raise AiError("费用候选输入无效","invalid_request",400)
    with transaction.atomic(),connection.cursor() as cursor:
        cursor.execute("SELECT session_user")
        if cursor.fetchone()[0]!=ROLE:
            raise AiError("费用候选只能由独立证明角色提交","access_denied",403)
        cursor.execute("SELECT public.ai_market_v2_record_cost_candidate(%s,%s)",
            [plan_id,candidate_json])
        row_id=cursor.fetchone()[0]
    return {"ledgerId":row_id,"planId":plan_id,
        "reservedCents":0,"providerCallsAllowed":False}


def read(plan_id,principal):
    current_principal(principal,admin=True)
    row=AppUser.objects.filter(email=principal.email.lower(),status="active").first()
    if row is None:
        raise AiError("费用候选账号失效","access_denied",403)
    with connection.cursor() as cursor:
        cursor.execute("SELECT public.ai_market_v2_cost_candidate_receipt(%s,%s,%s)",
            [plan_id,row.email,row.version])
        raw=cursor.fetchone()[0]
    value=json.loads(raw) if type(raw) is str else raw
    if (type(value) is not dict or value.get("planId")!=plan_id
            or value.get("ledgerReservedCents")!=0
            or value.get("providerCallsAllowed") is not False
            or value.get("status")!="pending_rate_and_approval_verification"):
        raise AiError("费用候选窄回执不是关闭状态","conflict",409)
    return value
