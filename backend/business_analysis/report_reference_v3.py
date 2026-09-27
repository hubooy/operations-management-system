"""Pure non-executable five-agent reference candidate for sealed mixed v3 evidence."""
from __future__ import annotations

from . import evidence_seal_v3, evidence_v3
from .contracts import AnalysisContractError, canonical, comparison_periods, digest

SCHEMA = "business-report-admission-candidate-v3"
PROFILE = "business-agent-reference-v3-candidate"
AGENTS = ("commerce", "promotion", "market_b2b", "independent_review", "report")
POLICY = {"financeRole": "monthly_context_only", "financeDailyProrationAllowed": False,
    "financeSkuProfitAttributionAllowed": False, "sumOverlappingErpB2bAdsAllowed": False,
    "dailyMissingRowsMeanZero": False, "monthlyMissingMeansZero": False,
    "crossDomainSnapshotAtomic": False,
    "meaning": "店铺/市场/ERP/B端与自然月财报分别解释；财报只提供月度背景，不摊销到日、SKU或关键词，也不把可能重叠的成交/归因金额相加。"}


def _need(condition, message):
    if not condition:
        raise AnalysisContractError(message)


def _sha(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def build(header, entries, seal, *, profile=PROFILE):
    """Return a bounded candidate; never authorize a run or calculate revenue."""
    _need(profile == PROFILE, "v3报告候选配置不支持旧v2或未知执行画像")
    _need(type(header) is dict and header.get("schemaVersion") == evidence_v3.HEADER_SCHEMA,
          "v3报告候选只接受v3目录")
    _need(type(entries) is list and 2 <= len(entries) <= 48 and
          type(seal) is dict and seal.get("schemaVersion") == evidence_seal_v3.SCHEMA,
          "v3报告候选只接受已核验的v3封存")
    base = {key: value for key, value in seal.items() if key != "sealedDigest"}
    _need(seal.get("sealedDigest") == digest(base) and _sha(seal.get("sealedDigest"))
          and seal.get("planDigest") == digest(header)
          and seal.get("catalogDigest") == header.get("catalogDigest")
          and seal.get("sourcesDigest") == digest(seal.get("sources"))
          and seal.get("receiptBound") is True and seal.get("ownedReplayVerified") is True
          and seal.get("sourceAuthorityVerified") is False
          and seal.get("reportGenerationSupported") is False,
          "v3报告候选封存摘要或证明边界无效")
    _need(type(seal.get("sources")) is list and len(seal["sources"]) == len(entries)
          and len(entries) == header.get("sourceCount"), "v3报告候选目录来源数变化")
    daily, monthly = [], []
    for entry, proof in zip(entries, seal["sources"]):
        _need(type(entry) is dict and type(proof) is dict
              and (proof.get("sourceKey"), proof.get("ordinal"), proof.get("domain"),
                   proof.get("queryDigest")) == (entry.get("key"), entry.get("ordinal"),
                   entry.get("domain"), entry.get("queryDigest")),
              "v3报告候选来源身份或顺序不一致")
        query, coverage = entry["query"], proof["coverage"]
        common = {"sourceKey": proof["sourceKey"], "domain": proof["domain"],
            "queryDigest": proof["queryDigest"], "sourceRef": proof["sourceRef"],
            "sourceRevision": proof["sourceRevision"],
            "pageCount": proof["pageCount"], "rowCount": proof["rowCount"],
            "receiptChainDigest": proof["receiptChainDigest"]}
        if proof["domain"] == "finance":
            months = query["months"]
            _need(coverage.get("kind") == "natural_month_publication"
                  and coverage.get("months") == months
                  and type(coverage.get("publishedBits")) is str
                  and len(coverage["publishedBits"]) == len(months)
                  and set(coverage["publishedBits"]) <= {"0", "1"}
                  and coverage.get("missingMonths") == [month for month, bit in
                      zip(months, coverage["publishedBits"]) if bit == "0"]
                  and _sha(coverage.get("publicationDigest"))
                  and _sha(coverage.get("detailedCoverageDigest")),
                  "v3报告候选财报自然月覆盖无效")
            monthly.append({**common, "role": "monthly_context", "scope": query["scope"],
                "analysisPeriod": query["analysisPeriod"], "months": months,
                "publishedBits": coverage["publishedBits"],
                "missingMonths": coverage["missingMonths"],
                "lastPublishedMonth": next((month for month, bit in
                    reversed(list(zip(months, coverage["publishedBits"]))) if bit == "1"), None),
                "publicationDigest": coverage["publicationDigest"],
                "detailedCoverageDigest": coverage["detailedCoverageDigest"]})
        else:
            period = comparison_periods(query["startDate"], query["endDate"])[query["window"]]
            kind = "current_master_snapshot" if proof["domain"] == "netshop" and query["dataset"] == "master" else "observed_row_dates"
            _need(coverage.get("kind") == kind and coverage.get("startDate") == period["startDate"]
                  and coverage.get("endDate") == period["endDate"],
                  "v3报告候选日来源截止区间变化")
            if kind == "observed_row_dates":
                bits = coverage.get("presentBits")
                _need(type(bits) is str and len(bits) == period["days"]
                      and set(bits) <= {"0", "1"}
                      and coverage.get("missingRowDateCount") == bits.count("0"),
                      "v3报告候选缺行日位图无效")
            else:
                _need(coverage.get("presentBits") is None
                      and coverage.get("missingRowDateCount") is None,
                      "当前主数据不能声称逐日覆盖")
            daily.append({**common, "role": "daily_fact_or_market_observation",
                "query": query, "period": period,
                "dateCoverage": coverage})
    _need(daily and monthly, "v3报告候选必须分别包含日来源与月财报背景")
    reference = {"schemaVersion": "business-report-reference-v3-candidate",
        "evidenceRunId": seal["runId"], "evidenceVersion": seal["evidenceVersion"],
        "evidencePlanDigest": seal["planDigest"], "catalogDigest": seal["catalogDigest"],
        "sealedDigest": seal["sealedDigest"], "sourcesDigest": seal["sourcesDigest"]}
    candidate = {"schemaVersion": SCHEMA, "executionProfile": PROFILE,
        "state": "unregistered_candidate_only", "reference": reference,
        "question": header["analysisRequest"]["question"],
        "requestedDimensions": header["analysisRequest"]["requestedDimensions"],
        "requestedWindows": header["analysisRequest"]["requestedWindows"],
        "dailyFacts": daily, "financeMonthlyContext": monthly,
        "fiveAgentRoles": list(AGENTS), "policy": dict(POLICY),
        "modelDispatchSupported": False, "reportGenerationSupported": False,
        "fileGenerationSupported": False}
    result = {**candidate, "candidateDigest": digest(candidate)}
    _need(len(canonical(result).encode("utf-8")) <= 64 * 1024,
          "v3报告候选超出内部容量")
    return result
