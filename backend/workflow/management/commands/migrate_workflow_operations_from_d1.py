from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timezone as datetime_timezone
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from workflow.models import (
    WorkflowAttachmentCleanup,
    WorkflowOperationActivity,
    WorkflowOperationRecord,
    WorkflowOperationsMigrationRun,
    WorkflowOperationsWriteAuthority,
    WorkflowTask,
    WorkflowTaskActivityLog,
    WorkflowTaskAttachment,
    WorkflowTaskComment,
    WorkflowTaskEntityLink,
    WorkflowTaskReminder,
    WorkflowTaskTemplate,
)
from workflow.revisions import bump_revision


RUN_RE = re.compile(r"^workflow-ops-[0-9a-f]{32}$")
RESOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
GENERATION_VERSION = "workflow-operations-migration-v1"
TABLES = (
    "tasks", "comments", "activity_logs", "reminders", "templates", "entity_links",
    "attachments", "cleanup_queue", "operation_records", "operation_activities",
)
SOURCE_TABLES = {
    "workflow_tasks", "workflow_task_states", "workflow_task_comments",
    "workflow_task_activity_logs", "workflow_task_reminders", "workflow_task_templates",
    "workflow_task_template_states", "workflow_task_entity_links", "workflow_task_attachments",
    "workflow_attachment_cleanup_queue", "workflow_operation_records", "workflow_operation_activities",
}
MODELS = {
    "tasks": WorkflowTask,
    "comments": WorkflowTaskComment,
    "activity_logs": WorkflowTaskActivityLog,
    "reminders": WorkflowTaskReminder,
    "templates": WorkflowTaskTemplate,
    "entity_links": WorkflowTaskEntityLink,
    "attachments": WorkflowTaskAttachment,
    "cleanup_queue": WorkflowAttachmentCleanup,
    "operation_records": WorkflowOperationRecord,
    "operation_activities": WorkflowOperationActivity,
}
DATETIME_FIELDS = {
    "tasks": {"created_at", "updated_at", "deleted_at"},
    "comments": {"created_at"},
    "activity_logs": {"created_at"},
    "reminders": {"remind_at", "created_at", "updated_at"},
    "templates": {"created_at", "updated_at"},
    "entity_links": {"created_at"},
    "attachments": {"created_at"},
    "cleanup_queue": {"enqueued_at", "updated_at"},
    "operation_records": {"occurred_at", "due_at", "created_at", "updated_at", "deleted_at"},
    "operation_activities": {"created_at"},
}


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def normalized_datetime(value: object, label: str, *, nullable: bool = False) -> str | None:
    if value is None or value == "":
        if nullable:
            return None
        raise CommandError(f"{label} 缺少日期时间")
    if not isinstance(value, str):
        raise CommandError(f"{label} 日期时间类型无效")
    text = value.strip()
    parsed = parse_datetime(text.replace(" ", "T", 1) + ("Z" if "T" in text and not re.search(r"(?:Z|[+-]\d{2}:\d{2})$", text) else ""))
    if parsed is None:
        raise CommandError(f"{label} 日期时间格式无效")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime_timezone.utc)
    return parsed.astimezone(datetime_timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parsed_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        raise CommandError("规范化日期时间无法回读")
    return parsed


def json_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, str):
        raise CommandError(f"{label} JSON 类型无效")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise CommandError(f"{label} JSON 无效") from error
    if not isinstance(parsed, dict):
        raise CommandError(f"{label} 必须是 JSON 对象")
    return parsed


def valid_calendar_date(value: object) -> bool:
    if value == "待排期":
        return True
    if not isinstance(value, str):
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return parsed.isoformat() == value


def source_path_digest(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).casefold().encode("utf-8")).hexdigest()


def rows(connection: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql).fetchall()]


