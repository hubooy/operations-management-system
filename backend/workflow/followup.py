from __future__ import annotations

import base64
import binascii
import hashlib
from ipaddress import ip_address
import json
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlsplit

from django.db import connection, transaction
from django.db.models import F, Max
from django.utils import timezone

from sales.auth import Principal
from sales.models import ErpProductMaster, SalesImportBatch, SalesOrderLine

from .errors import WorkflowApiError
from .models import (
    NewProductLine,
    NewProductLineCode,
    NewProductWeeklyDelivery,
    NewProductWeeklyReportConfig,
)
from .revisions import bump_revision
from .write_requests import lock_active_authority


MAX_LINES = 1_000
MAX_CODES_PER_LINE = 500
MAX_CATALOG_SCAN = 100_000
MAX_PRODUCT_IMAGE_BYTES = 300 * 1024
MAX_PRODUCT_IMAGE_DIMENSION = 4_096
MAX_PRODUCT_IMAGE_PIXELS = 16_000_000
MAX_EMBEDDED_IMAGE_LINES = 200
REPORT_TIMELINE_START = date(2026, 8, 3)
APPROVED_DINGTALK_GROUP = "测试群聊"
APPROVED_DINGTALK_ROBOT = "志高助手"
LINE_FIELDS = {
    "name", "matchTerms", "productImageUrl", "productImage", "monitoringStartDate",
    "weeklyUnitTarget", "weeklySalesTargetCents", "active", "codes",
}
LINE_UPDATE_FIELDS = LINE_FIELDS | {"expectedVersion"}
REPORT_CONFIG_FIELDS = {
    "enabled", "targetGroupName", "robotName", "sendWeekday",
    "sendLocalTime", "expectedVersion",
}


def _error(message: str, *, code: str = "invalid_request", status: int = 400) -> WorkflowApiError:
    return WorkflowApiError(message, code=code, status=status)


