from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence, TypeVar

from django.db import DatabaseError, connection as target_connection, transaction
from django.utils import timezone

from .locking import lock_erp_reference_for_replace
from .models import ErpReferenceSyncCheckpoint


CANONICAL_FORMAT_VERSION = "erp-reference-projection-v1"
ERP_SCOPE_JSON = '{"source":"products"}'
MAX_PRODUCT_ROWS = 1_000_000
MAX_PRODUCT_PAYLOAD_BYTES = 512 * 1024 * 1024
MAX_TEXT_FIELD_BYTES = 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SOURCE_EPOCH_PATTERN = re.compile(r"^[0-9a-f]{32}$")

ERP_PRODUCT_COLUMNS = (
    "product_code",
    "product_name",
    "brand",
    "specification",
    "barcode",
    "category",
    "supplier",
    "product_status",
    "source_row_number",
    "last_import_batch_id",
    "created_at",
    "updated_at",
)
OUTBOX_COLUMNS = (
    "event_sequence",
    "event_id",
    "source_epoch",
    "domain",
    "operation",
    "scope_json",
    "source_batch_id",
    "erp_revision",
    "row_count",
    "content_hash",
    "canonical_format_version",
    "created_at",
)


class ErpReferenceSyncError(RuntimeError):
    """Fail-closed ERP reference synchronization error."""


class SourceChangedDuringSync(ErpReferenceSyncError):
    """The D1 ERP authority changed while the PG transaction was in flight."""


@dataclass(frozen=True)
class SourceState:
    source_epoch: str
    head_sequence: int
    head_event_id: str
    erp_revision: int
    content_hash: str
    row_count: int
    source_batch_id: str


@dataclass(frozen=True)
class OutboxEvent:
    event_sequence: int
    event_id: str
    source_epoch: str
    domain: str
    operation: str
    scope_json: str
    source_batch_id: str
    erp_revision: int
    row_count: int
    content_hash: str
    canonical_format_version: str
    created_at: str


def resolve_source_path(value: str | os.PathLike[str]) -> Path:
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ErpReferenceSyncError("--source 文件不存在或无法解析") from error
    if not path.is_file() or path.suffix.lower() != ".sqlite":
        raise ErpReferenceSyncError("--source 必须精确指向 D1 SQLite 文件")
    if target_connection.vendor == "sqlite":
        target_name = str(target_connection.settings_dict.get("NAME") or "")
        if target_name and target_name != ":memory:" and not target_name.startswith("file:"):
            target_path = Path(target_name).expanduser().resolve()
            try:
                aliases = path == target_path or os.path.samefile(path, target_path)
            except (FileNotFoundError, OSError):
                aliases = path == target_path
            if aliases:
                raise ErpReferenceSyncError("D1 只读源不能与 Django 目标使用同一文件")
    return path


