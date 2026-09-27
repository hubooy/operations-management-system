"""Explicit immutable mapping choices; no facts, authority, or runtime dispatch.

Sources are the complete verified v2 directory in Reader.sources form. Service
callers must separately bind evidence/owner/scope and each sourceRef. Rebuilding
this small plan does not prove that any sales/master facts exist or reconcile.
"""
import re

from .contracts import AnalysisContractError, canonical, digest
from .evidence_v2 import normalize_sources
from .identity_partitioned import ALGORITHM_VERSION

SCHEMA_VERSION = "business-mapping-plan-v1"
MAX_PAIRS = 47
MAX_PLAN_BYTES = 16000


def _require(condition, message):
    if not condition:
        raise AnalysisContractError(message)


def _fields(value, names):
    _require(type(value) is dict and len(value) == len(names) and set(value) == set(names),
        "关联计划字段无效或缺失")


def _identifier(value):
    _require(type(value) is str and 1 <= len(value) <= 160
        and re.fullmatch(r"[A-Za-z0-9_-]+", value) is not None, "关联来源key无效")
    return value


def _sha(value):
    _require(type(value) is str and len(value) == 64
        and re.fullmatch(r"[a-f0-9]{64}", value) is not None, "关联pairKey无效")
    return value


def _same(actual, expected):
    """Bound traversal by the rebuilt small structure, never serialize input."""
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return (len(actual) == len(expected) and set(actual) == set(expected)
            and all(_same(actual[key], value) for key, value in expected.items()))
    if type(expected) is list:
        return len(actual) == len(expected) and all(_same(a, b) for a, b in zip(actual, expected))
    if type(expected) is str:
        return len(actual) == len(expected) and actual == expected
    return actual == expected


def normalize(sources, pairs):
    """Validate exact user choices, copy, and deterministically order all pairs.

Each sales source appears once at most. Reusing one current master for several
sales windows/channels is explicit and valid; nothing is inferred or merged.
"""
    _require(type(pairs) is list and 1 <= len(pairs) <= MAX_PAIRS,
        "显式关联计划须包含1—47组来源，不得截断")
    entries = normalize_sources(sources)
    indexed = {source["key"]: source for source in entries}
    seen, result = set(), []
    for pair in pairs:
        _fields(pair, {"salesKey", "masterKey"})
        sales_key, master_key = _identifier(pair["salesKey"]), _identifier(pair["masterKey"])
        sales, master = indexed.get(sales_key), indexed.get(master_key)
        _require(sales_key not in seen, "每个销售来源只能显式指定一个主数据来源，重复不得合并")
        _require(sales is not None and master is not None and sales_key != master_key,
            "关联来源不存在或使用同一来源")
        _require(sales["domain"] == "sales" and master["domain"] == "netshop"
            and master["query"]["dataset"] == "master" and master["query"]["window"] == "current",
            "关联须为ERP销售和本期商品主数据")
        _require(all(sales["query"][key] == master["query"][key] for key in ("platform", "shop")),
            "关联两端须属于同一精确平台和店铺")
        seen.add(sales_key)
        result.append({"pairKey": digest([ALGORITHM_VERSION, sales_key, master_key]),
            "salesKey": sales_key, "masterKey": master_key})
    result.sort(key=lambda pair: (pair["salesKey"], pair["masterKey"]))
    plan = {"schemaVersion": SCHEMA_VERSION, "algorithmVersion": ALGORITHM_VERSION, "pairs": result}
    _require(len(canonical(plan).encode("utf-8")) <= MAX_PLAN_BYTES,
        "完整关联计划超过16000 UTF-8字节，不得截断")
    return plan


def build(sources, pairs):
    """Return {plan, planDigest}; digest covers the complete canonical plan."""
    plan = normalize(sources, pairs)
    return {"plan": plan, "planDigest": digest(plan)}


def validate(actual, sources, pairs):
    """Compare a claimed build result with independent trusted choices.

The digest supplied in actual is not an authority. Even a self-consistently
rehashed change fails unless the independent source choices match exactly.
"""
    expected = build(sources, pairs)
    _require(_same(actual, expected), "关联计划与可信显式选择或摘要不一致")
    return expected


def _checked_plan(plan, sources):
    _fields(plan, {"schemaVersion", "algorithmVersion", "pairs"})
    _require(type(plan["pairs"]) is list and 1 <= len(plan["pairs"]) <= MAX_PAIRS,
        "关联计划来源对数量无效")
    choices = []
    for pair in plan["pairs"]:
        _fields(pair, {"pairKey", "salesKey", "masterKey"})
        _sha(pair["pairKey"])
        choices.append({"salesKey": _identifier(pair["salesKey"]), "masterKey": _identifier(pair["masterKey"])})
    expected = normalize(sources, choices)
    _require(_same(plan, expected), "关联计划版本、排序或pairKey不一致")
    return expected


def validate_baseline_pair(sources, plan, current_pair_key, baseline_pair_key):
    """Validate comparable windows under the same *current* master identity.

This proves query compatibility only, never date coverage, historical SKU
ownership, matched coverage, or numeric comparability of any result row.
"""
    current_pair_key, baseline_pair_key = _sha(current_pair_key), _sha(baseline_pair_key)
    checked = _checked_plan(plan, sources)
    indexed_pairs = {pair["pairKey"]: pair for pair in checked["pairs"]}
    current, baseline = indexed_pairs.get(current_pair_key), indexed_pairs.get(baseline_pair_key)
    _require(current is not None and baseline is not None and current_pair_key != baseline_pair_key,
        "比较来源对不存在或重复")
    indexed = {entry["key"]: entry for entry in normalize_sources(sources)}
    a, b = indexed[current["salesKey"]]["query"], indexed[baseline["salesKey"]]["query"]
    _require(a["window"] == "current" and b["window"] in {"previous", "yearAgo"},
        "关联比较须为本期与环比或同比基期")
    _require(current["masterKey"] == baseline["masterKey"],
        "关联比较须固定同一当前主数据来源，不得混用历史归属")
    _require({key: value for key, value in a.items() if key != "window"}
        == {key: value for key, value in b.items() if key != "window"},
        "关联比较的平台、店铺、渠道或原始日期范围不一致")
    return {"currentPairKey": current_pair_key, "baselinePairKey": baseline_pair_key, "window": b["window"]}
