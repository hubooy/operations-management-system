"""Guangdong watchlists and risk projections; never writes stock or sales facts."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, timedelta

from django.db import transaction
from django.db.models import F, Q, Window
from django.db.models.functions import RowNumber
from django.utils import timezone

from sales.models import ErpProductMaster
from .errors import InventoryApiError
from .models import (
    GuangdongMonitorAudit,
    GuangdongMonitorItem,
    GuangdongSupplierCycle,
    InventoryAgeLine,
    InventoryStockLine,
    ReplenishmentPlanItem,
)
from .query import _latest_batch, _sales_query, _sales_revision, _warehouse_key
from .revisions import revision_value, bump_revision
from .write_requests import lock_active_authority
from .replenishment_health import waiting_for_stock
from .guangdong_replenishment import add_remaining_quantities

MAX_ITEMS = 5000
RISK_LABELS = {"no_stock": "无库存可用", "urgent": "紧急补货", "warning": "补货预警", "stale": "低周转", "unknown": "积压风险", "healthy": "库存健康"}
RISK_PRIORITY = ("no_stock", "stale", "urgent", "warning")


def version():
    return revision_value() + "/" + _sales_revision()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _conflict():
    raise InventoryApiError("数据已变化，请刷新并重新预览后提交", status=409, code="version_conflict")


def _bounded_items():
    rows = list(GuangdongMonitorItem.objects.order_by("product_code")[:MAX_ITEMS + 1])
    if len(rows) > MAX_ITEMS:
        raise InventoryApiError("监控清单超过5000个型号，请联系管理员", status=503)
    return rows


def _identity(codes, stock):
    masters = {row.product_code: row for row in ErpProductMaster.objects.filter(product_code__in=codes)}
    result = {}
    for code in codes:
        master = masters.get(code)
        row = stock.get(code)
        def field(name):
            return str(getattr(row, name, "") or "").strip() or str(getattr(master, name, "") or "").strip()
        result[code] = {
            "productCode": code, "productName": field("product_name") or code,
            "specification": field("specification"), "brand": field("brand"),
            "category": field("category") or "未分类", "supplier": field("supplier") or "未映射供应商",
            "supplierSource": "库存快照" if row and row.supplier.strip() else "ERP档案" if master and master.supplier.strip() else "待补映射",
        }
    return result


def _stock(codes):
    latest = _latest_batch("stock")
    rows = {}
    if latest is not None:
        for row in InventoryStockLine.objects.filter(batch_id=latest.id, warehouse="广东仓", product_code__in=codes):
            if row.product_code in rows:
                raise InventoryApiError("广东仓快照存在重复型号记录，需先核验库存来源", status=503)
            rows[row.product_code] = row
    return latest, rows


def _ages(codes):
    latest = _latest_batch("age")
    rows = {}
    if latest is not None:
        for row in InventoryAgeLine.objects.filter(
            batch_id=latest.id,
            warehouse="广东仓",
            product_code__in=codes,
        ):
            if row.product_code in rows:
                raise InventoryApiError("广东仓库龄快照存在重复型号记录，需先核验吉客云库龄来源", status=503)
            rows[row.product_code] = row
    return latest, rows


def _latest_plans(codes):
    if not codes:
        return {}
    rows = (
        ReplenishmentPlanItem.objects.filter(product_code__in=codes, warehouse="广东仓")
        .exclude(status="cancelled")
        .annotate(
            latest_rank=Window(
                expression=RowNumber(),
                partition_by=[F("product_code"), F("warehouse")],
                order_by=[
                    F("order_date").desc(nulls_last=True),
                    F("updated_at").desc(),
                    F("id").desc(),
                ],
            )
        )
        .filter(latest_rank=1)
    )
    return {row.product_code: row for row in rows}


def list_items():
    watched = _bounded_items()
    codes = [row.product_code for row in watched]
    _, stock = _stock(codes)
    identities = _identity(codes, stock)
    return {"version": version(), "items": [{**identities[row.product_code], "active": row.active, "notes": row.notes} for row in watched]}


def search_products(query):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 100:
        raise InventoryApiError("请输入1–100字的编码或品名")
    q = query.strip()
    codes = list(ErpProductMaster.objects.filter(Q(product_code__icontains=q) | Q(product_name__icontains=q)).order_by("product_code").values_list("product_code", flat=True)[:21])
    return {"items": list(_identity(codes[:20], {}).values()), "truncated": len(codes) > 20}


def _normalize(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ITEMS:
        raise InventoryApiError("每次提交必须包含1–5000行")
    unique, errors = {}, []
    for index, row in enumerate(rows, 2):
        if not isinstance(row, dict) or set(row) - {"productCode", "active", "notes"}:
            errors.append({"row": index, "error": "包含非法字段"}); continue
        code, active, notes = row.get("productCode"), row.get("active", True), row.get("notes", "")
        if not isinstance(code, str) or not code.strip() or len(code.strip()) > 200 or re.search(r"[\x00-\x1f]", code):
            errors.append({"row": index, "error": "货品编码必须为1–200字文本"}); continue
        if not isinstance(active, bool) or not isinstance(notes, str) or len(notes) > 1000:
            errors.append({"row": index, "error": "监控状态或备注无效"}); continue
        code = code.strip()
        value = {"productCode": code, "active": active, "notes": notes.strip()}
        if code in unique and value != unique[code]:
            errors.append({"row": index, "error": "同一编码有冲突内容"})
        unique[code] = value
    codes = set(unique)
    known = set(ErpProductMaster.objects.filter(product_code__in=codes).values_list("product_code", flat=True))
    for code in sorted(codes - known):
        errors.append({"productCode": code, "error": "ERP档案中不存在该编码"})
    return [unique[code] for code in sorted(unique)], errors


def preview(rows):
    normalized, errors = _normalize(rows)
    existing = {row.product_code: row for row in _bounded_items()}
    counts = {"added": 0, "updated": 0, "unchanged": 0}
    items = []
    for row in normalized:
        old = existing.get(row["productCode"])
        action = "added" if old is None else "unchanged" if old.active == row["active"] and old.notes == row["notes"] else "updated"
        counts[action] += 1
        items.append({**row, "change": action, "before": {"active": old.active, "notes": old.notes} if old else None})
    if len(existing) + counts["added"] > MAX_ITEMS:
        errors.append({"error": "监控清单总数不能超过5000个型号"})
    return {"version": version(), "contentHash": _digest(normalized), "counts": counts, "errors": errors, "items": items, "valid": not errors}


def mutate(payload, actor):
    if not isinstance(payload, dict):
        raise InventoryApiError("请求必须为对象")
    action = payload.get("action")
    allowed = {"action", "rows", "version", "contentHash", "source", "rawHash", "supplier", "productCode", "leadDays", "bufferDays", "operatorName", "buyer", "risk", "riskReason", "error"}
    source = str(payload.get("source", "页面维护"))[:255]
    raw_hash = payload.get("rawHash", "")
    if not isinstance(raw_hash, str) or (raw_hash and not re.fullmatch("[0-9a-f]{64}", raw_hash)):
        raise InventoryApiError("原始内容摘要无效")
    content_hash = ""
    try:
        if set(payload) - allowed or action not in {"import", "supplier", "item", "reject"}:
            raise InventoryApiError("维护操作或字段无效")
        with transaction.atomic():
            lock_active_authority()
            revision_value(for_update=True)
            if action == "reject":
                raise InventoryApiError("文件预校验失败，请修正后重试")
            if action == "import":
                result = preview(payload.get("rows"))
                content_hash = result["contentHash"]
                if not result["valid"]:
                    raise InventoryApiError("存在错误行，请修正后重新预览")
                # A replay is harmless only when every requested business value still matches.
                unchanged = result["counts"]["added"] + result["counts"]["updated"] == 0
                if not unchanged and (payload.get("version") != result["version"] or payload.get("contentHash") != content_hash):
                    _conflict()
                for item in result["items"]:
                    if item["change"] != "unchanged":
                        GuangdongMonitorItem.objects.update_or_create(product_code=item["productCode"], defaults={"active": item["active"], "notes": item["notes"], "updated_by": actor})
                actual = {row.product_code: (row.active, row.notes) for row in GuangdongMonitorItem.objects.filter(product_code__in=[r["productCode"] for r in result["items"]])}
                if any(actual.get(row["productCode"]) != (row["active"], row["notes"]) for row in result["items"]):
                    raise InventoryApiError("清单写后回查不一致", status=503)
                summary = result["counts"]
                audit_details = {"counts": summary, "changes": [{"productCode": row["productCode"], "before": row["before"], "after": {"active": row["active"], "notes": row["notes"]}} for row in result["items"] if row["change"] != "unchanged"]}
            elif action == "supplier":
                supplier, lead, buffer = payload.get("supplier"), payload.get("leadDays"), payload.get("bufferDays", 7)
                if not isinstance(supplier, str) or not supplier.strip() or len(supplier) > 512 or supplier == "未映射供应商":
                    raise InventoryApiError("供应商无效")
                if isinstance(lead, bool) or not isinstance(lead, int) or not 1 <= lead <= 365 or isinstance(buffer, bool) or not isinstance(buffer, int) or not 0 <= buffer <= 365:
                    raise InventoryApiError("周期须为1–365天，缓冲须为0–365天整数")
                supplier = supplier.strip()
                if supplier not in {row["supplier"] for row in list_items()["items"]}:
                    raise InventoryApiError("供应商不属于当前广东监控清单")
                old = GuangdongSupplierCycle.objects.filter(supplier=supplier).first()
                unchanged = old is not None and (old.lead_days, old.buffer_days) == (lead, buffer)
                if not unchanged and payload.get("version") != version():
                    _conflict()
                if not unchanged:
                    GuangdongSupplierCycle.objects.update_or_create(supplier=supplier, defaults={"lead_days": lead, "buffer_days": buffer, "updated_by": actor})
                saved = GuangdongSupplierCycle.objects.get(supplier=supplier)
                if (saved.lead_days, saved.buffer_days) != (lead, buffer):
                    raise InventoryApiError("供应商设置回查失败", status=503)
                summary = {"supplier": supplier, "leadDays": lead, "bufferDays": buffer}
                audit_details = {"before": {"leadDays": old.lead_days, "bufferDays": old.buffer_days} if old else None, "after": summary}
                content_hash = _digest(summary)
            else:
                product_code = payload.get("productCode")
                lead, buffer = payload.get("leadDays"), payload.get("bufferDays")
                operator_name, buyer = payload.get("operatorName"), payload.get("buyer")
                risk, risk_reason = payload.get("risk", ""), payload.get("riskReason", "")
                if not isinstance(product_code, str) or not product_code.strip() or len(product_code.strip()) > 200 or re.search(r"[\x00-\x1f]", product_code):
                    raise InventoryApiError("货品编码必须为1–200字文本")
                if (lead is None) != (buffer is None):
                    raise InventoryApiError("生产周期和安全天数必须同时设置或同时留空")
                if lead is not None and (isinstance(lead, bool) or not isinstance(lead, int) or not 1 <= lead <= 365 or isinstance(buffer, bool) or not isinstance(buffer, int) or not 0 <= buffer <= 365):
                    raise InventoryApiError("周期须为1–365天，安全天数须为0–365天整数")
                if not isinstance(operator_name, str) or len(operator_name.strip()) > 200 or re.search(r"[\x00-\x1f]", operator_name) or not isinstance(buyer, str) or len(buyer.strip()) > 200 or re.search(r"[\x00-\x1f]", buyer):
                    raise InventoryApiError("负责人必须为不超过200字的文本")
                if not isinstance(risk, str) or not isinstance(risk_reason, str) or len(risk_reason.strip()) > 1000 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", risk_reason):
                    raise InventoryApiError("风险或原因说明无效")
                risk = risk.strip()
                risk_reason = risk_reason.strip()
                if risk and risk not in RISK_LABELS:
                    raise InventoryApiError("风险选项无效")
                if bool(risk) != bool(risk_reason):
                    raise InventoryApiError("手工风险和原因说明必须同时填写或同时留空")
                product_code = product_code.strip()
                operator_name = operator_name.strip() or None
                buyer = buyer.strip() or None
                risk = risk or None
                risk_reason = risk_reason or None
                old = GuangdongMonitorItem.objects.select_for_update().filter(product_code=product_code).first()
                if old is None:
                    raise InventoryApiError("型号不属于当前广东监控清单")
                before = {
                    "leadDays": old.lead_days_override,
                    "bufferDays": old.buffer_days_override,
                    "operatorName": old.operator_name_override,
                    "buyer": old.buyer_override,
                    "risk": old.risk_override,
                    "riskReason": old.risk_reason_override,
                }
                after = {"leadDays": lead, "bufferDays": buffer, "operatorName": operator_name, "buyer": buyer, "risk": risk, "riskReason": risk_reason}
                unchanged = before == after
                if not unchanged and payload.get("version") != version():
                    _conflict()
                if not unchanged:
                    old.lead_days_override = lead
                    old.buffer_days_override = buffer
                    old.operator_name_override = operator_name
                    old.buyer_override = buyer
                    old.risk_override = risk
                    old.risk_reason_override = risk_reason
                    old.updated_by = actor
                    old.save(update_fields=["lead_days_override", "buffer_days_override", "operator_name_override", "buyer_override", "risk_override", "risk_reason_override", "updated_by", "updated_at"])
                saved = GuangdongMonitorItem.objects.get(product_code=product_code)
                if (saved.lead_days_override, saved.buffer_days_override, saved.operator_name_override, saved.buyer_override, saved.risk_override, saved.risk_reason_override) != (lead, buffer, operator_name, buyer, risk, risk_reason):
                    raise InventoryApiError("型号设置回查失败", status=503)
                summary = {"productCode": product_code, **after}
                audit_details = {"before": before, "after": after}
                content_hash = _digest(summary)
            if not unchanged:
                bump_revision({"kind": "guangdong_" + action, "contentHash": content_hash})
            result = {"status": "unchanged" if unchanged else "saved", "version": version(), "summary": summary}
            GuangdongMonitorAudit.objects.create(action=action, source=source, raw_hash=raw_hash, content_hash=content_hash, actor=actor, status=result["status"], result=audit_details)
            return result
    except InventoryApiError as error:
        with transaction.atomic():
            lock_active_authority()
            GuangdongMonitorAudit.objects.create(action=str(action)[:32], source=source, raw_hash=raw_hash, actor=actor, status="rejected", result={"code": error.code})
        raise


def risk_fields(*, available, sales30, lead, buffer, snapshot):
    turnover = max(0, available) / (sales30 / 30) if available is not None and sales30 is not None and sales30 > 0 else None
    reasons, risks = [], []
    if available is None:
        reasons.append("最新快照缺少该型号广东仓记录")
    elif available <= 0:
        risks.append("no_stock"); reasons.append("广东仓可用库存小于等于0")
    if turnover is not None and turnover > 180:
        risks.append("stale"); reasons.append("库存周转大于180天")
    if turnover is not None and lead is not None:
        if turnover <= lead:
            risks.append("urgent"); reasons.append("销售周转不超过生产周期")
        elif turnover < lead + buffer:
            risks.append("warning"); reasons.append("销售周转低于生产周期及安全天数之和")
    if lead is None: reasons.append("供应商生产周期待设置")
    if sales30 is None: reasons.append("近30日销量未匹配或覆盖不足")
    elif sales30 == 0: reasons.append("近30日无正向出库，销售周转待观察")
    pending = available is None or lead is None or turnover is None
    risk = next((key for key in RISK_PRIORITY if key in risks), "unknown" if pending else "healthy")
    order_date = None
    if snapshot and turnover is not None and lead is not None:
        days = math.floor(turnover - lead - buffer)
        if abs(days) <= 36500:
            calculated = snapshot + timedelta(days=days)
            order_date = max(calculated, timezone.localdate()).isoformat()
    return {"risk": risk, "riskLabel": RISK_LABELS[risk], "riskReasons": reasons, "turnoverDays": turnover, "latestOrderDate": order_date}


def _project_items(principal, watched):
    codes = [row.product_code for row in watched]
    latest, stock = _stock(codes)
    age_latest, ages = _ages(codes)
    plans = _latest_plans(codes)
    identities = _identity(codes, stock)
    sales = {"rows": [], "asOfDate": None, "dataStartDate": None, "truncated": False}
    if codes:
        sales = _sales_query(principal, {"operation": "inventory_inbound_windows", "productCodes": codes, "asOfDate": None, "limit": 10000})
    if sales.get("truncated") is not False:
        raise InventoryApiError("销售查询超过安全范围，未返回截断结果", status=503)
    demand = {}
    seen = set()
    for row in sales.get("rows", []):
        code, warehouse = row.get("productCode"), row.get("warehouseKey")
        if code not in identities or not isinstance(warehouse, str) or (code, warehouse) in seen:
            raise InventoryApiError("销售响应范围或业务键无效", status=503)
        seen.add((code, warehouse))
        if warehouse == _warehouse_key("广东仓"): demand[code] = row
    cutoff = date.fromisoformat(sales["asOfDate"]) if sales.get("asOfDate") else None
    start = date.fromisoformat(sales["dataStartDate"]) if sales.get("dataStartDate") else None
    cycles = {row.supplier: row for row in GuangdongSupplierCycle.objects.filter(supplier__in={r["supplier"] for r in identities.values()})}
    stale = latest is None or (timezone.localdate() - latest.snapshot_date).days > 3
    items = []
    for watched_row in watched:
        code = watched_row.product_code
        row, age_row, sale = stock.get(code), ages.get(code), demand.get(code)
        item = dict(identities[code])
        cycle = cycles.get(item["supplier"])
        available = int(row.available_quantity) if row else None
        def quantity(days):
            return int(sale[f"sales{days}dQuantity"]) if sale and cutoff and start and start <= cutoff - timedelta(days=days - 1) else None
        s7, s15, s30 = quantity(7), quantity(15), quantity(30)
        cost = int(row.unit_cost_cents) if row else None
        plan = plans.get(code)
        has_cycle_override = watched_row.lead_days_override is not None and watched_row.buffer_days_override is not None
        supplier_lead_days = cycle.lead_days if cycle else None
        supplier_buffer_days = cycle.buffer_days if cycle else 7
        lead_days = watched_row.lead_days_override if has_cycle_override else supplier_lead_days
        buffer_days = watched_row.buffer_days_override if has_cycle_override else supplier_buffer_days
        plan_operator = plan.operator_name.strip() if plan else ""
        plan_buyer = plan.buyer.strip() if plan else ""
        operator_override = (watched_row.operator_name_override or "").strip() or None
        buyer_override = (watched_row.buyer_override or "").strip() or None
        operator_name = operator_override if operator_override is not None else plan_operator
        buyer = buyer_override if buyer_override is not None else plan_buyer
        item.update({"warehouse": "广东仓", "notes": watched_row.notes, "availableQuantity": available,
            "inTransitQuantity": int(row.in_transit_quantity) if row else None,
            "inventoryAgeDays": age_row.inventory_age_days if age_row else None, "unitCostCents": cost,
            "knownStockValueCents": max(0, available or 0) * (cost or 0), "costMissing": row is not None and available > 0 and cost is None,
            "outbound7dQuantity": s7, "outbound15dQuantity": s15, "outbound30dQuantity": s30,
            "leadDays": lead_days, "bufferDays": buffer_days,
            "supplierLeadDays": supplier_lead_days, "supplierBufferDays": supplier_buffer_days,
            "leadDaysOverride": watched_row.lead_days_override, "bufferDaysOverride": watched_row.buffer_days_override,
            "cycleSource": "型号设置" if has_cycle_override else "供应商设置" if cycle else "待设置",
            "replenishmentQuantity": int(plan.planned_quantity) if plan else None,
            "latestReplenishmentOrderDate": plan.order_date.isoformat() if plan and plan.order_date else None,
            "operatorName": operator_name, "operatorNameOverride": operator_override,
            "planOperatorName": plan_operator,
            "operatorNameSource": "型号设置" if operator_override is not None else "最新备货计划" if plan_operator else "待设置",
            "buyer": buyer, "buyerOverride": buyer_override,
            "planBuyer": plan_buyer,
            "buyerSource": "型号设置" if buyer_override is not None else "最新备货计划" if plan_buyer else "待设置",
            "inventoryStale": stale,
        })
        item.update(risk_fields(available=available, sales30=s30, lead=item["leadDays"], buffer=item["bufferDays"], snapshot=latest.snapshot_date if latest else None))
        if stale:
            item["riskReasons"].append("库存快照待更新，估算仅供参考")
            if item["risk"] == "healthy": item.update(risk="unknown", riskLabel=RISK_LABELS["unknown"])
        item.update({
            "autoRisk": item["risk"], "autoRiskLabel": item["riskLabel"], "autoRiskReasons": list(item["riskReasons"]),
            "riskOverride": watched_row.risk_override, "riskReasonOverride": watched_row.risk_reason_override,
            "riskSource": "型号设置" if watched_row.risk_override else "系统判定",
        })
        if watched_row.risk_override:
            auto_reason = "；".join(item["autoRiskReasons"]) or "可售天数充足"
            item.update(
                risk=watched_row.risk_override,
                riskLabel=RISK_LABELS[watched_row.risk_override],
                riskReasons=[f"人工设置：{watched_row.risk_reason_override}", f"系统原判：{item['autoRiskLabel']}（{auto_reason}）"],
            )
        if waiting_for_stock(plan):
            item.update(
                risk="healthy", riskLabel=RISK_LABELS["healthy"], riskSource="备货跟进",
                riskReasons=["已增加备货数量，已关注并下单备货；等待广东仓实物库存首次增加后重新检测库存健康状态"],
            )
        item["riskReason"] = "；".join(item["riskReasons"])
        items.append(item)
    return items, latest, age_latest, sales, stale


def overview_risks(principal, codes):
    """Apply identical Guangdong rules to every exact-warehouse overview SKU."""
    if not codes:
        return {}
    configured = {row.product_code: row for row in _bounded_items() if row.active}
    result = {}
    for offset in range(0, len(codes), MAX_ITEMS):
        watched = [configured.get(code) or GuangdongMonitorItem(product_code=code)
                   for code in codes[offset:offset + MAX_ITEMS]]
        items, *_ = _project_items(principal, watched)
        result.update({item["productCode"]: item for item in items})
    return result


def monitor(principal, options, *, export=False):
    before = version()
    watched = [row for row in _bounded_items() if row.active]
    items, latest, age_latest, sales, stale = _project_items(principal, watched)
    facets = {key: sorted({row[field] for row in items if row[field]}) for key, field in (("brands", "brand"), ("categories", "category"), ("suppliers", "supplier"))}
    keywords = list(dict.fromkeys(re.split(r"[\s,，;；]+", str(options.get("query") or "").strip().lower())))[:8]
    filtered = [row for row in items if (not any(keywords) or any(keyword in str(row[key]).lower() for keyword in keywords if keyword for key in ("productCode", "productName", "specification", "supplier", "brand", "category"))) and all(not options.get(key) or row[field] in options[key] for key, field in (("brands", "brand"), ("categories", "category"), ("suppliers", "supplier")))]
    total_quantity = sum(max(0, row["availableQuantity"] or 0) for row in filtered)
    total_value = sum(row["knownStockValueCents"] for row in filtered)
    distribution = []
    for risk, label in RISK_LABELS.items():
        group = [row for row in filtered if row["risk"] == risk]
        quantity_total = sum(max(0, row["availableQuantity"] or 0) for row in group)
        value = sum(row["knownStockValueCents"] for row in group)
        distribution.append({"risk": risk, "label": label, "itemCount": len(group), "quantity": quantity_total, "knownStockValueCents": value, "itemRate": len(group) / len(filtered) if filtered else 0, "quantityRate": quantity_total / total_quantity if total_quantity else 0, "valueRate": value / total_value if total_value else 0})
    if options.get("risk"): filtered = [row for row in filtered if row["risk"] == options["risk"]]
    filtered.sort(key=lambda row: (list(RISK_LABELS).index(row["risk"]), -row["knownStockValueCents"], row["productCode"]))
    page, size = options.get("page", 1), options.get("pageSize", 50)
    displayed = filtered if export else filtered[(page - 1) * size:page * size]
    add_remaining_quantities(displayed, latest)
    result = {"version": before, "hasInventory": latest is not None,
        "sync": {"inventoryAsOf": latest.snapshot_date.isoformat() if latest else None, "inventoryAgeAsOf": age_latest.snapshot_date.isoformat() if age_latest else None, "salesThrough": sales.get("asOfDate"), "latestInventoryBatchId": latest.id if latest else None, "inventoryStale": stale},
        "filters": facets, "distribution": distribution, "watchCount": len(watched),
        "metrics": {"itemCount": len(filtered), "availableQuantity": sum(row["availableQuantity"] or 0 for row in filtered), "inTransitQuantity": sum(row["inTransitQuantity"] or 0 for row in filtered), "knownStockValueCents": sum(row["knownStockValueCents"] for row in filtered), "missingCostCount": sum(row["costMissing"] for row in filtered), "missingStockCount": sum(row["availableQuantity"] is None for row in filtered)},
        "pagination": {"page": page, "pageSize": size, "total": len(filtered), "totalPages": math.ceil(len(filtered) / size)},
        "items": displayed,
        "disclosures": ["仅人工清单内启用型号，仓库精确限定广东仓。", "销售周转=当前可用库存÷广东仓近30日平均正向出库；在途不抵减预警。", "库龄只取最新吉客云库龄表格中仓名精确为广东仓的记录。", "备货数量与最新下单日期取备货计划中同货品、同广东仓的最新非取消计划。", "下单剩余库存=备货数量−下单日期之后广东仓实物库存逐日正向增量之和；下降不抵扣，结果可为负。下单当日为基线，缺失每日快照时待核算；快照增量不等于入库流水。", "新增或增加正数备货量后按健康跟进，广东仓实物库存首次增加后恢复风险检测；库存下降不会重新开启旧备货周期。", "金额单位为人民币分，仅汇总已覆盖固定成本。", "健康分布按搜索、品牌、品类及供应商范围统计，风险点击筛选明细。"],
    }
    if version() != before: _conflict()
    if export and options.get("version") != before: _conflict()
    return result
