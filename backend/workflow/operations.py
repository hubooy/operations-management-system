from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, datetime, timezone as datetime_timezone
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from django.db import IntegrityError, connection, transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from sales.auth import Principal

from .errors import WorkflowApiError
from .models import (
    WorkflowAttachmentCleanup,
    WorkflowOperationActivity,
    WorkflowOperationRecord,
    WorkflowTask,
    WorkflowTaskActivityLog,
    WorkflowTaskAttachment,
    WorkflowTaskComment,
    WorkflowTaskEntityLink,
    WorkflowTaskReminder,
    WorkflowTaskTemplate,
)
from .revisions import bump_revision


TASK_STATUSES = {"待开始", "工作中", "已完成"}
PRIORITIES = {"high", "normal", "low"}
TASK_SOURCES = {"系统预置", "手动录入"}
ENTITY_TYPES = {"shop", "product", "campaign", "order", "report", "url"}
RECORD_TYPES = {"inspection", "review"}
RECORD_SOURCES = {"manual", "system", "import", "integration"}
RECORD_STATUSES = {
    "inspection": {"正常", "待处理", "处理中", "已关闭"},
    "review": {"待回复", "处理中", "已回复", "无需回复"},
}
RESOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def _invalid(message: str) -> None:
    raise WorkflowApiError(message)


def _text(value: object, label: str, maximum: int, *, required: bool = True, fallback: str = "") -> str:
    if value is None and not required:
        return fallback
    if not isinstance(value, str):
        _invalid(f"{label}必须是文本")
    normalized = value.strip()
    if required and not normalized:
        _invalid(f"{label}不能为空")
    if len(normalized) > maximum:
        _invalid(f"{label}不能超过 {maximum} 个字符")
    return normalized or fallback


def _resource_id(value: object, label: str) -> str:
    identifier = _text(value, label, 128)
    if not RESOURCE_ID_RE.fullmatch(identifier):
        _invalid(f"{label}格式无效")
    return identifier


def _positive(value: object, label: str, *, default: int | None = None, maximum: int = 2_147_483_646) -> int:
    if value in (None, "") and default is not None:
        return default
    if isinstance(value, bool):
        _invalid(f"{label}必须是正整数")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"[1-9]\d*", value.strip()):
        parsed = int(value)
    else:
        _invalid(f"{label}必须是正整数")
    if parsed < 1 or parsed > maximum:
        _invalid(f"{label}超出允许范围")
    return parsed


def _keys(payload: dict[str, object], allowed: set[str], *, require_change: set[str] | None = None) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        _invalid(f"包含不支持的字段：{'、'.join(unknown[:5])}")
    if require_change is not None and not any(key in payload for key in require_change):
        _invalid("缺少可更新字段")


def _calendar(value: object, label: str) -> str:
    text = _text(value, label, 10)
    if text == "待排期":
        return text
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise WorkflowApiError(f"{label}必须为 YYYY-MM-DD 或待排期") from error
    if parsed.isoformat() != text:
        _invalid(f"{label}必须为 YYYY-MM-DD 或待排期")
    return text


