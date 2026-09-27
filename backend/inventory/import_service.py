from __future__ import annotations

from datetime import date
import hashlib
import json
import logging
import re
import uuid

from django.db import connection, transaction
from django.utils import timezone

from .errors import InventoryApiError
from .models import (
    InventoryAgeLine,
    InventoryImportAttempt,
    InventoryImportBatch,
    InventoryImportFingerprint,
    InventoryImportScopeHead,
    InventoryOperatingSettings,
    InventoryStockLine,
)
from .revisions import bump_revision
from .write_requests import lock_active_authority


DATASETS = frozenset({"stock", "age"})
SCOPE_KEYS = {
    "stock": "b1dda3405306702bed118060f189eb3837be5e07dcec8df8684b12edf4840704",
    "age": "ce499a195aa16f0f763b11768a0c897ff8f51beb6d4c3a35e6f5dcbb8795055d",
}
IMPORT_VERSION = {"stock": "inventory-stock-pg-v2", "age": "inventory-age-pg-v1"}
MAX_ROWS = 100_000
MAX_WARNINGS = 200
MAX_ABSOLUTE_QUANTITY = 10_000_000
MAX_INVENTORY_AGE_DAYS = 3_650
MAX_UNIT_COST_CENTS = 100_000_000
MAX_ROW_STOCK_VALUE_CENTS = 100_000_000_000
HEX_64_RE = re.compile(r"^[a-f0-9]{64}$")
SUMMARY_LABEL_RE = re.compile(r"^(?:合计|总计|小计|汇总|grand\s*total|total)$", re.I)
logger = logging.getLogger(__name__)

STOCK_FIELDS = frozenset(
    {
        "sourceRowNumber",
        "rowKey",
        "snapshotDate",
        "warehouse",
        "warehouseType",
        "warehouseCategory",
        "includeInInventory",
        "productCode",
        "productName",
        "brand",
        "supplier",
        "specification",
        "barcode",
        "category",
        "onHandQuantity",
        "availableQuantity",
        "lockedQuantity",
        "inTransitQuantity",
        "unitCostCents",
        "inventoryAgeDays",
        "sales7dQuantity",
        "sales30dQuantity",
    }
)
AGE_FIELDS = frozenset(
    {
        "sourceRowNumber",
        "warehouse",
        "warehouseType",
        "productCode",
        "productName",
        "specification",
        "category",
        "availableQuantity",
        "inventoryAgeDays",
        "sales7dQuantity",
        "sales30dQuantity",
        "unitCostCents",
        "stockValueCents",
    }
)


def _error(message: str, *, code: str = "invalid_request", status: int = 400) -> InventoryApiError:
    return InventoryApiError(message, code=code, status=status)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value: object, label: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise _error(f"{label} 无效")
    normalized = value.strip()
    if (not allow_empty and not normalized) or len(normalized) > maximum:
        raise _error(f"{label} 无效")
    return normalized


def _integer(
    value: object,
    label: str,
    minimum: int,
    maximum: int,
    *,
    nullable: bool = False,
) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise _error(f"{label} 无效")
    return value


def _date(value: object, label: str) -> date:
    text = _text(value, label, 10)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise _error(f"{label} 必须为有效的 YYYY-MM-DD") from error
    if parsed.isoformat() != text:
        raise _error(f"{label} 必须为有效的 YYYY-MM-DD")
    return parsed


def _hex(value: object, label: str) -> str:
    normalized = _text(value, label, 64).lower()
    if not HEX_64_RE.fullmatch(normalized):
        raise _error(f"{label} 无效")
    return normalized


def _warnings(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > MAX_WARNINGS:
        raise _error("warnings 无效")
    output: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict) or not set(item).issubset({"row", "field", "code", "message"}):
            raise _error("warning 字段集合无效")
        normalized: dict[str, object] = {
            "code": _text(item.get("code"), "warning.code", 64),
            "message": _text(item.get("message"), "warning.message", 500),
        }
        if "row" in item:
            normalized["row"] = _integer(item["row"], "warning.row", 1, 1_000_000)
        if "field" in item:
            normalized["field"] = _text(item["field"], "warning.field", 100, allow_empty=True)
        output.append(normalized)
    return output


def _warehouse_type(value: object) -> str:
    if value not in {"owned", "jd_rdc", "other"}:
        raise _error("warehouseType 无效")
    return str(value)


def _warehouse_category(value: object) -> str:
    from .warehouse_mapping import WAREHOUSE_CATEGORIES

    if value not in WAREHOUSE_CATEGORIES:
        raise _error("warehouseCategory 无效")
    return str(value)


