from __future__ import annotations

from datetime import date, timezone as datetime_timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from inventory.import_service import (
    IMPORT_VERSION,
    SCOPE_KEYS,
    _business_content_hash,
    _state_token,
)
from inventory.models import (
    InventoryAgeLine,
    InventoryDataRevision,
    InventoryImportAttempt,
    InventoryImportBatch,
    InventoryImportFingerprint,
    InventoryImportScopeHead,
    InventoryMigrationRun,
    InventoryOperatingSettings,
    InventoryRawUploadSession,
    InventoryStockLine,
    InventoryWriteAuthority,
    InventoryWriteRequestReceipt,
    ReplenishmentPlanItem,
)


GENERATION_VERSION = "inventory-d1-to-postgres-v1"
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
INVENTORY_DOMAIN = "inventory-stock"
ERP_DOMAIN = "erp-reference"
DEFAULT_SETTINGS = {
    "targetDays": 30,
    "criticalDays": 7,
    "slowDays": 45,
    "stagnantDays": 90,
    "autoReplenishment": False,
    "inventoryAlert": True,
    "allowNegativeInventory": False,
}
DEFAULT_UPDATED_AT = "1970-01-01T00:00:00+00:00"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _stable_hex(value: object, seed: object) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if HEX_RE.fullmatch(candidate) else _sha(seed)


def _parse_json(value: object, fallback: object) -> object:
    if not isinstance(value, str):
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _timestamp(value: object):
    parsed = parse_datetime(str(value or ""))
    if parsed is None:
        raise CommandError("D1 库存时间戳无效")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed.astimezone(datetime_timezone.utc)


def _optional_timestamp(value: object):
    return _timestamp(value) if value else None


def _table(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _rows(
    connection: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> list[dict[str, object]]:
    return [dict(row) for row in connection.execute(sql, parameters).fetchall()]


def _int(value: object, label: str, *, minimum: int | None = None) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError) as error:
        raise CommandError(f"D1 {label} 不是有效整数") from error
    if minimum is not None and result < minimum:
        raise CommandError(f"D1 {label} 小于安全下限")
    return result


def _normalize_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}

    def bounded(name: str, minimum: int, maximum: int) -> int:
        raw = source.get(name, DEFAULT_SETTINGS[name])
        try:
            parsed = round(float(raw))
        except (TypeError, ValueError):
            parsed = int(DEFAULT_SETTINGS[name])
        return min(maximum, max(minimum, parsed))

    return {
        "targetDays": bounded("targetDays", 1, 365),
        "criticalDays": bounded("criticalDays", 1, 120),
        "slowDays": bounded("slowDays", 1, 730),
        "stagnantDays": bounded("stagnantDays", 1, 1_460),
        "autoReplenishment": source.get("autoReplenishment", False) is True,
        "inventoryAlert": source.get("inventoryAlert", True) is not False,
        "allowNegativeInventory": source.get("allowNegativeInventory", False) is True,
    }


def _relevant_fingerprint(row: dict[str, object]) -> str | None:
    domain = str(row.get("domain") or "")
    if domain == INVENTORY_DOMAIN:
        return "stock"
    if domain != ERP_DOMAIN:
        return None
    scope = _parse_json(row.get("scope_json"), {})
    return "age" if isinstance(scope, dict) and scope.get("source") == "inventory_age" else None


