from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import timezone as datetime_timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError as DjangoCommandError
from django.db import connection as target_connection, models, transaction
from django.db.models.functions import Collate
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from erp_reference.locking import lock_erp_reference_for_replace
from erp_reference.models import ErpReferenceSyncCheckpoint
from sales.authority_lock import acquire_sales_write_authority_exclusive_lock
from sales.models import (
    ErpProductMaster,
    SalesDataRevision,
    SalesImportBatch,
    SalesImportAttempt,
    SalesImportFingerprint,
    SalesLegacyUploadAudit,
    SalesImportScopeHead,
    SalesMigrationLock,
    SalesMigrationRun,
    SalesOrderLine,
    SalesRawUploadSession,
    SalesStagedImportSession,
    SalesWriteAuthority,
    sales_projection_values,
)


class CommandError(DjangoCommandError):
    """A command rejection whose message is authored in this module.

    Keeping a distinct type lets the outer audit boundary preserve useful,
    controlled gate messages while redacting arbitrary database/parser errors
    that can contain full business rows.
    """


@dataclass(frozen=True)
class TableSpec:
    source_table: str
    model: type[models.Model]
    columns: tuple[str, ...]
    order_by: str
    unique_fields: tuple[str, ...]
    source_only_columns: tuple[str, ...] = ()

    @property
    def payload_columns(self) -> tuple[str, ...]:
        return tuple(column for column in self.columns if column not in self.source_only_columns)

    @property
    def update_fields(self) -> list[str]:
        primary_key = self.model._meta.pk.name
        immutable_conflict_fields = {primary_key, *self.unique_fields}
        return [column for column in self.payload_columns if column not in immutable_conflict_fields] + ["migration_generation"]


BATCH_COLUMNS = (
    "id", "source", "file_name", "file_size_bytes", "file_hash", "sheet_name", "status",
    "row_count", "inserted_count", "duplicate_count", "warning_count", "warnings_json",
    "totals_json", "created_at", "completed_at",
)
LINE_COLUMNS = (
    "id", "source_line_key", "source_row_hash", "first_import_batch_id", "last_import_batch_id",
    "source_row_number", "order_no", "online_order_no", "channel", "platform", "shop_name",
    "logistics_company", "warehouse", "product_code", "online_spec_code", "product_name",
    "specification", "barcode", "supplier", "category", "quantity", "list_unit_price_cents",
    "cost_amount_cents", "allocated_unit_price_cents", "allocated_amount_cents",
    "fee_allocation_cents", "gross_profit_cents", "gross_margin_bps",
    "untaxed_gross_profit_cents", "untaxed_gross_margin_bps", "order_time", "sales_time",
    "ship_time", "line_ship_time", "business_type", "created_at", "updated_at",
)
PRODUCT_COLUMNS = (
    "product_code", "product_name", "brand", "specification", "barcode", "category", "supplier",
    "product_status", "source_row_number", "last_import_batch_id", "created_at", "updated_at",
)

SPECS = (
    TableSpec("sales_import_batches", SalesImportBatch, BATCH_COLUMNS, "id", ("id",)),
    TableSpec("erp_product_master", ErpProductMaster, PRODUCT_COLUMNS, "product_code", ("product_code",)),
    TableSpec(
        "sales_order_lines",
        SalesOrderLine,
        LINE_COLUMNS,
        "source_line_key",
        ("source_line_key",),
        source_only_columns=("id",),
    ),
)

# Version the complete digest contract, not only the row JSON. Version 2
# excluded the allocation-local D1 sales-line id, version 3 added the import
# control snapshot, and version 4 canonicalizes JSON object keys while binding
# the privacy-safe legacy upload provenance snapshot.
CANONICAL_FORMAT_VERSION = "sales-projection-v4"
LEGACY_DOMAIN_DIGEST_FORMAT_VERSIONS = frozenset(
    {"sales-projection-v2", "sales-projection-v3"}
)
QUERY_PROJECTION_FORMAT_VERSION = "sales-query-projection-v1"
QUERY_PROJECTION_DIGEST_KEY = "sales_query_projection"
SALES_PROJECTION_FIELDS = (
    "business_date",
    "platform_key",
    "channel_key",
    "shop_key",
    "resolved_category",
    "order_identity",
    "is_business_row",
    "is_net_sales_row",
    "is_net_quantity_row",
)

CONTROL_TABLE_COLUMNS = {
    "import_content_fingerprints": (
        "domain", "batch_id", "scope_key", "scope_json", "import_hash",
        "raw_file_hash", "content_hash", "row_count", "status",
        "publication_sequence", "created_at",
    ),
    "import_content_attempts": (
        "attempt_id", "domain", "batch_id", "scope_key", "scope_json",
        "import_hash", "raw_file_hash", "content_hash", "row_count",
        "file_name", "file_size_bytes", "actor", "warnings_json", "outcome",
        "error_code", "recovered_from_attempt_id", "created_at", "updated_at",
    ),
    "import_scope_heads": (
        "domain", "scope_key", "state_token", "status", "owner_token",
        "current_batch_id", "generation", "updated_at",
    ),
}
CONTROL_ORDER = {
    "import_content_fingerprints": "batch_id",
    "import_content_attempts": "attempt_id",
    "import_scope_heads": "scope_key",
}
CONTROL_JSON_COLUMNS = {"scope_json", "warnings_json"}
CONTROL_TIME_COLUMNS = {"created_at", "updated_at"}

LEGACY_UPLOAD_COLUMNS = (
    "id", "fingerprint", "file_name", "file_size_bytes", "chunk_size_bytes",
    "chunk_count", "received_chunk_count", "received_bytes", "status",
    "created_at", "updated_at", "expires_at",
)
LEGACY_UPLOAD_CHUNK_COLUMNS = (
    "upload_id", "chunk_index", "object_key", "size_bytes", "sha256", "created_at",
)
LEGACY_UPLOAD_COUNT_KEY = "sales_import_uploads"
LEGACY_UPLOAD_CHUNK_COUNT_KEY = "sales_import_upload_chunks"
LEGACY_MANIFEST_FORMAT_VERSION = "legacy-sales-upload-manifest-v1"
LEGACY_UPLOAD_AUDIT_COLUMNS = (
    "source_upload_id", "source_fingerprint_sha256", "file_name_sha256",
    "file_size_bytes", "chunk_size_bytes", "declared_chunk_count",
    "declared_received_chunk_count", "declared_received_bytes", "source_status",
    "archive_reason", "source_created_at", "source_updated_at", "source_expires_at",
    "manifest_chunk_count", "manifest_bytes", "manifest_sha256",
)


