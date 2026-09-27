from __future__ import annotations

from decimal import Decimal
import hashlib
import json
import logging
import re
import uuid

from django.db import connection, transaction
from django.utils import timezone

from .errors import ProductsApiError
from .models import (
    ProductImportAttempt,
    ProductImportFingerprint,
    ProductImportScopeHead,
    ProductShippingRate,
    ProductShippingRateImportBatch,
)
from .revisions import bump_revision
from .write_requests import lock_active_authority


IMPORT_DOMAIN = "product-shipping-rates"
IMPORT_VERSION = "product-shipping-rates-normalized-v1"
SHEET_NAME = "SKU累计"
SCOPE = {"dataset": "sku_cumulative", "sheetName": SHEET_NAME}
LOCK_SCOPE = {"dataset": "sku_cumulative"}
SCOPE_KEY = "f0796d659d78eadb83280aff923663095d2a72709d99e3f4fae988b943afea63"
MAX_ROWS = 50_000
MAX_WARNINGS = 200
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
logger = logging.getLogger(__name__)


def _error(message: str, *, code: str = "invalid_request", status: int = 400) -> ProductsApiError:
    return ProductsApiError(message, code=code, status=status)


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
    if (not allow_empty and not normalized) or len(normalized) > maximum or any(
        ord(char) < 32 or ord(char) == 127 for char in normalized
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
        message = _text(raw.get("message"), "warning.message", 500)
        item: dict[str, object] = {"message": message}
        if "row" in raw:
            item["row"] = _integer(raw["row"], "warning.row", 1, 10_000_000)
        if "field" in raw:
            item["field"] = _text(raw["field"], "warning.field", 100)
        if "code" in raw:
            item["code"] = _text(raw["code"], "warning.code", 100)
        normalized.append(item)
    return normalized


def _normalize_rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value or len(value) > MAX_ROWS:
        raise _error(f"rows 必须是 1 到 {MAX_ROWS} 项的数组")
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {
            "productCode",
            "shippingRatePpt",
            "sourceRowNumber",
        }:
            raise _error("快递费率行字段集合无效")
        product_code = _text(raw["productCode"], "productCode", 512)
        if product_code in seen:
            raise _error("规范化快递费率包含重复规格代码")
        seen.add(product_code)
        ppt = _integer(raw["shippingRatePpt"], "shippingRatePpt", -(10**15), 10**15)
        source_row = _integer(raw["sourceRowNumber"], "sourceRowNumber", 1, 10_000_000)
        rows.append(
            {
                "productCode": product_code,
                "shippingRatePpt": ppt,
                "sourceRowNumber": source_row,
            }
        )
    return sorted(rows, key=lambda row: str(row["productCode"]).encode("utf-8"))


def _content_hash(rows: list[dict[str, object]]) -> str:
    row_digests = sorted(
        _sha(_json({"productCode": row["productCode"], "shippingRatePpt": row["shippingRatePpt"]}))
        for row in rows
    )
    return _sha(f"{IMPORT_VERSION}\n{_json(SCOPE)}\n{len(rows)}\n{''.join(row_digests)}")


def _state_token(previous: str, batch_id: str, content_hash: str, row_count: int) -> str:
    return _sha(f"product-scope-state-v1\n{previous}\n{batch_id}\n{content_hash}\n{row_count}")


def _attempt_hash(previous: str, content_hash: str) -> str:
    return _sha(f"product-import-attempt-v1\n{SCOPE_KEY}\n{content_hash}\n{previous}")


def _safe_metadata(payload: dict[str, object], warnings: list[dict[str, object]]) -> dict[str, object]:
    return {
        "fileName": str(payload.get("fileName") or "")[:255],
        "fileSizeBytes": payload.get("fileSizeBytes") if isinstance(payload.get("fileSizeBytes"), int) else 0,
        "warnings": warnings,
        "contractVersion": IMPORT_VERSION,
    }


def _batch_payload(batch: ProductShippingRateImportBatch) -> dict[str, object]:
    return {
        "id": batch.id,
        "source": batch.source,
        "fileName": batch.file_name,
        "fileSizeBytes": int(batch.file_size_bytes),
        "fileHash": batch.file_hash,
        "rawFileHash": batch.raw_file_hash,
        "contentHash": batch.content_hash,
        "sheetName": batch.sheet_name,
        "actor": batch.actor_email,
        "status": batch.status,
        "sourceRowCount": int(batch.source_row_count),
        "rowCount": int(batch.row_count),
        "insertedCount": int(batch.inserted_count),
        "updatedCount": int(batch.updated_count),
        "duplicateCount": int(batch.duplicate_count),
        "warningCount": int(batch.warning_count),
        "warnings": batch.warnings_json,
        "totals": batch.totals_json,
        "createdAt": batch.created_at,
        "completedAt": batch.completed_at,
    }


def list_import_batches(page: int, page_size: int) -> dict[str, object]:
    if not 1 <= page <= 10_000 or not 1 <= page_size <= 100:
        raise _error("分页参数无效")
    rows = ProductShippingRateImportBatch.objects.order_by("-created_at", "-id")
    total = rows.count()
    offset = (page - 1) * page_size
    items = list(rows[offset : offset + page_size])
    return {
        "items": [_batch_payload(batch) for batch in items],
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "returned": len(items),
            "truncated": offset + len(items) < total,
        },
    }


