from __future__ import annotations

import copy
import hashlib
import json


def finance_line(
    month: str,
    metric_key: str,
    amount_cents: int | None,
    *,
    rate_bps: int | None = None,
    section: str = "summary",
    subject_name: str | None = None,
    scope_type: str = "business",
    scope_name: str = "志高事业部",
    group_name: str = "",
    scope_key: str = "business",
    sort_order: int = 1,
    is_total: bool = False,
) -> dict[str, object]:
    return {
        "month": month,
        "section": section,
        "metricKey": metric_key,
        "subjectName": subject_name or metric_key,
        "scopeKey": scope_key,
        "scopeType": scope_type,
        "scopeName": scope_name,
        "groupName": group_name,
        "valueType": "rate" if rate_bps is not None and amount_cents is None else "amount",
        "amountCents": amount_cents,
        "rateBps": rate_bps,
        "rawValue": str(amount_cents if amount_cents is not None else rate_bps or 0),
        "sourceRowCount": 1,
        "sortOrder": sort_order,
        "isTotal": is_total,
    }


def finance_month(month: str, multiplier: int = 1) -> dict[str, object]:
    business = [
        finance_line(month, "gross_sales", 120_000 * multiplier, sort_order=1),
        finance_line(month, "return_amount", -20_000 * multiplier, sort_order=2),
        finance_line(month, "net_sales", 100_000 * multiplier, sort_order=3),
        finance_line(month, "net_cost", 60_000 * multiplier, sort_order=4),
        finance_line(month, "gross_profit", 40_000 * multiplier, sort_order=5),
        finance_line(month, "selling_expense_total", 10_000 * multiplier, sort_order=6),
        finance_line(month, "small_profit", 30_000 * multiplier, sort_order=7),
        finance_line(month, "other_expense_total", 5_000 * multiplier, sort_order=8),
        finance_line(month, "profit", 25_000 * multiplier, sort_order=9),
        finance_line(
            month,
            "selling_expense_total",
            10_000 * multiplier,
            section="kingdee",
            subject_name="销售费用",
            sort_order=20,
            is_total=True,
        ),
        finance_line(
            month,
            "",
            6_000 * multiplier,
            section="kingdee",
            subject_name="销售费用_推广费用_京东",
            sort_order=21,
        ),
        finance_line(
            month,
            "",
            4_000 * multiplier,
            section="kingdee",
            subject_name="管理费用",
            sort_order=22,
        ),
    ]
    shops: list[dict[str, object]] = []
    for index, (platform, sales) in enumerate((("京东", 60_000), ("天猫", 40_000)), start=1):
        scope_key = f"shop:{platform}:同名店"
        for order, (metric, amount) in enumerate((
            ("gross_sales", sales),
            ("return_amount", 0),
            ("net_sales", sales),
            ("net_cost", sales * 6 // 10),
            ("gross_profit", sales * 4 // 10),
            ("selling_expense_total", sales // 10),
            ("small_profit", sales * 3 // 10),
            ("other_expense_total", sales // 20),
            ("profit", sales // 4),
        ), start=1):
            shops.append(finance_line(
                month,
                metric,
                amount * multiplier,
                scope_type="shop",
                scope_name="同名店",
                group_name=platform,
                scope_key=scope_key,
                sort_order=100 * index + order,
            ))
        shops.extend([
            finance_line(
                month,
                "selling_expense_total",
                sales // 10 * multiplier,
                section="kingdee",
                subject_name="销售费用",
                scope_type="shop",
                scope_name="同名店",
                group_name=platform,
                scope_key=scope_key,
                sort_order=200 * index,
                is_total=True,
            ),
            finance_line(
                month,
                "",
                sales // 20 * multiplier,
                section="kingdee",
                subject_name=f"销售费用_推广费用_{platform}",
                scope_type="shop",
                scope_name="同名店",
                group_name=platform,
                scope_key=scope_key,
                sort_order=200 * index + 1,
            ),
        ])
    lines = business + shops
    return {
        "month": month,
        "sheetName": month,
        "businessName": "志高事业部",
        "shopCount": 2,
        "subjectCount": 4,
        "lines": lines,
    }


def prepared_payload(*months: str) -> dict[str, object]:
    payload = {
        "schemaVersion": "finance-normalized-v1",
        "disposition": "prepared",
        "fileName": "月度财报.xlsx",
        "fileSizeBytes": 2048,
        "rawFileHash": hashlib.sha256("first-file".encode()).hexdigest(),
        "sourceSheetCount": len(months),
        "months": [finance_month(month, index + 1) for index, month in enumerate(months)],
        "warnings": [],
    }
    return payload


def body_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def changed_raw_file(payload: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(payload)
    result["rawFileHash"] = hashlib.sha256("same-business-content-new-file".encode()).hexdigest()
    result["fileName"] = "重存后的月度财报.xlsx"
    for month in result["months"]:  # type: ignore[index]
        month["lines"].reverse()
        for line in month["lines"]:
            line["rawValue"] = f" {line['rawValue']} "
            line["sourceRowCount"] = 2
            line["sortOrder"] += 10_000
    return result
