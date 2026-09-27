"""PostgreSQL-owned sales import staging, fencing, and atomic publication."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.db.models import Count, Max
from django.utils import timezone

from erp_reference.locking import lock_erp_reference_for_sales_read

from .authority_lock import acquire_sales_write_authority_shared_lock
from .models import (
    ErpProductMaster,
    SalesDataRevision,
    SalesImportAttempt,
    SalesImportBatch,
    SalesImportFingerprint,
    SalesImportScopeHead,
    SalesOrderLine,
    SalesRawUploadChunk,
    SalesRawUploadSession,
    SalesStagedImportChunk,
    SalesStagedImportSession,
    SalesWriteAuthority,
    sales_projection_values,
)
from .policy import (
    approved_sales_channels,
    excluded_sales_warehouses,
    zero_cost_product_names,
)
from .runtime_guard import WriterRuntimeGuardError, validate_writer_runtime_state


SALES_IMPORT_SOURCE = "吉客云 ERP · 销售单明细账"
SALES_UPLOAD_CHUNK_BYTES = 2 * 1024 * 1024
MAX_CHUNKED_SALES_FILE_BYTES = 128 * 1024 * 1024
MAX_SALES_IMPORT_RANGE_DAYS = 366
MAX_SALES_IMPORT_CHANNELS = 50
MAX_SALES_IMPORT_ROWS = 500_000
MAX_STAGED_CHUNKS = 1_000
MAX_ROWS_PER_STAGED_CHUNK = 1_000
UPLOAD_TTL = timedelta(hours=24)
STALE_OWNER_AGE = timedelta(minutes=30)
JS_SAFE_INTEGER = 9_007_199_254_740_991
HEX_64_RE = re.compile(r"^[a-f0-9]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SalesImportServiceError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_request",
        status: int = 422,
        issues: Sequence[Mapping[str, object]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.issues = list(issues or [])[:200]


@dataclass(frozen=True)
class PreparedImport:
    session_id: str
    actor_email: str
    file_name: str
    file_size_bytes: int
    raw_file_hash: str
    sheet_name: str
    start_date: date
    end_date: date
    channels: tuple[str, ...] | None
    rows: list[dict[str, object]]
    warnings: list[dict[str, object]]
    totals: dict[str, object]
    scope: dict[str, object]
    scope_key: str
    content_hash: str


@dataclass(frozen=True)
class ScopeReservation:
    attempt_id: str
    batch_id: str
    previous_state_token: str
    recovered_from_attempt_id: str


STRING_FIELDS = {
    "sourceLineKey": 2_000,
    "sourceRowHash": 2_000,
    "orderNo": 2_000,
    "onlineOrderNo": 2_000,
    "channel": 500,
    "platform": 500,
    "shopName": 1_000,
    "logisticsCompany": 1_000,
    "warehouse": 1_000,
    "productCode": 1_000,
    "onlineSpecCode": 1_000,
    "productName": 2_000,
    "specification": 2_000,
    "barcode": 1_000,
    "supplier": 2_000,
    "category": 1_000,
    "orderTime": 100,
    "salesTime": 100,
    "shipTime": 100,
    "lineShipTime": 100,
    "businessType": 20,
}
INTEGER_FIELDS = (
    "sourceRowNumber",
    "quantity",
    "listUnitPriceCents",
    "costAmountCents",
    "allocatedUnitPriceCents",
    "allocatedAmountCents",
    "feeAllocationCents",
    "grossProfitCents",
    "grossMarginBps",
    "untaxedGrossProfitCents",
    "untaxedGrossMarginBps",
)
ROW_FIELDS = frozenset((*STRING_FIELDS, *INTEGER_FIELDS))


def lock_active_write_authority() -> SalesWriteAuthority:
    """Fence and verify the singleton authority inside the caller's transaction."""

    acquire_sales_write_authority_shared_lock()
    try:
        authority = SalesWriteAuthority.objects.get(id=1)
    except SalesWriteAuthority.DoesNotExist as error:
        raise SalesImportServiceError(
            "PostgreSQL 销售写入权威门禁尚未初始化",
            code="sales_write_authority_unavailable",
            status=503,
        ) from error
    if authority.status != "active":
        raise SalesImportServiceError(
            "PostgreSQL 尚未取得销售唯一写入权",
            code="sales_write_authority_inactive",
            status=503,
        )
    expected_epoch = str(getattr(settings, "SALES_WRITE_AUTHORITY_EPOCH", "") or "")
    expected_cutover = str(getattr(settings, "SALES_WRITE_CUTOVER_ID", "") or "")
    if (
        not expected_epoch
        or not expected_cutover
        or str(authority.authority_epoch) != expected_epoch
        or authority.cutover_id != expected_cutover
    ):
        raise SalesImportServiceError(
            "PostgreSQL 销售写入权威的 epoch/cutover 配置不匹配",
            code="sales_write_authority_mismatch",
            status=503,
        )
    try:
        validate_writer_runtime_state(cutover_id=authority.cutover_id)
    except WriterRuntimeGuardError as error:
        raise SalesImportServiceError(
            "PostgreSQL 销售写入运行时安全门禁未就绪",
            code="sales_writer_runtime_guard_unavailable",
            status=503,
        ) from error
    return authority


def _lock_idempotency_key(namespace: str, material: object) -> None:
    """Serialize absent-row idempotency checks on PostgreSQL.

    ``select_for_update`` cannot lock a row that does not exist.  A transaction
    advisory lock closes that first-create gap without preventing a new session
    after the previous one expires.
    """

    if connection.vendor != "postgresql":
        return
    digest = hashlib.sha256(
        f"{namespace}\n{_canonical_json(material)}".encode("utf-8")
    ).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


def _safe_file_name(value: object) -> str:
    name = str(value or "sales-ledger.xlsx").replace("\\", "/").split("/")[-1]
    return re.sub(r"[\x00-\x1f\x7f]", "", name)[:255]


def _parse_date(value: object, label: str) -> date:
    text = str(value or "").strip()
    if not DATE_RE.fullmatch(text):
        raise SalesImportServiceError(
            f"{label}必须为真实的 YYYY-MM-DD 自然日",
            code="INVALID_EXPECTED_DATE_RANGE",
        )
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise SalesImportServiceError(
            f"{label}必须为真实的 YYYY-MM-DD 自然日",
            code="INVALID_EXPECTED_DATE_RANGE",
        ) from error
    if parsed.year < 1900 or parsed.year > 2199:
        raise SalesImportServiceError(
            f"{label}必须为真实的 YYYY-MM-DD 自然日",
            code="INVALID_EXPECTED_DATE_RANGE",
        )
    return parsed


def validate_import_date_range(start_value: object, end_value: object) -> tuple[date, date]:
    start_date = _parse_date(start_value, "开始日期")
    end_date = _parse_date(end_value, "结束日期")
    if start_date > end_date:
        raise SalesImportServiceError(
            "开始日期不能晚于结束日期",
            code="INVALID_EXPECTED_DATE_RANGE",
        )
    if (end_date - start_date).days + 1 > MAX_SALES_IMPORT_RANGE_DAYS:
        raise SalesImportServiceError(
            f"单次销售导入日期范围最多 {MAX_SALES_IMPORT_RANGE_DAYS} 天",
            code="EXPECTED_DATE_RANGE_TOO_LARGE",
        )
    return start_date, end_date


def validate_import_channels(value: object) -> tuple[str, ...] | None:
    if value in (None, ""):
        return None
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_SALES_IMPORT_CHANNELS:
        raise SalesImportServiceError(
            f"expectedChannels 必须包含 1 到 {MAX_SALES_IMPORT_CHANNELS} 个销售渠道",
            code="INVALID_EXPECTED_CHANNELS",
        )
    channels = [item.strip() if isinstance(item, str) else "" for item in value]
    if any(not item or len(item) > 100 for item in channels):
        raise SalesImportServiceError(
            "expectedChannels 只能包含非空且不超过 100 字符的渠道名",
            code="INVALID_EXPECTED_CHANNELS",
        )
    if len(set(channels)) != len(channels):
        raise SalesImportServiceError(
            "expectedChannels 不能包含重复渠道",
            code="DUPLICATE_EXPECTED_CHANNELS",
        )
    approved = approved_sales_channels()
    approved_set = set(approved)
    unapproved = [item for item in channels if item not in approved_set]
    if unapproved:
        raise SalesImportServiceError(
            f"expectedChannels 包含未纳入白名单的渠道：{'、'.join(unapproved)}",
            code="UNAPPROVED_EXPECTED_CHANNELS",
        )
    rank = {item: index for index, item in enumerate(approved)}
    return tuple(sorted(channels, key=lambda item: rank[item]))


def _bounded_integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise SalesImportServiceError(f"{label}必须是整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise SalesImportServiceError(f"{label}必须是整数") from error
    if parsed < minimum or parsed > maximum:
        raise SalesImportServiceError(f"{label}必须在 {minimum} 到 {maximum} 之间")
    return parsed


def _hex_64(value: object, label: str) -> str:
    text = str(value or "").strip().lower()
    if not HEX_64_RE.fullmatch(text):
        raise SalesImportServiceError(f"{label}必须是 64 位小写 SHA-256")
    return text


def _canonical_value(value: object, ignored: frozenset[str], *, top_level: bool) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SalesImportServiceError("导入内容包含非有限数字")
        return 0 if value == 0 else value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item, ignored, top_level=False) for item in value]
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(value[key], ignored, top_level=False)
            for key in sorted(value, key=lambda item: str(item))
            if not (top_level and str(key) in ignored)
        }
    raise SalesImportServiceError(f"导入内容包含不支持的字段类型：{type(value).__name__}")


