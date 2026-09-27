"""Read-only, parameterized ORM queries over the committed column manifest."""
import base64
import hashlib
import json
import os
import secrets
import re
import time
import math
from urllib.parse import urlsplit, urlunsplit
from contextlib import contextmanager
from datetime import date, datetime, time as datetime_time
from decimal import Decimal
from uuid import UUID
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.apps import apps
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q, Subquery
from django.http import JsonResponse
from django.utils import timezone
from sales.auth import verify_principal, PrincipalEnvelopeError
from ai_assistant.policy import AiError, canonical, digest, fields, passive
from .catalog import SPECS, BY_MODEL, DOMAINS, authorize

PATH = "/api/ai/dataset-records"


def _model(spec):
    model = apps.get_model(spec["domain"], spec["model"])
    if model._meta.db_table != spec["table"]:
        raise AiError("数据集模型契约已变化", "service_unavailable", 503)
    return model


def _owned(spec, principal):
    queryset = _model(spec).objects.all()
    owner = spec.get("owner")
    if owner:
        if owner.get("parent"):
            parent = BY_MODEL[(spec["domain"], owner["parent"])]
            queryset = queryset.filter(**{owner["field"] + "__in": Subquery(
                _owned(parent, principal).values(owner["parentKey"]))})
        else:
            queryset = queryset.filter(**{owner["field"] + "__iexact": principal.email})
    return queryset


def _cipher():
    secret = os.getenv("TERUISI_DJANGO_INTERNAL_SECRET", "")
    if len(secret.encode()) < 32:
        raise AiError("数据集游标签名未配置", "service_unavailable", 503)
    return AESGCM(hashlib.sha256(("system-datasets-cursor-v1:" + secret).encode()).digest())


def _encode(value):
    nonce = secrets.token_bytes(12)
    return base64.urlsafe_b64encode(nonce + _cipher().encrypt(nonce, canonical(value).encode(), b"datasets-v1")).decode()


def _decode(raw):
    if not isinstance(raw, str) or not raw or len(raw) > 8000:
        raise AiError("数据集游标无效")
    try:
        binary = base64.b64decode(raw, altchars=b"-_", validate=True)
        value = json.loads(_cipher().decrypt(binary[:12], binary[12:], b"datasets-v1"))
        if not isinstance(value, dict) or not 0 <= time.time() - value["issued"] <= 1800:
            raise ValueError()
        return value
    except AiError:
        raise
    except Exception as error:
        raise AiError("游标无效、过期或已被修改") from error