def _object(value: object, allowed: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not set(value).issubset(allowed):
        raise _error(f"{label}包含未知字段或格式无效")
    return value


def _text(value: object, label: str, maximum: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise _error(f"{label}必须是文本")
    result = value.strip()
    if required and not result:
        raise _error(f"{label}不能为空")
    if len(result) > maximum or any(ord(character) < 32 for character in result):
        raise _error(f"{label}超出允许范围")
    return result


def _calendar_date(value: object, label: str, *, required: bool = True) -> date | None:
    if value in (None, "") and not required:
        return None
    if not isinstance(value, str):
        raise _error(f"{label}必须为 YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise _error(f"{label}必须为真实的 YYYY-MM-DD 日期") from error
    if parsed.isoformat() != value:
        raise _error(f"{label}必须为真实的 YYYY-MM-DD 日期")
    return parsed


def _integer(value: object, label: str, *, minimum: int, maximum: int, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise _error(f"{label}超出允许范围")
    return value


def _terms(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 20:
        raise _error("匹配关键词最多支持 20 个")
    output: list[str] = []
    for raw in value:
        term = _text(raw, "匹配关键词", 80, required=True)
        if term not in output:
            output.append(term)
    return output


def _url(value: object, label: str) -> str:
    text = _text(value, label, 1_000)
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError as error:
        raise _error(f"{label}不是有效链接") from error
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    try:
        port = parsed.port
    except ValueError as error:
        raise _error(f"{label}不是有效链接") from error
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password or port not in {None, 443}:
        raise _error(f"{label}必须是无账号信息的标准 HTTPS 链接")
    if hostname == "localhost" or hostname.endswith(".local"):
        raise _error(f"{label}不能指向本机或内网地址")
    try:
        address = ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise _error(f"{label}不能指向本机或内网地址")
    return text


def _jpeg_dimensions(content: bytes) -> tuple[int, int]:
    if len(content) < 4 or not content.startswith(b"\xff\xd8") or not content.endswith(b"\xff\xd9"):
        raise _error("产品图内容不是有效的 JPEG 图片")
    position = 2
    dimensions: tuple[int, int] | None = None
    start_of_frame = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while position + 4 <= len(content):
        if content[position] != 0xFF:
            raise _error("产品图内容不是有效的 JPEG 图片")
        while position < len(content) and content[position] == 0xFF:
            position += 1
        if position >= len(content):
            break
        marker = content[position]
        position += 1
        if marker in {0x01, 0xD8, 0xD9}:
            continue
        if position + 2 > len(content):
            break
        segment_length = int.from_bytes(content[position:position + 2], "big")
        if segment_length < 2 or position + segment_length > len(content):
            raise _error("产品图内容不是有效的 JPEG 图片")
        if marker == 0xDA:
            if dimensions is None or position + segment_length >= len(content) - 2:
                raise _error("产品图内容不是有效的 JPEG 图片")
            return dimensions
        if marker in start_of_frame:
            if segment_length < 7:
                raise _error("产品图内容不是有效的 JPEG 图片")
            height = int.from_bytes(content[position + 3:position + 5], "big")
            width = int.from_bytes(content[position + 5:position + 7], "big")
            if width < 1 or height < 1:
                raise _error("产品图尺寸无效")
            dimensions = (width, height)
        position += segment_length
    raise _error("产品图缺少有效的尺寸信息")


def _uploaded_product_image(value: object) -> dict[str, object]:
    if value is None:
        return {
            "product_image_url": "", "product_image_file_name": "", "product_image_mime_type": "",
            "product_image_size_bytes": 0, "product_image_sha256": "", "product_image_bytes": None,
        }
    allowed = {"fileName", "mimeType", "sizeBytes", "sha256", "dataBase64"}
    if not isinstance(value, dict) or set(value) != allowed:
        raise _error("产品图上传内容无效")
    file_name = _text(value.get("fileName"), "产品图文件名", 255, required=True)
    if file_name != re.split(r"[\\/]", file_name)[-1] or not file_name.casefold().endswith((".jpg", ".jpeg")):
        raise _error("产品图文件名必须是 JPG 图片")
    if value.get("mimeType") != "image/jpeg":
        raise _error("产品图上传格式必须是 JPEG")
    size = _integer(value.get("sizeBytes"), "产品图大小", minimum=1, maximum=MAX_PRODUCT_IMAGE_BYTES)
    sha256 = _text(value.get("sha256"), "产品图摘要", 64, required=True).casefold()
    encoded = value.get("dataBase64")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256) or not isinstance(encoded, str) or len(encoded) > 410_000:
        raise _error("产品图上传内容无效")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise _error("产品图上传内容无效") from error
    if len(content) != size or hashlib.sha256(content).hexdigest() != sha256:
        raise _error("产品图大小或完整性校验失败")
    width, height = _jpeg_dimensions(content)
    if width > MAX_PRODUCT_IMAGE_DIMENSION or height > MAX_PRODUCT_IMAGE_DIMENSION or width * height > MAX_PRODUCT_IMAGE_PIXELS:
        raise _error("产品图尺寸超出允许范围")
    return {
        "product_image_url": "", "product_image_file_name": file_name, "product_image_mime_type": "image/jpeg",
        "product_image_size_bytes": size, "product_image_sha256": sha256, "product_image_bytes": content,
    }


def _normalized_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", normalized)


def _local_clock() -> tuple[date, str]:
    local = datetime.now().astimezone()
    offset = local.utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    zone = local.tzname() or "本机时区"
    return local.date(), f"{zone} (UTC{sign}{hours:02d}:{minutes:02d})"


def _serialize_code(item: NewProductLineCode) -> dict[str, object]:
    return {
        "id": str(item.id),
        "productCode": item.product_code,
        "productName": item.product_name,
        "source": item.source,
        "sourceBatchId": item.source_batch_id,
        "active": item.active,
        "createdAt": item.created_at.isoformat(),
    }


def _line_image_source(item: NewProductLine, *, embed_uploaded: bool = False) -> str:
    if item.product_image_size_bytes > 0:
        if embed_uploaded:
            if not item.product_image_bytes:
                raise _error("产品图内容缺失", code="conflict", status=409)
            encoded = base64.b64encode(bytes(item.product_image_bytes)).decode("ascii")
            return f"data:{item.product_image_mime_type};base64,{encoded}"
        return f"/api/workflow/new-product-lines/{item.id}/image?v={item.version}"
    return item.product_image_url


def _serialize_line(item: NewProductLine) -> dict[str, object]:
    codes = sorted(list(item.codes.all()), key=lambda value: value.product_code)
    return {
        "id": str(item.id),
        "name": item.name,
        "matchTerms": list(item.match_terms or []),
        "productImageUrl": _line_image_source(item),
        "productImageFileName": item.product_image_file_name,
        "monitoringStartDate": item.monitoring_start_date.isoformat(),
        "weeklyUnitTarget": item.weekly_unit_target,
        "weeklySalesTargetCents": item.weekly_sales_target_cents,
        "active": item.active,
        "version": int(item.version),
        "codes": [_serialize_code(code) for code in codes],
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
    }


def get_product_line_image(line_id: object) -> dict[str, object] | None:
    item = NewProductLine.objects.filter(id=line_id, deleted_at__isnull=True).only(
        "product_image_file_name", "product_image_mime_type", "product_image_size_bytes",
        "product_image_sha256", "product_image_bytes",
    ).first()
    if item is None or not item.product_image_bytes:
        return None
    content = bytes(item.product_image_bytes)
    if len(content) != item.product_image_size_bytes or hashlib.sha256(content).hexdigest() != item.product_image_sha256:
        raise _error("产品图完整性校验失败", code="conflict", status=409)
    return {
        "fileName": item.product_image_file_name,
        "mimeType": item.product_image_mime_type,
        "sizeBytes": item.product_image_size_bytes,
        "sha256": item.product_image_sha256,
        "dataBase64": base64.b64encode(content).decode("ascii"),
    }


def list_product_lines() -> dict[str, object]:
    rows = list(
        NewProductLine.objects.filter(deleted_at__isnull=True)
        .defer("product_image_bytes")
        .prefetch_related("codes")
        .order_by("-active", "name", "id")[: MAX_LINES + 1]
    )
    if len(rows) > MAX_LINES:
        raise _error("新品产品线超过 1,000 条，请先停用历史产品线", code="payload_too_large", status=413)
    return {"items": [_serialize_line(row) for row in rows], "total": len(rows)}


def _normalized_line_fields(payload: dict[str, object], *, partial: bool) -> dict[str, object]:
    output: dict[str, object] = {}
    if "name" in payload or not partial:
        output["name"] = _text(payload.get("name"), "产品线名称", 160, required=True)
    if "matchTerms" in payload or not partial:
        output["match_terms"] = _terms(payload.get("matchTerms"))
    if "productImageUrl" in payload or not partial:
        output["product_image_url"] = _url(payload.get("productImageUrl"), "产品图链接")
    if "monitoringStartDate" in payload or not partial:
        output["monitoring_start_date"] = _calendar_date(payload.get("monitoringStartDate"), "监控开始日期")
    if "weeklyUnitTarget" in payload or not partial:
        output["weekly_unit_target"] = _integer(payload.get("weeklyUnitTarget"), "周销量目标", minimum=0, maximum=10**12, nullable=True)
    if "weeklySalesTargetCents" in payload or not partial:
        output["weekly_sales_target_cents"] = _integer(payload.get("weeklySalesTargetCents"), "周销售额目标", minimum=0, maximum=10**15, nullable=True)
    if "active" in payload or not partial:
        active = payload.get("active", True)
        if not isinstance(active, bool):
            raise _error("产品线启用状态无效")
        output["active"] = active
    return output


def _resolve_codes(raw: object) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_CODES_PER_LINE:
        raise _error("单条产品线最多关联 500 个吉客云代码")
    requested: list[str] = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict) or not set(value).issubset({"productCode", "productName"}):
            raise _error(f"第 {index + 1} 个吉客云代码格式无效")
        code = _text(value.get("productCode"), "吉客云代码", 200, required=True)
        if code not in requested:
            requested.append(code)
    masters = {
        row.product_code: row
        for row in ErpProductMaster.objects.filter(product_code__in=requested)
    }
    missing = [code for code in requested if code not in masters]
    if missing:
        raise _error(f"以下代码尚未出现在吉客云货品主数据：{'、'.join(missing[:10])}")
    return [
        {
            "product_code": code,
            "product_name": str(masters[code].product_name or code)[:500],
            "source_batch_id": str(masters[code].last_import_batch_id or "")[:200],
        }
        for code in requested
    ]


def _replace_codes(line: NewProductLine, rows: list[dict[str, str]], actor: str) -> None:
    requested = {row["product_code"]: row for row in rows}
    conflicts = list(
        NewProductLineCode.objects.filter(product_code__in=requested)
        .exclude(product_line=line)
        .values_list("product_code", "product_line__name")
    )
    if conflicts:
        code, name = conflicts[0]
        raise _error(f"吉客云代码 {code} 已归入产品线“{name}”", code="conflict", status=409)
    existing = {item.product_code: item for item in line.codes.all()}
    for code, row in requested.items():
        current = existing.get(code)
        if current:
            current.product_name = row["product_name"]
            current.source_batch_id = row["source_batch_id"]
            current.active = True
            current.save(update_fields=["product_name", "source_batch_id", "active", "updated_at"])
        else:
            NewProductLineCode.objects.create(
                product_line=line,
                product_code=code,
                product_name=row["product_name"],
                source="manual",
                source_batch_id=row["source_batch_id"],
                active=True,
                added_by=actor,
            )
    line.codes.exclude(product_code__in=requested).update(active=False)


def create_product_line(payload: object, principal: Principal) -> dict[str, object]:
    data = _object(payload, LINE_FIELDS, "新品产品线")
    if "productImage" in data and "productImageUrl" in data:
        raise _error("产品图上传与历史图片链接不能同时提交")
    fields = _normalized_line_fields(data, partial=False)
    if "productImage" in data:
        fields.update(_uploaded_product_image(data["productImage"]))
    codes = _resolve_codes(data.get("codes"))
    actor = principal.email.strip().lower()
    with transaction.atomic():
        lock_active_authority()
        if NewProductLine.objects.filter(name=fields["name"], deleted_at__isnull=True).exists():
            raise _error(f"产品线名称“{fields['name']}”已存在", code="conflict", status=409)
        line = NewProductLine.objects.create(**fields, created_by=actor, updated_by=actor)
        _replace_codes(line, codes, actor)
        bump_revision({"action": "product_line.created", "id": str(line.id)})
    return _serialize_line(NewProductLine.objects.prefetch_related("codes").get(id=line.id))


def update_product_line(line_id: object, payload: object, principal: Principal) -> dict[str, object]:
    data = _object(payload, LINE_UPDATE_FIELDS, "新品产品线更新")
    if "productImage" in data and "productImageUrl" in data:
        raise _error("产品图上传与历史图片链接不能同时提交")
    expected = _integer(data.get("expectedVersion"), "expectedVersion", minimum=1, maximum=9_007_199_254_740_991)
    fields = _normalized_line_fields(data, partial=True)
    if "productImage" in data:
        fields.update(_uploaded_product_image(data["productImage"]))
    elif "productImageUrl" in data:
        fields.update(_uploaded_product_image(None))
    replace_codes = "codes" in data
    codes = _resolve_codes(data.get("codes")) if replace_codes else []
    actor = principal.email.strip().lower()
    with transaction.atomic():
        lock_active_authority()
        line = NewProductLine.objects.select_for_update().filter(id=line_id, deleted_at__isnull=True).first()
        if line is None:
            raise _error("新品产品线不存在", code="not_found", status=404)
        if line.version != expected:
            raise _error("新品产品线已被其他人更新，请刷新后重试", code="version_conflict", status=409)
        next_name = str(fields.get("name", line.name))
        if NewProductLine.objects.filter(name=next_name, deleted_at__isnull=True).exclude(id=line.id).exists():
            raise _error(f"产品线名称“{next_name}”已存在", code="conflict", status=409)
        changed = False
        for key, value in fields.items():
            current = getattr(line, key)
            if key == "product_image_bytes" and current is not None:
                current = bytes(current)
            if current != value:
                setattr(line, key, value)
                changed = True
        if replace_codes:
            before = sorted(line.codes.filter(active=True).values_list("product_code", flat=True))
            after = sorted(row["product_code"] for row in codes)
            _replace_codes(line, codes, actor)
            changed = changed or before != after
        if not changed:
            raise _error("新品产品线没有发生变化")
        line.version += 1
        line.updated_by = actor
        line.save()
        bump_revision({"action": "product_line.updated", "id": str(line.id), "version": line.version})
    return _serialize_line(NewProductLine.objects.prefetch_related("codes").get(id=line.id))


def preview_product_line_codes(line_id: str, name: str, match_terms: list[str]) -> dict[str, object]:
    """Read-only candidate discovery; draft changes never update another product line."""
    name = _text(name, "产品线名称", 160, required=True)
    terms = _terms(match_terms)
    line_id = line_id.lower()
    if line_id and not NewProductLine.objects.filter(id=line_id, deleted_at__isnull=True).exists():
        raise _error("产品线不存在", code="not_found", status=404)
    lines = list(NewProductLine.objects.filter(deleted_at__isnull=True, active=True).exclude(id=line_id or None)[:MAX_LINES + 1])
    if len(lines) > MAX_LINES:
        raise _error("产品线超过学习安全上限", code="payload_too_large", status=413)
    normalize = lambda values: [term for value in values if len(term := _normalized_match_text(str(value))) >= 2]
    current_terms = normalize([name, *terms])
    if not current_terms:
        raise _error("请填写至少两个字符的学习关键词或产品线名称")
    other_terms = [(line.name, normalize([line.name, *(line.match_terms or [])])) for line in lines]
    catalog = list(ErpProductMaster.objects.order_by("product_code").values("product_code", "product_name")[:MAX_CATALOG_SCAN + 1])
    if len(catalog) > MAX_CATALOG_SCAN:
        raise _error("吉客云货品主数据超过学习安全上限", code="payload_too_large", status=413)
    matching = [row for row in catalog if any(term in _normalized_match_text(str(row["product_name"] or "")) for term in current_terms)]
    codes = [str(row["product_code"]) for row in matching]
    if len(codes) > MAX_CODES_PER_LINE:
        raise _error("匹配代码超过 500 个，请缩小关键词范围后学习", code="payload_too_large", status=413)
    owned = dict(NewProductLineCode.objects.filter(product_code__in=codes).values_list("product_code", "product_line_id"))
    candidates, ambiguous = [], []
    for master in matching:
        code = str(master["product_code"])
        normalized_name = _normalized_match_text(str(master["product_name"] or ""))
        conflicts = [label for label, matcher in other_terms if any(term in normalized_name for term in matcher)]
        row = {"productCode": code, "productName": str(master["product_name"] or code)}
        if code in owned and str(owned[code]) != line_id:
            ambiguous.append({**row, "reason": "已归属其他产品线"})
        elif conflicts:
            ambiguous.append({**row, "reason": "同时匹配：" + "、".join(conflicts[:5]) + (f"等 {len(conflicts)} 条产品线" if len(conflicts) > 5 else "")})
        else:
            candidates.append(row)
    return {"candidates": candidates, "ambiguous": ambiguous, "scanned": len(catalog)}


def learn_product_line_codes(principal: Principal, *, expected_source_batch_id: str = "") -> dict[str, object]:
    actor = principal.email.strip().lower()
    with transaction.atomic():
        lock_active_authority()
        lines = list(
            NewProductLine.objects.select_for_update()
            .filter(deleted_at__isnull=True, active=True)
            .prefetch_related("codes")
            .order_by("id")
        )
        if not lines:
            return {"added": [], "ambiguous": [], "scanned": 0}
        owned = set(NewProductLineCode.objects.values_list("product_code", flat=True))
        catalog = list(ErpProductMaster.objects.order_by("product_code")[: MAX_CATALOG_SCAN + 1])
        if len(catalog) > MAX_CATALOG_SCAN:
            raise _error("吉客云货品主数据超过自动学习安全上限", code="payload_too_large", status=413)
        catalog_batch_ids = {str(row.last_import_batch_id or "") for row in catalog}
        if expected_source_batch_id and expected_source_batch_id not in catalog_batch_ids:
            return {"added": [], "ambiguous": [], "scanned": len(catalog), "deferred": True}
        matchers: dict[object, list[str]] = {}
        for line in lines:
            terms = [line.name, *(line.match_terms or [])]
            normalized = list(dict.fromkeys(_normalized_match_text(str(term)) for term in terms))
            matchers[line.id] = [term for term in normalized if len(term) >= 2]
        added: list[dict[str, str]] = []
        ambiguous: list[dict[str, object]] = []
        touched: set[object] = set()
        for master in catalog:
            code = str(master.product_code)
            if code in owned:
                continue
            name = str(master.product_name or "")
            normalized_name = _normalized_match_text(name)
            matches = [line for line in lines if any(term in normalized_name for term in matchers[line.id])]
            if len(matches) == 1:
                line = matches[0]
                NewProductLineCode.objects.create(
                    product_line=line,
                    product_code=code[:200],
                    product_name=(name or code)[:500],
                    source="learned",
                    source_batch_id=str(master.last_import_batch_id or "")[:200],
                    active=True,
                    added_by=actor,
                )
                owned.add(code)
                touched.add(line.id)
                added.append({"productLineId": str(line.id), "productLineName": line.name, "productCode": code, "productName": name})
            elif len(matches) > 1:
                ambiguous.append({"productCode": code, "productName": name, "productLines": [line.name for line in matches]})
        if added:
            NewProductLine.objects.filter(id__in=touched).update(version=F("version") + 1, updated_by=actor)
            bump_revision({"action": "product_line.learned", "added": len(added), "lines": sorted(str(value) for value in touched)})
        return {"added": added[:1_000], "ambiguous": ambiguous[:1_000], "scanned": len(catalog), "deferred": False, "truncated": len(added) > 1_000 or len(ambiguous) > 1_000}


def _metric_rows(start: date, end: date) -> dict[str, dict[str, int]]:
    """Aggregate monitored codes while enforcing each line's own start date."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT sales.product_code, "
            "COALESCE(SUM(CASE WHEN sales.is_net_quantity_row THEN sales.quantity ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN sales.is_net_sales_row AND sales.allocated_amount_cents > 0 THEN sales.allocated_amount_cents ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN sales.is_net_sales_row AND sales.allocated_amount_cents < 0 THEN -sales.allocated_amount_cents ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN sales.is_net_sales_row THEN sales.allocated_amount_cents ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN sales.is_net_sales_row THEN sales.gross_profit_cents ELSE 0 END),0) "
            "FROM sales_order_lines AS sales "
            "INNER JOIN workflow_new_product_line_codes AS code ON code.product_code=sales.product_code AND code.active "
            "INNER JOIN workflow_new_product_lines AS line ON line.id=code.product_line_id AND line.deleted_at IS NULL AND line.active "
            "WHERE sales.is_business_row AND sales.business_date >= %s AND sales.business_date < %s "
            "AND sales.business_date >= line.monitoring_start_date "
            "GROUP BY sales.product_code",
            [start, end],
        )
        rows = cursor.fetchall()
    return {
        str(row[0]): {
            "netQuantity": int(row[1] or 0),
            "grossSalesCents": int(row[2] or 0),
            "refundAmountCents": int(row[3] or 0),
            "netSalesCents": int(row[4] or 0),
            "grossProfitCents": int(row[5] or 0),
        }
        for row in rows
    }


def _weekly_quantity_rows(start: date, end: date) -> dict[tuple[str, date], int]:
    """Return one bounded aggregate per monitored code and local calendar week."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT sales.product_code, sales.business_date, "
            "COALESCE(SUM(CASE WHEN sales.is_net_quantity_row THEN sales.quantity ELSE 0 END),0) "
            "FROM sales_order_lines AS sales "
            "INNER JOIN workflow_new_product_line_codes AS code ON code.product_code=sales.product_code AND code.active "
            "INNER JOIN workflow_new_product_lines AS line ON line.id=code.product_line_id AND line.deleted_at IS NULL AND line.active "
            "WHERE sales.is_business_row AND sales.business_date >= %s AND sales.business_date < %s "
            "AND sales.business_date >= line.monitoring_start_date "
            "GROUP BY sales.product_code, sales.business_date",
            [start, end],
        )
        rows = cursor.fetchall()
    result: dict[tuple[str, date], int] = {}
    for product_code, business_date, quantity in rows:
        day = business_date.date() if isinstance(business_date, datetime) else business_date
        if not isinstance(day, date):
            day = date.fromisoformat(str(day))
        week_start = day - timedelta(days=day.weekday())
        result[(str(product_code), week_start)] = result.get((str(product_code), week_start), 0) + int(quantity or 0)
    return result


def _report_weeks(selected_start: date, data_cutoff: date | None) -> list[dict[str, object]]:
    if selected_start < REPORT_TIMELINE_START:
        raise _error(f"周起始日期不能早于 {REPORT_TIMELINE_START.isoformat()}")
    weeks: list[dict[str, object]] = []
    cursor = REPORT_TIMELINE_START
    while cursor <= selected_start:
        week_end = cursor + timedelta(days=6)
        weeks.append({
            "weekStart": cursor.isoformat(),
            "weekEnd": week_end.isoformat(),
            "weekNumber": int(cursor.isocalendar().week),
            "label": f"第{cursor.isocalendar().week}周",
            "dateRange": f"{cursor:%m.%d}-{week_end:%m.%d}",
            "dataComplete": data_cutoff is not None and data_cutoff >= week_end,
        })
        cursor += timedelta(days=7)
    return weeks


def _zero_metric() -> dict[str, int]:
    return {"netQuantity": 0, "grossSalesCents": 0, "refundAmountCents": 0, "netSalesCents": 0, "grossProfitCents": 0}


def _sum_metrics(values: list[dict[str, int]]) -> dict[str, int]:
    result = _zero_metric()
    for value in values:
        for key in result:
            result[key] += int(value.get(key, 0))
    return result


def _rate(current: int, previous: int) -> float | None:
    if previous == 0:
        return None
    return (current - previous) / abs(previous)


def _line_status(current: dict[str, int], previous: dict[str, int], cumulative: dict[str, int], line: NewProductLine, as_of: date) -> str:
    if as_of < line.monitoring_start_date:
        return "not_started"
    if not line.codes.filter(active=True).exists():
        return "missing_codes"
    unit_hit = line.weekly_unit_target is not None and current["netQuantity"] >= line.weekly_unit_target
    sales_hit = line.weekly_sales_target_cents is not None and current["netSalesCents"] >= line.weekly_sales_target_cents
    if unit_hit or sales_hit:
        return "target_achieved"
    if current["netQuantity"] > 0 or current["netSalesCents"] > 0:
        return "selling"
    if previous["netQuantity"] > 0 or previous["netSalesCents"] > 0:
        return "stalled"
    if cumulative["netQuantity"] == 0 and cumulative["netSalesCents"] == 0:
        return "no_sales"
    return "selling"


def weekly_followup(*, week_start: date | None = None, embed_uploaded_images: bool = False) -> dict[str, object]:
    local_today, zone_label = _local_clock()
    start = week_start or (local_today - timedelta(days=local_today.weekday() + 7))
    if start.weekday() != 0:
        raise _error("周起始日期必须是本机日历中的星期一")
    end = start + timedelta(days=7)
    previous_start = start - timedelta(days=7)
    line_query = NewProductLine.objects.filter(deleted_at__isnull=True, active=True)
    if not embed_uploaded_images:
        line_query = line_query.defer("product_image_bytes")
    lines = list(line_query.prefetch_related("codes").order_by("name", "id")[: MAX_LINES + 1])
    if len(lines) > MAX_LINES:
        raise _error("新品产品线超过 1,000 条", code="payload_too_large", status=413)
    if embed_uploaded_images and len(lines) > MAX_EMBEDDED_IMAGE_LINES:
        raise _error("新品周报图片最多支持 200 条产品线", code="payload_too_large", status=413)
    current_by_code = _metric_rows(start, end)
    previous_by_code = _metric_rows(previous_start, start)
    earliest = min((line.monitoring_start_date for line in lines), default=start)
    cumulative_by_code = _metric_rows(earliest, end)
    data_cutoff = SalesOrderLine.objects.filter(is_business_row=True).aggregate(value=Max("business_date"))["value"]
    weeks = _report_weeks(start, data_cutoff)
    weekly_quantity_by_code = _weekly_quantity_rows(REPORT_TIMELINE_START, end)
    monitored_codes = {
        code.product_code
        for line in lines
        for code in line.codes.all()
        if code.active
    }
    brands_by_code = {
        str(product_code): str(brand or "").strip()
        for product_code, brand in ErpProductMaster.objects.filter(product_code__in=monitored_codes)
        .values_list("product_code", "brand")
    }
    latest_batch = SalesImportBatch.objects.filter(status="completed").order_by("-completed_at", "-id").first()
    items: list[dict[str, object]] = []
    for line in lines:
        codes = [code for code in line.codes.all() if code.active]
        brands = sorted({brands_by_code.get(code.product_code, "") for code in codes} - {""})
        current = _sum_metrics([current_by_code.get(code.product_code, _zero_metric()) for code in codes])
        previous = _sum_metrics([previous_by_code.get(code.product_code, _zero_metric()) for code in codes])
        cumulative = _sum_metrics([cumulative_by_code.get(code.product_code, _zero_metric()) for code in codes])
        margin_rate = current["grossProfitCents"] / current["netSalesCents"] if current["netSalesCents"] else None
        items.append({
            "id": str(line.id),
            "name": line.name,
            "brand": "、".join(brands) if brands else "志高",
            "productImageUrl": _line_image_source(line, embed_uploaded=embed_uploaded_images),
            "active": line.active,
            "monitoringStartDate": line.monitoring_start_date.isoformat(),
            "status": _line_status(current, previous, cumulative, line, local_today),
            "codeCount": len(codes),
            "codes": [
                {
                    **_serialize_code(code),
                    "current": current_by_code.get(code.product_code, _zero_metric()),
                    "previous": previous_by_code.get(code.product_code, _zero_metric()),
                }
                for code in codes
            ],
            "current": {**current, "grossMarginRate": margin_rate},
            "previous": previous,
            "cumulative": cumulative,
            "salesWeekOverWeekRate": _rate(current["netSalesCents"], previous["netSalesCents"]),
            "quantityWeekOverWeekRate": _rate(current["netQuantity"], previous["netQuantity"]),
            "weeklyNetQuantities": [
                sum(weekly_quantity_by_code.get((code.product_code, date.fromisoformat(str(week["weekStart"]))), 0) for code in codes)
                for week in weeks
            ],
            "weeklyUnitTarget": line.weekly_unit_target,
            "weeklySalesTargetCents": line.weekly_sales_target_cents,
        })
    items.sort(key=lambda item: (-int(item["current"]["netSalesCents"]), str(item["name"])))
    totals = _sum_metrics([item["current"] for item in items])
    incomplete = data_cutoff is None or data_cutoff < end - timedelta(days=1)
    report = {
        "timelineStart": REPORT_TIMELINE_START.isoformat(),
        "weeks": weeks,
        "weekStart": start.isoformat(),
        "weekEnd": (end - timedelta(days=1)).isoformat(),
        "endExclusive": end.isoformat(),
        "timezone": zone_label,
        "localToday": local_today.isoformat(),
        "dataCutoffDate": data_cutoff.isoformat() if data_cutoff else None,
        "dataIncomplete": incomplete,
        "latestBatch": None if latest_batch is None else {
            "id": latest_batch.id,
            "fileName": latest_batch.file_name,
            "completedAt": latest_batch.completed_at,
        },
        "summary": {
            "lineCount": len(items),
            "sellingCount": sum(1 for item in items if item["status"] in {"selling", "target_achieved"}),
            "noSalesCount": sum(1 for item in items if item["status"] == "no_sales"),
            "stalledCount": sum(1 for item in items if item["status"] == "stalled"),
            "targetAchievedCount": sum(1 for item in items if item["status"] == "target_achieved"),
            **totals,
        },
        "items": items,
    }
    report["messageText"] = render_weekly_message(report)
    report["reportSha256"] = hashlib.sha256(
        json.dumps({key: value for key, value in report.items() if key not in {"messageText", "reportSha256"}}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return report


def render_weekly_message(report: dict[str, object]) -> str:
    return "新品销售周报"


def get_report_config() -> dict[str, object]:
    config = NewProductWeeklyReportConfig.objects.get(id=1)
    latest = NewProductWeeklyDelivery.objects.order_by("-created_at", "-id").first()
    return {
        "enabled": config.enabled,
        "connectionMode": "dws_stream",
        "credentialsManagedExternally": True,
        "deliveryMode": "png_drive_preview_by_bot",
        "targetGroupName": config.target_group_name,
        "robotName": config.robot_name,
        "sendWeekday": int(config.send_weekday),
        "sendLocalTime": config.send_local_time.strftime("%H:%M"),
        "version": int(config.version),
        "updatedAt": config.updated_at.isoformat(),
        "lastDelivery": None if latest is None else {
            "weekStart": latest.week_start.isoformat(),
            "weekEnd": latest.week_end.isoformat(),
            "status": latest.status,
            "attemptCount": int(latest.attempt_count),
            "dataCutoffDate": latest.data_cutoff_date.isoformat() if latest.data_cutoff_date else None,
            "deliveredAt": latest.delivered_at.isoformat() if latest.delivered_at else None,
            "updatedAt": latest.updated_at.isoformat(),
        },
    }


def update_report_config(payload: object, principal: Principal) -> dict[str, object]:
    data = _object(payload, REPORT_CONFIG_FIELDS, "新品周报配置")
    expected = _integer(data.get("expectedVersion"), "expectedVersion", minimum=1, maximum=9_007_199_254_740_991)
    actor = principal.email.strip().lower()
    with transaction.atomic():
        lock_active_authority()
        config = NewProductWeeklyReportConfig.objects.select_for_update().get(id=1)
        if config.version != expected:
            raise _error("新品周报配置已被其他人更新，请刷新后重试", code="version_conflict", status=409)
        values: dict[str, Any] = {}
        if "enabled" in data:
            if not isinstance(data["enabled"], bool):
                raise _error("周报启用状态无效")
            values["enabled"] = data["enabled"]
        if "targetGroupName" in data:
            values["target_group_name"] = _text(data["targetGroupName"], "钉钉群名称", 200)
        if "robotName" in data:
            values["robot_name"] = _text(data["robotName"], "钉钉机器人名称", 160)
        if "sendWeekday" in data:
            values["send_weekday"] = _integer(data["sendWeekday"], "发送星期", minimum=0, maximum=6)
        if "sendLocalTime" in data:
            raw_time = _text(data["sendLocalTime"], "发送时间", 5, required=True)
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", raw_time):
                raise _error("发送时间必须为 HH:mm")
            hours, minutes = (int(value) for value in raw_time.split(":"))
            values["send_local_time"] = time(hours, minutes)
        next_enabled = bool(values.get("enabled", config.enabled))
        next_group = str(values.get("target_group_name", config.target_group_name))
        next_robot = str(values.get("robot_name", config.robot_name))
        if next_enabled and (not next_group or not next_robot):
            raise _error("启用周报前必须填写唯一的钉钉群名称和机器人名称")
        if next_group != APPROVED_DINGTALK_GROUP or next_robot != APPROVED_DINGTALK_ROBOT:
            raise _error("新品周报只允许由“志高助手”发送到“测试群聊”")
        changed = False
        for key, value in values.items():
            if getattr(config, key) != value:
                setattr(config, key, value)
                changed = True
        if not changed:
            raise _error("新品周报配置没有发生变化")
        config.version += 1
        config.updated_by = actor
        config.save()
        bump_revision({"action": "weekly_report.configured", "version": config.version, "enabled": config.enabled})
    return get_report_config()


def delivery_idempotency_key(report: dict[str, object], config: NewProductWeeklyReportConfig) -> str:
    value = "\0".join((
        str(report["weekStart"]),
        str(report["weekEnd"]),
        config.target_group_name,
        config.robot_name,
    ))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def claim_weekly_delivery(
    report: dict[str, object],
    config: NewProductWeeklyReportConfig,
    *,
    actor: str,
) -> tuple[NewProductWeeklyDelivery, bool]:
    """Create a single external-send claim; uncertain sends never auto-retry."""

    key = delivery_idempotency_key(report, config)
    with transaction.atomic():
        lock_active_authority()
        existing = NewProductWeeklyDelivery.objects.select_for_update().filter(idempotency_key=key).first()
        if existing is not None:
            return existing, False
        delivery = NewProductWeeklyDelivery.objects.create(
            week_start=date.fromisoformat(str(report["weekStart"])),
            week_end=date.fromisoformat(str(report["weekEnd"])),
            target_group_name=config.target_group_name,
            robot_name=config.robot_name,
            idempotency_key=key,
            report_sha256=str(report["reportSha256"]),
            data_cutoff_date=date.fromisoformat(str(report["dataCutoffDate"])) if report.get("dataCutoffDate") else None,
            status="claimed",
            claimed_by=actor[:320],
        )
        return delivery, True


def mark_weekly_delivery_sending(delivery_id: object) -> None:
    with transaction.atomic():
        lock_active_authority()
        delivery = NewProductWeeklyDelivery.objects.select_for_update().get(id=delivery_id)
        if delivery.status != "claimed":
            raise _error("新品周报发送任务状态已变化", code="conflict", status=409)
        delivery.status = "sending"
        delivery.save(update_fields=["status", "updated_at"])


def finish_weekly_delivery(delivery_id: object, *, provider_receipt: str) -> None:
    with transaction.atomic():
        lock_active_authority()
        delivery = NewProductWeeklyDelivery.objects.select_for_update().get(id=delivery_id)
        if delivery.status != "sending":
            raise _error("新品周报发送任务状态已变化", code="conflict", status=409)
        delivery.status = "delivered"
        delivery.provider_receipt = provider_receipt[:500]
        delivery.error_code = ""
        delivery.delivered_at = timezone.now()
        delivery.save(update_fields=["status", "provider_receipt", "error_code", "delivered_at", "updated_at"])
        bump_revision({"action": "weekly_report.delivered", "weekStart": delivery.week_start.isoformat()})


def mark_weekly_delivery_uncertain(delivery_id: object, *, error_code: str) -> None:
    """Persist ambiguity after the external call starts so retries cannot duplicate a message."""

    with transaction.atomic():
        lock_active_authority()
        delivery = NewProductWeeklyDelivery.objects.select_for_update().get(id=delivery_id)
        if delivery.status != "sending":
            return
        delivery.status = "uncertain"
        delivery.error_code = error_code[:120]
        delivery.save(update_fields=["status", "error_code", "updated_at"])