def _canonical_json(value: object, ignored: frozenset[str] = frozenset()) -> str:
    return json.dumps(
        _canonical_value(value, ignored, top_level=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _encode_part(value: str) -> str:
    return f"{len(value.encode('utf-8'))}:{value}"


def _scope_material(start_date: date, end_date: date, channels: tuple[str, ...] | None) -> dict[str, object]:
    scope: dict[str, object] = {
        "source": "sales_ledger",
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
    }
    if channels is not None:
        scope["channels"] = list(channels)
    return scope


def _scope_key() -> str:
    domain = "sales"
    lock_scope = _canonical_json({"source": "sales_ledger"})
    return _sha256_text(
        f"import-lock-scope-v1\n{_encode_part(domain)}{_encode_part(lock_scope)}"
    )


def _content_hash(scope: Mapping[str, object], rows: Sequence[Mapping[str, object]]) -> str:
    ignored = frozenset({"sourceRowNumber", "sourceLineKey", "sourceRowHash"})
    row_hashes = sorted(_sha256_text(_canonical_json(row, ignored)) for row in rows)
    scope_json = _canonical_json(scope)
    payload = (
        f"import-content-v3\n{_encode_part(scope_json)}{len(row_hashes)}\n"
        + "".join(row_hashes)
    )
    return _sha256_text(payload)


def _attempt_hash(scope_key: str, content_hash: str, state_token: str) -> str:
    return _sha256_text(
        "".join(
            _encode_part(item)
            for item in (
                "import-attempt-v1",
                "sales",
                scope_key,
                content_hash,
                state_token.strip() or "initial",
            )
        )
    )


def _next_state_token(previous: str, batch_id: str, content_hash: str, row_count: int) -> str:
    return _sha256_text(
        "".join(
            _encode_part(item)
            for item in (
                "import-scope-state-v2",
                previous.strip() or "initial",
                batch_id,
                content_hash,
                str(row_count),
            )
        )
    )


def sanitize_issues(values: object) -> list[dict[str, object]]:
    if not isinstance(values, list):
        return []
    sanitized: list[dict[str, object]] = []
    for value in values[:200]:
        if isinstance(value, str):
            sanitized.append({"message": value[:500]})
            continue
        if not isinstance(value, Mapping):
            sanitized.append({"message": str(value)[:500]})
            continue
        message = str(value.get("message") or value.get("reason") or value.get("code") or "数据校验失败")
        issue: dict[str, object] = {"message": message[:500]}
        raw_row = value.get("row", value.get("rowNumber", value.get("sourceRowNumber")))
        try:
            numeric_row = int(raw_row) if not isinstance(raw_row, bool) else 0
        except (TypeError, ValueError):
            numeric_row = 0
        if numeric_row > 0:
            issue["row"] = numeric_row
        for field in ("column", "field", "code"):
            if isinstance(value.get(field), str):
                issue[field] = str(value[field])[:100]
        sanitized.append(issue)
    return sanitized


def _normalize_row(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise SalesImportServiceError("销售明细行必须是对象", code="INVALID_ROW")
    unknown = set(value) - ROW_FIELDS
    if unknown:
        raise SalesImportServiceError(
            f"销售明细包含未声明字段：{', '.join(sorted(str(item) for item in unknown)[:10])}",
            code="INVALID_ROW",
        )
    row: dict[str, object] = {}
    for field, maximum in STRING_FIELDS.items():
        raw = value.get(field, "")
        text = "" if raw is None else str(raw)
        if len(text) > maximum:
            raise SalesImportServiceError(f"{field} 超出长度上限", code="INVALID_ROW")
        row[field] = text
    for field in INTEGER_FIELDS:
        raw = value.get(field)
        if isinstance(raw, bool) or not isinstance(raw, int) or abs(raw) > JS_SAFE_INTEGER:
            raise SalesImportServiceError(
                f"{field} 必须是安全整数",
                code="INVALID_INTEGER",
            )
        row[field] = raw
    if int(row["sourceRowNumber"]) <= 0:
        raise SalesImportServiceError("sourceRowNumber 必须为正整数", code="INVALID_INTEGER")
    if not row["sourceLineKey"] or not row["sourceRowHash"]:
        raise SalesImportServiceError("明细行缺少唯一标识", code="MISSING_ROW_KEY")
    if not row["orderNo"] and not row["onlineOrderNo"]:
        raise SalesImportServiceError(
            "销售单号和线上单号不能同时为空",
            code="MISSING_ORDER_NO",
        )
    if row["businessType"] not in {"sale", "return", "zero"}:
        raise SalesImportServiceError("业务类型无效", code="INVALID_BUSINESS_TYPE")
    if not row["shipTime"]:
        row["shipTime"] = row["orderTime"]
    for field in ("salesTime", "shipTime"):
        text = str(row[field])
        try:
            date.fromisoformat(text[:10])
        except ValueError as error:
            raise SalesImportServiceError(
                f"{field} 必须以有效业务日期开头",
                code="INVALID_SALES_TIME" if field == "salesTime" else "INVALID_SHIP_TIME",
            ) from error
    return row


def _validate_system_cost_snapshot(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise SalesImportServiceError("systemCostSnapshot 格式无效")
    source_batch_id = str(value.get("sourceBatchId") or "").strip()
    snapshot_date = _parse_date(value.get("snapshotDate"), "系统成本快照日期")
    costs = value.get("costs")
    if not source_batch_id or len(source_batch_id) > 200 or not isinstance(costs, list):
        raise SalesImportServiceError("systemCostSnapshot 缺少批次或成本集合")
    if len(costs) > 100_000:
        raise SalesImportServiceError("systemCostSnapshot 成本集合超出上限")
    normalized_costs: list[dict[str, object]] = []
    for item in costs:
        if not isinstance(item, Mapping):
            raise SalesImportServiceError("系统成本行格式无效")
        product_code = str(item.get("productCode") or "").strip()
        warehouse = str(item.get("warehouse") or "").strip()
        unit_cost = item.get("unitCostCents")
        if (
            not product_code
            or len(product_code) > 1_000
            or len(warehouse) > 1_000
            or isinstance(unit_cost, bool)
            or not isinstance(unit_cost, int)
            or unit_cost <= 0
            or unit_cost > JS_SAFE_INTEGER
        ):
            raise SalesImportServiceError("系统成本行包含无效货品、仓库或固定成本价")
        normalized_costs.append(
            {"productCode": product_code, "warehouse": warehouse, "unitCostCents": unit_cost}
        )
    return {
        "sourceBatchId": source_batch_id,
        "snapshotDate": snapshot_date.isoformat(),
        "costs": normalized_costs,
    }


def _raw_upload_payload(
    session: SalesRawUploadSession,
    *,
    include_chunks: bool = True,
    include_owner: bool = False,
) -> dict[str, object]:
    chunks = list(session.chunks.order_by("chunk_index")) if include_chunks else []
    payload: dict[str, object] = {
        "id": str(session.id),
        "fingerprint": session.client_fingerprint,
        "fileName": session.file_name,
        "fileSizeBytes": session.file_size_bytes,
        "chunkSizeBytes": session.chunk_size_bytes,
        "chunkCount": session.chunk_count,
        "receivedChunkIndexes": [item.chunk_index for item in chunks],
        "receivedBytes": session.received_bytes,
        "status": session.status,
        "ownerGeneration": session.owner_generation,
        "resultBatchId": session.result_batch_id or None,
        "expiresAt": session.expires_at.isoformat(),
        "chunks": [
            {
                "chunkIndex": item.chunk_index,
                "objectKey": item.object_key,
                "sizeBytes": item.size_bytes,
                "sha256": item.sha256,
            }
            for item in chunks
        ],
    }
    if include_owner and session.owner_token:
        payload["ownerToken"] = session.owner_token
    if session.status == "completed" and session.result_batch_id:
        batch = SalesImportBatch.objects.filter(
            id=session.result_batch_id, status="completed"
        ).first()
        staged = (
            session.normalized_imports.filter(
                status="completed", result_batch_id=session.result_batch_id
            )
            .order_by("-updated_at")
            .first()
        )
        if batch and staged:
            attempt = (
                SalesImportAttempt.objects.filter(session_id=str(staged.id))
                .order_by("-created_at")
                .first()
            )
            batch_result = batch_payload(batch)
            payload["result"] = {
                "ok": True,
                "status": "duplicate" if attempt and attempt.outcome == "duplicate" else "imported",
                "message": "规范化销售资料已完成发布",
                "batch": batch_result,
                "warnings": batch_result["warnings"],
            }
    return payload


def read_raw_upload(upload_id: object, actor_email: str) -> dict[str, object]:
    try:
        session = SalesRawUploadSession.objects.get(id=upload_id, actor_email=actor_email)
    except (SalesRawUploadSession.DoesNotExist, ValueError) as error:
        raise SalesImportServiceError("上传会话不存在", code="not_found", status=404) from error
    return _raw_upload_payload(session)


def begin_raw_upload(payload: Mapping[str, object], actor_email: str) -> dict[str, object]:
    file_name = _safe_file_name(payload.get("fileName"))
    if not file_name.lower().endswith(".xlsx"):
        raise SalesImportServiceError("仅支持 .xlsx 格式的销售单明细账")
    file_size = _bounded_integer(
        payload.get("fileSizeBytes"), "fileSizeBytes", 1, MAX_CHUNKED_SALES_FILE_BYTES
    )
    chunk_count = _bounded_integer(payload.get("chunkCount"), "chunkCount", 1, 64)
    expected_count = math.ceil(file_size / SALES_UPLOAD_CHUNK_BYTES)
    if chunk_count != expected_count:
        raise SalesImportServiceError("分片数量与文件大小不一致")
    fingerprint = str(payload.get("fingerprint") or "").strip()
    if not fingerprint or len(fingerprint) > 255:
        raise SalesImportServiceError("上传指纹无效")
    start_date, end_date = validate_import_date_range(
        payload.get("expectedStartDate"), payload.get("expectedEndDate")
    )
    channels = validate_import_channels(payload.get("expectedChannels"))
    now = timezone.now()
    with transaction.atomic():
        lock_active_write_authority()
        _lock_idempotency_key(
            "sales-raw-upload-v1",
            {
                "actor": actor_email,
                "fingerprint": fingerprint,
                "startDate": start_date,
                "endDate": end_date,
                "channels": channels,
            },
        )
        existing_query = SalesRawUploadSession.objects.select_for_update().filter(
                client_fingerprint=fingerprint,
                actor_email=actor_email,
                expected_start_date=start_date,
                expected_end_date=end_date,
                expires_at__gt=now,
                status__in=["uploading", "ready", "processing"],
            )
        existing_query = (
            existing_query.filter(expected_channels=list(channels))
            if channels is not None
            else existing_query.filter(expected_channels__isnull=True)
        )
        existing = (
            existing_query
            .order_by("created_at")
            .first()
        )
        if existing:
            if (
                existing.file_name != file_name
                or existing.file_size_bytes != file_size
                or existing.chunk_count != chunk_count
            ):
                raise SalesImportServiceError(
                    "上传指纹已绑定不同文件元数据",
                    code="conflict",
                    status=409,
                )
            return _raw_upload_payload(existing)
        session = SalesRawUploadSession.objects.create(
            client_fingerprint=fingerprint,
            actor_email=actor_email,
            file_name=file_name,
            file_size_bytes=file_size,
            chunk_size_bytes=SALES_UPLOAD_CHUNK_BYTES,
            chunk_count=chunk_count,
            expected_start_date=start_date,
            expected_end_date=end_date,
            expected_channels=list(channels) if channels is not None else None,
            expires_at=now + UPLOAD_TTL,
        )
    return _raw_upload_payload(session)


def register_raw_upload_chunk(payload: Mapping[str, object], actor_email: str) -> dict[str, object]:
    upload_id = payload.get("uploadId")
    chunk_index = _bounded_integer(payload.get("chunkIndex"), "chunkIndex", 0, 63)
    size_bytes = _bounded_integer(
        payload.get("sizeBytes"), "sizeBytes", 1, SALES_UPLOAD_CHUNK_BYTES
    )
    checksum = _hex_64(payload.get("sha256"), "sha256")
    object_key = str(payload.get("objectKey") or "")
    encoded_payload = payload.get("contentBase64")
    maximum_encoded_bytes = ((SALES_UPLOAD_CHUNK_BYTES + 2) // 3) * 4
    if (
        not isinstance(encoded_payload, str)
        or not encoded_payload
        or len(encoded_payload) > maximum_encoded_bytes
        or len(encoded_payload) % 4 != 0
    ):
        raise SalesImportServiceError("分片内容编码无效", status=400)
    try:
        chunk_payload = base64.b64decode(encoded_payload, validate=True)
    except (binascii.Error, ValueError) as error:
        raise SalesImportServiceError("分片内容编码无效", status=400) from error
    if base64.b64encode(chunk_payload).decode("ascii") != encoded_payload:
        raise SalesImportServiceError("分片内容编码不是规范 Base64", status=400)
    if len(chunk_payload) != size_bytes:
        raise SalesImportServiceError("分片内容大小与声明不一致")
    if hashlib.sha256(chunk_payload).hexdigest() != checksum:
        raise SalesImportServiceError("分片内容校验码与声明不一致")
    with transaction.atomic():
        lock_active_write_authority()
        try:
            session = SalesRawUploadSession.objects.select_for_update().get(
                id=upload_id, actor_email=actor_email
            )
        except (SalesRawUploadSession.DoesNotExist, ValueError) as error:
            raise SalesImportServiceError("上传会话不存在", code="not_found", status=404) from error
        if session.expires_at <= timezone.now():
            raise SalesImportServiceError("上传会话已过期", code="not_found", status=404)
        if session.status not in {"uploading", "ready"}:
            raise SalesImportServiceError(
                "销售文件已开始合并，不能继续登记分片", code="conflict", status=409
            )
        if chunk_index >= session.chunk_count:
            raise SalesImportServiceError("分片序号无效")
        expected_bytes = (
            session.file_size_bytes - session.chunk_size_bytes * (session.chunk_count - 1)
            if chunk_index == session.chunk_count - 1
            else session.chunk_size_bytes
        )
        if size_bytes != expected_bytes:
            raise SalesImportServiceError("分片大小与预期不一致")
        prefix = f"sales-upload/{session.id}/{chunk_index:06d}-{checksum}-"
        if not re.fullmatch(
            re.escape(prefix)
            + r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            object_key,
            re.IGNORECASE,
        ):
            raise SalesImportServiceError("分片存储键与会话、序号或摘要不一致")
        previous = SalesRawUploadChunk.objects.filter(
            session=session, chunk_index=chunk_index
        ).first()
        discarded_object_key = None
        if previous:
            if (
                previous.size_bytes != size_bytes
                or previous.sha256 != checksum
                or bytes(previous.payload) != chunk_payload
            ):
                raise SalesImportServiceError(
                    "同一分片序号已绑定不同内容，请重新创建上传会话",
                    code="conflict",
                    status=409,
                )
            if previous.object_key != object_key:
                # Keep the first authoritative storage identity. PostgreSQL has
                # already verified that the retried payload is byte-identical.
                discarded_object_key = object_key
        else:
            SalesRawUploadChunk.objects.create(
                session=session,
                chunk_index=chunk_index,
                object_key=object_key,
                size_bytes=size_bytes,
                sha256=checksum,
                payload=chunk_payload,
            )
        aggregate = session.chunks.aggregate(total=Count("id"), size=Max("chunk_index"))
        # Count and byte sum are deliberately recalculated from authoritative rows.
        chunks = list(session.chunks.only("size_bytes", "chunk_index"))
        session.received_chunk_count = int(aggregate["total"] or 0)
        session.received_bytes = sum(item.size_bytes for item in chunks)
        session.status = "ready" if session.received_chunk_count == session.chunk_count else "uploading"
        session.expires_at = timezone.now() + UPLOAD_TTL
        session.save(
            update_fields=[
                "received_chunk_count",
                "received_bytes",
                "status",
                "expires_at",
                "updated_at",
            ]
        )
        response = _raw_upload_payload(session)
        response["discardedObjectKey"] = discarded_object_key
        return response


def read_raw_upload_chunk(
    payload: Mapping[str, object], actor_email: str
) -> dict[str, object]:
    upload_id = payload.get("uploadId")
    chunk_index = _bounded_integer(payload.get("chunkIndex"), "chunkIndex", 0, 63)
    owner_token = str(payload.get("ownerToken") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", owner_token):
        raise SalesImportServiceError("ownerToken 无效", code="conflict", status=409)
    with transaction.atomic():
        lock_active_write_authority()
        try:
            session = SalesRawUploadSession.objects.select_for_update().get(
                id=upload_id, actor_email=actor_email
            )
        except (SalesRawUploadSession.DoesNotExist, ValueError) as error:
            raise SalesImportServiceError(
                "上传会话不存在", code="not_found", status=404
            ) from error
        if session.expires_at <= timezone.now():
            raise SalesImportServiceError("上传会话已过期", code="not_found", status=404)
        if session.status != "processing" or session.owner_token != owner_token:
            raise SalesImportServiceError(
                "上传分片不属于当前处理 owner", code="conflict", status=409
            )
        try:
            chunk = session.chunks.get(chunk_index=chunk_index)
        except SalesRawUploadChunk.DoesNotExist as error:
            raise SalesImportServiceError(
                "上传分片不存在", code="not_found", status=404
            ) from error
        chunk_payload = bytes(chunk.payload)
        if (
            len(chunk_payload) != chunk.size_bytes
            or hashlib.sha256(chunk_payload).hexdigest() != chunk.sha256
        ):
            raise SalesImportServiceError(
                "PostgreSQL 上传分片完整性校验失败", code="conflict", status=409
            )
        return {
            "chunkIndex": chunk.chunk_index,
            "sizeBytes": chunk.size_bytes,
            "sha256": chunk.sha256,
            "contentBase64": base64.b64encode(chunk_payload).decode("ascii"),
        }


def claim_raw_upload(upload_id: object, actor_email: str) -> dict[str, object]:
    with transaction.atomic():
        lock_active_write_authority()
        try:
            session = SalesRawUploadSession.objects.select_for_update().get(
                id=upload_id, actor_email=actor_email
            )
        except (SalesRawUploadSession.DoesNotExist, ValueError) as error:
            raise SalesImportServiceError("上传会话不存在", code="not_found", status=404) from error
        if session.expires_at <= timezone.now():
            raise SalesImportServiceError("上传会话已过期", code="not_found", status=404)
        if session.status == "processing" and session.updated_at > timezone.now() - STALE_OWNER_AGE:
            raise SalesImportServiceError("销售文件正在被另一个请求处理", code="conflict", status=409)
        if session.status == "completed":
            return _raw_upload_payload(session)
        if session.status not in {"ready", "processing"} or (
            session.received_chunk_count != session.chunk_count
        ):
            raise SalesImportServiceError("仍有分片尚未上传完成", code="conflict", status=409)
        chunks = list(session.chunks.order_by("chunk_index"))
        if [item.chunk_index for item in chunks] != list(range(session.chunk_count)):
            raise SalesImportServiceError("分片序号不连续", code="conflict", status=409)
        if sum(item.size_bytes for item in chunks) != session.file_size_bytes:
            raise SalesImportServiceError("分片总大小与文件不一致", code="conflict", status=409)
        session.status = "processing"
        session.owner_token = uuid.uuid4().hex
        session.owner_generation += 1
        session.save(
            update_fields=["status", "owner_token", "owner_generation", "updated_at"]
        )
        return _raw_upload_payload(session, include_owner=True)


def finish_raw_upload(
    upload_id: object,
    actor_email: str,
    *,
    owner_token: object,
    completed: bool,
    result_batch_id: object | None = None,
) -> dict[str, object]:
    supplied_owner = str(owner_token or "").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", supplied_owner):
        raise SalesImportServiceError("ownerToken 无效", code="conflict", status=409)
    with transaction.atomic():
        lock_active_write_authority()
        try:
            session = SalesRawUploadSession.objects.select_for_update().get(
                id=upload_id, actor_email=actor_email
            )
        except (SalesRawUploadSession.DoesNotExist, ValueError) as error:
            raise SalesImportServiceError("上传会话不存在", code="not_found", status=404) from error
        supplied_batch_id = str(result_batch_id or "").strip()
        if session.status == "completed" and completed:
            if not supplied_batch_id or supplied_batch_id != session.result_batch_id:
                raise SalesImportServiceError(
                    "已完成上传的结果批次不匹配", code="conflict", status=409
                )
            return _raw_upload_payload(session)
        if session.status != "processing" or session.owner_token != supplied_owner:
            raise SalesImportServiceError(
                "上传会话不在处理状态或已被新 owner 接管", code="conflict", status=409
            )
        if completed:
            if not supplied_batch_id:
                raise SalesImportServiceError("完成上传缺少结果批次", code="conflict", status=409)
            linked = session.normalized_imports.filter(
                status="completed", result_batch_id=supplied_batch_id
            ).exists()
            if not linked or not SalesImportBatch.objects.filter(
                id=supplied_batch_id, status="completed"
            ).exists():
                raise SalesImportServiceError(
                    "上传会话缺少已发布的结果批次", code="conflict", status=409
                )
        elif supplied_batch_id:
            raise SalesImportServiceError("未完成上传不能绑定结果批次", code="conflict", status=409)
        session.status = "completed" if completed else "ready"
        session.owner_token = ""
        session.result_batch_id = supplied_batch_id if completed else ""
        session.expires_at = timezone.now() + UPLOAD_TTL
        session.save(
            update_fields=[
                "status",
                "owner_token",
                "result_batch_id",
                "expires_at",
                "updated_at",
            ]
        )
        return _raw_upload_payload(session)


def cleanup_raw_upload_chunks(upload_id: object, actor_email: str) -> dict[str, object]:
    with transaction.atomic():
        lock_active_write_authority()
        try:
            session = SalesRawUploadSession.objects.select_for_update().get(
                id=upload_id, actor_email=actor_email
            )
        except (SalesRawUploadSession.DoesNotExist, ValueError) as error:
            raise SalesImportServiceError("上传会话不存在", code="not_found", status=404) from error
        if session.status != "completed":
            raise SalesImportServiceError("只能清理已完成上传会话", code="conflict", status=409)
        object_keys = list(session.chunks.order_by("chunk_index").values_list("object_key", flat=True))
        session.chunks.all().delete()
        return {"id": str(session.id), "status": session.status, "removedObjectKeys": object_keys}


def list_expired_raw_uploads(_maintenance_actor_email: str, limit: object = 10) -> dict[str, object]:
    bounded_limit = _bounded_integer(limit, "limit", 1, 25)
    with transaction.atomic():
        lock_active_write_authority()
        sessions = list(
            SalesRawUploadSession.objects.select_for_update()
            .filter(expires_at__lte=timezone.now())
            .exclude(status="expired")
            .order_by("expires_at")[:bounded_limit]
        )
        items: list[dict[str, object]] = []
        for session in sessions:
            if session.status != "cleaning":
                session.status = "cleaning"
                session.owner_token = uuid.uuid4().hex
                session.owner_generation += 1
                session.save(
                    update_fields=[
                        "status",
                        "owner_token",
                        "owner_generation",
                        "updated_at",
                    ]
                )
            items.append(
                {
                    "id": str(session.id),
                    "ownerGeneration": session.owner_generation,
                    "cleanupToken": session.owner_token,
                }
            )
        return {"items": items}


def purge_expired_raw_upload(
    upload_id: object,
    actor_email: str,
    *,
    owner_generation: object,
    cleanup_token: object,
) -> dict[str, object]:
    generation = _bounded_integer(owner_generation, "ownerGeneration", 0, JS_SAFE_INTEGER)
    supplied_cleanup_token = str(cleanup_token or "").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", supplied_cleanup_token):
        raise SalesImportServiceError("cleanupToken 无效", status=400)
    with transaction.atomic():
        lock_active_write_authority()
        try:
            session = SalesRawUploadSession.objects.select_for_update().get(id=upload_id)
        except (SalesRawUploadSession.DoesNotExist, ValueError) as error:
            raise SalesImportServiceError(
                "上传会话不存在", code="not_found", status=404
            ) from error
        if (
            session.status != "cleaning"
            or session.owner_generation != generation
            or session.owner_token != supplied_cleanup_token
        ):
            raise SalesImportServiceError(
                "过期上传清理栅栏已变化", code="conflict", status=409
            )
        for staged in session.normalized_imports.select_for_update():
            staged.chunks.all().delete()
            if staged.status != "completed":
                staged.status = "expired"
                staged.owner_token = ""
                staged.save(update_fields=["status", "owner_token", "updated_at"])
        session.chunks.all().delete()
        session.status = "expired"
        session.owner_token = ""
        session.received_chunk_count = 0
        session.received_bytes = 0
        # PostgreSQL owns both metadata and bytes, so the fenced transaction is
        # terminal; no external object-store orphan recheck is required.
        session.expires_at = timezone.now()
        session.save(
            update_fields=[
                "status",
                "owner_token",
                "received_chunk_count",
                "received_bytes",
                "expires_at",
                "updated_at",
            ]
        )
        # Direct-upload staging rows can be pruned in the same bounded
        # maintenance transaction.
        expired_stage_ids = list(
            SalesStagedImportSession.objects.filter(
                raw_upload__isnull=True, expires_at__lte=timezone.now()
            )
            .order_by("expires_at")
            .values_list("id", flat=True)[:25]
        )
        for staged in SalesStagedImportSession.objects.select_for_update().filter(
            id__in=expired_stage_ids
        ):
            staged.chunks.all().delete()
            if staged.status != "completed":
                staged.status = "expired"
                staged.owner_token = ""
                staged.save(update_fields=["status", "owner_token", "updated_at"])
        return {"id": str(upload_id), "status": "purged"}


def _staged_session_payload(session: SalesStagedImportSession) -> dict[str, object]:
    received_indexes = (
        list(range(session.chunk_count))
        if session.status == "completed"
        else list(
            session.chunks.order_by("chunk_index").values_list("chunk_index", flat=True)
        )
    )
    return {
        "id": str(session.id),
        "rawUploadId": str(session.raw_upload_id) if session.raw_upload_id else None,
        "fingerprint": session.client_fingerprint,
        "fileName": session.file_name,
        "fileSizeBytes": session.file_size_bytes,
        "rawFileHash": session.raw_file_hash,
        "sheetName": session.sheet_name,
        "expectedStartDate": session.expected_start_date.isoformat(),
        "expectedEndDate": session.expected_end_date.isoformat(),
        "expectedChannels": session.expected_channels,
        "chunkCount": session.chunk_count,
        "receivedChunkCount": session.received_chunk_count,
        "receivedRowCount": session.received_row_count,
        "receivedChunkIndexes": received_indexes,
        "status": session.status,
        "resultBatchId": session.result_batch_id or None,
        "expiresAt": session.expires_at.isoformat(),
    }


def read_staged_import(session_id: object, actor_email: str) -> dict[str, object]:
    try:
        session = SalesStagedImportSession.objects.get(id=session_id, actor_email=actor_email)
    except (SalesStagedImportSession.DoesNotExist, ValueError) as error:
        raise SalesImportServiceError("规范化导入会话不存在", code="not_found", status=404) from error
    return _staged_session_payload(session)


def _verify_staged_raw_owner(
    session: SalesStagedImportSession,
    supplied_owner: object | None = None,
    *,
    heartbeat: bool = True,
) -> None:
    supplied = str(supplied_owner or "").strip()
    if session.raw_upload_id is None:
        if supplied:
            raise SalesImportServiceError(
                "直接导入会话不能携带 raw owner token", code="conflict", status=409
            )
        return
    try:
        raw_upload = SalesRawUploadSession.objects.select_for_update().get(
            id=session.raw_upload_id,
            actor_email=session.actor_email,
        )
    except SalesRawUploadSession.DoesNotExist as error:
        raise SalesImportServiceError(
            "原始上传会话不存在", code="not_found", status=404
        ) from error
    expected = session.raw_upload_owner_token
    if (
        raw_upload.status != "processing"
        or not expected
        or raw_upload.owner_token != expected
        or session.raw_upload_owner_generation != raw_upload.owner_generation
        or (supplied_owner is not None and supplied != expected)
    ):
        raise SalesImportServiceError(
            "原始上传会话已被其他 owner 接管", code="conflict", status=409
        )
    if heartbeat:
        raw_upload.save(update_fields=["updated_at"])


def _record_prevalidation_rejection(
    *,
    actor_email: str,
    raw_file_hash: str,
    file_name: str,
    file_size_bytes: int,
    issues: list[dict[str, object]],
    session_id: str = "",
) -> None:
    hint = {"stage": "prevalidation", "sessionId": session_id or None}
    scope_key = _sha256_text(
        f"import-rejected-scope-v1\n{_encode_part('sales')}{_encode_part(_canonical_json(hint))}"
    )
    content_hash = _sha256_text(
        f"import-rejected-content-v1\n{_encode_part(_canonical_json(hint))}{_encode_part(raw_file_hash)}"
    )
    SalesImportAttempt.objects.create(
        id=uuid.uuid4().hex,
        session_id=session_id,
        scope_key=scope_key,
        scope_json=hint,
        raw_file_hash=raw_file_hash,
        content_hash=content_hash,
        file_name=file_name,
        file_size_bytes=file_size_bytes,
        actor_email=actor_email,
        warnings=issues,
        outcome="rejected",
        error_code=str(issues[0].get("code") if issues else "IMPORT_REJECTED")[:100],
    )


def begin_staged_import(payload: Mapping[str, object], actor_email: str) -> dict[str, object]:
    file_name = _safe_file_name(payload.get("fileName"))
    if not file_name.lower().endswith(".xlsx"):
        raise SalesImportServiceError("仅支持 .xlsx 格式的销售单明细账")
    file_size = _bounded_integer(
        payload.get("fileSizeBytes"), "fileSizeBytes", 1, MAX_CHUNKED_SALES_FILE_BYTES
    )
    raw_file_hash = _hex_64(payload.get("rawFileHash"), "rawFileHash")
    sheet_name = str(payload.get("sheetName") or "")[:255]
    if not sheet_name:
        raise SalesImportServiceError("销售工作表名称不能为空")
    client_fingerprint = str(payload.get("fingerprint") or "").strip()
    if not client_fingerprint or len(client_fingerprint) > 255:
        raise SalesImportServiceError("规范化导入指纹无效")
    chunk_count = _bounded_integer(
        payload.get("chunkCount"), "chunkCount", 1, MAX_STAGED_CHUNKS
    )
    start_date, end_date = validate_import_date_range(
        payload.get("expectedStartDate"), payload.get("expectedEndDate")
    )
    channels = validate_import_channels(payload.get("expectedChannels"))
    source_totals = payload.get("sourceTotals")
    if not isinstance(source_totals, Mapping):
        raise SalesImportServiceError("sourceTotals 必须是对象")
    # Validate JSON/domain-safe values before opening a write transaction.
    _canonical_json(source_totals)
    warnings = sanitize_issues(payload.get("parserWarnings"))
    parser_errors = sanitize_issues(payload.get("parserErrors"))
    system_cost_snapshot = _validate_system_cost_snapshot(payload.get("systemCostSnapshot"))
    raw_upload_id = payload.get("rawUploadId")
    now = timezone.now()
    if parser_errors:
        with transaction.atomic():
            lock_active_write_authority()
            _record_prevalidation_rejection(
                actor_email=actor_email,
                raw_file_hash=raw_file_hash,
                file_name=file_name,
                file_size_bytes=file_size,
                issues=[*parser_errors, *warnings],
            )
        raise SalesImportServiceError(
            "文件校验未通过，未创建规范化导入会话",
            code=str(parser_errors[0].get("code") or "IMPORT_REJECTED"),
            issues=parser_errors,
        )
    with transaction.atomic():
        lock_active_write_authority()
        raw_upload = None
        raw_owner = ""
        if raw_upload_id:
            try:
                raw_upload = SalesRawUploadSession.objects.select_for_update().get(
                    id=raw_upload_id, actor_email=actor_email
                )
            except (SalesRawUploadSession.DoesNotExist, ValueError) as error:
                raise SalesImportServiceError(
                    "原始上传会话不存在", code="not_found", status=404
                ) from error
            if raw_upload.status not in {"processing", "completed"}:
                raise SalesImportServiceError(
                    "原始上传会话尚未完成分片接管", code="conflict", status=409
                )
            raw_owner = str(payload.get("rawUploadOwnerToken") or "").strip()
            if raw_upload.status == "processing" and (
                not raw_owner or raw_upload.owner_token != raw_owner
            ):
                raise SalesImportServiceError(
                    "原始上传会话已被其他 owner 接管", code="conflict", status=409
                )
            if raw_upload.file_name != file_name or raw_upload.file_size_bytes != file_size:
                raise SalesImportServiceError(
                    "规范化导入与原始上传文件身份不一致", code="conflict", status=409
                )
            if (
                raw_upload.expected_start_date != start_date
                or raw_upload.expected_end_date != end_date
                or raw_upload.expected_channels
                != (list(channels) if channels is not None else None)
            ):
                raise SalesImportServiceError(
                    "规范化导入与原始上传业务范围不一致", code="conflict", status=409
                )
        _lock_idempotency_key(
            "sales-staged-import-v1",
            {
                "actor": actor_email,
                "fingerprint": client_fingerprint,
                "rawFileHash": raw_file_hash,
                "startDate": start_date,
                "endDate": end_date,
                "channels": channels,
            },
        )
        existing_query = SalesStagedImportSession.objects.select_for_update().filter(
                client_fingerprint=client_fingerprint,
                actor_email=actor_email,
                raw_file_hash=raw_file_hash,
                expected_start_date=start_date,
                expected_end_date=end_date,
                expires_at__gt=now,
                status__in=["uploading", "ready", "processing", "completed"],
            )
        existing_query = (
            existing_query.filter(expected_channels=list(channels))
            if channels is not None
            else existing_query.filter(expected_channels__isnull=True)
        )
        existing = (
            existing_query
            .order_by("created_at")
            .first()
        )
        if existing:
            if (
                existing.chunk_count != chunk_count
                or existing.file_size_bytes != file_size
                or existing.file_name != file_name
                or existing.sheet_name != sheet_name
                or existing.raw_upload_id != (raw_upload.id if raw_upload else None)
                or _canonical_json(existing.source_totals)
                != _canonical_json(source_totals)
                or _canonical_json(existing.parser_warnings)
                != _canonical_json(warnings)
                or _canonical_json(existing.system_cost_snapshot)
                != _canonical_json(system_cost_snapshot)
            ):
                raise SalesImportServiceError(
                    "规范化导入指纹已绑定不同元数据", code="conflict", status=409
                )
            if existing.status == "completed":
                return _staged_session_payload(existing)
            if raw_upload is not None:
                if raw_upload.status != "processing":
                    raise SalesImportServiceError(
                        "已完成的原始上传只能回查已完成规范化会话",
                        code="conflict",
                        status=409,
                    )
                if existing.raw_upload_owner_token != raw_owner:
                    if raw_upload.owner_generation <= existing.raw_upload_owner_generation:
                        raise SalesImportServiceError(
                            "规范化导入会话仍由上一 raw owner 处理",
                            code="conflict",
                            status=409,
                        )
                    existing.raw_upload_owner_token = raw_owner
                    existing.raw_upload_owner_generation = raw_upload.owner_generation
                    existing.status = (
                        "ready"
                        if existing.received_chunk_count == existing.chunk_count
                        else "uploading"
                    )
                    existing.owner_token = ""
                    existing.expires_at = now + UPLOAD_TTL
                    existing.save(
                        update_fields=[
                            "raw_upload_owner_token",
                            "raw_upload_owner_generation",
                            "status",
                            "owner_token",
                            "expires_at",
                            "updated_at",
                        ]
                    )
            return _staged_session_payload(existing)
        if raw_upload is not None and raw_upload.status != "processing":
            raise SalesImportServiceError(
                "已完成的原始上传不能创建新的规范化会话",
                code="conflict",
                status=409,
            )
        session = SalesStagedImportSession.objects.create(
            raw_upload=raw_upload,
            raw_upload_owner_token=raw_owner,
            raw_upload_owner_generation=raw_upload.owner_generation if raw_upload else 0,
            client_fingerprint=client_fingerprint,
            actor_email=actor_email,
            file_name=file_name,
            file_size_bytes=file_size,
            raw_file_hash=raw_file_hash,
            sheet_name=sheet_name,
            expected_start_date=start_date,
            expected_end_date=end_date,
            expected_channels=list(channels) if channels is not None else None,
            chunk_count=chunk_count,
            source_totals=dict(source_totals),
            parser_warnings=warnings,
            system_cost_snapshot=system_cost_snapshot,
            expires_at=now + UPLOAD_TTL,
        )
    return _staged_session_payload(session)


def stage_normalized_chunk(payload: Mapping[str, object], actor_email: str) -> dict[str, object]:
    session_id = payload.get("sessionId")
    chunk_index = _bounded_integer(
        payload.get("chunkIndex"), "chunkIndex", 0, MAX_STAGED_CHUNKS - 1
    )
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not 1 <= len(raw_rows) <= MAX_ROWS_PER_STAGED_CHUNK:
        raise SalesImportServiceError(
            f"每个规范化分片必须包含 1 到 {MAX_ROWS_PER_STAGED_CHUNK} 行"
        )
    normalized_rows = [_normalize_row(item) for item in raw_rows]
    content_hash = _sha256_text(_canonical_json(normalized_rows))
    with transaction.atomic():
        lock_active_write_authority()
        try:
            session = SalesStagedImportSession.objects.select_for_update().get(
                id=session_id, actor_email=actor_email
            )
        except (SalesStagedImportSession.DoesNotExist, ValueError) as error:
            raise SalesImportServiceError(
                "规范化导入会话不存在", code="not_found", status=404
            ) from error
        if session.expires_at <= timezone.now():
            raise SalesImportServiceError("规范化导入会话已过期", code="not_found", status=404)
        if session.status not in {"uploading", "ready"}:
            raise SalesImportServiceError(
                "规范化销售集合已开始处理，不能继续登记分片", code="conflict", status=409
            )
        _verify_staged_raw_owner(session, payload.get("rawUploadOwnerToken"))
        if chunk_index >= session.chunk_count:
            raise SalesImportServiceError("规范化分片序号无效")
        existing = SalesStagedImportChunk.objects.filter(
            session=session, chunk_index=chunk_index
        ).first()
        other_rows = session.received_row_count - (existing.row_count if existing else 0)
        if other_rows + len(normalized_rows) > MAX_SALES_IMPORT_ROWS:
            raise SalesImportServiceError(
                f"销售明细总行数超过 {MAX_SALES_IMPORT_ROWS} 行上限",
                code="ROW_LIMIT",
            )
        if existing and existing.content_hash == content_hash:
            return _staged_session_payload(session)
        if existing:
            existing.row_count = len(normalized_rows)
            existing.content_hash = content_hash
            existing.rows = normalized_rows
            existing.save(update_fields=["row_count", "content_hash", "rows"])
        else:
            SalesStagedImportChunk.objects.create(
                session=session,
                chunk_index=chunk_index,
                row_count=len(normalized_rows),
                content_hash=content_hash,
                rows=normalized_rows,
            )
        aggregate = session.chunks.aggregate(total=Count("id"))
        session.received_chunk_count = int(aggregate["total"] or 0)
        session.received_row_count = sum(
            session.chunks.values_list("row_count", flat=True)
        )
        session.status = "ready" if session.received_chunk_count == session.chunk_count else "uploading"
        session.expires_at = timezone.now() + UPLOAD_TTL
        session.save(
            update_fields=[
                "received_chunk_count",
                "received_row_count",
                "status",
                "expires_at",
                "updated_at",
            ]
        )
        return _staged_session_payload(session)


def _claim_staged_session(
    session_id: object,
    actor_email: str,
    raw_upload_owner_token: object | None = None,
) -> tuple[SalesStagedImportSession, str] | tuple[dict[str, object], None]:
    with transaction.atomic():
        lock_active_write_authority()
        try:
            session = SalesStagedImportSession.objects.select_for_update().get(
                id=session_id, actor_email=actor_email
            )
        except (SalesStagedImportSession.DoesNotExist, ValueError) as error:
            raise SalesImportServiceError(
                "规范化导入会话不存在", code="not_found", status=404
            ) from error
        if session.status == "completed":
            attempt = (
                SalesImportAttempt.objects.filter(session_id=str(session.id))
                .order_by("-created_at")
                .first()
            )
            batch = SalesImportBatch.objects.filter(id=session.result_batch_id).first()
            if not batch:
                raise SalesImportServiceError(
                    "已完成会话缺少批次回查", code="internal_error", status=500
                )
            batch_result = batch_payload(batch)
            return {
                "ok": True,
                "status": "duplicate" if attempt and attempt.outcome == "duplicate" else "imported",
                "message": "规范化销售资料已完成发布",
                "batch": batch_result,
                "warnings": batch_result["warnings"],
            }, None
        _verify_staged_raw_owner(session, raw_upload_owner_token)
        if session.expires_at <= timezone.now():
            raise SalesImportServiceError("规范化导入会话已过期", code="not_found", status=404)
        if session.status == "processing":
            if session.updated_at > timezone.now() - STALE_OWNER_AGE:
                raise SalesImportServiceError(
                    "规范化销售集合正在被另一个请求处理", code="conflict", status=409
                )
            session.status = "ready"
            session.owner_token = ""
        if session.status != "ready" or session.received_chunk_count != session.chunk_count:
            raise SalesImportServiceError("仍有规范化分片尚未上传完成", code="conflict", status=409)
        indexes = list(session.chunks.order_by("chunk_index").values_list("chunk_index", flat=True))
        if indexes != list(range(session.chunk_count)):
            raise SalesImportServiceError("规范化分片序号不连续", code="conflict", status=409)
        owner_token = uuid.uuid4().hex
        session.status = "processing"
        session.owner_token = owner_token
        session.save(update_fields=["status", "owner_token", "updated_at"])
        return session, owner_token


def _business_date_from_row(row: Mapping[str, object]) -> date:
    return date.fromisoformat(str(row["shipTime"])[:10])


def _clean_zero_cost_rows(
    rows: list[dict[str, object]], snapshot: object
) -> tuple[list[dict[str, object]], dict[str, object] | None, list[dict[str, object]], set[int]]:
    cleanable = [
        row
        for row in rows
        if row["costAmountCents"] == 0
        and row["productCode"] != "ERP_PRICE_ADJUSTMENT"
        and str(row["productName"]).strip() not in set(zero_cost_product_names())
    ]
    if not cleanable:
        return rows, None, [], set()
    normalized_snapshot = _validate_system_cost_snapshot(snapshot)
    if normalized_snapshot is None:
        raise SalesImportServiceError(
            "检测到货品成本为 0 的销售明细，但没有可用的系统成本快照",
            code="MISSING_SYSTEM_COST_SNAPSHOT",
            issues=[
                {
                    "code": "MISSING_SYSTEM_COST_SNAPSHOT",
                    "field": "costAmountCents",
                    "message": "请先同步包含正固定成本价的系统成本快照，再重新导入销售明细",
                }
            ],
        )
    by_product_warehouse: dict[tuple[str, str], set[int]] = {}
    by_product: dict[str, set[int]] = {}
    for item in normalized_snapshot["costs"]:  # type: ignore[index]
        product = str(item["productCode"]).strip().upper()
        warehouse = str(item["warehouse"]).strip()
        unit_cost = int(item["unitCostCents"])
        by_product_warehouse.setdefault((product, warehouse), set()).add(unit_cost)
        by_product.setdefault(product, set()).add(unit_cost)
    cleaned_rows: list[dict[str, object]] = []
    cleaned_numbers: set[int] = set()
    unresolved: list[dict[str, object]] = []
    warehouse_matches = 0
    product_matches = 0
    skipped = 0
    for source in rows:
        row = dict(source)
        if row["costAmountCents"] != 0:
            cleaned_rows.append(row)
            continue
        if (
            row["productCode"] == "ERP_PRICE_ADJUSTMENT"
            or str(row["productName"]).strip() in set(zero_cost_product_names())
        ):
            skipped += 1
            cleaned_rows.append(row)
            continue
        product = str(row["productCode"]).strip().upper()
        warehouse = str(row["warehouse"]).strip()
        warehouse_costs = by_product_warehouse.get((product, warehouse))
        kind = ""
        unit_cost: int | None = None
        if warehouse_costs and len(warehouse_costs) == 1:
            kind = "warehouse"
            unit_cost = next(iter(warehouse_costs))
        elif warehouse_costs and len(warehouse_costs) > 1:
            kind = "ambiguous"
        else:
            product_costs = by_product.get(product)
            if product_costs and len(product_costs) == 1:
                kind = "product"
                unit_cost = next(iter(product_costs))
            elif product_costs and len(product_costs) > 1:
                kind = "ambiguous"
            else:
                kind = "missing"
        if unit_cost is None:
            unresolved.append(
                {
                    "sourceRowNumber": row["sourceRowNumber"],
                    "productCode": row["productCode"],
                    "productName": row["productName"],
                    "warehouse": row["warehouse"],
                    "reason": "AMBIGUOUS_SYSTEM_COST" if kind == "ambiguous" else "MISSING_SYSTEM_COST",
                }
            )
            cleaned_rows.append(row)
            continue
        cost_amount = unit_cost * int(row["quantity"])
        gross_profit = int(row["allocatedAmountCents"]) - cost_amount - int(row["feeAllocationCents"])
        margin = 0 if int(row["allocatedAmountCents"]) == 0 else math.floor(
            gross_profit / int(row["allocatedAmountCents"]) * 10_000 + 0.5
        )
        if any(abs(value) > JS_SAFE_INTEGER for value in (cost_amount, gross_profit, margin)):
            unresolved.append(
                {
                    "sourceRowNumber": row["sourceRowNumber"],
                    "productCode": row["productCode"],
                    "productName": row["productName"],
                    "warehouse": row["warehouse"],
                    "reason": "INVALID_CLEANED_AMOUNT",
                }
            )
            cleaned_rows.append(row)
            continue
        row.update(
            {
                "costAmountCents": cost_amount,
                "grossProfitCents": gross_profit,
                "grossMarginBps": margin,
                "untaxedGrossProfitCents": gross_profit,
                "untaxedGrossMarginBps": margin,
            }
        )
        cleaned_numbers.add(int(row["sourceRowNumber"]))
        warehouse_matches += int(kind == "warehouse")
        product_matches += int(kind == "product")
        cleaned_rows.append(row)
    proof = {
        "sourceBatchId": normalized_snapshot["sourceBatchId"],
        "snapshotDate": normalized_snapshot["snapshotDate"],
        "cleanedRows": len(cleaned_numbers),
        "matchedByWarehouseRows": warehouse_matches,
        "matchedByProductFallbackRows": product_matches,
        "skippedPriceAdjustmentRows": skipped,
        "unresolvedRows": len(unresolved),
    }
    warnings: list[dict[str, object]] = []
    if cleaned_numbers:
        warnings.append(
            {
                "code": "SYSTEM_COST_CLEANED",
                "message": f"已按系统成本快照 {proof['snapshotDate']} 清洗 {len(cleaned_numbers)} 行原始成本为 0 的销售明细",
            }
        )
    if unresolved:
        samples = "、".join(
            list(dict.fromkeys(str(item["productCode"] or item["productName"]) for item in unresolved))[:8]
        )
        warnings.append(
            {
                "code": "SYSTEM_COST_UNRESOLVED",
                "message": f"系统成本快照未匹配 {len(unresolved)} 行 0 成本明细，已保留原始 0 成本继续导入"
                + (f"；样例：{samples}" if samples else ""),
            }
        )
    if product_matches:
        warnings.append(
            {
                "code": "SYSTEM_COST_PRODUCT_FALLBACK",
                "message": f"{product_matches} 行未匹配到同仓成本，已使用货品唯一系统成本",
            }
        )
    return cleaned_rows, proof, warnings, cleaned_numbers


def _safe_total(total: int, value: int, label: str) -> int:
    result = total + value
    if abs(result) > JS_SAFE_INTEGER:
        raise SalesImportServiceError(f"{label}汇总超出安全整数范围")
    return result


def _stored_totals(
    source_totals: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
    *,
    raw_file_hash: str,
    excluded_warehouse_rows: int,
    excluded_future_rows: int,
    system_cost: Mapping[str, object] | None,
    scope: Mapping[str, object],
    content_hash: str,
) -> dict[str, object]:
    totals = dict(source_totals)
    totals.update(
        {
            "rowCount": len(rows),
            "saleRowCount": 0,
            "returnRowCount": 0,
            "quantity": 0,
            "netSalesCents": 0,
            "costAmountCents": 0,
            "feeAllocationCents": 0,
            "grossProfitCents": 0,
            "untaxedGrossProfitCents": 0,
            "rawFileHash": raw_file_hash,
            "excludedBrushWarehouseRows": excluded_warehouse_rows,
            "excludedFutureDateRows": excluded_future_rows,
            "importScope": {
                "startDate": scope["startDate"],
                "endDate": scope["endDate"],
                "channels": scope.get("channels"),
            },
            "contentHash": content_hash,
        }
    )
    for row in rows:
        if row["businessType"] == "return":
            totals["returnRowCount"] = int(totals["returnRowCount"]) + 1
        else:
            totals["saleRowCount"] = int(totals["saleRowCount"]) + 1
        for key, label in (
            ("quantity", "销售数量"),
            ("allocatedAmountCents", "销售金额"),
            ("costAmountCents", "货品成本"),
            ("feeAllocationCents", "费用分摊"),
            ("grossProfitCents", "毛利"),
            ("untaxedGrossProfitCents", "未税毛利"),
        ):
            target = "netSalesCents" if key == "allocatedAmountCents" else key
            totals[target] = _safe_total(int(totals[target]), int(row[key]), label)
    if system_cost is not None:
        totals["systemCost"] = dict(system_cost)
    return totals


def _prepare_import(session: SalesStagedImportSession) -> PreparedImport:
    chunks = list(session.chunks.order_by("chunk_index"))
    if len(chunks) != session.chunk_count or [item.chunk_index for item in chunks] != list(
        range(session.chunk_count)
    ):
        raise SalesImportServiceError("规范化分片不完整", code="conflict", status=409)
    rows: list[dict[str, object]] = []
    for chunk in chunks:
        if not isinstance(chunk.rows, list) or len(chunk.rows) != chunk.row_count:
            raise SalesImportServiceError("规范化分片行数证据不一致", code="conflict", status=409)
        if _sha256_text(_canonical_json(chunk.rows)) != chunk.content_hash:
            raise SalesImportServiceError("规范化分片摘要不一致", code="conflict", status=409)
        rows.extend(_normalize_row(item) for item in chunk.rows)
    if not rows or len(rows) > MAX_SALES_IMPORT_ROWS:
        raise SalesImportServiceError("工作表中没有可导入的销售明细行", code="NO_DATA_ROWS")
    shanghai_today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    cutoff = shanghai_today - timedelta(days=1)
    today_rows = [row for row in rows if _business_date_from_row(row) == shanghai_today]
    invalid_future = [row for row in rows if _business_date_from_row(row) > shanghai_today]
    if invalid_future:
        raise SalesImportServiceError(
            "文件校验未通过，未写入任何销售数据",
            code="INVALID_FUTURE_SHIP_TIME",
            issues=[
                {
                    "row": row["sourceRowNumber"],
                    "field": "shipTime",
                    "code": "INVALID_FUTURE_SHIP_TIME",
                    "message": f"发货日期晚于执行当天：{row['shipTime']}",
                }
                for row in invalid_future[:200]
            ],
        )
    rows = [row for row in rows if _business_date_from_row(row) <= cutoff]
    excluded_warehouses = set(excluded_sales_warehouses())
    excluded_rows = [row for row in rows if str(row["warehouse"]).strip() in excluded_warehouses]
    rows = [row for row in rows if str(row["warehouse"]).strip() not in excluded_warehouses]
    expected_channels = (
        tuple(session.expected_channels) if session.expected_channels is not None else None
    )
    expected_set = set(expected_channels or ())
    unexpected = [row for row in rows if expected_channels is not None and row["channel"] not in expected_set]
    approved_set = set(approved_sales_channels())
    disallowed = [row for row in rows if str(row["channel"]) not in approved_set]
    rows = [row for row in rows if str(row["channel"]) in approved_set]
    present_channels = {str(row["channel"]) for row in rows}
    missing = [item for item in expected_channels or () if item not in present_channels]
    outside = [
        row
        for row in rows
        if not (
            session.expected_start_date
            <= _business_date_from_row(row)
            < session.expected_end_date + timedelta(days=1)
        )
    ]
    issues: list[dict[str, object]] = []
    if unexpected:
        issues.append(
            {
                "code": "UNEXPECTED_IMPORT_CHANNELS",
                "message": f"{len(unexpected)} 行销售渠道不属于本次权威渠道范围",
            }
        )
    if missing:
        issues.append(
            {
                "code": "MISSING_EXPECTED_CHANNELS",
                "message": f"文件未覆盖本次声明的渠道：{'、'.join(missing)}",
            }
        )
    if outside:
        issues.append(
            {
                "code": "OUT_OF_EXPECTED_DATE_RANGE",
                "message": f"{len(outside)} 行发货日期超出权威导入范围 {session.expected_start_date} 至 {session.expected_end_date}",
            }
        )
    if not rows:
        issues.append(
            {
                "code": "NO_DATA_ROWS",
                "message": "剔除当天订单明细、刷刷仓和白名单外店铺后没有可导入的销售数据",
            }
        )
    if issues:
        raise SalesImportServiceError(
            "文件校验未通过，未写入任何销售数据",
            code=str(issues[0]["code"]),
            issues=issues,
        )
    keys: set[str] = set()
    duplicates: list[dict[str, object]] = []
    for row in rows:
        key = str(row["sourceLineKey"])
        if key in keys:
            duplicates.append(
                {
                    "row": row["sourceRowNumber"],
                    "code": "DUPLICATE_ROW_KEY",
                    "message": "文件内存在重复的销售明细行",
                }
            )
        keys.add(key)
    if duplicates:
        raise SalesImportServiceError(
            "文件校验未通过，未写入任何销售数据",
            code="DUPLICATE_ROW_KEY",
            issues=duplicates,
        )
    cleaned_rows, system_cost, cost_warnings, cleaned_numbers = _clean_zero_cost_rows(
        rows, session.system_cost_snapshot
    )
    warnings = [
        item
        for item in sanitize_issues(session.parser_warnings)
        if not (
            item.get("code") == "GROSS_PROFIT_MISMATCH"
            and int(item.get("row") or 0) in cleaned_numbers
        )
    ]
    if today_rows:
        warnings.append(
            {
                "code": "EXCLUDED_FUTURE_DATE_ROWS",
                "message": f"已剔除晚于截止日期的 {len(today_rows)} 行当天订单明细",
            }
        )
    if excluded_rows:
        warnings.append(
            {
                "code": "EXCLUDED_BRUSH_WAREHOUSE",
                "message": f"已剔除刷刷仓 {len(excluded_rows)} 行，不写入经营分析数据",
            }
        )
    if disallowed:
        warnings.append(
            {
                "code": "EXCLUDED_NON_WHITELIST_CHANNEL",
                "message": f"已剔除白名单外店铺 {len(disallowed)} 行，不写入经营分析数据",
            }
        )
    warnings = sanitize_issues([*warnings, *cost_warnings])
    scope = _scope_material(session.expected_start_date, session.expected_end_date, expected_channels)
    content_hash = _content_hash(scope, cleaned_rows)
    totals = _stored_totals(
        session.source_totals,
        cleaned_rows,
        raw_file_hash=session.raw_file_hash,
        excluded_warehouse_rows=len(excluded_rows),
        excluded_future_rows=len(today_rows),
        system_cost=system_cost,
        scope=scope,
        content_hash=content_hash,
    )
    return PreparedImport(
        session_id=str(session.id),
        actor_email=session.actor_email,
        file_name=session.file_name,
        file_size_bytes=session.file_size_bytes,
        raw_file_hash=session.raw_file_hash,
        sheet_name=session.sheet_name,
        start_date=session.expected_start_date,
        end_date=session.expected_end_date,
        channels=expected_channels,
        rows=cleaned_rows,
        warnings=warnings,
        totals=totals,
        scope=scope,
        scope_key=_scope_key(),
        content_hash=content_hash,
    )


def _scope_rows(prepared: PreparedImport):
    queryset = SalesOrderLine.objects.filter(
        business_date__gte=prepared.start_date,
        business_date__lt=prepared.end_date + timedelta(days=1),
    )
    if prepared.channels is not None:
        queryset = queryset.filter(channel__in=prepared.channels)
    return queryset


def _stored_line_content_row(line: SalesOrderLine) -> dict[str, object]:
    """Rebuild the canonical business row from a migrated legacy fact.

    Technical source identity fields participate in validation but are ignored
    by ``_content_hash``. This permits a first post-cutover upload to prove an
    exact match against the *current scoped facts* even when an old D1 batch did
    not contain an authoritative importScope/contentHash.
    """
    business_type = {
        "销售": "sale",
        "退货": "return",
        "零金额": "zero",
    }.get(line.business_type, line.business_type)
    return {
        "sourceLineKey": line.source_line_key,
        "sourceRowHash": line.source_row_hash,
        "sourceRowNumber": line.source_row_number,
        "orderNo": line.order_no,
        "onlineOrderNo": line.online_order_no,
        "channel": line.channel,
        "platform": line.platform,
        "shopName": line.shop_name,
        "logisticsCompany": line.logistics_company,
        "warehouse": line.warehouse,
        "productCode": line.product_code,
        "onlineSpecCode": line.online_spec_code,
        "productName": line.product_name,
        "specification": line.specification,
        "barcode": line.barcode,
        "supplier": line.supplier,
        "category": line.category,
        "quantity": line.quantity,
        "listUnitPriceCents": line.list_unit_price_cents,
        "costAmountCents": line.cost_amount_cents,
        "allocatedUnitPriceCents": line.allocated_unit_price_cents,
        "allocatedAmountCents": line.allocated_amount_cents,
        "feeAllocationCents": line.fee_allocation_cents,
        "grossProfitCents": line.gross_profit_cents,
        "grossMarginBps": line.gross_margin_bps,
        "untaxedGrossProfitCents": line.untaxed_gross_profit_cents,
        "untaxedGrossMarginBps": line.untaxed_gross_margin_bps,
        "orderTime": line.order_time,
        "salesTime": line.sales_time,
        "shipTime": line.ship_time,
        "lineShipTime": line.line_ship_time,
        "businessType": business_type,
    }


def _locked_scope_head(scope_key: str) -> SalesImportScopeHead:
    try:
        # Keep a concurrent unique-key loss inside its own savepoint; catching
        # IntegrityError in the outer publication transaction would otherwise
        # leave PostgreSQL's transaction unusable.
        with transaction.atomic():
            SalesImportScopeHead.objects.get_or_create(
                scope_key=scope_key,
                defaults={
                    "domain": "sales",
                    "state_token": "initial",
                    "status": "ready",
                },
            )
    except IntegrityError:
        # A concurrent first writer may have installed the singleton head.
        pass
    return SalesImportScopeHead.objects.select_for_update().get(scope_key=scope_key)


def _reserve_scope(prepared: PreparedImport, session_owner: str) -> ScopeReservation | dict[str, object]:
    with transaction.atomic():
        lock_active_write_authority()
        session = SalesStagedImportSession.objects.select_for_update().get(id=prepared.session_id)
        if session.status != "processing" or session.owner_token != session_owner:
            raise SalesImportServiceError(
                "规范化导入会话已被其他请求接管", code="conflict", status=409
            )
        _verify_staged_raw_owner(session)
        head = _locked_scope_head(prepared.scope_key)
        recovered_from = ""
        if head.status == "processing":
            if head.updated_at > timezone.now() - STALE_OWNER_AGE:
                raise SalesImportServiceError(
                    "同一销售写入基域正在处理另一批次", code="IMPORT_SCOPE_BUSY", status=409
                )
            recovered_from = head.owner_token
            if recovered_from:
                SalesImportAttempt.objects.filter(
                    id=recovered_from, outcome="processing"
                ).update(outcome="failed", error_code="IMPORT_RESERVATION_EXPIRED")
            head.status = "ready"
            head.owner_token = ""
            head.current_batch_id = ""
            head.generation += 1
            head.save(
                update_fields=[
                    "status",
                    "owner_token",
                    "current_batch_id",
                    "generation",
                    "updated_at",
                ]
            )
        ownership = list(
            _scope_rows(prepared)
            .values("last_import_batch_id")
            .annotate(row_count=Count("id"))
            .order_by("last_import_batch_id")
        )
        current_batch = None
        if len(ownership) == 1 and int(ownership[0]["row_count"]) == len(prepared.rows):
            current_batch = SalesImportBatch.objects.filter(
                id=ownership[0]["last_import_batch_id"],
                status="completed",
                row_count=len(prepared.rows),
                content_hash=prepared.content_hash,
            ).first()
        if current_batch:
            attempt_id = uuid.uuid4().hex
            SalesImportAttempt.objects.create(
                id=attempt_id,
                session_id=prepared.session_id,
                batch_id=current_batch.id,
                scope_key=prepared.scope_key,
                scope_json=prepared.scope,
                import_hash=current_batch.file_hash,
                raw_file_hash=prepared.raw_file_hash,
                content_hash=prepared.content_hash,
                row_count=len(prepared.rows),
                file_name=prepared.file_name,
                file_size_bytes=prepared.file_size_bytes,
                actor_email=prepared.actor_email,
                warnings=prepared.warnings,
                outcome="duplicate",
            )
            SalesImportFingerprint.objects.get_or_create(
                domain="sales",
                batch_id=current_batch.id,
                defaults={
                    "scope_key": prepared.scope_key,
                    "scope_json": prepared.scope,
                    "import_hash": current_batch.file_hash,
                    "raw_file_hash": current_batch.raw_file_hash or prepared.raw_file_hash,
                    "content_hash": prepared.content_hash,
                    "row_count": len(prepared.rows),
                    "status": "completed",
                    "publication_sequence": (
                        SalesImportFingerprint.objects.aggregate(
                            maximum=Max("publication_sequence")
                        )["maximum"]
                        or 0
                    )
                    + 1,
                },
            )
            session.status = "completed"
            session.result_batch_id = current_batch.id
            session.owner_token = ""
            session.save(
                update_fields=["status", "result_batch_id", "owner_token", "updated_at"]
            )
            session.chunks.all().delete()
            return {
                "ok": True,
                "status": "duplicate",
                "message": "全部标准化销售资料与当前期间一致，无需重复导入",
                "batch": batch_payload(current_batch),
                "warnings": json.loads(current_batch.warnings_json or "[]"),
            }
        # Legacy D1 batches often predate authoritative importScope/contentHash
        # metadata and a current range may be composed from several historical
        # batches. Never guess that old range from min/max dates. Instead, use
        # the caller's already validated scope to hash the current facts and
        # require an exact canonical-set match before treating the first
        # post-cutover upload as a duplicate.
        # A modern batch binds both content and its declared date/channel scope.
        # When a rolling window moves, identical rows still need a new scoped
        # receipt and coherent ownership. Do not mistake that for legacy data
        # lacking scope metadata, or verification would reuse the old file/range.
        scoped_batch_exists = (
            SalesImportBatch.objects.filter(
                id__in=_scope_rows(prepared).values("last_import_batch_id")
            )
            .exclude(content_hash="")
            .exclude(scope_key="")
            .exclude(scope_json={})
            .exists()
        )
        current_scope_rows = [] if scoped_batch_exists else list(_scope_rows(prepared))
        if not scoped_batch_exists and len(current_scope_rows) == len(prepared.rows) and _content_hash(
            prepared.scope,
            [_stored_line_content_row(line) for line in current_scope_rows],
        ) == prepared.content_hash:
            owned_batch_ids = [
                str(item["last_import_batch_id"])
                for item in ownership
                if item["last_import_batch_id"]
            ]
            current_batch = (
                SalesImportBatch.objects.filter(
                    id=owned_batch_ids[0], status="completed"
                ).first()
                if len(owned_batch_ids) == 1
                else None
            )
            if current_batch is None:
                compatibility_id = _sha256_text(
                    "legacy-current-content-v1\n"
                    + _encode_part(prepared.scope_key)
                    + _encode_part(prepared.content_hash)
                )
                now_text = timezone.now().isoformat()
                current_batch, _ = SalesImportBatch.objects.get_or_create(
                    id=compatibility_id,
                    defaults={
                        "source": SALES_IMPORT_SOURCE,
                        "file_name": prepared.file_name,
                        "file_size_bytes": prepared.file_size_bytes,
                        "file_hash": compatibility_id,
                        "sheet_name": prepared.sheet_name,
                        "status": "completed",
                        "row_count": len(prepared.rows),
                        "inserted_count": 0,
                        "duplicate_count": len(prepared.rows),
                        "warning_count": len(prepared.warnings),
                        "warnings_json": json.dumps(
                            prepared.warnings,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "totals_json": json.dumps(
                            prepared.totals,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "created_at": now_text,
                        "completed_at": now_text,
                        "raw_file_hash": prepared.raw_file_hash,
                        "content_hash": prepared.content_hash,
                        "scope_key": prepared.scope_key,
                        "scope_json": prepared.scope,
                        "actor_email": prepared.actor_email,
                    },
                )
            else:
                # Exact current-fact proof safely fills missing legacy metadata;
                # it never infers a scope before the new file is parsed.
                updates: list[str] = []
                for field, value in (
                    ("raw_file_hash", prepared.raw_file_hash),
                    ("content_hash", prepared.content_hash),
                    ("scope_key", prepared.scope_key),
                    ("scope_json", prepared.scope),
                    ("actor_email", prepared.actor_email),
                ):
                    if not getattr(current_batch, field):
                        setattr(current_batch, field, value)
                        updates.append(field)
                if updates:
                    current_batch.save(update_fields=updates)
            attempt_id = uuid.uuid4().hex
            SalesImportAttempt.objects.create(
                id=attempt_id,
                session_id=prepared.session_id,
                batch_id=current_batch.id,
                scope_key=prepared.scope_key,
                scope_json=prepared.scope,
                import_hash=current_batch.file_hash,
                raw_file_hash=prepared.raw_file_hash,
                content_hash=prepared.content_hash,
                row_count=len(prepared.rows),
                file_name=prepared.file_name,
                file_size_bytes=prepared.file_size_bytes,
                actor_email=prepared.actor_email,
                warnings=prepared.warnings,
                outcome="duplicate",
            )
            SalesImportFingerprint.objects.get_or_create(
                domain="sales",
                batch_id=current_batch.id,
                defaults={
                    "scope_key": prepared.scope_key,
                    "scope_json": prepared.scope,
                    "import_hash": current_batch.file_hash,
                    "raw_file_hash": current_batch.raw_file_hash
                    or prepared.raw_file_hash,
                    "content_hash": prepared.content_hash,
                    "row_count": len(prepared.rows),
                    "status": "completed",
                    "publication_sequence": (
                        SalesImportFingerprint.objects.aggregate(
                            maximum=Max("publication_sequence")
                        )["maximum"]
                        or 0
                    )
                    + 1,
                },
            )
            session.status = "completed"
            session.result_batch_id = current_batch.id
            session.owner_token = ""
            session.save(
                update_fields=["status", "result_batch_id", "owner_token", "updated_at"]
            )
            session.chunks.all().delete()
            return {
                "ok": True,
                "status": "duplicate",
                "message": "全部标准化销售资料与当前期间一致，无需重复导入",
                "batch": batch_payload(current_batch),
                "warnings": json.loads(current_batch.warnings_json or "[]"),
            }
        attempt_id = uuid.uuid4().hex
        batch_id = _attempt_hash(prepared.scope_key, prepared.content_hash, head.state_token)
        SalesImportAttempt.objects.create(
            id=attempt_id,
            session_id=prepared.session_id,
            batch_id=batch_id,
            scope_key=prepared.scope_key,
            scope_json=prepared.scope,
            import_hash=batch_id,
            raw_file_hash=prepared.raw_file_hash,
            content_hash=prepared.content_hash,
            row_count=len(prepared.rows),
            file_name=prepared.file_name,
            file_size_bytes=prepared.file_size_bytes,
            actor_email=prepared.actor_email,
            warnings=prepared.warnings,
            outcome="processing",
            recovered_from_attempt_id=recovered_from,
        )
        head.status = "processing"
        head.owner_token = attempt_id
        head.current_batch_id = batch_id
        head.save(update_fields=["status", "owner_token", "current_batch_id", "updated_at"])
        return ScopeReservation(
            attempt_id=attempt_id,
            batch_id=batch_id,
            previous_state_token=head.state_token,
            recovered_from_attempt_id=recovered_from,
        )


def _line_model(
    row: Mapping[str, object], batch_id: str, erp_category: str, timestamp: str
) -> SalesOrderLine:
    raw = {
        "source_line_key": row["sourceLineKey"],
        "source_row_hash": row["sourceRowHash"],
        "source_row_number": row["sourceRowNumber"],
        "order_no": row["orderNo"],
        "online_order_no": row["onlineOrderNo"],
        "channel": row["channel"],
        "platform": row["platform"],
        "shop_name": row["shopName"],
        "logistics_company": row["logisticsCompany"],
        "warehouse": row["warehouse"],
        "product_code": row["productCode"],
        "online_spec_code": row["onlineSpecCode"],
        "product_name": row["productName"],
        "specification": row["specification"],
        "barcode": row["barcode"],
        "supplier": row["supplier"],
        "category": row["category"],
        "quantity": row["quantity"],
        "list_unit_price_cents": row["listUnitPriceCents"],
        "cost_amount_cents": row["costAmountCents"],
        "allocated_unit_price_cents": row["allocatedUnitPriceCents"],
        "allocated_amount_cents": row["allocatedAmountCents"],
        "fee_allocation_cents": row["feeAllocationCents"],
        "gross_profit_cents": row["grossProfitCents"],
        "gross_margin_bps": row["grossMarginBps"],
        "untaxed_gross_profit_cents": row["untaxedGrossProfitCents"],
        "untaxed_gross_margin_bps": row["untaxedGrossMarginBps"],
        "order_time": row["orderTime"],
        "sales_time": row["salesTime"],
        "ship_time": row["shipTime"],
        "line_ship_time": row["lineShipTime"],
        "business_type": row["businessType"],
    }
    return SalesOrderLine(
        **raw,
        first_import_batch_id=batch_id,
        last_import_batch_id=batch_id,
        created_at=timestamp,
        updated_at=timestamp,
        migration_generation="",
        **sales_projection_values(raw, erp_category=erp_category),
    )


LINE_UPDATE_FIELDS = [
    "source_row_hash",
    "last_import_batch_id",
    "source_row_number",
    "order_no",
    "online_order_no",
    "channel",
    "platform",
    "shop_name",
    "logistics_company",
    "warehouse",
    "product_code",
    "online_spec_code",
    "product_name",
    "specification",
    "barcode",
    "supplier",
    "category",
    "quantity",
    "list_unit_price_cents",
    "cost_amount_cents",
    "allocated_unit_price_cents",
    "allocated_amount_cents",
    "fee_allocation_cents",
    "gross_profit_cents",
    "gross_margin_bps",
    "untaxed_gross_profit_cents",
    "untaxed_gross_margin_bps",
    "order_time",
    "sales_time",
    "ship_time",
    "line_ship_time",
    "business_type",
    "updated_at",
    "business_date",
    "platform_key",
    "channel_key",
    "shop_key",
    "resolved_category",
    "order_identity",
    "is_business_row",
    "is_net_sales_row",
    "is_net_quantity_row",
    "migration_generation",
]


def _erp_categories(product_codes: Iterable[str]) -> dict[str, str]:
    codes = sorted(set(product_codes))
    result: dict[str, str] = {}
    for offset in range(0, len(codes), 1_000):
        result.update(
            ErpProductMaster.objects.filter(product_code__in=codes[offset : offset + 1_000])
            .values_list("product_code", "category")
        )
    return result


def _upsert_rows(prepared: PreparedImport, batch_id: str) -> None:
    categories = _erp_categories(str(row["productCode"]) for row in prepared.rows)
    timestamp = timezone.now().isoformat()
    for offset in range(0, len(prepared.rows), 1_000):
        models = [
            _line_model(
                row,
                batch_id,
                categories.get(str(row["productCode"]), ""),
                timestamp,
            )
            for row in prepared.rows[offset : offset + 1_000]
        ]
        SalesOrderLine.objects.bulk_create(
            models,
            batch_size=1_000,
            update_conflicts=True,
            unique_fields=["source_line_key"],
            update_fields=LINE_UPDATE_FIELDS,
        )


def _bump_sales_revision(content_hash: str) -> int:
    try:
        revision = SalesDataRevision.objects.select_for_update().get(domain="sales")
    except SalesDataRevision.DoesNotExist:
        SalesDataRevision.objects.create(
            domain="sales", revision=1, source_digest=content_hash
        )
        return 1
    revision.revision += 1
    revision.source_digest = content_hash
    revision.save(update_fields=["revision", "source_digest", "updated_at"])
    return revision.revision


def _publish_import(
    prepared: PreparedImport, reservation: ScopeReservation, session_owner: str
) -> SalesImportBatch:
    with transaction.atomic():
        lock_active_write_authority()
        lock_erp_reference_for_sales_read()
        session = SalesStagedImportSession.objects.select_for_update().get(id=prepared.session_id)
        _verify_staged_raw_owner(session)
        head = SalesImportScopeHead.objects.select_for_update().get(scope_key=prepared.scope_key)
        if (
            session.status != "processing"
            or session.owner_token != session_owner
            or head.status != "processing"
            or head.owner_token != reservation.attempt_id
            or head.current_batch_id != reservation.batch_id
            or head.state_token != reservation.previous_state_token
        ):
            raise SalesImportServiceError(
                "导入范围已被其他任务接管", code="conflict", status=409
            )
        now_text = timezone.now().isoformat()
        batch = SalesImportBatch.objects.create(
            id=reservation.batch_id,
            source=SALES_IMPORT_SOURCE,
            file_name=prepared.file_name,
            file_size_bytes=prepared.file_size_bytes,
            file_hash=reservation.batch_id,
            sheet_name=prepared.sheet_name,
            status="processing",
            row_count=len(prepared.rows),
            warning_count=len(prepared.warnings),
            warnings_json=json.dumps(prepared.warnings, ensure_ascii=False, separators=(",", ":")),
            totals_json=json.dumps(prepared.totals, ensure_ascii=False, separators=(",", ":")),
            created_at=now_text,
            raw_file_hash=prepared.raw_file_hash,
            content_hash=prepared.content_hash,
            scope_key=prepared.scope_key,
            scope_json=prepared.scope,
            actor_email=prepared.actor_email,
        )
        _upsert_rows(prepared, reservation.batch_id)
        stale_rows = _scope_rows(prepared).exclude(last_import_batch_id=reservation.batch_id)
        stale_rows.delete()
        ownership = _scope_rows(prepared)
        if ownership.count() != len(prepared.rows) or ownership.exclude(
            last_import_batch_id=reservation.batch_id
        ).exists():
            raise SalesImportServiceError(
                "销售导入落库事实与规范化集合不一致",
                code="SALES_IMPORT_VERIFICATION_FAILED",
                status=500,
            )
        inserted = SalesOrderLine.objects.filter(
            first_import_batch_id=reservation.batch_id
        ).count()
        next_state = _next_state_token(
            reservation.previous_state_token,
            reservation.batch_id,
            prepared.content_hash,
            len(prepared.rows),
        )
        batch.status = "completed"
        batch.inserted_count = inserted
        batch.duplicate_count = len(prepared.rows) - inserted
        batch.completed_at = timezone.now().isoformat()
        batch.published_state_token = next_state
        batch.save(
            update_fields=[
                "status",
                "inserted_count",
                "duplicate_count",
                "completed_at",
                "published_state_token",
            ]
        )
        _bump_sales_revision(prepared.content_hash)
        publication_sequence = (
            SalesImportFingerprint.objects.aggregate(maximum=Max("publication_sequence"))[
                "maximum"
            ]
            or 0
        ) + 1
        SalesImportFingerprint.objects.create(
            domain="sales",
            batch_id=reservation.batch_id,
            scope_key=prepared.scope_key,
            scope_json=prepared.scope,
            import_hash=reservation.batch_id,
            raw_file_hash=prepared.raw_file_hash,
            content_hash=prepared.content_hash,
            row_count=len(prepared.rows),
            status="completed",
            publication_sequence=publication_sequence,
        )
        updated_attempt = SalesImportAttempt.objects.filter(
            id=reservation.attempt_id,
            outcome="processing",
        ).update(outcome="imported", error_code="")
        if updated_attempt != 1:
            raise SalesImportServiceError(
                "导入尝试审计状态不一致", code="conflict", status=409
            )
        head.state_token = next_state
        head.status = "ready"
        head.owner_token = ""
        head.current_batch_id = reservation.batch_id
        head.generation += 1
        head.save(
            update_fields=[
                "state_token",
                "status",
                "owner_token",
                "current_batch_id",
                "generation",
                "updated_at",
            ]
        )
        session.status = "completed"
        session.owner_token = ""
        session.result_batch_id = reservation.batch_id
        session.save(
            update_fields=["status", "owner_token", "result_batch_id", "updated_at"]
        )
        session.chunks.all().delete()
        transaction.on_commit(cache.clear)
        return batch


def _release_failed_reservation(
    prepared: PreparedImport,
    reservation: ScopeReservation,
    session_owner: str,
    error_code: str,
) -> None:
    with transaction.atomic():
        lock_active_write_authority()
        head = SalesImportScopeHead.objects.select_for_update().filter(
            scope_key=prepared.scope_key
        ).first()
        if (
            head
            and head.status == "processing"
            and head.owner_token == reservation.attempt_id
            and head.current_batch_id == reservation.batch_id
        ):
            SalesImportAttempt.objects.filter(
                id=reservation.attempt_id, outcome="processing"
            ).update(outcome="failed", error_code=error_code[:100])
            head.status = "ready"
            head.owner_token = ""
            head.current_batch_id = ""
            head.generation += 1
            head.save(
                update_fields=[
                    "status",
                    "owner_token",
                    "current_batch_id",
                    "generation",
                    "updated_at",
                ]
            )
        session = SalesStagedImportSession.objects.select_for_update().filter(
            id=prepared.session_id,
            status="processing",
            owner_token=session_owner,
        ).first()
        if session:
            session.status = "ready"
            session.owner_token = ""
            session.save(update_fields=["status", "owner_token", "updated_at"])


def _reject_claimed_session(
    session: SalesStagedImportSession,
    session_owner: str,
    error: SalesImportServiceError,
) -> None:
    with transaction.atomic():
        lock_active_write_authority()
        locked = SalesStagedImportSession.objects.select_for_update().filter(
            id=session.id,
            status="processing",
            owner_token=session_owner,
        ).first()
        if not locked:
            return
        issues = sanitize_issues(error.issues or [{"code": error.code, "message": str(error)}])
        _record_prevalidation_rejection(
            actor_email=locked.actor_email,
            raw_file_hash=locked.raw_file_hash,
            file_name=locked.file_name,
            file_size_bytes=locked.file_size_bytes,
            issues=issues,
            session_id=str(locked.id),
        )
        locked.status = "rejected"
        locked.owner_token = ""
        locked.save(update_fields=["status", "owner_token", "updated_at"])


def complete_staged_import(
    session_id: object,
    actor_email: str,
    raw_upload_owner_token: object | None = None,
) -> dict[str, object]:
    claimed, session_owner = _claim_staged_session(
        session_id, actor_email, raw_upload_owner_token
    )
    if session_owner is None:
        return claimed  # type: ignore[return-value]
    session = claimed
    assert isinstance(session, SalesStagedImportSession)
    try:
        prepared = _prepare_import(session)
    except SalesImportServiceError as error:
        _reject_claimed_session(session, session_owner, error)
        raise
    try:
        reservation = _reserve_scope(prepared, session_owner)
    except SalesImportServiceError:
        with transaction.atomic():
            lock_active_write_authority()
            SalesStagedImportSession.objects.filter(
                id=prepared.session_id,
                status="processing",
                owner_token=session_owner,
            ).update(status="ready", owner_token="")
        raise
    if isinstance(reservation, dict):
        return reservation
    try:
        batch = _publish_import(prepared, reservation, session_owner)
    except Exception as error:
        _release_failed_reservation(
            prepared,
            reservation,
            session_owner,
            getattr(error, "code", "SALES_IMPORT_FAILED"),
        )
        raise
    return {
        "ok": True,
        "status": "imported",
        "message": "销售单明细账导入成功",
        "batch": batch_payload(batch),
        "warnings": prepared.warnings,
    }


def batch_payload(batch: SalesImportBatch) -> dict[str, object]:
    try:
        warnings = json.loads(batch.warnings_json or "[]")
    except json.JSONDecodeError:
        warnings = []
    try:
        totals = json.loads(batch.totals_json or "{}")
    except json.JSONDecodeError:
        totals = {}
    return {
        "id": batch.id,
        "source": batch.source,
        "fileName": batch.file_name,
        "fileSizeBytes": batch.file_size_bytes,
        "fileHash": batch.file_hash,
        "rawFileHash": batch.raw_file_hash,
        "contentHash": batch.content_hash,
        "sheetName": batch.sheet_name,
        "status": batch.status,
        "rowCount": batch.row_count,
        "insertedCount": batch.inserted_count,
        "duplicateCount": batch.duplicate_count,
        "warningCount": batch.warning_count,
        "warnings": warnings,
        "totals": totals,
        "createdAt": batch.created_at,
        "completedAt": batch.completed_at,
    }


def list_import_batches(page: int = 1, page_size: int = 20) -> dict[str, object]:
    if not 1 <= page <= 10_000 or not 1 <= page_size <= 100:
        raise SalesImportServiceError("分页参数无效", status=400)
    offset = (page - 1) * page_size
    queryset = SalesImportBatch.objects.order_by("-created_at", "-id")
    total = queryset.count()
    items = [batch_payload(item) for item in queryset[offset : offset + page_size]]
    return {
        "items": items,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "returned": len(items),
            "truncated": offset + len(items) < total,
        },
    }
