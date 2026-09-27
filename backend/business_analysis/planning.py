"""Pure source planning; a supported query is not evidence of available data.

Owning-reader support may be injected without importing Django. market_validator
receives a copied normalized query and returns None on acceptance, or a reason
string / raises AnalysisContractError when that combination is unsupported.
"""
from copy import deepcopy
import unicodedata

from .contracts import AnalysisContractError, canonical, comparison_periods, digest


NETSHOP_SOURCES = {
    "promotion": {"京东", "天猫"}, "sku": {"京东"},
    "spu": {"京东", "天猫"}, "b2b": {"京东"}, "master": {"京东", "天猫"},
}
WINDOWS = ("current", "previous", "yearAgo")
DIMENSIONS = ("shop", "category", "spu", "sku", "keyword")
EVIDENCE_ID_PLACEHOLDER = "evidence-" + "0" * 36


def _fields(value, required, name):
    if not isinstance(value, dict) or set(value) != set(required):
        raise AnalysisContractError(f"{name}字段集合无效")


def _text(value, name, maximum, *, exact=True):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise AnalysisContractError(f"{name}须为1—{maximum}字符")
    if exact and value != value.strip():
        raise AnalysisContractError(f"{name}须使用精确身份，不接受首尾空白")
    if any(unicodedata.category(c) in {"Cc", "Cs"} and (exact or c not in "\n\r\t") for c in value):
        raise AnalysisContractError(f"{name}含无效控制字符")
    return value


