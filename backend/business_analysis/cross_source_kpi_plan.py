"""Pure single-shop cross-source KPI selection; no fact assignment or totals.

The caller must supply one sealed directory and owning Reader.info records.
Those supplied records remain constraints, never database or report authority.
"""
import re

from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, comparison_periods, coverage, digest
from .evidence_v2 import normalize_sources
from .mapping_plan import build as mapping_build, validate_baseline_pair
from .promotion_views import _copy


SCHEMA = "business-cross-source-kpi-plan-candidate-v1"
MAX_V2_SOURCE_PAGES = 2000
MAX_V2_PAGE_ROWS = 100
FAMILIES = ("erpSales", "netshopSku", "netshopSpu", "promotion")
WINDOWS = ("current", "previous", "yearAgo")
FAMILY_DATASET = {"erpSales": ("sales", None), "netshopSku": ("netshop", "sku"),
    "netshopSpu": ("netshop", "spu"), "promotion": ("netshop", "promotion")}
ERP_METRICS = {"netSalesCents", "positiveSalesCents", "refundCents", "costCents",
    "grossProfitCents", "reportedGrossProfitCents", "feeCents", "netQuantity",
    "positiveQuantity", "returnQuantity", "netSalesExcludingAccessoriesCents"}
PRODUCT_METRICS = {"paymentCents", "paymentQuantity", "productDayVisitors",
    "pageViews", "reportedOrders"}
PROMOTION_METRICS = {"spendCents", "reportedGmvCents", "impressions",
    "clicks", "reportedOrderLines", "cartQuantity"}
PROMOTION_OPTIONAL_METRICS = {"directGmvCents", "indirectGmvCents",
    "newCustomerGmvCents"}
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _need(ok, message="跨来源KPI选择合同无效"):
    if not ok:
        raise AnalysisContractError(message)


def _id(value):
    _need(type(value) is str and _ID.fullmatch(value) is not None,
        "来源或报告身份无效")
    return value


def _sha(value):
    _need(type(value) is str and _SHA.fullmatch(value) is not None,
        "来源摘要无效")
    return value


def _context(value):
    _need(type(value) is dict and set(value) == {"reportId", "evidenceRunId",
        "evidenceVersion", "sealedDigest", "ownerEmail", "scope"})
    for key in ("reportId", "evidenceRunId"):
        _id(value[key])
    _sha(value["sealedDigest"])
    _need(type(value["evidenceVersion"]) is int and 1 <= value["evidenceVersion"] <= MAX_SAFE_INTEGER)
    owner = value["ownerEmail"]
    _need(type(owner) is str and owner == owner.strip().lower() and
        1 <= len(owner) <= 320 and "@" in owner and value["scope"] is None,
        "首版跨来源KPI仅接受无范围管理员的单店封存任务")


