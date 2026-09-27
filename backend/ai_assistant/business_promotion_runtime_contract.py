"""Pure candidate selection/reference contract; no registered runtime authority.

The owning adapter must load context and raw source entries from persisted
roots. Model-supplied digests are constraints, never evidence of authorization.
"""
from business_analysis.contracts import AnalysisContractError, canonical, digest, comparison_periods
from business_analysis.evidence_v2 import normalize_sources, _fields, _identifier, _sha
from business_analysis.promotion_keyword_sku import ALGORITHM_VERSION, VIEWS, LIMITS
from business_analysis.promotion_views import _copy
from . import business_screening_runtime_contract as screening

PROFILE = "business-agent-screening-promotion-reference-v1"
SURFACE = "business_agent_screening_promotion_v1"
PACKAGE_TOOL = "get_business_promotion_screening_package_v1"
TABLE_TOOL = "get_business_promotion_screening_analysis_v1"
BUDGET_TOOL = "get_business_promotion_screening_budget_v1"
PROMOTION_TOOL = "get_business_promotion_keyword_sku_v1"
TOOLS = frozenset((PACKAGE_TOOL, TABLE_TOOL, BUDGET_TOOL, PROMOTION_TOOL))
TOOL_ORDER = (PACKAGE_TOOL, TABLE_TOOL, BUDGET_TOOL, PROMOTION_TOOL)
# Four prospective entries retain the three existing responsibilities. This is
# not a catalog implementation or permission to register old tools on a surface.
TOOL_CAPABILITIES = ((PACKAGE_TOOL, ("role_package",)),
    (TABLE_TOOL, ("native", "mapped")), (BUDGET_TOOL, ("budget",)),
    (PROMOTION_TOOL, ("keyword_sku", "keyword_sku_context")))
SCHEMA = "business-promotion-selection-candidate-v1"
REFERENCE_SCHEMA = "business-promotion-row-reference-candidate-v1"
CONTEXT_FIELDS = frozenset(("reportId", "runId", "screeningId", "sealedDigest"))
MONEY_METRICS = frozenset(("spendCents", "reportedGmvCents", "directGmvCents", "indirectGmvCents", "newCustomerGmvCents"))
VALUE_FIELDS = frozenset(("value", "baseline", "difference", "changeRate"))
PROMOTION_ROLES = frozenset(("promotion", "independent_review", "report"))
SNAPSHOT_SCHEMA = "business-promotion-runtime-snapshot-candidate-v1"
PROMOTION_VIEWS = ("keyword_sku", "keyword_sku_context")
# Pin the complete predecessor graph, including instructions and the budget
# variant. A future edit to the old profile must not silently redefine this one.
_BASE_GRAPH_DIGEST = {
    False: "b825ed989cd07e29fb0a463c636b7febe4a597baebac348cf99203d4f0d82430",
    True: "606304c4dfd49f3d2fb5eafbc057e6bfda2fb63ca9b0e7ad73615fc2c73b6bd5",
}


def _require(condition, message):
    if not condition: raise AnalysisContractError(message)


def graph(with_budget=False):
    """Prospective five-role graph. It grants no workflow or tool authority."""
    _require(type(with_budget) is bool, "预算标记必须为精确布尔值")
    value = screening.graph(with_budget)
    _require(digest(value) == _BASE_GRAPH_DIGEST[with_budget], "旧筛查图已变化，须显式重审新协议")
    names = ((screening.PACKAGE_TOOL, PACKAGE_TOOL), (screening.TABLE_TOOL, TABLE_TOOL),
             (screening.BUDGET_TOOL, BUDGET_TOOL))
    for node in value["nodes"]:
        if node["type"] != "agent":
            node["instruction"] += "复核词货引用时须检查实际读取回执、商品身份缺口和调整前提。"
            continue
        instruction = node["instruction"]
        for old, new in names:
            instruction = instruction.replace(old, new)
        if node["key"] in PROMOTION_ROLES:
            instruction += ("本角色可用" + PROMOTION_TOOL + "读取固定京东推广来源的keyword_sku及"
                "keyword_sku_context；只能使用workflowInput固定promotionSelector中的sourceKey、baselineKey和两种视图。"
                "先完整读取本人角色包；词货页从offset=0沿nextOffset读取，引用必须来自本人实际读取的页或精确行回执，"
                "不能借用其他Agent回执。词货结构化引用须含kind=promotion_keyword_sku、sourceKey、可选baselineKey、"
                "view、rowIndex、完整rowId、tableBindingDigest、metric、field，不填number或数值value。"
                "仅明确promotedSkuId可建议商品操作；缺身份桶保留费用并说明缺口。两种视图是相同费用的不同分组，"
                "不能相加；归因成交不是ERP净销售或增量收益。")
        else:
            instruction += "本角色不得调用词货工具或输出promotion_keyword_sku数值引用。"
        node["instruction"] = instruction
    return value


def freeze_snapshot(sources, context, selector):
    """Bind both views to one explicit current source and optional baseline."""
    selector = _copy(selector, 2048)
    _require(type(selector) is dict and set(selector) in ({"sourceKey"}, {"sourceKey", "baselineKey"}),
             "固定推广来源选择字段无效")
    fixed = [selection(sources, context, {**selector, "view": view}) for view in PROMOTION_VIEWS]
    first = fixed[0]
    _require(all(item["catalogDigest"] == first["catalogDigest"] and item["source"] == first["source"]
                 and item["baseline"] == first["baseline"] for item in fixed[1:]), "两种词货视图来源不一致")
    promotion_selector = {**selector, "views": list(PROMOTION_VIEWS)}
    binding = {"context": first["context"], "catalogDigest": first["catalogDigest"],
        "source": first["source"], "baseline": first["baseline"],
        "promotionSelector": promotion_selector, "algorithmVersion": ALGORITHM_VERSION,
        "executionProfile": PROFILE}
    return {"schemaVersion": SNAPSHOT_SCHEMA, "executionProfile": PROFILE,
        "promotionSelector": promotion_selector, "contextDigest": digest(binding),
        "sealedDigest": first["context"]["sealedDigest"],
        "promotionAlgorithmVersion": ALGORITHM_VERSION, "catalogDigest": first["catalogDigest"],
        "authorityVerified": False, "registered": False}


