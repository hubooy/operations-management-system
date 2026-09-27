"""Bounded, low-trust page selections; never an authority or data source."""

import re
from datetime import date
from .policy import AiError, canonical, fields

MODULES = {
    "n8n_workflows", "dashboard", "shop", "market", "customer_service", "sales",
    "inventory", "product", "workflow", "import", "settings", "ai",
}
VIEWS = {
    "n8n_workflows": {"jackyun", "tmall", "jd", "jd_market", "jd_promotion", "jd_promotion_cut_meat"},
    "dashboard": {"overview"}, "shop": {"analysis", "outlets", "platforms", "products", "promotion"},
    "market": {"ranking", "overview", "compare", "settings"}, "customer_service": {"conversations"},
    "sales": {"overview", "channel", "category", "finance", "targets"},
    "inventory": {"overview", "age", "plan", "stale", "inbound", "guangdong"},
    "product": {"overview", "calculator"},
    "workflow": {"plan", "inspection", "reviews", "launch", "launch-followup", "variables"},
    "import": {"files", "history", "continuity"}, "settings": {"parameters", "master", "dingtalk", "permissions"},
    "ai": {"assistant", "agents", "memory", "sandbox", "space", "management", "scheduled", "configuration", "reports", "skills", "pipelines"},
}
FILTERS = {
    "platforms", "shops", "channels", "categories", "warehouses", "brands",
    "skus", "spus", "productCodes", "months", "scope", "rankingDimension",
    "query", "status", "dataset", "operationMode", "subcategories",
    "dueFrom", "dueTo", "owner", "selectedIds", "outletKeys", "priceBands", "suppliers",
    "warehouseTypes", "healthStatuses", "ageBuckets", "agents", "robotScopes", "problemTypes",
    "conversionStatuses", "priorities", "sources", "stageKey", "stageStatus", "proposedFrom",
    "weekStart", "marginFilterKeys", "itemSegments", "storageStatuses", "recognitionSources", "risk",
    "masterSection", "priceStatuses", "candidatePriceSources", "annotationStatuses", "pendingPriceSources",
}


def module_key(value):
    if not isinstance(value, str) or value not in MODULES:
        raise AiError("AI 会话板块无效")
    return value


def normalize(value):
    if value is None:
        return None
    fields(value, {"version", "module", "moduleLabel", "view", "period", "importSource", "suggestedTools", "filters"}, {"module", "view"})
    if type(value.get("version", 1)) is not int or value.get("version", 1) != 1:
        raise AiError("页面上下文版本无效")
    module_key(value["module"])
    if not isinstance(value["view"], str) or value["view"] not in VIEWS[value["module"]]:
        raise AiError("页面视图无效")
    period = value.get("period")
    if period is not None:
        fields(period, {"startDate", "endDate"}, {"startDate", "endDate"})
        try:
            start, end = (date.fromisoformat(period[k]) for k in ("startDate", "endDate"))
            if start > end or str(start) != period["startDate"] or str(end) != period["endDate"]:
                raise ValueError()
        except (TypeError, ValueError):
            raise AiError("页面日期无效") from None
    selections = value.get("filters", {})
    fields(selections, FILTERS)
    if value["module"] == "customer_service" and "query" in selections:
        raise AiError("顾客搜索内容不能作为页面上下文保存")
    for item in selections.values():
        values = item if isinstance(item, list) else [item]
        if not 1 <= len(values) <= 20 or any(not isinstance(v, str) or not v.strip() or len(v) > 160 for v in values):
            raise AiError("页面筛选超出范围")
    # Display-only labels/tool suggestions are reconstructed on the client, not stored.
    result = {"version": 1, "module": value["module"], "view": value["view"], "period": period, "filters": selections}
    source = value.get("importSource")
    if source is not None and (not isinstance(source, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", source)):
        raise AiError("导入来源无效")
    result["importSource"] = source
    if len(canonical(result)) > 4000:
        raise AiError("页面上下文过长，请减少所选项目")
    return result