def _source_snapshot(source: Path) -> dict[str, object]:
    try:
        connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise CommandError("无法以只读方式打开 D1 库存快照") from error
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        required = (
            "inventory_import_batches",
            "inventory_stock_lines",
            "inventory_age_metrics",
            "erp_reference_import_batches",
            "erp_inventory_age_lines",
            "replenishment_plan_items",
            "system_settings",
        )
        for name in required:
            if not _table(connection, name):
                raise CommandError(f"D1 快照缺少库存迁移表 {name}，或库存域已经退役")

        raw_stock_batches = _rows(
            connection,
            "SELECT rowid AS _legacy_rowid,* "
            "FROM inventory_import_batches ORDER BY created_at,id",
        )
        raw_age_batches = _rows(
            connection,
            "SELECT * FROM erp_reference_import_batches "
            "WHERE source_key='inventory_age' ORDER BY created_at,id",
        )
        if any(str(row.get("status")) == "processing" for row in raw_stock_batches + raw_age_batches):
            raise CommandError("D1 仍有 processing 库存或库龄批次")

        raw_stock = _rows(
            connection,
            "SELECT s.*,COALESCE(m.sales_7d_quantity,0) AS migrated_sales_7d_quantity,"
            "COALESCE(m.sales_30d_quantity,0) AS migrated_sales_30d_quantity "
            "FROM inventory_stock_lines s LEFT JOIN inventory_age_metrics m "
            "ON m.batch_id=s.batch_id AND m.row_key=s.row_key "
            "ORDER BY s.snapshot_date,s.batch_id,s.row_key,s.id",
        )
        raw_age = _rows(
            connection,
            "SELECT * FROM erp_inventory_age_lines "
            "ORDER BY snapshot_date,last_import_batch_id,warehouse,product_code,id",
        )
        stock_batch_ids = {str(row["id"]): row for row in raw_stock_batches}
        age_batch_ids = {str(row["id"]): row for row in raw_age_batches}
        if set(stock_batch_ids) & set(age_batch_ids):
            raise CommandError("D1 库存与库龄批次标识冲突")
        for row in raw_stock:
            batch = stock_batch_ids.get(str(row.get("batch_id") or ""))
            if batch is None or str(batch.get("status")) != "completed" or str(batch.get("snapshot_date")) != str(row.get("snapshot_date")):
                raise CommandError("D1 库存事实存在无效批次所有权")
        for row in raw_age:
            batch = age_batch_ids.get(str(row.get("last_import_batch_id") or ""))
            if batch is None or str(batch.get("status")) != "completed" or str(batch.get("snapshot_date")) != str(row.get("snapshot_date")):
                raise CommandError("D1 库龄事实存在无效批次所有权")

        stock_winner_by_date: dict[str, str] = {}
        for row in raw_stock_batches:
            if str(row.get("status") or "") != "completed":
                continue
            snapshot_date = str(row.get("snapshot_date") or "")
            existing = stock_batch_ids.get(stock_winner_by_date.get(snapshot_date, ""))
            if existing is None or _int(
                row.get("_legacy_rowid"), "库存批次 rowid", minimum=1
            ) > _int(existing.get("_legacy_rowid"), "库存批次 rowid", minimum=1):
                stock_winner_by_date[snapshot_date] = str(row["id"])

        superseded_stock_by_batch: dict[str, int] = {}
        authoritative_stock: list[dict[str, object]] = []
        for row in raw_stock:
            batch_id = str(row.get("batch_id") or "")
            if stock_winner_by_date.get(str(row.get("snapshot_date") or "")) != batch_id:
                superseded_stock_by_batch[batch_id] = (
                    superseded_stock_by_batch.get(batch_id, 0) + 1
                )
                continue
            authoritative_stock.append(row)
        raw_stock = authoritative_stock

        residual_brush_by_dataset: dict[str, dict[str, int]] = {
            "stock": {},
            "age": {},
        }
        for dataset, rows, owner_field in (
            ("stock", raw_stock, "batch_id"),
            ("age", raw_age, "last_import_batch_id"),
        ):
            for row in rows:
                if str(row.get("warehouse") or "").strip() != "刷刷仓":
                    continue
                batch_id = str(row.get(owner_field) or "")
                residual_brush_by_dataset[dataset][batch_id] = (
                    residual_brush_by_dataset[dataset].get(batch_id, 0) + 1
                )
        raw_stock = [
            row for row in raw_stock
            if str(row.get("warehouse") or "").strip() != "刷刷仓"
        ]
        raw_age = [
            row for row in raw_age
            if str(row.get("warehouse") or "").strip() != "刷刷仓"
        ]

        stock_keys = [(str(row["snapshot_date"]), str(row["warehouse"]), str(row["product_code"])) for row in raw_stock]
        age_keys = [(str(row["snapshot_date"]), str(row["warehouse"]), str(row["product_code"])) for row in raw_age]
        if len(stock_keys) != len(set(stock_keys)) or len(age_keys) != len(set(age_keys)):
            raise CommandError("D1 库存或库龄事实包含重复业务身份")

        legacy_age_scope_keys: set[str] = set()
        for table_name in ("import_content_fingerprints", "import_content_attempts"):
            if not _table(connection, table_name):
                continue
            for row in _rows(connection, f"SELECT * FROM {table_name}"):
                if _relevant_fingerprint(row) == "age":
                    legacy_age_scope_keys.add(str(row.get("scope_key") or ""))

        raw_heads = _rows(connection, "SELECT * FROM import_scope_heads") if _table(connection, "import_scope_heads") else []
        heads_by_dataset: dict[str, dict[str, object]] = {}
        for row in raw_heads:
            domain = str(row.get("domain") or "")
            current_batch_id = str(row.get("current_batch_id") or "")
            legacy_scope_key = str(row.get("scope_key") or "")
            dataset = (
                "stock" if domain == INVENTORY_DOMAIN
                else "age" if domain == ERP_DOMAIN and (
                    current_batch_id in age_batch_ids
                    or legacy_scope_key in legacy_age_scope_keys
                )
                else None
            )
            if dataset is None:
                continue
            if dataset in heads_by_dataset:
                raise CommandError("D1 库存导入范围头重复")
            if str(row.get("status") or "") != "ready" or str(row.get("owner_token") or ""):
                raise CommandError("D1 库存导入范围仍被写入所有者占用")
            if not HEX_RE.fullmatch(legacy_scope_key):
                raise CommandError("D1 库存导入范围键无效")
            valid_batches = stock_batch_ids if dataset == "stock" else age_batch_ids
            if current_batch_id and current_batch_id not in valid_batches:
                raise CommandError("D1 库存导入范围头引用错误业务批次")
            heads_by_dataset[dataset] = row

        latest_batch = {
            "stock": max(
                (row for row in raw_stock_batches if str(row.get("status")) == "completed"),
                key=lambda row: (
                    str(row.get("snapshot_date") or ""),
                    _int(row.get("_legacy_rowid"), "库存批次 rowid", minimum=1),
                ),
                default=None,
            ),
            "age": max(
                (row for row in raw_age_batches if str(row.get("status")) == "completed"),
                key=lambda row: (str(row.get("snapshot_date") or ""), str(row.get("completed_at") or ""), str(row["id"])),
                default=None,
            ),
        }
        prepared_heads: list[dict[str, object]] = []
        for dataset in ("stock", "age"):
            source_head = heads_by_dataset.get(dataset)
            current_batch_id = str(source_head.get("current_batch_id") or "") if source_head else str((latest_batch[dataset] or {}).get("id") or "")
            expected_current_batch_id = str((latest_batch[dataset] or {}).get("id") or "")
            if source_head and current_batch_id != expected_current_batch_id:
                raise CommandError("D1 库存导入范围头与最新完成批次不一致")
            generation = _int(source_head.get("generation"), "范围代次", minimum=0) if source_head else (1 if current_batch_id else 0)
            token = str(source_head.get("state_token") or "") if source_head else (
                _state_token("0" * 64, current_batch_id, _sha({"legacyBatch": current_batch_id}), 0)
                if current_batch_id else "0" * 64
            )
            if not HEX_RE.fullmatch(token):
                raise CommandError("D1 库存导入范围状态令牌无效")
            prepared_heads.append({
                "dataset": dataset,
                "scopeKey": SCOPE_KEYS[dataset],
                "stateToken": token,
                "status": "ready",
                "currentBatchId": current_batch_id,
                "generation": generation,
            })

        token_by_dataset = {str(row["dataset"]): str(row["stateToken"]) for row in prepared_heads}
        current_by_dataset = {str(row["dataset"]): str(row["currentBatchId"]) for row in prepared_heads}

        def prepare_batch(row: dict[str, object], dataset: str) -> dict[str, object]:
            totals = _parse_json(row.get("totals_json"), {})
            totals = totals if isinstance(totals, dict) else {}
            declared_excluded = (
                _int(totals.get("excludedBrushWarehouseRows"), "排除刷刷仓行数", minimum=0)
                + (_int(totals.get("excludedZeroCostRows"), "排除零成本行数", minimum=0) if dataset == "stock" else 0)
            )
            legacy_row_count = _int(row.get("row_count"), "批次行数", minimum=0)
            batch_id = str(row.get("id") or "")
            migration_excluded = residual_brush_by_dataset[dataset].get(batch_id, 0)
            excluded = declared_excluded + migration_excluded
            business_row_count = (
                legacy_row_count - migration_excluded
                if dataset == "stock"
                else legacy_row_count - declared_excluded - migration_excluded
            )
            if business_row_count < 0:
                raise CommandError("D1 库存批次排除行数超过批次行数")
            raw_hash = _stable_hex(totals.get("rawFileHash"), {"raw": dataset, "batch": batch_id})
            content_hash = _stable_hex(totals.get("contentHash"), {"content": dataset, "batch": batch_id})
            file_hash = _stable_hex(row.get("file_hash"), {"file": dataset, "batch": batch_id})
            created_at = _timestamp(row.get("created_at")).isoformat()
            return {
                "id": batch_id,
                "dataset": dataset,
                "source": str(row.get("source") or row.get("source_label") or ("吉客云 ERP · 分仓库存查询" if dataset == "stock" else "吉客云 ERP · 库龄分析")),
                "fileName": str(row.get("file_name") or ""),
                "fileSizeBytes": _int(row.get("file_size_bytes"), "文件字节数", minimum=0),
                "fileHash": file_hash,
                "rawFileHash": raw_hash,
                "contentHash": content_hash,
                "scopeKey": SCOPE_KEYS[dataset],
                "publishedStateToken": token_by_dataset[dataset] if current_by_dataset[dataset] == batch_id else _sha({"legacyPublished": dataset, "batch": batch_id}),
                "sheetName": str(row.get("sheet_name") or ""),
                "snapshotDate": str(row.get("snapshot_date") or ""),
                "actor": str(row.get("actor") or ""),
                "status": str(row.get("status") or ""),
                "sourceRowCount": _int(totals.get("sourceRowCount", legacy_row_count), "来源行数", minimum=0),
                "rowCount": business_row_count,
                "insertedCount": business_row_count if str(row.get("status")) == "completed" else 0,
                "excludedCount": excluded,
                "warningCount": _int(row.get("warning_count"), "告警数", minimum=0),
                "warnings": _parse_json(row.get("warnings_json"), []),
                "totals": {
                    **totals,
                    "legacyMigrationVersion": GENERATION_VERSION,
                    "migrationExcludedBrushWarehouseRows": migration_excluded,
                    "migrationSupersededStockRows": superseded_stock_by_batch.get(batch_id, 0),
                },
                "createdAt": created_at,
                "completedAt": _optional_timestamp(row.get("completed_at")).isoformat() if row.get("completed_at") else None,
            }

        batches = [prepare_batch(row, "stock") for row in raw_stock_batches] + [prepare_batch(row, "age") for row in raw_age_batches]
        if len({row["fileHash"] for row in batches}) != len(batches):
            raise CommandError("D1 库存与库龄文件身份在 PostgreSQL 唯一约束下冲突")

        stock = [
            {
                "batchId": str(row["batch_id"]),
                "rowKey": str(row["row_key"]),
                "sourceRowNumber": _int(row["source_row_number"], "库存源行号", minimum=1),
                "snapshotDate": str(row["snapshot_date"]),
                "warehouse": str(row["warehouse"]),
                "warehouseType": str(row["warehouse_type"]),
                "productCode": str(row["product_code"]),
                "productName": str(row.get("product_name") or ""),
                "brand": str(row.get("brand") or ""),
                "specification": str(row.get("specification") or ""),
                "barcode": str(row.get("barcode") or ""),
                "category": str(row.get("category") or ""),
                "onHandQuantity": _int(row["on_hand_quantity"], "实盘数量"),
                "availableQuantity": _int(row["available_quantity"], "可用数量"),
                "lockedQuantity": _int(row["locked_quantity"], "锁定数量"),
                "inTransitQuantity": _int(row["in_transit_quantity"], "在途数量"),
                "unitCostCents": _int(row["unit_cost_cents"], "单位成本", minimum=0),
                "inventoryAgeDays": None if row.get("inventory_age_days") is None else _int(row["inventory_age_days"], "库存库龄", minimum=0),
                "sales7dQuantity": _int(row.get("migrated_sales_7d_quantity"), "7 日销量"),
                "sales30dQuantity": _int(row.get("migrated_sales_30d_quantity"), "30 日销量"),
            }
            for row in raw_stock
        ]
        age = [
            {
                "batchId": str(row["last_import_batch_id"]),
                "rowKey": f"{str(row['warehouse'])}\x1f{str(row['product_code'])}",
                "sourceRowNumber": _int(row["source_row_number"], "库龄源行号", minimum=1),
                "snapshotDate": str(row["snapshot_date"]),
                "warehouse": str(row["warehouse"]),
                "warehouseType": str(row["warehouse_type"]),
                "productCode": str(row["product_code"]),
                "productName": str(row.get("product_name") or ""),
                "specification": str(row.get("specification") or ""),
                "category": str(row.get("category") or ""),
                "availableQuantity": _int(row["available_quantity"], "库龄可用数量"),
                "inventoryAgeDays": None if row.get("inventory_age_days") is None else _int(row["inventory_age_days"], "库龄天数", minimum=0),
                "sales7dQuantity": None if row.get("sales_7d_quantity") is None else _int(row["sales_7d_quantity"], "库龄 7 日销量"),
                "sales30dQuantity": None if row.get("sales_30d_quantity") is None else _int(row["sales_30d_quantity"], "库龄 30 日销量"),
                "unitCostCents": _int(row["unit_cost_cents"], "库龄单位成本", minimum=0),
                "stockValueCents": _int(row["stock_value_cents"], "库龄库存金额"),
            }
            for row in raw_age
        ]

        owned_rows: dict[str, list[dict[str, object]]] = {}
        for dataset, rows in (("stock", stock), ("age", age)):
            for row in rows:
                business = dict(row)
                batch_id = str(business.pop("batchId"))
                business.pop("rowKey", None)
                business.pop("sourceRowNumber", None)
                business.pop("snapshotDate", None)
                owned_rows.setdefault(batch_id, []).append(business)
        batches_by_id = {str(row["id"]): row for row in batches}
        for batch_id, rows in owned_rows.items():
            batch = batches_by_id[batch_id]
            if len(rows) != int(batch["rowCount"]):
                raise CommandError("D1 库存批次仅持有部分当前事实")
            canonical_version = (
                "inventory-stock-pg-v1"
                if str(batch["dataset"]) == "stock"
                else IMPORT_VERSION[str(batch["dataset"])]
            )
            migrated_hash = _business_content_hash(
                str(batch["dataset"]),
                date.fromisoformat(str(batch["snapshotDate"])),
                rows,
                version=canonical_version,
            )
            batch["contentHash"] = migrated_hash
            batch["totals"] = {
                **batch["totals"],
                "contentHash": migrated_hash,
                "canonicalFormatVersion": canonical_version,
            }

        raw_plans = _rows(connection, "SELECT * FROM replenishment_plan_items ORDER BY created_at,id")
        plans: list[dict[str, object]] = []
        for row in raw_plans:
            if str(row.get("status")) not in {"draft", "confirmed", "completed", "cancelled"}:
                raise CommandError("D1 备货计划状态无效")
            if str(row.get("source_batch_id")) not in stock_batch_ids:
                raise CommandError("D1 备货计划引用不存在的库存批次")
            plans.append({
                "id": str(row["id"]),
                "sourceBatchId": str(row["source_batch_id"]),
                "productCode": str(row["product_code"]),
                "productName": str(row.get("product_name") or ""),
                "warehouse": str(row["warehouse"]),
                "suggestedQuantity": _int(row["suggested_quantity"], "建议数量", minimum=0),
                "plannedQuantity": _int(row["planned_quantity"], "计划数量", minimum=0),
                "coverageDaysTenths": None if row.get("coverage_days_tenths") is None else _int(row["coverage_days_tenths"], "覆盖天数"),
                "reason": str(row.get("reason") or ""),
                "status": str(row["status"]),
                "createdBy": "",
                "createdAt": _timestamp(row["created_at"]).isoformat(),
                "updatedAt": _timestamp(row["updated_at"]).isoformat(),
            })

        setting_row = connection.execute(
            "SELECT value_json,updated_by,updated_at FROM system_settings WHERE key='operating'"
        ).fetchone()
        operating = _normalize_settings(_parse_json(setting_row["value_json"], {}) if setting_row else {})
        operating.update({
            "updatedBy": str(setting_row["updated_by"] or "") if setting_row else "",
            "updatedAt": _timestamp(setting_row["updated_at"]).isoformat() if setting_row else DEFAULT_UPDATED_AT,
        })

        fingerprints: list[dict[str, object]] = []
        if _table(connection, "import_content_fingerprints"):
            for row in _rows(connection, "SELECT * FROM import_content_fingerprints ORDER BY sequence"):
                dataset = _relevant_fingerprint(row)
                if dataset is None:
                    continue
                batch_id = str(row.get("batch_id") or "")
                fingerprints.append({
                    "dataset": dataset,
                    "batchId": batch_id,
                    "scopeKey": SCOPE_KEYS[dataset],
                    "scope": _parse_json(row.get("scope_json"), {}),
                    "importHash": str(row.get("import_hash") or ""),
                    "contentHash": (
                        str(batches_by_id[batch_id]["contentHash"])
                        if batch_id in owned_rows
                        else _stable_hex(row.get("content_hash"), {"fingerprint": dataset, "batch": batch_id})
                    ),
                    "rawFileHash": _stable_hex(row.get("raw_file_hash"), {"fingerprintRaw": dataset, "batch": batch_id}),
                    "rowCount": (
                        int(batches_by_id[batch_id]["rowCount"])
                        if batch_id in batches_by_id
                        else _int(row.get("row_count"), "指纹行数", minimum=0)
                    ),
                    "publishedStateToken": token_by_dataset[dataset] if current_by_dataset[dataset] == batch_id else _sha({"legacyFingerprint": dataset, "sequence": row.get("sequence")}),
                    "status": str(row.get("status") or "completed"),
                    "createdAt": _timestamp(row.get("created_at")).isoformat(),
                })

        attempts: list[dict[str, object]] = []
        if _table(connection, "import_content_attempts"):
            for row in _rows(connection, "SELECT * FROM import_content_attempts ORDER BY sequence"):
                dataset = _relevant_fingerprint(row)
                if dataset is None:
                    continue
                if str(row.get("outcome")) == "processing":
                    raise CommandError("D1 仍有 processing 库存导入尝试")
                attempts.append({
                    "id": str(row.get("attempt_id") or f"legacy-{row.get('sequence') or uuid.uuid4().hex}"),
                    "dataset": dataset,
                    "batchId": str(row.get("batch_id") or ""),
                    "scopeKey": str(row.get("scope_key") or ""),
                    "scope": _parse_json(row.get("scope_json"), {}),
                    "rawFileHash": _stable_hex(row.get("raw_file_hash"), {"attemptRaw": dataset, "sequence": row.get("sequence")}),
                    "contentHash": _stable_hex(row.get("content_hash"), {"attemptContent": dataset, "sequence": row.get("sequence")}),
                    "rowCount": _int(row.get("row_count"), "尝试行数", minimum=0),
                    "outcome": str(row.get("outcome") or "failed"),
                    "errorCode": str(row.get("error_code") or "")[:64],
                    "actor": str(row.get("actor") or ""),
                    "metadata": {
                        "fileName": str(row.get("file_name") or ""),
                        "fileSizeBytes": _int(row.get("file_size_bytes"), "尝试文件字节数", minimum=0),
                        "warnings": _parse_json(row.get("warnings_json"), []),
                        "legacyImportHash": str(row.get("import_hash") or ""),
                        "recoveredFromAttemptId": str(row.get("recovered_from_attempt_id") or ""),
                    },
                    "createdAt": _timestamp(row.get("created_at")).isoformat(),
                    "completedAt": _timestamp(row.get("updated_at") or row.get("created_at")).isoformat(),
                })

        if _table(connection, "inventory_import_uploads"):
            active_uploads = connection.execute(
                "SELECT COUNT(*) FROM inventory_import_uploads WHERE "
                "(fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%') "
                "AND status IN ('uploading','ready','processing')"
            ).fetchone()[0]
            inventory_upload_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM inventory_import_uploads WHERE "
                    "fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%'"
                ).fetchall()
            ]
            chunk_count = 0
            if inventory_upload_ids and _table(connection, "inventory_import_upload_chunks"):
                marks = ",".join("?" for _ in inventory_upload_ids)
                chunk_count = connection.execute(
                    f"SELECT COUNT(*) FROM inventory_import_upload_chunks WHERE upload_id IN ({marks})",
                    tuple(inventory_upload_ids),
                ).fetchone()[0]
            if active_uploads or chunk_count:
                raise CommandError("D1 库存分片会话尚未静默，拒绝迁移")

        snapshot = {
            "version": GENERATION_VERSION,
            "batches": sorted(batches, key=lambda row: (row["createdAt"], row["id"])),
            "stock": stock,
            "age": age,
            "plans": plans,
            "settings": operating,
            "fingerprints": fingerprints,
            "attempts": attempts,
            "scopeHeads": sorted(prepared_heads, key=lambda row: row["dataset"]),
        }
        snapshot["fingerprints"] = sorted(
            snapshot["fingerprints"], key=lambda row: (row["createdAt"], row["batchId"])
        )
        snapshot["attempts"] = sorted(
            snapshot["attempts"], key=lambda row: (row["createdAt"], row["id"])
        )
        connection.rollback()
        return snapshot
    finally:
        connection.close()


