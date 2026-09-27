from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from datetime import timezone as datetime_timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from netshop.errors import NetshopApiError
from netshop.import_service import _row_projection, _validate_image_metadata
from netshop.models import (
    NetshopAssetUpload,
    NetshopAssetUploadChunk,
    NetshopAssetUploadResult,
    NetshopDataRevision,
    NetshopImportAttempt,
    NetshopImportBatch,
    NetshopImportFingerprint,
    NetshopImportScopeHead,
    NetshopMigrationRun,
    NetshopProductDailyRevision,
    NetshopProductDailyScopeRevision,
    NetshopPromotionAggregateControl,
    NetshopPromotionAggregateManifest,
    NetshopPromotionAggregateState,
    NetshopPromotionProductDaily,
    NetshopPromotionScopeRevision,
    NetshopPromotionShopDaily,
    NetshopRow,
    NetshopWriteAuthority,
)


FORMAT_VERSION = "netshop-d1-migration-v1"
MAX_ROWS = 2_000_000
ZERO_DIGEST = "0" * 64
HEX = frozenset("0123456789abcdef")
UINT256_MODULUS = 1 << 256

BATCH_COLUMNS = (
    "id", "source", "dataset", "platform", "shop_name", "file_name",
    "file_size_bytes", "file_hash", "sheet_name", "status", "row_count",
    "inserted_count", "duplicate_count", "warning_count", "date_min", "date_max",
    "snapshot_date", "warnings_json", "totals_json", "note", "created_at", "completed_at",
)
ROW_COLUMNS = (
    "source_row_key", "source_row_hash", "first_import_batch_id", "last_import_batch_id",
    "source_row_number", "source", "dataset", "platform", "shop_name", "business_date",
    "snapshot_date", "product_code", "product_name", "sku_id", "spu_id", "warehouse_type",
    "metrics_json", "raw_json", "created_at", "updated_at",
)
ROW_PROJECTION_COLUMNS = tuple(
    _row_projection({"metrics": {}, "raw": {}}).keys()
)
PROMOTION_PRODUCT_COLUMNS = (
    "platform", "shop_name", "business_date", "product_id", "source", "product_name",
    "product_line", "spend_cents", "net_transaction_amount_cents",
    "gross_transaction_amount_cents", "impressions", "clicks", "net_orders", "favorites",
    "cart_quantity", "source_row_count", "source_batch_id", "source_batch_count", "rebuilt_at",
)
PROMOTION_SHOP_COLUMNS = (
    "platform", "shop_name", "business_date", "source", "product_count", "spend_cents",
    "net_transaction_amount_cents", "gross_transaction_amount_cents", "impressions", "clicks",
    "net_orders", "favorites", "cart_quantity", "source_row_count", "source_batch_id",
    "source_batch_count", "rebuilt_at",
)
PROMOTION_STATE_COLUMNS = (
    "platform", "shop_name", "business_date", "source", "ready", "raw_row_count",
    "product_row_count", "source_batch_id", "source_batch_count", "rebuilt_at", "invalidated_at",
)
PROMOTION_MANIFEST_COLUMNS = (
    "platform", "ready", "historical_data_cutoff", "source_shop_count", "raw_row_count",
    "product_row_count", "shop_day_count", "state_day_count", "completed_at", "invalidated_at",
    "data_version",
)
PROMOTION_CONTROL_COLUMNS = (
    "platform", "bootstrap_batch_id", "bootstrap_raw_row_count", "bootstrap_product_row_count",
    "bootstrap_shop_day_count", "bootstrap_data_cutoff", "maintenance_token",
    "maintenance_version", "maintenance_previous_ready", "maintenance_started_at", "updated_at",
)