def _validate_identity(warehouse: str, product_code: str, product_name: str, row_number: int) -> None:
    if warehouse == "刷刷仓":
        raise _error(f"第 {row_number} 行仍包含业务排除仓刷刷仓")
    if any(SUMMARY_LABEL_RE.fullmatch(value.strip()) for value in (warehouse, product_code, product_name)):
        raise _error(f"第 {row_number} 行是合计或汇总行，不能导入")


def _stock_row(
    item: object,
    snapshot_date: date,
    allow_negative: bool,
    warehouse_mapping: dict[str, dict[str, object]],
) -> dict[str, object]:
    if not isinstance(item, dict) or set(item) != STOCK_FIELDS:
        raise _error("库存行字段集合无效")
    source_row = int(_integer(item["sourceRowNumber"], "sourceRowNumber", 1, 1_000_000) or 0)
    warehouse = _text(item["warehouse"], "warehouse", 240)
    product_code = _text(item["productCode"], "productCode", 512)
    product_name = _text(item["productName"], "productName", 1000, allow_empty=True)
    _validate_identity(warehouse, product_code, product_name, source_row)
    row_key = _text(item["rowKey"], "rowKey", 512)
    if row_key != f"{warehouse}\x1f{product_code}":
        raise _error(f"第 {source_row} 行库存业务键不一致")
    row_date = item["snapshotDate"]
    if row_date is not None and _date(row_date, "row.snapshotDate") != snapshot_date:
        raise _error(f"第 {source_row} 行快照日期与批次范围不一致")
    minimum = -MAX_ABSOLUTE_QUANTITY if allow_negative else 0
    on_hand = int(_integer(item["onHandQuantity"], "onHandQuantity", minimum, MAX_ABSOLUTE_QUANTITY) or 0)
    available = int(_integer(item["availableQuantity"], "availableQuantity", minimum, MAX_ABSOLUTE_QUANTITY) or 0)
    locked = int(_integer(item["lockedQuantity"], "lockedQuantity", -MAX_ABSOLUTE_QUANTITY, MAX_ABSOLUTE_QUANTITY) or 0)
    in_transit = int(_integer(item["inTransitQuantity"], "inTransitQuantity", -MAX_ABSOLUTE_QUANTITY, MAX_ABSOLUTE_QUANTITY) or 0)
    unit_cost = int(_integer(item["unitCostCents"], "unitCostCents", 0, MAX_UNIT_COST_CENTS) or 0)
    age = _integer(item["inventoryAgeDays"], "inventoryAgeDays", 0, MAX_INVENTORY_AGE_DAYS, nullable=True)
    if max(0, available) * unit_cost > MAX_ROW_STOCK_VALUE_CENTS:
        raise _error(f"第 {source_row} 行库存货值超过 10 亿元安全上限")
    from .warehouse_mapping import classify_warehouse

    classification = classify_warehouse(warehouse, mapping=warehouse_mapping)
    warehouse_type = _warehouse_type(item["warehouseType"])
    warehouse_category = _warehouse_category(item["warehouseCategory"])
    include_in_inventory = item["includeInInventory"]
    if not isinstance(include_in_inventory, bool):
        raise _error("includeInInventory 无效")
    if (
        warehouse_type != classification.warehouse_type
        or warehouse_category != classification.category
        or include_in_inventory != classification.include_in_inventory
    ):
        raise _error(f"第 {source_row} 行仓库类型映射与受控配置不一致")
    return {
        "sourceRowNumber": source_row,
        "rowKey": row_key,
        "warehouse": warehouse,
        "warehouseType": warehouse_type,
        "warehouseCategory": warehouse_category,
        "includeInInventory": include_in_inventory,
        "productCode": product_code,
        "productName": product_name,
        "brand": _text(item["brand"], "brand", 500, allow_empty=True),
        "supplier": _text(item["supplier"], "supplier", 500, allow_empty=True),
        "specification": _text(item["specification"], "specification", 1000, allow_empty=True),
        "barcode": _text(item["barcode"], "barcode", 500, allow_empty=True),
        "category": _text(item["category"], "category", 500, allow_empty=True),
        "onHandQuantity": on_hand,
        "availableQuantity": available,
        "lockedQuantity": locked,
        "inTransitQuantity": in_transit,
        "unitCostCents": unit_cost,
        "inventoryAgeDays": age,
        "sales7dQuantity": int(_integer(item["sales7dQuantity"], "sales7dQuantity", -MAX_ABSOLUTE_QUANTITY, MAX_ABSOLUTE_QUANTITY) or 0),
        "sales30dQuantity": int(_integer(item["sales30dQuantity"], "sales30dQuantity", -MAX_ABSOLUTE_QUANTITY, MAX_ABSOLUTE_QUANTITY) or 0),
    }