def _target_snapshot() -> dict[str, object]:
    batches = [{
        "id": row.id,
        "dataset": row.dataset,
        "source": row.source,
        "fileName": row.file_name,
        "fileSizeBytes": int(row.file_size_bytes),
        "fileHash": row.file_hash,
        "rawFileHash": row.raw_file_hash,
        "contentHash": row.content_hash,
        "scopeKey": row.scope_key,
        "publishedStateToken": row.published_state_token,
        "sheetName": row.sheet_name,
        "snapshotDate": row.snapshot_date.isoformat(),
        "actor": row.actor_email,
        "status": row.status,
        "sourceRowCount": int(row.source_row_count),
        "rowCount": int(row.row_count),
        "insertedCount": int(row.inserted_count),
        "excludedCount": int(row.excluded_count),
        "warningCount": int(row.warning_count),
        "warnings": row.warnings_json,
        "totals": row.totals_json,
        "createdAt": row.created_at.astimezone(datetime_timezone.utc).isoformat(),
        "completedAt": row.completed_at.astimezone(datetime_timezone.utc).isoformat() if row.completed_at else None,
    } for row in InventoryImportBatch.objects.order_by("created_at", "id")]
    stock = [{
        "batchId": row.batch_id, "rowKey": row.row_key, "sourceRowNumber": int(row.source_row_number),
        "snapshotDate": row.snapshot_date.isoformat(), "warehouse": row.warehouse,
        "warehouseType": row.warehouse_type, "productCode": row.product_code,
        "productName": row.product_name, "brand": row.brand, "specification": row.specification,
        "barcode": row.barcode, "category": row.category, "onHandQuantity": int(row.on_hand_quantity),
        "availableQuantity": int(row.available_quantity), "lockedQuantity": int(row.locked_quantity),
        "inTransitQuantity": int(row.in_transit_quantity), "unitCostCents": int(row.unit_cost_cents),
        "inventoryAgeDays": row.inventory_age_days, "sales7dQuantity": int(row.sales_7d_quantity or 0),
        "sales30dQuantity": int(row.sales_30d_quantity or 0),
    } for row in InventoryStockLine.objects.order_by("snapshot_date", "batch_id", "row_key", "id")]
    age = [{
        "batchId": row.batch_id, "rowKey": row.row_key, "sourceRowNumber": int(row.source_row_number),
        "snapshotDate": row.snapshot_date.isoformat(), "warehouse": row.warehouse,
        "warehouseType": row.warehouse_type, "productCode": row.product_code,
        "productName": row.product_name, "specification": row.specification, "category": row.category,
        "availableQuantity": int(row.available_quantity), "inventoryAgeDays": row.inventory_age_days,
        "sales7dQuantity": row.sales_7d_quantity, "sales30dQuantity": row.sales_30d_quantity,
        "unitCostCents": int(row.unit_cost_cents), "stockValueCents": int(row.stock_value_cents or 0),
    } for row in InventoryAgeLine.objects.order_by("snapshot_date", "batch_id", "warehouse", "product_code", "id")]
    plans = [{
        "id": row.id, "sourceBatchId": row.source_batch_id, "productCode": row.product_code,
        "productName": row.product_name, "warehouse": row.warehouse,
        "suggestedQuantity": int(row.suggested_quantity), "plannedQuantity": int(row.planned_quantity),
        "coverageDaysTenths": row.coverage_days_tenths, "reason": row.reason, "status": row.status,
        "createdBy": row.created_by,
        "createdAt": row.created_at.astimezone(datetime_timezone.utc).isoformat(),
        "updatedAt": row.updated_at.astimezone(datetime_timezone.utc).isoformat(),
    } for row in ReplenishmentPlanItem.objects.order_by("created_at", "id")]
    setting = InventoryOperatingSettings.objects.get(id=1)
    operating = {
        "targetDays": int(setting.target_days), "criticalDays": int(setting.critical_days),
        "slowDays": int(setting.slow_days), "stagnantDays": int(setting.stagnant_days),
        "autoReplenishment": bool(setting.auto_replenishment),
        "inventoryAlert": bool(setting.inventory_alert),
        "allowNegativeInventory": bool(setting.allow_negative_inventory),
        "updatedBy": setting.updated_by,
        "updatedAt": setting.updated_at.astimezone(datetime_timezone.utc).isoformat() if setting.updated_at else None,
    }
    fingerprints = [{
        "dataset": row.dataset, "batchId": row.batch_id, "scopeKey": row.scope_key,
        "scope": row.scope_json, "importHash": row.import_hash, "contentHash": row.content_hash,
        "rawFileHash": row.raw_file_hash, "rowCount": int(row.row_count),
        "publishedStateToken": row.published_state_token, "status": row.status,
        "createdAt": row.created_at.astimezone(datetime_timezone.utc).isoformat(),
    } for row in InventoryImportFingerprint.objects.order_by("created_at", "batch_id")]
    attempts = [{
        "id": str(row.id), "dataset": row.dataset, "batchId": row.batch_id,
        "scopeKey": row.scope_key, "scope": row.scope_json, "rawFileHash": row.raw_file_hash,
        "contentHash": row.content_hash, "rowCount": int(row.row_count), "outcome": row.outcome,
        "errorCode": row.error_code, "actor": row.actor_email, "metadata": row.metadata,
        "createdAt": row.created_at.astimezone(datetime_timezone.utc).isoformat(),
        "completedAt": row.completed_at.astimezone(datetime_timezone.utc).isoformat() if row.completed_at else "",
    } for row in InventoryImportAttempt.objects.order_by("created_at", "id")]
    heads = [{
        "dataset": row.dataset, "scopeKey": row.scope_key, "stateToken": row.state_token,
        "status": row.status, "currentBatchId": row.current_batch_id, "generation": int(row.generation),
    } for row in InventoryImportScopeHead.objects.order_by("dataset")]
    return {
        "version": GENERATION_VERSION,
        "batches": batches, "stock": stock, "age": age, "plans": plans,
        "settings": operating, "fingerprints": fingerprints, "attempts": attempts,
        "scopeHeads": heads,
    }


