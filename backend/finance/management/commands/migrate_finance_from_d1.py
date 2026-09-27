from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connection as target_connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from finance.import_service import JS_SAFE_INTEGER, _fingerprint, finance_scope_key
from finance.models import (
    FinanceDataRevision,
    FinanceImportAttempt,
    FinanceImportBatch,
    FinanceImportFingerprint,
    FinanceImportScopeHead,
    FinanceLine,
    FinanceMigrationRun,
    FinanceMonth,
    FinanceTarget,
    FinanceTargetDeletionAudit,
    FinanceWriteAuthority,
    FinanceWriteRequestReceipt,
)


FORMAT_VERSION = "finance-d1-migration-v3"
SOURCE_MANIFEST_FORMAT_VERSION = "finance-d1-rehearsal-snapshot-v1"
LEGACY_SYNTHESIS_VERSION = "finance-legacy-audit-synthesis-v1"
LEGACY_ATTEMPT_NAMESPACE = uuid.UUID("4a945c8f-6db2-42a5-9046-9d15e508a522")
ZERO_TOKEN = "0" * 64
MAX_PROJECTED_ROWS = 250_000
HEX_64 = frozenset("0123456789abcdef")

BATCH_COLUMNS = (
    "id", "source", "file_name", "file_size_bytes", "file_hash", "status",
    "row_count", "inserted_count", "duplicate_count", "warning_count",
    "parsed_month_count", "imported_month_count", "skipped_month_count",
    "subject_count", "months_json", "warnings_json", "created_at", "completed_at",
)
MONTH_COLUMNS = (
    "month", "batch_id", "sheet_name", "business_name", "source_file_name",
    "status", "shop_count", "subject_count", "imported_at",
)
LINE_COLUMNS = (
    "id", "month", "section", "metric_key", "subject_name", "scope_key",
    "scope_type", "scope_name", "group_name", "value_type", "amount_cents",
    "rate_bps", "raw_value", "source_row_count", "sort_order", "is_total",
    "created_at",
)
TARGET_COLUMNS = (
    "id", "period_type", "period_key", "platform", "shop_name", "category",
    "manager", "sales_target_cents", "profit_target_cents", "small_margin_bps",
    "inventory_cleanup_target_cents", "promotion_fee_ratio_bps",
    "stagnant_inventory_target_cents", "created_at", "updated_at",
)
SCOPED_AUDIT_COLUMNS = (
    "audit_id", "target_id", "period_type", "period_key", "platform", "shop_name",
    "category", "actor", "old_version", "expected_version", "reason", "deleted_at",
)
LEGACY_AUDIT_COLUMNS = (
    "audit_id", "target_id", "period_type", "period_key", "shop_name", "category",
    "actor", "old_version", "expected_version", "reason", "deleted_at",
)
FINGERPRINT_COLUMNS = (
    "batch_id", "scope_key", "raw_file_hash", "content_hash", "row_count",
    "status", "created_at",
)
ATTEMPT_COLUMNS = (
    "attempt_id", "batch_id", "scope_key", "scope_json", "import_hash",
    "raw_file_hash", "content_hash", "row_count", "file_name", "file_size_bytes",
    "actor", "warnings_json", "outcome", "error_code", "recovered_from_attempt_id",
    "created_at", "updated_at",
)
HEAD_COLUMNS = (
    "scope_key", "state_token", "status", "owner_token", "current_batch_id",
    "generation", "updated_at",
)
AUTHORITY_COLUMNS = ("id", "owner", "epoch", "cutover_id", "updated_at")

REQUIRED_SCHEMAS: dict[str, tuple[str, ...]] = {
    "finance_import_batches": BATCH_COLUMNS,
    "finance_months": MONTH_COLUMNS,
    "finance_lines": LINE_COLUMNS,
    "finance_targets": ("id",),
    "finance_target_versions": ("target_id", "version", "updated_at"),
    "finance_targets_scoped": TARGET_COLUMNS,
    "finance_target_scoped_versions": ("target_id", "version", "updated_at"),
    "finance_target_deletion_audits": LEGACY_AUDIT_COLUMNS,
    "finance_target_scoped_deletion_audits": SCOPED_AUDIT_COLUMNS,
    "finance_target_legacy_migrations": ("target_id", "migrated_at"),
    "import_content_fingerprints": (
        "domain", "batch_id", "scope_key", "raw_file_hash", "content_hash",
        "row_count", "status", "publication_sequence", "created_at",
    ),
    "import_content_attempts": (
        "attempt_id", "domain", "batch_id", "scope_key", "scope_json",
        "import_hash", "raw_file_hash", "content_hash", "row_count", "file_name",
        "file_size_bytes", "actor", "warnings_json", "outcome", "error_code",
        "recovered_from_attempt_id", "created_at", "updated_at",
    ),
    "import_scope_heads": (
        "domain", "scope_key", "state_token", "status", "owner_token",
        "current_batch_id", "generation", "updated_at",
    ),
    "finance_write_authority": AUTHORITY_COLUMNS,
}

PROJECTION_KEYS = (
    "batches", "months", "lines", "targets", "deletion_audits",
    "attempts", "fingerprints", "scope_head",
)


@dataclass(frozen=True)
class Snapshot:
    counts: dict[str, int]
    digests: dict[str, str]
    authority: dict[str, object]
    source_digest: str
    target_digest: str


