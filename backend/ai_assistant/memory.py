import re
import unicodedata
from difflib import SequenceMatcher
from django.db.models import Q
from django.utils import timezone
from . import models as m
from .policy import (
    AiError,
    canonical,
    cas,
    choice,
    digest,
    fields,
    identifier,
    page,
    record,
    scope_filter,
    text,
    uid,
)

KINDS = ["preference", "glossary", "business_context"]
SECRET = re.compile(
    r"(?:password|passwd|pwd|api[ _-]?key|access[ _-]?token|authorization|cookie|secret|webhook|密码|口令|密钥|令牌|授权头)\s*[=:：]\s*\S+|\bBearer\s+\S{12,}|\bsk-\S{16,}|\beyJ\S{10,}\.\S+|https?://\S*(?:token|webhook|secret)\S*",
    re.I,
)
RAW = re.compile(
    r"客户聊天原文|完整聊天记录|原始客服会话|客户手机号|客户电话|收货地址|身份证号|银行卡号"
)
OVERRIDE = re.compile(
    r"(?:忽略|覆盖|绕过|取消|跳过).{0,24}(?:系统提示|既有指令|安全规则|权限|数据范围|人工确认|审计|工具限制)|(?:ignore|override|bypass|disable|skip).{0,48}(?:system prompt|previous instructions?|security|permission|scope|confirmation|audit|tool restriction)",
    re.I,
)
TIME = re.compile(
    r"20\d{2}[-/.年]|今天|今日|昨天|昨日|本周|上周|本月|上月|截至|截止|as of|today|yesterday|this (?:week|month)|last (?:week|month)",
    re.I,
)
METRIC = re.compile(
    r"gmv|销售额|销售量|销量|订单量|订单数|库存|金额|收入|利润|毛利率|转化率|访客数|成交量|\d+(?:\.\d+)?\s*(?:%|元|万元|件|单)",
    re.I,
)


def visible(principal):
    all_owner = m.AiMemoryEntries.objects.filter(
        owner_email__iexact=principal.email, status="active"
    )
    scoped = scope_filter(all_owner.filter(scope_mode="data_scope"), principal)
    return all_owner.filter(Q(scope_mode="owner") | Q(pk__in=scoped.values("pk")))


def mapping(row):
    result = record(
        row,
        "id kind content scope_mode status version source created_at updated_at archived_at",
    )
    result["key"] = row.memory_key
    return result


def get(memory_id, principal):
    row = visible(principal).filter(id=identifier(memory_id)).first()
    if not row:
        raise AiError("记忆不存在或不在当前范围", "not_found", 404)
    return row


def listing(params, principal):
    fields(params, {"page", "pageSize", "q", "kind"})
    query = visible(principal)
    if params.get("kind"):
        query = query.filter(kind=choice(params["kind"], KINDS, "kind"))
    if params.get("q"):
        q = text(params["q"], "q", 200)
        query = query.filter(Q(memory_key__icontains=q) | Q(content__icontains=q))
    return page(query.order_by("-updated_at", "id"), params, default=20, mapper=mapping)


def audit(row, principal, request_id, operation, before=None):
    op = uid("ai-memory-operation")
    m.AiMemoryAuditLogs.objects.create(
        id=uid("ai-memory-audit"),
        operation_id=op,
        request_id=request_id,
        memory_id=row.id,
        owner_email=principal.email.lower(),
        actor_role=principal.role,
        operation=operation,
        status="duplicate" if operation == "duplicate" else "succeeded",
        scope_digest=row.scope_digest,
        before_digest=before,
        after_digest=row.content_digest,
        result_version=row.version,
        policy_version="ai-memory-v1",
        gate_results_json=canonical(
            {
                "source": "explicit_confirmation",
                "sensitive": "passed",
                "exact": "duplicate" if operation == "duplicate" else "unique",
                "similarity": "low",
            }
        ),
    )
    row.last_operation_id = op
    row.save(update_fields=["last_operation_id"])


