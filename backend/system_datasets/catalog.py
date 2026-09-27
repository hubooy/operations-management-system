import json
from pathlib import Path
from ai_assistant.policy import AiError

MANIFEST = json.loads(Path(__file__).with_name("manifest.json").read_text(encoding="utf-8"))
SPECS = {item["id"]: item for item in MANIFEST["datasets"]}
BY_MODEL = {(item["domain"], item["model"]): item for item in SPECS.values()}
DOMAINS = {
    "sales": ("reader", "teruisi_sales_reader"),
    "erp_reference": ("erp_reference_reader", "teruisi_erp_reference_reader"),
    "finance": ("finance_reader", "teruisi_finance_reader"),
    "netshop": ("netshop_reader", "teruisi_netshop_reader"),
    "market": ("market_reader", "teruisi_market_reader"),
    "products": ("products_reader", "teruisi_products_reader"),
    "inventory": ("inventory_reader", "teruisi_inventory_reader"),
    "workflow": ("workflow_reader", "teruisi_workflow_reader"),
    "customer_service": ("customer_service_reader", "teruisi_customer_service_reader"),
    "access_control": ("access_control_reader", "teruisi_access_control_reader"),
    "ai_assistant": ("ai_reader", "teruisi_ai_reader"),
    "bi": ("bi_reader", "teruisi_bi_reader"),
}


def authorize(spec, principal):
    if principal.role not in spec["roles"] or principal.scope is not None:
        raise AiError("该记录数据集仅对未限制数据范围的管理员开放", "access_denied", 403)


def query_schema(spec):
    names = list(spec["fields"])
    return {"type": "object", "additionalProperties": False, "properties": {
        "columns": {"type": "array", "minItems": 1, "maxItems": 50, "uniqueItems": True,
                    "items": {"type": "string", "enum": names}},
        "filters": {"type": "array", "maxItems": 8, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"field": {"type": "string", "enum": names},
                "op": {"type": "string", "enum": ["eq", "gte", "gt", "lte", "lt", "in", "isnull"]},
                "value": {"description": "与字段类型一致的标量；in 使用最多 50 个标量，isnull 使用布尔值"}},
            "required": ["field", "op", "value"]}},
        "pageSize": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        "cursor": {"type": "string", "maxLength": 8000},
        "textOffset": {"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0},
        "textLimit": {"type": "integer", "minimum": 1, "maximum": 8000, "default": 2000},
    }}


def describe(spec, detail=False):
    result = {"id": spec["id"], "title": spec["title"], "domain": spec["domain"],
        "kind": "records", "schemaVersion": "1", "fieldCount": len(spec["fields"]),
        "queryEndpoint": f'/api/ai/datasets/{spec["id"]}/query',
        "schemaEndpoint": f'/api/ai/datasets/{spec["id"]}',
        "description": "权威记录数据集，按唯一键连续分页，含业务记录及其原有状态；不假设全部行均为已发布事实。"}
    if detail:
        result.update(querySchema=query_schema(spec), fields=spec["fields"],
            excludedFields=spec["excludedFields"], scopePolicy=spec["scopePolicy"],
            ownerPolicy="current_actor_only" if spec.get("owner") else "authorized_admin",
            resultFormat="rows", pagination="encrypted_keyset_cursor",
            consistency="live_per_page", sourceDomain=spec["domain"],
            limits={"maxPageSize": 100, "maxColumns": 50, "defaultCellCharacters": 2000, "maxCellCharacters": 8000,
                    "maxResponseCharacters": 34000, "cursorTtlSeconds": 1800})
    return result
