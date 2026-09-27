from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from django.conf import settings
from django.db import connection, transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from sales.auth import Principal, PrincipalEnvelopeError, verify_principal

from .models import AccessControlDataRevision, AccessControlWriteAuthority, AccessControlWriteRequestReceipt
from .policy import PolicyError, normalize_display_name, normalize_email
from .service import (
    AccessControlError,
    create_user,
    list_audits,
    list_roles,
    list_users,
    resolve_background_user,
    resolve_user,
    revision_header,
    update_user,
    user_state,
)


def _json(payload: dict[str, object], status: int = 200, *, replayed: bool = False, revision: str | None = None) -> JsonResponse:
    response = JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False, "separators": (",", ":")})
    response["Cache-Control"] = "no-store"
    if replayed:
        response["X-Teruisi-Write-Replay"] = "1"
    if revision:
        response["X-Access-Control-Revision"] = revision
    return response


def _error(error: Exception, fallback: str) -> JsonResponse:
    if isinstance(error, (AccessControlError, PrincipalEnvelopeError)):
        return _json({"error": str(error), "code": error.code}, error.status)
    if isinstance(error, PolicyError):
        return _json({"error": str(error), "code": "invalid_request"}, 400)
    return _json({"error": fallback, "code": "service_unavailable"}, 503)


def _body(request: HttpRequest) -> dict[str, object]:
    content_type = request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise AccessControlError("请求必须使用 application/json", status=415)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AccessControlError("请求正文不是有效 JSON") from error
    if not isinstance(payload, dict):
        raise AccessControlError("请求正文不是 JSON 对象")
    return payload


def _admin(request: HttpRequest) -> Principal:
    principal = verify_principal(request)
    # Reserved edge-only identity: the Worker already verifies the explicit local
    # build/development flags and exact loopback host before signing this actor.
    # It is never resolvable as a login account or persisted as an AppUser.
    if principal.email == "local-admin@teruisi.local" and principal.role == "admin" and principal.scope is None:
        return principal
    current = resolve_user(principal.email)
    if (
        principal.role != "admin" or principal.scope is not None
        or current.role_id != "admin" or current.scope is not None
    ):
        raise AccessControlError("权限管理仅允许数据范围不受限的管理员", code="access_denied", status=403)
    return principal


def _positive(value: str | None, fallback: int, maximum: int) -> int:
    if value is None or value == "":
        return fallback
    if not value.isdigit() or int(value) < 1 or int(value) > maximum:
        raise AccessControlError("分页参数无效")
    return int(value)


def _writer_authority() -> None:
    if settings.DJANGO_PROCESS_ROLE == "development":
        return
    # Runtime writers must not gain UPDATE on immutable authority evidence.
    # The revision mutex serializes writes and authority activation.
    authority = AccessControlWriteAuthority.objects.filter(id=1).first()
    if (
        authority is None or authority.status != "postgres" or authority.authority_epoch is None
        or str(authority.authority_epoch) != settings.ACCESS_CONTROL_WRITE_AUTHORITY_EPOCH
        or authority.cutover_id != settings.ACCESS_CONTROL_WRITE_CUTOVER_ID
    ):
        raise AccessControlError("权限写入权威未激活", code="service_unavailable", status=503)


def _lock_request_id(request_id: str) -> None:
    if connection.vendor != "postgresql":
        return
    digest = hashlib.sha256(f"access-control-write-receipt\n{request_id}".encode()).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


