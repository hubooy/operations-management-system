"""Live logical datasets over explicitly selected, audited central read tools.

No copied facts, SQL executor or second tool registry. Record datasets use the
owning reader's explicit column grant contract in system_datasets.permissions.
The edge remains the sole authority for tool schemas, roles and execution.
"""

import json
from django.utils import timezone
from . import transport
from .policy import AiError, canonical, digest, fields, identifier, passive
from system_datasets import catalog as record_catalog


# Stable public dataset ID -> existing central capability, domain, fixed selector.
DATASETS = {
    "sales_summary": ("get_sales_summary", "sales", {}),
    "sales_category": ("get_sales_category_analysis", "sales", {}),
    "inventory_health": ("get_inventory_health", "inventory", {}),
    "inventory_age": ("get_inventory_page_data", "inventory", {"view": "age"}),
    "inventory_inbound": ("get_inventory_page_data", "inventory", {"view": "inbound"}),
    "inventory_guangdong": ("get_inventory_page_data", "inventory", {"view": "guangdong"}),
    "replenishment_plans": ("list_replenishment_plans", "inventory", {}),
    "product_performance": ("get_product_performance", "products", {}),
    "netshop_catalog": ("get_netshop_page_data", "netshop", {"view": "catalog"}),
    "netshop_products": ("get_netshop_page_data", "netshop", {"view": "performance"}),
    "netshop_product_daily": ("get_netshop_performance", "netshop", {"dataset": "product_daily"}),
    "netshop_promotion": ("get_netshop_performance", "netshop", {"dataset": "promotion"}),
    "market_overview": ("get_market_overview", "market", {}),
    "market_sku_trend": ("get_market_sku_trend", "market", {}),
    "market_brands": ("get_market_brand_analysis", "market", {}),
    "market_price_bands": ("get_market_price_band_analysis", "market", {}),
    "market_pending_review": ("get_market_pending_review_summary", "market", {}),
    "finance_analysis": ("get_finance_page_data", "finance", {"view": "analysis"}),
    "finance_targets": ("get_finance_page_data", "finance", {"view": "targets"}),
    "customer_service": ("get_customer_service_conversations", "customer_service", {}),
    "workflow_tasks": ("get_workflow_page_data", "workflow", {"view": "tasks"}),
    "workflow_operations": ("get_workflow_page_data", "workflow", {"view": "operations"}),
    "workflow_launch_projects": ("get_workflow_page_data", "workflow", {"view": "launch_projects"}),
    "workflow_templates": ("get_workflow_page_data", "workflow", {"view": "templates"}),
}

SURFACE = "ai_chat"
BASE = "/api/ai/datasets"
MAX_QUERY_BYTES = 16000
MAX_RESULT_BYTES = 140000
MAX_RESULT_CHARACTERS = 39500


def visible(principal, surface=SURFACE):
    if surface not in {"ai_chat", "dingtalk_chat"}:
        raise AiError("数据集查询入口无效", "access_denied", 403)
    entries = transport.catalog(principal, surface)
    selected = {}
    for entry in entries:
        policy = entry.get("execution", {})
        if (entry.get("risk") == "read_only"
                and policy.get("mode") == "direct"
                and surface in policy.get("allowedSurfaces", [])
                and principal.role in entry.get("allowedRoles", [])
                and (principal.scope is None or entry.get("scopePolicy") != "unscoped_only")):
            selected[entry["name"]] = entry
    return entries, selected


def allowed(dataset_id, principal, selected):
    if dataset_id in record_catalog.SPECS:
        return principal.role == "admin" and principal.scope is None and "get_system_dataset_records" in selected
    spec = DATASETS.get(dataset_id)
    if not spec or spec[0] not in selected:
        return False
    # The shared workflow tool supports scoped operations records, but its other
    # views explicitly reject scoped callers. Hide those views in discovery too.
    return not (principal.scope is not None and spec[0] == "get_workflow_page_data"
                and spec[2].get("view") != "operations")