def _datetime(value: object, label: str, *, nullable: bool = False) -> datetime | None:
    if value in (None, "") and nullable:
        return None
    text = _text(value, label, 40)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            local_date = date.fromisoformat(text)
        except ValueError as error:
            raise WorkflowApiError(f"{label}不是有效日期") from error
        return datetime(
            local_date.year,
            local_date.month,
            local_date.day,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ).astimezone(datetime_timezone.utc)
    parsed = parse_datetime(text)
    if parsed is None or parsed.tzinfo is None:
        _invalid(f"{label}必须是 YYYY-MM-DD 或包含时区的 ISO 日期时间")
    return parsed.astimezone(datetime_timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    rendered = value.astimezone(datetime_timezone.utc).isoformat(timespec="milliseconds")
    return rendered.replace("+00:00", "Z")


def _task_item(row: WorkflowTask) -> dict[str, object]:
    return {
        "id": row.id,
        "title": row.title,
        "workContent": row.work_content,
        "category": row.category,
        "owner": row.owner,
        "shopName": row.shop_name,
        "startDate": row.start_date,
        "due": row.due_date,
        "status": row.status,
        "priority": row.priority,
        "source": "系统预置" if row.created_by == "system" else "手动录入",
        "version": int(row.version),
        "createdAt": _iso(row.created_at),
        "updatedAt": _iso(row.updated_at),
        "attachments": [],
    }


def _comment_item(row: WorkflowTaskComment) -> dict[str, object]:
    return {"id": row.id, "taskId": row.task_id, "content": row.content, "createdBy": row.created_by, "createdAt": _iso(row.created_at)}


def _activity_item(row: WorkflowTaskActivityLog) -> dict[str, object]:
    return {
        "id": row.id, "taskId": row.task_id, "action": row.action, "summary": row.summary,
        "metadata": row.metadata if isinstance(row.metadata, dict) else {},
        "actorEmail": row.actor_email, "createdAt": _iso(row.created_at),
    }


def _reminder_item(row: WorkflowTaskReminder) -> dict[str, object]:
    return {
        "id": row.id, "taskId": row.task_id, "remindAt": _iso(row.remind_at), "note": row.note,
        "status": row.status, "createdBy": row.created_by, "createdAt": _iso(row.created_at),
        "updatedAt": _iso(row.updated_at),
    }


def _link_item(row: WorkflowTaskEntityLink) -> dict[str, object]:
    return {
        "id": row.id, "taskId": row.task_id, "entityType": row.entity_type, "entityId": row.entity_id,
        "label": row.label, "url": row.url, "createdBy": row.created_by, "createdAt": _iso(row.created_at),
    }


def _attachment_item(row: WorkflowTaskAttachment, *, internal: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": row.id, "taskId": row.task_id, "fileName": row.file_name, "mimeType": row.mime_type,
        "sizeBytes": int(row.size_bytes), "sha256": row.sha256, "createdBy": row.created_by,
        "createdAt": _iso(row.created_at),
        "downloadUrl": f"/api/workflow/tasks/{row.task_id}/attachments/{row.id}",
    }
    if internal:
        payload["objectKey"] = row.object_key
    return payload


def _template_item(row: WorkflowTaskTemplate) -> dict[str, object]:
    return {
        "id": row.id, "name": row.name, "description": row.description, "title": row.title,
        "workContent": row.work_content, "category": row.category, "owner": row.owner,
        "shopName": row.shop_name, "startOffsetDays": row.start_offset_days,
        "dueOffsetDays": row.due_offset_days, "priority": row.priority, "active": row.active,
        "version": int(row.version), "createdBy": row.created_by, "updatedBy": row.updated_by,
        "createdAt": _iso(row.created_at), "updatedAt": _iso(row.updated_at),
    }


def _record_item(row: WorkflowOperationRecord) -> dict[str, object]:
    return {
        "id": row.id, "type": row.record_type, "title": row.title, "status": row.status,
        "priority": row.priority, "platform": row.platform, "channel": row.channel,
        "shopName": row.shop_name, "owner": row.owner, "occurredAt": _iso(row.occurred_at),
        "dueAt": _iso(row.due_at), "content": row.content, "source": row.source,
        "sourceRef": row.source_ref, "referenceCode": row.reference_code, "version": int(row.version),
        "createdBy": row.created_by, "updatedBy": row.updated_by,
        "createdAt": _iso(row.created_at), "updatedAt": _iso(row.updated_at),
    }


def _record_activity_item(row: WorkflowOperationActivity) -> dict[str, object]:
    detail = row.detail if isinstance(row.detail, dict) else {}
    return {
        "id": row.id, "recordId": row.record_id, "action": row.action, "actorEmail": row.actor_email,
        "actorRole": row.actor_role, "fromVersion": row.from_version, "toVersion": int(row.to_version),
        "changedFields": detail.get("changedFields", []) if isinstance(detail.get("changedFields", []), list) else [],
        "fromStatus": detail.get("fromStatus") if isinstance(detail.get("fromStatus"), str) else None,
        "toStatus": detail.get("toStatus") if isinstance(detail.get("toStatus"), str) else None,
        "createdAt": _iso(row.created_at),
    }


def _task_exists(task_id: object, *, lock: bool = False) -> WorkflowTask:
    identifier = _resource_id(task_id, "工作项标识")
    query = WorkflowTask.objects.select_for_update() if lock else WorkflowTask.objects
    row = query.filter(id=identifier, deleted_at__isnull=True).first()
    if row is None:
        raise WorkflowApiError("工作项不存在或已删除", code="not_found", status=404)
    return row


def _append_task_activity(task: WorkflowTask, action: str, summary: str, actor: str, metadata: dict[str, object] | None = None) -> None:
    WorkflowTaskActivityLog.objects.create(
        id=str(uuid.uuid4()), task=task, action=action, summary=summary,
        actor_email=actor, metadata=metadata or {},
    )


def _task_filters(options: dict[str, object], *, include_status: bool = True) -> Q:
    query = Q(deleted_at__isnull=True)
    search = str(options.get("query") or "")
    if search:
        query &= Q(title__icontains=search) | Q(work_content__icontains=search) | Q(category__icontains=search) | Q(owner__icontains=search) | Q(shop_name__icontains=search)
    mapping = {
        "priorities": "priority__in", "owners": "owner__in", "shop_names": "shop_name__in", "categories": "category__in",
    }
    if include_status:
        mapping["statuses"] = "status__in"
    for key, lookup in mapping.items():
        values = options.get(key) or []
        if values:
            query &= Q(**{lookup: values})
    sources = options.get("sources") or []
    if sources:
        source_query = Q()
        if "系统预置" in sources:
            source_query |= Q(created_by="system")
        if "手动录入" in sources:
            source_query |= ~Q(created_by="system")
        query &= source_query
    if options.get("due_from"):
        query &= ~Q(due_date="待排期") & Q(due_date__gte=options["due_from"])
    if options.get("due_to"):
        query &= ~Q(due_date="待排期") & Q(due_date__lt=options["due_to"])
    return query


def list_tasks(options: dict[str, object]) -> dict[str, object]:
    page = int(options["page"]); page_size = int(options["page_size"]); offset = (page - 1) * page_size
    selected = WorkflowTask.objects.filter(_task_filters(options)).order_by("-created_at", "-id")
    summary_query = WorkflowTask.objects.filter(_task_filters(options, include_status=False))
    total = selected.count()
    summary_counts = dict(summary_query.values_list("status").annotate(total=Count("id")))
    items = [_task_item(row) for row in selected[offset:offset + page_size]]
    facets = WorkflowTask.objects.filter(deleted_at__isnull=True)
    values = lambda field: list(facets.exclude(**{field: ""}).order_by(field).values_list(field, flat=True).distinct()[:200])
    pending = int(summary_counts.get("待开始", 0)); progress = int(summary_counts.get("工作中", 0)); completed = int(summary_counts.get("已完成", 0))
    return {
        "items": items,
        "pagination": {"page": page, "pageSize": page_size, "total": total, "returned": len(items), "truncated": offset + len(items) < total},
        "summary": {"total": sum(summary_counts.values()), "pending": pending, "inProgress": progress, "completed": completed, "open": pending + progress},
        "facets": {"categories": values("category"), "owners": values("owner"), "shopNames": values("shop_name"), "sources": ["系统预置", "手动录入"]},
        "filtersApplied": {
            "query": options.get("query") or "", "statuses": options.get("statuses") or [], "priorities": options.get("priorities") or [],
            "owners": options.get("owners") or [], "shopNames": options.get("shop_names") or [], "categories": options.get("categories") or [],
            "sources": options.get("sources") or [], "dueFrom": options.get("due_from"), "dueTo": options.get("due_to"),
        },
    }


def _normalize_task(payload: dict[str, object], current: WorkflowTask | None = None) -> dict[str, object]:
    start = current.start_date if current and "startDate" not in payload else _calendar(payload.get("startDate", "待排期"), "开始日期")
    due = current.due_date if current and "due" not in payload else _calendar(payload.get("due", "待排期"), "截止日期")
    if start != "待排期" and due != "待排期" and due < start:
        _invalid("截止时间不能早于开始时间")
    priority = current.priority if current and "priority" not in payload else payload.get("priority", "normal")
    if priority not in PRIORITIES:
        _invalid("工作项紧急程度无效")
    status = current.status if current and "status" not in payload else payload.get("status", "待开始")
    if status not in TASK_STATUSES:
        _invalid("工作项状态无效")
    def field(key: str, attr: str, label: str, maximum: int, fallback: str) -> str:
        if current is not None and key not in payload:
            return str(getattr(current, attr))
        value = payload.get(key)
        if value is None:
            return fallback
        normalized = _text(value, label, maximum, required=False)
        return normalized or fallback
    return {
        "title": field("title", "title", "工作事项", 160, "未命名工作项"),
        "work_content": field("workContent", "work_content", "工作内容", 2_000, "未填写工作内容"),
        "category": field("category", "category", "事项分类", 80, "工作计划"),
        "owner": field("owner", "owner", "跟进人", 120, "未指定跟进人"),
        "shop_name": field("shopName", "shop_name", "店铺", 160, "未关联店铺"),
        "start_date": start, "due_date": due, "status": status, "priority": priority,
    }


@transaction.atomic
def create_task(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    _keys(payload, {"title", "workContent", "category", "owner", "shopName", "startDate", "due", "priority"})
    normalized = _normalize_task(payload)
    actor = principal.email.strip().lower()
    row = WorkflowTask.objects.create(id=str(uuid.uuid4()), created_by=actor, updated_by=actor, **normalized)
    _append_task_activity(row, "task.created", "创建了工作事项", actor, {"version": 1})
    bump_revision({"operation": "task_create", "id": row.id})
    return _task_item(row)


@transaction.atomic
def update_task(task_id: object, payload: dict[str, object], principal: Principal) -> dict[str, object] | None:
    editable = {"title", "workContent", "category", "owner", "shopName", "startDate", "due", "status", "priority"}
    _keys(payload, editable | {"expectedVersion"}, require_change=editable)
    identifier = _resource_id(task_id, "工作项标识")
    row = WorkflowTask.objects.select_for_update().filter(id=identifier, deleted_at__isnull=True).first()
    if row is None:
        return None
    expected = _positive(payload.get("expectedVersion"), "预期版本")
    if row.version != expected:
        raise WorkflowApiError("工作事项已被其他人更新，请刷新后重试", code="version_conflict", status=409)
    normalized = _normalize_task(payload, row)
    field_names = {
        "title": "title", "work_content": "workContent", "category": "category", "owner": "owner",
        "shop_name": "shopName", "start_date": "startDate", "due_date": "due", "status": "status", "priority": "priority",
    }
    changed = [public for field, public in field_names.items() if getattr(row, field) != normalized[field]]
    if not changed:
        _invalid("工作事项没有发生变化")
    previous_status = row.status
    for field, value in normalized.items():
        setattr(row, field, value)
    row.version += 1; row.mutation_token = uuid.uuid4().hex; row.updated_by = principal.email.strip().lower(); row.updated_at = timezone.now()
    row.save()
    status_changed = previous_status != row.status
    metadata: dict[str, object] = {"changedFields": changed, "version": int(row.version)}
    if status_changed:
        metadata["status"] = row.status
    _append_task_activity(row, "task.status_changed" if status_changed else "task.updated", "更新了工作事项状态" if status_changed else "更新了工作事项", row.updated_by, metadata)
    bump_revision({"operation": "task_update", "id": row.id, "version": row.version})
    return _task_item(row)


@transaction.atomic
def delete_task(task_id: object, expected_version: object, principal: Principal) -> dict[str, object] | None:
    identifier = _resource_id(task_id, "工作项标识")
    row = WorkflowTask.objects.select_for_update().filter(id=identifier, deleted_at__isnull=True).first()
    if row is None:
        return None
    expected = _positive(expected_version, "预期版本")
    if row.version != expected:
        raise WorkflowApiError("工作事项已被其他人更新，请刷新后重试", code="version_conflict", status=409)
    object_keys = list(row.attachments.values_list("object_key", flat=True))
    now = timezone.now(); actor = principal.email.strip().lower()
    for object_key in object_keys:
        WorkflowAttachmentCleanup.objects.update_or_create(object_key=object_key, defaults={"updated_at": now})
    row.attachments.all().delete()
    row.version += 1; row.mutation_token = uuid.uuid4().hex; row.deleted_at = now; row.deleted_by = actor; row.updated_by = actor; row.updated_at = now
    row.save()
    _append_task_activity(row, "task.deleted", "删除了工作事项", actor, {"version": int(row.version)})
    bump_revision({"operation": "task_delete", "id": row.id, "version": row.version})
    return {"id": row.id, "deleted": True, "version": int(row.version), "cleanupObjectKeys": object_keys}


def collaboration(task_id: object) -> dict[str, object]:
    row = _task_exists(task_id)
    return {
        "comments": [_comment_item(item) for item in row.comments.order_by("created_at", "id")[:500]],
        "activity": [_activity_item(item) for item in row.activity_logs.order_by("-created_at", "-id")[:500]],
        "reminders": [_reminder_item(item) for item in row.reminders.filter(status="pending").order_by("remind_at", "id")[:200]],
        "links": [_link_item(item) for item in row.entity_links.order_by("created_at", "id")[:200]],
        "attachments": [_attachment_item(item) for item in row.attachments.order_by("created_at", "id")[:100]],
    }


@transaction.atomic
def create_comment(task_id: object, payload: dict[str, object], principal: Principal) -> dict[str, object]:
    _keys(payload, {"content"})
    row = _task_exists(task_id, lock=True); content = _text(payload.get("content"), "评论内容", 2_000); actor = principal.email.strip().lower()
    item = WorkflowTaskComment.objects.create(id=str(uuid.uuid4()), task=row, content=content, created_by=actor)
    _append_task_activity(row, "comment.created", "添加了评论", actor, {"commentId": item.id})
    bump_revision({"operation": "comment_create", "id": item.id})
    return _comment_item(item)


@transaction.atomic
def create_reminder(task_id: object, payload: dict[str, object], principal: Principal) -> dict[str, object]:
    _keys(payload, {"remindAt", "note"})
    row = _task_exists(task_id, lock=True); remind_at = _datetime(payload.get("remindAt"), "提醒时间"); actor = principal.email.strip().lower()
    item = WorkflowTaskReminder.objects.create(
        id=str(uuid.uuid4()), task=row, remind_at=remind_at, note=_text(payload.get("note", ""), "提醒备注", 500, required=False), created_by=actor,
    )
    _append_task_activity(row, "reminder.created", "设置了提醒", actor, {"reminderId": item.id, "remindAt": _iso(remind_at)})
    bump_revision({"operation": "reminder_create", "id": item.id})
    return _reminder_item(item)


@transaction.atomic
def dismiss_reminder(task_id: object, reminder_id: object, principal: Principal) -> bool:
    row = _task_exists(task_id, lock=True); identifier = _resource_id(reminder_id, "提醒标识")
    reminder = WorkflowTaskReminder.objects.select_for_update().filter(id=identifier, task=row, status="pending").first()
    if reminder is None:
        return False
    reminder.status = "dismissed"; reminder.updated_at = timezone.now(); reminder.save(update_fields=["status", "updated_at"])
    _append_task_activity(row, "reminder.dismissed", "取消了提醒", principal.email.strip().lower(), {"reminderId": identifier})
    bump_revision({"operation": "reminder_dismiss", "id": identifier})
    return True


def _safe_url(value: object) -> str:
    url = _text(value, "关联地址", 1_000, required=False)
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        _invalid("关联地址无效或包含账号密码")
    return url


@transaction.atomic
def create_link(task_id: object, payload: dict[str, object], principal: Principal) -> dict[str, object]:
    _keys(payload, {"entityType", "entityId", "label", "url"})
    row = _task_exists(task_id, lock=True); entity_type = payload.get("entityType")
    if entity_type not in ENTITY_TYPES:
        _invalid("业务实体类型无效")
    actor = principal.email.strip().lower()
    try:
        item = WorkflowTaskEntityLink.objects.create(
            id=str(uuid.uuid4()), task=row, entity_type=str(entity_type), entity_id=_text(payload.get("entityId"), "业务实体标识", 240),
            label=_text(payload.get("label"), "业务实体名称", 240), url=_safe_url(payload.get("url", "")), created_by=actor,
        )
    except IntegrityError as error:
        raise WorkflowApiError("该业务对象已关联到当前工作事项", code="conflict", status=409) from error
    _append_task_activity(row, "link.created", "关联了业务对象", actor, {"linkId": item.id, "entityType": item.entity_type, "entityId": item.entity_id})
    bump_revision({"operation": "link_create", "id": item.id})
    return _link_item(item)


@transaction.atomic
def delete_link(task_id: object, link_id: object, principal: Principal) -> bool:
    row = _task_exists(task_id, lock=True); identifier = _resource_id(link_id, "关联标识")
    item = WorkflowTaskEntityLink.objects.select_for_update().filter(id=identifier, task=row).first()
    if item is None:
        return False
    item.delete(); _append_task_activity(row, "link.deleted", "移除了业务关联", principal.email.strip().lower(), {"linkId": identifier})
    bump_revision({"operation": "link_delete", "id": identifier})
    return True


@transaction.atomic
def create_attachment_metadata(task_id: object, payload: dict[str, object], principal: Principal) -> dict[str, object]:
    _keys(payload, {"id", "fileName", "mimeType", "sizeBytes", "sha256", "objectKey"})
    row = _task_exists(task_id, lock=True)
    if row.attachments.count() >= 100:
        raise WorkflowApiError("每个工作事项最多保存 100 个附件", code="conflict", status=409)
    identifier = _resource_id(payload.get("id"), "附件标识")
    object_key = _text(payload.get("objectKey"), "附件对象键", 500)
    if object_key != f"workflow-attachments/{row.id}/{identifier}":
        _invalid("附件对象键与工作事项不匹配")
    sha256 = _text(payload.get("sha256"), "附件摘要", 64)
    if not re.fullmatch(r"[a-f0-9]{64}", sha256):
        _invalid("附件摘要无效")
    size = _positive(payload.get("sizeBytes"), "附件大小", maximum=10 * 1024 * 1024)
    actor = principal.email.strip().lower()
    try:
        item = WorkflowTaskAttachment.objects.create(
            id=identifier, task=row, file_name=_text(payload.get("fileName"), "附件名称", 255),
            mime_type=_text(payload.get("mimeType"), "附件类型", 120), size_bytes=size, sha256=sha256,
            object_key=object_key, created_by=actor,
        )
    except IntegrityError as error:
        raise WorkflowApiError("附件标识或对象键已存在", code="conflict", status=409) from error
    _append_task_activity(row, "attachment.created", "上传了附件", actor, {"attachmentId": item.id, "fileName": item.file_name, "sizeBytes": size})
    bump_revision({"operation": "attachment_create", "id": item.id})
    return _attachment_item(item)


def attachment_metadata(task_id: object, attachment_id: object) -> dict[str, object] | None:
    row = _task_exists(task_id); identifier = _resource_id(attachment_id, "附件标识")
    item = WorkflowTaskAttachment.objects.filter(id=identifier, task=row).first()
    return _attachment_item(item, internal=True) if item else None


@transaction.atomic
def delete_attachment_metadata(task_id: object, attachment_id: object, principal: Principal) -> dict[str, object] | None:
    row = _task_exists(task_id, lock=True); identifier = _resource_id(attachment_id, "附件标识")
    item = WorkflowTaskAttachment.objects.select_for_update().filter(id=identifier, task=row).first()
    if item is None:
        return None
    object_key = item.object_key; file_name = item.file_name
    WorkflowAttachmentCleanup.objects.update_or_create(object_key=object_key, defaults={"updated_at": timezone.now()})
    item.delete()
    _append_task_activity(row, "attachment.deleted", "删除了附件", principal.email.strip().lower(), {"attachmentId": identifier, "fileName": file_name})
    bump_revision({"operation": "attachment_delete", "id": identifier})
    return {"id": identifier, "deleted": True, "cleanupObjectKey": object_key}


def cleanup_batch(limit: int) -> dict[str, object]:
    rows = list(WorkflowAttachmentCleanup.objects.order_by("updated_at", "object_key")[:limit])
    return {"items": [{"objectKey": row.object_key, "attempts": row.attempts} for row in rows]}


@transaction.atomic
def cleanup_result(payload: dict[str, object]) -> dict[str, object]:
    _keys(payload, {"objectKey", "deleted", "error", "enqueue"})
    object_key = _text(payload.get("objectKey"), "附件对象键", 500)
    if not object_key.startswith("workflow-attachments/"):
        _invalid("附件对象键超出允许范围")
    if payload.get("enqueue") is True:
        row, _created = WorkflowAttachmentCleanup.objects.update_or_create(
            object_key=object_key,
            defaults={"updated_at": timezone.now()},
        )
        bump_revision({"operation": "attachment_cleanup_enqueue", "objectKey": object_key})
        return {"acknowledged": True, "enqueued": True, "attempts": row.attempts}
    row = WorkflowAttachmentCleanup.objects.select_for_update().filter(object_key=object_key).first()
    if row is None:
        return {"acknowledged": False}
    if payload.get("deleted") is True:
        row.delete()
        bump_revision({"operation": "attachment_cleanup_complete", "objectKey": object_key})
        return {"acknowledged": True, "removed": True}
    error = _text(payload.get("error", "object_delete_failed"), "清理错误", 500, required=False, fallback="object_delete_failed")
    row.attempts += 1; row.last_error = error; row.updated_at = timezone.now(); row.save()
    bump_revision({"operation": "attachment_cleanup_failed", "objectKey": object_key, "attempts": row.attempts})
    return {"acknowledged": True, "removed": False, "attempts": row.attempts}


def list_templates(include_inactive: bool) -> list[dict[str, object]]:
    query = WorkflowTaskTemplate.objects.all()
    if not include_inactive:
        query = query.filter(active=True)
    return [_template_item(row) for row in query.order_by("-active", "-updated_at", "-id")[:200]]


def _offset(value: object, label: str, fallback: int) -> int:
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, int) or value < -365 or value > 365:
        _invalid(f"{label}必须是 -365 至 365 的整数")
    return value


def _normalize_template(payload: dict[str, object], current: WorkflowTaskTemplate | None = None) -> dict[str, object]:
    def field(key: str, attr: str, label: str, maximum: int, required: bool = False, fallback: str = "") -> str:
        if current is not None and key not in payload:
            return str(getattr(current, attr))
        return _text(payload.get(key, fallback), label, maximum, required=required, fallback=fallback)
    priority = current.priority if current and "priority" not in payload else payload.get("priority", "normal")
    if priority not in PRIORITIES:
        _invalid("模板紧急程度无效")
    active = current.active if current and "active" not in payload else payload.get("active", True)
    if not isinstance(active, bool):
        _invalid("模板启用状态无效")
    start = _offset(payload.get("startOffsetDays"), "开始偏移天数", current.start_offset_days if current else 0)
    due = _offset(payload.get("dueOffsetDays"), "截止偏移天数", current.due_offset_days if current else 0)
    if due < start:
        _invalid("截止偏移天数不能早于开始偏移天数")
    return {
        "name": field("name", "name", "模板名称", 120, True), "description": field("description", "description", "模板说明", 500),
        "title": field("title", "title", "工作事项", 160), "work_content": field("workContent", "work_content", "工作内容", 2_000),
        "category": field("category", "category", "事项分类", 80, False, "工作计划") or "工作计划",
        "owner": field("owner", "owner", "跟进人", 120), "shop_name": field("shopName", "shop_name", "店铺", 160),
        "start_offset_days": start, "due_offset_days": due, "priority": priority, "active": active,
    }


@transaction.atomic
def create_template(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    editable = {"name", "description", "title", "workContent", "category", "owner", "shopName", "startOffsetDays", "dueOffsetDays", "priority", "active"}
    _keys(payload, editable); actor = principal.email.strip().lower()
    row = WorkflowTaskTemplate.objects.create(id=str(uuid.uuid4()), created_by=actor, updated_by=actor, **_normalize_template(payload))
    bump_revision({"operation": "template_create", "id": row.id})
    return _template_item(row)


@transaction.atomic
def update_template(template_id: object, payload: dict[str, object], principal: Principal) -> dict[str, object] | None:
    editable = {"name", "description", "title", "workContent", "category", "owner", "shopName", "startOffsetDays", "dueOffsetDays", "priority", "active"}
    _keys(payload, editable | {"expectedVersion"}, require_change=editable)
    identifier = _resource_id(template_id, "模板标识"); row = WorkflowTaskTemplate.objects.select_for_update().filter(id=identifier).first()
    if row is None:
        return None
    expected = _positive(payload.get("expectedVersion"), "预期版本")
    if row.version != expected:
        raise WorkflowApiError("工作模板已被其他人更新，请刷新后重试", code="version_conflict", status=409)
    normalized = _normalize_template(payload, row)
    if all(getattr(row, field) == value for field, value in normalized.items()):
        _invalid("工作模板没有发生变化")
    for field, value in normalized.items(): setattr(row, field, value)
    row.version += 1; row.mutation_token = uuid.uuid4().hex; row.updated_by = principal.email.strip().lower(); row.updated_at = timezone.now(); row.save()
    bump_revision({"operation": "template_update", "id": row.id, "version": row.version})
    return _template_item(row)


@transaction.atomic
def delete_template(template_id: object, expected_version: object) -> bool:
    identifier = _resource_id(template_id, "模板标识"); row = WorkflowTaskTemplate.objects.select_for_update().filter(id=identifier).first()
    if row is None:
        return False
    expected = _positive(expected_version, "预期版本")
    if row.version != expected:
        raise WorkflowApiError("工作模板已被其他人更新，请刷新后重试", code="version_conflict", status=409)
    row.delete(); bump_revision({"operation": "template_delete", "id": identifier, "version": expected + 1})
    return True


def _scope_query(principal: Principal) -> Q:
    if principal.scope is None:
        return Q()
    channels = principal.scope.get("channels", []); platforms = principal.scope.get("platforms", [])
    if not channels and not platforms:
        return Q(pk__in=[])
    result = Q()
    if channels: result |= Q(channel__in=channels)
    if platforms: result |= Q(platform__in=platforms)
    return result


def _assert_record_scope(principal: Principal, platform: str, channel: str) -> None:
    if principal.scope is not None and platform not in principal.scope.get("platforms", []) and channel not in principal.scope.get("channels", []):
        raise WorkflowApiError("当前账号不能写入该平台或渠道的运营记录", code="access_denied", status=403)


def list_records(options: dict[str, object], principal: Principal) -> dict[str, object]:
    query = WorkflowOperationRecord.objects.filter(deleted_at__isnull=True).filter(_scope_query(principal))
    mapping = {"priorities": "priority__in", "types": "record_type__in", "statuses": "status__in", "shop_names": "shop_name__in", "platforms": "platform__in", "owners": "owner__in"}
    for key, lookup in mapping.items():
        if options.get(key): query = query.filter(**{lookup: options[key]})
    search = str(options.get("query") or "")
    if search:
        query = query.filter(Q(title__icontains=search) | Q(content__icontains=search) | Q(shop_name__icontains=search) | Q(owner__icontains=search) | Q(reference_code__icontains=search))
    if options.get("from_time"): query = query.filter(occurred_at__gte=options["from_time"])
    if options.get("to_time"): query = query.filter(occurred_at__lt=options["to_time"])
    page = int(options["page"]); page_size = int(options["page_size"]); offset = (page - 1) * page_size; total = query.count()
    items = [_record_item(row) for row in query.order_by("-occurred_at", "-id")[offset:offset + page_size]]
    return {
        "items": items,
        "pagination": {"page": page, "pageSize": page_size, "total": total, "returned": len(items), "truncated": offset + len(items) < total},
        "filtersApplied": {
            "types": options.get("types") or [], "statuses": options.get("statuses") or [], "shopNames": options.get("shop_names") or [],
            "platforms": options.get("platforms") or [], "owners": options.get("owners") or [], "query": search,
            "from": _iso(options.get("from_time")), "to": _iso(options.get("to_time")), "page": page, "pageSize": page_size,
            "dataScope": "unrestricted" if principal.scope is None else "restricted",
        },
    }


def _visible_record(record_id: object, principal: Principal, *, lock: bool = False, include_deleted: bool = False) -> WorkflowOperationRecord | None:
    identifier = _resource_id(record_id, "记录标识"); query = WorkflowOperationRecord.objects.select_for_update() if lock else WorkflowOperationRecord.objects
    query = query.filter(id=identifier).filter(_scope_query(principal))
    if not include_deleted: query = query.filter(deleted_at__isnull=True)
    return query.first()


def get_record(record_id: object, principal: Principal) -> dict[str, object] | None:
    row = _visible_record(record_id, principal)
    return _record_item(row) if row else None


def _normalize_record(payload: dict[str, object], principal: Principal, current: WorkflowOperationRecord | None = None) -> dict[str, object]:
    record_type = current.record_type if current else payload.get("type")
    if record_type not in RECORD_TYPES: _invalid("运营记录类型无效")
    source = current.source if current else payload.get("source", "manual")
    if source not in RECORD_SOURCES: _invalid("记录来源无效")
    if current is None and source != "manual" and principal.role != "admin":
        raise WorkflowApiError("只有管理员可以登记非手工来源的运营记录", code="access_denied", status=403)
    def field(key: str, attr: str, label: str, maximum: int, *, required: bool = False, fallback: str = "") -> str:
        if current and key not in payload: return str(getattr(current, attr))
        return _text(payload.get(key, fallback), label, maximum, required=required, fallback=fallback)
    platform = field("platform", "platform", "平台", 80); channel = field("channel", "channel", "渠道", 80)
    _assert_record_scope(principal, platform, channel)
    default_status = "正常" if record_type == "inspection" else "待回复"
    status = current.status if current and "status" not in payload else payload.get("status", default_status)
    if status not in RECORD_STATUSES[str(record_type)]: _invalid(f"{record_type} 的状态无效")
    priority = current.priority if current and "priority" not in payload else payload.get("priority", "normal")
    if priority not in PRIORITIES: _invalid("优先级无效")
    occurred_at = current.occurred_at if current and "occurredAt" not in payload else _datetime(payload.get("occurredAt"), "发生时间")
    due_at = current.due_at if current and "dueAt" not in payload else _datetime(payload.get("dueAt"), "截止时间", nullable=True)
    if due_at is not None and due_at < occurred_at:
        _invalid("截止时间不能早于发生时间")
    return {
        "record_type": record_type, "title": field("title", "title", "标题", 200, required=True), "status": status, "priority": priority,
        "platform": platform, "channel": channel, "shop_name": field("shopName", "shop_name", "店铺", 160, required=True),
        "owner": field("owner", "owner", "责任人", 120), "occurred_at": occurred_at, "due_at": due_at,
        "content": field("content", "content", "内容", 4_000), "source": source,
        "source_ref": field("sourceRef", "source_ref", "来源标识", 300), "reference_code": field("referenceCode", "reference_code", "业务参考编码", 160),
    }


@transaction.atomic
def create_record(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    allowed = {"type", "title", "status", "priority", "platform", "channel", "shopName", "owner", "occurredAt", "dueAt", "content", "source", "sourceRef", "referenceCode"}
    _keys(payload, allowed); normalized = _normalize_record(payload, principal); actor = principal.email.strip().lower()
    row = WorkflowOperationRecord.objects.create(id=str(uuid.uuid4()), created_by=actor, updated_by=actor, **normalized)
    changed = ["type", "title", "status", "priority", "platform", "channel", "shopName", "owner", "occurredAt", "dueAt", "content", "source", "sourceRef", "referenceCode"]
    WorkflowOperationActivity.objects.create(
        id=str(uuid.uuid4()), record=row, action="created", actor_email=actor, actor_role=principal.role, to_version=1,
        detail={"changedFields": changed, "fromStatus": None, "toStatus": row.status},
    )
    bump_revision({"operation": "record_create", "id": row.id})
    return _record_item(row)


@transaction.atomic
def update_record(record_id: object, payload: dict[str, object], principal: Principal) -> dict[str, object] | None:
    editable = {"title", "status", "priority", "platform", "channel", "shopName", "owner", "occurredAt", "dueAt", "content", "sourceRef", "referenceCode"}
    _keys(payload, editable | {"expectedVersion"}, require_change=editable)
    row = _visible_record(record_id, principal, lock=True)
    if row is None: return None
    expected = _positive(payload.get("expectedVersion"), "预期版本")
    if row.version != expected: raise WorkflowApiError("运营记录已被其他人更新，请刷新后重试", code="version_conflict", status=409)
    normalized = _normalize_record(payload, principal, row)
    public_names = {"record_type": "type", "shop_name": "shopName", "occurred_at": "occurredAt", "due_at": "dueAt", "source_ref": "sourceRef", "reference_code": "referenceCode"}
    changed = [public_names.get(field, field) for field, value in normalized.items() if getattr(row, field) != value]
    if not changed: _invalid("运营记录没有发生变化")
    previous_status = row.status
    for field, value in normalized.items(): setattr(row, field, value)
    row.version += 1; row.mutation_token = uuid.uuid4().hex; row.updated_by = principal.email.strip().lower(); row.updated_at = timezone.now(); row.save()
    WorkflowOperationActivity.objects.create(
        id=str(uuid.uuid4()), record=row, action="status_changed" if previous_status != row.status else "updated",
        actor_email=row.updated_by, actor_role=principal.role, from_version=expected, to_version=row.version,
        detail={"changedFields": changed, "fromStatus": previous_status, "toStatus": row.status},
    )
    bump_revision({"operation": "record_update", "id": row.id, "version": row.version})
    return _record_item(row)


@transaction.atomic
def delete_record(record_id: object, expected_version: object, principal: Principal) -> dict[str, object] | None:
    row = _visible_record(record_id, principal, lock=True)
    if row is None: return None
    expected = _positive(expected_version, "预期版本")
    if row.version != expected: raise WorkflowApiError("运营记录已被其他人更新，请刷新后重试", code="version_conflict", status=409)
    previous_status = row.status; row.version += 1; row.mutation_token = uuid.uuid4().hex; row.deleted_at = timezone.now(); row.deleted_by = principal.email.strip().lower(); row.updated_by = row.deleted_by; row.updated_at = row.deleted_at; row.save()
    WorkflowOperationActivity.objects.create(
        id=str(uuid.uuid4()), record=row, action="deleted", actor_email=row.updated_by, actor_role=principal.role,
        from_version=expected, to_version=row.version,
        detail={"changedFields": ["deletedAt"], "fromStatus": previous_status, "toStatus": previous_status},
    )
    bump_revision({"operation": "record_delete", "id": row.id, "version": row.version})
    return {"id": row.id, "deleted": True, "version": int(row.version)}


def record_activities(record_id: object, page: int, page_size: int, principal: Principal) -> dict[str, object]:
    row = _visible_record(record_id, principal, include_deleted=True)
    if row is None: raise WorkflowApiError("运营记录不存在或不可访问", code="not_found", status=404)
    query = row.activities.order_by("-to_version", "-id"); total = query.count(); offset = (page - 1) * page_size
    items = [_record_activity_item(item) for item in query[offset:offset + page_size]]
    return {"items": items, "pagination": {"page": page, "pageSize": page_size, "total": total, "returned": len(items), "truncated": offset + len(items) < total}}


def _lock_inventory_work_item_identity(entity_id: str) -> None:
    """Serialize PostgreSQL get-or-create decisions for one inventory identity."""
    if connection.vendor != "postgresql":
        return
    key = int.from_bytes(
        hashlib.sha256(f"workflow-inventory-work-item:{entity_id}".encode()).digest()[:8],
        byteorder="big",
        signed=True,
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


@transaction.atomic
def create_inventory_work_item(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    allowed = {"entityId", "entityType", "label", "url", "title", "workContent", "category", "owner", "shopName", "startDate", "due", "priority"}
    _keys(payload, allowed)
    entity_id = _text(payload.get("entityId"), "库存事项标识", 240)
    entity_type = payload.get("entityType", "product")
    if entity_type != "product":
        _invalid("库存执行事项只允许关联货品实体")
    _lock_inventory_work_item_identity(entity_id)
    existing_link = WorkflowTaskEntityLink.objects.select_for_update().filter(
        entity_type="product",
        entity_id=entity_id,
        task__deleted_at__isnull=True,
    ).exclude(task__status="已完成").select_related("task").first()
    if existing_link:
        return {"created": False, "task": _task_item(existing_link.task)}
    task_payload = {key: payload[key] for key in ("title", "workContent", "category", "owner", "shopName", "startDate", "due", "priority") if key in payload}
    normalized = _normalize_task(task_payload); actor = principal.email.strip().lower()
    row = WorkflowTask.objects.create(id=str(uuid.uuid4()), created_by=actor, updated_by=actor, **normalized)
    _append_task_activity(row, "task.created", "创建了工作事项", actor, {"version": 1, "source": "inventory"})
    WorkflowTaskEntityLink.objects.create(
        id=str(uuid.uuid4()), task=row, entity_type="product", entity_id=entity_id,
        label=_text(payload.get("label"), "业务实体名称", 240), url=_safe_url(payload.get("url", "")), created_by=actor,
    )
    _append_task_activity(row, "link.created", "关联了业务对象", actor, {"entityType": "product", "entityId": entity_id})
    bump_revision({"operation": "inventory_work_item_create", "id": row.id, "entityId": entity_id})
    return {"created": True, "task": _task_item(row)}