def source_path_digest(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()


def _open_source(path: Path) -> sqlite3.Connection:
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only = ON")
    source.execute("BEGIN")
    return source


def _table_columns(source: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in source.execute(f'PRAGMA table_info("{table}")')}


def _validate_source_schema(source: sqlite3.Connection) -> None:
    required = {
        "erp_reference_projection_source_state": {"id", "source_epoch"},
        "erp_product_projection_state": {
            "id",
            "erp_revision",
            "source_batch_id",
            "row_count",
            "content_hash",
        },
        "erp_reference_projection_outbox": set(OUTBOX_COLUMNS),
        "erp_reference_import_batches": {
            "id",
            "source_key",
            "status",
            "row_count",
            "totals_json",
        },
        "erp_product_master": set(ERP_PRODUCT_COLUMNS),
    }
    for table, columns in required.items():
        actual = _table_columns(source, table)
        if not actual:
            raise ErpReferenceSyncError(f"D1 ERP 源缺少必需表 {table}")
        missing = columns - actual
        if missing:
            raise ErpReferenceSyncError(
                f"D1 ERP 源表 {table} 缺少字段: {', '.join(sorted(missing))}"
            )


def _validate_hash(value: object, label: str) -> str:
    result = str(value or "")
    if not SHA256_PATTERN.fullmatch(result):
        raise ErpReferenceSyncError(f"{label} 必须是 64 位小写 SHA-256")
    return result


def _read_source_state(source: sqlite3.Connection) -> SourceState:
    epoch_row = source.execute(
        "SELECT source_epoch FROM erp_reference_projection_source_state WHERE id = 1"
    ).fetchone()
    state_row = source.execute(
        "SELECT erp_revision, content_hash, row_count, source_batch_id "
        "FROM erp_product_projection_state WHERE id = 1"
    ).fetchone()
    if epoch_row is None or state_row is None:
        raise ErpReferenceSyncError("D1 ERP 源缺少 source epoch 或 revision 水位")
    source_epoch = str(epoch_row[0])
    if not SOURCE_EPOCH_PATTERN.fullmatch(source_epoch):
        raise ErpReferenceSyncError("D1 ERP source epoch 格式无效")
    erp_revision = int(state_row[0])
    row_count = int(state_row[2])
    source_batch_id = str(state_row[3] or "")
    if erp_revision < 1 or row_count <= 0 or row_count > MAX_PRODUCT_ROWS:
        raise ErpReferenceSyncError("D1 ERP revision 或行数水位无效")
    content_hash = _validate_hash(state_row[1], "D1 ERP 内容摘要")
    head = source.execute(
        "SELECT event_sequence, event_id, source_epoch, source_batch_id, "
        "erp_revision, row_count, content_hash, domain, operation, scope_json, "
        "canonical_format_version FROM erp_reference_projection_outbox "
        "ORDER BY event_sequence DESC LIMIT 1"
    ).fetchone()
    if head is None:
        return SourceState(
            source_epoch,
            0,
            "",
            erp_revision,
            content_hash,
            row_count,
            source_batch_id,
        )
    head_sequence = int(head[0])
    head_event_id = str(head[1])
    if (
        head_sequence < 1
        or str(head[2]) != source_epoch
        or str(head[3]) != source_batch_id
        or int(head[4]) != erp_revision
        or int(head[5]) != row_count
        or str(head[6]) != content_hash
        or str(head[7]) != "erp"
        or str(head[8]) != "replace_all"
        or str(head[9]) != ERP_SCOPE_JSON
        or str(head[10]) != CANONICAL_FORMAT_VERSION
        or head_event_id != f"{source_epoch}:erp:{source_batch_id}"
    ):
        raise ErpReferenceSyncError("D1 ERP outbox head 与当前权威水位不一致")
    return SourceState(
        source_epoch,
        head_sequence,
        head_event_id,
        erp_revision,
        content_hash,
        row_count,
        source_batch_id,
    )


def _read_live_source_state(path: Path) -> SourceState:
    source = _open_source(path)
    try:
        _validate_source_schema(source)
        return _read_source_state(source)
    finally:
        source.rollback()
        source.close()


def _require_source_stable(path: Path, expected: SourceState) -> None:
    if _read_live_source_state(path) != expected:
        raise SourceChangedDuringSync(
            "D1 ERP revision 或 outbox head 在抽取期间变化，PG 事务已回滚"
        )


def _checkpoint_values(checkpoint: ErpReferenceSyncCheckpoint) -> tuple[object, ...]:
    return (
        checkpoint.source_epoch,
        checkpoint.source_path_digest,
        int(checkpoint.last_event_sequence),
        checkpoint.last_event_id,
        int(checkpoint.erp_revision),
        checkpoint.content_hash,
        int(checkpoint.row_count),
        checkpoint.source_batch_id,
    )


def _validate_checkpoint(checkpoint: ErpReferenceSyncCheckpoint) -> None:
    if not SOURCE_EPOCH_PATTERN.fullmatch(checkpoint.source_epoch):
        raise ErpReferenceSyncError("ERP checkpoint 的 source epoch 格式无效")
    if not SHA256_PATTERN.fullmatch(checkpoint.source_path_digest):
        raise ErpReferenceSyncError("ERP checkpoint 的源路径摘要格式无效")
    _validate_hash(checkpoint.content_hash, "ERP checkpoint 内容摘要")
    if (
        checkpoint.last_event_sequence < 0
        or checkpoint.erp_revision < 1
        or checkpoint.row_count <= 0
        or bool(checkpoint.last_event_id) != (checkpoint.last_event_sequence > 0)
    ):
        raise ErpReferenceSyncError("ERP checkpoint 水位无效")


def read_checkpoint(*, for_update: bool = False) -> ErpReferenceSyncCheckpoint | None:
    queryset = ErpReferenceSyncCheckpoint.objects
    if for_update:
        queryset = queryset.select_for_update()
    try:
        checkpoint = queryset.filter(id=1).first()
    except DatabaseError as error:
        raise ErpReferenceSyncError(
            "PG 缺少 ERP reference checkpoint；请先应用 Django migrations"
        ) from error
    if checkpoint is not None:
        _validate_checkpoint(checkpoint)
    return checkpoint


def _validate_checkpoint_anchor(
    source: sqlite3.Connection, checkpoint: ErpReferenceSyncCheckpoint
) -> None:
    if checkpoint.last_event_sequence == 0:
        return
    row = source.execute(
        "SELECT event_id, source_epoch, domain, operation, scope_json, source_batch_id, "
        "erp_revision, content_hash, row_count, canonical_format_version "
        "FROM erp_reference_projection_outbox WHERE event_sequence = ?",
        (checkpoint.last_event_sequence,),
    ).fetchone()
    if row is None:
        raise ErpReferenceSyncError("D1 ERP outbox 缺少 checkpoint 锚点事件")
    expected = (
        checkpoint.last_event_id,
        checkpoint.source_epoch,
        "erp",
        "replace_all",
        ERP_SCOPE_JSON,
        checkpoint.source_batch_id,
        checkpoint.erp_revision,
        checkpoint.content_hash,
        checkpoint.row_count,
        CANONICAL_FORMAT_VERSION,
    )
    if tuple(row) != expected:
        raise ErpReferenceSyncError("D1 ERP outbox checkpoint 锚点已变化")
    if checkpoint.last_event_id != (
        f"{checkpoint.source_epoch}:erp:{checkpoint.source_batch_id}"
    ):
        raise ErpReferenceSyncError("D1 ERP outbox checkpoint event id 无效")


def _parse_event(row: sqlite3.Row) -> OutboxEvent:
    event = OutboxEvent(
        event_sequence=int(row["event_sequence"]),
        event_id=str(row["event_id"]),
        source_epoch=str(row["source_epoch"]),
        domain=str(row["domain"]),
        operation=str(row["operation"]),
        scope_json=str(row["scope_json"]),
        source_batch_id=str(row["source_batch_id"]),
        erp_revision=int(row["erp_revision"]),
        row_count=int(row["row_count"]),
        content_hash=_validate_hash(row["content_hash"], "D1 ERP outbox 内容摘要"),
        canonical_format_version=str(row["canonical_format_version"]),
        created_at=str(row["created_at"]),
    )
    if (
        event.domain != "erp"
        or event.operation != "replace_all"
        or event.scope_json != ERP_SCOPE_JSON
        or event.canonical_format_version != CANONICAL_FORMAT_VERSION
    ):
        raise ErpReferenceSyncError("ERP bridge 只接受 products replace_all 的 ERP 事件")
    if event.event_id != f"{event.source_epoch}:erp:{event.source_batch_id}":
        raise ErpReferenceSyncError("D1 ERP outbox event id 无效")
    if event.row_count <= 0 or event.row_count > MAX_PRODUCT_ROWS:
        raise ErpReferenceSyncError("D1 ERP outbox 行数无效")
    try:
        datetime.fromisoformat(event.created_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ErpReferenceSyncError("D1 ERP outbox 创建时间无效") from error
    return event


def _read_pending_events(
    source: sqlite3.Connection,
    checkpoint: ErpReferenceSyncCheckpoint,
    state: SourceState,
    *,
    max_events: int,
) -> list[OutboxEvent]:
    rows = source.execute(
        f"SELECT {', '.join(OUTBOX_COLUMNS)} "
        "FROM erp_reference_projection_outbox WHERE event_sequence > ? "
        "AND event_sequence <= ? ORDER BY event_sequence LIMIT ?",
        (checkpoint.last_event_sequence, state.head_sequence, max_events + 1),
    ).fetchall()
    if len(rows) > max_events:
        raise ErpReferenceSyncError(
            "D1 ERP pending 事件超过单次上限；不得用当前最终快照部分推进 checkpoint"
        )
    events = [_parse_event(row) for row in rows]
    expected_sequence = checkpoint.last_event_sequence + 1
    expected_revision = checkpoint.erp_revision + 1
    for event in events:
        if event.event_sequence != expected_sequence:
            raise ErpReferenceSyncError("D1 ERP outbox 存在事件缺口或乱序")
        if event.source_epoch != checkpoint.source_epoch:
            raise ErpReferenceSyncError("D1 ERP outbox source epoch 不一致")
        if event.erp_revision != expected_revision:
            raise ErpReferenceSyncError("D1 ERP outbox revision 未按单步严格推进")
        expected_sequence += 1
        expected_revision += 1
    if events and (
        events[-1].event_sequence != state.head_sequence
        or events[-1].event_id != state.head_event_id
        or events[-1].erp_revision != state.erp_revision
        or events[-1].content_hash != state.content_hash
        or events[-1].row_count != state.row_count
        or events[-1].source_batch_id != state.source_batch_id
    ):
        raise ErpReferenceSyncError("D1 ERP outbox 事件链终点与固定 head 不一致")
    return events


def _validate_event_batches(
    source: sqlite3.Connection, events: Iterable[OutboxEvent]
) -> None:
    for event in events:
        row = source.execute(
            "SELECT source_key, status, row_count, totals_json "
            "FROM erp_reference_import_batches WHERE id = ? LIMIT 1",
            (event.source_batch_id,),
        ).fetchone()
        if row is None:
            raise ErpReferenceSyncError("D1 ERP outbox 来源批次不存在")
        if str(row[0]) != "products" or str(row[1]) != "completed":
            raise ErpReferenceSyncError("D1 ERP outbox 来源批次不是 completed products")
        if int(row[2]) != event.row_count:
            raise ErpReferenceSyncError("D1 ERP outbox 与来源批次行数不一致")
        try:
            totals = json.loads(str(row[3]))
        except (TypeError, ValueError) as error:
            raise ErpReferenceSyncError("D1 ERP 来源批次 totals_json 无效") from error
        if not isinstance(totals, dict) or totals.get("contentHash") != event.content_hash:
            raise ErpReferenceSyncError("D1 ERP outbox 与来源批次内容摘要不一致")


def _normalise_product(row: Sequence[object]) -> tuple[object, ...]:
    values: list[object] = []
    for index, column in enumerate(ERP_PRODUCT_COLUMNS):
        value = row[index]
        if column == "source_row_number":
            number = int(value)
            if number < 0:
                raise ErpReferenceSyncError("D1 ERP source row number 不能为负数")
            values.append(number)
            continue
        text = "" if value is None else str(value)
        if column == "product_code" and not text:
            raise ErpReferenceSyncError("D1 ERP 产品代码不能为空")
        if len(text.encode("utf-8")) > MAX_TEXT_FIELD_BYTES:
            raise ErpReferenceSyncError(f"D1 ERP 字段 {column} 超过允许大小")
        values.append(text)
    return tuple(values)


def _read_products(source: sqlite3.Connection, expected_count: int) -> list[tuple[object, ...]]:
    if expected_count <= 0:
        raise ErpReferenceSyncError("D1 ERP 产品全集不能为空")
    if expected_count > MAX_PRODUCT_ROWS:
        raise ErpReferenceSyncError("D1 ERP 产品行数超过同步上限")
    rows: list[tuple[object, ...]] = []
    payload_bytes = 0
    previous_code = ""
    query = f"SELECT {', '.join(ERP_PRODUCT_COLUMNS)} FROM erp_product_master ORDER BY product_code"
    for raw in source.execute(query):
        row = _normalise_product(tuple(raw))
        code = str(row[0])
        if previous_code and code <= previous_code:
            raise ErpReferenceSyncError("D1 ERP 产品代码重复或排序不稳定")
        previous_code = code
        payload_bytes += sum(
            len(str(value).encode("utf-8")) if not isinstance(value, int) else 8
            for value in row
        )
        if payload_bytes > MAX_PRODUCT_PAYLOAD_BYTES:
            raise ErpReferenceSyncError("D1 ERP 产品快照超过同步载荷上限")
        rows.append(row)
        if len(rows) > MAX_PRODUCT_ROWS:
            raise ErpReferenceSyncError("D1 ERP 产品行数超过同步上限")
    if len(rows) != expected_count:
        raise ErpReferenceSyncError("D1 ERP 产品全集行数与权威水位不一致")
    return rows


def _rows_digest(rows: Iterable[Sequence[object]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        encoded = json.dumps(
            list(row), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _read_target_products() -> list[tuple[object, ...]]:
    with target_connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(ERP_PRODUCT_COLUMNS)} "
            "FROM erp_product_master ORDER BY product_code"
        )
        return [_normalise_product(tuple(row)) for row in cursor.fetchall()]


def _lock_target_writers() -> None:
    lock_erp_reference_for_replace()


def _target_revision(*, for_update: bool) -> tuple[int, str]:
    suffix = " FOR UPDATE" if for_update and target_connection.vendor == "postgresql" else ""
    with target_connection.cursor() as cursor:
        cursor.execute(
            "SELECT revision, source_digest FROM sales_data_revisions "
            f"WHERE domain = 'erp'{suffix}"
        )
        row = cursor.fetchone()
    if row is None:
        raise ErpReferenceSyncError("PG 缺少 ERP revision 水位")
    revision = int(row[0])
    digest = str(row[1] or "")
    if revision < 1 or (digest and not SHA256_PATTERN.fullmatch(digest)):
        raise ErpReferenceSyncError("PG ERP revision 水位无效")
    return revision, digest


def _apply_products(
    products: Sequence[tuple[object, ...]], generation: str, *, batch_size: int
) -> int:
    if not products:
        raise ErpReferenceSyncError("PG ERP replace_all 拒绝空产品全集")
    old_categories: dict[str, str] = {}
    with target_connection.cursor() as cursor:
        cursor.execute("SELECT product_code, category FROM erp_product_master")
        old_categories = {
            str(code): "" if category is None else str(category).strip()
            for code, category in cursor.fetchall()
        }
        cursor.execute("DELETE FROM erp_product_master")
        columns = (*ERP_PRODUCT_COLUMNS, "migration_generation")
        placeholders = ", ".join(["%s"] * len(columns))
        sql = (
            f"INSERT INTO erp_product_master ({', '.join(columns)}) "
            f"VALUES ({placeholders})"
        )
        for offset in range(0, len(products), batch_size):
            cursor.executemany(
                sql,
                [(*row, generation) for row in products[offset : offset + batch_size]],
            )
        cursor.execute("SELECT COUNT(*) FROM erp_product_master")
        target_count = int(cursor.fetchone()[0])
    if target_count != len(products):
        raise ErpReferenceSyncError("PG ERP replace_all 行数回查失败")

    new_categories = {str(row[0]): str(row[5]).strip() for row in products}
    changed_codes = sorted(
        code
        for code in set(old_categories) | set(new_categories)
        if old_categories.get(code, "") != new_categories.get(code, "")
    )
    updated = 0
    with target_connection.cursor() as cursor:
        for offset in range(0, len(changed_codes), 500):
            codes = changed_codes[offset : offset + 500]
            placeholders = ", ".join(["%s"] * len(codes))
            cursor.execute(
                "UPDATE sales_order_lines AS line SET resolved_category = "
                "COALESCE((SELECT NULLIF(TRIM(product.category), '') "
                "FROM erp_product_master AS product "
                "WHERE product.product_code = line.product_code), "
                "NULLIF(TRIM(line.category), ''), %s) "
                f"WHERE line.product_code IN ({placeholders})",
                ["未分类", *codes],
            )
            updated += int(cursor.rowcount)
    return updated


def _publish_checkpoint(
    checkpoint: ErpReferenceSyncCheckpoint,
    state: SourceState,
) -> None:
    with target_connection.cursor() as cursor:
        cursor.execute(
            "UPDATE sales_data_revisions SET revision = %s, source_digest = %s, "
            "updated_at = CURRENT_TIMESTAMP WHERE domain = 'erp' AND revision = %s "
            "AND source_digest = %s",
            [
                state.erp_revision,
                state.content_hash,
                checkpoint.erp_revision,
                checkpoint.content_hash,
            ],
        )
        if cursor.rowcount != 1:
            raise ErpReferenceSyncError("PG ERP revision CAS 发布失败")
    checkpoint.last_event_sequence = state.head_sequence
    checkpoint.last_event_id = state.head_event_id
    checkpoint.erp_revision = state.erp_revision
    checkpoint.content_hash = state.content_hash
    checkpoint.row_count = state.row_count
    checkpoint.source_batch_id = state.source_batch_id
    now = timezone.now()
    checkpoint.updated_at = now
    checkpoint.last_checked_at = now
    checkpoint.save(
        update_fields=[
            "last_event_sequence",
            "last_event_id",
            "erp_revision",
            "content_hash",
            "row_count",
            "source_batch_id",
            "updated_at",
            "last_checked_at",
        ]
    )


def _touch_checkpoint(checkpoint: ErpReferenceSyncCheckpoint) -> None:
    now = timezone.now()
    checkpoint.updated_at = now
    checkpoint.last_checked_at = now
    checkpoint.save(update_fields=["updated_at", "last_checked_at"])


def inspect_sync_status(
    path: Path,
    *,
    max_age_seconds: float = 60.0,
) -> dict[str, object]:
    """Read and compare the source head, PG revision and durable checkpoint."""

    if (
        not isinstance(max_age_seconds, (int, float))
        or not 1 <= float(max_age_seconds) <= 3600
    ):
        raise ErpReferenceSyncError("max_age_seconds 必须在 1 到 3600 之间")
    path = resolve_source_path(path)
    path_digest = source_path_digest(path)
    source = _open_source(path)
    try:
        _validate_source_schema(source)
        state = _read_source_state(source)
        with transaction.atomic():
            checkpoint = read_checkpoint()
            if checkpoint is None:
                raise ErpReferenceSyncError(
                    "ERP checkpoint 尚未初始化；先运行 --initialize-checkpoint"
                )
            if checkpoint.source_path_digest != path_digest:
                raise ErpReferenceSyncError("--source 与 ERP checkpoint 绑定路径不一致")
            if checkpoint.source_epoch != state.source_epoch:
                raise ErpReferenceSyncError("D1 ERP source epoch 已变化，必须受控重建")
            _validate_checkpoint_anchor(source, checkpoint)
            target_revision, target_digest = _target_revision(for_update=False)
            if (
                target_revision != checkpoint.erp_revision
                or target_digest != checkpoint.content_hash
            ):
                raise ErpReferenceSyncError("PG ERP revision 与 checkpoint 不一致")
            if (
                checkpoint.last_event_sequence != state.head_sequence
                or checkpoint.last_event_id != state.head_event_id
                or checkpoint.erp_revision != state.erp_revision
                or checkpoint.content_hash != state.content_hash
                or checkpoint.row_count != state.row_count
                or checkpoint.source_batch_id != state.source_batch_id
            ):
                raise ErpReferenceSyncError("ERP checkpoint 尚未追平 D1 ERP head")
            with target_connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM erp_product_master")
                if int(cursor.fetchone()[0]) != checkpoint.row_count:
                    raise ErpReferenceSyncError("PG ERP 产品行数偏离 checkpoint")
            checked_at = checkpoint.last_checked_at
            if checked_at is None:
                raise ErpReferenceSyncError("ERP checkpoint 缺少心跳时间")
            age_seconds = (timezone.now() - checked_at).total_seconds()
            if age_seconds < -5 or age_seconds > float(max_age_seconds):
                raise ErpReferenceSyncError("ERP checkpoint 心跳已过期")
            _require_source_stable(path, state)
        return {
            "status": "caught_up",
            "sourceEpoch": state.source_epoch,
            "headSequence": state.head_sequence,
            "headEventId": state.head_event_id,
            "erpRevision": state.erp_revision,
            "rowCount": state.row_count,
            "contentHash": state.content_hash,
            "sourceBatchId": state.source_batch_id,
            "lastCheckedAt": checked_at.isoformat(),
            "ageSeconds": max(0.0, age_seconds),
        }
    finally:
        source.rollback()
        source.close()


def initialize_checkpoint(path: Path) -> dict[str, object]:
    path = resolve_source_path(path)
    source = _open_source(path)
    try:
        _validate_source_schema(source)
        state = _read_source_state(source)
        products = _read_products(source, state.row_count)
        source_digest = _rows_digest(products)
        if state.source_batch_id:
            synthetic = OutboxEvent(
                event_sequence=state.head_sequence,
                event_id=state.head_event_id,
                source_epoch=state.source_epoch,
                domain="erp",
                operation="replace_all",
                scope_json=ERP_SCOPE_JSON,
                source_batch_id=state.source_batch_id,
                erp_revision=state.erp_revision,
                row_count=state.row_count,
                content_hash=state.content_hash,
                canonical_format_version=CANONICAL_FORMAT_VERSION,
                created_at="1970-01-01T00:00:00+00:00",
            )
            _validate_event_batches(source, [synthetic])
        elif state.row_count:
            raise ErpReferenceSyncError("非空 D1 ERP 基线缺少 completed products 来源批次")
        _require_source_stable(path, state)
        with transaction.atomic():
            _lock_target_writers()
            if read_checkpoint(for_update=True) is not None:
                raise ErpReferenceSyncError("ERP checkpoint 已初始化，不得重新绑定源")
            target_revision, _target_content_hash = _target_revision(for_update=True)
            if target_revision != state.erp_revision:
                raise ErpReferenceSyncError("PG 与 D1 ERP revision 不一致，拒绝初始化 checkpoint")
            target_products = _read_target_products()
            if len(target_products) != state.row_count or _rows_digest(target_products) != source_digest:
                raise ErpReferenceSyncError("PG 与 D1 ERP 产品全集不一致，拒绝初始化 checkpoint")
            _require_source_stable(path, state)
            with target_connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE sales_data_revisions SET source_digest = %s, "
                    "updated_at = CURRENT_TIMESTAMP WHERE domain = 'erp' AND revision = %s",
                    [state.content_hash, state.erp_revision],
                )
                if cursor.rowcount != 1:
                    raise ErpReferenceSyncError("PG ERP revision 初始化 CAS 失败")
            checkpoint = ErpReferenceSyncCheckpoint.objects.create(
                id=1,
                source_epoch=state.source_epoch,
                source_path_digest=source_path_digest(path),
                last_event_sequence=state.head_sequence,
                last_event_id=state.head_event_id,
                erp_revision=state.erp_revision,
                content_hash=state.content_hash,
                row_count=state.row_count,
                source_batch_id=state.source_batch_id,
            )
            _validate_checkpoint(checkpoint)
        return {
            "status": "initialized",
            "sourceEpoch": state.source_epoch,
            "headSequence": state.head_sequence,
            "headEventId": state.head_event_id,
            "erpRevision": state.erp_revision,
            "rowCount": state.row_count,
            "contentHash": state.content_hash,
            "sourceBatchId": state.source_batch_id,
        }
    finally:
        source.rollback()
        source.close()


def sync_reference_once(
    path: Path,
    *,
    max_events: int = 1000,
    batch_size: int = 1000,
) -> dict[str, object]:
    path = resolve_source_path(path)
    if max_events < 1 or max_events > 10_000:
        raise ErpReferenceSyncError("max_events 必须在 1 到 10000 之间")
    if batch_size < 100 or batch_size > 10_000:
        raise ErpReferenceSyncError("batch_size 必须在 100 到 10000 之间")
    path_digest = source_path_digest(path)
    source = _open_source(path)
    try:
        _validate_source_schema(source)
        state = _read_source_state(source)
        _require_source_stable(path, state)
        with transaction.atomic():
            _lock_target_writers()
            checkpoint = read_checkpoint(for_update=True)
            if checkpoint is None:
                raise ErpReferenceSyncError(
                    "ERP checkpoint 尚未初始化；先运行 --initialize-checkpoint"
                )
            if checkpoint.source_path_digest != path_digest:
                raise ErpReferenceSyncError("--source 与 ERP checkpoint 绑定路径不一致")
            if checkpoint.source_epoch != state.source_epoch:
                raise ErpReferenceSyncError("D1 ERP source epoch 已变化，必须受控重建")
            if checkpoint.last_event_sequence > state.head_sequence:
                raise ErpReferenceSyncError("ERP checkpoint sequence 超过 D1 outbox head")
            _validate_checkpoint_anchor(source, checkpoint)
            target_revision, target_digest = _target_revision(for_update=True)
            if (
                target_revision != checkpoint.erp_revision
                or target_digest != checkpoint.content_hash
            ):
                raise ErpReferenceSyncError("PG ERP revision 与 checkpoint 不一致")
            events = _read_pending_events(
                source, checkpoint, state, max_events=max_events
            )
            if not events:
                if checkpoint.last_event_sequence != state.head_sequence:
                    raise ErpReferenceSyncError("D1 ERP outbox pending 事件不可读取")
                if (
                    checkpoint.erp_revision != state.erp_revision
                    or checkpoint.content_hash != state.content_hash
                    or checkpoint.row_count != state.row_count
                    or checkpoint.source_batch_id != state.source_batch_id
                ):
                    raise ErpReferenceSyncError("D1 ERP 水位已推进但缺少对应 outbox 事件")
                with target_connection.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM erp_product_master")
                    if int(cursor.fetchone()[0]) != checkpoint.row_count:
                        raise ErpReferenceSyncError("PG ERP 产品行数偏离 checkpoint")
                _require_source_stable(path, state)
                _touch_checkpoint(checkpoint)
                return {
                    "status": "up_to_date",
                    "sourceEpoch": state.source_epoch,
                    "headSequence": state.head_sequence,
                    "headEventId": state.head_event_id,
                    "erpRevision": state.erp_revision,
                    "rowCount": state.row_count,
                    "contentHash": state.content_hash,
                    "sourceBatchId": state.source_batch_id,
                    "eventCount": 0,
                }

            _validate_event_batches(source, events)
            products = _read_products(source, state.row_count)
            source_rows_digest = _rows_digest(products)
            generation = f"erp-reference-sync-{state.head_sequence}"
            category_rows = _apply_products(products, generation, batch_size=batch_size)
            target_products = _read_target_products()
            if (
                len(target_products) != state.row_count
                or _rows_digest(target_products) != source_rows_digest
            ):
                raise ErpReferenceSyncError("PG ERP 产品全集内容回查失败")
            _require_source_stable(path, state)
            _publish_checkpoint(checkpoint, state)
        return {
            "status": "synchronized",
            "sourceEpoch": state.source_epoch,
            "headSequence": state.head_sequence,
            "headEventId": state.head_event_id,
            "erpRevision": state.erp_revision,
            "rowCount": state.row_count,
            "contentHash": state.content_hash,
            "sourceBatchId": state.source_batch_id,
            "eventCount": len(events),
            "categoryRowsRecomputed": category_rows,
        }
    finally:
        source.rollback()
        source.close()


T = TypeVar("T")


def retry_source_changes(
    action: Callable[[], T],
    *,
    attempts: int = 3,
    delay_seconds: float = 0.1,
) -> T:
    if attempts < 1 or attempts > 10:
        raise ErpReferenceSyncError("source change retry 次数必须在 1 到 10 之间")
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except SourceChangedDuringSync:
            if attempt == attempts:
                raise
            if delay_seconds > 0:
                time.sleep(delay_seconds)
    raise AssertionError("unreachable")
