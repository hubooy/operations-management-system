from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from .errors import FinanceApiError
from .models import (
    FinanceDataRevision,
    FinanceImportAttempt,
    FinanceImportBatch,
    FinanceImportFingerprint,
    FinanceImportScopeHead,
    FinanceLine,
    FinanceMonth,
    FinanceWriteAuthority,
)
from .serialization import batch_payload


FINANCE_IMPORT_SOURCE = "月度财报 · 志高事业部"
FINANCE_SCHEMA_VERSION = "finance-normalized-v1"
MAX_IMPORT_MONTHS = 120
MAX_IMPORT_ROWS = 100_000
MAX_WARNING_COUNT = 300
JS_SAFE_INTEGER = 9_007_199_254_740_991
HEX_64_RE = re.compile(r"^[a-f0-9]{64}$")
MONTH_RE = re.compile(r"^(?:19|20|21)\d{2}-(?:0[1-9]|1[0-2])$")
ZERO_TOKEN = "0" * 64


def _canonical_json(value: object, ignored: frozenset[str] = frozenset(), *, top_level: bool = True) -> str:
    def normalize(item: object, *, root: bool) -> object:
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            if not item.is_integer():
                raise FinanceApiError("规范化财务数据包含非整数数值")
            return int(item)
        if isinstance(item, (list, tuple)):
            return [normalize(child, root=False) for child in item]
        if isinstance(item, Mapping):
            return {
                str(key): normalize(item[key], root=False)
                for key in sorted(item, key=lambda candidate: str(candidate))
                if not (root and str(key) in ignored)
            }
        raise FinanceApiError("规范化财务数据包含不支持的字段类型")

    return json.dumps(
        normalize(value, root=top_level),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _encode_part(value: str) -> str:
    return f"{len(value.encode('utf-8'))}:{value}"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def finance_scope_key() -> str:
    domain = "finance"
    lock_scope = _canonical_json({"source": "monthly-finance-report"})
    return _sha256_text(
        f"import-lock-scope-v1\n{_encode_part(domain)}{_encode_part(lock_scope)}"
    )


def _fingerprint(months: Sequence[dict[str, object]]) -> tuple[str, str, int]:
    month_keys = sorted(str(month["month"]) for month in months)
    scope_json = _canonical_json(
        {"months": month_keys, "source": "monthly-finance-report"}
    )
    ignored = frozenset({"rawValue", "sourceRowCount", "sortOrder"})
    row_hashes: list[str] = []
    for month in months:
        for line in month["lines"]:  # type: ignore[index]
            row = {"businessName": month["businessName"], **line}  # type: ignore[arg-type]
            row_hashes.append(_sha256_text(_canonical_json(row, ignored)))
    row_hashes.sort()
    payload = (
        f"import-content-v3\n{_encode_part(scope_json)}{len(row_hashes)}\n"
        + "".join(row_hashes)
    )
    return finance_scope_key(), _sha256_text(payload), len(row_hashes)


def _attempt_hash(scope_key: str, content_hash: str, state_token: str) -> str:
    return _sha256_text(
        "".join(
            _encode_part(value)
            for value in (
                "import-attempt-v1",
                "finance",
                scope_key,
                content_hash,
                state_token.strip() or "initial",
            )
        )
    )


def _next_state_token(
    previous: str, batch_id: str, content_hash: str, row_count: int
) -> str:
    return _sha256_text(
        "".join(
            _encode_part(value)
            for value in (
                "import-scope-state-v2",
                previous.strip() or "initial",
                batch_id,
                content_hash,
                str(row_count),
            )
        )
    )


def _now_text() -> str:
    return timezone.now().isoformat()


def _bounded_text(value: object, label: str, maximum: int, *, empty: bool = True) -> str:
    if not isinstance(value, str):
        raise FinanceApiError(f"{label}必须是字符串")
    normalized = value.strip()
    if (not empty and not normalized) or len(normalized) > maximum:
        raise FinanceApiError(f"{label}长度超出限制")
    return normalized


def _integer(
    value: object,
    label: str,
    *,
    minimum: int = -JS_SAFE_INTEGER,
    maximum: int = JS_SAFE_INTEGER,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FinanceApiError(f"{label}必须是安全整数")
    if value < minimum or value > maximum:
        raise FinanceApiError(f"{label}超出安全范围")
    return value


def _nullable_integer(value: object, label: str) -> int | None:
    return None if value is None else _integer(value, label)


def _issue(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise FinanceApiError("财务导入告警格式无效")
    allowed = {"sheet", "month", "row", "code", "message"}
    if set(value) - allowed:
        raise FinanceApiError("财务导入告警包含未知字段")
    result: dict[str, object] = {
        "code": _bounded_text(value.get("code"), "告警代码", 100, empty=False),
        "message": _bounded_text(value.get("message"), "告警内容", 500, empty=False),
    }
    for key, maximum in (("sheet", 200), ("month", 20)):
        if key in value:
            result[key] = _bounded_text(value[key], key, maximum)
    if "row" in value:
        result["row"] = _integer(value["row"], "告警行号", minimum=1, maximum=10_000_000)
    return result


def _validate_line(value: object, month: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise FinanceApiError("财务明细行格式无效")
    expected = {
        "month", "section", "metricKey", "subjectName", "scopeKey", "scopeType",
        "scopeName", "groupName", "valueType", "amountCents", "rateBps", "rawValue",
        "sourceRowCount", "sortOrder", "isTotal",
    }
    if set(value) != expected:
        raise FinanceApiError("财务明细行字段与 finance-normalized-v1 不一致")
    row_month = _bounded_text(value["month"], "明细月份", 7, empty=False)
    if row_month != month:
        raise FinanceApiError("财务明细月份与工作表月份不一致")
    section = _bounded_text(value["section"], "财务区段", 32, empty=False)
    scope_type = _bounded_text(value["scopeType"], "财务范围类型", 32, empty=False)
    value_type = _bounded_text(value["valueType"], "财务值类型", 32, empty=False)
    if section not in {"summary", "kingdee"}:
        raise FinanceApiError("财务区段无效")
    if scope_type not in {"business", "group", "shop"}:
        raise FinanceApiError("财务范围类型无效")
    if value_type not in {"amount", "rate", "number", "text"}:
        raise FinanceApiError("财务值类型无效")
    if not isinstance(value["isTotal"], bool):
        raise FinanceApiError("财务合计标记必须是布尔值")
    return {
        "month": month,
        "section": section,
        "metricKey": _bounded_text(value["metricKey"], "指标键", 500),
        "subjectName": _bounded_text(value["subjectName"], "科目", 2_000, empty=False),
        "scopeKey": _bounded_text(value["scopeKey"], "范围键", 2_000, empty=False),
        "scopeType": scope_type,
        "scopeName": _bounded_text(value["scopeName"], "范围名称", 1_000),
        "groupName": _bounded_text(value["groupName"], "分组名称", 1_000),
        "valueType": value_type,
        "amountCents": _nullable_integer(value["amountCents"], "金额"),
        "rateBps": _nullable_integer(value["rateBps"], "比率"),
        "rawValue": _bounded_text(value["rawValue"], "原始值", 4_000),
        "sourceRowCount": _integer(value["sourceRowCount"], "来源行数", minimum=1, maximum=1_000_000),
        "sortOrder": _integer(value["sortOrder"], "排序号", minimum=0, maximum=10_000_000),
        "isTotal": value["isTotal"],
    }


def validate_import_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise FinanceApiError("请求内容不是有效 JSON")
    allowed = {
        "schemaVersion", "disposition", "fileName", "fileSizeBytes", "rawFileHash",
        "sourceSheetCount", "months", "warnings", "errors", "message",
    }
    if set(payload) - allowed:
        raise FinanceApiError("财务导入请求包含未知字段")
    if payload.get("schemaVersion") != FINANCE_SCHEMA_VERSION:
        raise FinanceApiError("财务规范化契约版本不受支持", status=422)
    disposition = payload.get("disposition")
    if disposition not in {"prepared", "rejected"}:
        raise FinanceApiError("财务导入处置类型无效")
    file_name = _bounded_text(payload.get("fileName"), "文件名", 255, empty=False)
    file_size = _integer(payload.get("fileSizeBytes"), "文件大小", minimum=0, maximum=8 * 1024 * 1024)
    raw_hash = _bounded_text(payload.get("rawFileHash"), "原文件哈希", 64, empty=False).lower()
    if not HEX_64_RE.fullmatch(raw_hash):
        raise FinanceApiError("原文件哈希必须是 64 位小写 SHA-256")
    warnings_value = payload.get("warnings", [])
    if not isinstance(warnings_value, list) or len(warnings_value) > MAX_WARNING_COUNT:
        raise FinanceApiError("财务导入告警数量超出限制")
    warnings = [_issue(item) for item in warnings_value]
    base: dict[str, object] = {
        "schemaVersion": FINANCE_SCHEMA_VERSION,
        "disposition": disposition,
        "fileName": file_name,
        "fileSizeBytes": file_size,
        "rawFileHash": raw_hash,
        "warnings": warnings,
    }
    if disposition == "rejected":
        errors_value = payload.get("errors", [])
        if not isinstance(errors_value, list) or not errors_value or len(errors_value) > 200:
            raise FinanceApiError("被拒绝财务导入必须携带有界错误清单")
        base["errors"] = [_issue(item) for item in errors_value]
        base["message"] = _bounded_text(payload.get("message"), "拒绝原因", 500, empty=False)
        return base

    source_sheet_count = _integer(
        payload.get("sourceSheetCount"), "工作表数量", minimum=1, maximum=500
    )
    raw_months = payload.get("months")
    if not isinstance(raw_months, list) or not 1 <= len(raw_months) <= MAX_IMPORT_MONTHS:
        raise FinanceApiError("财务月份数量超出限制")
    months: list[dict[str, object]] = []
    seen_months: set[str] = set()
    total_rows = 0
    for raw_month in raw_months:
        if not isinstance(raw_month, dict) or set(raw_month) != {
            "month", "sheetName", "businessName", "shopCount", "subjectCount", "lines"
        }:
            raise FinanceApiError("财务月份字段与 finance-normalized-v1 不一致")
        month = _bounded_text(raw_month["month"], "财务月份", 7, empty=False)
        if not MONTH_RE.fullmatch(month) or month in seen_months:
            raise FinanceApiError("财务月份无效或重复")
        seen_months.add(month)
        raw_lines = raw_month["lines"]
        if not isinstance(raw_lines, list) or not raw_lines:
            raise FinanceApiError("财务月份不能是空集合", status=422)
        lines = [_validate_line(item, month) for item in raw_lines]
        identities = {
            (line["section"], line["scopeKey"], line["subjectName"]) for line in lines
        }
        if len(identities) != len(lines):
            raise FinanceApiError("财务月份包含重复业务身份", status=422)
        if not any(
            line["scopeType"] == "business"
            and line["section"] == "summary"
            and line["metricKey"] == "net_sales"
            for line in lines
        ):
            raise FinanceApiError("经营汇总区缺少实际销售金额", status=422)
        if not any(
            line["section"] == "kingdee" and line["subjectName"] == "销售费用"
            for line in lines
        ):
            raise FinanceApiError("金蝶科目明细区缺少销售费用总额", status=422)
        total_rows += len(lines)
        if total_rows > MAX_IMPORT_ROWS:
            raise FinanceApiError("规范化财务明细超过 100000 行上限", status=413, code="payload_too_large")
        months.append({
            "month": month,
            "sheetName": _bounded_text(raw_month["sheetName"], "工作表名", 200, empty=False),
            "businessName": _bounded_text(raw_month["businessName"], "事业部名称", 500),
            "shopCount": _integer(raw_month["shopCount"], "店铺数", minimum=0, maximum=100_000),
            "subjectCount": _integer(raw_month["subjectCount"], "科目数", minimum=0, maximum=100_000),
            "lines": lines,
        })
    months.sort(key=lambda item: str(item["month"]))
    base.update({"sourceSheetCount": source_sheet_count, "months": months})
    return base


def assert_active_authority() -> FinanceWriteAuthority:
    # The post-cutover authority row is immutable to the domain writer.  A
    # PostgreSQL SELECT FOR UPDATE would require granting this process UPDATE
    # on the authority table, defeating that database-level separation.  The
    # migration owner is the only role that can transition authority, and no
    # reverse transition is supported after activation, so an exact plain read
    # plus epoch/cutover comparison is the correct writer fence.
    try:
        authority = FinanceWriteAuthority.objects.get(id=1)
    except FinanceWriteAuthority.DoesNotExist as error:
        raise FinanceApiError(
            "PostgreSQL 财务写入权威门禁尚未初始化",
            code="finance_write_authority_unavailable",
            status=503,
        ) from error
    if authority.status != "postgres":
        raise FinanceApiError(
            "PostgreSQL 尚未取得财务唯一写入权",
            code="finance_write_authority_inactive",
            status=503,
        )
    expected_epoch = str(getattr(settings, "FINANCE_WRITE_AUTHORITY_EPOCH", "") or "")
    expected_cutover = str(getattr(settings, "FINANCE_WRITE_CUTOVER_ID", "") or "")
    if settings.DJANGO_PROCESS_ROLE == "finance_writer" and (
        not expected_epoch
        or not expected_cutover
        or str(authority.authority_epoch) != expected_epoch
        or authority.cutover_id != expected_cutover
    ):
        raise FinanceApiError(
            "PostgreSQL 财务写入权威的 epoch/cutover 配置不匹配",
            code="finance_write_authority_mismatch",
            status=503,
        )
    return authority


def _record_rejection(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    with transaction.atomic():
        assert_active_authority()
        errors = list(payload["errors"])  # type: ignore[arg-type]
        FinanceImportAttempt.objects.create(
            raw_file_hash=payload["rawFileHash"],
            outcome="rejected",
            error_code=str(errors[0].get("code") or "FINANCE_PREVALIDATION_REJECTED"),
            actor_email=actor_email,
            metadata={
                "fileName": payload["fileName"],
                "fileSizeBytes": payload["fileSizeBytes"],
                "issues": errors,
            },
            completed_at=timezone.now(),
        )
    return {
        "ok": False,
        "status": "rejected",
        "message": payload["message"],
        "warnings": payload["warnings"],
        "errors": errors,
        "errorCount": len(errors),
    }


def _current_batch_for_months(months: list[str]) -> FinanceImportBatch | None:
    ownership = list(
        FinanceMonth.objects.filter(month__in=months, status="completed")
        .values("batch_id")
        .annotate(month_count=Count("month"))
        .order_by("batch_id")
    )
    if len(ownership) != 1 or int(ownership[0]["month_count"]) != len(months):
        return None
    return FinanceImportBatch.objects.filter(
        id=ownership[0]["batch_id"], status="completed"
    ).first()


def _line_model(line: dict[str, object], created_at: str) -> FinanceLine:
    return FinanceLine(
        month=line["month"],
        section=line["section"],
        metric_key=line["metricKey"],
        subject_name=line["subjectName"],
        scope_key=line["scopeKey"],
        scope_type=line["scopeType"],
        scope_name=line["scopeName"],
        group_name=line["groupName"],
        value_type=line["valueType"],
        amount_cents=line["amountCents"],
        rate_bps=line["rateBps"],
        raw_value=line["rawValue"],
        source_row_count=line["sourceRowCount"],
        sort_order=line["sortOrder"],
        is_total=line["isTotal"],
        created_at=created_at,
    )


def _import_prepared(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    months = payload["months"]  # type: ignore[assignment]
    month_keys = [str(item["month"]) for item in months]
    scope_key, content_hash, row_count = _fingerprint(months)
    now = timezone.now()
    now_text = now.isoformat()
    with transaction.atomic():
        assert_active_authority()
        head, _created = FinanceImportScopeHead.objects.select_for_update().get_or_create(
            id=1,
            defaults={"scope_key": scope_key, "state_token": ZERO_TOKEN},
        )
        if head.scope_key != scope_key:
            raise FinanceApiError(
                "财务导入基域与控制头不一致",
                code="finance_import_scope_mismatch",
                status=503,
            )
        current = _current_batch_for_months(month_keys)
        fingerprint = (
            FinanceImportFingerprint.objects.filter(batch_id=current.id).first()
            if current
            else None
        )
        if current and fingerprint and fingerprint.scope_key == scope_key and fingerprint.content_hash == content_hash:
            FinanceImportAttempt.objects.create(
                batch_id=current.id,
                scope_key=scope_key,
                raw_file_hash=payload["rawFileHash"],
                content_hash=content_hash,
                outcome="duplicate",
                actor_email=actor_email,
                metadata={"fileName": payload["fileName"], "fileSizeBytes": payload["fileSizeBytes"]},
                completed_at=now,
            )
            return {
                "ok": True,
                "status": "duplicate",
                "message": "全部标准化财务资料与当前月份一致，无需重复导入",
                "batch": batch_payload(current),
                "importedMonths": [],
                "skippedMonths": month_keys,
                "warnings": list(current.warnings_json or []),
            }

        previous_state = head.state_token or "initial"
        batch_id = _attempt_hash(scope_key, content_hash, previous_state)
        attempt = FinanceImportAttempt.objects.create(
            batch_id=batch_id,
            scope_key=scope_key,
            raw_file_hash=payload["rawFileHash"],
            content_hash=content_hash,
            outcome="processing",
            actor_email=actor_email,
            metadata={"fileName": payload["fileName"], "fileSizeBytes": payload["fileSizeBytes"]},
        )
        head.status = "processing"
        head.owner_token = str(attempt.id)
        head.current_batch_id = batch_id
        head.generation += 1
        head.owner_started_at = now
        head.heartbeat_at = now
        head.save()
        warnings = list(payload["warnings"])[:MAX_WARNING_COUNT]  # type: ignore[arg-type]
        subject_count = len({
            str(line["subjectName"])
            for month in months
            for line in month["lines"]
            if line["section"] == "kingdee"
        })
        batch = FinanceImportBatch.objects.create(
            id=batch_id,
            source=FINANCE_IMPORT_SOURCE,
            file_name=payload["fileName"],
            file_size_bytes=payload["fileSizeBytes"],
            file_hash=batch_id,
            raw_file_hash=payload["rawFileHash"],
            content_hash=content_hash,
            scope_key=scope_key,
            status="processing",
            row_count=row_count,
            warning_count=len(warnings),
            parsed_month_count=len(months),
            subject_count=subject_count,
            months_json=month_keys,
            warnings_json=warnings,
            actor_email=actor_email,
            created_at=now_text,
        )
        for month in months:
            month_key = str(month["month"])
            FinanceLine.objects.filter(month=month_key).delete()
            FinanceMonth.objects.update_or_create(
                month=month_key,
                defaults={
                    "batch_id": batch_id,
                    "sheet_name": month["sheetName"],
                    "business_name": month["businessName"],
                    "source_file_name": payload["fileName"],
                    "status": "completed",
                    "shop_count": month["shopCount"],
                    "subject_count": month["subjectCount"],
                    "imported_at": now_text,
                },
            )
            FinanceLine.objects.bulk_create(
                [_line_model(line, now_text) for line in month["lines"]],
                batch_size=1_000,
            )
        next_state = _next_state_token(previous_state, batch_id, content_hash, row_count)
        batch.status = "completed"
        batch.inserted_count = row_count
        batch.imported_month_count = len(months)
        batch.completed_at = now_text
        batch.published_state_token = next_state
        batch.save()
        FinanceImportFingerprint.objects.create(
            batch_id=batch_id,
            scope_key=scope_key,
            content_hash=content_hash,
            raw_file_hash=payload["rawFileHash"],
            row_count=row_count,
            published_state_token=next_state,
        )
        revision, _ = FinanceDataRevision.objects.select_for_update().get_or_create(
            domain="finance", defaults={"revision": 0, "source_digest": ZERO_TOKEN}
        )
        revision.revision += 1
        revision.source_digest = next_state
        revision.save()
        head.state_token = next_state
        head.status = "ready"
        head.owner_token = ""
        head.current_batch_id = batch_id
        head.owner_started_at = None
        head.heartbeat_at = now
        head.save()
        attempt.outcome = "imported"
        attempt.completed_at = now
        attempt.save(update_fields=["outcome", "completed_at"])
        published_count = FinanceLine.objects.filter(month__in=month_keys).count()
        owned_months = FinanceMonth.objects.filter(
            month__in=month_keys, batch_id=batch_id, status="completed"
        ).count()
        if published_count != row_count or owned_months != len(month_keys):
            raise FinanceApiError(
                "财报导入完成后落库回查不一致",
                code="finance_publish_verification_failed",
                status=503,
            )
        result_batch = batch_payload(batch)
    return {
        "ok": True,
        "status": "imported",
        "message": f"月度财报导入成功：{'、'.join(month_keys)}",
        "batch": result_batch,
        "importedMonths": month_keys,
        "skippedMonths": [],
        "warnings": list(payload["warnings"]),
    }


def import_finance_payload(payload: object, actor_email: str) -> dict[str, object]:
    validated = validate_import_payload(payload)
    actor = actor_email.strip().lower()
    if not actor or len(actor) > 320:
        raise FinanceApiError("财务导入缺少有效执行人", status=403, code="access_denied")
    if validated["disposition"] == "rejected":
        return _record_rejection(validated, actor)
    try:
        return _import_prepared(validated, actor)
    except IntegrityError as error:
        raise FinanceApiError(
            "财务导入遇到并发冲突，请刷新后重试",
            status=409,
            code="conflict",
        ) from error


def list_import_batches(page: int, page_size: int) -> dict[str, object]:
    offset = (page - 1) * page_size
    queryset = FinanceImportBatch.objects.order_by("-created_at", "-id")
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