def _canonical_value(value: object) -> object:
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            value = timezone.make_aware(value, datetime_timezone.utc)
        return value.astimezone(datetime_timezone.utc).isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            _canonical_value(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _json_value(value: object, *, kind: type[list] | type[dict]) -> list | dict:
    try:
        parsed = json.loads(str(value or "[]" if kind is list else value or "{}"))
    except json.JSONDecodeError as error:
        raise CommandError("D1 财务迁移材料包含无效 JSON。") from error
    if not isinstance(parsed, kind):
        raise CommandError("D1 财务迁移材料 JSON 类型不符合契约。")
    return parsed


def _time_value(value: object) -> datetime:
    parsed = parse_datetime(str(value or ""))
    if parsed is None:
        raise CommandError("D1 财务迁移材料包含无效时间。")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, datetime_timezone.utc)
    return parsed.astimezone(datetime_timezone.utc)


def _valid_hex(value: object) -> bool:
    text = str(value or "")
    return len(text) == 64 and set(text) <= HEX_64


def _source_uri(path: Path) -> str:
    return f"file:{path.as_posix()}?mode=ro"


def _open_source(path: Path) -> sqlite3.Connection:
    source = sqlite3.connect(_source_uri(path), uri=True, timeout=30)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only = ON")
    source.execute("BEGIN")
    return source


def _target_source_alias(source: Path) -> bool:
    if target_connection.vendor != "sqlite":
        return False
    target_name = str(target_connection.settings_dict.get("NAME") or "")
    if not target_name or target_name == ":memory:" or target_name.startswith("file:"):
        return False
    target = Path(target_name).expanduser().resolve()
    source = source.resolve()
    if source == target:
        return True
    try:
        return os.path.samefile(source, target)
    except (FileNotFoundError, OSError):
        return False


def _table_columns(source: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in source.execute(f'PRAGMA table_info("{table}")')}


def _normalized_batch_months(
    source: sqlite3.Connection, batch_id: str
) -> list[dict[str, object]]:
    months: list[dict[str, object]] = []
    for month in source.execute(
        "SELECT month, sheet_name, business_name, shop_count, subject_count "
        "FROM finance_months WHERE batch_id=? AND status='completed' "
        "ORDER BY month COLLATE BINARY",
        (batch_id,),
    ):
        lines: list[dict[str, object]] = []
        for line in source.execute(
            "SELECT month, section, metric_key, subject_name, scope_key, scope_type, "
            "scope_name, group_name, value_type, amount_cents, rate_bps, raw_value, "
            "source_row_count, sort_order, is_total FROM finance_lines "
            "WHERE month=? ORDER BY id",
            (month["month"],),
        ):
            lines.append({
                "month": line["month"],
                "section": line["section"],
                "metricKey": line["metric_key"],
                "subjectName": line["subject_name"],
                "scopeKey": line["scope_key"],
                "scopeType": line["scope_type"],
                "scopeName": line["scope_name"],
                "groupName": line["group_name"],
                "valueType": line["value_type"],
                "amountCents": line["amount_cents"],
                "rateBps": line["rate_bps"],
                "rawValue": line["raw_value"],
                "sourceRowCount": line["source_row_count"],
                "sortOrder": line["sort_order"],
                "isTotal": bool(line["is_total"]),
            })
        months.append({
            "month": month["month"],
            "sheetName": month["sheet_name"],
            "businessName": month["business_name"],
            "shopCount": month["shop_count"],
            "subjectCount": month["subject_count"],
            "lines": lines,
        })
    return months


def _owned_batch_metadata(
    source: sqlite3.Connection,
) -> dict[str, dict[str, object]]:
    """Return exact fingerprints for batches that still own finance months.

    Batches created before the shared content-fingerprint tables existed are
    not rewritten in D1.  Their canonical normalized content is reconstructed
    from the currently owned month/line facts and projected with an explicit
    migration-only audit marker.
    """

    existing = {
        str(row["batch_id"]): dict(row)
        for row in source.execute(
            "SELECT batch_id, scope_key, raw_file_hash, content_hash, row_count, "
            "status, created_at FROM import_content_fingerprints WHERE domain='finance'"
        )
    }
    existing_attempt_batches = {
        str(row[0])
        for row in source.execute(
            "SELECT DISTINCT batch_id FROM import_content_attempts "
            "WHERE domain='finance' AND batch_id<>''"
        )
    }
    metadata: dict[str, dict[str, object]] = {}
    for batch in source.execute(
        "SELECT id, file_hash, file_name, file_size_bytes, row_count, "
        "imported_month_count, months_json, warnings_json, created_at, completed_at "
        "FROM finance_import_batches WHERE id IN ("
        "SELECT DISTINCT batch_id FROM finance_months WHERE status='completed') "
        "ORDER BY id COLLATE BINARY"
    ):
        batch_id = str(batch["id"])
        months = _normalized_batch_months(source, batch_id)
        month_keys = [str(month["month"]) for month in months]
        declared_months = _json_value(batch["months_json"], kind=list)
        scope_key, content_hash, row_count = _fingerprint(months)
        if (
            not months
            or declared_months != month_keys
            or int(batch["imported_month_count"]) != len(months)
            or int(batch["row_count"]) != row_count
        ):
            raise CommandError("D1 财务历史批次的月份或行数所有权不完整。")
        fingerprint = existing.get(batch_id)
        synthesized = fingerprint is None
        reconciled = False
        source_content_hash = ""
        if fingerprint is not None:
            if (
                str(fingerprint["status"]) != "completed"
                or str(fingerprint["scope_key"]) != scope_key
                or int(fingerprint["row_count"]) != row_count
                or not _valid_hex(fingerprint["raw_file_hash"])
                or not _valid_hex(fingerprint["content_hash"])
            ):
                raise CommandError("D1 财务内容指纹与当前权威事实不一致。")
            source_content_hash = str(fingerprint["content_hash"])
            reconciled = source_content_hash != content_hash
            raw_file_hash = str(fingerprint["raw_file_hash"])
            fingerprint_created_at = fingerprint["created_at"]
        else:
            if (
                batch_id in existing_attempt_batches
                or str(batch["file_hash"]) != batch_id
                or not _valid_hex(batch["file_hash"])
            ):
                raise CommandError("D1 财务历史批次缺少可安全重建的内容指纹审计。")
            raw_file_hash = str(batch["file_hash"])
            fingerprint_created_at = batch["completed_at"] or batch["created_at"]
        metadata[batch_id] = {
            "scope_key": scope_key,
            "content_hash": content_hash,
            "raw_file_hash": raw_file_hash,
            "row_count": row_count,
            "created_at": fingerprint_created_at,
            "synthesized": synthesized,
            "reconciled": reconciled,
            "migration_audit_required": synthesized or reconciled,
            "source_content_hash": source_content_hash,
            "months": month_keys,
            "file_name": batch["file_name"],
            "file_size_bytes": batch["file_size_bytes"],
            "warnings": _json_value(batch["warnings_json"], kind=list),
            "batch_created_at": batch["created_at"],
            "batch_completed_at": batch["completed_at"] or batch["created_at"],
        }
    if set(existing) - set(metadata):
        raise CommandError("D1 财务内容指纹指向不再拥有月份的批次。")
    return metadata