def _canonical_bytes(values: Sequence[Any]) -> bytes:
    return (
        json.dumps(
            list(values),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _new_table_digest(spec: TableSpec) -> Any:
    digest = hashlib.sha256()
    digest.update(
        _canonical_bytes(
            (
                CANONICAL_FORMAT_VERSION,
                "table",
                spec.source_table,
                *spec.payload_columns,
            )
        )
    )
    return digest


def _source_uri(path: Path) -> str:
    # sqlite URI accepts a forward-slash Windows absolute path (file:D:/...).
    return f"file:{path.as_posix()}?mode=ro"


def _paths_alias(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    if left == right:
        return True
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False


def _reject_source_target_alias(source: Path) -> None:
    if target_connection.vendor != "sqlite":
        return
    target_name = str(target_connection.settings_dict.get("NAME") or "")
    if not target_name or target_name == ":memory:" or target_name.startswith("file:"):
        return
    if _paths_alias(source, Path(target_name).expanduser()):
        raise CommandError("D1 只读源不能与 Django SQLite 目标使用同一文件")


def _open_source(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(_source_uri(path), uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("BEGIN")
    return connection


def _read_live_source_revision(path: Path) -> tuple[int, int]:
    connection = sqlite3.connect(_source_uri(path), uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            "SELECT sales_revision, erp_product_revision FROM sales_overview_cache_state WHERE id = 1"
        ).fetchone()
        if row is None:
            raise CommandError("D1 源版本水位在迁移期间消失")
        return int(row[0]), int(row[1])
    finally:
        connection.close()


def _ensure_source_stable(path: Path, expected: tuple[int, int]) -> None:
    if _read_live_source_revision(path) != expected:
        raise CommandError("D1 源版本水位在迁移期间变化，目标事务已拒绝提交")


def _validate_source(connection: sqlite3.Connection) -> tuple[int, int]:
    for spec in SPECS:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1", (spec.source_table,)
        ).fetchone()
        if not exists:
            raise CommandError(f"D1 源缺少必需表 {spec.source_table}")
        actual = {row[1] for row in connection.execute(f"PRAGMA table_info({spec.source_table})")}
        missing = set(spec.columns) - actual
        if missing:
            raise CommandError(f"D1 源表 {spec.source_table} 缺少字段: {', '.join(sorted(missing))}")
    for table_name, columns in CONTROL_TABLE_COLUMNS.items():
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
        if not exists:
            raise CommandError(f"D1 源缺少必需的销售导入控制表 {table_name}")
        actual = {
            row[1]
            for row in connection.execute(f'PRAGMA table_info("{table_name}")')
        }
        missing = set(columns) - actual
        if missing:
            raise CommandError(
                f"D1 源表 {table_name} 缺少字段: {', '.join(sorted(missing))}"
            )
    _validate_legacy_upload_source_schema(connection)
    processing_batches = int(
        connection.execute(
            "SELECT COUNT(*) FROM sales_import_batches WHERE status = 'processing'"
        ).fetchone()[0]
    )
    processing_attempts = int(
        connection.execute(
            "SELECT COUNT(*) FROM import_content_attempts "
            "WHERE domain = 'sales' AND outcome = 'processing'"
        ).fetchone()[0]
    )
    processing_heads = int(
        connection.execute(
            "SELECT COUNT(*) FROM import_scope_heads WHERE domain = 'sales' "
            "AND (status <> 'ready' OR COALESCE(owner_token, '') <> '')"
        ).fetchone()[0]
    )
    nonterminal_fingerprints = int(
        connection.execute(
            "SELECT COUNT(*) FROM import_content_fingerprints "
            "WHERE domain = 'sales' AND status <> 'completed'"
        ).fetchone()[0]
    )
    if any(
        (processing_batches, processing_attempts, processing_heads, nonterminal_fingerprints)
    ):
        raise CommandError(
            "D1 销售导入控制状态不是静默终态，拒绝迁移或推断所有权"
        )
    sales_count = int(connection.execute("SELECT COUNT(*) FROM sales_order_lines").fetchone()[0])
    if sales_count <= 0:
        raise CommandError("D1 销售事实为空，拒绝覆盖目标快照")
    state_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sales_overview_cache_state' LIMIT 1"
    ).fetchone()
    if not state_table:
        raise CommandError("D1 源缺少 sales_overview_cache_state，无法绑定真实数据版本")
    revision = connection.execute(
        "SELECT sales_revision, erp_product_revision FROM sales_overview_cache_state WHERE id = 1"
    ).fetchone()
    if revision is None or int(revision[0]) < 1 or int(revision[1]) < 1:
        raise CommandError("D1 源缺少有效的销售/ERP版本水位")
    erp_count = int(
        connection.execute("SELECT COUNT(*) FROM erp_product_master").fetchone()[0]
    )
    if int(revision[1]) > 0 and erp_count == 0:
        raise CommandError(
            "D1 ERP 版本为正但商品主数据为空，缺少受控空集证明，拒绝零业务写入"
        )
    return int(revision[0]), int(revision[1])


def _source_erp_bridge_state(
    connection: sqlite3.Connection,
) -> dict[str, object] | None:
    tables = {
        "erp_reference_projection_source_state",
        "erp_product_projection_state",
        "erp_reference_projection_outbox",
    }
    present = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            f"AND name IN ({', '.join('?' for _ in tables)})",
            tuple(sorted(tables)),
        )
    }
    if not present:
        return None
    if present != tables:
        raise CommandError("D1 ERP projection bridge schema 不完整")
    source = connection.execute(
        "SELECT source_epoch FROM erp_reference_projection_source_state WHERE id = 1"
    ).fetchone()
    projection = connection.execute(
        "SELECT erp_revision, source_batch_id, row_count, content_hash "
        "FROM erp_product_projection_state WHERE id = 1"
    ).fetchone()
    head = connection.execute(
        "SELECT event_sequence, event_id FROM erp_reference_projection_outbox "
        "ORDER BY event_sequence DESC LIMIT 1"
    ).fetchone()
    if source is None or projection is None:
        raise CommandError("D1 ERP projection bridge 缺少单例状态")
    return {
        "source_epoch": str(source[0] or ""),
        "erp_revision": int(projection[0]),
        "source_batch_id": str(projection[1] or ""),
        "row_count": int(projection[2]),
        "content_hash": str(projection[3] or ""),
        "head_sequence": int(head[0]) if head is not None else 0,
        "head_event_id": str(head[1] or "") if head is not None else "",
    }


def _normalized_control_time(value: object) -> str:
    parsed = parse_datetime(str(value or ""))
    if parsed is None:
        raise CommandError("D1 销售导入控制表包含无效时间")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, datetime_timezone.utc)
    return parsed.astimezone(datetime_timezone.utc).isoformat()


def _normalized_control_value(column: str, value: object) -> object:
    if column in CONTROL_JSON_COLUMNS:
        try:
            fallback = "{}" if column == "scope_json" else "[]"
            parsed = json.loads(str(value or fallback))
        except json.JSONDecodeError as error:
            raise CommandError(f"D1 销售导入控制表字段 {column} 不是有效 JSON") from error
        if column == "scope_json" and not isinstance(parsed, dict):
            raise CommandError("D1 销售导入 scope_json 必须是对象")
        if column == "warnings_json" and not isinstance(parsed, list):
            raise CommandError("D1 销售导入 warnings_json 必须是数组")
        return parsed
    if column in CONTROL_TIME_COLUMNS:
        return _normalized_control_time(value)
    return value


def _source_control_records(
    connection: sqlite3.Connection, table_name: str
) -> list[dict[str, object]]:
    columns = CONTROL_TABLE_COLUMNS[table_name]
    selected = ", ".join(f'"{column}"' for column in columns)
    rows = connection.execute(
        f'SELECT {selected} FROM "{table_name}" WHERE domain = ? '
        f'ORDER BY "{CONTROL_ORDER[table_name]}" COLLATE BINARY ASC',
        ("sales",),
    )
    records = [
        {
            column: _normalized_control_value(column, value)
            for column, value in zip(columns, tuple(row), strict=True)
        }
        for row in rows
    ]
    # D1 historically allowed nullable owner/control text while the PostgreSQL
    # models use explicit empty strings.  Canonicalize to the exact values the
    # apply path materializes, otherwise JSONB/key/null representation details
    # can create a false cross-database digest mismatch.
    if table_name == "import_scope_heads":
        for record in records:
            record["state_token"] = str(record["state_token"] or "initial")
            record["owner_token"] = str(record["owner_token"] or "")
            record["current_batch_id"] = str(record["current_batch_id"] or "")
    elif table_name == "import_content_attempts":
        for record in records:
            for column in (
                "batch_id", "scope_key", "import_hash", "raw_file_hash",
                "content_hash", "file_name", "actor", "error_code",
                "recovered_from_attempt_id",
            ):
                record[column] = str(record[column] or "")
    return records