def _age_row(item: object) -> dict[str, object]:
    if not isinstance(item, dict) or set(item) != AGE_FIELDS:
        raise _error("库龄行字段集合无效")
    source_row = int(_integer(item["sourceRowNumber"], "sourceRowNumber", 1, 1_000_000) or 0)
    warehouse = _text(item["warehouse"], "warehouse", 240)
    product_code = _text(item["productCode"], "productCode", 512)
    product_name = _text(item["productName"], "productName", 1000, allow_empty=True)
    _validate_identity(warehouse, product_code, product_name, source_row)
    available = int(_integer(item["availableQuantity"], "availableQuantity", -MAX_ABSOLUTE_QUANTITY, MAX_ABSOLUTE_QUANTITY) or 0)
    unit_cost = int(_integer(item["unitCostCents"], "unitCostCents", 0, MAX_UNIT_COST_CENTS) or 0)
    stock_value = int(_integer(item["stockValueCents"], "stockValueCents", -MAX_ROW_STOCK_VALUE_CENTS, MAX_ROW_STOCK_VALUE_CENTS) or 0)
    return {
        "sourceRowNumber": source_row,
        "rowKey": f"{warehouse}\x1f{product_code}",
        "warehouse": warehouse,
        "warehouseType": _warehouse_type(item["warehouseType"]),
        "productCode": product_code,
        "productName": product_name,
        "specification": _text(item["specification"], "specification", 1000, allow_empty=True),
        "category": _text(item["category"], "category", 500, allow_empty=True),
        "availableQuantity": available,
        "inventoryAgeDays": _integer(item["inventoryAgeDays"], "inventoryAgeDays", 0, MAX_INVENTORY_AGE_DAYS, nullable=True),
        "sales7dQuantity": _integer(item["sales7dQuantity"], "sales7dQuantity", -MAX_ABSOLUTE_QUANTITY, MAX_ABSOLUTE_QUANTITY, nullable=True),
        "sales30dQuantity": _integer(item["sales30dQuantity"], "sales30dQuantity", -MAX_ABSOLUTE_QUANTITY, MAX_ABSOLUTE_QUANTITY, nullable=True),
        "unitCostCents": unit_cost,
        "stockValueCents": stock_value,
    }