def read_source(
    path: Path,
    *,
    existing_connection: sqlite3.Connection | None = None,
) -> dict[str, list[dict[str, object]]]:
    if not path.is_file():
        raise CommandError("D1 SQLite 快照不存在")
    owns_connection = existing_connection is None
    if existing_connection is None:
        uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
    else:
        connection = existing_connection
    connection.row_factory = sqlite3.Row
    try:
        quick = connection.execute("PRAGMA quick_check").fetchone()
        if quick is None or quick[0] != "ok":
            raise CommandError("D1 SQLite 快照 quick_check 未通过")
        present = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(SOURCE_TABLES - present)
        if missing:
            raise CommandError(f"D1 SQLite 快照缺少运营事务表：{', '.join(missing)}")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise CommandError("D1 SQLite 快照存在外键异常")
        task_count = int(connection.execute("SELECT COUNT(*) FROM workflow_tasks").fetchone()[0])
        task_state_count = int(connection.execute("SELECT COUNT(*) FROM workflow_task_states").fetchone()[0])
        template_count = int(connection.execute("SELECT COUNT(*) FROM workflow_task_templates").fetchone()[0])
        template_state_count = int(connection.execute("SELECT COUNT(*) FROM workflow_task_template_states").fetchone()[0])
        if task_count != task_state_count:
            raise CommandError("D1 工作事项与状态表无法一一合并")
        if template_count != template_state_count:
            raise CommandError("D1 模板与状态表无法一一合并")

        snapshot: dict[str, list[dict[str, object]]] = {}
        snapshot["tasks"] = rows(connection, """
            SELECT t.id,t.title,t.work_content,t.category,t.owner,t.shop_name,t.start_date,t.due_date,
              t.status,t.priority,s.version,s.mutation_token,t.created_by,t.updated_by,t.created_at,t.updated_at,
              s.deleted_at,s.deleted_by
            FROM workflow_tasks t JOIN workflow_task_states s ON s.task_id=t.id ORDER BY t.id
        """)
        snapshot["comments"] = rows(connection, "SELECT id,task_id,content,created_by,created_at FROM workflow_task_comments ORDER BY id")
        raw_activity = rows(connection, "SELECT id,task_id,action,summary,metadata_json,actor_email,created_at FROM workflow_task_activity_logs ORDER BY id")
        snapshot["activity_logs"] = [
            {**{key: value for key, value in row.items() if key != "metadata_json"}, "metadata": json_object(row["metadata_json"], f"activity {row['id']}")}
            for row in raw_activity
        ]
        snapshot["reminders"] = rows(connection, "SELECT id,task_id,remind_at,note,status,created_by,created_at,updated_at FROM workflow_task_reminders ORDER BY id")
        snapshot["templates"] = rows(connection, """
            SELECT t.id,t.name,t.description,t.title,t.work_content,t.category,t.owner,t.shop_name,
              t.start_offset_days,t.due_offset_days,t.priority,t.active,s.version,s.mutation_token,
              t.created_by,t.updated_by,t.created_at,t.updated_at
            FROM workflow_task_templates t JOIN workflow_task_template_states s ON s.template_id=t.id ORDER BY t.id
        """)
        snapshot["entity_links"] = rows(connection, "SELECT id,task_id,entity_type,entity_id,label,url,created_by,created_at FROM workflow_task_entity_links ORDER BY id")
        snapshot["attachments"] = rows(connection, "SELECT id,task_id,file_name,mime_type,size_bytes,sha256,object_key,created_by,created_at FROM workflow_task_attachments ORDER BY id")
        snapshot["cleanup_queue"] = rows(connection, "SELECT object_key,attempts,last_error,enqueued_at,updated_at FROM workflow_attachment_cleanup_queue ORDER BY object_key")
        snapshot["operation_records"] = rows(connection, """
            SELECT id,record_type,title,status,priority,platform,channel,shop_name,owner,occurred_at,due_at,
              content,source,source_ref,reference_code,version,mutation_token,created_by,updated_by,
              created_at,updated_at,deleted_at,deleted_by
            FROM workflow_operation_records ORDER BY id
        """)
        raw_operation_activity = rows(connection, "SELECT id,record_id,action,actor_email,actor_role,from_version,to_version,detail_json,created_at FROM workflow_operation_activities ORDER BY id")
        snapshot["operation_activities"] = [
            {**{key: value for key, value in row.items() if key != "detail_json"}, "detail": json_object(row["detail_json"], f"operation activity {row['id']}")}
            for row in raw_operation_activity
        ]
    finally:
        if owns_connection:
            connection.close()

    for name, table_rows in snapshot.items():
        nullable = {"deleted_at", "due_at"}
        for row in table_rows:
            for field in DATETIME_FIELDS[name]:
                row[field] = normalized_datetime(row.get(field), f"{name}.{row.get('id', row.get('object_key'))}.{field}", nullable=field in nullable)
    # Legacy soft-delete state rows store NULL while the terminal PostgreSQL
    # contract uses an empty string for "not deleted". Keep the absence of an
    # actor explicit without inventing an identity.
    for name in ("tasks", "operation_records"):
        for row in snapshot[name]:
            row["deleted_by"] = str(row.get("deleted_by") or "")
    for row in snapshot["templates"]:
        if row["active"] not in (0, 1, False, True):
            raise CommandError("工作模板启用状态无效")
        row["active"] = bool(row["active"])
    validate_snapshot(snapshot)
    return snapshot