def save(body, principal, request_id, memory_id=None):
    fields(
        body,
        {"confirmed", "key", "content", "expectedVersion"}
        if memory_id
        else {"confirmed", "kind", "key", "content"},
        {"confirmed"} if memory_id else {"confirmed", "kind", "key", "content"},
    )
    if body["confirmed"] is not True:
        raise AiError("保存记忆需要用户明确确认")
    row = get(memory_id, principal) if memory_id else None
    if row:
        cas(row, body.get("expectedVersion"))
    kind = row.kind if row else choice(body["kind"], KINDS, "kind")
    key = re.sub(
        r"\s+",
        " ",
        text(
            unicodedata.normalize(
                "NFKC", body.get("key", row.memory_key if row else "")
            ),
            "key",
            80,
        ),
    )
    if not key[0].isalnum() or any(not (c.isalnum() or c in "_.:：- ") for c in key):
        raise AiError("记忆键包含不支持的字符")
    content = text(
        unicodedata.normalize("NFKC", body.get("content", row.content if row else "")),
        "content",
        2000,
    )
    candidate = key + "\n" + content
    if (
        SECRET.search(candidate)
        or RAW.search(candidate)
        or OVERRIDE.search(candidate)
        or TIME.search(candidate)
        and METRIC.search(candidate)
    ):
        raise AiError("记忆不得包含凭证、客户敏感信息、安全覆盖指令或时效指标")
    scope = (
        row.scope_json
        if row
        else canonical(principal.scope if kind == "business_context" else None)
    )
    mode = (
        row.scope_mode
        if row
        else "data_scope"
        if kind == "business_context"
        else "owner"
    )
    scope_digest = digest(scope) if mode == "data_scope" else "owner:v1"
    values = {
        "kind": kind,
        "memory_key": key,
        "memory_key_normalized": key.lower(),
        "content": content,
        "scope_mode": mode,
        "scope_json": scope,
        "scope_digest": scope_digest,
    }
    values["content_digest"] = digest(
        {
            "kind": kind,
            "key": key.lower(),
            "content": content,
            "scopeMode": mode,
            "scopeJson": scope,
        }
    )
    candidates = (
        visible(principal)
        .filter(kind=kind, scope_digest=scope_digest)
        .exclude(pk=row.pk if row else "")
    )
    exact = candidates.filter(memory_key_normalized=key.lower()).first()
    if exact:
        if exact.content != content:
            raise AiError("同一记忆键已有不同内容，请编辑原记录", "conflict", 409)
        audit(exact, principal, request_id, "duplicate")
        return {"item": mapping(exact), "created": False, "duplicate": True}
    for other in candidates.order_by("-updated_at")[:100]:
        if (
            SequenceMatcher(None, other.content, content, autojunk=False).ratio()
            >= 0.86
        ):
            raise AiError("已存在相似记忆，请编辑原记录", "conflict", 409)
    before = row.content_digest if row else None
    if row:
        for k, v in values.items():
            setattr(row, k, v)
        row.version += 1
        row.updated_at = timezone.now()
        row.save()
    else:
        row = m.AiMemoryEntries.objects.create(
            id=uid("ai-memory"),
            owner_email=principal.email.lower(),
            source="management_ui",
            last_operation_id="",
            **values,
        )
    audit(row, principal, request_id, "update" if memory_id else "create", before)
    return {"item": mapping(row), "created": memory_id is None, "duplicate": False}


def archive(memory_id, body, principal, request_id):
    fields(body, {"confirmed", "expectedVersion"}, {"confirmed", "expectedVersion"})
    if body["confirmed"] is not True:
        raise AiError("归档记忆需要明确确认")
    row = get(memory_id, principal)
    cas(row, body["expectedVersion"])
    row.status = "archived"
    row.archived_at = timezone.now()
    row.updated_at = timezone.now()
    row.version += 1
    row.save()
    audit(row, principal, request_id, "archive", row.content_digest)
    return {"id": row.id, "archived": True, "version": row.version}


def recall(prompt, principal):
    # Bound both candidates and context. This is user data, never system instructions.
    query = text(prompt, "query", 200, empty=True) if prompt is not None else ""
    matched = (
        visible(principal).filter(
            Q(memory_key__icontains=query) | Q(content__icontains=query)
        )
        if query
        else visible(principal).none()
    )
    total = matched.count()
    rows = list(matched.order_by("-updated_at", "id")[:8])
    items = []
    size = 0
    for row in rows[:8]:
        item = {
            k: v
            for k, v in mapping(row).items()
            if k in {"id", "kind", "key", "content", "version", "updatedAt"}
        }
        length = len(row.memory_key) + len(row.content)
        if size + length > 4000:
            break
        items.append(item)
        size += length
    return {
        "trust": "untrusted_memory_data",
        "items": items,
        "totalMatched": total,
        "returned": len(items),
        "truncated": total > len(items),
    }
