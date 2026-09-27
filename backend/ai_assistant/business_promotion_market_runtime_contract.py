"""Unregistered interval-compatible market supplement for a future promotion profile.

This pure candidate binds source identities and declared coverage receipts. It
cannot authenticate their origin; an owning sealed-page reader must rebuild
and compare those receipts before any Agent or file publication.
"""
import re

from business_analysis.contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, coverage, digest
from business_analysis.evidence_v2 import normalize_sources
from business_analysis.market_dynamics_v2 import ALGORITHM_VERSION as RANK_ALGORITHM, observation_dates
from business_analysis.market_dynamics import ALGORITHM_VERSION as BAND_ALGORITHM
from business_analysis.promotion_views import _copy


SCHEMA = "business-promotion-market-selection-candidate-v1"
PROFILE = "business-agent-screening-promotion-market-reference-v2"
SELECTOR_FIELDS = {"priceBandSourceKey", "rankCurrentSourceKey", "rankBaselineKey",
    "bands", "currentObservationDate", "baselineObservationDate"}
CONTEXT_FIELDS = {"reportId", "runId", "screeningId", "sealedDigest"}
PROOF_FIELDS = {"sourceKey", "sourceRef", "evidenceDigest", "rowCount", "reconciled", "coverage"}
LIMITATIONS = ("区间价格带与商品日成员是同一TOP样本，不可相加。",
    "进出榜仅比较明确两个观察日；日期缺失不等于未入榜，未入榜不等于零销量。",
    "市场SKU/SPU是榜单身份，不证明本店、ERP或B端销售归属。")
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _need(ok, message="市场补充选择合同无效"):
    if not ok:
        raise AnalysisContractError(message)


def _id(value):
    _need(type(value) is str and _ID.fullmatch(value) is not None, "市场来源或报告身份无效")
    return value


def _sha(value):
    _need(type(value) is str and _SHA.fullmatch(value) is not None, "市场来源摘要无效")
    return value


def _bands(value):
    value = _copy(value, 8192)
    _need(type(value) is list and 1 <= len(value) <= 20, "价格带数量无效")
    previous, seen = 0, set()
    for index, band in enumerate(value):
        _need(type(band) is dict and set(band) == {"key", "lowerCents", "upperExclusiveCents"},
            "价格带字段无效")
        key, lower, upper = band["key"], band["lowerCents"], band["upperExclusiveCents"]
        _need(type(key) is str and 0 < len(key) <= 100 and key not in seen
            and not key.startswith("unallocated_"), "价格带名称无效")
        _need(type(lower) is int and 0 <= lower <= MAX_SAFE_INTEGER
            and previous is not None and lower >= previous, "价格带重叠或下界无效")
        if upper is None:
            _need(index == len(value)-1, "无上界价格带必须位于末尾")
        else:
            _need(type(upper) is int and lower < upper <= MAX_SAFE_INTEGER,
                "价格带上界无效")
        previous = upper
        seen.add(key)
    return value


def _proof(proof, source, window):
    _need(type(proof) is dict and set(proof) == PROOF_FIELDS
        and proof["sourceKey"] == source["key"] and proof["reconciled"] is True,
        "市场来源覆盖回执缺失或跨来源")
    _sha(proof["sourceRef"]); _sha(proof["evidenceDigest"])
    _need(type(proof["rowCount"]) is int and 0 <= proof["rowCount"] <= 200_000,
        "市场覆盖回执行数无效")
    supplied = proof["coverage"]
    _need(type(supplied) is dict and type(supplied.get("presentDates")) is list
        and len(supplied["presentDates"]) <= 93
        and all(type(day) is str for day in supplied["presentDates"])
        and len(set(supplied["presentDates"])) == len(supplied["presentDates"]),
        "市场覆盖日期清单无效")
    _need(canonical(supplied) == canonical(coverage(window, supplied["presentDates"])),
        "市场覆盖状态与声明日期不一致")
    _need(proof["rowCount"] >= len(supplied["presentDates"]),
        "市场观察日数量超过封存行数")
    return proof


def prepare_candidate(sources, context, selector, coverage_proofs):
    """Bind interval price bands and two corresponding days without authority."""
    context = _copy(context, 2048)
    selector = _copy(selector, 12288)
    coverage_proofs = _copy(coverage_proofs, 8192)
    _need(type(context) is dict and set(context) == CONTEXT_FIELDS)
    for key in CONTEXT_FIELDS - {"sealedDigest"}: _id(context[key])
    _sha(context["sealedDigest"])
    _need(type(selector) is dict and set(selector) == SELECTOR_FIELDS)
    for key in ("priceBandSourceKey", "rankCurrentSourceKey", "rankBaselineKey"):
        _id(selector[key])
    _need(selector["priceBandSourceKey"] == selector["rankCurrentSourceKey"]
        and selector["rankCurrentSourceKey"] != selector["rankBaselineKey"],
        "区间价格带须复用本期市场来源，基期须是另一来源")
    bands = _bands(selector["bands"])
    catalogue = normalize_sources(_copy(sources, 128*1024))
    by_key = {item["key"]: item for item in catalogue}
    _need(selector["rankCurrentSourceKey"] in by_key
        and selector["rankBaselineKey"] in by_key, "市场来源不在同一封存目录")
    current, baseline = (by_key[selector[key]] for key in
        ("rankCurrentSourceKey", "rankBaselineKey"))
    a, b = current["query"], baseline["query"]
    _need(current["domain"] == baseline["domain"] == "market"
        and a["platform"] == b["platform"] == "京东"
        and a["window"] == "current" and b["window"] in ("previous", "yearAgo")
        and {key: value for key, value in a.items() if key != "window"}
            == {key: value for key, value in b.items() if key != "window"},
        "市场类别、经营范围、榜单粒度或原日期不一致")
    periods = observation_dates(a, selector["currentObservationDate"],
        selector["baselineObservationDate"], b["window"])
    _need(type(coverage_proofs) is dict and set(coverage_proofs) ==
        {current["key"], baseline["key"]}, "必须给同报告本期和基期覆盖回执")
    first = _proof(coverage_proofs[current["key"]], current, periods["current"])
    second = _proof(coverage_proofs[baseline["key"]], baseline, periods[b["window"]])
    observed = {"currentDatePresent": selector["currentObservationDate"] in first["coverage"]["presentDates"],
        "baselineDatePresent": selector["baselineObservationDate"] in second["coverage"]["presentDates"]}
    observed["bothDatesPresent"] = all(observed.values())
    value = {"schemaVersion": SCHEMA, "executionProfile": PROFILE,
        "contextDigest": digest(context), "catalogDigest": digest(catalogue),
        "selector": {**selector, "bands": bands}, "sources": {"current": current, "baseline": baseline},
        "coverageProofDigests": {key: digest(coverage_proofs[key]) for key in sorted(coverage_proofs)},
        "observationCoverage": observed,
        "algorithms": {"priceBand": BAND_ALGORITHM, "rankEntryExit": RANK_ALGORITHM},
        "limitations": list(LIMITATIONS), "authorityVerified": False,
        "sourceCoverageVerified": False, "registered": False}
    value["candidateDigest"] = digest(value)
    return value


def check_candidate(sources, context, selector, coverage_proofs, candidate):
    expected = prepare_candidate(sources, context, selector, coverage_proofs)
    _need(type(candidate) is dict and canonical(candidate) == canonical(expected),
        "市场候选跨报告、来源、覆盖或观察日期")
    return expected