def _normalized_payload(payload: object) -> dict[str, object]:
    required = {
        "dataset", "file", "snapshotDate", "sourceRowCount", "excludedCount", "rows", "warnings", "totals"
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise _error("库存导入字段集合无效")
    dataset = payload["dataset"]
    if dataset not in DATASETS:
        raise _error("dataset 必须是 stock 或 age")
    if not isinstance(payload["file"], dict) or set(payload["file"]) != {
        "name", "sizeBytes", "rawFileHash", "sheetName"
    }:
        raise _error("file 字段集合无效")
    file_value = payload["file"]
    snapshot_date = _date(payload["snapshotDate"], "snapshotDate")
    source_row_count = int(_integer(payload["sourceRowCount"], "sourceRowCount", 1, 1_000_000) or 0)
    excluded_count = int(_integer(payload["excludedCount"], "excludedCount", 0, 1_000_000) or 0)
    rows_value = payload["rows"]
    if not isinstance(rows_value, list) or not rows_value or len(rows_value) > MAX_ROWS:
        raise _error(f"rows 必须是 1 到 {MAX_ROWS} 项的数组")
    if len(rows_value) + excluded_count > source_row_count:
        raise _error("业务行数与排除行数超过来源行数")
    settings = InventoryOperatingSettings.objects.get(id=1)
    from .warehouse_mapping_service import effective_mapping
    warehouse_mapping = effective_mapping(settings)
    rows = [
        _stock_row(item, snapshot_date, settings.allow_negative_inventory, warehouse_mapping)
        if dataset == "stock"
        else _age_row(item)
        for item in rows_value
    ]
    row_keys = [str(row["rowKey"]) for row in rows]
    if len(set(row_keys)) != len(row_keys):
        raise _error("导入包含重复的仓库与货品业务键")
    totals = payload["totals"]
    if not isinstance(totals, dict) or len(_canonical(totals).encode("utf-8")) > 64_000:
        raise _error("totals 无效")
    return {
        "dataset": dataset,
        "snapshotDate": snapshot_date,
        "sourceRowCount": source_row_count,
        "excludedCount": excluded_count,
        "rows": rows,
        "warnings": _warnings(payload["warnings"]),
        "totals": totals,
        "file": {
            "name": _text(file_value["name"], "file.name", 255),
            "sizeBytes": int(_integer(file_value["sizeBytes"], "file.sizeBytes", 1, 128 * 1024 * 1024) or 0),
            "rawFileHash": _hex(file_value["rawFileHash"], "file.rawFileHash"),
            "sheetName": _text(file_value["sheetName"], "file.sheetName", 255),
        },
    }


def _business_content_hash(
    dataset: str,
    snapshot_date: date,
    business_rows: list[dict[str, object]],
    *,
    version: str | None = None,
) -> str:
    row_digests = sorted(_sha(_canonical(row)) for row in business_rows)
    scope = {"dataset": dataset, "snapshotDate": snapshot_date.isoformat()}
    return _sha(
        f"{version or IMPORT_VERSION[dataset]}\n{_canonical(scope)}\n{len(business_rows)}\n{''.join(row_digests)}"
    )


def _content_hash(data: dict[str, object]) -> str:
    business_rows: list[dict[str, object]] = []
    for row_value in data["rows"]:  # type: ignore[union-attr]
        row = dict(row_value)
        row.pop("sourceRowNumber", None)
        row.pop("rowKey", None)
        business_rows.append(row)
    return _business_content_hash(
        str(data["dataset"]),
        data["snapshotDate"],  # type: ignore[arg-type]
        business_rows,
    )


def _stored_content(batch: InventoryImportBatch) -> tuple[int, str]:
    stored_version = str(
        (batch.totals_json or {}).get("canonicalFormatVersion")
        or ("inventory-stock-pg-v1" if batch.dataset == "stock" else "inventory-age-pg-v1")
    )
    if batch.dataset == "stock":
        records = InventoryStockLine.objects.filter(batch_id=batch.id).order_by("id")
        rows_v2 = [
            {
                "warehouse": row.warehouse,
                "warehouseType": row.warehouse_type,
                "warehouseCategory": row.warehouse_category,
                "includeInInventory": bool(row.include_in_inventory),
                "productCode": row.product_code,
                "productName": row.product_name,
                "brand": row.brand,
                "supplier": row.supplier,
                "specification": row.specification,
                "barcode": row.barcode,
                "category": row.category,
                "onHandQuantity": int(row.on_hand_quantity),
                "availableQuantity": int(row.available_quantity),
                "lockedQuantity": int(row.locked_quantity),
                "inTransitQuantity": int(row.in_transit_quantity),
                "unitCostCents": int(row.unit_cost_cents),
                "inventoryAgeDays": row.inventory_age_days,
                "sales7dQuantity": int(row.sales_7d_quantity or 0),
                "sales30dQuantity": int(row.sales_30d_quantity or 0),
            }
            for row in records
        ]
        if stored_version == "inventory-stock-pg-v1":
            rows = [
                {
                    key: value
                    for key, value in row.items()
                    if key not in {"warehouseCategory", "includeInInventory", "supplier"}
                }
                for row in rows_v2
            ]
        elif stored_version == IMPORT_VERSION["stock"]:
            rows = rows_v2
        else:
            raise _error("库存批次规范化版本不受支持", code="version_conflict", status=409)
    else:
        records = InventoryAgeLine.objects.filter(batch_id=batch.id).order_by("id")
        rows = [
            {
                "warehouse": row.warehouse,
                "warehouseType": row.warehouse_type,
                "productCode": row.product_code,
                "productName": row.product_name,
                "specification": row.specification,
                "category": row.category,
                "availableQuantity": int(row.available_quantity),
                "inventoryAgeDays": row.inventory_age_days,
                "sales7dQuantity": row.sales_7d_quantity,
                "sales30dQuantity": row.sales_30d_quantity,
                "unitCostCents": int(row.unit_cost_cents),
                "stockValueCents": int(row.stock_value_cents or 0),
            }
            for row in records
        ]
    return len(rows), _business_content_hash(
        batch.dataset,
        batch.snapshot_date,
        rows,
        version=stored_version,
    )


def _state_token(previous: str, batch_id: str, content_hash: str, row_count: int) -> str:
    return _sha(f"inventory-state-v1\n{previous}\n{batch_id}\n{content_hash}\n{row_count}")


def _batch_id(dataset: str, attempt_hash: str) -> str:
    return attempt_hash if dataset == "stock" else f"inventory_age:{attempt_hash}"


def _lock_scope(dataset: str) -> InventoryImportScopeHead:
    if connection.vendor == "postgresql":
        key = int.from_bytes(
            hashlib.sha256(f"inventory-scope\n{dataset}".encode()).digest()[:8],
            "big",
            signed=True,
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])
    return InventoryImportScopeHead.objects.select_for_update().get(dataset=dataset)