def scalar(value):
    if isinstance(value, (date, datetime, datetime_time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if type(value) is int and abs(value) > 2**53-1:
        return str(value)
    return value


def _value(value, definition):
    if isinstance(value, (dict, list)) or value is None:
        raise AiError("筛选值必须是非空标量；空值使用 isnull")
    typ = definition["type"]
    if typ in {"JSONField", "BinaryField"} or definition.get("json"):
        raise AiError("结构字段不支持通用筛选")
    if "Integer" in typ or typ in {"AutoField", "BigAutoField", "SmallAutoField"}:
        if type(value) is not int or abs(value) > 2**53-1:
            raise AiError("整数字段的筛选值无效")
    elif typ == "BooleanField":
        if type(value) is not bool:
            raise AiError("布尔字段的筛选值无效")
    elif typ in {"FloatField", "DecimalField"}:
        if type(value) not in {str, int, float} or len(str(value)) > 100:
            raise AiError("数值字段的筛选值无效")
        try:
            number = Decimal(str(value))
            if not number.is_finite() or not math.isfinite(float(number)):
                raise ValueError()
        except (ValueError, ArithmeticError):
            raise AiError("数值字段的筛选值无效") from None
        return number if typ == "DecimalField" else float(number)
    elif not isinstance(value, str) or len(value) > 500:
        raise AiError("筛选字符串超限或类型无效")
    return value


PRIVATE_KEY = re.compile(r"password|secret|token|encrypted|api.?key|aes.?key|authorization|cookie|webhook|object.?key", re.I)


def _redact(value, depth=0):
    if depth > 12:
        return "[depth-limited]"
    if isinstance(value, dict):
        return {key: "[redacted]" if PRIVATE_KEY.search(key) else _redact(item, depth+1) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, depth+1) for item in value]
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        url = urlsplit(value)
        return urlunsplit((url.scheme, (url.hostname or "") + (f":{url.port}" if url.port else ""), url.path, "", ""))
    return scalar(value)


def _clean(value, definition, path, truncated, windows, offset, limit):
    value = scalar(value)
    if definition.get("json") and isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = "[invalid structured value]"
    value = _redact(value)
    structured = isinstance(value, (dict, list))
    text = canonical(value) if structured else value
    if isinstance(text, str) and (len(text) > limit or offset):
        part = text[offset:offset+limit]
        windows[path] = {"encoding": "json" if structured else "text", "totalCharacters": len(text),
                         "offset": offset, "returnedCharacters": len(part),
                         "nextOffset": offset+len(part) if offset+len(part) < len(text) else None}
        truncated.append(path)
        return part
    return value


@contextmanager
def read_transaction(expected_role=None):
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("SET LOCAL statement_timeout = '8000ms'")
                if expected_role and settings.DJANGO_PROCESS_ROLE != "development":
                    cursor.execute("SELECT current_user, current_setting('transaction_read_only')")
                    if cursor.fetchone() != (expected_role, "on"):
                        raise AiError("数据集数据库只读角色不匹配", "service_unavailable", 503)
        yield


def query(dataset_id, query, principal):
    spec = SPECS.get(dataset_id)
    if not spec:
        raise AiError("数据集不存在", "not_found", 404)
    authorize(spec, principal)
    if settings.DJANGO_PROCESS_ROLE not in {"development", DOMAINS[spec["domain"]][0]}:
        raise AiError("数据集必须由所属领域 reader 读取", "access_denied", 403)
    fields(query, {"columns", "filters", "cursor", "pageSize", "textOffset", "textLimit"})
    passive(query, 16000)
    size = query.get("pageSize", 20)
    if type(size) is not int or not 1 <= size <= 100:
        raise AiError("pageSize 必须在 1 到 100 之间")
    text_offset, text_limit = query.get("textOffset", 0), query.get("textLimit", 2000)
    if type(text_offset) is not int or not 0 <= text_offset <= 10000000 or type(text_limit) is not int or not 1 <= text_limit <= 8000:
        raise AiError("文本分页参数无效")
    columns = query.get("columns", list(spec["fields"])[:12])
    if (not isinstance(columns, list) or not 1 <= len(columns) <= 50
            or any(not isinstance(key, str) or key not in spec["fields"] for key in columns)
            or len(set(columns)) != len(columns)):
        raise AiError("columns 包含不可访问字段或超过上限")
    filters = query.get("filters", [])
    if not isinstance(filters, list) or len(filters) > 8:
        raise AiError("filters 最多 8 个")
    queryset = _owned(spec, principal)
    for item in filters:
        fields(item, {"field", "op", "value"}, {"field", "op", "value"})
        name, op, value = item["field"], item["op"], item["value"]
        if not isinstance(name, str) or name not in spec["fields"] or op not in {"eq", "gte", "gt", "lte", "lt", "in", "isnull"}:
            raise AiError("筛选字段或操作无效")
        if op == "isnull":
            if type(value) is not bool:
                raise AiError("isnull 必须使用布尔值")
        elif op == "in":
            if not isinstance(value, list) or not 1 <= len(value) <= 50:
                raise AiError("in 最多 50 个值")
            value = [_value(v, spec["fields"][name]) for v in value]
        else:
            value = _value(value, spec["fields"][name])
        queryset = queryset.filter(**{name + "__" + ("exact" if op == "eq" else op): value})
    binding = digest({"dataset": dataset_id, "actor": principal.email.lower(), "role": principal.role,
                      "scope": principal.scope, "columns": columns, "filters": filters,
                      "spec": digest(spec), "textOffset": text_offset, "textLimit": text_limit})
    keys = spec["keys"]
    if "cursor" in query:
        cursor = _decode(query["cursor"])
        if cursor.get("binding") != binding or len(cursor.get("last", [])) != len(keys):
            raise AiError("游标与账号、数据集、字段或筛选条件不匹配")
        after = Q()
        for i, key in enumerate(keys):
            term = Q(**{key + "__gt": cursor["last"][i]})
            for j in range(i):
                term &= Q(**{keys[j]: cursor["last"][j]})
            after |= term
        queryset = queryset.filter(after)
    selected = list(dict.fromkeys([*columns, *keys]))
    with read_transaction(DOMAINS[spec["domain"]][1]):
        source = list(queryset.order_by(*keys).values(*selected)[:size + 1])
    rows, truncated, windows = [], [], {}
    used, last = 0, None
    for raw in source[:size]:
        markers = []
        row_windows = {}
        row = {name: _clean(raw[name], spec["fields"][name], f"{len(rows)}.{name}", markers, row_windows, text_offset, text_limit) for name in columns}
        encoded = canonical(row)
        if used + len(encoded) > 24000:
            if not rows:
                raise AiError("单行结果过大，请减少 columns", "payload_too_large", 413)
            break
        rows.append(row)
        truncated.extend(markers)
        windows.update(row_windows)
        used += len(encoded)
        last = [scalar(raw[key]) for key in keys]
    more = len(source) > len(rows)
    result = {"dataset": dataset_id, "schemaVersion": "1", "columns": columns, "rows": rows,
        "returned": len(rows), "total": None, "hasMore": more, "truncated": more or bool(truncated),
        "truncatedFields": truncated, "cellWindows": windows, "consistency": "live_per_page", "queriedAt": timezone.now().isoformat(),
        "dataCutoffDate": None, "sourceDomain": spec["domain"],
        "nextCursor": _encode({"binding": binding, "last": last, "issued": int(time.time())}) if more else None}
    if len(canonical(result)) > 34000:
        raise AiError("结果过大，请减少字段或 pageSize", "payload_too_large", 413)
    return result


def endpoint(request):
    try:
        principal = verify_principal(request)
        if request.method != "POST":
            raise AiError("仅支持 POST", "invalid_request", 405)
        if request.GET or request.content_type != "application/json" or len(request.body) > 18000:
            raise AiError("数据集查询请求格式或大小无效")
        payload = json.loads(request.body)
        fields(payload, {"dataset", "query"}, {"dataset", "query"})
        result = query(payload["dataset"], payload["query"], principal)
        return JsonResponse(result, json_dumps_params={"ensure_ascii": False}, headers={"Cache-Control": "no-store"})
    except (AiError, PrincipalEnvelopeError) as error:
        return JsonResponse({"error": str(error), "code": error.code}, status=error.status, headers={"Cache-Control": "no-store"})
    except (ValueError, TypeError, KeyError):
        return JsonResponse({"error": "数据集查询参数无效", "code": "invalid_request"}, status=400, headers={"Cache-Control": "no-store"})
    except Exception:
        return JsonResponse({"error": "领域数据集读取不可用", "code": "service_unavailable"}, status=503, headers={"Cache-Control": "no-store"})