REQUIRED_TABLES = {
    "netshop_import_batches": set(BATCH_COLUMNS),
    "netshop_rows": set(ROW_COLUMNS),
    "netshop_product_daily_revisions": {"platform", "data_version"},
    "netshop_product_daily_scope_revisions": {"platform", "shop_name", "data_version"},
    "netshop_promotion_product_daily": set(PROMOTION_PRODUCT_COLUMNS),
    "netshop_promotion_shop_daily": set(PROMOTION_SHOP_COLUMNS),
    "netshop_promotion_aggregate_state": set(PROMOTION_STATE_COLUMNS),
    "netshop_promotion_aggregate_manifest": set(PROMOTION_MANIFEST_COLUMNS),
    "netshop_promotion_aggregate_control": set(PROMOTION_CONTROL_COLUMNS),
    "netshop_promotion_scope_revisions": {"platform", "shop_name", "data_version"},
    "netshop_asset_uploads": {
        "id", "fingerprint", "shop_name", "snapshot_date", "file_name", "file_size_bytes",
        "chunk_size_bytes", "chunk_count", "received_chunk_count", "received_bytes", "status",
        "processing_owner", "expires_at", "created_at", "updated_at",
    },
    "netshop_asset_upload_chunks": {
        "upload_id", "chunk_index", "object_key", "size_bytes", "sha256", "created_at",
    },
    "netshop_asset_upload_results": {"upload_id", "result_json", "created_at"},
    "import_content_fingerprints": {
        "domain", "batch_id", "scope_key", "scope_json", "import_hash", "raw_file_hash",
        "content_hash", "row_count", "status", "publication_sequence", "created_at",
    },
    "import_content_attempts": {
        "attempt_id", "domain", "batch_id", "scope_key", "scope_json", "import_hash",
        "raw_file_hash", "content_hash", "row_count", "file_name", "file_size_bytes", "actor",
        "warnings_json", "outcome", "error_code", "recovered_from_attempt_id", "created_at", "updated_at",
    },
    "import_scope_heads": {
        "domain", "scope_key", "state_token", "status", "owner_token", "current_batch_id",
        "generation", "updated_at",
    },
    "netshop_write_authority": {"id", "owner", "epoch", "cutover_id", "updated_at"},
}


def _canonical(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_hash(value: object) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and set(text) <= HEX


def _json(value: object, expected: type[list] | type[dict]) -> list | dict:
    if isinstance(value, expected):
        return value
    try:
        parsed = json.loads(str(value or "[]" if expected is list else value or "{}"))
    except json.JSONDecodeError as error:
        raise CommandError("D1 网店迁移材料包含无效 JSON。") from error
    if not isinstance(parsed, expected):
        raise CommandError("D1 网店迁移材料 JSON 类型不符合契约。")
    return parsed


def _datetime(value: object):
    parsed = parse_datetime(str(value or ""))
    if parsed is None:
        raise CommandError("D1 网店迁移材料包含无效时间。")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, datetime_timezone.utc)
    return parsed