def record_rejection(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    allowed = {
        "version",
        "kind",
        "fileName",
        "fileSizeBytes",
        "rawFileHash",
        "errors",
        "warnings",
    }
    if set(payload) != allowed or payload.get("version") != IMPORT_VERSION or payload.get("kind") != "rejection":
        raise _error("拒绝审计契约字段集合无效")
    raw_hash = _hex(payload["rawFileHash"], "rawFileHash")
    file_name = _text(payload["fileName"], "fileName", 255)
    file_size = _integer(payload["fileSizeBytes"], "fileSizeBytes", 0, 128 * 1024 * 1024)
    warnings = _warnings(payload["warnings"])
    errors = _warnings(payload["errors"])
    if not errors:
        raise _error("拒绝审计必须包含错误")
    with transaction.atomic():
        lock_active_authority()
        ProductImportAttempt.objects.create(
            batch_id="",
            scope_key="",
            scope_json={"dataset": "sku_cumulative", "sheetName": SHEET_NAME},
            raw_file_hash=raw_hash,
            content_hash="",
            row_count=0,
            outcome="rejected",
            error_code=str(errors[0].get("code") or "PREVALIDATION_REJECTED")[:64],
            actor_email=actor_email,
            metadata={
                "fileName": file_name,
                "fileSizeBytes": file_size,
                "errors": errors,
                "warnings": warnings,
            },
            completed_at=timezone.now(),
        )
    return {
        "ok": False,
        "status": "rejected",
        "message": str(errors[0]["message"]),
        "warnings": warnings,
        "errors": errors,
        "errorCount": len(errors),
    }


def _lock_scope() -> None:
    if connection.vendor != "postgresql":
        return
    key = int.from_bytes(hashlib.sha256(f"products-scope\n{SCOPE_KEY}".encode()).digest()[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


def import_shipping_rates(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    allowed = {
        "version",
        "kind",
        "fileName",
        "fileSizeBytes",
        "rawFileHash",
        "sheetName",
        "sourceRowCount",
        "duplicateCount",
        "rows",
        "warnings",
        "totals",
    }
    if set(payload) != allowed or payload.get("version") != IMPORT_VERSION or payload.get("kind") != "import":
        raise _error("商品快递费率导入契约字段集合无效")
    file_name = _text(payload["fileName"], "fileName", 255)
    if not file_name.lower().endswith(".xlsx"):
        raise _error("仅支持 .xlsx 格式的 SKU 快递费率报表")
    file_size = _integer(payload["fileSizeBytes"], "fileSizeBytes", 1, 20 * 1024 * 1024)
    raw_hash = _hex(payload["rawFileHash"], "rawFileHash")
    if payload["sheetName"] != SHEET_NAME:
        raise _error("SKU 快递费率工作表必须精确命名为 SKU累计")
    source_row_count = _integer(payload["sourceRowCount"], "sourceRowCount", 1, 10_000_000)
    duplicate_count = _integer(payload["duplicateCount"], "duplicateCount", 0, 10_000_000)
    warnings = _warnings(payload["warnings"])
    if not isinstance(payload["totals"], dict) or len(payload["totals"]) > 50:
        raise _error("totals 无效")
    rows = _normalize_rows(payload["rows"])
    content_hash = _content_hash(rows)
    attempt_id = uuid.uuid4().hex
    attempt_started = False
    attempt_metadata = _safe_metadata(payload, warnings)
    try:
        with transaction.atomic():
            lock_active_authority()
            _lock_scope()
            head = ProductImportScopeHead.objects.select_for_update().get(scope_key=SCOPE_KEY)
            attempt = ProductImportAttempt.objects.create(
                id=attempt_id,
                batch_id="",
                scope_key=SCOPE_KEY,
                scope_json=SCOPE,
                raw_file_hash=raw_hash,
                content_hash=content_hash,
                row_count=len(rows),
                outcome="processing",
                actor_email=actor_email,
                metadata=attempt_metadata,
            )
            attempt_started = True
            current_count = ProductShippingRate.objects.count()
            current_batch = (
                ProductShippingRateImportBatch.objects.filter(
                    id=head.current_batch_id, status="completed"
                ).first()
                if head.current_batch_id
                else None
            )
            if head.current_batch_id and current_batch is None:
                raise _error(
                    "SKU 快递费率范围头指向无效批次",
                    code="version_conflict",
                    status=409,
                )
            current_rows = list(
                ProductShippingRate.objects.order_by("product_code").values_list(
                    "product_code", "shipping_rate"
                )
            )
            current_content_hash = (
                _content_hash(
                    [
                        {
                            "productCode": code,
                            "shippingRatePpt": int(
                                (Decimal(rate) * Decimal(10**12)).to_integral_exact()
                            ),
                            "sourceRowNumber": 0,
                        }
                        for code, rate in current_rows
                    ]
                )
                if current_rows
                else ""
            )
            if current_batch is None and current_count:
                raise _error(
                    "SKU 快递费率存在无批次所有者的事实",
                    code="version_conflict",
                    status=409,
                )
            if current_batch is not None and (
                current_count != int(current_batch.row_count)
                or current_content_hash != current_batch.content_hash
            ):
                raise _error(
                    "SKU 快递费率当前批次与事实摘要不一致",
                    code="version_conflict",
                    status=409,
                )
            if (
                current_batch is not None
                and current_batch.content_hash == content_hash
                and int(current_batch.row_count) == len(rows)
            ):
                attempt.batch_id = current_batch.id
                attempt.outcome = "duplicate"
                attempt.completed_at = timezone.now()
                attempt.save(update_fields=["batch_id", "outcome", "completed_at"])
                return {
                    "ok": True,
                    "status": "duplicate",
                    "message": "全部规格代码及快递费率与当前已发布数据一致，无需重复导入",
                    "batch": _batch_payload(current_batch),
                    "warnings": warnings,
                    "verification": {
                        "verified": True,
                        "parsedRowCount": len(rows),
                        "readbackRowCount": current_count,
                    },
                }

            owner_token = uuid.uuid4().hex
            previous_token = head.state_token
            generation = int(head.generation) + 1
            attempt_hash = _attempt_hash(previous_token, content_hash)
            batch_id = f"sku-shipping-rates:{attempt_hash}"
            existing_codes = set(ProductShippingRate.objects.values_list("product_code", flat=True))
            incoming_codes = {str(row["productCode"]) for row in rows}
            updated_count = len(existing_codes & incoming_codes)
            inserted_count = len(incoming_codes - existing_codes)
            published_token = _state_token(previous_token, batch_id, content_hash, len(rows))
            now_text = timezone.now().isoformat()

            head.status = "processing"
            head.owner_token = owner_token
            head.current_batch_id = batch_id
            head.generation = generation
            head.owner_started_at = timezone.now()
            head.heartbeat_at = timezone.now()
            head.save()
            batch, created = ProductShippingRateImportBatch.objects.get_or_create(
                id=batch_id,
                defaults={
                    "source": "sku_cumulative",
                    "file_name": file_name,
                    "file_size_bytes": file_size,
                    "file_hash": attempt_hash,
                    "raw_file_hash": raw_hash,
                    "content_hash": content_hash,
                    "scope_key": SCOPE_KEY,
                    "published_state_token": published_token,
                    "sheet_name": SHEET_NAME,
                    "actor_email": actor_email,
                    "status": "processing",
                    "source_row_count": source_row_count,
                    "row_count": len(rows),
                    "inserted_count": inserted_count,
                    "updated_count": updated_count,
                    "duplicate_count": duplicate_count,
                    "warning_count": len(warnings),
                    "warnings_json": warnings,
                    "totals_json": {
                        **payload["totals"],
                        "rawFileHash": raw_hash,
                        "contentHash": content_hash,
                        "canonicalFormatVersion": IMPORT_VERSION,
                    },
                    "created_at": now_text,
                },
            )
            if not created:
                raise _error("确定性商品导入批次已存在", code="version_conflict", status=409)
            ProductShippingRate.objects.all().delete()
            ProductShippingRate.objects.bulk_create(
                [
                    ProductShippingRate(
                        product_code=str(row["productCode"]),
                        shipping_rate=Decimal(int(row["shippingRatePpt"])) / Decimal(10**12),
                        source_row_number=int(row["sourceRowNumber"]),
                        last_import_batch_id=batch_id,
                    )
                    for row in rows
                ],
                batch_size=500,
            )
            readback = list(
                ProductShippingRate.objects.order_by("product_code").values_list(
                    "product_code", "shipping_rate"
                )
            )
            readback_rows = [
                {
                    "productCode": code,
                    "shippingRatePpt": int(
                        (Decimal(rate) * Decimal(10**12)).to_integral_exact()
                    ),
                    "sourceRowNumber": 0,
                }
                for code, rate in readback
            ]
            if len(readback_rows) != len(rows) or _content_hash(readback_rows) != content_hash:
                raise _error("SKU 快递费率落库内容回查不一致", code="version_conflict", status=409)
            batch.status = "completed"
            batch.completed_at = now_text
            batch.save(update_fields=["status", "completed_at"])
            ProductImportFingerprint.objects.update_or_create(
                batch_id=batch_id,
                defaults={
                    "scope_key": SCOPE_KEY,
                    "scope_json": SCOPE,
                    "import_hash": attempt_hash,
                    "content_hash": content_hash,
                    "raw_file_hash": raw_hash,
                    "row_count": len(rows),
                    "published_state_token": published_token,
                    "status": "completed",
                    "publication_sequence": generation,
                },
            )
            head.state_token = published_token
            head.status = "ready"
            head.owner_token = ""
            head.owner_started_at = None
            head.heartbeat_at = None
            head.save()
            bump_revision(
                {
                    "kind": "shipping_rates",
                    "batchId": batch_id,
                    "contentHash": content_hash,
                    "rowCount": len(rows),
                }
            )
            attempt.batch_id = batch_id
            attempt.outcome = "imported"
            attempt.completed_at = timezone.now()
            attempt.save(update_fields=["batch_id", "outcome", "completed_at"])
        return {
            "ok": True,
            "status": "imported",
            "message": f"SKU 快递费率导入成功，共发布 {len(rows)} 个规格",
            "batch": _batch_payload(batch),
            "warnings": warnings,
            "verification": {
                "verified": True,
                "parsedRowCount": len(rows),
                "readbackRowCount": len(readback),
            },
        }
    except Exception as error:
        if attempt_started:
            try:
                with transaction.atomic():
                    lock_active_authority()
                    ProductImportAttempt.objects.update_or_create(
                        id=attempt_id,
                        defaults={
                            "batch_id": "",
                            "scope_key": SCOPE_KEY,
                            "scope_json": SCOPE,
                            "raw_file_hash": raw_hash,
                            "content_hash": content_hash,
                            "row_count": len(rows),
                            "outcome": "failed",
                            "error_code": (
                                error.code
                                if isinstance(error, ProductsApiError)
                                else "PRODUCT_SHIPPING_RATE_IMPORT_FAILED"
                            )[:64],
                            "actor_email": actor_email,
                            "metadata": attempt_metadata,
                            "completed_at": timezone.now(),
                        },
                    )
            except Exception as audit_error:
                # Preserve the original import failure. Authority or database
                # loss must not be hidden by a best-effort failure audit.
                logger.exception(
                    "products_import_failure_audit_failed type=%s",
                    type(audit_error).__name__,
                )
        raise


def import_product_payload(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    if payload.get("kind") == "rejection":
        return record_rejection(payload, actor_email)
    return import_shipping_rates(payload, actor_email)
