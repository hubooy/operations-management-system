"""Bounded, read-only D1 snapshots and atomic, approved PostgreSQL adoption.

No target clearing, production fallback, domain writes or authority activation
are part of importing a snapshot. Historical ciphertext and JSON are preserved.
"""

from __future__ import annotations
import json
import sqlite3
import uuid
from datetime import datetime, timezone as utc
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.utils import timezone
from .models import HISTORICAL_MODELS
from .control_models import AiDataRevision, AiMigrationRun, AiWriteAuthority
from .policy import canonical, digest, valid_scope

VERSION = "ai-d1-postgres-v1"
MAX_ROWS = 1_000_000
MAX_BYTES = 256 * 1024 * 1024
ADDED = {
    "ai_conversation_messages": {"ordinal", "execution_json"},
    "ai_models": {"generation_options_json"},
    "ai_chat_request_receipts": {"cancel_requested"},
}
LEGACY_DEFAULTS = {
    "ai_tool_audit_logs": {"invocation_id": "", "provider_call_id": None}
}


def compatible_columns(table, actual):
    expected = set(columns(HISTORICAL_MODELS[table])) - ADDED.get(table, set())
    optional = set(LEGACY_DEFAULTS.get(table, {}))
    return not actual - expected and not expected - actual - optional


def timestamp(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise CommandError("AI 历史时间戳无效") from error
    if timezone.is_naive(value):
        value = value.replace(tzinfo=utc.utc)
    return value.astimezone(utc.utc).isoformat(timespec="microseconds")


def columns(model):
    return {field.column: field for field in model._meta.local_concrete_fields}


def normalize(table, row, *, source=False):
    model = HISTORICAL_MODELS[table]
    expected = columns(model)
    actual = set(row) - ({"_source_ordinal"} if source else set())
    if source and not compatible_columns(table, actual):
        raise CommandError(f"{table} 源字段与当前迁移契约不一致")
    values = {}
    for column, field in expected.items():
        if source and column == "ordinal" and table == "ai_conversation_messages":
            value = row["_source_ordinal"]
        elif (
            source
            and column == "cancel_requested"
            and table == "ai_chat_request_receipts"
        ):
            value = 0
        elif source and column not in row and column in ADDED.get(table, set()):
            value = field.get_default()
        elif source and column not in row and column in LEGACY_DEFAULTS.get(table, {}):
            value = LEGACY_DEFAULTS[table][column]
        else:
            value = row[column]
        if field.get_internal_type() == "DateTimeField":
            value = timestamp(value)
        elif field.get_internal_type() == "BooleanField":
            value = int(value)
        if column.endswith("_json") and value is not None:
            try:
                parsed = json.loads(
                    value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
                )
            except (ValueError, TypeError) as error:
                raise CommandError(f"{table} 历史 JSON 无效") from error
            if column == "scope_json" and not valid_scope(parsed):
                raise CommandError(f"{table} 历史数据范围无效")
        values[column] = value
    return values


def order_rows(rows):
    return sorted(rows, key=canonical)


def source_snapshot(source):
    path = Path(source)
    if (
        not path.is_file()
        or path.is_symlink()
        or path.suffix.lower() not in {".sqlite", ".sqlite3"}
    ):
        raise CommandError("AI 迁移源必须是普通 SQLite 文件")
    path = path.resolve()
    snapshot = {}
    size = 0
    count = 0
    try:
        database = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA query_only=ON")
        database.execute("BEGIN")
        inventory = dict(
            database.execute(
                "SELECT name,type FROM sqlite_master WHERE name LIKE 'ai_%' AND type IN ('table','view')"
            )
        )
        if set(inventory) - set(HISTORICAL_MODELS) - {"ai_write_authority"}:
            raise CommandError("发现未声明的 AI 源对象，需先补齐迁移清单")
        if any(inventory.get(name) != "table" for name in HISTORICAL_MODELS):
            raise CommandError("AI 源权威表缺失或已退役")
        for table in HISTORICAL_MODELS:
            source_columns = {
                row[1] for row in database.execute(f'PRAGMA table_info("{table}")')
            }
            if not compatible_columns(table, source_columns):
                raise CommandError(f"{table} 源结构与迁移契约不一致")
            select = (
                "rowid AS _source_ordinal,*"
                if table == "ai_conversation_messages"
                else "*"
            )
            rows = []
            for row in database.execute(f'SELECT {select} FROM "{table}"'):
                normalized = normalize(table, dict(row), source=True)
                size += len(canonical(normalized).encode())
                count += 1
                if size > MAX_BYTES or count > MAX_ROWS:
                    raise CommandError("AI 迁移快照超过安全上限")
                rows.append(normalized)
            snapshot[table] = order_rows(rows)
        if snapshot["ai_memory_commit_guards"]:
            raise CommandError("源存在未完成记忆提交 guard")
        if any(
            r["status"] in {"processing", "dispatched"}
            for r in snapshot["ai_chat_request_receipts"]
        ):
            raise CommandError("源存在未收敛聊天派发；需先完成停写审查")
        if any(
            r["status"] in {"queued", "running", "paused", "waiting_review"}
            for table in ["ai_agent_jobs", "ai_workflow_runs", "ai_space_jobs"]
            for r in snapshot[table]
        ):
            raise CommandError("源存在活动 AI 长任务；需先完成租约与付费派发审查")
        if any(
            r["state"] == "calling"
            for table in ["ai_agent_provider_dispatches", "ai_agent_tool_dispatches"]
            for r in snapshot[table]
        ):
            raise CommandError("源存在结果不确定的 AI 外部派发，需先完成派发审查")
        schema_digest = digest(
            {
                table: [
                    tuple(row)
                    for row in database.execute(f'PRAGMA table_info("{table}")')
                ]
                for table in HISTORICAL_MODELS
            }
        )
        database.rollback()
    except sqlite3.DatabaseError as error:
        raise CommandError("AI 源只读快照失败") from error
    finally:
        if "database" in locals():
            database.close()
    return {
        "rows": snapshot,
        "digest": digest(snapshot),
        "counts": {name: len(rows) for name, rows in snapshot.items()},
        "schemaDigest": schema_digest,
        "pathDigest": digest(str(path)),
        "bytes": size,
    }


def target_snapshot():
    snapshot = {}
    count = 0
    size = 0
    for table, model in HISTORICAL_MODELS.items():
        fields = columns(model)
        rows = []
        for values in model.objects.values_list(
            *(field.attname for field in fields.values())
        ).iterator(chunk_size=1000):
            row = normalize(table, dict(zip(fields, values)))
            count += 1
            size += len(canonical(row).encode())
            if count > MAX_ROWS or size > MAX_BYTES:
                raise CommandError("AI 目标快照超限")
            rows.append(row)
        snapshot[table] = order_rows(rows)
    return {
        "rows": snapshot,
        "digest": digest(snapshot),
        "counts": {name: len(rows) for name, rows in snapshot.items()},
    }


def import_order():
    pending = dict(HISTORICAL_MODELS)
    ordered = []
    while pending:
        ready = [
            name
            for name, model in pending.items()
            if all(
                not field.is_relation
                or field.remote_field.model._meta.db_table not in pending
                for field in model._meta.local_concrete_fields
            )
        ]
        if not ready:
            raise CommandError("AI 外键依赖存在环")
        for table in ready:
            ordered.append(table)
            del pending[table]
    return ordered


def migrate(source, mode, approved=""):
    if (
        settings.DJANGO_ENVIRONMENT == "production"
        and settings.DJANGO_PROCESS_ROLE != "migration_writer"
    ):
        raise CommandError("生产 AI 迁移仅允许 migration_writer")
    if mode not in {"dry-run", "apply", "verify-only"} or bool(approved) != (
        mode == "apply"
    ):
        raise CommandError("迁移模式或精确批准 ID 无效")
    source = source_snapshot(source)
    manifest = {
        "version": VERSION,
        "sourceSchemaDigest": source["schemaDigest"],
        "normalizedBytes": source["bytes"],
    }
    with transaction.atomic():
        authority = AiWriteAuthority.objects.select_for_update().get(id=1)
        revision = AiDataRevision.objects.select_for_update().get(domain="ai-assistant")
        if mode != "verify-only" and authority.status != "d1":
            raise CommandError("AI authority 已激活，禁止重新迁移")
        identifier = (
            "ai-"
            + {"dry-run": "dryrun", "apply": "apply", "verify-only": "verify"}[mode]
            + "-"
            + uuid.uuid4().hex
        )
        target = target_snapshot()
        if mode in {"dry-run", "apply"} and any(target["counts"].values()):
            raise CommandError("AI 目标已有历史事实，禁止覆盖或清空")
        if mode == "apply":
            dry = (
                AiMigrationRun.objects.select_for_update()
                .filter(
                    id=approved,
                    mode="dry-run",
                    status="verified",
                    consumed_by_run_id="",
                )
                .first()
            )
            if (
                not dry
                or dry.source_path_digest != source["pathDigest"]
                or dry.source_snapshot_digest != source["digest"]
                or dry.source_counts != source["counts"]
                or dry.manifest != manifest
            ):
                raise CommandError("批准的 AI dry-run 已变化、已消费或不匹配")
            for table in import_order():
                model = HISTORICAL_MODELS[table]
                field_map = columns(model)
                rows = []
                for item in source["rows"][table]:
                    values = {
                        field_map[k].attname: datetime.fromisoformat(v)
                        if v is not None
                        and field_map[k].get_internal_type() == "DateTimeField"
                        else v
                        for k, v in item.items()
                    }
                    rows.append(model(**values))
                    if len(rows) >= 500:
                        model.objects.bulk_create(rows)
                        rows = []
                if rows:
                    model.objects.bulk_create(rows)
            connection.check_constraints(table_names=list(HISTORICAL_MODELS))
            target = target_snapshot()
            if (
                target["digest"] != source["digest"]
                or target["counts"] != source["counts"]
            ):
                raise CommandError("AI 事务内迁移回查不一致")
            revision.revision += 1
            revision.source_digest = source["digest"]
            revision.save()
            authority.migration_verify_run_id = identifier
            authority.save()
            dry.consumed_by_run_id = identifier
            dry.save(update_fields=["consumed_by_run_id"])
        elif mode == "verify-only" and (
            target["digest"] != source["digest"] or target["counts"] != source["counts"]
        ):
            raise CommandError("AI 源/目标计数或摘要不一致")
        AiMigrationRun.objects.create(
            id=identifier,
            mode=mode,
            status="verified",
            source_path_digest=source["pathDigest"],
            source_snapshot_digest=source["digest"],
            target_snapshot_digest=target["digest"],
            source_counts=source["counts"],
            target_counts=target["counts"],
            approved_run_id=approved,
            manifest=manifest,
            completed_at=timezone.now(),
        )
    return {
        "mode": mode,
        "status": "verified",
        "runId": identifier,
        "sourceDigest": source["digest"],
        "targetDigest": target["digest"],
        "counts": source["counts"],
        "revision": revision.revision,
    }
