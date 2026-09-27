"""Strict input, owner/scope and transaction gates shared only within the AI domain."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import datetime
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from sales.auth import Principal
from .control_models import AiDataRevision, AiWriteAuthority


class AiError(Exception):
    def __init__(self, message="AI 请求无效", code="invalid_request", status=400):
        super().__init__(message)
        self.code, self.status = code, status


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(
        (value if isinstance(value, str) else canonical(value)).encode()
    ).hexdigest()


def uid(prefix="ai"):
    return f"{prefix}-{uuid.uuid4()}"


def identifier(value, field="id"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise AiError(f"{field} 格式无效")
    return value


def text(value, field, maximum, *, empty=False):
    if not isinstance(value, str):
        raise AiError(f"{field} 必须是字符串")
    result = value.replace("\r\n", "\n").replace("\r", "\n").replace("\0", "").strip()
    if not empty and not result:
        raise AiError(f"{field} 不能为空")
    if len(result) > maximum or len(result.encode()) > maximum * 4:
        raise AiError(f"{field} 超出上限", "payload_too_large", 413)
    return result


def integer(value, field, lo=1, hi=2**53 - 1):
    if type(value) is not int or not lo <= value <= hi:
        raise AiError(f"{field} 必须在 {lo} 到 {hi} 之间")
    return value


def choice(value, values, field):
    if value not in values:
        raise AiError(f"{field} 无效")
    return value


def boolean(value, field):
    if type(value) is not bool:
        raise AiError(f"{field} 必须为布尔值")
    return int(value)


def fields(value, allowed, required=()):
    if (
        not isinstance(value, dict)
        or set(value) - set(allowed)
        or set(required) - set(value)
    ):
        raise AiError("请求字段无效或缺少必要字段")
    return value


def passive(value, maximum=24 * 1024, depth=0):
    if depth > 16:
        raise AiError("JSON 深度超限")
    if isinstance(value, dict):
        if len(value) > 100 or any(
            not isinstance(k, str) or k in {"__proto__", "constructor", "prototype"}
            for k in value
        ):
            raise AiError("JSON 字段无效")
        for v in value.values():
            passive(v, maximum, depth + 1)
    elif isinstance(value, list):
        if len(value) > 1000:
            raise AiError("JSON 数组超限")
        for v in value:
            passive(v, maximum, depth + 1)
    elif value is not None and type(value) not in (bool, int, float, str):
        raise AiError("JSON 值无效")
    try:
        encoded = canonical(value)
    except (ValueError, TypeError) as e:
        raise AiError("JSON 数值无效") from e
    if len(encoded.encode()) > maximum:
        raise AiError("JSON 大小超限", "payload_too_large", 413)
    return value


def valid_scope(value):
    return (
        value is None
        or isinstance(value, dict)
        and set(value) == {"warehouses", "channels", "platforms"}
        and all(
            isinstance(items, list)
            and len(items) <= 1000
            and all(isinstance(v, str) and v.strip() and len(v) <= 200 for v in items)
            for items in value.values()
        )
    )


def scope_covers(current, snapshot):
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except (ValueError, TypeError):
            return False
    if not valid_scope(current) or not valid_scope(snapshot):
        return False
    if current is None:
        return True
    if snapshot is None:
        return False
    return all(all(v in current[k] for v in snapshot[k]) for k in snapshot)


def current_principal(principal, *, write=False, admin=False, background=False):
    # The reserved local actor is only issued by the edge's build + exact-loopback gate.
    if principal.email.lower() == "local-admin@teruisi.local":
        if principal.role != "admin" or principal.scope is not None:
            raise AiError("当前账号不允许执行此操作", "access_denied", 403)
        if background:
            from .transport import edge

            result = edge(
                "authorize_background",
                {"ownerEmail": principal.email, "scopeJson": "null"},
                principal,
            )
            if (
                not result.get("ok")
                or result.get("principal", {}).get("role") != "admin"
            ):
                raise AiError("本地后台身份已失效", "access_denied", 403)
        current = principal
    else:
        from access_control.models import AppUser

        user = AppUser.objects.filter(
            email=principal.email.lower(), status="active"
        ).first()
        if (
            not user
            or user.role_id != principal.role
            or not scope_covers(user.scope, principal.scope)
        ):
            raise AiError("账号或数据权限已变化", "access_denied", 403)
        current = Principal(
            user.email, user.display_name, user.role_id, principal.scope
        )
    if (
        write
        and current.role == "viewer"
        or admin
        and (current.role != "admin" or current.scope is not None)
    ):
        raise AiError("当前角色或数据范围不允许此操作", "access_denied", 403)
    return current


def authorize_owner(row, principal, *, owner="owner_email", scope="scope_json"):
    if getattr(row, owner).lower() != principal.email.lower() or not scope_covers(
        principal.scope, getattr(row, scope, "null")
    ):
        raise AiError("记录不存在或不在当前授权范围", "not_found", 404)
    return row


def scope_filter(query, principal, *, field="scope_json"):
    if principal.scope is None:
        return query
    # Cast the immutable JSON text using the ORM. PostgreSQL JSONB containment
    # proves each stored dimension is a subset; empty arrays remain restrictive.
    from django.db.models import JSONField
    from django.db.models.functions import Cast

    if connection.vendor == "postgresql":
        query = query.annotate(_ai_scope=Cast(field, JSONField()))
        return query.filter(
            _ai_scope__contained_by=principal.scope,
            _ai_scope__has_keys=["warehouses", "channels", "platforms"],
        )
    # SQLite tests/dev use a registered deterministic function for identical semantics.
    connection.ensure_connection()
    function = "ai_scope_covers"
    connection.connection.create_function(
        function,
        2,
        lambda raw, scope: int(scope_covers(json.loads(scope), raw)),
        deterministic=True,
    )
    from django.db.models import BooleanField, F, Func, Value

    return query.annotate(
        _ai_visible=Func(
            F(field),
            Value(canonical(principal.scope)),
            function=function,
            output_field=BooleanField(),
        )
    ).filter(_ai_visible=True)


def owned(query, principal, *, owner="owner_email", field="scope_json"):
    return scope_filter(
        query.filter(**{owner + "__iexact": principal.email}), principal, field=field
    )


def revision():
    return str(AiDataRevision.objects.get(domain="ai-assistant").revision)


def authority():
    if settings.DJANGO_PROCESS_ROLE == "development":
        return
    row = AiWriteAuthority.objects.filter(id=1).first()
    if (
        not row
        or row.status != "postgres"
        or str(row.authority_epoch) != settings.AI_WRITE_AUTHORITY_EPOCH
        or row.cutover_id != settings.AI_WRITE_CUTOVER_ID
    ):
        raise AiError("AI 写入权威未激活", "service_unavailable", 503)


_mutation_depth = ContextVar("ai_mutation_depth", default=0)
_mutation_writes = ContextVar("ai_mutation_writes", default=None)


@contextmanager
def mutation(principal=None, *, background=False, audit_only=False):
    if settings.DJANGO_PROCESS_ROLE not in {"development", "ai_writer"}:
        raise AiError("此进程不允许 AI 写入", "access_denied", 403)
    # Remote background identity checks must finish before taking the domain lock.
    if principal and background:
        current_principal(principal, write=not audit_only, background=True)
    with transaction.atomic():
        row = AiDataRevision.objects.select_for_update().get(domain="ai-assistant")
        authority()
        if principal:
            current_principal(principal, write=not audit_only)
        depth = _mutation_depth.get()
        token = _mutation_depth.set(depth + 1)
        writes = _mutation_writes.get() if depth else [0]
        write_token = _mutation_writes.set(writes)
        prior = writes[0]

        def track(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if (
                re.match(r'^\s*(?:INSERT INTO|UPDATE|DELETE FROM)\s+"?ai_', sql, re.I)
                and context["cursor"].rowcount > 0
            ):
                writes[0] += 1
            return result

        if (
            depth == 0
            and connection.vendor == "postgresql"
            and settings.DJANGO_PROCESS_ROLE == "ai_writer"
        ):
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('teruisi.ai_epoch',%s,true),set_config('teruisi.ai_cutover',%s,true)",
                    [settings.AI_WRITE_AUTHORITY_EPOCH, settings.AI_WRITE_CUTOVER_ID],
                )
        try:
            with connection.execute_wrapper(track) if depth == 0 else nullcontext():
                yield row
            if depth == 0 and writes[0]:
                from django.db.models import F

                AiDataRevision.objects.filter(pk=row.pk).update(
                    revision=F("revision") + 1, updated_at=timezone.now()
                )
        except Exception:
            writes[0] = prior
            raise
        finally:
            _mutation_depth.reset(token)
            _mutation_writes.reset(write_token)


def cas(row, expected):
    if row.version != integer(expected, "expectedVersion"):
        raise AiError("版本已变化，请刷新后重试", "version_conflict", 409)


def iso(value):
    return (
        value.isoformat().replace("+00:00", "Z")
        if isinstance(value, datetime)
        else value
    )


def camel(value):
    head, *tail = value.split("_")
    return head + "".join(v.title() for v in tail)


def record(row, names, *, json_fields=(), bool_fields=()):
    result = {}
    for name in names.split():
        value = getattr(row, name)
        key = camel(name[:-5] if name in json_fields else name)
        result[key] = (
            json.loads(value)
            if name in json_fields and value is not None
            else bool(value)
            if name in bool_fields
            else iso(value)
        )
    return result


def page(query, params, *, maximum=50, default=30, mapper=lambda x: x, batch=None):
    def number(name, default, limit):
        raw = params.get(name, str(default))
        if isinstance(raw, str) and re.fullmatch(r"[1-9]\d{0,6}", raw):
            raw = int(raw)
        return integer(raw, name, 1, limit)

    index, size = number("page", 1, 10000), number("pageSize", default, maximum)
    count = query.count()
    rows = list(query[(index - 1) * size : index * size])
    return {
        "items": batch(rows) if batch else [mapper(row) for row in rows],
        "pagination": {
            "page": index,
            "pageSize": size,
            "total": count,
            "returned": len(rows),
            "truncated": count > len(rows),
            "hasMore": index * size < count,
        },
    }