def _batch_payload(batch: InventoryImportBatch) -> dict[str, object]:
    return {
        "id": batch.id,
        "source": batch.source,
        "sourceLabel": batch.source,
        "sourceKey": "inventory_age" if batch.dataset == "age" else "inventory_stock",
        "dataset": batch.dataset,
        "fileName": batch.file_name,
        "fileSizeBytes": int(batch.file_size_bytes),
        "fileHash": batch.file_hash,
        "sheetName": batch.sheet_name,
        "snapshotDate": batch.snapshot_date.isoformat(),
        "status": batch.status,
        "sourceRowCount": int(batch.source_row_count),
        "rowCount": int(batch.row_count),
        "insertedCount": int(batch.inserted_count),
        "excludedCount": int(batch.excluded_count),
        "warningCount": int(batch.warning_count),
        "warnings": batch.warnings_json,
        "totals": batch.totals_json,
        "createdAt": batch.created_at.isoformat(),
        "completedAt": batch.completed_at.isoformat() if batch.completed_at else None,
    }


def list_import_batches(
    *, dataset: str | None, page: int, page_size: int, batch_id: str = ""
) -> dict[str, object]:
    rows = InventoryImportBatch.objects.all()
    if dataset:
        rows = rows.filter(dataset=dataset)
    if batch_id:
        rows = rows.filter(id=batch_id)
    rows = rows.order_by("-snapshot_date", "-completed_at", "-id")
    total = rows.count()
    offset = (page - 1) * page_size
    items = [_batch_payload(batch) for batch in rows[offset : offset + page_size]]
    return {
        "items": items,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "returned": len(items),
            "totalPages": (total + page_size - 1) // page_size,
            "truncated": offset + len(items) < total,
        },
    }


def _record_rejection(payload: object, actor_email: str, error: InventoryApiError) -> None:
    dataset = payload.get("dataset") if isinstance(payload, dict) else ""
    if dataset not in DATASETS:
        dataset = "stock"
    raw_hash = ""
    file_value = payload.get("file") if isinstance(payload, dict) else None
    if isinstance(file_value, dict) and isinstance(file_value.get("rawFileHash"), str):
        candidate = str(file_value["rawFileHash"]).lower()
        raw_hash = candidate if HEX_64_RE.fullmatch(candidate) else ""
    with transaction.atomic():
        lock_active_authority()
        InventoryImportAttempt.objects.create(
            id=uuid.uuid4().hex,
            dataset=dataset,
            scope_key="",
            raw_file_hash=raw_hash,
            outcome="rejected",
            error_code=error.code,
            actor_email=actor_email[:320],
            metadata={"message": str(error)[:500]},
            completed_at=timezone.now(),
        )


def record_edge_rejection(payload: object, actor_email: str) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != {
        "action", "dataset", "file", "snapshotDate", "message", "errors", "warnings"
    } or payload.get("action") != "reject" or payload.get("dataset") not in DATASETS:
        raise _error("库存拒绝审计字段集合无效")
    if not isinstance(payload["file"], dict) or set(payload["file"]) != {
        "name", "sizeBytes", "rawFileHash"
    }:
        raise _error("库存拒绝审计文件字段无效")
    file_value = payload["file"]
    safe_payload = {
        "dataset": payload["dataset"],
        "file": {
            "name": _text(file_value["name"], "file.name", 255),
            "sizeBytes": int(_integer(file_value["sizeBytes"], "file.sizeBytes", 0, 128 * 1024 * 1024) or 0),
            "rawFileHash": _hex(file_value["rawFileHash"], "file.rawFileHash"),
        },
    }
    snapshot = payload["snapshotDate"]
    if snapshot is not None:
        snapshot = _date(snapshot, "snapshotDate").isoformat()
    message = _text(payload["message"], "message", 500)
    errors = _warnings(payload["errors"])
    warnings = _warnings(payload["warnings"])
    with transaction.atomic():
        lock_active_authority()
        InventoryImportAttempt.objects.create(
            id=uuid.uuid4().hex,
            dataset=str(payload["dataset"]),
            scope_json={"dataset": payload["dataset"], "snapshotDate": snapshot},
            raw_file_hash=str(safe_payload["file"]["rawFileHash"]),
            outcome="rejected",
            error_code=str(errors[0]["code"] if errors else "EDGE_VALIDATION_REJECTED"),
            actor_email=actor_email[:320],
            metadata={"fileName": safe_payload["file"]["name"], "fileSizeBytes": safe_payload["file"]["sizeBytes"], "message": message, "errorCount": len(errors)},
            completed_at=timezone.now(),
        )
    return {"ok": False, "status": "rejected", "message": message, "warnings": warnings, "errors": errors, "errorCount": len(errors)}