def descriptor(dataset_id, entry, *, detail=False):
    tool, domain, fixed = DATASETS[dataset_id]
    item = {
        "id": dataset_id, "title": entry["title"], "domain": domain,
        "description": entry["description"], "schemaVersion": "1",
        "queryEndpoint": f"{BASE}/{dataset_id}/query",
        "schemaEndpoint": f"{BASE}/{dataset_id}",
        "resultFormat": "native_json", "fixedFilters": fixed,
    }
    if detail:
        schema = entry["inputSchema"]
        item.update({
            "querySchema": {**schema,
                "properties": {k: v for k, v in schema["properties"].items() if k not in fixed},
                "required": [k for k in schema.get("required", []) if k not in fixed]},
            "sourceTool": tool, "scopePolicy": entry["scopePolicy"],
            "limits": {**entry["execution"], "maxQueryBytes": MAX_QUERY_BYTES,
                       "maxEnvelopeCharacters": MAX_RESULT_CHARACTERS},
            "resultContract": {
                "data": "原业务工具 JSON；保留明细、汇总、单位、覆盖、分页及截断字段，不对截断行重算总计。",
                "freshness": "查询前读取的销售/库存水位，仅覆盖这两个域，不能当作其他数据集截止日期。",
                "dataCutoffDate": "仅取来源明确给出的 dataCutoffDate；未知为 null，不能解释为今日或无数据。",
                "consistency": "live_per_source：多次只读查询，不保证跨域或跨页原子快照。",
            },
            "pandasExport": {
                "collection": "trend.items" if dataset_id == "sales_category" else (
                    "items" if dataset_id in {"inventory_age", "inventory_inbound", "inventory_guangdong",
                    "inventory_health", "product_performance", "replenishment_plans", "finance_targets",
                    "netshop_catalog", "netshop_products", "customer_service", "workflow_operations", "workflow_tasks",
                    "workflow_launch_projects"} else None),
                "instructions": "先 query_system_dataset 检查实际数组和标量字段；inputsJson 可用 columns 选择所需字段，排除嵌套对象。分页分析数据从 page=1 开始，完整性由来源分页信息验证；没有完整性证明的数组不能计算全量。",
            },
        })
    return item


def describe(principal, dataset_id=None, *, page=1, page_size=20, domain=None, surface=SURFACE):
    _, selected = visible(principal, surface)
    if dataset_id is not None:
        identifier(dataset_id, "dataset")
        if not allowed(dataset_id, principal, selected):
            raise AiError("数据集不存在或当前账号无权访问", "not_found", 404)
        if dataset_id in record_catalog.SPECS:
            return record_catalog.describe(record_catalog.SPECS[dataset_id], detail=True)
        return descriptor(dataset_id, selected[DATASETS[dataset_id][0]], detail=True)
    items = [descriptor(key, selected[spec[0]]) for key, spec in DATASETS.items()
             if allowed(key, principal, selected)]
    items.extend(record_catalog.describe(spec) for key, spec in record_catalog.SPECS.items()
                 if allowed(key, principal, selected))
    if domain is not None:
        if not isinstance(domain, str) or domain not in record_catalog.DOMAINS:
            raise AiError("domain 无效")
        items = [item for item in items if item["domain"] == domain]
    if type(page) is not int or not 1 <= page <= 100 or type(page_size) is not int or not 1 <= page_size <= 50:
        raise AiError("目录分页参数无效")
    window = items[(page-1)*page_size:page*page_size]
    return {"schemaVersion": "1", "items": window, "count": len(items), "total": len(items),
            "returned": len(window), "page": page, "pageSize": page_size, "hasMore": page*page_size < len(items),
            "readOnly": True, "storage": "live_authoritative_sources"}