def checked_snapshot(sources, context, value):
    """Rebuild from supplied roots; the owning adapter must load those roots."""
    value = _copy(value, 4096)
    _require(type(value) is dict and type(value.get("promotionSelector")) is dict,
             "推广快照结构无效")
    selector = value["promotionSelector"]
    _require(selector.get("views") == list(PROMOTION_VIEWS), "推广视图清单已变化")
    expected = freeze_snapshot(sources, context, {k: v for k, v in selector.items() if k != "views"})
    _require(canonical(value) == canonical(expected), "推广快照与固定来源、身份或算法不一致")
    return expected


def scoped_row_reference(role, sources, context, snapshot, selector, reference):
    """Prospective role-bound lookup only; no row or reading proof is resolved."""
    _require(type(role) is str and role in PROMOTION_ROLES, "此角色不能引用词货数值")
    fixed = checked_snapshot(sources, context, snapshot)
    selector = _copy(selector, 2048)
    _require(type(selector) is dict and set(selector) in ({"sourceKey", "view"},
             {"sourceKey", "baselineKey", "view"}), "词货引用选择字段无效")
    expected_selector = {k: v for k, v in fixed["promotionSelector"].items() if k != "views"}
    _require(selector.get("view") in PROMOTION_VIEWS and
             {k: v for k, v in selector.items() if k != "view"} == expected_selector,
             "词货引用跨固定来源、基期或视图")
    result = row_reference(sources, context, selector, reference)
    result["role"] = role
    result["promotionSnapshotDigest"] = digest(fixed)
    result["referenceDigest"] = digest({k: v for k, v in result.items() if k != "referenceDigest"})
    return result


def selection(sources, context, selector):
    """Validate against the complete supplied directory, never guess a base."""
    sources = _copy(sources, 128 * 1024)
    context, selector = _copy(context, 2048), _copy(selector, 2048)
    _fields(context, CONTEXT_FIELDS, "推广上下文")
    for key in CONTEXT_FIELDS - {"sealedDigest"}: _identifier(context[key], key)
    _sha(context["sealedDigest"], "sealedDigest")
    _require(type(selector) is dict and set(selector) in (
        {"sourceKey", "view"}, {"sourceKey", "view", "baselineKey"}), "推广选择字段无效")
    _identifier(selector["sourceKey"], "sourceKey")
    if "baselineKey" in selector: _identifier(selector["baselineKey"], "baselineKey")
    _require(type(selector["view"]) is str and selector["view"] in VIEWS, "推广视图不支持")
    catalog = normalize_sources(sources)
    by_key = {item["key"]: item for item in catalog}
    def selected(key):
        _require(key in by_key, "来源不在固定目录")
        item = by_key[key]
        _require(item["domain"] == "netshop" and item["query"]["platform"] == "京东"
            and item["query"]["dataset"] == "promotion", "仅支持京东推广来源")
        return item
    current = selected(selector["sourceKey"])
    _require(current["query"]["window"] == "current", "主来源必须是当前期")
    baseline = selected(selector["baselineKey"]) if "baselineKey" in selector else None
    if baseline:
        a, b = current["query"], baseline["query"]
        _require(current["key"] != baseline["key"] and b["window"] in ("previous", "yearAgo")
            and {k:v for k,v in a.items() if k != "window"} == {k:v for k,v in b.items() if k != "window"},
            "基期来源身份、原日期区间或窗口不对应")
    result = {"schemaVersion": SCHEMA, "profile": PROFILE, "algorithmVersion": ALGORITHM_VERSION,
        "context": context, "catalogDigest": digest(catalog), "selector": selector,
        "source": current, "baseline": baseline,
        "periods": comparison_periods(current["query"]["startDate"], current["query"]["endDate"]),
        "authorityVerified": False, "registered": False}
    result["contextDigest"] = digest(result)
    return result


def row_reference(sources, context, selector, reference):
    """Validate a numeric lookup request, without accepting a claimed number.

The returned reference is not a resolved row. Owning read_row must recheck row
identity, table binding, missing identity and comparison availability later.
"""
    fixed = selection(sources, context, selector)
    reference = _copy(reference, 2048)
    _fields(reference, {"contextDigest", "tableBindingDigest", "rowIndex", "rowId", "metric", "field"}, "推广行引用")
    for key in ("contextDigest", "tableBindingDigest", "rowId"): _sha(reference[key], key)
    _require(reference["contextDigest"] == fixed["contextDigest"], "推广引用上下文不一致")
    _require(type(reference["rowIndex"]) is int and 0 <= reference["rowIndex"] < LIMITS["maxGroups"], "推广行位置无效")
    _require(type(reference["metric"]) is str and reference["metric"] in MONEY_METRICS, "推广金额引用指标无效")
    _require(type(reference["field"]) is str and reference["field"] in VALUE_FIELDS, "推广金额引用字段无效")
    _require(reference["field"] == "value" or fixed["baseline"] is not None, "比较金额引用必须固定基期")
    result = {"schemaVersion": REFERENCE_SCHEMA, "selection": fixed, "reference": reference,
              "authorityVerified": False, "resolved": False}
    result["referenceDigest"] = digest(result)
    return result
