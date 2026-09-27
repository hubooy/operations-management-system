from __future__ import annotations

import json
import hashlib
import re
import uuid

from django.db import IntegrityError, transaction
from django.db.models import Case, F, IntegerField, Value, When
from django.utils import timezone

from .errors import FinanceApiError
from .import_service import JS_SAFE_INTEGER, assert_active_authority
from .models import FinanceDataRevision, FinanceLine, FinanceTarget, FinanceTargetDeletionAudit
from .serialization import target_payload


MAX_TARGET_AMOUNT_CENTS = 10_000_000_000_000
ANNUAL_TARGET_IMPORT_SCHEMA_VERSION = "finance-annual-target-import-v1"
MAX_ANNUAL_TARGET_IMPORT_ROWS = 500


def _bump_revision(material: str) -> None:
    revision, _ = FinanceDataRevision.objects.select_for_update().get_or_create(
        domain="finance", defaults={"revision": 0, "source_digest": "0" * 64}
    )
    revision.revision += 1
    revision.source_digest = hashlib.sha256(
        f"finance-revision-v1\n{revision.source_digest}\n{material}\n{revision.revision}".encode()
    ).hexdigest()
    revision.save()


def _text(value: object, label: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise FinanceApiError(f"{label}必须是字符串")
    result = value.strip()
    if (required and not result) or len(result) > maximum:
        raise FinanceApiError(f"{label}长度无效")
    return result


def _nonnegative_integer(value: object, label: str, maximum: int) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise FinanceApiError(f"{label}必须是范围内的 JSON 非负安全整数")
    return value


def validate_target_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise FinanceApiError("请求内容不是有效 JSON")
    allowed = {
        "id", "expectedVersion", "periodType", "periodKey", "platform", "shopName",
        "category", "manager", "salesTargetCents", "profitTargetCents", "smallMarginBps",
        "grossMarginBps", "inventoryCleanupTargetCents", "promotionFeeRatioBps", "stagnantInventoryTargetCents",
    }
    if set(payload) - allowed:
        raise FinanceApiError("经营目标请求包含未知字段")
    period_type = _text(payload.get("periodType"), "目标周期类型", 16, required=True)
    period_key = _text(payload.get("periodKey"), "目标周期", 100, required=True)
    if period_type not in {"month", "year", "project"}:
        raise FinanceApiError("目标周期类型无效")
    if period_type == "month":
        import re
        if not re.fullmatch(r"(?:19|20|21)\d{2}-(?:0[1-9]|1[0-2])", period_key):
            raise FinanceApiError("月度目标周期应为真实的 YYYY-MM")
    if period_type == "year":
        import re
        if not re.fullmatch(r"(?:19|20|21)\d{2}", period_key):
            raise FinanceApiError("年度目标周期应为 YYYY")
    identifier = ""
    if "id" in payload:
        identifier = _text(payload["id"], "目标 ID", 128)
    expected_version = payload.get("expectedVersion")
    if identifier:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1 or expected_version > JS_SAFE_INTEGER:
            raise FinanceApiError("编辑经营目标必须提供有效的 expectedVersion")
    elif expected_version is not None:
        raise FinanceApiError("新建目标不能携带 expectedVersion")
    platform = "" if period_type == "project" else _text(payload.get("platform", ""), "平台", 100, required=True)
    shop_name = "" if period_type == "project" else _text(payload.get("shopName", ""), "店铺", 100, required=True)
    return {
        "id": identifier,
        "expectedVersion": expected_version,
        "periodType": period_type,
        "periodKey": period_key,
        "platform": platform,
        "shopName": shop_name,
        "category": "" if period_type == "project" else _text(payload.get("category", ""), "品类", 100),
        "manager": _text(payload.get("manager", ""), "负责人", 120),
        "salesTargetCents": _nonnegative_integer(payload.get("salesTargetCents"), "销售额目标", MAX_TARGET_AMOUNT_CENTS),
        "profitTargetCents": _nonnegative_integer(payload.get("profitTargetCents"), "利润目标", MAX_TARGET_AMOUNT_CENTS),
        "grossMarginBps": _nonnegative_integer(payload.get("grossMarginBps"), "大毛利率目标", 10_000),
        "smallMarginBps": _nonnegative_integer(payload.get("smallMarginBps"), "小毛利率目标", 10_000),
        "inventoryCleanupTargetCents": _nonnegative_integer(payload.get("inventoryCleanupTargetCents"), "库存清理目标", MAX_TARGET_AMOUNT_CENTS),
        "promotionFeeRatioBps": _nonnegative_integer(payload.get("promotionFeeRatioBps"), "推广费占比目标", 10_000),
        "stagnantInventoryTargetCents": _nonnegative_integer(payload.get("stagnantInventoryTargetCents"), "呆滞库存目标", MAX_TARGET_AMOUNT_CENTS),
    }


def list_targets(page: int, page_size: int, *, annual_year: str | None = None) -> dict[str, object]:
    offset = (page - 1) * page_size
    queryset = FinanceTarget.objects.annotate(
        period_rank=Case(
            When(period_type="month", then=Value(1)),
            When(period_type="year", then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        )
    ).order_by("period_rank", "-period_key", "platform", "shop_name", "category")
    if annual_year is not None:
        queryset = queryset.filter(period_type="year", period_key=annual_year, category="")
    total = queryset.count()
    selected = list(queryset[offset : offset + page_size])
    return {
        "items": [target_payload(item) for item in selected],
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "returned": len(selected),
            "truncated": offset + len(selected) < total,
        },
    }


def target_options() -> dict[str, object]:
    base = (
        FinanceLine.objects.filter(scope_type="shop")
        .exclude(scope_name="")
        .values_list("group_name", "scope_name")
        .distinct()
    )
    pairs = sorted(
        {(platform or "未分组", name) for platform, name in base},
        key=lambda item: (item[0], item[1]),
    )
    shops = [
        {
            "key": json.dumps([platform, name], ensure_ascii=False, separators=(",", ":")),
            "platform": platform,
            "name": name,
        }
        for platform, name in pairs[:300]
    ]
    return {
        "shops": shops,
        "projects": ["8系列"],
        "pagination": {
            "shops": {
                "total": len(pairs),
                "returned": len(shops),
                "truncated": len(shops) < len(pairs),
            }
        },
    }


def _target_values(input_value: dict[str, object], now_text: str) -> dict[str, object]:
    return {
        "period_type": input_value["periodType"],
        "period_key": input_value["periodKey"],
        "platform": input_value["platform"],
        "shop_name": input_value["shopName"],
        "category": input_value["category"],
        "manager": input_value["manager"],
        "sales_target_cents": input_value["salesTargetCents"],
        "profit_target_cents": input_value["profitTargetCents"],
        "gross_margin_bps": input_value["grossMarginBps"],
        "small_margin_bps": input_value["smallMarginBps"],
        "inventory_cleanup_target_cents": input_value["inventoryCleanupTargetCents"],
        "promotion_fee_ratio_bps": input_value["promotionFeeRatioBps"],
        "stagnant_inventory_target_cents": input_value["stagnantInventoryTargetCents"],
        "updated_at": now_text,
    }


def upsert_target(payload: object) -> tuple[dict[str, object], bool]:
    input_value = validate_target_payload(payload)
    identifier = str(input_value["id"] or uuid.uuid4())
    expected_version = input_value["expectedVersion"]
    now_text = timezone.now().isoformat()
    with transaction.atomic():
        assert_active_authority()
        exists = FinanceTarget.objects.filter(id=identifier).exists()
        if exists:
            try:
                changed = FinanceTarget.objects.filter(
                    id=identifier, version=expected_version
                ).update(
                    **_target_values(input_value, now_text),
                    version=F("version") + 1,
                )
            except IntegrityError as error:
                raise FinanceApiError(
                    "同周期、平台、店铺和品类的经营目标已存在，请刷新后编辑",
                    status=409,
                    code="version_conflict",
                ) from error
            if changed != 1:
                if not FinanceTarget.objects.filter(id=identifier).exists():
                    raise FinanceApiError("经营目标不存在或已被删除", status=404, code="not_found")
                raise FinanceApiError(
                    "经营目标已被其他人更新，请刷新后重试",
                    status=409,
                    code="version_conflict",
                )
            target = FinanceTarget.objects.get(id=identifier)
            _bump_revision(f"target:update:{identifier}:{target.version}")
            return target_payload(target), False
        if expected_version is not None:
            raise FinanceApiError("经营目标不存在或已被删除", status=404, code="not_found")
        try:
            target = FinanceTarget.objects.create(
                id=identifier,
                version=1,
                created_at=now_text,
                **_target_values(input_value, now_text),
            )
        except IntegrityError as error:
            raise FinanceApiError(
                "经营目标 ID 或同周期、平台、店铺和品类已存在，请刷新后编辑",
                status=409,
                code="version_conflict",
            ) from error
        _bump_revision(f"target:create:{identifier}:1")
        return target_payload(target), True


def _annual_import_row(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise FinanceApiError("年度目标行不是有效对象")
    allowed = {
        "rowNumber", "storeLabel", "manager", "salesTargetCents", "profitTargetCents",
        "grossMarginBps", "promotionFeeRatioBps",
    }
    if set(payload) != allowed:
        raise FinanceApiError("年度目标行字段不完整或包含未知字段")
    row_number = _nonnegative_integer(payload.get("rowNumber"), "Excel 行号", 1_000_000)
    if row_number < 1:
        raise FinanceApiError("Excel 行号无效")
    return {
        "rowNumber": row_number,
        "storeLabel": _text(payload.get("storeLabel"), "店铺", 200, required=True),
        "manager": _text(payload.get("manager"), "负责人", 120),
        "salesTargetCents": _nonnegative_integer(payload.get("salesTargetCents"), "销售额目标", MAX_TARGET_AMOUNT_CENTS),
        "profitTargetCents": _nonnegative_integer(payload.get("profitTargetCents"), "利润目标", MAX_TARGET_AMOUNT_CENTS),
        "grossMarginBps": _nonnegative_integer(payload.get("grossMarginBps"), "大毛利率目标", 10_000),
        "promotionFeeRatioBps": _nonnegative_integer(payload.get("promotionFeeRatioBps"), "推广费占比目标", 10_000),
    }


def _trailing_note_alias(value: str) -> str | None:
    alias = re.sub(r"\s*(?:（[^（）]{1,80}）|\([^()]{1,80}\))\s*$", "", value).strip()
    return alias if alias and alias != value else None


def _resolve_import_shops(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    known = {
        (str(platform or "未分组").strip(), str(name).strip())
        for platform, name in FinanceLine.objects.filter(scope_type="shop")
        .exclude(scope_name="").values_list("group_name", "scope_name").distinct()[:5001]
    }
    known.update(
        (str(platform).strip(), str(name).strip())
        for platform, name in FinanceTarget.objects.exclude(platform="").exclude(shop_name="")
        .values_list("platform", "shop_name").distinct()[:5001]
    )
    if len(known) > 5000:
        raise FinanceApiError("可匹配店铺数量超过导入上限", code="service_unavailable", status=503)
    by_name: dict[str, set[tuple[str, str]]] = {}
    by_alias: dict[str, set[tuple[str, str]]] = {}
    by_platform_alias: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for pair in known:
        by_name.setdefault(pair[1], set()).add(pair)
        alias = _trailing_note_alias(pair[1])
        if alias is not None:
            by_alias.setdefault(alias, set()).add(pair)
            by_platform_alias.setdefault((pair[0], alias), set()).add(pair)
    resolved = []
    identities: dict[tuple[str, str], int] = {}
    for row in rows:
        label = str(row["storeLabel"])
        matches = set(by_name.get(label, set()))
        pair: tuple[str, str] | None = next(iter(matches)) if len(matches) == 1 else None
        normalized = re.sub(r"[－—–]", "-", label)
        if pair is None and "-" in normalized:
            platform, shop_name = (item.strip() for item in normalized.split("-", 1))
            candidate = (platform, shop_name)
            if platform and shop_name and candidate in known:
                pair = candidate
            elif platform and shop_name:
                alias_matches = by_platform_alias.get(candidate, set())
                matches.update(alias_matches)
                if len(alias_matches) == 1:
                    pair = next(iter(alias_matches))
        if pair is None:
            alias_matches = by_alias.get(label, set())
            matches.update(alias_matches)
            if len(alias_matches) == 1:
                pair = next(iter(alias_matches))
        if pair is None:
            reason = "匹配到多个候选" if len(matches) > 1 else "未在财报或现有目标中找到"
            raise FinanceApiError(f"第 {row['rowNumber']} 行店铺{reason}：{label}")
        previous = identities.get(pair)
        if previous is not None:
            raise FinanceApiError(f"第 {row['rowNumber']} 行与第 {previous} 行指向同一平台店铺")
        identities[pair] = int(row["rowNumber"])
        resolved.append({**row, "platform": pair[0], "shopName": pair[1]})
    return resolved


def import_annual_targets(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise FinanceApiError("请求内容不是有效 JSON")
    allowed = {
        "schemaVersion", "year", "fileName", "fileSizeBytes", "fileSha256", "sheetName",
        "headerRowNumber", "sourceRowCount", "skippedRowCount", "rows",
    }
    if set(payload) != allowed:
        raise FinanceApiError("年度目标导入请求字段不完整或包含未知字段")
    if payload.get("schemaVersion") != ANNUAL_TARGET_IMPORT_SCHEMA_VERSION:
        raise FinanceApiError("年度目标导入版本不受支持")
    year = _text(payload.get("year"), "目标年份", 4, required=True)
    if not re.fullmatch(r"(?:19|20|21)\d{2}", year):
        raise FinanceApiError("导入年份应为 YYYY")
    file_name = _text(payload.get("fileName"), "文件名", 200, required=True)
    if not file_name.lower().endswith(".xlsx"):
        raise FinanceApiError("仅支持 .xlsx 店铺年度目标文件")
    file_size = _nonnegative_integer(payload.get("fileSizeBytes"), "文件大小", 2 * 1024 * 1024)
    if file_size < 1:
        raise FinanceApiError("上传文件为空")
    file_sha256 = _text(payload.get("fileSha256"), "文件摘要", 64, required=True).lower()
    if not re.fullmatch(r"[a-f0-9]{64}", file_sha256):
        raise FinanceApiError("文件摘要无效")
    _text(payload.get("sheetName"), "工作表名称", 100, required=True)
    header_row = _nonnegative_integer(payload.get("headerRowNumber"), "表头行号", 1_000_000)
    source_rows = _nonnegative_integer(payload.get("sourceRowCount"), "源店铺行数", 1_000_000)
    skipped_rows = _nonnegative_integer(payload.get("skippedRowCount"), "跳过行数", 1_000_000)
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows or len(raw_rows) > MAX_ANNUAL_TARGET_IMPORT_ROWS:
        raise FinanceApiError(f"有效目标必须为 1 到 {MAX_ANNUAL_TARGET_IMPORT_ROWS} 行")
    rows = [_annual_import_row(row) for row in raw_rows]
    if source_rows != len(rows) + skipped_rows or header_row < 1:
        raise FinanceApiError("年度目标导入行数摘要不一致")
    resolved = _resolve_import_shops(rows)
    now_text = timezone.now().isoformat()
    created = 0
    updated = 0
    with transaction.atomic():
        assert_active_authority()
        existing = {
            (item.platform, item.shop_name): item
            for item in FinanceTarget.objects.select_for_update().filter(
                period_type="year", period_key=year, category=""
            )
        }
        for row in resolved:
            pair = (str(row["platform"]), str(row["shopName"]))
            item = existing.get(pair)
            if item is None:
                item = FinanceTarget(
                    id=str(uuid.uuid4()), period_type="year", period_key=year,
                    platform=pair[0], shop_name=pair[1], category="", version=1,
                    created_at=now_text,
                )
                created += 1
            else:
                item.version += 1
                updated += 1
            item.manager = str(row["manager"])
            item.sales_target_cents = int(row["salesTargetCents"])
            item.profit_target_cents = int(row["profitTargetCents"])
            item.gross_margin_bps = int(row["grossMarginBps"])
            item.promotion_fee_ratio_bps = int(row["promotionFeeRatioBps"])
            item.updated_at = now_text
            try:
                item.save()
            except IntegrityError as error:
                raise FinanceApiError(
                    "导入期间目标已被其他请求修改，请刷新后重试",
                    status=409,
                    code="version_conflict",
                ) from error
            existing[pair] = item
        verified = {
            (item.platform, item.shop_name): item
            for item in FinanceTarget.objects.filter(
                period_type="year", period_key=year, category="",
                platform__in={str(row["platform"]) for row in resolved},
                shop_name__in={str(row["shopName"]) for row in resolved},
            )
        }
        for row in resolved:
            item = verified.get((str(row["platform"]), str(row["shopName"])))
            expected = (
                int(row["salesTargetCents"]), int(row["profitTargetCents"]),
                int(row["grossMarginBps"]), int(row["promotionFeeRatioBps"]), str(row["manager"]),
            )
            actual = None if item is None else (
                int(item.sales_target_cents), int(item.profit_target_cents),
                int(item.gross_margin_bps), int(item.promotion_fee_ratio_bps), item.manager,
            )
            if actual != expected:
                raise FinanceApiError("年度目标导入后回查不一致", code="service_unavailable", status=503)
        _bump_revision(f"annual-target-import:{year}:{file_sha256}:{created}:{updated}")
    return {
        "ok": True,
        "status": "imported",
        "year": year,
        "fileName": file_name,
        "createdCount": created,
        "updatedCount": updated,
        "importedCount": len(resolved),
        "skippedCount": skipped_rows,
        "preservedUnlistedTargets": True,
        "items": [target_payload(existing[(str(row["platform"]), str(row["shopName"]))]) for row in resolved],
    }


def delete_target(
    identifier: str,
    expected_version: int,
    actor: str,
    reason: str,
) -> dict[str, object]:
    normalized_actor = actor.strip().lower()
    normalized_reason = reason.strip()
    if not normalized_actor or len(normalized_actor) > 320:
        raise FinanceApiError("删除操作缺少有效执行人")
    if not normalized_reason or len(normalized_reason) > 200:
        raise FinanceApiError("删除原因必须为 1 到 200 字")
    if isinstance(expected_version, bool) or expected_version < 1 or expected_version > JS_SAFE_INTEGER:
        raise FinanceApiError("删除经营目标必须提供有效的 expectedVersion")
    with transaction.atomic():
        assert_active_authority()
        try:
            target = FinanceTarget.objects.select_for_update().get(id=identifier)
        except FinanceTarget.DoesNotExist as error:
            raise FinanceApiError("经营目标不存在或已被删除", status=404, code="not_found") from error
        if target.version != expected_version:
            raise FinanceApiError(
                "经营目标已被其他人更新，请刷新后重试",
                status=409,
                code="version_conflict",
            )
        audit = FinanceTargetDeletionAudit.objects.create(
            target_id=target.id,
            period_type=target.period_type,
            period_key=target.period_key,
            platform=target.platform,
            shop_name=target.shop_name,
            category=target.category,
            actor=normalized_actor,
            old_version=target.version,
            expected_version=expected_version,
            reason=normalized_reason,
        )
        target.delete()
        _bump_revision(f"target:delete:{identifier}:{expected_version}")
    return {"deleted": True, "auditId": str(audit.audit_id)}