def _result(value, expected_tool):
    if not isinstance(value, dict) or value.get("toolName") != expected_tool:
        raise AiError("数据集来源响应无效", "service_unavailable", 503)
    if value.get("auditStatus") == "unavailable":
        raise AiError("数据集查询审计不可用", "service_unavailable", 503)
    if value.get("ok") is not True:
        code = value.get("error", {}).get("code")
        if code in {"invalid_arguments", "invalid_request"}:
            raise AiError("查询参数不符合数据集 schema")
        if code in {"access_denied", "forbidden", "tool_forbidden", "tool_not_allowed"}:
            raise AiError("当前账号无权查询数据集", "access_denied", 403)
        if code in {"payload_too_large", "tool_result_too_large"}:
            raise AiError("请缩小字段范围或分页大小", "payload_too_large", 413)
        raise AiError("数据集来源执行失败，请检查权限、范围或稍后重试", "service_unavailable", 503)
    if not isinstance(value.get("data"), dict):
        raise AiError("数据集来源响应无效", "service_unavailable", 503)
    return value["data"]


def query(dataset_id, body, principal, request_id, *, surface=SURFACE):
    identifier(dataset_id, "dataset")
    fields(body, {"query"})
    args = body.get("query", {})
    if not isinstance(args, dict):
        raise AiError("query 必须为 JSON 对象")
    passive(args, MAX_QUERY_BYTES)
    entries, selected = visible(principal, surface)
    if not allowed(dataset_id, principal, selected):
        raise AiError("数据集不存在或当前账号无权访问", "not_found", 404)
    record_spec = record_catalog.SPECS.get(dataset_id)
    tool, domain, fixed = ("get_system_dataset_records", record_spec["domain"], {}) if record_spec else DATASETS[dataset_id]
    # Selectors belong to the dataset; callers cannot switch to another view.
    schema = record_catalog.query_schema(record_spec) if record_spec else selected[tool]["inputSchema"]
    fields(args, set(schema["properties"]) - set(fixed), set(schema.get("required", [])) - set(fixed))
    if "get_data_freshness" not in selected:
        raise AiError("数据水位查询不可用", "service_unavailable", 503)
    policy_digest = digest(entries)

    def execute(name, arguments):
        return _result(transport.execute_tool(
            name, arguments, principal, surface=surface, request_id=request_id,
            policy_digest=policy_digest,
        ), name)

    freshness = execute("get_data_freshness", {})
    data = execute(tool, {"dataset": dataset_id, "queryJson": canonical(args)} if record_spec else {**args, **fixed})
    result = {
        "schemaVersion": "1", "dataset": dataset_id,
        "source": {"domain": domain, "tool": tool, "storage": "Django/PostgreSQL"},
        "requestId": request_id, "queriedAt": timezone.now().isoformat(),
        "timezone": "Asia/Shanghai", "consistency": "live_per_source",
        "query": {**args, **fixed}, "freshness": freshness,
        "dataCutoffDate": data.get("dataCutoffDate"), "data": data,
    }
    encoded = canonical(result)
    # Reject, never slice JSON or silently drop source rows/totals to fit a budget.
    if len(encoded) > MAX_RESULT_CHARACTERS or len(encoded.encode()) > MAX_RESULT_BYTES:
        raise AiError("结果超限，请缩小日期范围或分页大小", "payload_too_large", 413)
    return result


def consumer(payload, principal, request_id):
    operation = payload.get("operation")
    if operation == "datasets-describe":
        fields(payload, {"operation", "dataset", "page", "pageSize", "domain", "surface"}, {"operation"})
        return describe(principal, payload.get("dataset"), page=payload.get("page", 1),
                        page_size=payload.get("pageSize", 20), domain=payload.get("domain"), surface=payload.get("surface", SURFACE))
    fields(payload, {"operation", "dataset", "queryJson", "surface"}, {"operation", "dataset", "queryJson"})
    raw = payload["queryJson"]
    if not isinstance(raw, str) or len(raw.encode()) > MAX_QUERY_BYTES:
        raise AiError("queryJson 超限或不是字符串")
    try:
        args = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise AiError("queryJson 必须为有效 JSON 对象") from error
    return query(payload["dataset"], {"query": args}, principal, request_id, surface=payload.get("surface", SURFACE))