def _open_source(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    source = sqlite3.connect(uri, uri=True, timeout=30)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    source.execute("BEGIN")
    return source


def _columns(source: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in source.execute(f'PRAGMA table_info("{table}")')}


def _validate_source(
    source: sqlite3.Connection,
    *,
    apply: bool,
    allowed_authority_owners: set[str] | frozenset[str] | None = None,
) -> dict[str, object]:
    quick_check = [str(row[0]) for row in source.execute("PRAGMA quick_check")]
    if quick_check != ["ok"]:
        raise CommandError("D1 网店封存快照未通过 SQLite quick_check。")
    present = {str(row[0]) for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, expected in REQUIRED_TABLES.items():
        if table not in present:
            raise CommandError(f"D1 源缺少必需网店表 {table}。")
        missing = expected - _columns(source, table)
        if missing:
            raise CommandError(f"D1 源表 {table} 缺少字段：{', '.join(sorted(missing))}。")
    authority = source.execute(
        "SELECT id,owner,epoch,cutover_id,updated_at FROM netshop_write_authority WHERE id=1"
    ).fetchone()
    allowed = (
        set(allowed_authority_owners)
        if allowed_authority_owners is not None
        else {"pending"}
        if apply
        else {"d1", "pending"}
    )
    if authority is None or str(authority["owner"]) not in allowed or int(authority["epoch"]) < 1:
        raise CommandError("D1 网店写 authority 不在本操作允许状态。")
    if int(source.execute("SELECT COUNT(*) FROM netshop_import_batches WHERE status='processing'").fetchone()[0]):
        raise CommandError("D1 存在处理中网店批次，拒绝迁移。")
    if int(source.execute("SELECT COUNT(*) FROM import_scope_heads WHERE domain='netshop' AND (status<>'ready' OR COALESCE(owner_token,'')<>'')").fetchone()[0]):
        raise CommandError("D1 网店导入 scope head 不是静默终态。")
    if int(source.execute("SELECT COUNT(*) FROM import_content_attempts WHERE domain='netshop' AND outcome='processing'").fetchone()[0]):
        raise CommandError("D1 存在处理中网店导入尝试。")
    if int(source.execute("SELECT COUNT(*) FROM netshop_asset_uploads WHERE status<>'completed'").fetchone()[0]):
        raise CommandError("D1 存在未完成商品图上传会话。")
    row_count = int(source.execute("SELECT COUNT(*) FROM netshop_rows").fetchone()[0])
    if row_count <= 0 or row_count > MAX_ROWS:
        raise CommandError("D1 网店事实为空或超过受控迁移上限。")
    orphan_rows = int(source.execute(
        "SELECT COUNT(*) FROM netshop_rows r LEFT JOIN netshop_import_batches b "
        "ON b.id=r.last_import_batch_id WHERE b.id IS NULL OR b.status<>'completed'"
    ).fetchone()[0])
    if orphan_rows:
        raise CommandError("D1 网店事实与完成批次的所有权链不完整。")
    orphan_first_rows = int(source.execute(
        "SELECT COUNT(*) FROM netshop_rows r LEFT JOIN netshop_import_batches b "
        "ON b.id=r.first_import_batch_id WHERE b.id IS NULL OR b.status<>'completed'"
    ).fetchone()[0])
    if orphan_first_rows:
        raise CommandError("D1 网店事实的首次导入批次所有权链不完整。")
    return dict(authority)


def _rows(source: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()) -> Iterator[dict[str, object]]:
    cursor = source.execute(sql, parameters)
    while True:
        page = cursor.fetchmany(1_000)
        if not page:
            return
        for row in page:
            yield dict(row)


def _select(columns: Iterable[str], table: str, order: str, where: str = "") -> str:
    return f"SELECT {','.join(columns)} FROM {table} {where} ORDER BY {order}"


def _source_row_projections(
    source: sqlite3.Connection,
) -> Iterator[dict[str, object]]:
    sql = "SELECT source_row_key,metrics_json,raw_json FROM netshop_rows ORDER BY source_row_key COLLATE BINARY"
    for row in source.execute(sql):
        projection = _row_projection(
            {
                "metrics": _json(row["metrics_json"], dict),
                "raw": _json(row["raw_json"], dict),
            }
        )
        try:
            _validate_image_metadata(projection)
        except NetshopApiError as error:
            raise CommandError(
                f"D1 网店行 {row['source_row_key']} 的图片元数据不符合内容寻址契约。"
            ) from error
        yield {"source_row_key": row["source_row_key"], **projection}


def _source_sections(source: sqlite3.Connection) -> dict[str, Iterable[dict[str, object]]]:
    sections: dict[str, Iterable[dict[str, object]]] = {
        "batches": _rows(source, _select(BATCH_COLUMNS, "netshop_import_batches", "id COLLATE BINARY")),
        "rows": _rows(source, _select(ROW_COLUMNS, "netshop_rows", "source_row_key COLLATE BINARY")),
        "row_projections": _source_row_projections(source),
        "promotion_products": _rows(source, _select(PROMOTION_PRODUCT_COLUMNS, "netshop_promotion_product_daily", "platform,shop_name,business_date,product_id")),
        "promotion_shops": _rows(source, _select(PROMOTION_SHOP_COLUMNS, "netshop_promotion_shop_daily", "platform,shop_name,business_date")),
        "promotion_states": _rows(source, _select(PROMOTION_STATE_COLUMNS, "netshop_promotion_aggregate_state", "platform,shop_name,business_date")),
        "promotion_manifests": _rows(source, _select(PROMOTION_MANIFEST_COLUMNS, "netshop_promotion_aggregate_manifest", "platform")),
        "promotion_controls": _rows(source, _select(PROMOTION_CONTROL_COLUMNS, "netshop_promotion_aggregate_control", "platform")),
        "product_revisions": _rows(source, "SELECT platform,data_version FROM netshop_product_daily_revisions ORDER BY platform"),
        "product_scope_revisions": _rows(source, "SELECT platform,shop_name,data_version FROM netshop_product_daily_scope_revisions ORDER BY platform,shop_name"),
        "promotion_scope_revisions": _rows(source, "SELECT platform,shop_name,data_version FROM netshop_promotion_scope_revisions ORDER BY platform,shop_name"),
        "fingerprints": _rows(source, "SELECT batch_id,scope_key,scope_json,import_hash,raw_file_hash,content_hash,row_count,status,publication_sequence FROM import_content_fingerprints WHERE domain='netshop' ORDER BY batch_id"),
        "attempts": _rows(source, "SELECT attempt_id,batch_id,scope_key,raw_file_hash,content_hash,outcome,error_code FROM import_content_attempts WHERE domain='netshop' ORDER BY attempt_id"),
        "heads": _rows(source, "SELECT scope_key,state_token,status,COALESCE(owner_token,'') owner_token,COALESCE(current_batch_id,'') current_batch_id,generation FROM import_scope_heads WHERE domain='netshop' ORDER BY scope_key"),
        "uploads": _rows(source, "SELECT id,fingerprint,shop_name,snapshot_date,file_name,file_size_bytes,chunk_size_bytes,chunk_count,received_chunk_count,received_bytes,status,COALESCE(processing_owner,'') processing_owner FROM netshop_asset_uploads ORDER BY id"),
        "upload_chunks": _rows(source, "SELECT upload_id,chunk_index,object_key,size_bytes,sha256 FROM netshop_asset_upload_chunks ORDER BY upload_id,chunk_index"),
        "upload_results": _rows(source, "SELECT upload_id,result_json FROM netshop_asset_upload_results ORDER BY upload_id"),
    }
    return sections


JSON_COLUMNS = {
    "warnings_json": list,
    "totals_json": dict,
    "metrics_json": dict,
    "raw_json": dict,
    "scope_json": dict,
    "result_json": dict,
}
BOOLEAN_COLUMNS = {"ready", "maintenance_previous_ready"}


def _semantic_record(record: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in record.items():
        if key in JSON_COLUMNS:
            result[key] = _json(value, JSON_COLUMNS[key])
        elif key in BOOLEAN_COLUMNS:
            result[key] = bool(value)
        else:
            result[key] = value
    return result


def _snapshot(sections: dict[str, Iterable[dict[str, object]]]) -> tuple[dict[str, int], dict[str, str], str]:
    counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    for name in sorted(sections):
        count = 0
        xor_value = 0
        sum_value = 0
        for record in sections[name]:
            record_digest = hashlib.sha256(
                _canonical_bytes(_semantic_record(record))
            ).digest()
            integer = int.from_bytes(record_digest, "big")
            xor_value ^= integer
            sum_value = (sum_value + integer) % UINT256_MODULUS
            count += 1
        counts[name] = count
        # Count + XOR + modular sum is an order-independent multiset receipt.
        # It preserves multiplicity while avoiding SQLite/PostgreSQL collation drift.
        digests[name] = hashlib.sha256(
            _canonical_bytes(
                {
                    "algorithm": "sha256-multiset-xor-sum-v1",
                    "count": count,
                    "xor": f"{xor_value:064x}",
                    "sum": f"{sum_value:064x}",
                }
            )
        ).hexdigest()
    combined = hashlib.sha256()
    for name in sorted(digests):
        combined.update(_canonical_bytes({"section": name, "count": counts[name], "digest": digests[name]}))
    return counts, digests, combined.hexdigest()


def _target_sections() -> dict[str, Iterable[dict[str, object]]]:
    def values(model, fields: tuple[str, ...], order: tuple[str, ...]):
        return model.objects.order_by(*order).values(*fields).iterator(chunk_size=1_000)

    return {
        "batches": values(NetshopImportBatch, BATCH_COLUMNS, ("id",)),
        "rows": values(NetshopRow, ROW_COLUMNS, ("source_row_key",)),
        "row_projections": values(
            NetshopRow,
            ("source_row_key", *ROW_PROJECTION_COLUMNS),
            ("source_row_key",),
        ),
        "promotion_products": values(NetshopPromotionProductDaily, PROMOTION_PRODUCT_COLUMNS, ("platform", "shop_name", "business_date", "product_id")),
        "promotion_shops": values(NetshopPromotionShopDaily, PROMOTION_SHOP_COLUMNS, ("platform", "shop_name", "business_date")),
        "promotion_states": values(NetshopPromotionAggregateState, PROMOTION_STATE_COLUMNS, ("platform", "shop_name", "business_date")),
        "promotion_manifests": values(NetshopPromotionAggregateManifest, PROMOTION_MANIFEST_COLUMNS, ("platform",)),
        "promotion_controls": values(NetshopPromotionAggregateControl, PROMOTION_CONTROL_COLUMNS, ("platform",)),
        "product_revisions": values(NetshopProductDailyRevision, ("platform", "data_version"), ("platform",)),
        "product_scope_revisions": values(NetshopProductDailyScopeRevision, ("platform", "shop_name", "data_version"), ("platform", "shop_name")),
        "promotion_scope_revisions": values(NetshopPromotionScopeRevision, ("platform", "shop_name", "data_version"), ("platform", "shop_name")),
        "fingerprints": NetshopImportFingerprint.objects.exclude(
            status="migration_synthesized"
        ).order_by("batch_id").values(
            "batch_id", "scope_key", "scope_json", "import_hash", "raw_file_hash",
            "content_hash", "row_count", "status", "publication_sequence",
        ).iterator(chunk_size=1_000),
        "attempts": ({"attempt_id": str(row.id), "batch_id": row.batch_id, "scope_key": row.scope_key, "raw_file_hash": row.raw_file_hash, "content_hash": row.content_hash, "outcome": row.outcome, "error_code": row.error_code} for row in NetshopImportAttempt.objects.order_by("id").iterator(chunk_size=1_000)),
        "heads": values(NetshopImportScopeHead, ("scope_key", "state_token", "status", "owner_token", "current_batch_id", "generation"), ("scope_key",)),
        "uploads": values(NetshopAssetUpload, ("id", "fingerprint", "shop_name", "snapshot_date", "file_name", "file_size_bytes", "chunk_size_bytes", "chunk_count", "received_chunk_count", "received_bytes", "status", "processing_owner"), ("id",)),
        "upload_chunks": values(NetshopAssetUploadChunk, ("upload_id", "chunk_index", "object_key", "size_bytes", "sha256"), ("upload_id", "chunk_index")),
        "upload_results": values(NetshopAssetUploadResult, ("upload_id", "result_json"), ("upload_id",)),
    }


def _source_fingerprints(source: sqlite3.Connection) -> dict[str, dict[str, object]]:
    return {str(row["batch_id"]): dict(row) for row in source.execute(
        "SELECT * FROM import_content_fingerprints WHERE domain='netshop' ORDER BY batch_id"
    )}


def _source_heads(source: sqlite3.Connection) -> tuple[dict[str, dict[str, object]], dict[str, str]]:
    heads = {str(row["scope_key"]): dict(row) for row in source.execute(
        "SELECT * FROM import_scope_heads WHERE domain='netshop' ORDER BY scope_key"
    )}
    by_batch = {str(row["current_batch_id"] or ""): str(row["state_token"]) for row in heads.values() if row["current_batch_id"]}
    return heads, by_batch


def _scope(source: sqlite3.Row) -> dict[str, object]:
    snapshot = str(source["snapshot_date"] or "")
    return {
        "source": source["source"], "dataset": source["dataset"],
        "platform": source["platform"], "shopName": source["shop_name"],
        "snapshotDate": snapshot or None,
        "startDate": None if snapshot else source["date_min"],
        "endDate": None if snapshot else source["date_max"],
    }


def _lock_scope_key(batch: sqlite3.Row) -> str:
    lock = {"dataset": batch["dataset"], "platform": batch["platform"], "shopName": batch["shop_name"], "source": batch["source"]}
    canonical = json.dumps(lock, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    encoded = f"{len('netshop'.encode())}:netshop{len(canonical.encode())}:{canonical}"
    return _hash_text("import-lock-scope-v1\n" + encoded)


def _legacy_content(batch: sqlite3.Row) -> str:
    return _hash_text("netshop-legacy-migration-v1\n" + json.dumps(
        {key: batch[key] for key in BATCH_COLUMNS}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ))


def _bulk(model, objects: list[object]) -> None:
    if objects:
        model.objects.bulk_create(objects, batch_size=1_000)
        objects.clear()


def _migrate(source: sqlite3.Connection, generation: str, source_digest: str, run_id: str) -> dict[str, int]:
    fingerprints = _source_fingerprints(source)
    heads, state_by_batch = _source_heads(source)
    actor_by_batch = {str(row["batch_id"]): str(row["actor"] or "") for row in source.execute(
        "SELECT batch_id,actor FROM import_content_attempts WHERE domain='netshop' ORDER BY sequence"
    )}
    batches: list[NetshopImportBatch] = []
    synthesized: dict[str, dict[str, object]] = {}
    for batch in source.execute(_select(BATCH_COLUMNS, "netshop_import_batches", "id COLLATE BINARY")):
        fingerprint = fingerprints.get(str(batch["id"]))
        scope_json = _json(fingerprint["scope_json"], dict) if fingerprint else _scope(batch)
        scope_key = str(fingerprint["scope_key"]) if fingerprint else _lock_scope_key(batch)
        content_hash = str(fingerprint["content_hash"]) if fingerprint else _legacy_content(batch)
        raw_hash = str(fingerprint["raw_file_hash"]) if fingerprint else str(batch["file_hash"])
        if not _valid_hash(raw_hash):
            raw_hash = _hash_text(f"netshop-legacy-raw-v1\n{batch['id']}\n{raw_hash}")
        published = state_by_batch.get(str(batch["id"])) or _hash_text(f"netshop-migration-state-v1\n{batch['id']}\n{content_hash}")
        if not fingerprint:
            synthesized[str(batch["id"])] = {
                "scope_key": scope_key, "scope_json": scope_json, "content_hash": content_hash,
                "raw_file_hash": raw_hash, "published_state_token": published,
            }
        batches.append(NetshopImportBatch(
            **{key: (_json(batch[key], JSON_COLUMNS[key]) if key in JSON_COLUMNS else batch[key]) for key in BATCH_COLUMNS},
            raw_file_hash=raw_hash, content_hash=content_hash, scope_key=scope_key,
            published_state_token=published, actor_email=actor_by_batch.get(str(batch["id"]), ""),
            migration_generation=generation,
        ))
    _bulk(NetshopImportBatch, batches)

    row_models: list[NetshopRow] = []
    for row in source.execute(_select(ROW_COLUMNS, "netshop_rows", "source_row_key COLLATE BINARY")):
        metrics = _json(row["metrics_json"], dict)
        raw = _json(row["raw_json"], dict)
        projection = _row_projection({"metrics": metrics, "raw": raw})
        try:
            _validate_image_metadata(projection)
        except NetshopApiError as error:
            raise CommandError(
                f"D1 网店行 {row['source_row_key']} 的图片元数据不符合内容寻址契约。"
            ) from error
        values = {key: row[key] for key in ROW_COLUMNS if key not in {"metrics_json", "raw_json"}}
        row_models.append(NetshopRow(
            **values, metrics_json=metrics, raw_json=raw, **projection,
            migration_generation=generation,
        ))
        if len(row_models) >= 1_000:
            _bulk(NetshopRow, row_models)
    _bulk(NetshopRow, row_models)

    def copy_simple(model, table: str, columns: tuple[str, ...], booleans: set[str] = set()):
        objects: list[object] = []
        for row in source.execute(_select(columns, table, ",".join(columns[:4]))):
            values = {key: (bool(row[key]) if key in booleans else row[key]) for key in columns}
            objects.append(model(**values))
            if len(objects) >= 1_000:
                _bulk(model, objects)
        _bulk(model, objects)

    copy_simple(NetshopPromotionProductDaily, "netshop_promotion_product_daily", PROMOTION_PRODUCT_COLUMNS)
    copy_simple(NetshopPromotionShopDaily, "netshop_promotion_shop_daily", PROMOTION_SHOP_COLUMNS)
    copy_simple(NetshopPromotionAggregateState, "netshop_promotion_aggregate_state", PROMOTION_STATE_COLUMNS, {"ready"})
    copy_simple(NetshopPromotionAggregateManifest, "netshop_promotion_aggregate_manifest", PROMOTION_MANIFEST_COLUMNS, {"ready"})
    copy_simple(NetshopPromotionAggregateControl, "netshop_promotion_aggregate_control", PROMOTION_CONTROL_COLUMNS, {"maintenance_previous_ready"})
    copy_simple(NetshopProductDailyRevision, "netshop_product_daily_revisions", ("platform", "data_version"))
    copy_simple(NetshopProductDailyScopeRevision, "netshop_product_daily_scope_revisions", ("platform", "shop_name", "data_version"))
    copy_simple(NetshopPromotionScopeRevision, "netshop_promotion_scope_revisions", ("platform", "shop_name", "data_version"))

    fingerprint_models: list[NetshopImportFingerprint] = []
    for row in source.execute("SELECT * FROM import_content_fingerprints WHERE domain='netshop' ORDER BY batch_id"):
        fingerprint_models.append(NetshopImportFingerprint(
            batch_id=row["batch_id"], scope_key=row["scope_key"], scope_json=_json(row["scope_json"], dict),
            import_hash=row["import_hash"], raw_file_hash=row["raw_file_hash"], content_hash=row["content_hash"],
            row_count=row["row_count"], published_state_token=state_by_batch.get(str(row["batch_id"])) or _hash_text(f"netshop-migration-state-v1\n{row['batch_id']}\n{row['content_hash']}"),
            status=row["status"], publication_sequence=row["publication_sequence"],
        ))
    for batch_id, value in synthesized.items():
        batch = NetshopImportBatch.objects.get(id=batch_id)
        fingerprint_models.append(NetshopImportFingerprint(
            batch_id=batch_id, scope_key=value["scope_key"], scope_json=value["scope_json"],
            import_hash=batch.file_hash if _valid_hash(batch.file_hash) else _hash_text(batch.file_hash),
            raw_file_hash=value["raw_file_hash"], content_hash=value["content_hash"], row_count=batch.row_count,
            published_state_token=value["published_state_token"], status="migration_synthesized", publication_sequence=None,
        ))
    _bulk(NetshopImportFingerprint, fingerprint_models)

    attempt_models: list[NetshopImportAttempt] = []
    for row in source.execute("SELECT * FROM import_content_attempts WHERE domain='netshop' ORDER BY sequence"):
        attempt_models.append(NetshopImportAttempt(
            id=uuid.UUID(str(row["attempt_id"])), batch_id=row["batch_id"], scope_key=row["scope_key"],
            raw_file_hash=row["raw_file_hash"], content_hash=row["content_hash"], outcome=row["outcome"],
            error_code=row["error_code"], actor_email=row["actor"],
            metadata={"scopeJson": _json(row["scope_json"], dict), "importHash": row["import_hash"],
                      "rowCount": row["row_count"], "fileName": row["file_name"],
                      "fileSizeBytes": row["file_size_bytes"], "warnings": _json(row["warnings_json"], list),
                      "recoveredFromAttemptId": row["recovered_from_attempt_id"],
                      "sourceCreatedAt": row["created_at"], "sourceUpdatedAt": row["updated_at"]},
            completed_at=_datetime(row["updated_at"]) if row["outcome"] != "processing" else None,
        ))
    _bulk(NetshopImportAttempt, attempt_models)
    _bulk(NetshopImportScopeHead, [NetshopImportScopeHead(
        scope_key=row["scope_key"], state_token=row["state_token"], status=row["status"],
        owner_token=str(row["owner_token"] or ""), current_batch_id=str(row["current_batch_id"] or ""),
        generation=row["generation"],
    ) for row in heads.values()])

    _bulk(NetshopAssetUpload, [NetshopAssetUpload(
        id=row["id"], fingerprint=row["fingerprint"], shop_name=row["shop_name"], snapshot_date=row["snapshot_date"],
        file_name=row["file_name"], file_size_bytes=row["file_size_bytes"], chunk_size_bytes=row["chunk_size_bytes"],
        chunk_count=row["chunk_count"], received_chunk_count=row["received_chunk_count"], received_bytes=row["received_bytes"],
        status=row["status"], processing_owner=str(row["processing_owner"] or ""), owner_generation=0,
        expires_at=_datetime(row["expires_at"]),
    ) for row in source.execute("SELECT * FROM netshop_asset_uploads ORDER BY id")])
    _bulk(NetshopAssetUploadChunk, [NetshopAssetUploadChunk(
        upload_id=row["upload_id"], chunk_index=row["chunk_index"], object_key=row["object_key"],
        size_bytes=row["size_bytes"], sha256=row["sha256"],
    ) for row in source.execute("SELECT * FROM netshop_asset_upload_chunks ORDER BY upload_id,chunk_index")])
    _bulk(NetshopAssetUploadResult, [NetshopAssetUploadResult(
        upload_id=row["upload_id"], result_json=_json(row["result_json"], dict),
    ) for row in source.execute("SELECT * FROM netshop_asset_upload_results ORDER BY upload_id")])
    NetshopDataRevision.objects.update_or_create(
        domain="netshop", defaults={"revision": 1, "source_digest": source_digest}
    )
    authority = NetshopWriteAuthority.objects.select_for_update().get(id=1)
    authority.migration_verify_run_id = run_id
    authority.save(update_fields=["migration_verify_run_id", "updated_at"])
    return {"synthesizedFingerprints": len(synthesized)}


def _target_empty() -> bool:
    models = (
        NetshopImportBatch, NetshopRow, NetshopPromotionProductDaily, NetshopPromotionShopDaily,
        NetshopPromotionAggregateState, NetshopPromotionAggregateManifest,
        NetshopPromotionAggregateControl, NetshopProductDailyRevision,
        NetshopProductDailyScopeRevision, NetshopPromotionScopeRevision,
        NetshopImportAttempt, NetshopImportFingerprint, NetshopImportScopeHead,
        NetshopAssetUpload, NetshopAssetUploadChunk, NetshopAssetUploadResult,
    )
    return all(not model.objects.exists() for model in models)


class Command(BaseCommand):
    help = "Plan, apply, or verify the D1 to PostgreSQL netshop migration."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument("--apply", action="store_true")
        modes.add_argument("--verify-only", action="store_true")
        parser.add_argument("--approved-run-id", default="")

    def handle(self, *args, **options):
        source_path = Path(options["source"]).expanduser().resolve()
        if not source_path.is_file() or source_path.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
            raise CommandError("--source 必须指向只读 SQLite 快照文件。")
        if any(Path(str(source_path) + suffix).exists() for suffix in ("-wal", "-shm")):
            raise CommandError("迁移源旁存在 WAL/SHM；请先生成封存快照。")
        apply = bool(options["apply"])
        verify_only = bool(options["verify_only"])
        approved = str(options["approved_run_id"] or "")
        source = _open_source(source_path)
        try:
            authority = _validate_source(source, apply=apply)
            counts, digests, source_digest = _snapshot(_source_sections(source))
            run_id = f"netshop-{source_digest[:24]}"
            manifest = {
                "version": FORMAT_VERSION,
                "runId": run_id,
                "sourceDigest": source_digest,
                "counts": counts,
                "digests": digests,
                "authority": authority,
            }
            if not apply and not verify_only:
                self.stdout.write(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
                return
            if approved != run_id:
                raise CommandError("--approved-run-id 与当前封存快照计划不一致。")
            if verify_only:
                run = NetshopMigrationRun.objects.filter(id=run_id, status="completed").first()
                if (
                    run is None
                    or run.source_snapshot_digest != source_digest
                    or run.completed_at is None
                    or run.completed_at < run.created_at
                ):
                    raise CommandError("PostgreSQL 缺少对应的已完成网店迁移记录。")
                target_counts, target_digests, target_digest = _snapshot(_target_sections())
                if target_counts != counts or target_digests != digests or target_digest != source_digest:
                    raise CommandError("D1 与 PostgreSQL 网店迁移回查不一致。")
                self.stdout.write(json.dumps({**manifest, "status": "verified", "targetDigest": target_digest}, ensure_ascii=False, sort_keys=True))
                return
            existing = NetshopMigrationRun.objects.filter(id=run_id, status="completed").first()
            if existing:
                target_counts, target_digests, target_digest = _snapshot(_target_sections())
                if target_counts != counts or target_digests != digests or target_digest != source_digest:
                    raise CommandError("既有网店迁移记录与当前 PostgreSQL 数据不一致。")
                self.stdout.write(json.dumps({**manifest, "status": "duplicate", "targetDigest": target_digest}, ensure_ascii=False, sort_keys=True))
                return
            authority_row = NetshopWriteAuthority.objects.filter(id=1).first()
            if authority_row is None or authority_row.status != "d1":
                raise CommandError("PostgreSQL 网店 authority 必须保持 d1 预切换状态。")
            if not _target_empty():
                raise CommandError("PostgreSQL 网店目标不是空白镜像，拒绝覆盖。")
            generation = uuid.uuid4().hex
            path_digest = _hash_text(os.path.normcase(str(source_path)))
            with transaction.atomic():
                audit = _migrate(source, generation, source_digest, run_id)
                target_counts, target_digests, target_digest = _snapshot(_target_sections())
                if target_counts != counts or target_digests != digests or target_digest != source_digest:
                    raise CommandError("网店迁移事务内逐节回查不一致。")
                migration_run = NetshopMigrationRun.objects.create(
                    id=run_id, mode="apply", status="completed", source_path_digest=path_digest,
                    source_snapshot_digest=source_digest, target_snapshot_digest=target_digest,
                    source_counts=counts, target_counts=target_counts, approved_run_id=approved,
                    manifest={**manifest, **audit}, completed_at=None,
                )
                migration_run.completed_at = timezone.now()
                migration_run.save(update_fields=["completed_at"])
            self.stdout.write(json.dumps({**manifest, **audit, "status": "applied", "targetDigest": source_digest}, ensure_ascii=False, sort_keys=True))
        finally:
            source.rollback()
            source.close()