def _list(value, name, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise AnalysisContractError(f"{name}最多{maximum}项")
    return value


def _unique(items, name):
    identities = [canonical(item) for item in items]
    if len(set(identities)) != len(identities):
        raise AnalysisContractError(f"{name}重复，不得静默合并")


def _question(value):
    # Same normalization as ai_assistant.policy.text, without a Django import.
    if not isinstance(value, str):
        raise AnalysisContractError("问题必须是字符串")
    result = value.replace("\r\n", "\n").replace("\r", "\n").replace("\0", "").strip()
    try:
        valid = bool(result) and len(result) <= 1000 and len(result.encode("utf-8")) <= 4000
    except UnicodeError as error:
        raise AnalysisContractError("问题包含无效Unicode字符") from error
    if not valid:
        raise AnalysisContractError("问题须为1—1000字符且不超过4000 UTF-8字节")
    return result


def validate_analysis_request(value):
    """Validate optional immutable evidence metadata, including on API creation."""
    _fields(value, {"schemaVersion", "question", "requestedDimensions", "requestedWindows"}, "分析问题上下文")
    if value["schemaVersion"] != "business-analysis-request-v1":
        raise AnalysisContractError("分析问题上下文版本无效")
    question = _question(value["question"])
    dimensions = _list(value["requestedDimensions"], "请求维度", len(DIMENSIONS))
    if not dimensions or any(not isinstance(d, str) or d not in DIMENSIONS for d in dimensions):
        raise AnalysisContractError("请求维度无效")
    _unique(dimensions, "请求维度")
    windows = _list(value["requestedWindows"], "比较窗口", len(WINDOWS))
    if "current" not in windows or any(not isinstance(w, str) or w not in WINDOWS for w in windows):
        raise AnalysisContractError("比较窗口须包含current，只允许current/previous/yearAgo")
    _unique(windows, "比较窗口")
    return {"schemaVersion": value["schemaVersion"], "question": question,
            "requestedDimensions": list(dimensions), "requestedWindows": list(windows)}


def workflow_input_bytes(question, sources, *, evidence_run_id=EVIDENCE_ID_PLACEHOLDER):
    """Measure current source-bearing DAG input; does not validate source queries."""
    question = _question(question)
    _text(evidence_run_id, "证据ID", 160)
    if not isinstance(sources, list):
        raise AnalysisContractError("来源须为数组")
    try:
        return len(canonical({"evidenceRunId": evidence_run_id, "question": question, "sources": sources}).encode("utf-8"))
    except (ValueError, TypeError, UnicodeError) as error:
        raise AnalysisContractError("DAG输入无法规范序列化") from error


def _normalize(body):
    _fields(body, {"question", "startDate", "endDate", "shops", "windows", "markets"}, "分析请求")
    question = _question(body["question"])
    comparison_periods(body["startDate"], body["endDate"])
    windows = _list(body["windows"], "比较窗口", 3)
    if any(not isinstance(w, str) or w not in WINDOWS for w in windows) or "current" not in windows:
        raise AnalysisContractError("比较窗口须包含current，只允许current/previous/yearAgo")
    _unique(windows, "比较窗口")
    shops = []
    for shop in _list(body["shops"], "店铺", 4):
        _fields(shop, {"platform", "shop", "datasets", "salesChannels"}, "店铺")
        platform = _text(shop["platform"], "平台", 100)
        name = _text(shop["shop"], "店铺", 100)
        datasets = _list(shop["datasets"], "数据集", 5)
        if any(not isinstance(d, str) or d not in NETSHOP_SOURCES for d in datasets):
            raise AnalysisContractError("网店数据集无效")
        channels = [_text(c, "ERP渠道", 100) for c in _list(shop["salesChannels"], "ERP渠道", 10)]
        _unique(datasets, "数据集")
        _unique(channels, "ERP渠道")
        shops.append({"platform": platform, "shop": name, "datasets": sorted(datasets), "salesChannels": sorted(channels)})
    _unique([{k: s[k] for k in ("platform", "shop")} for s in shops], "店铺身份")
    markets = []
    market_fields = {"platform", "category", "scope", "rankingDimension", "priceBandFilter"}
    for market in _list(body["markets"], "市场条件", 7):
        _fields(market, market_fields, "市场条件")
        markets.append({key: _text(market[key], key, 100 if key == "platform" else 200) for key in sorted(market_fields)})
    _unique(markets, "市场条件")
    return {"question": question, "startDate": body["startDate"], "endDate": body["endDate"],
            "shops": sorted(shops, key=canonical), "windows": [w for w in WINDOWS if w in windows],
            "markets": sorted(markets, key=canonical)}


def preview(body, *, max_sources=12, max_plan_bytes=16000, max_workflow_bytes=8000,
            netshop_sources=None, market_validator=None):
    """Return a complete proposal; never trim unsupported or over-capacity scope.

planBytes measures the exact current evidence plan including collection and
analysis metadata. workflowBytes conservatively measures the current DAG input
    using a 45-character evidence-UUID (the current generated ID shape), without a
budget plan. Backend creation must still validate its final serialized input.
"""
    for value in (max_sources, max_plan_bytes, max_workflow_bytes):
        if type(value) is not int or value < 1:
            raise AnalysisContractError("计划容量参数须为正整数")
    request = _normalize(body)
    support = NETSHOP_SOURCES if netshop_sources is None else netshop_sources
    dates = {key: request[key] for key in ("startDate", "endDate")}
    sources, coverage = [], []
    limitations = [
        "计划只核验查询组合；所有来源尚未采集，不能据此认定存在业务记录或日期覆盖完整。",
        "行数、证据字节和身份映射容量尚未核验；采集仍受实际存储、分页及映射上限约束。",
        "证据按各来源版本核验，不是跨领域同一时刻的数据库快照。",
        "请求维度不保证来源具有对应字段；缺字段、缺日期和口径差异须在采集后核验。",
        "workflowBytes按当前45字符evidence-UUID及无预算DAG输入测算，后端创建时仍须最终核验。",
    ]

    def add(domain, query, reason=None):
        source = {"key": "source-" + digest({"domain": domain, "query": query}), "domain": domain, "query": query}
        entry = {"domain": domain, "query": deepcopy(query), "status": "unsupported" if reason else "planned",
                 "availability": "not_collected", "reason": reason or "查询组合已列入计划，数据可用性待采集核验"}
        if reason is None:
            sources.append(source)
            entry["sourceKey"] = source["key"]
        coverage.append(entry)

    for shop in request["shops"]:
        identity = {k: shop[k] for k in ("platform", "shop")}
        for dataset in shop["datasets"]:
            for window in (request["windows"] if dataset != "master" else ["current"]):
                query = {**identity, **dates, "dataset": dataset, "window": window}
                reason = None if shop["platform"] in support.get(dataset, ()) else f"{shop['platform']}不支持{dataset}规范来源，不能以其他数据集替代"
                add("netshop", query, reason)
        for channel in shop["salesChannels"]:
            for window in request["windows"]:
                add("sales", {**identity, **dates, "channel": channel, "window": window})
    if any("master" in s["datasets"] for s in request["shops"]):
        limitations.append("主数据只采集本期最新完成快照，不生成同比或环比主数据，也不证明历史商品映射。")
    for market in request["markets"]:
        for window in request["windows"]:
            query = {**market, **dates, "window": window}
            reason = None
            if market["platform"] != "京东" or market["rankingDimension"] not in ("SKU", "SPU"):
                reason = "市场规范来源仅支持京东SKU/SPU榜单"
            elif market_validator:
                try:
                    reason = market_validator(deepcopy(query))
                except AnalysisContractError as error:
                    reason = str(error)
                if reason is not None and (not isinstance(reason, str) or not reason):
                    raise AnalysisContractError("市场校验器须返回None或非空拒绝原因")
            add("market", query, reason)
    if request["markets"]:
        limitations.append("市场来源是精确榜单条件下的逐日TOP样本与金额区间，不能据此声称全市场规模或店铺市场份额。")
    sources.sort(key=lambda s: canonical({"domain": s["domain"], "query": s["query"]}))
    coverage.sort(key=lambda s: canonical({"domain": s["domain"], "query": s["query"]}))
    analysis = {"schemaVersion": "business-analysis-request-v1", "question": request["question"],
                "requestedDimensions": list(DIMENSIONS), "requestedWindows": request["windows"]}
    evidence_request = {"sources": sources, "collectionMode": "bulk", "autoCollect": True, "analysisRequest": analysis}
    plan = {"schemaVersion": "business-evidence-v1", "sources": sources, "autoCollect": True,
            "collector": {"version": 1, "surface": "business_collection", "pageSize": 100}, "analysisRequest": analysis}
    capacity = {"sourceCount": len(sources), "maxSources": max_sources,
                "planBytes": len(canonical(plan).encode("utf-8")), "maxPlanBytes": max_plan_bytes,
                "workflowBytes": workflow_input_bytes(request["question"], sources), "maxWorkflowBytes": max_workflow_bytes}
    blockers = []
    if any(c["status"] == "unsupported" for c in coverage):
        blockers.append("包含不支持的请求能力；未授权缩小范围，禁止仅采集剩余来源。")
    if not sources:
        blockers.append("没有可采集来源，请指定店铺数据集、ERP渠道或市场条件。")
    for measured, bound, label in (("sourceCount", "maxSources", "来源数量"), ("planBytes", "maxPlanBytes", "证据计划UTF-8字节"), ("workflowBytes", "maxWorkflowBytes", "DAG输入估算UTF-8字节")):
        if capacity[measured] > capacity[bound]:
            blockers.append(f"{label}{capacity[measured]}超过上限{capacity[bound]}；完整请求保留，不自动缩小或拆分后冒充综合报告。")
    return {"schemaVersion": "business-plan-preview-v1", "request": request, "planDigest": digest(plan),
            "sources": sources, "coverage": coverage, "limitations": limitations + blockers,
            "canCollect": not blockers, "capacity": capacity, "evidenceRequest": evidence_request}