def _info(info, source, periods):
    _need(type(info) is dict and set(info) == {"metadata", "expected", "pageCount"})
    meta, expected = info["metadata"], info["expected"]
    _need(type(meta) is dict and type(expected) is dict
        and set(expected) == {"sourceRef", "rowCount", "metrics", "reconciled", "evidenceDigest"}
        and expected["reconciled"] is True
        and type(info["pageCount"]) is int and 1 <= info["pageCount"] <= MAX_V2_SOURCE_PAGES)
    _sha(expected["sourceRef"]); _sha(expected["evidenceDigest"])
    _need(type(expected["rowCount"]) is int and 0 <= expected["rowCount"] <= MAX_SAFE_INTEGER
        and type(expected["metrics"]) is dict
        and type(meta.get("sourceRevision")) is str and bool(meta["sourceRevision"]),
        "封存来源水位或行数无效")
    metric_keys = set(expected["metrics"])
    if source["domain"] == "sales":
        _need(metric_keys == ERP_METRICS, "ERP来源缺少固定销售与成本口径")
    elif source["query"].get("dataset") == "master":
        _need(not metric_keys, "身份主数据不能含经营金额")
    elif source["query"]["dataset"] == "promotion":
        _need(PROMOTION_METRICS <= metric_keys
            and metric_keys <= PROMOTION_METRICS | PROMOTION_OPTIONAL_METRICS,
            "推广来源核心指标不完整或混入其他口径")
    else:
        _need(metric_keys == PRODUCT_METRICS,
            "SKU/SPU原生支付与商品日指标不完整")
    for cell in expected["metrics"].values():
        _need(type(cell) is dict and set(cell) ==
            {"value", "presentRows", "missingRows"}
            and type(cell["presentRows"]) is int and type(cell["missingRows"]) is int
            and 0 <= cell["presentRows"] <= expected["rowCount"]
            and 0 <= cell["missingRows"] <= expected["rowCount"]
            and cell["presentRows"] + cell["missingRows"] == expected["rowCount"]
            and ((cell["value"] is None and cell["presentRows"] == 0)
                or (type(cell["value"]) is int and abs(cell["value"]) <= MAX_SAFE_INTEGER
                    and cell["presentRows"] > 0)),
            "来源指标有值/缺值行数或null语义不一致")
    observed = meta.get("coverage")
    if source["query"].get("dataset") == "master":
        _need(type(observed) is dict and observed.get("status") in
            {"current_master", "no_records"}
            and observed.get("historicalMapping") is False
            and (observed["status"] == "no_records") == (expected["rowCount"] == 0),
            "当前主数据快照或无记录状态无效")
    else:
        _need(type(observed) is dict and type(observed.get("presentDates")) is list
            and len(observed["presentDates"]) <= 93
            and all(type(day) is str for day in observed["presentDates"])
            and len(set(observed["presentDates"])) == len(observed["presentDates"])
            and canonical(observed) == canonical(coverage(
                periods[source["query"]["window"]], observed["presentDates"]))
            and expected["rowCount"] >= len(observed["presentDates"]),
            "来源日期覆盖与固定比较期不一致")
    return {"sourceRef": expected["sourceRef"],
        "evidenceDigest": expected["evidenceDigest"],
        "rowCount": expected["rowCount"], "sourceRevision": meta["sourceRevision"],
        "coverage": observed, "coverageDigest": digest(observed),
        "metricKeys": sorted(expected["metrics"]), "pageCount": info["pageCount"],
        "capacityStatus": ("within_current_v2_page_budget"
            if expected["rowCount"] <= info["pageCount"] * MAX_V2_PAGE_ROWS
            else "reference_scale_capacity_gap")}


