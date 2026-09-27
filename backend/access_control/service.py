from __future__ import annotations

from datetime import datetime, timezone as datetime_timezone
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    AccessControlDataRevision,
    AccessControlWriteAuthority,
    AccessRole,
    AppUser,
    PermissionAuditEvent,
)
from .policy import (
    HEX_64_RE,
    ROLE_CATALOG,
    ROLE_CODES,
    USER_STATUSES,
    PolicyError,
    normalize_display_name,
    normalize_email,
    normalize_scope,
    scope_covers,
    sha256_json,
)


BOOTSTRAP_ADMIN_EMAIL = "dengweizhang321@gmail.com"
ZERO_DIGEST = "0" * 64


class AccessControlError(Exception):
    def __init__(self, message: str, *, code: str = "invalid_request", status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def user_state(user: AppUser) -> dict[str, Any]:
    if user.role_id not in ROLE_CODES or user.status not in USER_STATUSES:
        raise AccessControlError("权限用户状态与固定契约不一致", code="service_unavailable", status=503)
    try:
        scope = normalize_scope(user.scope)
    except PolicyError as error:
        raise AccessControlError("权限用户数据范围与固定契约不一致", code="service_unavailable", status=503) from error
    return {
        "email": user.email,
        "displayName": user.display_name,
        "role": user.role_id,
        "status": user.status,
        "scope": scope,
        "version": user.version,
        "createdAt": user.created_at.astimezone(datetime_timezone.utc).isoformat(),
        "updatedAt": user.updated_at.astimezone(datetime_timezone.utc).isoformat(),
    }


def role_state(role: AccessRole) -> dict[str, Any]:
    return {
        "code": role.code,
        "label": role.label,
        "description": role.description,
        "rank": role.rank,
        "permissions": role.permissions,
        "version": role.version,
    }


def audit_state(event: PermissionAuditEvent) -> dict[str, Any]:
    return {
        "sequence": event.sequence,
        "eventId": str(event.event_id),
        "requestId": event.request_id,
        "actorEmail": event.actor_email,
        "actorRole": event.actor_role,
        "targetEmail": event.target_email,
        "action": event.action,
        "before": event.before_state,
        "after": event.after_state,
        "reason": event.reason,
        "source": event.source,
        "occurredAt": event.occurred_at.isoformat(),
    }


def domain_snapshot() -> list[dict[str, Any]]:
    return [user_state(row) for row in AppUser.objects.order_by("email")]


def current_revision() -> AccessControlDataRevision:
    revision = AccessControlDataRevision.objects.filter(domain="access-control").first()
    if revision is None or revision.revision < 1 or not HEX_64_RE.fullmatch(revision.source_digest):
        raise AccessControlError("权限数据版本不可用", code="service_unavailable", status=503)
    return revision


def revision_header() -> str:
    row = current_revision()
    return f"{row.revision}:{row.source_digest[:12]}"


def _bump_revision() -> AccessControlDataRevision:
    digest = sha256_json(domain_snapshot())
    revision = AccessControlDataRevision.objects.select_for_update().get(domain="access-control")
    revision.revision += 1
    revision.source_digest = digest
    revision.save(update_fields=["revision", "source_digest", "updated_at"])
    return revision


def resolve_user(email_input: object) -> AppUser:
    try:
        email = normalize_email(email_input)
    except PolicyError as error:
        raise AccessControlError(str(error)) from error
    user = AppUser.objects.select_related("role").filter(email=email).first()
    if user is None or user.status != "active" or user.role_id not in ROLE_CODES:
        raise AccessControlError("当前账号未获得运营管理系统访问权限", code="access_denied", status=403)
    user_state(user)
    return user


def resolve_background_user(email_input: object, scope_input: object) -> dict[str, Any]:
    user = resolve_user(email_input)
    if user.role_id not in {"analyst", "operator", "admin"}:
        raise AccessControlError("任务发起账号已停用或不再具备 Agent 执行角色", code="access_denied", status=403)
    snapshot = normalize_scope(scope_input)
    current = normalize_scope(user.scope)
    if not scope_covers(current, snapshot):
        raise AccessControlError("任务发起账号的当前数据范围不再覆盖创建快照", code="access_denied", status=403)
    result = user_state(user)
    result["scope"] = snapshot
    return result


def list_roles() -> list[dict[str, Any]]:
    roles = list(AccessRole.objects.order_by("rank"))
    if [role.code for role in roles] != list(ROLE_CATALOG):
        raise AccessControlError("角色目录与固定权限契约不一致", code="service_unavailable", status=503)
    for role in roles:
        expected = ROLE_CATALOG[role.code]
        if role.permissions != expected["permissions"] or role.rank != expected["rank"]:
            raise AccessControlError("角色目录与固定权限契约不一致", code="service_unavailable", status=503)
    return [role_state(role) for role in roles]


def list_users(*, page: int, page_size: int, query: str = "", status: str = "", role: str = "") -> dict[str, Any]:
    rows = AppUser.objects.select_related("role").order_by("email")
    if query:
        rows = rows.filter(Q(email__icontains=query) | Q(display_name__icontains=query))
    if status:
        if status not in USER_STATUSES:
            raise AccessControlError("用户状态筛选无效")
        rows = rows.filter(status=status)
    if role:
        if role not in ROLE_CODES:
            raise AccessControlError("角色筛选无效")
        rows = rows.filter(role_id=role)
    total = rows.count()
    offset = (page - 1) * page_size
    items = [user_state(row) for row in rows[offset:offset + page_size]]
    return {"items": items, "page": page, "pageSize": page_size, "total": total, "returned": len(items), "truncated": offset + len(items) < total}


def list_audits(*, page: int, page_size: int, target_email: str = "", action: str = "") -> dict[str, Any]:
    rows = PermissionAuditEvent.objects.order_by("-sequence")
    if target_email:
        try:
            rows = rows.filter(target_email=normalize_email(target_email))
        except PolicyError as error:
            raise AccessControlError(str(error)) from error
    if action:
        if len(action) > 64 or not action.replace("_", "").isalnum():
            raise AccessControlError("审计动作筛选无效")
        rows = rows.filter(action=action)
    total = rows.count()
    offset = (page - 1) * page_size
    items = [audit_state(row) for row in rows[offset:offset + page_size]]
    return {"items": items, "page": page, "pageSize": page_size, "total": total, "returned": len(items), "truncated": offset + len(items) < total}


def _validated_mutation(payload: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    allowed = {"email", "displayName", "role", "status", "scope", "expectedVersion", "reason"}
    if set(payload) - allowed:
        raise AccessControlError("用户权限请求包含未知字段")
    try:
        result = {
            "email": normalize_email(payload.get("email")),
            "display_name": normalize_display_name(payload.get("displayName")),
            "role": payload.get("role") if isinstance(payload.get("role"), str) else "",
            "status": payload.get("status") if isinstance(payload.get("status"), str) else "",
            "scope": normalize_scope(payload.get("scope")),
            "reason": payload.get("reason", "").strip() if isinstance(payload.get("reason", ""), str) else "",
        }
    except PolicyError as error:
        raise AccessControlError(str(error)) from error
    if result["role"] not in ROLE_CODES:
        raise AccessControlError("用户角色无效")
    if result["email"] == "local-admin@teruisi.local":
        raise AccessControlError("本地直连身份为保留的边缘操作者，不能登记为登录账号")
    if result["status"] not in USER_STATUSES:
        raise AccessControlError("用户状态无效")
    if not result["reason"] or len(result["reason"]) > 200:
        raise AccessControlError("权限变更必须填写 1 到 200 个字符的原因")
    if not creating:
        expected = payload.get("expectedVersion")
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
            raise AccessControlError("expectedVersion 无效")
        result["expected_version"] = expected
    return result


def _assert_admin_invariant(target_email: str, role: str, status: str, scope: object) -> None:
    if target_email == BOOTSTRAP_ADMIN_EMAIL and (role != "admin" or status != "active" or scope is not None):
        raise AccessControlError("系统引导管理员必须保持启用、管理员角色和无限制数据范围", code="conflict", status=409)
    if role == "admin" and status == "active" and scope is None:
        return
    remaining = AppUser.objects.filter(role_id="admin", status="active", scope__isnull=True).exclude(email=target_email).exists()
    if not remaining:
        raise AccessControlError("必须至少保留一个启用且数据范围不受限的管理员", code="conflict", status=409)


def create_user(payload: dict[str, Any], *, actor_email: str, actor_role: str, request_id: str) -> dict[str, Any]:
    values = _validated_mutation(payload, creating=True)
    with transaction.atomic():
        AccessControlDataRevision.objects.select_for_update().get(domain="access-control")
        if AppUser.objects.filter(email=values["email"]).exists():
            raise AccessControlError("用户已存在", code="conflict", status=409)
        now = timezone.now()
        user = AppUser.objects.create(
            email=values["email"], display_name=values["display_name"], role_id=values["role"],
            status=values["status"], scope=values["scope"], created_at=now, updated_at=now,
        )
        after = user_state(user)
        PermissionAuditEvent.objects.create(
            request_id=request_id, actor_email=actor_email, actor_role=actor_role,
            target_email=user.email, action="user_created", before_state=None, after_state=after,
            before_digest=ZERO_DIGEST, after_digest=sha256_json(after), reason=values["reason"],
            occurred_at=now,
        )
        revision = _bump_revision()
        return {"ok": True, "user": after, "revision": f"{revision.revision}:{revision.source_digest[:12]}"}


def update_user(target_email: str, payload: dict[str, Any], *, actor_email: str, actor_role: str, request_id: str) -> dict[str, Any]:
    values = _validated_mutation({**payload, "email": target_email}, creating=False)
    with transaction.atomic():
        AccessControlDataRevision.objects.select_for_update().get(domain="access-control")
        user = AppUser.objects.select_for_update(of=("self",)).select_related("role").filter(email=values["email"]).first()
        if user is None:
            raise AccessControlError("用户不存在", code="not_found", status=404)
        if user.version != values["expected_version"]:
            raise AccessControlError("用户权限已被其他管理员修改，请刷新后重试", code="version_conflict", status=409)
        _assert_admin_invariant(user.email, values["role"], values["status"], values["scope"])
        if (
            user.display_name == values["display_name"]
            and user.role_id == values["role"]
            and user.status == values["status"]
            and user.scope == values["scope"]
        ):
            raise AccessControlError("用户权限没有变化")
        before = user_state(user)
        user.display_name = values["display_name"]
        user.role_id = values["role"]
        user.status = values["status"]
        user.scope = values["scope"]
        user.version += 1
        user.updated_at = timezone.now()
        user.save()
        after = user_state(user)
        PermissionAuditEvent.objects.create(
            request_id=request_id, actor_email=actor_email, actor_role=actor_role,
            target_email=user.email, action="user_permissions_updated", before_state=before,
            after_state=after, before_digest=sha256_json(before), after_digest=sha256_json(after),
            reason=values["reason"], occurred_at=user.updated_at,
        )
        revision = _bump_revision()
        return {"ok": True, "user": after, "revision": f"{revision.revision}:{revision.source_digest[:12]}"}
