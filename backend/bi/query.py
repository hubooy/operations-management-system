from __future__ import annotations

from datetime import date

from django.utils import timezone

from inventory.query import inventory_overview
from inventory.revisions import revision_value as inventory_revision_value
from sales.auth import Principal
from sales.query import revision_token as sales_revision_token
from sales.summary import dashboard_projection, get_sales_summary

from .errors import BiApiError


BI_RANGES = {"today", "yesterday", "last7", "last15", "last30", "month", "quarter", "custom"}
BI_CONTRACT_VERSION = "bi-dashboard-read-model-v1"


def _iso_date(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise BiApiError(f"{label} 日期格式无效，请使用 YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise BiApiError(f"{label} 日期格式无效，请使用 YYYY-MM-DD")
    return value


def parse_overview_options(query) -> dict[str, str | None]:
    allowed = {"range", "startDate", "endDate"}
    if any(key not in allowed for key in query):
        raise BiApiError("BI 看板包含未知查询参数")
    for key in allowed:
        if len(query.getlist(key)) > 1:
            raise BiApiError(f"{key} 参数不能重复")
    range_name = query.get("range", "month")
    if range_name not in BI_RANGES:
        raise BiApiError("range 不在 BI 看板允许范围内")
    start_date = _iso_date(query.get("startDate"), "开始")
    end_date = _iso_date(query.get("endDate"), "结束")
    if range_name == "custom":
        if start_date is None or end_date is None:
            raise BiApiError("自定义周期必须提供 startDate 和 endDate")
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if start > end:
            raise BiApiError("自定义周期必须满足 startDate <= endDate")
        if (end - start).days > 366:
            raise BiApiError("BI 自定义周期最长支持 367 个自然日")
    elif start_date is not None or end_date is not None:
        raise BiApiError("只有自定义周期可以提供 startDate 和 endDate")
    return {"range": range_name, "startDate": start_date, "endDate": end_date}


def source_revisions() -> dict[str, str]:
    return {
        "salesErp": sales_revision_token(),
        "inventory": inventory_revision_value(),
    }


def composite_revision(revisions: dict[str, str]) -> str:
    return f"{revisions['salesErp']}|{revisions['inventory']}"


def _inventory_options() -> dict[str, object]:
    return {
        "view": "dashboard",
        "startDate": None,
        "endDate": None,
        "query": None,
        "warehouses": [],
        "brands": [],
        "categories": [],
        "warehouseTypes": [],
        "statuses": [],
        "page": 1,
        "pageSize": 1,
        "planPage": 1,
        "planPageSize": 1,
        "planStatus": None,
        "includeCancelledPlans": False,
    }


def _inventory_health_score(inventory: dict[str, object]) -> int | None:
    metrics = inventory.get("metrics")
    health = inventory.get("health")
    if not isinstance(metrics, dict) or not isinstance(health, dict):
        raise BiApiError(
            "库存看板投影不完整",
            code="service_unavailable",
            status=503,
        )
    if not metrics.get("inventoryAlertsEnabled") or metrics.get("recommendationsSuppressed"):
        return None
    urgent = metrics.get("urgentCount")
    stagnant = health.get("stale")
    if isinstance(urgent, bool) or not isinstance(urgent, int) or urgent < 0:
        raise BiApiError("库存预警计数无效", code="service_unavailable", status=503)
    if isinstance(stagnant, bool) or not isinstance(stagnant, int) or stagnant < 0:
        raise BiApiError("库存健康计数无效", code="service_unavailable", status=503)
    return max(0, min(100, 100 - urgent * 8 - stagnant * 2))


def get_bi_overview(
    principal: Principal,
    options: dict[str, str | None],
) -> tuple[dict[str, object], str]:
    if principal.scope is not None:
        raise BiApiError("BI 看板仅支持未受限数据范围账号", code="access_denied", status=403)

    for _attempt in range(2):
        before = source_revisions()
        sales = dashboard_projection(
            get_sales_summary(
                range_name=str(options["range"]),
                projection="dashboard",
                start_date=options["startDate"],
                end_date=options["endDate"],
                product_queries=[],
                product_codes=[],
                platforms=[],
                shop=None,
                outlets=[],
                categories=[],
                principal=principal,
            )
        )
        inventory = inventory_overview(principal, _inventory_options())
        after = source_revisions()
        if before == after:
            revision = composite_revision(after)
            return (
                {
                    "projection": "dashboard",
                    "contractVersion": BI_CONTRACT_VERSION,
                    "generatedAt": timezone.now().isoformat(),
                    "revision": revision,
                    "sourceRevisions": after,
                    "sales": sales,
                    "inventory": inventory,
                    "inventoryHealthScore": _inventory_health_score(inventory),
                },
                revision,
            )
    raise BiApiError(
        "经营数据版本持续变化，请稍后重试",
        code="service_unavailable",
        status=503,
    )