def validate_snapshot(snapshot: dict[str, list[dict[str, object]]]) -> None:
    task_ids = {str(row["id"]) for row in snapshot["tasks"]}
    record_ids = {str(row["id"]) for row in snapshot["operation_records"]}
    for name in ("tasks", "comments", "activity_logs", "reminders", "templates", "entity_links", "attachments", "operation_records", "operation_activities"):
        ids = [str(row["id"]) for row in snapshot[name]]
        if len(ids) != len(set(ids)) or any(not RESOURCE_ID_RE.fullmatch(value) for value in ids):
            raise CommandError(f"{name} 存在重复或非法 ID")
    for name in ("comments", "activity_logs", "reminders", "entity_links", "attachments"):
        if any(str(row["task_id"]) not in task_ids for row in snapshot[name]):
            raise CommandError(f"{name} 存在孤儿 task_id")
    if any(str(row["record_id"]) not in record_ids for row in snapshot["operation_activities"]):
        raise CommandError("operation_activities 存在孤儿 record_id")
    if any(int(row["version"]) < 1 for row in snapshot["tasks"] + snapshot["templates"] + snapshot["operation_records"]):
        raise CommandError("运营事务快照存在非法版本")
    if any(row["status"] not in {"待开始", "工作中", "已完成"} or row["priority"] not in {"high", "normal", "low"} for row in snapshot["tasks"]):
        raise CommandError("工作事项状态或优先级无效")
    for row in snapshot["tasks"]:
        start = row["start_date"]
        due = row["due_date"]
        if not valid_calendar_date(start) or not valid_calendar_date(due):
            raise CommandError("工作事项日期格式无效")
        if start != "待排期" and due != "待排期" and str(due) < str(start):
            raise CommandError("工作事项截止时间早于开始时间")
    for row in snapshot["templates"]:
        start_offset = row["start_offset_days"]
        due_offset = row["due_offset_days"]
        if (
            isinstance(start_offset, bool)
            or isinstance(due_offset, bool)
            or not isinstance(start_offset, int)
            or not isinstance(due_offset, int)
            or not -365 <= start_offset <= 365
            or not -365 <= due_offset <= 365
            or due_offset < start_offset
            or row["priority"] not in {"high", "normal", "low"}
        ):
            raise CommandError("工作模板偏移、状态或优先级无效")
    if any(row["status"] not in {"pending", "dismissed", "sent"} for row in snapshot["reminders"]):
        raise CommandError("工作事项提醒状态无效")
    if any(row["entity_type"] not in {"shop", "product", "campaign", "order", "report", "url"} for row in snapshot["entity_links"]):
        raise CommandError("工作事项业务实体类型无效")
    record_statuses = {
        "inspection": {"正常", "待处理", "处理中", "已关闭"},
        "review": {"待回复", "处理中", "已回复", "无需回复"},
    }
    for row in snapshot["operation_records"]:
        record_type = row["record_type"]
        if record_type not in record_statuses:
            raise CommandError("运营记录包含已退役 launch 或未知类型")
        if (
            row["status"] not in record_statuses[str(record_type)]
            or row["priority"] not in {"high", "normal", "low"}
            or row["source"] not in {"manual", "system", "import", "integration"}
        ):
            raise CommandError("运营记录状态、优先级或来源无效")
        if row["due_at"] is not None and str(row["due_at"]) < str(row["occurred_at"]):
            raise CommandError("运营记录截止时间早于发生时间")
    if any(
        str(row["task_id"]) not in task_ids
        or str(row["object_key"]) != f"workflow-attachments/{row['task_id']}/{row['id']}"
        for row in snapshot["attachments"]
    ):
        raise CommandError("附件对象键与工作事项不匹配")
    attachment_keys = [str(row["object_key"]) for row in snapshot["attachments"]]
    cleanup_keys = [str(row["object_key"]) for row in snapshot["cleanup_queue"]]
    if len(attachment_keys) != len(set(attachment_keys)) or len(cleanup_keys) != len(set(cleanup_keys)):
        raise CommandError("附件对象键重复")
    if any(not value.startswith("workflow-attachments/") for value in cleanup_keys):
        raise CommandError("附件清理对象键超出允许范围")
    if any(not re.fullmatch(r"[a-f0-9]{64}", str(row["sha256"])) or not 0 < int(row["size_bytes"]) <= 10 * 1024 * 1024 for row in snapshot["attachments"]):
        raise CommandError("附件摘要或大小无效")
    if any(
        row["action"] not in {"created", "updated", "status_changed", "deleted"}
        or int(row["to_version"]) < 1
        or (row["from_version"] is not None and int(row["from_version"]) >= int(row["to_version"]))
        for row in snapshot["operation_activities"]
    ):
        raise CommandError("运营记录活动版本或动作无效")


