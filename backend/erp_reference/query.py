from __future__ import annotations

from django.db.models import Q

from sales.auth import Principal

from .errors import ErpReferenceApiError
from .models import ErpComboItem, ErpProductMaster, ErpReferenceImportBatch


MAX_LIMIT = 100
MAX_OFFSET = 100_000


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ErpReferenceApiError(f"{label} 无效")
    return value.strip()


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ErpReferenceApiError(f"{label} 无效")
    return value


def validate_consumer_request(payload: dict[str, object]) -> dict[str, object]:
    if set(payload) != {"operation", "params"} or not isinstance(payload.get("params"), dict):
        raise ErpReferenceApiError("ERP consumer 请求字段集合无效")
    operation = payload.get("operation")
    if operation not in {"product_search", "combo_search", "product_codes", "import_batch_search"}:
        raise ErpReferenceApiError("ERP consumer operation 不在允许清单中")
    params = dict(payload["params"])
    if operation in {"product_search", "combo_search", "import_batch_search"}:
        if set(params) != {"query", "limit", "offset"}:
            raise ErpReferenceApiError("ERP 搜索参数字段集合无效")
        return {
            "operation": operation,
            "params": {
                "query": _text(params.get("query"), "query", 80),
                "limit": _integer(params.get("limit"), "limit", 1, MAX_LIMIT),
                "offset": _integer(params.get("offset"), "offset", 0, MAX_OFFSET),
            },
        }
    if set(params) != {"codes"} or not isinstance(params.get("codes"), list):
        raise ErpReferenceApiError("ERP 货品编码参数字段集合无效")
    codes = [_text(value, "code", 512) for value in params["codes"]]
    if not codes or len(codes) > 500 or len(codes) != len(set(codes)):
        raise ErpReferenceApiError("ERP 货品编码数量或唯一性无效")
    return {"operation": operation, "params": {"codes": codes}}


def execute_consumer_query(
    principal: Principal, request: dict[str, object]
) -> dict[str, object]:
    del principal
    operation = str(request["operation"])
    params = request["params"]
    assert isinstance(params, dict)
    if operation == "product_codes":
        codes = params["codes"]
        assert isinstance(codes, list)
        rows = ErpProductMaster.objects.filter(product_code__in=codes).order_by("product_code")
        return {
            "items": [
                {
                    "productCode": row.product_code,
                    "productName": row.product_name,
                    "brand": row.brand,
                    "specification": row.specification,
                    "barcode": row.barcode,
                    "category": row.category,
                    "supplier": row.supplier,
                    "productStatus": row.product_status,
                    "updatedAt": row.updated_at,
                }
                for row in rows
            ]
        }
    query = str(params["query"])
    limit = int(params["limit"])
    offset = int(params["offset"])
    if operation == "import_batch_search":
        filters = (
            Q(id__icontains=query) | Q(file_name__icontains=query)
            | Q(source_key__icontains=query) | Q(source_label__icontains=query)
            | Q(status__icontains=query)
        )
        matched = ErpReferenceImportBatch.objects.filter(filters)
        total = matched.count()
        rows = matched.order_by("-created_at", "-id")[offset : offset + limit]
        return {
            "total": total,
            "items": [
                {
                    "id": row.id, "source": row.source_label,
                    "fileName": row.file_name, "status": row.status,
                    "rowCount": int(row.row_count), "createdAt": row.created_at.isoformat(),
                    "completedAt": row.completed_at.isoformat() if row.completed_at else None,
                }
                for row in rows
            ],
            "truncated": offset + min(limit, max(0, total - offset)) < total,
        }
    if operation == "product_search":
        filters = (
            Q(product_code__icontains=query) | Q(product_name__icontains=query)
            | Q(specification__icontains=query) | Q(barcode__icontains=query)
            | Q(brand__icontains=query) | Q(category__icontains=query)
            | Q(supplier__icontains=query)
        )
        matched = ErpProductMaster.objects.filter(filters)
        total = matched.count()
        rows = matched.order_by("-updated_at", "product_code")[offset : offset + limit]
        return {
            "total": total,
            "items": [
                {
                    "resultId": row.product_code,
                    "title": row.product_name,
                    "subtitle": " · ".join(
                        value for value in [row.specification, row.brand, row.supplier] if value
                    ),
                    "detail": " · ".join(value for value in [row.category, row.product_status] if value),
                    "updatedAt": row.updated_at,
                    "amountCents": None,
                }
                for row in rows
            ],
            "truncated": offset + min(limit, max(0, total - offset)) < total,
        }
    filters = (
        Q(parent_code__icontains=query) | Q(parent_name__icontains=query)
        | Q(child_code__icontains=query) | Q(child_name__icontains=query)
    )
    matched = ErpComboItem.objects.filter(filters)
    total = matched.count()
    rows = matched.order_by("-updated_at", "parent_code", "child_code")[offset : offset + limit]
    return {
        "total": total,
        "items": [
            {
                "resultId": str(row.id),
                "title": row.parent_name,
                "subtitle": f"{row.parent_code} · 子件 {row.child_code}",
                "detail": f"{row.child_name} · 数量 {row.child_quantity_milli / 1000:g}",
                "updatedAt": row.updated_at.isoformat(),
                "amountCents": None,
            }
            for row in rows
        ],
        "truncated": offset + min(limit, max(0, total - offset)) < total,
    }
