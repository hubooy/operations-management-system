from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections.abc import Iterable

from django.db import transaction
from django.utils import timezone

from sales.models import SalesDataRevision, SalesOrderLine

from .errors import ErpReferenceApiError
from .locking import lock_erp_reference_for_replace
from .models import (
    ErpComboItem,
    ErpProductMaster,
    ErpReferenceImportAttempt,
    ErpReferenceImportBatch,
    ErpReferenceImportFingerprint,
    ErpReferenceImportScopeHead,
)
from .write_requests import lock_active_authority


IMPORT_DOMAIN = "erp-reference"
IMPORT_VERSION = "erp-reference-normalized-v1"
MAX_ROWS = 100_000
MAX_WARNINGS = 200
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_LABELS = {
    "products": "吉客云 ERP · 货品导出",
    "combos": "吉客云 ERP · 组合装及子件",
}
SOURCE_SCOPES = {
    source: {"source": source}
    for source in SOURCE_LABELS
}
SOURCE_SCOPE_KEYS = {
    source: hashlib.sha256(
        json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    for source, scope in SOURCE_SCOPES.items()
}
logger = logging.getLogger(__name__)


def _error(message: str, *, code: str = "invalid_request", status: int = 400) -> ErpReferenceApiError:
    return ErpReferenceApiError(message, code=code, status=status)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hex(value: object, label: str) -> str:
    if not isinstance(value, str) or not HEX_RE.fullmatch(value):
        raise _error(f"{label} 必须是 64 位小写 SHA-256")
    return value


def _text(value: object, label: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise _error(f"{label} 无效")
    normalized = value.strip()
    if (
        (not allow_empty and not normalized)
        or len(normalized) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
    ):
        raise _error(f"{label} 无效")
    return normalized


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise _error(f"{label} 无效")
    return value


def _warnings(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > MAX_WARNINGS:
        raise _error("warnings 无效或超过上限")
    normalized: list[dict[str, object]] = []
    for raw in value:
        if not isinstance(raw, dict) or not set(raw).issubset({"row", "field", "code", "message"}):
            raise _error("warnings 包含无效项目")
        item: dict[str, object] = {"message": _text(raw.get("message"), "warning.message", 500)}
        if "row" in raw:
            item["row"] = _integer(raw["row"], "warning.row", 1, 10_000_000)
        if "field" in raw:
            item["field"] = _text(raw["field"], "warning.field", 100)
        if "code" in raw:
            item["code"] = _text(raw["code"], "warning.code", 100)
        normalized.append(item)
    return normalized


def _product_rows(value: object) -> list[dict[str, object]]:
    fields = {
        "productCode", "productName", "brand", "specification", "barcode",
        "category", "supplier", "productStatus", "sourceRowNumber",
    }
    if not isinstance(value, list) or not value or len(value) > MAX_ROWS:
        raise _error(f"rows 必须是 1 到 {MAX_ROWS} 项的数组")
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != fields:
            raise _error("货品主数据行字段集合无效")
        code = _text(raw["productCode"], "productCode", 512)
        if code in seen:
            raise _error("规范化货品主数据包含重复货品编号")
        seen.add(code)
        rows.append({
            "productCode": code,
            "productName": _text(raw["productName"], "productName", 4000, allow_empty=True),
            "brand": _text(raw["brand"], "brand", 1000, allow_empty=True),
            "specification": _text(raw["specification"], "specification", 4000, allow_empty=True),
            "barcode": _text(raw["barcode"], "barcode", 1000, allow_empty=True),
            "category": _text(raw["category"], "category", 2000, allow_empty=True),
            "supplier": _text(raw["supplier"], "supplier", 2000, allow_empty=True),
            "productStatus": _text(raw["productStatus"], "productStatus", 500, allow_empty=True),
            "sourceRowNumber": _integer(raw["sourceRowNumber"], "sourceRowNumber", 1, 10_000_000),
        })
    return sorted(rows, key=lambda row: str(row["productCode"]).encode("utf-8"))


def _combo_rows(value: object) -> list[dict[str, object]]:
    fields = {
        "parentCode", "parentName", "childCode", "childName",
        "childQuantityMilli", "sourceRowNumber",
    }
    if not isinstance(value, list) or not value or len(value) > MAX_ROWS:
        raise _error(f"rows 必须是 1 到 {MAX_ROWS} 项的数组")
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != fields:
            raise _error("组合装行字段集合无效")
        parent = _text(raw["parentCode"], "parentCode", 512)
        child = _text(raw["childCode"], "childCode", 512)
        identity = (parent, child)
        if identity in seen:
            raise _error("规范化组合装包含重复母件与子件组合")
        seen.add(identity)
        rows.append({
            "parentCode": parent,
            "parentName": _text(raw["parentName"], "parentName", 4000, allow_empty=True),
            "childCode": child,
            "childName": _text(raw["childName"], "childName", 4000, allow_empty=True),
            "childQuantityMilli": _integer(
                raw["childQuantityMilli"], "childQuantityMilli", 1, 10**15
            ),
            "sourceRowNumber": _integer(raw["sourceRowNumber"], "sourceRowNumber", 1, 10_000_000),
        })
    return sorted(
        rows,
        key=lambda row: (str(row["parentCode"]).encode("utf-8"), str(row["childCode"]).encode("utf-8")),
    )


def _business_row(source: str, row: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in row.items() if key != "sourceRowNumber"}


def content_hash(source: str, rows: Iterable[dict[str, object]]) -> str:
    normalized = list(rows)
    digests = sorted(_sha(_json(_business_row(source, row))) for row in normalized)
    return _sha(f"{IMPORT_VERSION}\n{source}\n{len(normalized)}\n{''.join(digests)}")


def product_rows_from_database() -> list[dict[str, object]]:
    return [
        {
            "productCode": row.product_code,
            "productName": row.product_name,
            "brand": row.brand,
            "specification": row.specification,
            "barcode": row.barcode,
            "category": row.category,
            "supplier": row.supplier,
            "productStatus": row.product_status,
            "sourceRowNumber": int(row.source_row_number),
        }
        for row in ErpProductMaster.objects.order_by("product_code")
    ]


def combo_rows_from_database() -> list[dict[str, object]]:
    return [
        {
            "parentCode": row.parent_code,
            "parentName": row.parent_name,
            "childCode": row.child_code,
            "childName": row.child_name,
            "childQuantityMilli": int(row.child_quantity_milli),
            "sourceRowNumber": int(row.source_row_number),
        }
        for row in ErpComboItem.objects.order_by("parent_code", "child_code")
    ]


def combined_database_digest() -> str:
    products = product_rows_from_database()
    combos = combo_rows_from_database()
    return _sha(
        f"erp-reference-authority-v1\n{content_hash('products', products)}\n"
        f"{content_hash('combos', combos)}\n{len(products)}\n{len(combos)}"
    )


def _batch_payload(batch: ErpReferenceImportBatch) -> dict[str, object]:
    return {
        "id": batch.id,
        "sourceKey": batch.source_key,
        "sourceLabel": batch.source_label,
        "fileName": batch.file_name,
        "fileSizeBytes": int(batch.file_size_bytes),
        "fileHash": batch.file_hash,
        "sheetName": batch.sheet_name,
        "snapshotDate": None,
        "status": batch.status,
        "rowCount": int(batch.row_count),
        "insertedCount": int(batch.inserted_count),
        "updatedCount": int(batch.updated_count),
        "excludedCount": int(batch.excluded_count),
        "warningCount": int(batch.warning_count),
        "warnings": batch.warnings_json,
        "totals": batch.totals_json,
        "createdAt": batch.created_at.isoformat(),
        "completedAt": batch.completed_at.isoformat() if batch.completed_at else None,
    }


def list_import_batches(source: str | None, page: int, page_size: int) -> dict[str, object]:
    if source is not None and source not in SOURCE_LABELS:
        raise _error("source 必须为 products 或 combos")
    if not 1 <= page <= 10_000 or not 1 <= page_size <= 100:
        raise _error("分页参数无效")
    query = ErpReferenceImportBatch.objects
    if source:
        query = query.filter(source_key=source)
    query = query.order_by("-created_at", "-id")
    total = query.count()
    offset = (page - 1) * page_size
    items = list(query[offset : offset + page_size])
    return {
        "items": [_batch_payload(batch) for batch in items],
        "pagination": {
            "page": page, "pageSize": page_size, "total": total,
            "returned": len(items), "truncated": offset + len(items) < total,
        },
    }


def get_import_batch(source: str, batch_id: str) -> dict[str, object] | None:
    batch = ErpReferenceImportBatch.objects.filter(source_key=source, id=batch_id).first()
    if batch is None:
        return None
    owned = (
        ErpProductMaster.objects.filter(last_import_batch_id=batch.id).count()
        if source == "products"
        else ErpComboItem.objects.filter(last_import_batch_id=batch.id).count()
    )
    return {**_batch_payload(batch), "ownedRowCount": owned}


def _record_attempt(
    *, source: str, raw_file_hash: str, outcome: str, actor_email: str,
    file_name: str = "", file_size_bytes: int = 0, content: str = "",
    row_count: int = 0, batch_id: str = "", import_hash: str = "",
    warnings: list[dict[str, object]] | None = None, error_code: str = "",
) -> None:
    ErpReferenceImportAttempt.objects.create(
        source_key=source,
        scope_key=SOURCE_SCOPE_KEYS.get(source, ""),
        scope_json=SOURCE_SCOPES.get(source, {}),
        raw_file_hash=raw_file_hash,
        content_hash=content,
        row_count=row_count,
        outcome=outcome,
        actor_email=actor_email[:320],
        file_name=file_name[:2000],
        file_size_bytes=file_size_bytes,
        batch_id=batch_id,
        import_hash=import_hash,
        warnings_json=warnings or [],
        error_code=error_code[:80],
    )


def record_rejection(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    allowed = {"kind", "version", "source", "fileName", "fileSizeBytes", "rawFileHash", "message", "warnings", "errors"}
    if set(payload) != allowed or payload.get("kind") != "rejection" or payload.get("version") != IMPORT_VERSION:
        raise _error("ERP 拒绝审计字段集合无效")
    source = payload.get("source")
    if source not in SOURCE_LABELS:
        raise _error("source 必须为 products 或 combos")
    raw_hash = _hex(payload.get("rawFileHash"), "rawFileHash")
    file_name = _text(payload.get("fileName"), "fileName", 255)
    file_size = _integer(payload.get("fileSizeBytes"), "fileSizeBytes", 0, 20 * 1024 * 1024)
    warnings = _warnings(payload.get("warnings"))
    errors = _warnings(payload.get("errors"))
    message = _text(payload.get("message"), "message", 500)
    with transaction.atomic():
        lock_active_authority()
        _record_attempt(
            source=str(source), raw_file_hash=raw_hash, outcome="rejected",
            actor_email=actor_email, file_name=file_name, file_size_bytes=file_size,
            warnings=warnings, error_code=str(errors[0].get("code") if errors else "EDGE_REJECTED"),
        )
    return {
        "ok": False, "status": "rejected", "message": message,
        "warnings": warnings, "errors": errors, "errorCount": len(errors),
    }


def _restore_sales_category_fallback(lines) -> None:
    # ERP may read only product_code/category/resolved_category. Group by the
    # source category instead of reading sales row IDs or widening privileges.
    for category in lines.values_list("category", flat=True).distinct():
        lines.filter(category=category).update(
            resolved_category=(category or "").strip() or "未分类"
        )


def import_payload(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    if payload.get("kind") == "rejection":
        return record_rejection(payload, actor_email)
    allowed = {
        "version", "source", "fileName", "fileSizeBytes", "rawFileHash", "sheetName",
        "sourceRowCount", "rows", "warnings", "totals",
    }
    if set(payload) != allowed or payload.get("version") != IMPORT_VERSION:
        raise _error("ERP 规范化导入字段集合无效")
    source = payload.get("source")
    if source not in SOURCE_LABELS:
        raise _error("source 必须为 products 或 combos")
    source = str(source)
    file_name = _text(payload.get("fileName"), "fileName", 255)
    if not file_name.lower().endswith(".xlsx"):
        raise _error("仅支持 .xlsx 格式的吉客云报表", status=422)
    file_size = _integer(payload.get("fileSizeBytes"), "fileSizeBytes", 1, 20 * 1024 * 1024)
    raw_hash = _hex(payload.get("rawFileHash"), "rawFileHash")
    sheet_name = _text(payload.get("sheetName"), "sheetName", 255)
    source_row_count = _integer(payload.get("sourceRowCount"), "sourceRowCount", 1, MAX_ROWS)
    warnings = _warnings(payload.get("warnings"))
    totals = payload.get("totals")
    if not isinstance(totals, dict) or len(_json(totals).encode("utf-8")) > 64 * 1024:
        raise _error("totals 无效或超过上限")
    rows = _product_rows(payload.get("rows")) if source == "products" else _combo_rows(payload.get("rows"))
    if source_row_count < len(rows):
        raise _error("sourceRowCount 不能小于规范化行数")
    digest = content_hash(source, rows)
    scope_key = SOURCE_SCOPE_KEYS[source]
    attempt_id = uuid.uuid4()
    attempt_started = False
    try:
        with transaction.atomic():
            lock_active_authority()
            lock_erp_reference_for_replace()
            head = ErpReferenceImportScopeHead.objects.select_for_update().get(
                scope_key=scope_key, source_key=source
            )
            current_rows = product_rows_from_database() if source == "products" else combo_rows_from_database()
            current_digest = content_hash(source, current_rows)
            current_batch = ErpReferenceImportBatch.objects.filter(
                id=head.current_batch_id, source_key=source, status="completed"
            ).first()
            if current_batch is not None and current_digest == digest:
                _record_attempt(
                    source=source, raw_file_hash=raw_hash, outcome="duplicate",
                    actor_email=actor_email, file_name=file_name, file_size_bytes=file_size,
                    content=digest, row_count=len(rows), batch_id=current_batch.id,
                    import_hash=current_batch.file_hash, warnings=warnings,
                )
                return {
                    "ok": True, "status": "duplicate",
                    "message": "全部标准化 ERP 业务资料与当前数据一致，无需重复导入",
                    "batch": _batch_payload(current_batch), "warnings": warnings,
                }
            previous = head.state_token
            import_hash = _sha(f"erp-import-attempt-v1\n{scope_key}\n{digest}\n{previous}")
            batch_id = f"{source}:{import_hash}"
            published = _sha(
                f"erp-scope-state-v1\n{previous}\n{batch_id}\n{digest}\n{len(rows)}"
            )
            old_keys = (
                set(ErpProductMaster.objects.values_list("product_code", flat=True))
                if source == "products"
                else set(ErpComboItem.objects.values_list("parent_code", "child_code"))
            )
            new_keys = (
                {str(row["productCode"]) for row in rows}
                if source == "products"
                else {(str(row["parentCode"]), str(row["childCode"])) for row in rows}
            )
            updated_count = len(old_keys & new_keys)
            inserted_count = len(new_keys - old_keys)
            now = timezone.now()
            batch = ErpReferenceImportBatch.objects.create(
                id=batch_id, source_key=source, source_label=SOURCE_LABELS[source],
                file_name=file_name, file_size_bytes=file_size, file_hash=import_hash,
                raw_file_hash=raw_hash, content_hash=digest, scope_key=scope_key,
                published_state_token=published, sheet_name=sheet_name, status="processing",
                row_count=len(rows), inserted_count=inserted_count, updated_count=updated_count,
                warning_count=len(warnings), warnings_json=warnings,
                totals_json={**totals, "rawFileHash": raw_hash, "contentHash": digest,
                             "canonicalFormatVersion": IMPORT_VERSION},
                actor_email=actor_email[:320],
            )
            attempt_started = True
            ErpReferenceImportAttempt.objects.create(
                id=attempt_id, batch_id=batch_id, source_key=source, scope_key=scope_key,
                scope_json=SOURCE_SCOPES[source], import_hash=import_hash,
                raw_file_hash=raw_hash, content_hash=digest, row_count=len(rows),
                file_name=file_name, file_size_bytes=file_size, actor_email=actor_email[:320],
                warnings_json=warnings, outcome="processing",
            )
            if source == "products":
                old_categories = dict(ErpProductMaster.objects.values_list("product_code", "category"))
                ErpProductMaster.objects.all().delete()
                now_text = now.isoformat()
                ErpProductMaster.objects.bulk_create([
                    ErpProductMaster(
                        product_code=row["productCode"], product_name=row["productName"],
                        brand=row["brand"], specification=row["specification"], barcode=row["barcode"],
                        category=row["category"], supplier=row["supplier"],
                        product_status=row["productStatus"], source_row_number=row["sourceRowNumber"],
                        last_import_batch_id=batch_id, created_at=now_text, updated_at=now_text,
                    ) for row in rows
                ], batch_size=500)
                new_categories = {str(row["productCode"]): str(row["category"]).strip() for row in rows}
                changed = sorted(
                    code for code in set(old_categories) | set(new_categories)
                    if str(old_categories.get(code, "")).strip() != new_categories.get(code, "")
                )
                for offset in range(0, len(changed), 500):
                    codes = changed[offset : offset + 500]
                    for code in codes:
                        fallback = new_categories.get(code) or None
                        lines = SalesOrderLine.objects.filter(product_code=code)
                        if fallback:
                            lines.update(resolved_category=fallback)
                        else:
                            _restore_sales_category_fallback(lines)
            else:
                ErpComboItem.objects.all().delete()
                ErpComboItem.objects.bulk_create([
                    ErpComboItem(
                        parent_code=row["parentCode"], parent_name=row["parentName"],
                        child_code=row["childCode"], child_name=row["childName"],
                        child_quantity_milli=row["childQuantityMilli"],
                        source_row_number=row["sourceRowNumber"], last_import_batch_id=batch_id,
                    ) for row in rows
                ], batch_size=500)
            readback = product_rows_from_database() if source == "products" else combo_rows_from_database()
            if len(readback) != len(rows) or content_hash(source, readback) != digest:
                raise _error("ERP 主数据落库内容回查不一致", code="version_conflict", status=409)
            revision = SalesDataRevision.objects.select_for_update().get(domain="erp")
            revision.revision = int(revision.revision) + 1
            revision.source_digest = combined_database_digest()
            revision.save(update_fields=["revision", "source_digest", "updated_at"])
            batch.status = "completed"
            batch.completed_at = now
            batch.save(update_fields=["status", "completed_at"])
            ErpReferenceImportFingerprint.objects.create(
                batch_id=batch_id, source_key=source, scope_key=scope_key,
                scope_json=SOURCE_SCOPES[source], import_hash=import_hash,
                raw_file_hash=raw_hash, content_hash=digest, row_count=len(rows),
                published_state_token=published, outcome="imported",
            )
            head.state_token = published
            head.status = "ready"
            head.owner_token = ""
            head.generation = int(head.generation) + 1
            head.current_batch_id = batch_id
            head.owner_started_at = None
            head.heartbeat_at = None
            head.save()
            ErpReferenceImportAttempt.objects.filter(id=attempt_id).update(outcome="imported")
        return {
            "ok": True, "status": "imported",
            "message": "货品主数据导入成功" if source == "products" else "组合装及子件导入成功",
            "batch": _batch_payload(batch), "warnings": warnings,
            "verification": {"verified": True, "parsedRowCount": len(rows), "readbackRowCount": len(readback)},
        }
    except Exception as error:
        if attempt_started:
            try:
                with transaction.atomic():
                    lock_active_authority()
                    ErpReferenceImportAttempt.objects.update_or_create(
                        id=attempt_id,
                        defaults={
                            "source_key": source, "scope_key": scope_key,
                            "scope_json": SOURCE_SCOPES[source], "raw_file_hash": raw_hash,
                            "content_hash": digest, "row_count": len(rows), "outcome": "failed",
                            "actor_email": actor_email[:320], "file_name": file_name,
                            "file_size_bytes": file_size, "warnings_json": warnings,
                            "error_code": getattr(error, "code", "ERP_IMPORT_FAILED")[:80],
                        },
                    )
            except Exception:
                logger.exception("erp_import_failure_audit_failed")
        raise
