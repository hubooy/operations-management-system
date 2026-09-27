"""Versioned pure market-selector candidate for a future formal report profile.

It binds explicit directory choices and price bands, not source authorization,
observed rows, own-product mapping, or permission to render files.
"""
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, comparison_periods, digest
from .evidence_v2 import _fields, _identifier, _sha, _text, normalize_sources
from .market_dynamics import ALGORITHM_VERSION
from .promotion_views import _copy


SCHEMA_VERSION = "business-market-selector-candidate-v1"
MAX_SELECTOR_BYTES = 16_000
MAX_BANDS_BYTES = 8192
CONTEXT_FIELDS = frozenset(("reportId", "runId", "screeningId", "sealedDigest"))
PRICE_BAND_VIEW = "price_band"
RANK_VIEW = "rank_entry_exit"
LIMITATIONS = ("仅固定京东逐日TOP榜单样本，不证明全行业规模或真实份额。",
    "缺席只表示未在所选TOP样本观察到，不代表零销量或退市。",
    "市场榜单没有本店归属；不得推定自家SKU/SPU、销售或利润。",
    "价格区间不取中点，区间缺失、跨段及未覆盖价格保持未分配。")


def _need(condition, message="市场选择合同无效"):
    if not condition: raise AnalysisContractError(message)


def _bands(value):
    value = _copy(value, MAX_BANDS_BYTES)
    _need(type(value) is list and 1 <= len(value) <= 20, "价格段须为1—20个显式有限区间")
    seen, prior, result = set(), 0, []
    for band in value:
        _fields(band, {"key", "lowerCents", "upperExclusiveCents"}, "价格段")
        key = _text(band["key"], 100, "价格段key")
        lower, upper = band["lowerCents"], band["upperExclusiveCents"]
        _need(not key.startswith("unallocated_") and key not in seen,
            "价格段key重复或占用系统未分配桶")
        _need(type(lower) is int and type(upper) is int and 0 <= lower < upper <= MAX_SAFE_INTEGER,
            "价格段上下界须为有限的无损整数")
        _need(lower >= prior, "价格段须按下界排序且不得重叠")
        seen.add(key); prior = upper
        result.append({"key": key, "lowerCents": lower, "upperExclusiveCents": upper})
    _need(len(canonical(result).encode("utf-8")) <= MAX_BANDS_BYTES, "完整价格段超过字节容量")
    return result


def freeze(sources, context, choice):
    """Freeze explicit market choices against a complete supplied v2 directory."""
    sources = _copy(sources, 128 * 1024)
    context, choice = _copy(context, 2048), _copy(choice, MAX_SELECTOR_BYTES)
    _fields(context, CONTEXT_FIELDS, "市场报告上下文")
    for key in CONTEXT_FIELDS - {"sealedDigest"}: _identifier(context[key], key)
    _sha(context["sealedDigest"], "sealedDigest")
    _need(type(choice) is dict and set(choice) in ({"sourceKey", "bands"},
        {"sourceKey", "baselineKey", "bands"}), "市场选择字段集合无效")
    source_key = _identifier(choice["sourceKey"], "sourceKey")
    baseline_key = _identifier(choice["baselineKey"], "baselineKey") if "baselineKey" in choice else None
    bands = _bands(choice["bands"])
    entries = normalize_sources(sources)
    indexed = {entry["key"]: entry for entry in entries}
    current = indexed.get(source_key)
    _need(current is not None and current["domain"] == "market"
        and current["query"]["platform"] == "京东"
        and current["query"]["window"] == "current", "本期须为目录中的明确京东市场来源")
    baseline = indexed.get(baseline_key) if baseline_key is not None else None
    if baseline_key is not None:
        _need(baseline is not None and baseline["domain"] == "market"
            and baseline["query"]["platform"] == "京东"
            and baseline_key != source_key and baseline["query"]["window"] in {"previous", "yearAgo"},
            "基期须为目录中的明确京东比较窗口")
        a, b = current["query"], baseline["query"]
        _need({key:value for key,value in a.items() if key != "window"}
            == {key:value for key,value in b.items() if key != "window"},
            "市场基期的类目、榜单粒度、范围、价格筛选或原始日期不对应")
        _need(a["startDate"] == a["endDate"],
            "进出榜只支持明确单日对单日，不能把多日排名混作一期")
    views = [PRICE_BAND_VIEW, RANK_VIEW] if baseline is not None else [PRICE_BAND_VIEW]
    selector = {"sourceKey": source_key, **({"baselineKey": baseline_key} if baseline is not None else {}),
        "bands": bands, "views": views}
    result = {"schemaVersion": SCHEMA_VERSION, "algorithmVersion": ALGORITHM_VERSION,
        "context": context, "catalogDigest": digest(entries), "marketSelector": selector,
        "source": current, "baseline": baseline,
        "periods": comparison_periods(current["query"]["startDate"], current["query"]["endDate"]),
        "limitations": list(LIMITATIONS), "authorityVerified": False, "registered": False,
        "ownProductIdentityVerified": False, "wholeMarketCoverageVerified": False}
    result["selectorDigest"] = digest(result)
    _need(len(canonical(result).encode("utf-8")) <= MAX_SELECTOR_BYTES,
        "完整市场选择与上下文超过字节容量")
    return result


def validate(sources, context, choice, claimed):
    """Rebuild from independent source roots and the caller's trusted choice."""
    claimed = _copy(claimed, MAX_SELECTOR_BYTES)
    _need(type(claimed) is dict and type(claimed.get("marketSelector")) is dict,
        "市场选择快照结构无效")
    expected = freeze(sources, context, choice)
    _need(canonical(claimed) == canonical(expected),
        "市场选择、算法、视图、目录或摘要与可信显式来源不一致")
    return expected
