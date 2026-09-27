"""Verification of the edge-authenticated principal envelope.

The edge and Django share only an internal HMAC secret. User-controlled role or
scope headers are ignored unless the complete request envelope verifies.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest


ROLES = {"viewer", "analyst", "operator", "admin"}
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
HEX_64_RE = re.compile(r"^[a-f0-9]{64}$")


class PrincipalEnvelopeError(Exception):
    def __init__(self, message: str, *, status: int = 401, code: str = "authentication_required") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class Principal:
    email: str
    display_name: str
    role: str
    scope: dict[str, list[str]] | None


def _required_header(request: HttpRequest, name: str) -> str:
    value = request.headers.get(name, "").strip()
    if not value:
        raise PrincipalEnvelopeError("缺少内部身份签名")
    return value


def _decode_principal(encoded: str) -> Principal:
    if len(encoded) > 16_384:
        raise PrincipalEnvelopeError("身份信封超出安全上限")
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        value: Any = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PrincipalEnvelopeError("身份信封格式无效") from error
    if not isinstance(value, dict):
        raise PrincipalEnvelopeError("身份信封格式无效")
    email = value.get("email")
    display_name = value.get("displayName")
    role = value.get("role")
    scope = value.get("scope")
    if not isinstance(email, str) or not email.strip() or len(email) > 320:
        raise PrincipalEnvelopeError("身份邮箱无效")
    if not isinstance(display_name, str) or len(display_name) > 200:
        raise PrincipalEnvelopeError("身份名称无效")
    if role not in ROLES:
        raise PrincipalEnvelopeError("身份角色无效", status=403, code="access_denied")
    if scope is not None:
        if not isinstance(scope, dict) or set(scope) != {"warehouses", "channels", "platforms"}:
            raise PrincipalEnvelopeError("身份数据范围无效", status=403, code="access_denied")
        normalized: dict[str, list[str]] = {}
        for key in ("warehouses", "channels", "platforms"):
            items = scope.get(key)
            if not isinstance(items, list) or len(items) > 500:
                raise PrincipalEnvelopeError("身份数据范围无效", status=403, code="access_denied")
            if any(not isinstance(item, str) or not item.strip() or len(item) > 100 for item in items):
                raise PrincipalEnvelopeError("身份数据范围无效", status=403, code="access_denied")
            normalized[key] = list(dict.fromkeys(item.strip() for item in items))
        scope = normalized
    return Principal(email=email.strip(), display_name=display_name, role=role, scope=scope)


def verify_principal(request: HttpRequest) -> Principal:
    secret = os.getenv("TERUISI_DJANGO_INTERNAL_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise PrincipalEnvelopeError("Django 内部签名密钥未安全配置", status=503, code="service_unavailable")
    principal_b64 = _required_header(request, "X-Teruisi-Principal")
    timestamp_text = _required_header(request, "X-Teruisi-Timestamp")
    request_id = _required_header(request, "X-Teruisi-Request-Id")
    body_digest = _required_header(request, "X-Teruisi-Content-SHA256").lower()
    signature = _required_header(request, "X-Teruisi-Signature")
    if not REQUEST_ID_RE.fullmatch(request_id):
        raise PrincipalEnvelopeError("请求标识无效")
    if not HEX_64_RE.fullmatch(body_digest):
        raise PrincipalEnvelopeError("请求摘要无效")
    try:
        timestamp = int(timestamp_text)
    except ValueError as error:
        raise PrincipalEnvelopeError("签名时间无效") from error
    try:
        configured_maximum_age = int(os.getenv("TERUISI_DJANGO_SIGNATURE_MAX_AGE_SECONDS", "60"))
    except ValueError as error:
        raise PrincipalEnvelopeError(
            "Django 内部签名时限配置无效", status=503, code="service_unavailable"
        ) from error
    maximum_age = max(1, min(300, configured_maximum_age))
    if abs(int(time.time()) - timestamp) > maximum_age:
        raise PrincipalEnvelopeError("内部身份签名已过期")
    actual_body_digest = hashlib.sha256(request.body).hexdigest()
    if not hmac.compare_digest(actual_body_digest, body_digest):
        raise PrincipalEnvelopeError("请求正文摘要不匹配")
    canonical = "\n".join(
        [
            "v1",
            timestamp_text,
            request_id,
            request.method.upper(),
            request.path,
            request.META.get("QUERY_STRING", ""),
            body_digest,
            principal_b64,
        ]
    )
    expected = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    supplied = signature[3:].lower() if signature.startswith("v1=") else ""
    if not HEX_64_RE.fullmatch(supplied) or not hmac.compare_digest(expected, supplied):
        raise PrincipalEnvelopeError("内部身份签名无效")
    return _decode_principal(principal_b64)