def _control_digest(table_name: str, records: Sequence[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    columns = CONTROL_TABLE_COLUMNS[table_name]
    digest.update(_canonical_bytes((CANONICAL_FORMAT_VERSION, "control", table_name, *columns)))
    for record in records:
        digest.update(_canonical_bytes(tuple(record[column] for column in columns)))
    return digest.hexdigest()


def _source_control_snapshot(
    connection: sqlite3.Connection,
) -> tuple[dict[str, int], dict[str, str], dict[str, list[dict[str, object]]]]:
    records = {
        table_name: _source_control_records(connection, table_name)
        for table_name in CONTROL_TABLE_COLUMNS
    }
    return (
        {table_name: len(items) for table_name, items in records.items()},
        {
            table_name: _control_digest(table_name, items)
            for table_name, items in records.items()
        },
        records,
    )


def _validate_legacy_upload_source_schema(connection: sqlite3.Connection) -> None:
    for table_name, columns in (
        (LEGACY_UPLOAD_COUNT_KEY, LEGACY_UPLOAD_COLUMNS),
        (LEGACY_UPLOAD_CHUNK_COUNT_KEY, LEGACY_UPLOAD_CHUNK_COLUMNS),
    ):
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
        if not exists:
            raise CommandError(f"D1 源缺少历史销售上传表 {table_name}")
        actual = {
            row[1]
            for row in connection.execute(f'PRAGMA table_info("{table_name}")')
        }
        missing = set(columns) - actual
        if missing:
            raise CommandError(
                f"D1 源表 {table_name} 缺少字段: {', '.join(sorted(missing))}"
            )

    orphan_chunks = int(
        connection.execute(
            "SELECT COUNT(*) FROM sales_import_upload_chunks c "
            "LEFT JOIN sales_import_uploads u ON u.id = c.upload_id "
            "WHERE u.id IS NULL"
        ).fetchone()[0]
    )
    if orphan_chunks:
        raise CommandError("D1 历史销售上传分片存在无属主记录，拒绝迁移")
    duplicate_chunks = connection.execute(
        "SELECT upload_id, chunk_index FROM sales_import_upload_chunks "
        "GROUP BY upload_id, chunk_index HAVING COUNT(*) <> 1 LIMIT 1"
    ).fetchone()
    if duplicate_chunks is not None:
        raise CommandError("D1 历史销售上传分片身份重复，拒绝迁移")


def _legacy_text(value: object, field: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CommandError(f"D1 历史销售上传字段 {field} 无效")
    if max_length is not None and len(value) > max_length:
        raise CommandError(f"D1 历史销售上传字段 {field} 超长")
    return value


def _legacy_integer(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CommandError(f"D1 历史销售上传字段 {field} 不是有效整数")
    return value


def _legacy_datetime(value: object, field: str) -> tuple[str, object]:
    if not isinstance(value, str):
        raise CommandError(f"D1 历史销售上传字段 {field} 不是有效时间")
    parsed = parse_datetime(value)
    if parsed is None:
        raise CommandError(f"D1 历史销售上传字段 {field} 不是有效时间")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, datetime_timezone.utc)
    parsed = parsed.astimezone(datetime_timezone.utc)
    return parsed.isoformat(), parsed


def _legacy_private_digest(kind: str, value: str) -> str:
    return hashlib.sha256(
        f"legacy-sales-upload-{kind}-v1\n{value}".encode("utf-8")
    ).hexdigest()


def _legacy_manifest_digest(upload_id: str, chunks: Sequence[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    digest.update(
        _canonical_bytes((LEGACY_MANIFEST_FORMAT_VERSION, upload_id))
    )
    for chunk in chunks:
        digest.update(
            _canonical_bytes(
                (
                    chunk["chunk_index"],
                    chunk["object_key_sha256"],
                    chunk["size_bytes"],
                    chunk["sha256"],
                    chunk["created_at"],
                )
            )
        )
    return digest.hexdigest()


def _source_legacy_upload_records(
    connection: sqlite3.Connection,
) -> list[dict[str, object]]:
    _validate_legacy_upload_source_schema(connection)
    cutoff = timezone.now().astimezone(datetime_timezone.utc)
    upload_columns = ", ".join(f'"{column}"' for column in LEGACY_UPLOAD_COLUMNS)
    chunk_columns = ", ".join(f'"{column}"' for column in LEGACY_UPLOAD_CHUNK_COLUMNS)
    upload_rows = connection.execute(
        f"SELECT {upload_columns} FROM sales_import_uploads "
        'ORDER BY id COLLATE BINARY ASC'
    ).fetchall()
    chunk_rows = connection.execute(
        f"SELECT {chunk_columns} FROM sales_import_upload_chunks "
        'ORDER BY upload_id COLLATE BINARY ASC, chunk_index ASC'
    ).fetchall()

    chunks_by_upload: dict[str, list[dict[str, object]]] = {}
    for row in chunk_rows:
        values = dict(zip(LEGACY_UPLOAD_CHUNK_COLUMNS, tuple(row), strict=True))
        upload_id = _legacy_text(values["upload_id"], "chunk.upload_id", max_length=128)
        chunk_index = _legacy_integer(values["chunk_index"], "chunk.chunk_index")
        object_key = _legacy_text(values["object_key"], "chunk.object_key")
        size_bytes = _legacy_integer(values["size_bytes"], "chunk.size_bytes", minimum=1)
        checksum = _legacy_text(values["sha256"], "chunk.sha256", max_length=64)
        if len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum):
            raise CommandError("D1 历史销售上传分片 sha256 无效")
        created_at, _ = _legacy_datetime(values["created_at"], "chunk.created_at")
        chunks_by_upload.setdefault(upload_id, []).append(
            {
                "chunk_index": chunk_index,
                "object_key_sha256": _legacy_private_digest("object-key", object_key),
                "size_bytes": size_bytes,
                "sha256": checksum,
                "created_at": created_at,
            }
        )

    records: list[dict[str, object]] = []
    seen_upload_ids: set[str] = set()
    for row in upload_rows:
        values = dict(zip(LEGACY_UPLOAD_COLUMNS, tuple(row), strict=True))
        upload_id = _legacy_text(values["id"], "id", max_length=128)
        if upload_id in seen_upload_ids:
            raise CommandError("D1 历史销售上传会话身份重复，拒绝迁移")
        seen_upload_ids.add(upload_id)
        fingerprint = _legacy_text(values["fingerprint"], "fingerprint", max_length=255)
        file_name = _legacy_text(values["file_name"], "file_name", max_length=255)
        file_size = _legacy_integer(values["file_size_bytes"], "file_size_bytes", minimum=1)
        chunk_size = _legacy_integer(values["chunk_size_bytes"], "chunk_size_bytes", minimum=1)
        declared_count = _legacy_integer(values["chunk_count"], "chunk_count", minimum=1)
        received_count = _legacy_integer(
            values["received_chunk_count"], "received_chunk_count"
        )
        received_bytes = _legacy_integer(values["received_bytes"], "received_bytes")
        status = _legacy_text(values["status"], "status", max_length=32)
        if status not in {"uploading", "ready", "processing", "completed"}:
            raise CommandError("D1 历史销售上传会话状态非法，拒绝迁移")
        created_at, created_datetime = _legacy_datetime(values["created_at"], "created_at")
        updated_at, updated_datetime = _legacy_datetime(values["updated_at"], "updated_at")
        expires_at, expires_datetime = _legacy_datetime(values["expires_at"], "expires_at")
        if updated_datetime < created_datetime or expires_datetime <= created_datetime:
            raise CommandError("D1 历史销售上传会话时间顺序非法，拒绝迁移")
        archive_reason = "completed" if status == "completed" else "expired"
        if status != "completed" and expires_datetime > cutoff:
            raise CommandError("D1 存在尚未过期的活动销售上传会话，拒绝迁移")
        expected_count = (file_size + chunk_size - 1) // chunk_size
        if declared_count != expected_count:
            raise CommandError("D1 历史销售上传分片声明与文件大小不一致")
        if received_count > declared_count or received_bytes > file_size:
            raise CommandError("D1 历史销售上传接收计数越界，拒绝迁移")

        chunks = chunks_by_upload.pop(upload_id, [])
        indexes = [int(chunk["chunk_index"]) for chunk in chunks]
        if indexes != list(range(len(chunks))):
            raise CommandError("D1 历史销售上传分片索引不连续，拒绝迁移")
        manifest_bytes = sum(int(chunk["size_bytes"]) for chunk in chunks)
        for chunk in chunks:
            chunk_index = int(chunk["chunk_index"])
            expected_size = (
                file_size - chunk_size * (declared_count - 1)
                if chunk_index == declared_count - 1
                else chunk_size
            )
            if int(chunk["size_bytes"]) != expected_size:
                raise CommandError("D1 历史销售上传分片大小与声明不一致")
        manifest_count = len(chunks)
        if status == "completed":
            if received_count != declared_count or received_bytes != file_size:
                raise CommandError("D1 已完成销售上传缺少完整接收计数")
            if manifest_count not in {0, declared_count}:
                raise CommandError("D1 已完成销售上传残留不完整分片清单")
            if manifest_count and manifest_bytes != file_size:
                raise CommandError("D1 已完成销售上传残留分片字节数不一致")
        else:
            if manifest_count != received_count or manifest_bytes != received_bytes:
                raise CommandError("D1 过期销售上传分片清单与接收计数不一致")
            if status in {"ready", "processing"} and (
                received_count != declared_count or received_bytes != file_size
            ):
                raise CommandError("D1 过期销售上传终态缺少完整分片")
            if status == "uploading" and received_count >= declared_count:
                raise CommandError("D1 uploading 销售上传却已具备完整分片")

        records.append(
            {
                "source_upload_id": upload_id,
                "source_fingerprint_sha256": _legacy_private_digest("fingerprint", fingerprint),
                "file_name_sha256": _legacy_private_digest("file-name", file_name),
                "file_size_bytes": file_size,
                "chunk_size_bytes": chunk_size,
                "declared_chunk_count": declared_count,
                "declared_received_chunk_count": received_count,
                "declared_received_bytes": received_bytes,
                "source_status": status,
                "archive_reason": archive_reason,
                "source_created_at": created_at,
                "source_updated_at": updated_at,
                "source_expires_at": expires_at,
                "manifest_chunk_count": manifest_count,
                "manifest_bytes": manifest_bytes,
                "manifest_sha256": _legacy_manifest_digest(upload_id, chunks),
            }
        )
    if chunks_by_upload:
        raise CommandError("D1 历史销售上传分片存在无属主记录，拒绝迁移")
    return records


def _legacy_upload_snapshot_digest(
    records: Sequence[dict[str, object]],
) -> tuple[dict[str, int], dict[str, str]]:
    uploads_digest = hashlib.sha256()
    uploads_digest.update(
        _canonical_bytes(
            (CANONICAL_FORMAT_VERSION, "legacy-upload-audit", *LEGACY_UPLOAD_AUDIT_COLUMNS)
        )
    )
    chunks_digest = hashlib.sha256()
    chunks_digest.update(
        _canonical_bytes(
            (
                CANONICAL_FORMAT_VERSION,
                "legacy-upload-manifests",
                "source_upload_id",
                "manifest_chunk_count",
                "manifest_bytes",
                "manifest_sha256",
            )
        )
    )
    chunk_count = 0
    for record in records:
        uploads_digest.update(
            _canonical_bytes(tuple(record[column] for column in LEGACY_UPLOAD_AUDIT_COLUMNS))
        )
        chunks_digest.update(
            _canonical_bytes(
                (
                    record["source_upload_id"],
                    record["manifest_chunk_count"],
                    record["manifest_bytes"],
                    record["manifest_sha256"],
                )
            )
        )
        chunk_count += int(record["manifest_chunk_count"])
    return (
        {
            LEGACY_UPLOAD_COUNT_KEY: len(records),
            LEGACY_UPLOAD_CHUNK_COUNT_KEY: chunk_count,
        },
        {
            LEGACY_UPLOAD_COUNT_KEY: uploads_digest.hexdigest(),
            LEGACY_UPLOAD_CHUNK_COUNT_KEY: chunks_digest.hexdigest(),
        },
    )


def _source_legacy_upload_snapshot(
    connection: sqlite3.Connection,
) -> tuple[dict[str, int], dict[str, str], list[dict[str, object]]]:
    records = _source_legacy_upload_records(connection)
    counts, digests = _legacy_upload_snapshot_digest(records)
    return counts, digests, records


def _complete_source_snapshot(
    connection: sqlite3.Connection,
    batch_size: int,
) -> tuple[
    tuple[int, int],
    dict[str, int],
    dict[str, str],
    dict[str, list[dict[str, object]]],
    list[dict[str, object]],
]:
    revision = _validate_source(connection)
    counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    for spec in SPECS:
        counts[spec.source_table], digests[spec.source_table] = _source_digest(
            connection, spec, batch_size
        )
    projection_count, projection_digest = _source_projection_digest(
        connection, batch_size
    )
    if projection_count != counts["sales_order_lines"]:
        raise CommandError("D1 销售查询投影校验行数不一致")
    counts[QUERY_PROJECTION_DIGEST_KEY] = projection_count
    digests[QUERY_PROJECTION_DIGEST_KEY] = projection_digest
    control_counts, control_digests, control_records = _source_control_snapshot(
        connection
    )
    counts.update(control_counts)
    digests.update(control_digests)
    legacy_counts, legacy_digests, legacy_records = (
        _source_legacy_upload_snapshot(connection)
    )
    counts.update(legacy_counts)
    digests.update(legacy_digests)
    return revision, counts, digests, control_records, legacy_records


def _ensure_complete_source_stable(
    path: Path,
    *,
    expected_revision: tuple[int, int],
    expected_counts: dict[str, int],
    expected_digests: dict[str, str],
    batch_size: int,
) -> None:
    # Keep the independent fast revision read as a separate fence, then reopen
    # and recompute every approved source material. Sales/ERP facts, derived
    # query projection, control audit, and legacy upload provenance are all
    # covered even when a faulty writer changes content without raising a
    # revision.
    _ensure_source_stable(path, expected_revision)
    live_connection = _open_source(path)
    try:
        live_revision, live_counts, live_digests, _, _ = _complete_source_snapshot(
            live_connection, batch_size
        )
    finally:
        live_connection.rollback()
        live_connection.close()
    mismatches = sorted(
        key
        for key in (
            set(expected_counts)
            | set(live_counts)
            | set(expected_digests)
            | set(live_digests)
        )
        if expected_counts.get(key) != live_counts.get(key)
        or expected_digests.get(key) != live_digests.get(key)
    )
    if live_revision != expected_revision or mismatches:
        suffix = ", ".join(mismatches) if mismatches else "revision"
        raise CommandError(
            "D1 完整迁移材料在迁移期间变化，目标事务已拒绝提交: " + suffix
        )


def _target_legacy_upload_records() -> list[dict[str, object]]:
    rows = SalesLegacyUploadAudit.objects.order_by(
        Collate(
            models.F("source_upload_id"),
            _target_binary_collation(target_connection.vendor),
        )
    )
    return [
        {
            "source_upload_id": row.source_upload_id,
            "source_fingerprint_sha256": row.source_fingerprint_sha256,
            "file_name_sha256": row.file_name_sha256,
            "file_size_bytes": row.file_size_bytes,
            "chunk_size_bytes": row.chunk_size_bytes,
            "declared_chunk_count": row.declared_chunk_count,
            "declared_received_chunk_count": row.declared_received_chunk_count,
            "declared_received_bytes": row.declared_received_bytes,
            "source_status": row.source_status,
            "archive_reason": row.archive_reason,
            "source_created_at": _normalized_control_time(row.source_created_at),
            "source_updated_at": _normalized_control_time(row.source_updated_at),
            "source_expires_at": _normalized_control_time(row.source_expires_at),
            "manifest_chunk_count": row.manifest_chunk_count,
            "manifest_bytes": row.manifest_bytes,
            "manifest_sha256": row.manifest_sha256,
        }
        for row in rows
    ]


def _target_legacy_upload_snapshot() -> tuple[dict[str, int], dict[str, str]]:
    return _legacy_upload_snapshot_digest(_target_legacy_upload_records())


def _apply_legacy_upload_snapshot(
    records: Sequence[dict[str, object]], generation: str
) -> None:
    objects = []
    for record in records:
        payload = dict(record)
        for field in ("source_created_at", "source_updated_at", "source_expires_at"):
            payload[field] = _parsed_control_datetime(payload[field])
        payload["migration_generation"] = generation
        objects.append(SalesLegacyUploadAudit(**payload))
    if objects:
        SalesLegacyUploadAudit.objects.bulk_create(
            objects,
            batch_size=1000,
            update_conflicts=True,
            update_fields=[
                *(
                    field
                    for field in LEGACY_UPLOAD_AUDIT_COLUMNS
                    if field != "source_upload_id"
                ),
                "migration_generation",
            ],
            unique_fields=["source_upload_id"],
        )
    SalesLegacyUploadAudit.objects.exclude(migration_generation=generation).delete()


def _lock_snapshot(run_id: str) -> SalesMigrationLock:
    lock = SalesMigrationLock.objects.select_for_update().get(name="sales_snapshot")
    if lock.owner_id:
        raise CommandError("已有销售快照迁移持有目标写锁")
    lock.owner_id = run_id
    lock.save(update_fields=["owner_id", "updated_at"])
    return lock


def _ensure_target_sales_writer_quiet() -> None:
    authority = SalesWriteAuthority.objects.select_for_update().filter(id=1).first()
    if authority is None or authority.status != "pending":
        raise CommandError("销售写 authority 必须保持 pending，拒绝覆盖已启用的 PostgreSQL 写域")
    if (
        SalesImportAttempt.objects.filter(domain="sales", outcome="processing").exists()
        or SalesImportScopeHead.objects.filter(domain="sales", status="processing").exists()
        or SalesRawUploadSession.objects.exclude(status__in=["completed", "expired"]).exists()
        or SalesStagedImportSession.objects.exclude(
            status__in=["completed", "rejected", "expired"]
        ).exists()
    ):
        raise CommandError("PostgreSQL 销售写入控制状态不是静默终态，拒绝迁移")


def _verify_target_revisions(
    source_revision: tuple[int, int],
    source_counts: dict[str, int],
    source_digests: dict[str, str],
    source_path_digest: str,
    source_erp_state: dict[str, object] | None,
    batch_size: int,
) -> None:
    target = {
        domain: (int(revision), str(source_digest or ""))
        for domain, revision, source_digest in SalesDataRevision.objects.filter(
            domain__in=["sales", "erp"]
        ).values_list("domain", "revision", "source_digest")
    }
    checkpoint_digest = _validated_erp_checkpoint_digest(
        source_path_digest=source_path_digest,
        source_revision=source_revision,
        source_counts=source_counts,
        source_digests=source_digests,
        source_erp_state=source_erp_state,
        batch_size=batch_size,
    )
    expected = {
        "sales": (
            source_revision[0],
            _domain_digest("sales", source_digests),
        ),
        "erp": (
            source_revision[1],
            checkpoint_digest or _domain_digest("erp", source_digests),
        ),
    }
    if target != expected:
        raise CommandError("目标销售/ERP版本水位与D1源不一致")


def _source_rows(connection: sqlite3.Connection, spec: TableSpec, batch_size: int) -> Iterator[list[sqlite3.Row]]:
    columns = ", ".join(f'"{column}"' for column in spec.payload_columns)
    cursor = connection.execute(
        f'SELECT {columns} FROM "{spec.source_table}" '
        f'ORDER BY "{spec.order_by}" COLLATE BINARY ASC'
    )
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            return
        yield rows


def _source_digest(connection: sqlite3.Connection, spec: TableSpec, batch_size: int) -> tuple[int, str]:
    digest = _new_table_digest(spec)
    count = 0
    for rows in _source_rows(connection, spec, batch_size):
        for row in rows:
            digest.update(_canonical_bytes(tuple(row)))
        count += len(rows)
    return count, digest.hexdigest()


def _source_erp_categories(connection: sqlite3.Connection) -> dict[str, object]:
    return {
        str(row[0]): row[1]
        for row in connection.execute("SELECT product_code, category FROM erp_product_master")
    }


def _projection_digest_values(source_line_key: object, projection: dict[str, object]) -> tuple[object, ...]:
    business_date = projection["business_date"]
    return (
        source_line_key,
        business_date.isoformat() if hasattr(business_date, "isoformat") else str(business_date),
        *(projection[field] for field in SALES_PROJECTION_FIELDS[1:]),
    )


def _new_projection_digest():
    digest = hashlib.sha256()
    digest.update(_canonical_bytes((QUERY_PROJECTION_FORMAT_VERSION, *SALES_PROJECTION_FIELDS)))
    return digest


def _source_projection_digest(
    connection: sqlite3.Connection, batch_size: int
) -> tuple[int, str]:
    erp_categories = _source_erp_categories(connection)
    digest = _new_projection_digest()
    count = 0
    line_spec = next(spec for spec in SPECS if spec.model is SalesOrderLine)
    for rows in _source_rows(connection, line_spec, batch_size):
        for row in rows:
            payload = dict(row)
            try:
                projection = sales_projection_values(
                    payload,
                    erp_category=erp_categories.get(str(payload.get("product_code") or ""), ""),
                )
            except ValueError as error:
                raise CommandError(
                    "D1 销售行 ship_time 无法生成业务日期"
                ) from error
            digest.update(_canonical_bytes(_projection_digest_values(payload["source_line_key"], projection)))
            count += 1
    return count, digest.hexdigest()


def _target_binary_collation(vendor: str) -> str:
    if vendor == "sqlite":
        return "BINARY"
    if vendor == "postgresql":
        return "C"
    raise CommandError(f"不支持在 {vendor} 上校验销售快照的二进制排序")


def _target_digest(spec: TableSpec, batch_size: int) -> tuple[int, str]:
    digest = _new_table_digest(spec)
    count = 0
    collation = _target_binary_collation(target_connection.vendor)
    queryset = spec.model.objects.order_by(Collate(models.F(spec.order_by), collation)).values_list(
        *spec.payload_columns
    )
    for values in queryset.iterator(chunk_size=batch_size):
        digest.update(_canonical_bytes(values))
        count += 1
    return count, digest.hexdigest()


def _target_projection_digest(batch_size: int) -> tuple[int, str]:
    digest = _new_projection_digest()
    count = 0
    collation = _target_binary_collation(target_connection.vendor)
    queryset = SalesOrderLine.objects.order_by(
        Collate(models.F("source_line_key"), collation)
    ).values_list("source_line_key", *SALES_PROJECTION_FIELDS)
    for values in queryset.iterator(chunk_size=batch_size):
        business_date = values[1]
        canonical = (
            values[0],
            business_date.isoformat() if hasattr(business_date, "isoformat") else str(business_date),
            *values[2:],
        )
        digest.update(_canonical_bytes(canonical))
        count += 1
    return count, digest.hexdigest()


def _target_control_records(table_name: str) -> list[dict[str, object]]:
    if table_name == "import_content_fingerprints":
        rows = SalesImportFingerprint.objects.filter(domain="sales").order_by(
            Collate(models.F("batch_id"), _target_binary_collation(target_connection.vendor))
        )
        return [
            {
                "domain": row.domain,
                "batch_id": row.batch_id,
                "scope_key": row.scope_key,
                "scope_json": row.scope_json,
                "import_hash": row.import_hash,
                "raw_file_hash": row.raw_file_hash,
                "content_hash": row.content_hash,
                "row_count": row.row_count,
                "status": row.status,
                "publication_sequence": row.publication_sequence,
                "created_at": _normalized_control_time(row.created_at),
            }
            for row in rows
        ]
    if table_name == "import_content_attempts":
        rows = SalesImportAttempt.objects.filter(domain="sales").order_by(
            Collate(models.F("id"), _target_binary_collation(target_connection.vendor))
        )
        return [
            {
                "attempt_id": row.id,
                "domain": row.domain,
                "batch_id": row.batch_id,
                "scope_key": row.scope_key,
                "scope_json": row.scope_json,
                "import_hash": row.import_hash,
                "raw_file_hash": row.raw_file_hash,
                "content_hash": row.content_hash,
                "row_count": row.row_count,
                "file_name": row.file_name,
                "file_size_bytes": row.file_size_bytes,
                "actor": row.actor_email,
                "warnings_json": row.warnings,
                "outcome": row.outcome,
                "error_code": row.error_code,
                "recovered_from_attempt_id": row.recovered_from_attempt_id,
                "created_at": _normalized_control_time(row.created_at),
                "updated_at": _normalized_control_time(row.updated_at),
            }
            for row in rows
        ]
    rows = SalesImportScopeHead.objects.filter(domain="sales").order_by(
        Collate(models.F("scope_key"), _target_binary_collation(target_connection.vendor))
    )
    return [
        {
            "domain": row.domain,
            "scope_key": row.scope_key,
            "state_token": row.state_token,
            "status": row.status,
            "owner_token": row.owner_token,
            "current_batch_id": row.current_batch_id,
            "generation": row.generation,
            "updated_at": _normalized_control_time(row.updated_at),
        }
        for row in rows
    ]


def _target_control_snapshot() -> tuple[dict[str, int], dict[str, str]]:
    records = {
        table_name: _target_control_records(table_name)
        for table_name in CONTROL_TABLE_COLUMNS
    }
    return (
        {table_name: len(items) for table_name, items in records.items()},
        {
            table_name: _control_digest(table_name, items)
            for table_name, items in records.items()
        },
    )


def _parsed_control_datetime(value: object):
    parsed = parse_datetime(str(value))
    if parsed is None:
        raise CommandError("D1 销售导入控制表包含无效时间")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, datetime_timezone.utc)
    return parsed


def _apply_control_snapshot(records: dict[str, list[dict[str, object]]]) -> None:
    SalesImportAttempt.objects.filter(domain="sales").delete()
    SalesImportFingerprint.objects.filter(domain="sales").delete()
    SalesImportScopeHead.objects.filter(domain="sales").delete()

    for item in records["import_content_fingerprints"]:
        created_at = _parsed_control_datetime(item["created_at"])
        row = SalesImportFingerprint.objects.create(
            domain=str(item["domain"]),
            batch_id=str(item["batch_id"]),
            scope_key=str(item["scope_key"]),
            scope_json=item["scope_json"],
            import_hash=str(item["import_hash"]),
            raw_file_hash=str(item["raw_file_hash"]),
            content_hash=str(item["content_hash"]),
            row_count=int(item["row_count"]),
            status=str(item["status"]),
            publication_sequence=(
                int(item["publication_sequence"])
                if item["publication_sequence"] is not None
                else None
            ),
        )
        SalesImportFingerprint.objects.filter(pk=row.pk).update(created_at=created_at)

    for item in records["import_content_attempts"]:
        created_at = _parsed_control_datetime(item["created_at"])
        updated_at = _parsed_control_datetime(item["updated_at"])
        SalesImportAttempt.objects.create(
            id=str(item["attempt_id"]),
            domain=str(item["domain"]),
            batch_id=str(item["batch_id"] or ""),
            scope_key=str(item["scope_key"] or ""),
            scope_json=item["scope_json"],
            import_hash=str(item["import_hash"] or ""),
            raw_file_hash=str(item["raw_file_hash"] or ""),
            content_hash=str(item["content_hash"] or ""),
            row_count=int(item["row_count"]),
            file_name=str(item["file_name"] or ""),
            file_size_bytes=int(item["file_size_bytes"]),
            actor_email=str(item["actor"] or ""),
            warnings=item["warnings_json"],
            outcome=str(item["outcome"]),
            error_code=str(item["error_code"] or ""),
            recovered_from_attempt_id=str(item["recovered_from_attempt_id"] or ""),
        )
        SalesImportAttempt.objects.filter(pk=str(item["attempt_id"])).update(
            created_at=created_at, updated_at=updated_at
        )

    for item in records["import_scope_heads"]:
        row = SalesImportScopeHead.objects.create(
            domain=str(item["domain"]),
            scope_key=str(item["scope_key"]),
            state_token=str(item["state_token"] or "initial"),
            status=str(item["status"]),
            owner_token=str(item["owner_token"] or ""),
            current_batch_id=str(item["current_batch_id"] or ""),
            generation=int(item["generation"]),
        )
        SalesImportScopeHead.objects.filter(pk=row.pk).update(
            updated_at=_parsed_control_datetime(item["updated_at"])
        )

    attempts_by_batch = {
        str(item["batch_id"]): item
        for item in records["import_content_attempts"]
        if item["batch_id"]
    }
    head_tokens = {
        str(item["current_batch_id"]): str(item["state_token"])
        for item in records["import_scope_heads"]
        if item["current_batch_id"]
    }
    for item in records["import_content_fingerprints"]:
        batch_id = str(item["batch_id"])
        attempt = attempts_by_batch.get(batch_id, {})
        SalesImportBatch.objects.filter(id=batch_id).update(
            raw_file_hash=str(item["raw_file_hash"]),
            content_hash=str(item["content_hash"]),
            scope_key=str(item["scope_key"]),
            scope_json=item["scope_json"],
            published_state_token=head_tokens.get(batch_id, ""),
            actor_email=str(attempt.get("actor") or ""),
        )


def _apply_table(connection: sqlite3.Connection, spec: TableSpec, batch_size: int, generation: str) -> tuple[int, str]:
    digest = _new_table_digest(spec)
    count = 0
    erp_categories = _source_erp_categories(connection) if spec.model is SalesOrderLine else {}
    update_fields = list(spec.update_fields)
    if spec.model is SalesOrderLine:
        update_fields.extend(SALES_PROJECTION_FIELDS)
    for rows in _source_rows(connection, spec, batch_size):
        objects = []
        for row in rows:
            values = tuple(row)
            digest.update(_canonical_bytes(values))
            payload = dict(zip(spec.payload_columns, values, strict=True))
            if spec.model is SalesOrderLine:
                payload.update(
                    sales_projection_values(
                        payload,
                        erp_category=erp_categories.get(str(payload.get("product_code") or ""), ""),
                    )
                )
            payload["migration_generation"] = generation
            objects.append(spec.model(**payload))
        spec.model.objects.bulk_create(
            objects,
            batch_size=batch_size,
            update_conflicts=True,
            update_fields=update_fields,
            unique_fields=list(spec.unique_fields),
        )
        count += len(objects)
    # The D1 tables are authoritative full snapshots for this migration slice.
    spec.model.objects.exclude(migration_generation=generation).delete()
    return count, digest.hexdigest()


def _fingerprint(path: Path) -> str:
    """Bind approval to the same filesystem object without treating live writes
    to unrelated D1 domains as a different source file.

    The sales/ERP revision plus complete table digests bind the approved
    business snapshot.  Device/inode detects replacement at the same path,
    while remaining stable when workerd updates other tables in the same D1.
    """
    stat = path.stat()
    return hashlib.sha256(
        f"file-identity-v2\n{stat.st_dev}\n{stat.st_ino}".encode()
    ).hexdigest()


def _domain_digest_for_format(
    domain: str,
    table_digests: dict[str, str],
    canonical_format_version: str,
) -> str:
    if domain == "sales":
        table_names = ("sales_import_batches", "sales_order_lines")
    elif domain == "erp":
        table_names = ("erp_product_master",)
    else:
        raise ValueError("unknown revision domain")
    material = _canonical_bytes(
        (
            canonical_format_version,
            "domain",
            domain,
            *((table_name, table_digests[table_name]) for table_name in table_names),
        )
    )
    return hashlib.sha256(material).hexdigest()


def _domain_digest(domain: str, table_digests: dict[str, str]) -> str:
    return _domain_digest_for_format(
        domain,
        table_digests,
        CANONICAL_FORMAT_VERSION,
    )


def _managed_legacy_digest_upgrade_enabled(explicitly_allowed: bool) -> bool:
    production = os.getenv("TERUISI_DJANGO_CUTOVER_MANAGED", "") == "1"
    rehearsal = os.getenv("TERUISI_DJANGO_CUTOVER_REHEARSAL_MANAGED", "") == "1"
    return (
        explicitly_allowed
        and settings.DJANGO_PROCESS_ROLE == "migration_writer"
        and production != rehearsal
    )


def _target_domain_matches_source(
    domain: str,
    source_counts: dict[str, int],
    source_digests: dict[str, str],
    batch_size: int,
) -> bool:
    table_names = {
        "sales": {"sales_import_batches", "sales_order_lines"},
        "erp": {"erp_product_master"},
    }[domain]
    for spec in SPECS:
        if spec.source_table not in table_names:
            continue
        count, digest = _target_digest(spec, batch_size)
        if (
            count != source_counts.get(spec.source_table)
            or digest != source_digests.get(spec.source_table)
        ):
            return False
    if domain == "sales":
        count, digest = _target_projection_digest(batch_size)
        if (
            count != source_counts.get(QUERY_PROJECTION_DIGEST_KEY)
            or digest != source_digests.get(QUERY_PROJECTION_DIGEST_KEY)
        ):
            return False
    return True


def _legacy_revision_evidence_matches(
    domain: str,
    current_digest: str,
    source_revision_token: str,
) -> bool:
    applies = SalesMigrationRun.objects.filter(
        status="completed",
        dry_run=False,
        source_revision=source_revision_token,
        target_revision=source_revision_token,
        canonical_format_version__in=LEGACY_DOMAIN_DIGEST_FORMAT_VERSIONS,
    ).exclude(approved_run_id="")
    for applied in applies.order_by("-completed_at"):
        if (
            applied.completed_at is None
            or not applied.source_counts
            or applied.source_counts != applied.target_counts
            or not applied.source_digests
            or applied.source_digests != applied.target_digests
        ):
            continue
        try:
            legacy_digest = _domain_digest_for_format(
                domain,
                applied.target_digests,
                applied.canonical_format_version,
            )
        except (KeyError, ValueError):
            continue
        if legacy_digest != current_digest:
            continue
        approval = SalesMigrationRun.objects.filter(
            id=applied.approved_run_id,
            status="dry_run_completed",
            dry_run=True,
            canonical_format_version=applied.canonical_format_version,
            source_revision=source_revision_token,
            consumed_by_run_id=applied.id,
        ).first()
        if (
            approval is None
            or approval.completed_at is None
            or approval.source_counts != applied.source_counts
            or approval.source_digests != applied.source_digests
        ):
            continue
        verified = SalesMigrationRun.objects.filter(
            status="verified",
            dry_run=False,
            canonical_format_version=applied.canonical_format_version,
            source_revision=source_revision_token,
            target_revision=source_revision_token,
        )
        if not any(
            run.completed_at is not None
            and run.source_counts == applied.source_counts
            and run.target_counts == applied.target_counts
            and run.source_digests == applied.source_digests
            and run.target_digests == applied.target_digests
            for run in verified
        ):
            continue
        return True
    return False


def _allow_legacy_revision_digest_upgrade(
    *,
    explicitly_allowed: bool,
    domain: str,
    current_digest: str,
    source_revision_token: str,
    source_counts: dict[str, int],
    source_digests: dict[str, str],
    batch_size: int,
) -> bool:
    return (
        _managed_legacy_digest_upgrade_enabled(explicitly_allowed)
        and _target_domain_matches_source(
            domain,
            source_counts,
            source_digests,
            batch_size,
        )
        and _legacy_revision_evidence_matches(
            domain,
            current_digest,
            source_revision_token,
        )
    )


def _validated_erp_checkpoint_digest(
    *,
    source_path_digest: str,
    source_revision: tuple[int, int],
    source_counts: dict[str, int],
    source_digests: dict[str, str],
    source_erp_state: dict[str, object] | None,
    batch_size: int,
) -> str | None:
    lock_erp_reference_for_replace()
    checkpoint = (
        ErpReferenceSyncCheckpoint.objects.select_for_update().filter(id=1).first()
    )
    if checkpoint is None:
        return None
    digest = str(checkpoint.content_hash or "")
    if source_erp_state is None:
        raise CommandError("PostgreSQL ERP checkpoint 存在但 D1 0091 状态缺失")
    expected = {
        "source_epoch": str(checkpoint.source_epoch),
        "erp_revision": int(checkpoint.erp_revision),
        "source_batch_id": str(checkpoint.source_batch_id),
        "row_count": int(checkpoint.row_count),
        "content_hash": digest,
        "head_sequence": int(checkpoint.last_event_sequence),
        "head_event_id": str(checkpoint.last_event_id),
    }
    if (
        checkpoint.source_path_digest != source_path_digest
        or checkpoint.erp_revision != source_revision[1]
        or checkpoint.row_count != source_counts.get("erp_product_master")
        or not re.fullmatch(r"[0-9a-f]{32}", str(checkpoint.source_epoch))
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or source_erp_state != expected
        or not _target_domain_matches_source(
            "erp",
            source_counts,
            source_digests,
            batch_size,
        )
    ):
        raise CommandError("PostgreSQL ERP checkpoint 与 D1 0091/目标主数据不一致")
    return digest


def _approved_dry_run(
    approved_run_id: str,
    *,
    source_fingerprint: str,
    source_path_digest: str,
    source_revision: str,
    source_counts: dict[str, int],
    source_digests: dict[str, str],
) -> SalesMigrationRun:
    try:
        approval = SalesMigrationRun.objects.select_for_update().get(id=approved_run_id)
    except SalesMigrationRun.DoesNotExist as error:
        raise CommandError("--approved-run-id 不存在") from error
    if not approval.dry_run or approval.status != "dry_run_completed" or approval.completed_at is None:
        raise CommandError("审批运行不是已成功完成的 dry-run")
    if approval.canonical_format_version != CANONICAL_FORMAT_VERSION:
        raise CommandError("审批运行的 canonical format version 与当前命令不一致")
    if approval.consumed_by_run_id or approval.approval_consumed_at is not None:
        raise CommandError("该 dry-run 审批已被消费，不得重复使用")
    if SalesMigrationRun.objects.filter(approved_run_id=approval.id).exists():
        raise CommandError("该 dry-run 审批已关联 apply 运行，不得重复使用")
    expected = {
        "source_path_digest": source_path_digest,
        "source_fingerprint": source_fingerprint,
        "source_revision": source_revision,
        "source_counts": source_counts,
        "source_digests": source_digests,
    }
    actual = {
        "source_path_digest": approval.source_path_digest,
        "source_fingerprint": approval.source_fingerprint,
        "source_revision": approval.source_revision,
        "source_counts": approval.source_counts,
        "source_digests": approval.source_digests,
    }
    mismatches = [field for field in expected if expected[field] != actual[field]]
    if mismatches:
        raise CommandError(
            "D1 源与 dry-run 审批不一致: " + ", ".join(mismatches)
        )
    return approval


def _recover_completed_apply(
    approved_run_id: str,
    *,
    source: Path,
    batch_size: int,
) -> SalesMigrationRun:
    """Recover an apply whose transaction committed but command output was lost.

    This path is deliberately read-only. It binds the approval, its unique
    consumer, and the complete current D1 snapshot before returning a run id.
    """

    try:
        approval = SalesMigrationRun.objects.get(id=approved_run_id)
    except SalesMigrationRun.DoesNotExist as error:
        raise CommandError("--approved-run-id 不存在") from error
    try:
        applied = SalesMigrationRun.objects.get(approved_run_id=approved_run_id)
    except SalesMigrationRun.DoesNotExist as error:
        raise CommandError("dry-run 审批尚无已提交的 apply 运行") from error
    if (
        not approval.dry_run
        or approval.status != "dry_run_completed"
        or approval.completed_at is None
        or approval.canonical_format_version != CANONICAL_FORMAT_VERSION
    ):
        raise CommandError("审批运行不是当前 v4 已完成 dry-run")
    if (
        applied.dry_run
        or applied.status != "completed"
        or applied.completed_at is None
        or applied.canonical_format_version != CANONICAL_FORMAT_VERSION
        or approval.consumed_by_run_id != applied.id
        or approval.approval_consumed_at is None
    ):
        raise CommandError("审批与已提交 apply 的消费关系不完整")

    source_fingerprint = _fingerprint(source)
    source_path_digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
    source_connection = _open_source(source)
    try:
        (
            source_revision,
            source_counts,
            source_digests,
            _source_control_records,
            _source_legacy_records,
        ) = _complete_source_snapshot(source_connection, batch_size)
    finally:
        source_connection.rollback()
        source_connection.close()
    source_revision_token = f"{source_revision[0]}:{source_revision[1]}"
    _ensure_complete_source_stable(
        source,
        expected_revision=source_revision,
        expected_counts=source_counts,
        expected_digests=source_digests,
        batch_size=batch_size,
    )
    expected = {
        "source_fingerprint": source_fingerprint,
        "source_path_digest": source_path_digest,
        "source_revision": source_revision_token,
        "source_counts": source_counts,
        "source_digests": source_digests,
    }
    approval_actual = {field: getattr(approval, field) for field in expected}
    apply_actual = {field: getattr(applied, field) for field in expected}
    if approval_actual != expected or apply_actual != expected:
        raise CommandError("当前 D1 全量快照与已消费审批/apply 不一致")
    if (
        applied.target_revision != source_revision_token
        or applied.target_counts != source_counts
        or applied.target_digests != source_digests
    ):
        raise CommandError("已提交 apply 的目标证据不完整")
    return applied


class Command(BaseCommand):
    help = "Stream an authoritative, verified sales read-model snapshot from a read-only local D1 SQLite file."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True, help="Path to the local D1 SQLite file")
        parser.add_argument("--batch-size", type=int, default=1000)
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="Read and digest the source without changing business tables")
        mode.add_argument("--verify-only", action="store_true", help="Compare the complete source and target snapshots")
        mode.add_argument("--apply", action="store_true", help="Apply exactly one previously approved dry-run snapshot")
        mode.add_argument(
            "--recover-approved-apply",
            action="store_true",
            help="Read-only recovery of a committed apply whose output was lost",
        )
        parser.add_argument(
            "--approved-run-id",
            default="",
            help="Successful, unconsumed --dry-run id required by --apply",
        )
        parser.add_argument(
            "--allow-legacy-digest-upgrade",
            action="store_true",
            help="Allow one managed cutover to upgrade a proven v2/v3 revision digest",
        )

    def handle(self, *args, **options):
        try:
            source = Path(options["source"]).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise CommandError("--source 文件不存在或无法解析") from error
        if not source.is_file():
            raise CommandError("--source 必须指向 SQLite 文件")
        _reject_source_target_alias(source)
        batch_size = int(options["batch_size"])
        if batch_size < 100 or batch_size > 10_000:
            raise CommandError("--batch-size 必须在 100 到 10000 之间")
        dry_run = bool(options["dry_run"])
        verify_only = bool(options["verify_only"])
        apply = bool(options["apply"])
        recover_approved_apply = bool(options["recover_approved_apply"])
        allow_legacy_digest_upgrade = bool(options["allow_legacy_digest_upgrade"])
        approved_run_id = str(options.get("approved_run_id") or "").strip()
        if not dry_run and not verify_only and not apply and not recover_approved_apply:
            raise CommandError("必须显式选择 --dry-run、--verify-only 或 --apply；省略模式不会写入")
        if (apply or recover_approved_apply) and not approved_run_id:
            raise CommandError("--apply/--recover-approved-apply 必须同时提供 --approved-run-id")
        if approved_run_id and not (apply or recover_approved_apply):
            raise CommandError("--approved-run-id 只能与 --apply/--recover-approved-apply 同时使用")
        if len(approved_run_id) > 64:
            raise CommandError("--approved-run-id 无效")
        if allow_legacy_digest_upgrade and not apply:
            raise CommandError("--allow-legacy-digest-upgrade 只能与 --apply 同时使用")
        if allow_legacy_digest_upgrade and not _managed_legacy_digest_upgrade_enabled(True):
            raise CommandError(
                "--allow-legacy-digest-upgrade 只允许受控 cutover migration_writer"
            )
        if recover_approved_apply:
            recovered = _recover_completed_apply(
                approved_run_id,
                source=source,
                batch_size=batch_size,
            )
            self.stdout.write(
                json.dumps(
                    {
                        "status": "recovered_completed_apply",
                        "runId": recovered.id,
                        "approvedRunId": approved_run_id,
                        "canonicalFormatVersion": CANONICAL_FORMAT_VERSION,
                        "sourceRevision": recovered.source_revision,
                    },
                    ensure_ascii=False,
                )
            )
            return
        run_id = uuid.uuid4().hex
        generation = uuid.uuid4().hex
        path_digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
        source_fingerprint = _fingerprint(source)
        run = SalesMigrationRun.objects.create(
            id=run_id,
            status="processing" if apply else "checking",
            dry_run=dry_run,
            source_fingerprint=source_fingerprint,
            source_path_digest=path_digest,
            generation=generation,
            canonical_format_version=CANONICAL_FORMAT_VERSION,
        )
        connection: sqlite3.Connection | None = None
        try:
            connection = _open_source(source)
            (
                source_revision,
                source_counts,
                source_digests,
                source_control_records,
                source_legacy_records,
            ) = _complete_source_snapshot(connection, batch_size)
            source_revision_token = f"{source_revision[0]}:{source_revision[1]}"
            source_erp_state = _source_erp_bridge_state(connection)
            run.source_revision = source_revision_token
            run.save(update_fields=["source_revision"])

            if dry_run:
                _ensure_complete_source_stable(
                    source,
                    expected_revision=source_revision,
                    expected_counts=source_counts,
                    expected_digests=source_digests,
                    batch_size=batch_size,
                )
                run.status = "dry_run_completed"
                run.source_counts = source_counts
                run.source_digests = source_digests
                run.completed_at = timezone.now()
                run.save(
                    update_fields=[
                        "status",
                        "source_counts",
                        "source_digests",
                        "completed_at",
                    ]
                )
                self.stdout.write(
                    json.dumps(
                        {
                            "status": run.status,
                            "runId": run_id,
                            "canonicalFormatVersion": CANONICAL_FORMAT_VERSION,
                            "sourceCounts": source_counts,
                            "sourceDigests": source_digests,
                            "sourceRevision": source_revision_token,
                        },
                        ensure_ascii=False,
                    )
                )
                return

            if verify_only:
                target_counts: dict[str, int] = {}
                target_digests: dict[str, str] = {}
                # select_for_update serializes verification against apply without
                # changing the lock row or any business/revision table.
                with transaction.atomic():
                    acquire_sales_write_authority_exclusive_lock()
                    lock = SalesMigrationLock.objects.select_for_update().get(name="sales_snapshot")
                    if lock.owner_id:
                        raise CommandError("已有销售快照迁移持有目标写锁")
                    for spec in SPECS:
                        target_counts[spec.source_table], target_digests[spec.source_table] = _target_digest(
                            spec, batch_size
                        )
                    target_projection_count, target_projection_digest = _target_projection_digest(batch_size)
                    target_counts[QUERY_PROJECTION_DIGEST_KEY] = target_projection_count
                    target_digests[QUERY_PROJECTION_DIGEST_KEY] = target_projection_digest
                    target_control_counts, target_control_digests = _target_control_snapshot()
                    target_counts.update(target_control_counts)
                    target_digests.update(target_control_digests)
                    target_legacy_counts, target_legacy_digests = (
                        _target_legacy_upload_snapshot()
                    )
                    target_counts.update(target_legacy_counts)
                    target_digests.update(target_legacy_digests)
                    if source_counts != target_counts or source_digests != target_digests:
                        raise CommandError("源与目标销售快照及查询投影的行数或摘要不一致")
                    _verify_target_revisions(
                        source_revision,
                        source_counts,
                        source_digests,
                        path_digest,
                        source_erp_state,
                        batch_size,
                    )
                    run.target_revision = source_revision_token
                _ensure_complete_source_stable(
                    source,
                    expected_revision=source_revision,
                    expected_counts=source_counts,
                    expected_digests=source_digests,
                    batch_size=batch_size,
                )
                run.status = "verified"
                run.source_counts = source_counts
                run.source_digests = source_digests
                run.target_counts = target_counts
                run.target_digests = target_digests
                run.completed_at = timezone.now()
                run.save(update_fields=["status", "source_counts", "source_digests", "target_counts", "target_digests", "target_revision", "completed_at"])
                self.stdout.write(
                    json.dumps(
                        {
                            "status": run.status,
                            "runId": run_id,
                            "canonicalFormatVersion": CANONICAL_FORMAT_VERSION,
                            "sourceCounts": source_counts,
                            "sourceRevision": source_revision_token,
                        },
                        ensure_ascii=False,
                    )
                )
                return

            with transaction.atomic():
                acquire_sales_write_authority_exclusive_lock()
                lock = _lock_snapshot(run_id)
                _ensure_target_sales_writer_quiet()
                approval = _approved_dry_run(
                    approved_run_id,
                    source_fingerprint=source_fingerprint,
                    source_path_digest=path_digest,
                    source_revision=source_revision_token,
                    source_counts=source_counts,
                    source_digests=source_digests,
                )
                checkpoint_digest = _validated_erp_checkpoint_digest(
                    source_path_digest=path_digest,
                    source_revision=source_revision,
                    source_counts=source_counts,
                    source_digests=source_digests,
                    source_erp_state=source_erp_state,
                    batch_size=batch_size,
                )
                revisions: dict[str, SalesDataRevision] = {}
                for domain, source_value in (
                    ("sales", source_revision[0]),
                    ("erp", source_revision[1]),
                ):
                    revision, _ = SalesDataRevision.objects.select_for_update().get_or_create(
                        domain=domain
                    )
                    revisions[domain] = revision
                    expected_digest = (
                        checkpoint_digest
                        if domain == "erp" and checkpoint_digest is not None
                        else _domain_digest(domain, source_digests)
                    )
                    if revision.revision > source_value:
                        raise CommandError(
                            f"拒绝把 {domain} 数据版本从 {revision.revision} 降级到 {source_value}"
                        )
                    if domain == "erp" and checkpoint_digest is not None:
                        if (
                            revision.revision != source_value
                            or revision.source_digest != checkpoint_digest
                        ):
                            raise CommandError(
                                "PostgreSQL ERP revision 与 checkpoint 不一致"
                            )
                        continue
                    if (
                        revision.revision == source_value
                        and revision.source_digest
                        and revision.source_digest != expected_digest
                        and not _allow_legacy_revision_digest_upgrade(
                            explicitly_allowed=allow_legacy_digest_upgrade,
                            domain=domain,
                            current_digest=revision.source_digest,
                            source_revision_token=source_revision_token,
                            source_counts=source_counts,
                            source_digests=source_digests,
                            batch_size=batch_size,
                        )
                    ):
                        raise CommandError(
                            f"D1 {domain} 数据在未提升版本水位时发生变化，拒绝发布"
                        )
                applied_counts: dict[str, int] = {}
                applied_digests: dict[str, str] = {}
                for spec in SPECS:
                    applied_counts[spec.source_table], applied_digests[spec.source_table] = _apply_table(
                        connection, spec, batch_size, generation
                    )
                _apply_control_snapshot(source_control_records)
                _apply_legacy_upload_snapshot(source_legacy_records, generation)
                applied_projection_count, applied_projection_digest = _target_projection_digest(batch_size)
                applied_counts[QUERY_PROJECTION_DIGEST_KEY] = applied_projection_count
                applied_digests[QUERY_PROJECTION_DIGEST_KEY] = applied_projection_digest
                applied_control_counts, applied_control_digests = _target_control_snapshot()
                applied_counts.update(applied_control_counts)
                applied_digests.update(applied_control_digests)
                applied_legacy_counts, applied_legacy_digests = (
                    _target_legacy_upload_snapshot()
                )
                applied_counts.update(applied_legacy_counts)
                applied_digests.update(applied_legacy_digests)
                if source_counts != applied_counts or source_digests != applied_digests:
                    mismatches = sorted(
                        key
                        for key in set(source_counts) | set(applied_counts)
                        if source_counts.get(key) != applied_counts.get(key)
                        or source_digests.get(key) != applied_digests.get(key)
                    )
                    raise CommandError(
                        "apply 期间读取的 D1 快照与已审批摘要不一致: "
                        + ", ".join(mismatches)
                    )
                target_counts: dict[str, int] = {}
                target_digests: dict[str, str] = {}
                for spec in SPECS:
                    target_counts[spec.source_table], target_digests[spec.source_table] = _target_digest(spec, batch_size)
                target_projection_count, target_projection_digest = _target_projection_digest(batch_size)
                target_counts[QUERY_PROJECTION_DIGEST_KEY] = target_projection_count
                target_digests[QUERY_PROJECTION_DIGEST_KEY] = target_projection_digest
                target_control_counts, target_control_digests = _target_control_snapshot()
                target_counts.update(target_control_counts)
                target_digests.update(target_control_digests)
                target_legacy_counts, target_legacy_digests = (
                    _target_legacy_upload_snapshot()
                )
                target_counts.update(target_legacy_counts)
                target_digests.update(target_legacy_digests)
                if source_counts != target_counts or source_digests != target_digests:
                    mismatches = sorted(
                        key
                        for key in set(source_counts) | set(target_counts)
                        if source_counts.get(key) != target_counts.get(key)
                        or source_digests.get(key) != target_digests.get(key)
                    )
                    raise CommandError(
                        "迁移后目标销售快照及查询投影的行数或摘要校验失败: "
                        + ", ".join(mismatches)
                    )
                _ensure_complete_source_stable(
                    source,
                    expected_revision=source_revision,
                    expected_counts=source_counts,
                    expected_digests=source_digests,
                    batch_size=batch_size,
                )
                for domain, source_value in (
                    ("sales", source_revision[0]),
                    ("erp", source_revision[1]),
                ):
                    if domain == "erp" and checkpoint_digest is not None:
                        continue
                    revision = revisions[domain]
                    domain_digest = _domain_digest(domain, source_digests)
                    revision.revision = source_value
                    revision.source_digest = domain_digest
                    revision.save(update_fields=["revision", "source_digest", "updated_at"])
                # This PG-only directory cannot inherit readiness from D1 data.
                from sales.models import SalesAnalysisOption, SalesAnalysisOptionsState
                SalesAnalysisOption.objects.all().delete()
                SalesAnalysisOptionsState.objects.filter(id=1).update(status="not_ready", generation="",
                    directory_digest="", identity_count=0, stored_bytes=0, source_sales_revision=-1,
                    reason="d1_reinitialized")
                run.status = "completed"
                run.source_counts = source_counts
                run.target_counts = target_counts
                run.source_digests = source_digests
                run.target_digests = target_digests
                run.target_revision = source_revision_token
                run.approved_run_id = approval.id
                run.completed_at = timezone.now()
                approval.consumed_by_run_id = run_id
                approval.approval_consumed_at = run.completed_at
                approval.save(update_fields=["consumed_by_run_id", "approval_consumed_at"])
                run.save(update_fields=["status", "source_counts", "target_counts", "source_digests", "target_digests", "target_revision", "approved_run_id", "completed_at"])
                lock.owner_id = ""
                lock.save(update_fields=["owner_id", "updated_at"])
            self.stdout.write(
                json.dumps(
                    {
                        "status": "completed",
                        "runId": run_id,
                        "approvedRunId": approved_run_id,
                        "canonicalFormatVersion": CANONICAL_FORMAT_VERSION,
                        "counts": source_counts,
                        "digests": source_digests,
                        "sourceRevision": source_revision_token,
                    },
                    ensure_ascii=False,
                )
            )
        except Exception as error:
            # A stdout/pipe failure can occur after the apply transaction has
            # committed and consumed its approval.  The database is the source
            # of truth: never rewrite a terminal success to failed or claim its
            # business transaction rolled back merely because the response was
            # lost.
            try:
                persisted_status = SalesMigrationRun.objects.filter(id=run_id).values_list(
                    "status", flat=True
                ).first()
            except Exception:
                persisted_status = None
            if persisted_status in {"completed", "verified", "dry_run_completed"}:
                raise DjangoCommandError(
                    "销售数据迁移已完成提交，但结果输出失败；请按迁移审计恢复并执行 verify-only"
                ) from None

            controlled = isinstance(error, CommandError)
            public_message = (
                str(error)[:2000]
                if controlled
                else "销售数据迁移发生内部错误；业务表事务已回滚"
            )
            error_code = "migration_rejected" if controlled else "migration_internal_error"
            try:
                SalesMigrationRun.objects.filter(
                    id=run_id, status__in=["processing", "checking"]
                ).update(
                    status="failed",
                    error_code=error_code,
                    error_message=public_message,
                    completed_at=timezone.now(),
                )
            except Exception:
                # Never expose a secondary database exception from the audit
                # write; it may contain a complete failing row or connection DSN.
                pass
            if controlled:
                raise CommandError(public_message) from None
            raise DjangoCommandError(
                "销售数据迁移失败；业务表事务已回滚，请查看迁移审计记录"
            ) from None
        finally:
            if connection is not None:
                connection.rollback()
                connection.close()