def _import_inventory_payload(payload: object, actor_email: str) -> dict[str, object]:
    try:
        data = _normalized_payload(payload)
    except InventoryApiError as error:
        _record_rejection(payload, actor_email, error)
        raise
    dataset = str(data["dataset"])
    snapshot_date: date = data["snapshotDate"]  # type: ignore[assignment]
    rows: list[dict[str, object]] = data["rows"]  # type: ignore[assignment]
    file_value: dict[str, object] = data["file"]  # type: ignore[assignment]
    content_hash = _content_hash(data)
    scope_key = SCOPE_KEYS[dataset]
    now = timezone.now()
    warnings: list[dict[str, object]] = list(data["warnings"])  # type: ignore[arg-type]
    discovery_warnings: list[dict[str, object]] = []
    with transaction.atomic():
        lock_active_authority()
        settings = InventoryOperatingSettings.objects.select_for_update().get(id=1)
        from .warehouse_mapping import classify_warehouse
        from .warehouse_mapping_service import discover_pending_mappings, effective_mapping

        locked_mapping = effective_mapping(settings)
        if dataset == "stock":
            for row in rows:
                classification = classify_warehouse(str(row["warehouse"]), mapping=locked_mapping)
                if (
                    str(row["warehouseType"]) != classification.warehouse_type
                    or str(row["warehouseCategory"]) != classification.category
                    or bool(row["includeInInventory"]) != classification.include_in_inventory
                ):
                    raise _error(
                        "仓库映射在库存文件解析后发生变化，请重新上传文件",
                        code="version_conflict",
                        status=409,
                    )
        discovered_warehouses = discover_pending_mappings(
            settings,
            {str(row["warehouse"]) for row in rows},
            actor_email,
        )
        if discovered_warehouses:
            discovery_warning = {
                "code": "NEW_WAREHOUSES_PENDING_CONFIRMATION",
                "message": (
                    f"发现 {len(discovered_warehouses)} 个新仓库，已加入仓库映射并标记为待确认："
                    + "、".join(discovered_warehouses[:10])
                    + ("等" if len(discovered_warehouses) > 10 else "")
                ),
            }
            if len(warnings) >= MAX_WARNINGS:
                warnings = warnings[: MAX_WARNINGS - 1]
            warnings.append(discovery_warning)
            discovery_warnings.append(discovery_warning)
        head = _lock_scope(dataset)
        previous = head.state_token
        current = (
            InventoryImportBatch.objects.filter(id=head.current_batch_id).first()
            if head.current_batch_id
            else None
        )
        if head.current_batch_id and current is None:
            raise _error("库存范围头指向不存在的批次", code="version_conflict", status=409)
        if current is None:
            orphan_count = (
                InventoryStockLine.objects.count()
                if dataset == "stock"
                else InventoryAgeLine.objects.count()
            )
            if orphan_count:
                raise _error("库存事实缺少当前批次所有者", code="version_conflict", status=409)
        else:
            current_count, current_hash = _stored_content(current)
            if (
                current.dataset != dataset
                or current.status != "completed"
                or current.scope_key != scope_key
                or current.published_state_token != previous
                or current_count != int(current.row_count)
                or current_hash != current.content_hash
            ):
                raise _error("库存当前批次与事实摘要不一致", code="version_conflict", status=409)
        existing = (
            InventoryImportBatch.objects.filter(
                dataset=dataset,
                snapshot_date=snapshot_date,
                status="completed",
                content_hash=content_hash,
                row_count=len(rows),
            )
            .order_by("-completed_at", "-id")
            .first()
        )
        existing_owned_rows = 0
        existing_content_hash = ""
        if existing:
            existing_owned_rows, existing_content_hash = _stored_content(existing)
            if existing_owned_rows not in {0, len(rows)}:
                raise _error("库存历史批次仅持有部分事实", code="version_conflict", status=409)
            if existing_owned_rows == len(rows) and existing_content_hash != content_hash:
                raise _error("库存历史批次与事实摘要不一致", code="version_conflict", status=409)
        if existing and existing_owned_rows == len(rows) and existing_content_hash == content_hash:
            InventoryImportAttempt.objects.create(
                id=uuid.uuid4().hex,
                dataset=dataset,
                batch_id=existing.id,
                scope_key=scope_key,
                scope_json={"dataset": dataset, "snapshotDate": snapshot_date.isoformat()},
                raw_file_hash=str(file_value["rawFileHash"]),
                content_hash=content_hash,
                row_count=len(rows),
                outcome="duplicate",
                actor_email=actor_email[:320],
                metadata={"fileName": file_value["name"]},
                completed_at=now,
            )
            if discovered_warehouses:
                bump_revision({
                    "kind": "warehouse_discovery",
                    "dataset": dataset,
                    "warehouses": discovered_warehouses,
                })
            return {
                "ok": True,
                "status": "duplicate",
                "message": "全部标准化库存资料与当前快照一致，无需重复导入",
                "batch": _batch_payload(existing),
                "warnings": [*existing.warnings_json, *discovery_warnings],
            }
        attempt_hash = _sha(
            f"inventory-attempt-v1\n{dataset}\n{snapshot_date.isoformat()}\n{content_hash}\n{previous}"
        )
        batch_id = _batch_id(dataset, attempt_hash)
        owner_token = uuid.uuid4().hex
        head.status = "processing"
        head.owner_token = owner_token
        head.current_batch_id = batch_id
        head.generation = int(head.generation) + 1
        head.owner_started_at = now
        head.heartbeat_at = now
        head.save()
        attempt = InventoryImportAttempt.objects.create(
            id=uuid.uuid4().hex,
            dataset=dataset,
            batch_id=batch_id,
            scope_key=scope_key,
            scope_json={"dataset": dataset, "snapshotDate": snapshot_date.isoformat()},
            raw_file_hash=str(file_value["rawFileHash"]),
            content_hash=content_hash,
            row_count=len(rows),
            excluded_count=int(data["excludedCount"]),
            outcome="processing",
            actor_email=actor_email[:320],
            metadata={"fileName": file_value["name"]},
        )
        next_state = _state_token(previous, batch_id, content_hash, len(rows))
        batch = InventoryImportBatch.objects.create(
            id=batch_id,
            dataset=dataset,
            source=(
                "吉客云 ERP · 分仓库存查询"
                if dataset == "stock"
                else "吉客云 ERP · 库龄分析"
            ),
            file_name=str(file_value["name"]),
            file_size_bytes=int(file_value["sizeBytes"]),
            file_hash=attempt_hash,
            raw_file_hash=str(file_value["rawFileHash"]),
            content_hash=content_hash,
            scope_key=scope_key,
            published_state_token=next_state,
            sheet_name=str(file_value["sheetName"]),
            snapshot_date=snapshot_date,
            actor_email=actor_email[:320],
            status="processing",
            source_row_count=int(data["sourceRowCount"]),
            row_count=len(rows),
            warning_count=len(warnings),
            warnings_json=warnings,
            totals_json={
                **data["totals"],  # type: ignore[arg-type]
                "rawFileHash": file_value["rawFileHash"],
                "contentHash": content_hash,
                "canonicalFormatVersion": IMPORT_VERSION[dataset],
            },
        )
        if dataset == "stock":
            from .guangdong_risk_reset import healthy_stock_baseline
            healthy_baseline = healthy_stock_baseline()
            InventoryStockLine.objects.filter(snapshot_date=snapshot_date).delete()
            InventoryStockLine.objects.bulk_create(
                [
                    InventoryStockLine(
                        batch_id=batch_id,
                        row_key=str(row["rowKey"]),
                        source_row_number=int(row["sourceRowNumber"]),
                        snapshot_date=snapshot_date,
                        warehouse=str(row["warehouse"]),
                        warehouse_type=str(row["warehouseType"]),
                        warehouse_category=str(row["warehouseCategory"]),
                        include_in_inventory=bool(row["includeInInventory"]),
                        product_code=str(row["productCode"]),
                        product_name=str(row["productName"]),
                        brand=str(row["brand"]),
                        supplier=str(row["supplier"]),
                        specification=str(row["specification"]),
                        barcode=str(row["barcode"]),
                        category=str(row["category"]),
                        on_hand_quantity=int(row["onHandQuantity"]),
                        available_quantity=int(row["availableQuantity"]),
                        locked_quantity=int(row["lockedQuantity"]),
                        in_transit_quantity=int(row["inTransitQuantity"]),
                        unit_cost_cents=int(row["unitCostCents"]),
                        inventory_age_days=row["inventoryAgeDays"],
                        sales_7d_quantity=int(row["sales7dQuantity"]),
                        sales_30d_quantity=int(row["sales30dQuantity"]),
                    )
                    for row in rows
                ],
                batch_size=2_000,
            )
            inserted = InventoryStockLine.objects.filter(batch_id=batch_id).count()
        else:
            InventoryAgeLine.objects.filter(snapshot_date=snapshot_date).delete()
            InventoryAgeLine.objects.bulk_create(
                [
                    InventoryAgeLine(
                        batch_id=batch_id,
                        row_key=str(row["rowKey"]),
                        source_row_number=int(row["sourceRowNumber"]),
                        snapshot_date=snapshot_date,
                        warehouse=str(row["warehouse"]),
                        warehouse_type=str(row["warehouseType"]),
                        product_code=str(row["productCode"]),
                        product_name=str(row["productName"]),
                        specification=str(row["specification"]),
                        category=str(row["category"]),
                        available_quantity=int(row["availableQuantity"]),
                        inventory_age_days=row["inventoryAgeDays"],
                        sales_7d_quantity=row["sales7dQuantity"],
                        sales_30d_quantity=row["sales30dQuantity"],
                        unit_cost_cents=int(row["unitCostCents"]),
                        stock_value_cents=int(row["stockValueCents"]),
                    )
                    for row in rows
                ],
                batch_size=2_000,
            )
            inserted = InventoryAgeLine.objects.filter(batch_id=batch_id).count()
        if inserted != len(rows):
            raise _error("库存事实落库行数与权威业务集合不一致", status=503, code="service_unavailable")
        readback_count, readback_hash = _stored_content(batch)
        if readback_count != len(rows) or readback_hash != content_hash:
            raise _error("库存事实落库内容回查不一致", status=503, code="service_unavailable")
        batch.status = "completed"
        batch.inserted_count = inserted
        batch.completed_at = now
        batch.save(update_fields=["status", "inserted_count", "completed_at"])
        InventoryImportFingerprint.objects.create(
            dataset=dataset,
            batch_id=batch_id,
            scope_key=scope_key,
            scope_json={"dataset": dataset, "snapshotDate": snapshot_date.isoformat()},
            import_hash=attempt_hash,
            content_hash=content_hash,
            raw_file_hash=str(file_value["rawFileHash"]),
            row_count=len(rows),
            published_state_token=next_state,
        )
        attempt.outcome = "imported"
        attempt.completed_at = now
        attempt.save(update_fields=["outcome", "completed_at"])
        head.state_token = next_state
        head.status = "ready"
        head.owner_token = ""
        head.current_batch_id = batch_id
        head.heartbeat_at = now
        head.save()
        if dataset == "stock":
            from .replenishment_health import advance_cycles
            advance_cycles(batch)
            from .guangdong_risk_reset import reset_increased_healthy
            reset_increased_healthy(batch, healthy_baseline, actor_email)
        bump_revision(
            {
                "kind": "import",
                "dataset": dataset,
                "batchId": batch_id,
                "snapshotDate": snapshot_date.isoformat(),
                "contentHash": content_hash,
                "discoveredWarehouses": discovered_warehouses,
            }
        )
        batch.refresh_from_db()
        if batch.status != "completed" or int(batch.inserted_count) != len(rows):
            raise _error("库存导入完成后批次回查失败", status=503, code="service_unavailable")
        return {
            "ok": True,
            "status": "imported",
            "message": "分仓库存快照同步成功" if dataset == "stock" else "库龄分析快照同步成功",
            "batch": _batch_payload(batch),
            "warnings": warnings,
        }