def _replay_fenced_write(request: HttpRequest, principal: Principal, callback: Callable[[str], tuple[dict[str, object], int]]) -> JsonResponse:
    request_id = request.headers.get("X-Teruisi-Request-Id", "").strip()
    body_digest = request.headers.get("X-Teruisi-Content-SHA256", "").strip().lower()
    query_digest = hashlib.sha256(request.META.get("QUERY_STRING", "").encode()).hexdigest()
    with transaction.atomic():
        _lock_request_id(request_id)
        AccessControlDataRevision.objects.select_for_update().get(domain="access-control")
        _writer_authority()
        # Recheck after waiting for the mutation mutex, including on replay:
        # a concurrent administrator revocation must take effect before commit.
        principal = _admin(request)
        receipt = AccessControlWriteRequestReceipt.objects.select_for_update().filter(request_id=request_id).first()
        if receipt:
            if (
                receipt.body_sha256 != body_digest or receipt.query_sha256 != query_digest
                or receipt.method != request.method or receipt.path != request.path
                or receipt.actor_email != principal.email.strip().lower()
            ):
                raise AccessControlError("内部请求标识已绑定其他权限写入", code="version_conflict", status=409)
            if receipt.status == "completed":
                return _json(receipt.response_payload, receipt.response_status, replayed=True, revision=revision_header())
            raise AccessControlError("相同权限请求仍在处理中", code="conflict", status=409)
        receipt = AccessControlWriteRequestReceipt.objects.create(
            request_id=request_id, body_sha256=body_digest, query_sha256=query_digest,
            method=request.method, path=request.path, actor_email=principal.email.strip().lower(),
        )
        payload, status = callback(request_id)
        receipt.status = "completed"
        receipt.response_status = status
        receipt.response_payload = payload
        receipt.completed_at = timezone.now()
        receipt.save()
        return _json(payload, status, revision=str(payload.get("revision") or revision_header()))


@require_POST
def principal_resolve(request: HttpRequest) -> JsonResponse:
    try:
        envelope = verify_principal(request)
        payload = _body(request)
        if set(payload) != {"email", "displayName"}:
            raise AccessControlError("身份解析请求字段无效")
        email = normalize_email(payload["email"])
        normalize_display_name(payload["displayName"])
        if envelope.email.strip().lower() != email:
            raise AccessControlError("签名身份与解析目标不一致", code="access_denied", status=403)
        return _json({"user": user_state(resolve_user(email))}, revision=revision_header())
    except Exception as error:
        return _error(error, "读取登录身份失败")


@require_POST
def background_authorize(request: HttpRequest) -> JsonResponse:
    try:
        envelope = verify_principal(request)
        payload = _body(request)
        if set(payload) != {"ownerEmail", "scope"}:
            raise AccessControlError("后台身份请求字段无效")
        email = normalize_email(payload["ownerEmail"])
        if envelope.email.strip().lower() != email:
            raise AccessControlError("签名身份与任务所有者不一致", code="access_denied", status=403)
        return _json({"user": resolve_background_user(email, payload["scope"])}, revision=revision_header())
    except Exception as error:
        return _error(error, "后台身份校验失败")


@require_GET
def roles(request: HttpRequest) -> JsonResponse:
    try:
        _admin(request)
        return _json({"items": list_roles()}, revision=revision_header())
    except Exception as error:
        return _error(error, "角色目录读取失败")


@require_http_methods(["GET", "POST", "PUT"])
def users(request: HttpRequest) -> JsonResponse:
    try:
        principal = _admin(request)
        if request.method == "GET":
            payload = list_users(
                page=_positive(request.GET.get("page"), 1, 10_000),
                page_size=_positive(request.GET.get("pageSize"), 50, 100),
                query=(request.GET.get("query") or "").strip()[:200],
                status=(request.GET.get("status") or "").strip(),
                role=(request.GET.get("role") or "").strip(),
            )
            return _json(payload, revision=revision_header())
        payload = _body(request)
        if request.method == "POST":
            return _replay_fenced_write(request, principal, lambda request_id: (
                create_user(payload, actor_email=principal.email, actor_role=principal.role, request_id=request_id), 201
            ))
        return _replay_fenced_write(request, principal, lambda request_id: (
            update_user(str(payload.get("email") or ""), payload, actor_email=principal.email, actor_role=principal.role, request_id=request_id), 200
        ))
    except Exception as error:
        return _error(error, "用户权限处理失败")


@require_GET
def audits(request: HttpRequest) -> JsonResponse:
    try:
        _admin(request)
        payload = list_audits(
            page=_positive(request.GET.get("page"), 1, 10_000),
            page_size=_positive(request.GET.get("pageSize"), 50, 100),
            target_email=(request.GET.get("targetEmail") or "").strip(),
            action=(request.GET.get("action") or "").strip(),
        )
        return _json(payload, revision=revision_header())
    except Exception as error:
        return _error(error, "权限审计读取失败")
