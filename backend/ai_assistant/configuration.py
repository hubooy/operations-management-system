from __future__ import annotations
import ipaddress
import os
from urllib.parse import urlsplit, urlunsplit
from django.conf import settings
from django.utils import timezone
from . import models as m
from .policy import (
    AiError,
    boolean,
    cas,
    choice,
    current_principal,
    fields,
    identifier,
    integer,
    record,
    text,
    uid,
)
from .secrets import encrypt
from . import model_capabilities as capabilities


def origin(value):
    url = urlsplit(value)
    return url.scheme + "://" + url.netloc.lower()


def endpoint(value):
    raw = text(value, "API 地址", 1000)
    try:
        url = urlsplit(raw)
        host = (url.hostname or "").lower()
        _port = url.port
    except ValueError as error:
        raise AiError("API 地址无效") from error
    if (
        url.username
        or url.password
        or url.query
        or url.fragment
        or not host
        or "%" in host
        or "\\" in raw
    ):
        raise AiError("API 地址不能包含凭证、查询、片段或歧义主机")
    local = host in {"localhost", "127.0.0.1", "::1"}
    local_allowed = (
        settings.DJANGO_ENVIRONMENT == "development"
        and os.getenv("AI_ALLOW_LOCAL_MODEL_ENDPOINTS") == "true"
    )
    if url.scheme != "https" and not (url.scheme == "http" and local and local_allowed):
        raise AiError("模型地址必须使用 HTTPS")
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global and not (local and local_allowed):
            raise AiError("禁止私网模型地址")
    except ValueError:
        pass
    if (
        host.endswith((".local", ".localhost", ".internal")) or "." not in host
    ) and not (local and local_allowed):
        raise AiError("模型主机无效")
    allowed = {"https://api.openai.com"} | {
        v.strip()
        for v in os.getenv("AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST", "").split(",")
        if v.strip()
    }
    if not (local and local_allowed) and origin(raw) not in allowed:
        raise AiError("模型来源未进入精确 origin 白名单")
    return urlunsplit((url.scheme, url.netloc, url.path.rstrip("/"), "", ""))


# List ORM queries must not fetch model credential ciphertext.  The admin
# settings and internal model-list now run on the writer process, but still
# use only their exact response fields; reader-side chat list is smaller.
MODEL_AVAILABLE_COLUMNS = ("id", "name", "protocol", "model_type",
    "model_name", "is_default_text_model")
MODEL_ADMIN_COLUMNS = ("id", "version", "name", "protocol", "model_type",
    "model_name", "base_url", "api_key_suffix", "is_default_text_model",
    "status", "timeout_ms", "max_tokens", "reasoning_mode",
    "temperature_milli", "max_tool_rounds", "max_total_tool_calls",
    "last_test_result", "last_tested_at", "created_at", "updated_at",
    "generation_options_json")
MODEL_RUNTIME_READER_COLUMNS = tuple(column for column in MODEL_ADMIN_COLUMNS
    if column not in {"api_key_suffix", "last_test_result"})


def model_record(row, *, available=False):
    if available:
        result = record(row, "id name protocol model_type model_name")
        result["isDefault"] = bool(row.is_default_text_model)
        return result
    result = record(
        row,
        "id version name protocol model_type model_name base_url api_key_suffix is_default_text_model status timeout_ms max_tokens reasoning_mode temperature_milli max_tool_rounds max_total_tool_calls last_test_result last_tested_at created_at updated_at",
        bool_fields={"is_default_text_model"},
    )
    result["generationOptions"] = capabilities.options(row)
    return result


def resolve_model(model_id=None):
    query = m.AiModels.objects.filter(
        status="enabled", model_type__in=["text", "vision"]
    )
    if settings.DJANGO_PROCESS_ROLE == "ai_reader":
        # Report verification needs model options but never credential bytes.
        # Provider calls remain on ai_writer with the complete model row.
        query = query.only(*MODEL_RUNTIME_READER_COLUMNS)
    row = (
        query.filter(id=identifier(model_id)).first()
        if model_id
        else query.order_by("-is_default_text_model", "created_at", "id").first()
    )
    if not row:
        raise AiError("尚未配置可用的对话模型", "not_found", 404)
    endpoint(row.base_url)
    return row


