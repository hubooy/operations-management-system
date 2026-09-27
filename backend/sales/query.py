from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Iterable, Sequence

from django.db.models import (
    BigIntegerField,
    Case,
    Count,
    F,
    Max,
    Q,
    QuerySet,
    Sum,
    TextField,
    Value,
    When,
)
from django.db import connection
from django.db.models.functions import Coalesce, Collate, NullIf

from .auth import Principal
from .models import SalesDataRevision, SalesImportBatch, SalesOrderLine


FILTER_SPLIT_RE = re.compile(r"[，,;；]+")
PRODUCT_SPLIT_RE = re.compile(r"[\r\n,，;；]+")
ASCII_SPACE_RE = re.compile(r"\s+")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OUTLET_SEPARATOR = "\x1f"


class SalesRequestError(Exception):
    pass


class SalesAccessError(Exception):
    pass


def binary_collation() -> str:
    """Use the same UTF-8 byte ordering as the authoritative SQLite/D1 API."""
    if connection.vendor == "postgresql":
        return "C"
    if connection.vendor == "sqlite":
        return "BINARY"
    raise RuntimeError(f"Unsupported database vendor for sales binary ordering: {connection.vendor}")


def binary_order(field: str):
    return Collate(F(field), binary_collation())


def binary_text_key(value: object) -> bytes:
    return str(value).encode("utf-8")


def parse_iso_date(value: str, message: str) -> date:
    if not ISO_DATE_RE.fullmatch(value):
        raise SalesRequestError(message)
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise SalesRequestError(message) from error


def add_days(value: str, days: int) -> str:
    return (date.fromisoformat(value) + timedelta(days=days)).isoformat()


def add_years(value: str, years: int) -> str:
    parsed = date.fromisoformat(value)
    try:
        return parsed.replace(year=parsed.year + years).isoformat()
    except ValueError:
        return parsed.replace(year=parsed.year + years, day=28).isoformat()