def _record_import_failure(
    data: dict[str, object], actor_email: str, error: Exception
) -> None:
    file_value: dict[str, object] = data["file"]  # type: ignore[assignment]
    dataset = str(data["dataset"])
    with transaction.atomic():
        lock_active_authority()
        InventoryImportAttempt.objects.create(
            id=uuid.uuid4().hex,
            dataset=dataset,
            scope_key=SCOPE_KEYS[dataset],
            scope_json={
                "dataset": dataset,
                "snapshotDate": data["snapshotDate"].isoformat(),  # type: ignore[union-attr]
            },
            raw_file_hash=str(file_value["rawFileHash"]),
            content_hash=_content_hash(data),
            row_count=len(data["rows"]),  # type: ignore[arg-type]
            excluded_count=int(data["excludedCount"]),
            outcome="failed",
            error_code=(
                error.code
                if isinstance(error, InventoryApiError)
                else "INVENTORY_IMPORT_FAILED"
            )[:64],
            actor_email=actor_email[:320],
            metadata={
                "fileName": file_value["name"],
                "message": str(error)[:500],
            },
            completed_at=timezone.now(),
        )


def import_inventory_payload(payload: object, actor_email: str) -> dict[str, object]:
    try:
        return _import_inventory_payload(payload, actor_email)
    except Exception as error:
        try:
            data = _normalized_payload(payload)
        except Exception:
            # Prevalidation failures are already persisted by
            # _import_inventory_payload through the rejection audit path.
            data = None
        if data is not None:
            try:
                _record_import_failure(data, actor_email, error)
            except Exception as audit_error:
                logger.exception(
                    "inventory_import_failure_audit_failed type=%s",
                    type(audit_error).__name__,
                )
        raise