def target_snapshot() -> dict[str, list[dict[str, object]]]:
    fields = {
        "tasks": ("id", "title", "work_content", "category", "owner", "shop_name", "start_date", "due_date", "status", "priority", "version", "mutation_token", "created_by", "updated_by", "created_at", "updated_at", "deleted_at", "deleted_by"),
        "comments": ("id", "task_id", "content", "created_by", "created_at"),
        "activity_logs": ("id", "task_id", "action", "summary", "metadata", "actor_email", "created_at"),
        "reminders": ("id", "task_id", "remind_at", "note", "status", "created_by", "created_at", "updated_at"),
        "templates": ("id", "name", "description", "title", "work_content", "category", "owner", "shop_name", "start_offset_days", "due_offset_days", "priority", "active", "version", "mutation_token", "created_by", "updated_by", "created_at", "updated_at"),
        "entity_links": ("id", "task_id", "entity_type", "entity_id", "label", "url", "created_by", "created_at"),
        "attachments": ("id", "task_id", "file_name", "mime_type", "size_bytes", "sha256", "object_key", "created_by", "created_at"),
        "cleanup_queue": ("object_key", "attempts", "last_error", "enqueued_at", "updated_at"),
        "operation_records": ("id", "record_type", "title", "status", "priority", "platform", "channel", "shop_name", "owner", "occurred_at", "due_at", "content", "source", "source_ref", "reference_code", "version", "mutation_token", "created_by", "updated_by", "created_at", "updated_at", "deleted_at", "deleted_by"),
        "operation_activities": ("id", "record_id", "action", "actor_email", "actor_role", "from_version", "to_version", "detail", "created_at"),
    }
    result: dict[str, list[dict[str, object]]] = {}
    for name in TABLES:
        ordering = "object_key" if name == "cleanup_queue" else "id"
        table_rows = [dict(row) for row in MODELS[name].objects.order_by(ordering).values(*fields[name])]
        for row in table_rows:
            for field in DATETIME_FIELDS[name]:
                value = row.get(field)
                row[field] = value.astimezone(datetime_timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z") if value else None
        result[name] = table_rows
    validate_snapshot(result)
    return result


def materialized(name: str, row: dict[str, object]) -> dict[str, object]:
    result = dict(row)
    for field in DATETIME_FIELDS[name]:
        result[field] = parsed_datetime(result.get(field))  # type: ignore[arg-type]
    return result


def counts(snapshot: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    return {name: len(snapshot[name]) for name in TABLES}


def run_id(path_digest: str, snapshot_digest: str) -> str:
    return "workflow-ops-" + hashlib.sha256(f"{path_digest}:{snapshot_digest}".encode()).hexdigest()[:32]


class Command(BaseCommand):
    help = "Plan, atomically apply, or independently verify the remaining workflow D1 migration."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        parser.add_argument("--mode", required=True, choices=("dry-run", "apply", "verify-only"))
        parser.add_argument("--approved-run-id", default="")

    def handle(self, *args, **options):
        source = Path(options["source"]).resolve()
        source_data = read_source(source)
        path_hash = source_path_digest(source)
        source_hash = digest(source_data)
        expected_run_id = run_id(path_hash, source_hash)
        source_counts = counts(source_data)
        mode = str(options["mode"])
        approved = str(options["approved_run_id"] or "")

        if mode == "dry-run":
            self.stdout.write(canonical({
                "mode": mode, "runId": expected_run_id, "sourcePathDigest": path_hash,
                "sourceSnapshotDigest": source_hash, "sourceCounts": source_counts,
            }))
            return
        if not RUN_RE.fullmatch(approved) or approved != expected_run_id:
            raise CommandError("approved-run-id 与当前密封快照计划不匹配")
        authority = WorkflowOperationsWriteAuthority.objects.get(id=1)
        if authority.status != "disabled":
            raise CommandError("运营事务全板块 authority 已激活，禁止重复迁移")

        if mode == "verify-only":
            migration = WorkflowOperationsMigrationRun.objects.filter(id=approved, status="verified").first()
            if migration is None or migration.source_path_digest != path_hash or migration.source_snapshot_digest != source_hash:
                raise CommandError("找不到与当前快照绑定的已验证迁移记录")
            target_data = target_snapshot(); target_hash = digest(target_data)
            if target_hash != source_hash or counts(target_data) != source_counts or migration.target_snapshot_digest != target_hash:
                raise CommandError("PostgreSQL 运营事务回查与密封快照不一致")
            self.stdout.write(canonical({"mode": mode, "runId": approved, "verified": True, "targetSnapshotDigest": target_hash, "targetCounts": counts(target_data)}))
            return

        if any(model.objects.exists() for model in MODELS.values()):
            raise CommandError("PostgreSQL 运营事务迁移目标不是空集")
        if WorkflowOperationsMigrationRun.objects.exclude(id=approved).exists():
            raise CommandError("PostgreSQL 已存在其他运营事务迁移尝试")

        with transaction.atomic():
            locked = WorkflowOperationsWriteAuthority.objects.select_for_update().get(id=1)
            if locked.status != "disabled":
                raise CommandError("运营事务全板块 authority 状态已变化")
            for name in TABLES:
                objects = [MODELS[name](**materialized(name, row)) for row in source_data[name]]
                if objects:
                    MODELS[name].objects.bulk_create(objects, batch_size=500)
            target_data = target_snapshot(); target_hash = digest(target_data); target_counts = counts(target_data)
            if target_hash != source_hash or target_counts != source_counts:
                raise CommandError("迁移后 PostgreSQL 全量摘要回查不一致")
            WorkflowOperationsMigrationRun.objects.update_or_create(
                id=approved,
                defaults={
                    "mode": "apply", "status": "verified", "source_path_digest": path_hash,
                    "source_snapshot_digest": source_hash, "target_snapshot_digest": target_hash,
                    "source_counts": source_counts, "target_counts": target_counts,
                    "approved_run_id": approved,
                    "manifest": {
                        "version": GENERATION_VERSION,
                        "source": source.name,
                        "sourceDigest": source_hash,
                        "sourcePathDigest": path_hash,
                        "tables": list(TABLES),
                    },
                    "completed_at": timezone.now(),
                },
            )
            locked.migration_verify_run_id = approved
            locked.save(update_fields=["migration_verify_run_id", "updated_at"])
            bump_revision({"operation": "workflow_operations_migration", "runId": approved, "sourceDigest": source_hash})
        self.stdout.write(canonical({
            "mode": mode, "runId": approved, "verified": True,
            "sourceSnapshotDigest": source_hash, "targetSnapshotDigest": source_hash,
            "sourceCounts": source_counts, "targetCounts": source_counts,
        }))