def save_model(body, principal, *, image=False):
    current_principal(principal, write=True, admin=True)
    allowed = {
        "id",
        "expectedVersion",
        "name",
        "modelName",
        "baseUrl",
        "apiKey",
        "status",
        "timeoutMs",
    }
    if not image:
        allowed |= {
            "protocol",
            "modelType",
            "isDefaultTextModel",
            "maxTokens",
            "reasoningMode",
            "temperatureMilli",
            "maxToolRounds",
            "maxTotalToolCalls",
            "generationOptions",
        }
    fields(body, allowed, {"name", "modelName"})
    cls = m.AiSpaceModelProfiles if image else m.AiModels
    row = (
        cls.objects.filter(id=identifier(body["id"])).first() if "id" in body else None
    )
    if "id" in body and not row:
        raise AiError("配置不存在", "not_found", 404)
    before = profile_record(row) if row and image else None
    if row:
        cas(row, body.get("expectedVersion"))
        active = (
            m.AiSpaceJobs.objects.filter(
                model_profile_id=row.id, status__in=["queued", "running"]
            ).exists()
            if image
            else m.AiAgentJobs.objects.filter(
                model_id=row.id, status__in=["queued", "running", "paused"]
            ).exists()
        )
        if active:
            raise AiError("模型仍有活动任务，请等待或取消后再修改", "conflict", 409)
    url = endpoint(body.get("baseUrl", row.base_url if row else ""))
    protocol = (
        "openai_images"
        if image
        else choice(body.get("protocol"), ["openai_compatible", "anthropic"], "协议")
    )
    key = text(body.get("apiKey", ""), "API Key", 2000, empty=True)
    if (
        row
        and not key
        and (origin(row.base_url) != origin(url) or row.protocol != protocol)
    ):
        raise AiError("更换服务 origin 或协议必须提供新 API Key")
    if not row and not key:
        raise AiError("新模型必须提供 API Key")
    values = {
        "name": text(body["name"], "名称", 100),
        "model_name": text(body["modelName"], "模型标识", 120),
        "base_url": url,
        "protocol": protocol,
        "status": choice(
            body.get("status", "enabled"), ["enabled", "disabled"], "状态"
        ),
        "timeout_ms": integer(
            body.get("timeoutMs", row.timeout_ms if row else 90000 if image else 60000), "timeoutMs", 3000, 120000 if image else 600000
        ),
        "updated_at": timezone.now(),
        "version": row.version + 1 if row else 1,
    }
    if key:
        values.update(api_key_encrypted=encrypt(key), api_key_suffix=key[-4:])
    if not image:
        values.update(
            model_type=choice(body.get("modelType"), ["text", "vision"], "能力类型"),
            is_default_text_model=boolean(
                body.get("isDefaultTextModel", False), "isDefaultTextModel"
            ),
            max_tokens=integer(body.get("maxTokens", row.max_tokens if row else 65536), "maxTokens", 128, 131072),
            reasoning_mode=choice(
                body.get("reasoningMode", "auto"), ["auto", "disabled"], "推理模式"
            ),
            temperature_milli=integer(
                body.get("temperatureMilli", 200), "temperatureMilli", 0, 1000 if protocol == "anthropic" else 2000
            ),
            max_tool_rounds=integer(
                body.get("maxToolRounds", 6), "maxToolRounds", 1, 62
            ),
            max_total_tool_calls=integer(
                body.get("maxTotalToolCalls", 12), "maxTotalToolCalls", 1, 300
            ),
        )
        from .policy import canonical
        values["generation_options_json"] = canonical(capabilities.validate(
            body.get("generationOptions", capabilities.options(row) if row else {}),
            protocol=protocol, max_tokens=values["max_tokens"], reasoning_mode=values["reasoning_mode"]))
        if values["is_default_text_model"]:
            if values["status"] != "enabled" or values["model_type"] != "text":
                raise AiError("默认文本模型必须启用且为文本能力")
            m.AiModels.objects.filter(is_default_text_model=1).exclude(
                id=row.id if row else ""
            ).update(is_default_text_model=0)
    if row:
        for k, v in values.items():
            setattr(row, k, v)
        row.save()
    else:
        row = cls.objects.create(
            id=uid("ai-space-profile" if image else "ai-model"), **values
        )
    after = profile_record(row) if image else model_record(row)
    if image:
        from .space import admin_audit

        admin_audit(principal, "upsert_profile", "model_profile", row.id, before, after)
    return after


def profile_record(row):
    return record(
        row,
        "id name protocol model_name base_url api_key_suffix status version timeout_ms last_success_result last_success_at created_at updated_at",
    )


def delete_model(params, principal, *, image=False):
    current_principal(principal, write=True, admin=True)
    fields(params, {"id", "expectedVersion"}, {"id", "expectedVersion"})
    cls = m.AiSpaceModelProfiles if image else m.AiModels
    row = cls.objects.filter(id=identifier(params["id"])).first()
    if not row:
        return {"ok": True, "deleted": False}
    version = params["expectedVersion"]
    if isinstance(version, str) and version.isdigit():
        version = int(version)
    cas(row, version)
    # Existing records reference model identities, including immutable execution ledgers.
    if image:
        used = (
            m.AiSpaceJobs.objects.filter(model_profile_id=row.id).exists()
            or m.AiSpaceTemplates.objects.filter(model_profile_id=row.id).exists()
        )
    else:
        used = (
            m.AiAgentJobs.objects.filter(model_id=row.id).exists()
            or m.AiConversations.objects.filter(model_id=row.id).exists()
            or m.AiWorkflowRuns.objects.filter(model_id=row.id).exists()
        )
    if used:
        raise AiError("配置仍被历史或活动记录引用，可将其停用", "conflict", 409)
    if image:
        from .space import admin_audit

        admin_audit(
            principal,
            "delete_profile",
            "model_profile",
            row.id,
            profile_record(row),
            None,
        )
    row.delete()
    return {"ok": True, "deleted": True}
