"""Test-only owner read of current non-secret model transport settings.

The AI reader and current unscoped administrator must both be present.  Only
explicit projected columns are queried; API key ciphertext/suffix are never
read.  This observes a model row, not its credential account or a price.
"""
from django.conf import settings
from django.db import DatabaseError, connection

from business_analysis.contracts import AnalysisContractError

from . import business_market_v2_rate_source_v1_contract as contract
from . import models as m
from .policy import AiError, current_principal, identifier


SCHEMA = "business-market-v2-model-transport-owner-read-v1"
READER = "teruisi_ai_reader"
# A values() projection must remain explicit.  Instantiating AiModels would
# SELECT api_key_encrypted and api_key_suffix, including on a reader connection.
DB_FIELDS = ("id", "version", "status", "model_type", "protocol",
    "model_name", "base_url", "generation_options_json", "max_tokens",
    "max_tool_rounds", "max_total_tool_calls", "timeout_ms",
    "reasoning_mode", "temperature_milli")
PROJECT = {"id": "id", "version": "version", "status": "status",
    "model_type": "modelType", "protocol": "protocol",
    "model_name": "modelName", "base_url": "baseUrl",
    "generation_options_json": "generationOptionsJson",
    "max_tokens": "maxTokens", "max_tool_rounds": "maxToolRounds",
    "max_total_tool_calls": "maxTotalToolCalls",
    "timeout_ms": "timeoutMs", "reasoning_mode": "reasoningMode",
    "temperature_milli": "temperatureMilli"}
assert set(DB_FIELDS) == set(PROJECT)


def _session():
    with connection.cursor() as cursor:
        cursor.execute("SELECT session_user")
        if cursor.fetchone() != (READER,):
            raise AiError("市场模型传输指纹需要独立 AI reader 连接",
                "market_model_transport_reader_required", 403)


def _row(model_id):
    return m.AiModels.objects.filter(pk=model_id).values(*DB_FIELDS).first()


def read(model_id, principal):
    """Double-read one exact model projection; never provide call authority."""
    if (getattr(settings, "AI_MARKET_V2_MODEL_TRANSPORT_OWNER_ENABLED", False)
            is not True or settings.DJANGO_ENVIRONMENT != "test"):
        raise AiError("市场模型传输拥有方读取未启用",
            "market_model_transport_owner_disabled", 409)
    model_id = identifier(model_id, "modelId")
    actor = current_principal(principal, admin=True)
    _session()
    try:
        before = _row(model_id)
        if before is None:
            raise AiError("当前模型不存在", "market_model_transport_unavailable", 409)
        projection = {external: before[internal]
            for internal, external in PROJECT.items()}
        fixed = contract.model_transport(projection)
        after = _row(model_id)
        current_principal(principal, admin=True)
        _session()
        if (after != before or actor.email.lower() != principal.email.lower()
                or fixed["model"]["id"] != model_id
                or fixed["model"]["version"] != before["version"]):
            raise AiError("模型传输配置在复核期间变化",
                "market_model_transport_changed", 409)
        return {"schemaVersion": SCHEMA, "modelId": model_id,
            "modelVersion": before["version"],
            "transport": fixed["model"],
            "transportFingerprint": fixed["fingerprint"],
            "currentModelOwnedRead": True,
            "nonSecretColumnsDoubleChecked": True,
            "credentialAccountVerified": False,
            "providerIdentityVerified": False,
            "rateSourceIndependentlyVerified": False,
            "humanCapApproved": False,
            "providerCallsAllowed": False}
    except (AnalysisContractError, DatabaseError, KeyError, TypeError,
            ValueError) as error:
        raise AiError("当前模型传输配置不可核验",
            "market_model_transport_unverified", 409) from error