def _counts(snapshot: dict[str, object]) -> dict[str, int]:
    return {key: len(snapshot[key]) for key in ("batches", "stock", "age", "plans", "fingerprints", "attempts")}


def _exclusions(snapshot: dict[str, object]) -> dict[str, int]:
    return {
        "brushWarehouseRows": sum(
            _int(
                row.get("totals", {}).get("migrationExcludedBrushWarehouseRows", 0),
                "迁移排除刷刷仓行数",
                minimum=0,
            )
            for row in snapshot["batches"]
        ),
        "supersededStockRows": sum(
            _int(
                row.get("totals", {}).get("migrationSupersededStockRows", 0),
                "迁移归档旧版本库存行数",
                minimum=0,
            )
            for row in snapshot["batches"]
        ),
    }


class Command(BaseCommand):
    help = "Plan, apply, or verify the inventory migration from a read-only D1 SQLite snapshot."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        parser.add_argument("--mode", choices=("plan", "apply", "verify"), required=True)
        parser.add_argument("--approve-run-id")
        parser.add_argument("--verify-run-id")

    def handle(self, *args, **options) -> None:
        if settings.DJANGO_ENVIRONMENT == "production" and settings.DJANGO_PROCESS_ROLE != "migration_writer":
            raise CommandError("生产库存迁移只能由 migration_writer 进程角色操作")
        source_input = Path(str(options["source"])).expanduser()
        if not source_input.is_file() or source_input.is_symlink():
            raise CommandError("D1 source 必须是现有普通 SQLite 文件")
        source = source_input.resolve()
        source_path_digest = hashlib.sha256(str(source).lower().encode()).hexdigest()
        snapshot = _source_snapshot(source)
        source_digest = _sha(snapshot)
        counts = _counts(snapshot)
        exclusions = _exclusions(snapshot)
        mode = str(options["mode"])
        if mode == "plan":
            run_id = f"inventory-plan-{uuid.uuid4().hex}"
            InventoryMigrationRun.objects.create(
                id=run_id, mode="plan", status="planned",
                source_path_digest=source_path_digest,
                source_snapshot_digest=source_digest,
                source_counts=counts,
                manifest={
                    "version": GENERATION_VERSION,
                    "sourceDigest": source_digest,
                    "exclusions": exclusions,
                },
            )
            self.stdout.write(_json({
                "ok": True,
                "mode": mode,
                "runId": run_id,
                "sourceDigest": source_digest,
                "counts": counts,
                "exclusions": exclusions,
            }))
            return
        if mode == "verify":
            run_id = str(options.get("verify_run_id") or "")
            run = InventoryMigrationRun.objects.filter(id=run_id, mode="apply", status__in=["applied", "verified"]).first()
            if run is None:
                raise CommandError("verify-run-id 不是已应用的库存迁移")
            del snapshot
            target = _target_snapshot()
            target_digest = _sha(target)
            if (
                source_path_digest != run.source_path_digest
                or source_digest != run.source_snapshot_digest
                or target_digest != run.target_snapshot_digest
                or _counts(target) != run.target_counts
                or _exclusions(target) != exclusions
            ):
                raise CommandError("库存迁移复验摘要不一致")
            with transaction.atomic():
                authority = InventoryWriteAuthority.objects.select_for_update().get(id=1)
                if authority.status != "d1":
                    raise CommandError("库存 PostgreSQL 已激活写权，禁止改写迁移凭据")
                run = InventoryMigrationRun.objects.select_for_update().get(id=run_id)
                run.status = "verified"
                run.completed_at = timezone.now()
                run.save(update_fields=["status", "completed_at"])
                authority.migration_verify_run_id = run_id
                authority.save(update_fields=["migration_verify_run_id", "updated_at"])
            self.stdout.write(_json({
                "ok": True,
                "mode": mode,
                "runId": run_id,
                "targetDigest": target_digest,
                "counts": _counts(target),
                "exclusions": _exclusions(target),
            }))
            return

        approved_id = str(options.get("approve_run_id") or "")
        approved = InventoryMigrationRun.objects.filter(id=approved_id, mode="plan", status="planned").first()
        if approved is None:
            raise CommandError("apply 必须提供仍有效的 --approve-run-id")
        if approved.source_path_digest != source_path_digest or approved.source_snapshot_digest != source_digest:
            raise CommandError("D1 库存快照在 plan 后已变化，拒绝应用")
        authority = InventoryWriteAuthority.objects.get(id=1)
        revision = InventoryDataRevision.objects.get(domain="inventory")
        heads = list(InventoryImportScopeHead.objects.order_by("dataset"))
        if authority.status != "d1" or authority.migration_verify_run_id:
            raise CommandError("库存 PostgreSQL authority 不是全新 D1 状态")
        if any(model.objects.exists() for model in (
            InventoryImportBatch, InventoryStockLine, InventoryAgeLine,
            InventoryImportAttempt, InventoryImportFingerprint, ReplenishmentPlanItem,
            InventoryRawUploadSession, InventoryWriteRequestReceipt,
        )):
            raise CommandError("库存目标库不是空镜像，拒绝覆盖")
        if int(revision.revision) != 0 or revision.source_digest or len(heads) != 2 or any(
            row.state_token != "0" * 64 or row.status != "ready" or row.owner_token or row.current_batch_id or int(row.generation) != 0
            for row in heads
        ):
            raise CommandError("库存目标控制状态不是全新镜像，拒绝覆盖")

        run_id = f"inventory-apply-{uuid.uuid4().hex}"
        with transaction.atomic():
            InventoryMigrationRun.objects.create(
                id=run_id, mode="apply", status="applying",
                source_path_digest=source_path_digest, source_snapshot_digest=source_digest,
                source_counts=counts, approved_run_id=approved_id,
                manifest={
                    "version": GENERATION_VERSION,
                    "sourceDigest": source_digest,
                    "exclusions": exclusions,
                },
            )
            InventoryImportBatch.objects.bulk_create([
                InventoryImportBatch(
                    id=row["id"], dataset=row["dataset"], source=row["source"], file_name=row["fileName"],
                    file_size_bytes=row["fileSizeBytes"], file_hash=row["fileHash"], raw_file_hash=row["rawFileHash"],
                    content_hash=row["contentHash"], scope_key=row["scopeKey"], published_state_token=row["publishedStateToken"],
                    sheet_name=row["sheetName"], snapshot_date=row["snapshotDate"], actor_email=row["actor"], status=row["status"],
                    source_row_count=row["sourceRowCount"], row_count=row["rowCount"], inserted_count=row["insertedCount"],
                    excluded_count=row["excludedCount"], warning_count=row["warningCount"], warnings_json=row["warnings"],
                    totals_json=row["totals"], created_at=_timestamp(row["createdAt"]),
                    completed_at=_optional_timestamp(row["completedAt"]), migration_generation=run_id,
                ) for row in snapshot["batches"]
            ], batch_size=500)
            stock_rows = snapshot["stock"]
            for offset in range(0, len(stock_rows), 1_000):
                InventoryStockLine.objects.bulk_create([
                    InventoryStockLine(
                        batch_id=row["batchId"], row_key=row["rowKey"], source_row_number=row["sourceRowNumber"],
                        snapshot_date=row["snapshotDate"], warehouse=row["warehouse"], warehouse_type=row["warehouseType"],
                        product_code=row["productCode"], product_name=row["productName"], brand=row["brand"],
                        specification=row["specification"], barcode=row["barcode"], category=row["category"],
                        on_hand_quantity=row["onHandQuantity"], available_quantity=row["availableQuantity"],
                        locked_quantity=row["lockedQuantity"], in_transit_quantity=row["inTransitQuantity"],
                        unit_cost_cents=row["unitCostCents"], inventory_age_days=row["inventoryAgeDays"],
                        sales_7d_quantity=row["sales7dQuantity"], sales_30d_quantity=row["sales30dQuantity"],
                        migration_generation=run_id,
                    ) for row in stock_rows[offset:offset + 1_000]
                ], batch_size=1_000)
            age_rows = snapshot["age"]
            for offset in range(0, len(age_rows), 1_000):
                InventoryAgeLine.objects.bulk_create([
                    InventoryAgeLine(
                        batch_id=row["batchId"], row_key=row["rowKey"], source_row_number=row["sourceRowNumber"],
                        snapshot_date=row["snapshotDate"], warehouse=row["warehouse"], warehouse_type=row["warehouseType"],
                        product_code=row["productCode"], product_name=row["productName"], specification=row["specification"],
                        category=row["category"], available_quantity=row["availableQuantity"],
                        inventory_age_days=row["inventoryAgeDays"], sales_7d_quantity=row["sales7dQuantity"],
                        sales_30d_quantity=row["sales30dQuantity"], unit_cost_cents=row["unitCostCents"],
                        stock_value_cents=row["stockValueCents"], migration_generation=run_id,
                    ) for row in age_rows[offset:offset + 1_000]
                ], batch_size=1_000)
            ReplenishmentPlanItem.objects.bulk_create([
                ReplenishmentPlanItem(
                    id=row["id"], source_batch_id=row["sourceBatchId"], product_code=row["productCode"],
                    product_name=row["productName"], warehouse=row["warehouse"], suggested_quantity=row["suggestedQuantity"],
                    planned_quantity=row["plannedQuantity"], coverage_days_tenths=row["coverageDaysTenths"],
                    reason=row["reason"], status=row["status"], created_by=row["createdBy"],
                    created_at=_timestamp(row["createdAt"]), updated_at=_timestamp(row["updatedAt"]),
                    migration_generation=run_id,
                ) for row in snapshot["plans"]
            ], batch_size=500)
            for row in snapshot["plans"]:
                ReplenishmentPlanItem.objects.filter(id=row["id"]).update(
                    updated_at=_timestamp(row["updatedAt"])
                )
            InventoryImportFingerprint.objects.bulk_create([
                InventoryImportFingerprint(
                    dataset=row["dataset"], batch_id=row["batchId"], scope_key=row["scopeKey"], scope_json=row["scope"],
                    import_hash=row["importHash"], content_hash=row["contentHash"], raw_file_hash=row["rawFileHash"],
                    row_count=row["rowCount"], published_state_token=row["publishedStateToken"], status=row["status"],
                    created_at=_timestamp(row["createdAt"]),
                ) for row in snapshot["fingerprints"]
            ], batch_size=500)
            InventoryImportAttempt.objects.bulk_create([
                InventoryImportAttempt(
                    id=row["id"], dataset=row["dataset"], batch_id=row["batchId"], scope_key=row["scopeKey"],
                    scope_json=row["scope"], raw_file_hash=row["rawFileHash"], content_hash=row["contentHash"],
                    row_count=row["rowCount"], outcome=row["outcome"], error_code=row["errorCode"],
                    actor_email=row["actor"], metadata=row["metadata"], created_at=_timestamp(row["createdAt"]),
                    completed_at=_optional_timestamp(row["completedAt"]),
                ) for row in snapshot["attempts"]
            ], batch_size=500)
            for row in snapshot["scopeHeads"]:
                InventoryImportScopeHead.objects.filter(dataset=row["dataset"]).update(
                    scope_key=row["scopeKey"], state_token=row["stateToken"], status="ready", owner_token="",
                    current_batch_id=row["currentBatchId"], generation=row["generation"], owner_started_at=None,
                    heartbeat_at=None,
                )
            operating = snapshot["settings"]
            InventoryOperatingSettings.objects.filter(id=1).update(
                target_days=operating["targetDays"], critical_days=operating["criticalDays"],
                slow_days=operating["slowDays"], stagnant_days=operating["stagnantDays"],
                auto_replenishment=operating["autoReplenishment"], inventory_alert=operating["inventoryAlert"],
                allow_negative_inventory=operating["allowNegativeInventory"], updated_by=operating["updatedBy"],
                updated_at=_timestamp(operating["updatedAt"]),
            )
            revision = InventoryDataRevision.objects.select_for_update().get(domain="inventory")
            revision.revision = 1
            revision.source_digest = source_digest
            revision.save()
            del snapshot
            target = _target_snapshot()
            target_digest = _sha(target)
            if target_digest != source_digest:
                raise CommandError("库存迁移落库摘要回查不一致")
            run = InventoryMigrationRun.objects.get(id=run_id)
            run.status = "applied"
            run.target_snapshot_digest = target_digest
            run.target_counts = _counts(target)
            run.completed_at = timezone.now()
            run.save()
        self.stdout.write(_json({
            "ok": True,
            "mode": mode,
            "runId": run_id,
            "approvedRunId": approved_id,
            "targetDigest": source_digest,
            "counts": counts,
            "exclusions": exclusions,
        }))