def prepare_candidate(sources, infos, context, source_keys):
    """Fix all four measure families and explicit missing comparison sources."""
    context = _copy(context, 2048)
    source_keys = _copy(source_keys, 4096)
    infos = _copy(infos, 128*1024)
    _context(context)
    _need(type(source_keys) is dict and set(source_keys) == {*FAMILIES, "master"})
    _need(all(type(source_keys[family]) is dict and set(source_keys[family]) == set(WINDOWS)
        for family in FAMILIES))
    catalogue = normalize_sources(_copy(sources, 128*1024))
    source_descriptors = [{key: entry[key] for key in ("key", "domain", "query")}
        for entry in catalogue]
    by_key = {source["key"]: source for source in catalogue}
    master_key = _id(source_keys["master"])
    _need(master_key in by_key)
    master = by_key[master_key]
    qmaster = master["query"]
    _need(master["domain"] == "netshop" and qmaster["dataset"] == "master"
        and qmaster["window"] == "current")
    shop, platform = qmaster["shop"], qmaster["platform"]
    _need(bool(shop) and bool(platform))
    periods = comparison_periods(qmaster["startDate"], qmaster["endDate"])
    selected, missing, status = {master_key}, [], {}
    pairs = []
    for family in FAMILIES:
        keys = source_keys[family]
        current_key = _id(keys["current"])
        _need(current_key in by_key and current_key not in selected)
        current = by_key[current_key]
        expected_domain, dataset = FAMILY_DATASET[family]
        query = current["query"]
        _need(current["domain"] == expected_domain
            and (dataset is None or query.get("dataset") == dataset)
            and query["window"] == "current"
            and (query["platform"], query["shop"]) == (platform, shop),
            "KPI来源跨平台、跨店铺或角色错配")
        selected.add(current_key)
        status[family] = {"current": "selected"}
        if family == "erpSales":
            pairs.append({"salesKey": current_key, "masterKey": master_key})
        for window in WINDOWS[1:]:
            key = keys[window]
            if key is None:
                _need(not any(entry["domain"] == expected_domain
                    and (dataset is None or entry["query"].get("dataset") == dataset)
                    and entry["query"]["window"] == window
                    and {name: value for name, value in entry["query"].items()
                        if name != "window"}
                        == {name: value for name, value in query.items()
                            if name != "window"} for entry in catalogue),
                    "匹配比较来源已在封存目录，不能声明缺源")
                missing.append({"family": family, "window": window,
                    "meaning": "来源未选择，比较数值为null而非0"})
                status[family][window] = "missing_source"
                continue
            key = _id(key)
            _need(key in by_key and key not in selected)
            entry = by_key[key]
            baseline = entry["query"]
            _need(entry["domain"] == expected_domain
                and (dataset is None or baseline.get("dataset") == dataset)
                and baseline["window"] == window
                and {name: value for name, value in baseline.items() if name != "window"}
                    == {name: value for name, value in query.items() if name != "window"},
                "比较来源不属于同一平台、店铺、粒度、渠道与原日期范围")
            selected.add(key)
            status[family][window] = "selected"
            if family == "erpSales":
                pairs.append({"salesKey": key, "masterKey": master_key})
    _need(type(infos) is dict and set(infos) == selected,
        "仅可提交精确已选封存来源的完整信息")
    binding = {key: _info(infos[key], by_key[key], periods) for key in sorted(selected)}
    for family in FAMILIES:
        for window in WINDOWS:
            key = source_keys[family][window]
            if key is not None and binding[key]["rowCount"] == 0:
                status[family][window] = "selected_no_records"
            elif key is not None and binding[key]["capacityStatus"] == "reference_scale_capacity_gap":
                status[family][window] = "unsupported_current_v2_collector"
    if binding[master_key]["rowCount"] == 0:
        status["master"] = "no_records_identity_unavailable"
    else:
        status["master"] = "selected_current_snapshot_not_historical"
    capacity_gaps = sorted(key for key, item in binding.items()
        if item["capacityStatus"] == "reference_scale_capacity_gap")
    if master_key in capacity_gaps:
        status["master"] = "unsupported_current_v2_collector"
    mapped = mapping_build(source_descriptors, pairs)
    indexed = {pair["salesKey"]: pair["pairKey"] for pair in mapped["plan"]["pairs"]}
    current_pair = indexed[source_keys["erpSales"]["current"]]
    baselines = {window: (validate_baseline_pair(source_descriptors, mapped["plan"],
        current_pair, indexed[source_keys["erpSales"][window]])
        if source_keys["erpSales"][window] is not None else None)
        for window in WINDOWS[1:]}
    value = {"schemaVersion": SCHEMA, "reportId": context["reportId"],
        "contextDigest": digest(context), "catalogDigest": digest(catalogue),
        "platform": platform, "shop": shop,
        "comparisonRule": periods["comparisonRule"], "periods": periods,
        "sourceKeys": source_keys, "sourceBindings": binding,
        "mappingPlan": mapped["plan"], "mappingPlanDigest": mapped["planDigest"],
        "currentMappingPairKey": current_pair, "baselineMappingPairs": baselines,
        "sourceStatus": status, "missingSources": missing,
        "referenceScaleCapacityGap": capacity_gaps,
        "currentV2CollectorSupported": not capacity_gaps,
        "metricPolicies": {"erpSalesIsNetSalesAuthority": True,
            "netshopSkuAndNativeSpuAreSeparate": True,
            "promotionAttributionIsNotErpSales": True,
            "productDayVisitorsAreNotShopUniqueUv": True,
            "monthlyFinanceIsNotDailyProfit": True,
            "unmatchedAndAmbiguousStayUnassigned": True},
        "identityAssignmentVerified": False, "costQualityVerified": False,
        "crossDomainSnapshotVerified": False, "numericTotalsVerified": False,
        "authorityVerified": False, "registered": False}
    value["planDigest"] = digest(value)
    return value


def check_candidate(sources, infos, context, source_keys, candidate):
    actual = prepare_candidate(sources, infos, context, source_keys)
    _need(type(candidate) is dict and canonical(candidate) == canonical(actual),
        "KPI候选跨来源、期间或缺源状态")
    return actual