def day_count(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


def selected_values(query, *keys: str, maximum: int = 50, label: str | None = None) -> list[str]:
    values: list[str] = []
    for key in keys:
        for raw in query.getlist(key):
            values.extend(item.strip() for item in FILTER_SPLIT_RE.split(raw) if item.strip())
    values = list(dict.fromkeys(values))
    if len(values) > maximum or any(len(value) > 100 for value in values):
        raise SalesRequestError(f"{label or keys[0]} 筛选最多 {maximum} 项，且每项不能超过 100 字。")
    return values


def parse_product_queries(values: Iterable[str], *, strict: bool = True) -> list[str]:
    values = list(values)
    parsed: list[str] = []
    for value in values:
        for candidate in PRODUCT_SPLIT_RE.split(value):
            candidate = candidate.strip()
            if not candidate:
                continue
            chunks = [candidate] if re.search(r"[\u3400-\u9fff]", candidate) else ASCII_SPACE_RE.split(candidate)
            parsed.extend(chunk.strip() for chunk in chunks if chunk.strip())
    parsed = list(dict.fromkeys(parsed))
    if strict and (len(parsed) > 100 or any(len(item) > 200 for item in parsed)):
        raise SalesRequestError("商品筛选最多 100 项，且每项不能超过 200 字。")
    if strict and len("\n".join(values).strip()) > 1000:
        raise SalesRequestError("货品筛选不能超过 1000 个字符。")
    return parsed[:100]


def _apply_principal_scope(
    queryset: QuerySet[SalesOrderLine], principal: Principal | None
) -> tuple[QuerySet[SalesOrderLine], str]:
    if principal is None or principal.scope is None:
        return queryset, "unrestricted"
    scope = principal.scope
    warehouses = list(dict.fromkeys(scope["warehouses"]))
    scoped_channels = list(dict.fromkeys(scope["channels"]))
    scoped_platforms = list(dict.fromkeys(scope["platforms"]))
    if not warehouses and not scoped_channels and not scoped_platforms:
        raise SalesAccessError("当前账号没有可读取的销售数据范围")
    if warehouses:
        queryset = queryset.filter(warehouse__in=warehouses)
    outlet_scope = Q()
    if scoped_channels:
        outlet_scope |= Q(channel__in=scoped_channels, channel_key__in=scoped_channels)
    if scoped_platforms:
        outlet_scope |= Q(platform__in=scoped_platforms, platform_key__in=scoped_platforms)
    if scoped_channels or scoped_platforms:
        queryset = queryset.filter(outlet_scope)
    return queryset, "restricted"


def resolve_product_codes(queries: Sequence[str], principal: Principal | None = None) -> list[str]:
    if not queries:
        return []
    lookup, _ = _apply_principal_scope(
        SalesOrderLine.objects.filter(is_business_row=True),
        principal,
    )
    rows = (
        lookup.filter(product_name__in=queries)
        .exclude(product_code="")
        .values_list("product_name", "product_code")
        .distinct()
        .order_by(binary_order("product_name"), binary_order("product_code"))[:100]
    )
    codes_by_name: dict[str, list[str]] = {}
    for name, code in rows:
        codes_by_name.setdefault(name, []).append(code)
    resolved: list[str] = []
    for query in queries:
        resolved.extend(codes_by_name.get(query, [query]))
    return list(dict.fromkeys(resolved))[:100]


def parse_outlets(values: Sequence[str]) -> list[dict[str, str]]:
    outlets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        parts = value.split(OUTLET_SEPARATOR)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise SalesRequestError("outlet 必须使用有效的平台与店铺复合键。")
        identity = (parts[0].strip(), parts[1].strip())
        if identity not in seen:
            seen.add(identity)
            outlets.append({"platform": identity[0], "shop": identity[1]})
    if len(outlets) > 50:
        raise SalesRequestError("outlet 筛选最多 50 项。")
    return outlets


def sales_queryset(
    *,
    start_date: str,
    end_exclusive: str,
    product_codes: Sequence[str] = (),
    categories: Sequence[str] = (),
    channels: Sequence[str] = (),
    platforms: Sequence[str] = (),
    outlets: Sequence[dict[str, str]] = (),
    principal: Principal | None = None,
    category_contract: bool = False,
) -> tuple[QuerySet[SalesOrderLine], str]:
    queryset = SalesOrderLine.objects.filter(
        business_date__gte=start_date,
        business_date__lt=end_exclusive,
        is_business_row=True,
    )
    if not category_contract:
        # Summary intentionally preserves the legacy raw-whitespace outlet
        # identity; category analysis uses the normalized stored keys.
        queryset = queryset.annotate(
            report_shop_key=Coalesce(
                NullIf(F("shop_name"), Value("")),
                NullIf(F("channel"), Value("")),
                NullIf(F("platform"), Value("")),
                Value("未分类"),
                output_field=TextField(),
            ),
            report_platform_key=Coalesce(
                NullIf(F("platform"), Value("")),
                Value("未分类"),
                output_field=TextField(),
            ),
        )
    queryset, scope_mode = _apply_principal_scope(queryset, principal)
    if product_codes:
        queryset = queryset.filter(product_code__in=product_codes)
    if categories:
        queryset = queryset.filter(resolved_category__in=categories)
    if channels:
        queryset = queryset.filter(channel__in=channels, channel_key__in=channels)
    if platforms:
        if category_contract:
            queryset = queryset.filter(platform__in=platforms, platform_key__in=platforms)
        else:
            queryset = queryset.filter(report_platform_key__in=platforms, platform_key__in=platforms)
    if outlets:
        outlet_query = Q()
        for outlet in outlets:
            if category_contract:
                outlet_query |= Q(
                    platform=outlet["platform"],
                    platform_key=outlet["platform"],
                    shop_key=outlet["shop"],
                )
            else:
                outlet_query |= Q(
                    report_platform_key=outlet["platform"],
                    report_shop_key=outlet["shop"],
                    platform_key=outlet["platform"],
                    shop_key=outlet["shop"],
                )
        queryset = queryset.filter(outlet_query)
    return queryset, scope_mode


def metric_aggregates(prefix: str = "", filter_q: Q | None = None) -> dict[str, object]:
    amount = f"{prefix}allocated_amount_cents"
    cost = f"{prefix}cost_amount_cents"
    profit = f"{prefix}gross_profit_cents"
    quantity = f"{prefix}quantity"
    net_sales_row = f"{prefix}is_net_sales_row"
    net_quantity_row = f"{prefix}is_net_quantity_row"
    order_identity = f"{prefix}order_identity"
    line_key = f"{prefix}source_line_key"
    row_filter = filter_q if filter_q is not None else Q()
    return {
        "gross_sales_cents": Coalesce(Sum(Case(When(row_filter & Q(**{f"{amount}__gt": 0}), then=F(amount)), default=Value(0), output_field=BigIntegerField())), 0),
        "refund_amount_cents": Coalesce(Sum(Case(When(row_filter & Q(**{f"{amount}__lt": 0}), then=-F(amount)), default=Value(0), output_field=BigIntegerField())), 0),
        "net_sales_excluding_accessories_cents": Coalesce(Sum(Case(When(row_filter & Q(**{net_sales_row: True}), then=F(amount)), default=Value(0), output_field=BigIntegerField())), 0),
        "cost_amount_cents": Coalesce(Sum(cost, filter=filter_q), 0),
        "gross_profit_cents": Coalesce(Sum(profit, filter=filter_q), 0),
        "net_quantity": Coalesce(Sum(Case(When(row_filter & Q(**{net_quantity_row: True}), then=F(quantity)), default=Value(0), output_field=BigIntegerField())), 0),
        "order_count": Count(order_identity, distinct=True, filter=filter_q),
        "line_count": Count(line_key, filter=filter_q),
    }


def category_aggregates(filter_q: Q | None = None) -> dict[str, object]:
    row_filter = filter_q if filter_q is not None else Q()
    valid_product = ~Q(product_code="ERP_PRICE_ADJUSTMENT")
    return {
        "gross_sales_cents": Coalesce(Sum(Case(When(row_filter & Q(allocated_amount_cents__gt=0), then=F("allocated_amount_cents")), default=Value(0), output_field=BigIntegerField())), 0),
        "refund_amount_cents": Coalesce(Sum(Case(When(row_filter & Q(allocated_amount_cents__lt=0), then=-F("allocated_amount_cents")), default=Value(0), output_field=BigIntegerField())), 0),
        "net_sales_cents": Coalesce(Sum("allocated_amount_cents", filter=filter_q), 0),
        "cost_amount_cents": Coalesce(Sum("cost_amount_cents", filter=filter_q), 0),
        "gross_profit_cents": Coalesce(Sum("gross_profit_cents", filter=filter_q), 0),
        "positive_quantity": Coalesce(Sum(Case(When(row_filter & Q(quantity__gt=0) & valid_product, then=F("quantity")), default=Value(0), output_field=BigIntegerField())), 0),
        "return_quantity": Coalesce(Sum(Case(When(row_filter & Q(quantity__lt=0) & valid_product, then=-F("quantity")), default=Value(0), output_field=BigIntegerField())), 0),
        "net_quantity": Coalesce(Sum(Case(When(row_filter & Q(is_net_quantity_row=True), then=F("quantity")), default=Value(0), output_field=BigIntegerField())), 0),
        "product_count": Count("product_code", distinct=True, filter=row_filter & ~Q(product_code="")),
        "line_count": Count("source_line_key", filter=filter_q),
        "latest_business_date": Max("business_date", filter=filter_q),
    }


def serialize_metric(row: dict[str, object] | None) -> dict[str, int | float]:
    row = row or {}
    gross = int(row.get("gross_sales_cents") or 0)
    refund = int(row.get("refund_amount_cents") or 0)
    net = gross - refund
    net_excluding_accessories = int(row.get("net_sales_excluding_accessories_cents") or 0)
    cost = int(row.get("cost_amount_cents") or 0)
    net_quantity = int(row.get("net_quantity") or 0)
    return {
        "grossSalesCents": gross,
        "netSalesCents": net,
        "netSalesExcludingAccessoriesCents": net_excluding_accessories,
        "costAmountCents": cost,
        "grossProfitCents": int(row.get("gross_profit_cents") or 0),
        "refundAmountCents": refund,
        "orderCount": int(row.get("order_count") or 0),
        "lineCount": int(row.get("line_count") or 0),
        "netQuantity": net_quantity,
        "averageOrderValueCents": 0 if net_quantity == 0 else net_excluding_accessories / net_quantity,
        "grossMarginRate": 0 if net == 0 else (net - cost) / net,
        "refundRate": 0 if gross == 0 else refund / gross,
    }


def latest_batch_payload() -> dict[str, object] | None:
    row = SalesImportBatch.objects.filter(status="completed").order_by(
        "-completed_at", "-created_at", binary_order("id").desc()
    ).first()
    if row is None:
        return None
    try:
        warnings = json.loads(row.warnings_json)
    except json.JSONDecodeError:
        warnings = []
    try:
        totals = json.loads(row.totals_json)
    except json.JSONDecodeError:
        totals = {}
    return {
        "id": row.id,
        "source": row.source,
        "fileName": row.file_name,
        "fileSizeBytes": row.file_size_bytes,
        "fileHash": row.file_hash,
        "sheetName": row.sheet_name,
        "status": row.status,
        "rowCount": row.row_count,
        "insertedCount": row.inserted_count,
        "duplicateCount": row.duplicate_count,
        "warningCount": row.warning_count,
        "warnings": warnings,
        "totals": totals,
        "createdAt": row.created_at,
        "completedAt": row.completed_at,
    }


def revision_token() -> str:
    revisions = dict(SalesDataRevision.objects.filter(domain__in=["sales", "erp"]).values_list("domain", "revision"))
    return f"{int(revisions.get('sales', 0))}:{int(revisions.get('erp', 0))}"