def _validate_source(
    source: sqlite3.Connection,
    *,
    allowed_authority_owners: frozenset[str] = frozenset({"d1"}),
) -> dict[str, object]:
    present = {
        str(row[0])
        for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, columns in REQUIRED_SCHEMAS.items():
        if table not in present:
            raise CommandError(f"D1 源缺少必需财务表 {table}。")
        missing = set(columns) - _table_columns(source, table)
        if missing:
            raise CommandError(
                f"D1 源表 {table} 缺少字段: {', '.join(sorted(missing))}。"
            )
    authority_row = source.execute(
        "SELECT id, owner, epoch, cutover_id, updated_at "
        "FROM finance_write_authority WHERE id=1"
    ).fetchone()
    if authority_row is None or str(authority_row["owner"]) not in allowed_authority_owners:
        raise CommandError("D1 财务写 authority 不在本操作允许状态。")
    if int(authority_row["epoch"]) < 1:
        raise CommandError("D1 财务写 authority epoch 无效。")
    if int(source.execute(
        "SELECT COUNT(*) FROM finance_import_batches WHERE status='processing'"
    ).fetchone()[0]):
        raise CommandError("D1 存在处理中财务批次，拒绝迁移。")
    if int(source.execute(
        "SELECT COUNT(*) FROM finance_months WHERE status<>'completed'"
    ).fetchone()[0]):
        raise CommandError("D1 存在非完成财务月份，拒绝迁移。")
    if int(source.execute(
        "SELECT COUNT(*) FROM import_content_attempts "
        "WHERE domain='finance' AND outcome='processing'"
    ).fetchone()[0]):
        raise CommandError("D1 存在处理中财务导入尝试，拒绝迁移。")
    if int(source.execute(
        "SELECT COUNT(*) FROM import_content_fingerprints "
        "WHERE domain='finance' AND status<>'completed'"
    ).fetchone()[0]):
        raise CommandError("D1 存在非完成财务内容指纹，拒绝迁移。")
    heads = list(source.execute(
        "SELECT scope_key, state_token, status, owner_token, current_batch_id, "
        "generation, updated_at FROM import_scope_heads WHERE domain='finance'"
    ))
    if len(heads) != 1:
        raise CommandError("D1 财务导入基域必须且只能有一个 scope head。")
    head = heads[0]
    if (
        str(head["scope_key"]) != finance_scope_key()
        or str(head["status"]) != "ready"
        or str(head["owner_token"] or "")
        or not _valid_hex(head["state_token"])
    ):
        raise CommandError("D1 财务导入 scope head 不是静默终态。")
    line_count = int(source.execute("SELECT COUNT(*) FROM finance_lines").fetchone()[0])
    month_count = int(source.execute("SELECT COUNT(*) FROM finance_months").fetchone()[0])
    if line_count <= 0 or month_count <= 0:
        raise CommandError("D1 财务事实为空且没有受控空集证明，拒绝迁移。")
    if line_count > MAX_PROJECTED_ROWS:
        raise CommandError("D1 财务明细超过受控迁移上限。")
    orphan_lines = int(source.execute(
        "SELECT COUNT(*) FROM finance_lines l LEFT JOIN finance_months m ON m.month=l.month "
        "WHERE m.month IS NULL OR m.status<>'completed'"
    ).fetchone()[0])
    orphan_months = int(source.execute(
        "SELECT COUNT(*) FROM finance_months m LEFT JOIN finance_import_batches b ON b.id=m.batch_id "
        "WHERE b.id IS NULL OR b.status<>'completed'"
    ).fetchone()[0])
    if orphan_lines or orphan_months:
        raise CommandError("D1 财务事实、批次和内容指纹的所有权链不完整。")
    _owned_batch_metadata(source)
    missing_versions = int(source.execute(
        "SELECT COUNT(*) FROM finance_targets_scoped t "
        "LEFT JOIN finance_target_scoped_versions v ON v.target_id=t.id "
        "WHERE v.target_id IS NULL OR v.version<1"
    ).fetchone()[0])
    unmigrated_legacy = int(source.execute(
        "SELECT COUNT(*) FROM finance_targets legacy "
        "LEFT JOIN finance_targets_scoped scoped ON scoped.id=legacy.id "
        "LEFT JOIN finance_target_legacy_migrations migration ON migration.target_id=legacy.id "
        "WHERE scoped.id IS NULL OR migration.target_id IS NULL"
    ).fetchone()[0])
    overlapping_audits = int(source.execute(
        "SELECT COUNT(*) FROM finance_target_deletion_audits legacy "
        "JOIN finance_target_scoped_deletion_audits scoped "
        "ON scoped.audit_id=legacy.audit_id"
    ).fetchone()[0])
    if missing_versions or unmigrated_legacy or overlapping_audits:
        raise CommandError("D1 财务目标平台迁移或审计链不完整。")
    return {column: authority_row[column] for column in AUTHORITY_COLUMNS}


def _query_dicts(
    source: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> Iterator[dict[str, object]]:
    cursor = source.execute(sql, parameters)
    while True:
        rows = cursor.fetchmany(1000)
        if not rows:
            return
        for row in rows:
            yield dict(row)


def _source_records(source: sqlite3.Connection, key: str) -> Iterator[dict[str, object]]:
    if key == "batches":
        head = source.execute(
            "SELECT state_token, current_batch_id FROM import_scope_heads "
            "WHERE domain='finance'"
        ).fetchone()
        fingerprints = _owned_batch_metadata(source)
        actors = {
            str(row["batch_id"]): str(row["actor"] or "")
            for row in source.execute(
                "SELECT batch_id, actor FROM import_content_attempts a "
                "WHERE domain='finance' AND outcome='imported' AND sequence=("
                "SELECT MAX(sequence) FROM import_content_attempts newest "
                "WHERE newest.domain='finance' AND newest.batch_id=a.batch_id "
                "AND newest.outcome='imported')"
            )
        }
        columns = ", ".join(f'"{column}"' for column in BATCH_COLUMNS)
        for row in _query_dicts(
            source, f"SELECT {columns} FROM finance_import_batches ORDER BY id COLLATE BINARY"
        ):
            fingerprint = fingerprints.get(str(row["id"]))
            row["months_json"] = _json_value(row["months_json"], kind=list)
            row["warnings_json"] = _json_value(row["warnings_json"], kind=list)
            row.update(
                {
                    "raw_file_hash": str(fingerprint["raw_file_hash"]) if fingerprint else "",
                    "content_hash": str(fingerprint["content_hash"]) if fingerprint else "",
                    "scope_key": str(fingerprint["scope_key"]) if fingerprint else "",
                    "published_state_token": str(head["state_token"])
                    if head and str(head["current_batch_id"] or "") == str(row["id"])
                    else ZERO_TOKEN,
                    "actor_email": actors.get(str(row["id"]), ""),
                }
            )
            yield row
        return
    if key == "months":
        columns = ", ".join(f'"{column}"' for column in MONTH_COLUMNS)
        yield from _query_dicts(
            source, f"SELECT {columns} FROM finance_months ORDER BY month COLLATE BINARY"
        )
        return
    if key == "lines":
        columns = ", ".join(f'"{column}"' for column in LINE_COLUMNS)
        for row in _query_dicts(
            source,
            f"SELECT {columns} FROM finance_lines ORDER BY id",
        ):
            row["is_total"] = bool(row["is_total"])
            yield row
        return
    if key == "targets":
        columns = ", ".join(f't."{column}"' for column in TARGET_COLUMNS)
        yield from _query_dicts(
            source,
            f"SELECT {columns}, v.version AS version FROM finance_targets_scoped t "
            "JOIN finance_target_scoped_versions v ON v.target_id=t.id "
            "ORDER BY t.id COLLATE BINARY",
        )
        return
    if key == "deletion_audits":
        scoped_columns = ", ".join(f'"{column}"' for column in SCOPED_AUDIT_COLUMNS)
        records = list(_query_dicts(
            source,
            f"SELECT {scoped_columns} FROM finance_target_scoped_deletion_audits "
            "ORDER BY audit_id COLLATE BINARY",
        ))
        legacy_columns = ", ".join(f'"{column}"' for column in LEGACY_AUDIT_COLUMNS)
        for row in _query_dicts(
            source,
            f"SELECT {legacy_columns} FROM finance_target_deletion_audits "
            "ORDER BY audit_id COLLATE BINARY",
        ):
            row["platform"] = ""
            records.append(row)
        for row in sorted(records, key=lambda item: str(item["audit_id"])):
            row["deleted_at"] = _time_value(row["deleted_at"])
            yield row
        return
    if key == "attempts":
        columns = ", ".join(f'"{column}"' for column in ATTEMPT_COLUMNS)
        records: list[dict[str, object]] = []
        for row in _query_dicts(
            source,
            f"SELECT {columns} FROM import_content_attempts WHERE domain='finance' "
            "ORDER BY attempt_id COLLATE BINARY",
        ):
            uuid.UUID(str(row["attempt_id"]))
            row["metadata"] = {
                "scope": _json_value(row.pop("scope_json"), kind=dict),
                "importHash": row.pop("import_hash"),
                "rowCount": row.pop("row_count"),
                "fileName": row.pop("file_name"),
                "fileSizeBytes": row.pop("file_size_bytes"),
                "warnings": _json_value(row.pop("warnings_json"), kind=list),
                "recoveredFromAttemptId": row.pop("recovered_from_attempt_id"),
            }
            row["id"] = row.pop("attempt_id")
            row["actor_email"] = row.pop("actor")
            row["created_at"] = _time_value(row["created_at"])
            row["completed_at"] = _time_value(row.pop("updated_at"))
            records.append(row)
        for batch_id, metadata in _owned_batch_metadata(source).items():
            if not metadata["migration_audit_required"]:
                continue
            migration_reason = (
                "missing_source_fingerprint"
                if metadata["synthesized"]
                else "source_fingerprint_diverged_from_current_facts"
            )
            records.append({
                "id": uuid.uuid5(
                    LEGACY_ATTEMPT_NAMESPACE,
                    f"{LEGACY_SYNTHESIS_VERSION}:{migration_reason}:{batch_id}",
                ),
                "batch_id": batch_id,
                "scope_key": metadata["scope_key"],
                "raw_file_hash": metadata["raw_file_hash"],
                "content_hash": metadata["content_hash"],
                "outcome": "migrated",
                "error_code": "",
                "actor_email": "",
                "metadata": {
                    "scope": {
                        "months": metadata["months"],
                        "source": "monthly-finance-report",
                    },
                    "importHash": batch_id,
                    "rowCount": metadata["row_count"],
                    "fileName": metadata["file_name"],
                    "fileSizeBytes": metadata["file_size_bytes"],
                    "warnings": metadata["warnings"],
                    "recoveredFromAttemptId": "",
                    "migrationSynthesisVersion": LEGACY_SYNTHESIS_VERSION,
                    "migrationReason": migration_reason,
                    "sourceContentHash": metadata["source_content_hash"],
                },
                "created_at": _time_value(metadata["batch_created_at"]),
                "completed_at": _time_value(metadata["batch_completed_at"]),
            })
        for record in sorted(records, key=lambda item: str(item["id"])):
            yield record
        return
    if key == "fingerprints":
        head = source.execute(
            "SELECT state_token, current_batch_id FROM import_scope_heads "
            "WHERE domain='finance'"
        ).fetchone()
        for batch_id, metadata in sorted(_owned_batch_metadata(source).items()):
            yield {
                "batch_id": batch_id,
                "scope_key": metadata["scope_key"],
                "raw_file_hash": metadata["raw_file_hash"],
                "content_hash": metadata["content_hash"],
                "row_count": metadata["row_count"],
                "published_state_token": (
                str(head["state_token"])
                if head and str(head["current_batch_id"] or "") == batch_id
                else ZERO_TOKEN
                ),
                "created_at": _time_value(metadata["created_at"]),
            }
        return
    if key == "scope_head":
        columns = ", ".join(f'"{column}"' for column in HEAD_COLUMNS)
        for row in _query_dicts(
            source,
            f"SELECT {columns} FROM import_scope_heads WHERE domain='finance' "
            "ORDER BY scope_key COLLATE BINARY",
        ):
            row["id"] = 1
            row["owner_token"] = str(row["owner_token"] or "")
            row["current_batch_id"] = str(row["current_batch_id"] or "")
            row["owner_started_at"] = None
            row["heartbeat_at"] = None
            row["updated_at"] = _time_value(row["updated_at"])
            yield row
        return
    raise CommandError("未知财务迁移投影。")


def _target_records(key: str) -> Iterator[dict[str, object]]:
    if key == "batches":
        fields = (*BATCH_COLUMNS, "raw_file_hash", "content_hash", "scope_key", "published_state_token", "actor_email")
        yield from FinanceImportBatch.objects.order_by("id").values(*fields)
        return
    if key == "months":
        yield from FinanceMonth.objects.order_by("month").values(*MONTH_COLUMNS)
        return
    if key == "lines":
        yield from FinanceLine.objects.order_by("id").values(*LINE_COLUMNS)
        return
    if key == "targets":
        yield from FinanceTarget.objects.order_by("id").values(*TARGET_COLUMNS, "version")
        return
    if key == "deletion_audits":
        yield from FinanceTargetDeletionAudit.objects.order_by("audit_id").values(*SCOPED_AUDIT_COLUMNS)
        return
    if key == "attempts":
        yield from FinanceImportAttempt.objects.order_by("id").values(
            "id", "batch_id", "scope_key", "raw_file_hash", "content_hash",
            "outcome", "error_code", "actor_email", "metadata", "created_at", "completed_at",
        )
        return
    if key == "fingerprints":
        yield from FinanceImportFingerprint.objects.order_by("batch_id").values(
            "batch_id", "scope_key", "raw_file_hash", "content_hash", "row_count",
            "published_state_token", "created_at",
        )
        return
    if key == "scope_head":
        yield from FinanceImportScopeHead.objects.order_by("scope_key").values(
            "id", "scope_key", "state_token", "status", "owner_token",
            "current_batch_id", "generation", "owner_started_at", "heartbeat_at", "updated_at",
        )
        return
    raise CommandError("未知财务目标投影。")


def _digest_records(key: str, records: Iterable[dict[str, object]]) -> tuple[int, str]:
    digest = hashlib.sha256()
    digest.update(_canonical_bytes((FORMAT_VERSION, key)))
    count = 0
    for record in records:
        digest.update(_canonical_bytes(record))
        count += 1
    return count, digest.hexdigest()


def _snapshot(
    source: sqlite3.Connection,
    *,
    allowed_authority_owners: frozenset[str] = frozenset({"d1"}),
) -> Snapshot:
    authority = _validate_source(
        source, allowed_authority_owners=allowed_authority_owners
    )
    counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    for key in PROJECTION_KEYS:
        counts[key], digests[key] = _digest_records(key, _source_records(source, key))
    target_digest = hashlib.sha256(
        _canonical_bytes((FORMAT_VERSION, counts, digests))
    ).hexdigest()
    source_digest = hashlib.sha256(
        _canonical_bytes((FORMAT_VERSION, counts, digests, authority))
    ).hexdigest()
    return Snapshot(counts, digests, authority, source_digest, target_digest)


def _target_snapshot() -> tuple[dict[str, int], dict[str, str], str]:
    counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    for key in PROJECTION_KEYS:
        counts[key], digests[key] = _digest_records(key, _target_records(key))
    digest = hashlib.sha256(
        _canonical_bytes((FORMAT_VERSION, counts, digests))
    ).hexdigest()
    return counts, digests, digest


def _chunks(records: Iterable[dict[str, object]], size: int) -> Iterator[list[dict[str, object]]]:
    chunk: list[dict[str, object]] = []
    for record in records:
        chunk.append(record)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _bulk(model, records: Iterable[dict[str, object]], generation: str, size: int) -> None:
    for chunk in _chunks(records, size):
        model.objects.bulk_create(
            [model(**record, migration_generation=generation) for record in chunk],
            batch_size=size,
        )


def _assert_apply_maintenance_state() -> None:
    """Lock the historical migration authority for both reuse and replacement."""
    if FinanceWriteRequestReceipt.objects.exists():
        raise CommandError("PostgreSQL 已存在财务写请求回执，拒绝覆盖迁移快照。")
    authority = FinanceWriteAuthority.objects.select_for_update().filter(id=1).first()
    if authority is None or authority.status != "d1":
        raise CommandError("PostgreSQL 财务 authority 必须保持 d1，拒绝迁移。")
    if (
        FinanceImportAttempt.objects.filter(outcome="processing").exists()
        or FinanceImportScopeHead.objects.exclude(status="ready").exists()
        or FinanceImportScopeHead.objects.exclude(owner_token="").exists()
    ):
        raise CommandError("PostgreSQL 财务导入控制状态不是静默终态。")


def _apply_snapshot(source: sqlite3.Connection, generation: str, batch_size: int) -> None:
    _assert_apply_maintenance_state()

    FinanceImportAttempt.objects.all().delete()
    FinanceImportFingerprint.objects.all().delete()
    FinanceImportScopeHead.objects.all().delete()
    FinanceTargetDeletionAudit.objects.all().delete()
    FinanceLine.objects.all().delete()
    FinanceMonth.objects.all().delete()
    FinanceTarget.objects.all().delete()
    FinanceImportBatch.objects.all().delete()

    _bulk(FinanceImportBatch, _source_records(source, "batches"), generation, batch_size)
    _bulk(FinanceMonth, _source_records(source, "months"), generation, batch_size)
    _bulk(FinanceLine, _source_records(source, "lines"), generation, batch_size)
    _bulk(FinanceTarget, _source_records(source, "targets"), generation, batch_size)

    audit_times: dict[uuid.UUID, datetime] = {}
    audit_records: list[dict[str, object]] = []
    for record in _source_records(source, "deletion_audits"):
        identifier = uuid.UUID(str(record["audit_id"]))
        audit_times[identifier] = _time_value(record["deleted_at"])
        record["audit_id"] = identifier
        record["deleted_at"] = audit_times[identifier]
        audit_records.append(record)
    FinanceTargetDeletionAudit.objects.bulk_create(
        [FinanceTargetDeletionAudit(**record) for record in audit_records],
        batch_size=batch_size,
    )
    for identifier, deleted_at in audit_times.items():
        FinanceTargetDeletionAudit.objects.filter(audit_id=identifier).update(deleted_at=deleted_at)

    attempt_times: dict[uuid.UUID, tuple[datetime, datetime]] = {}
    attempt_records: list[dict[str, object]] = []
    for record in _source_records(source, "attempts"):
        identifier = uuid.UUID(str(record["id"]))
        created_at = _time_value(record["created_at"])
        completed_at = _time_value(record["completed_at"])
        attempt_times[identifier] = (created_at, completed_at)
        record.update({"id": identifier, "created_at": created_at, "completed_at": completed_at})
        attempt_records.append(record)
    FinanceImportAttempt.objects.bulk_create(
        [FinanceImportAttempt(**record) for record in attempt_records],
        batch_size=batch_size,
    )
    for identifier, (created_at, completed_at) in attempt_times.items():
        FinanceImportAttempt.objects.filter(id=identifier).update(
            created_at=created_at, completed_at=completed_at
        )

    fingerprint_times: dict[str, datetime] = {}
    fingerprint_records: list[dict[str, object]] = []
    for record in _source_records(source, "fingerprints"):
        created_at = _time_value(record["created_at"])
        fingerprint_times[str(record["batch_id"])] = created_at
        record["created_at"] = created_at
        fingerprint_records.append(record)
    FinanceImportFingerprint.objects.bulk_create(
        [FinanceImportFingerprint(**record) for record in fingerprint_records],
        batch_size=batch_size,
    )
    for batch_id, created_at in fingerprint_times.items():
        FinanceImportFingerprint.objects.filter(batch_id=batch_id).update(created_at=created_at)

    head_records = list(_source_records(source, "scope_head"))
    for record in head_records:
        record["updated_at"] = _time_value(record["updated_at"])
    FinanceImportScopeHead.objects.bulk_create(
        [FinanceImportScopeHead(**record) for record in head_records]
    )
    for record in head_records:
        FinanceImportScopeHead.objects.filter(id=record["id"]).update(
            updated_at=record["updated_at"]
        )

    revision, _ = FinanceDataRevision.objects.select_for_update().get_or_create(
        domain="finance", defaults={"revision": 0, "source_digest": ZERO_TOKEN},
    )
    if not 0 <= revision.revision < JS_SAFE_INTEGER or not _valid_hex(revision.source_digest):
        raise CommandError("财报目标修订无法安全单调推进。")
    revision.revision += 1
    revision.source_digest = generation
    revision.save(update_fields=["revision", "source_digest", "updated_at"])
    if target_connection.vendor == "postgresql":
        statements = target_connection.ops.sequence_reset_sql(
            no_style(), [FinanceLine, FinanceImportFingerprint]
        )
        with target_connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)


def _path_digest(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _source_provenance(
    source_path: Path, source_manifest_path: Path | None
) -> dict[str, str]:
    artifact_digest = _file_digest(source_path)
    if source_manifest_path is None:
        if settings.DJANGO_ENVIRONMENT == "production":
            raise CommandError("生产财务迁移必须提供受控源清单。")
        return {
            "formatVersion": "finance-direct-source-v1",
            "liveSourcePathDigest": _path_digest(source_path),
            "sourceArtifactSha256": artifact_digest,
            "sourceManifestSha256": "",
            "sourceFinanceDigest": "",
        }

    manifest_path = source_manifest_path.expanduser().resolve()
    if (
        not manifest_path.is_file()
        or manifest_path.name != "finance-source-manifest.json"
        or manifest_path.parent != source_path.parent
    ):
        raise CommandError("财务迁移受控源清单路径无效。")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CommandError("财务迁移受控源清单无法读取。") from error
    if not isinstance(payload, dict):
        raise CommandError("财务迁移受控源清单结构无效。")
    live_path_digest = payload.get("sourcePathSha256")
    source_finance_digest = payload.get("sourceFinanceDigest")
    if (
        payload.get("formatVersion") != SOURCE_MANIFEST_FORMAT_VERSION
        or payload.get("outputSha256") != artifact_digest
        or not _valid_hex(live_path_digest)
        or not _valid_hex(source_finance_digest)
        or not isinstance(payload.get("counts"), dict)
        or not isinstance(payload.get("authority"), dict)
    ):
        raise CommandError("财务迁移受控源清单与快照不一致。")
    return {
        "formatVersion": SOURCE_MANIFEST_FORMAT_VERSION,
        "liveSourcePathDigest": str(live_path_digest),
        "sourceArtifactSha256": artifact_digest,
        "sourceManifestSha256": _file_digest(manifest_path),
        "sourceFinanceDigest": str(source_finance_digest),
    }


def _recheck_live_source(
    source_path: Path,
    source_manifest_path: Path | None,
    snapshot: Snapshot,
    provenance: dict[str, str],
) -> None:
    """Reject a late source-file change even when projected target data match."""
    live = _open_source(source_path)
    try:
        current = _snapshot(live)
    finally:
        live.rollback()
        live.close()
    if (current.source_digest != snapshot.source_digest
            or source_manifest_path is None and
                _path_digest(source_path) != provenance["liveSourcePathDigest"]
            or _file_digest(source_path) != provenance["sourceArtifactSha256"]
            or source_manifest_path is not None and
                _file_digest(source_manifest_path.expanduser().resolve()) !=
                    provenance["sourceManifestSha256"]):
        raise CommandError("D1 财务迁移材料在目标事务提交前发生变化。")


def _manifest(
    snapshot: Snapshot, source_provenance: dict[str, str]
) -> dict[str, object]:
    return {
        "formatVersion": FORMAT_VERSION,
        "legacySynthesisVersion": LEGACY_SYNTHESIS_VERSION,
        "projectionDigests": snapshot.digests,
        "sourceProvenance": source_provenance,
        "sourceAuthority": {
            "owner": snapshot.authority["owner"],
            "epoch": int(snapshot.authority["epoch"]),
            "cutoverId": snapshot.authority["cutover_id"],
        },
    }


def _record_run(
    *,
    mode: str,
    source_path_digest: str,
    source: Snapshot,
    target_counts: dict[str, int],
    target_digest: str,
    source_provenance: dict[str, str],
    approved_run_id: str = "",
) -> FinanceMigrationRun:
    return FinanceMigrationRun.objects.create(
        id=uuid.uuid4().hex,
        mode=mode,
        status="succeeded",
        source_path_digest=source_path_digest,
        source_snapshot_digest=source.source_digest,
        target_snapshot_digest=target_digest,
        source_counts=source.counts,
        target_counts=target_counts,
        approved_run_id=approved_run_id,
        manifest=_manifest(source, source_provenance),
        completed_at=timezone.now(),
    )


class Command(BaseCommand):
    help = "Dry-run, apply, or verify an exact finance snapshot from a read-only D1 file."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        parser.add_argument("--source-manifest")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--apply", action="store_true")
        mode.add_argument("--verify-only", action="store_true")
        parser.add_argument("--approved-run-id")
        parser.add_argument("--batch-size", type=int, default=1000)

    def handle(self, *args: Any, **options: Any) -> None:
        if (
            settings.DJANGO_ENVIRONMENT == "production"
            and settings.DJANGO_PROCESS_ROLE != "migration_writer"
        ):
            raise CommandError("生产财务迁移只能由 migration_writer 进程角色执行。")
        source_path = Path(str(options["source"])).expanduser().resolve()
        if not source_path.is_file():
            raise CommandError("D1 只读源文件不存在。")
        if _target_source_alias(source_path):
            raise CommandError("D1 只读源不能与 Django SQLite 目标使用同一文件。")
        batch_size = int(options["batch_size"])
        if batch_size < 1 or batch_size > 5000:
            raise CommandError("batch-size 必须在 1 到 5000 之间。")
        apply = bool(options["apply"])
        verify_only = bool(options["verify_only"])
        approved_run_id = str(options.get("approved_run_id") or "").strip()
        if (apply or verify_only) and not approved_run_id:
            raise CommandError("apply/verify-only 必须显式提供 approved-run-id。")
        if not (apply or verify_only) and approved_run_id:
            raise CommandError("dry-run 不接受 approved-run-id。")
        mode = "apply" if apply else "verify" if verify_only else "dry-run"
        path_digest = _path_digest(source_path)
        source_manifest_value = str(options.get("source_manifest") or "").strip()
        source_manifest_path = Path(source_manifest_value) if source_manifest_value else None

        source = _open_source(source_path)
        try:
            snapshot = _snapshot(source)
            source_provenance = _source_provenance(
                source_path, source_manifest_path
            )
            if mode == "dry-run":
                run = _record_run(
                    mode=mode,
                    source_path_digest=path_digest,
                    source=snapshot,
                    target_counts={},
                    target_digest="",
                    source_provenance=source_provenance,
                )
                self.stdout.write(json.dumps({
                    "status": "succeeded", "mode": mode, "runId": run.id,
                    "sourceSnapshotDigest": snapshot.source_digest,
                    "targetProjectionDigest": snapshot.target_digest,
                    "counts": snapshot.counts,
                }, ensure_ascii=False, separators=(",", ":")))
                return

            approved = FinanceMigrationRun.objects.filter(
                id=approved_run_id, status="succeeded"
            ).first()
            expected_mode = "dry-run" if apply else "apply"
            if approved is None or approved.mode != expected_mode:
                raise CommandError(f"approved-run-id 必须引用成功的 {expected_mode}。")
            if (
                approved.source_path_digest != path_digest
                or approved.source_snapshot_digest != snapshot.source_digest
                or approved.source_counts != snapshot.counts
                or approved.manifest != _manifest(snapshot, source_provenance)
            ):
                raise CommandError("D1 财务迁移材料与已审批运行不一致。")

            if verify_only:
                target_counts, target_digests, target_digest = _target_snapshot()
                if (
                    target_counts != snapshot.counts
                    or target_digests != snapshot.digests
                    or target_digest != snapshot.target_digest
                ):
                    raise CommandError("PostgreSQL 财务快照与 D1 源不一致。")
                run = _record_run(
                    mode=mode,
                    source_path_digest=path_digest,
                    source=snapshot,
                    target_counts=target_counts,
                    target_digest=target_digest,
                    source_provenance=source_provenance,
                    approved_run_id=approved_run_id,
                )
            else:
                generation = snapshot.target_digest
                with transaction.atomic():
                    _assert_apply_maintenance_state()
                    revision, _ = FinanceDataRevision.objects.select_for_update().get_or_create(
                        domain="finance", defaults={"revision": 0, "source_digest": ZERO_TOKEN},
                    )
                    if not 0 <= revision.revision <= JS_SAFE_INTEGER or not _valid_hex(revision.source_digest):
                        raise CommandError("财报目标修订不满足迁移维护门禁。")
                    prior_counts, prior_digests, prior_digest = _target_snapshot()
                    identical = (prior_counts == snapshot.counts and
                        prior_digests == snapshot.digests and
                        prior_digest == snapshot.target_digest)
                    if identical:
                        if revision.revision < 1 or revision.source_digest != generation:
                            raise CommandError("财报目标内容已相同但修订或摘要不一致，拒绝假定幂等。")
                    else:
                        _apply_snapshot(source, generation, batch_size)
                    target_counts, target_digests, target_digest = _target_snapshot()
                    if (target_counts != snapshot.counts or target_digests != snapshot.digests
                            or target_digest != snapshot.target_digest):
                        raise CommandError("财务快照写入后的行数或摘要回查不一致。")
                    _recheck_live_source(source_path, source_manifest_path,
                        snapshot, source_provenance)
                    _assert_apply_maintenance_state()
                    if _target_snapshot() != (snapshot.counts, snapshot.digests,
                            snapshot.target_digest):
                        raise CommandError("财报目标在提交前发生变化。")
                    run = _record_run(
                        mode=mode,
                        source_path_digest=path_digest,
                        source=snapshot,
                        target_counts=target_counts,
                        target_digest=target_digest,
                        source_provenance=source_provenance,
                        approved_run_id=approved_run_id,
                    )
            self.stdout.write(json.dumps({
                "status": "succeeded", "mode": mode, "runId": run.id,
                "approvedRunId": approved_run_id,
                "sourceSnapshotDigest": snapshot.source_digest,
                "targetProjectionDigest": snapshot.target_digest,
                "counts": snapshot.counts,
            }, ensure_ascii=False, separators=(",", ":")))
        except (sqlite3.DatabaseError, ValueError) as error:
            raise CommandError("D1 财务迁移材料校验失败。") from error
        finally:
            source.rollback()
            source.close()
