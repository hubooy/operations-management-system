"""Pure five-Agent market-v2 runtime proposal over one owning admission DTO.

This does not register a fifth tool, persist an Agent read, grant model calls,
or authorize a report. Runtime/dispatch owners must recheck every root later.
"""
from copy import deepcopy
import re

from business_analysis.contracts import AnalysisContractError, canonical, digest
from business_analysis import market_numeric_claims
from business_analysis.promotion_views import _copy
from . import business_promotion_market_runtime_contract as market
from . import business_promotion_runtime_contract as previous


SCHEMA = "business-promotion-market-five-agent-runtime-candidate-v2"
ADMISSION_SCHEMA = "business-promotion-market-report-admission-candidate-v1"
PROFILE = market.PROFILE
MARKET_TOOL = "get_business_promotion_market_v2"
TOOL_ORDER = (*previous.TOOL_ORDER, MARKET_TOOL)
ROLES = ("commerce", "promotion", "market_b2b", "independent_review", "report")
MARKET_ROLES = ("market_b2b", "independent_review", "report")
TOOL_VIEWS = ("price_band", "rank_entry_exit")
MATERIAL_TABLES = ("price_band_summary", "price_band_members", "rank_entry_exit")
PAGE_SIZE = 20
MAX_ROWS = 200_000
MAX_BYTES = 128 * 1024
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PREVIOUS_GRAPH_DIGEST = {False:
    "eb3f12e66e532b3ed40aad19bc20da700cdf450f6db6efcd2c6a37b30882de5b",
    True:"7973cd77c1c144158bfd212b7743d9e94c9b4a1345e3507218d96dc5ced3f42b"}


def _need(ok, message="市场v2五角色运行合同无效"):
    if not ok:
        raise AnalysisContractError(message)


def _id(value):
    _need(type(value) is str and _ID.fullmatch(value) is not None)
    return value


def _sha(value):
    _need(type(value) is str and _SHA.fullmatch(value) is not None)
    return value


def _admission(raw):
    value = _copy(raw, 64*1024)
    _need(type(value) is dict and value.get("schemaVersion") == ADMISSION_SCHEMA
        and type(value.get("binding")) is dict
        and type(value.get("candidate")) is dict
        and value.get("bindingDigest") == digest({key:item for key,item in value.items()
            if key != "bindingDigest"})
        and value.get("candidateEligible") is True
        and value.get("observationStatus") ==
            {"current":"observed_date","baseline":"observed_date"}
        and value.get("sourceCoverageVerified") is False
        and value.get("marketRowsReplayed") is False
        and value.get("authorityVerified") is False
        and value.get("marketAndOwnSalesAdditive") is False
        and value.get("ownProductIdentityVerified") is False,
        "市场准入DTO不是两个观察日齐全的同报告候选")
    binding, candidate = value["binding"], value["candidate"]
    _need(binding.get("schemaVersion") == ADMISSION_SCHEMA
        and candidate.get("schemaVersion") == market.SCHEMA
        and candidate.get("executionProfile") == PROFILE
        and candidate.get("candidateDigest") == digest({key:item
            for key,item in candidate.items() if key != "candidateDigest"})
        and candidate.get("observationCoverage") ==
            {"currentDatePresent":True,"baselineDatePresent":True,
             "bothDatesPresent":True}
        and candidate.get("authorityVerified") is False
        and candidate.get("sourceCoverageVerified") is False
        and candidate.get("registered") is False
        and binding.get("marketCandidateDigest") == candidate["candidateDigest"]
        and binding.get("selectedProofDigests") ==
            candidate.get("coverageProofDigests")
        and value.get("reportProfileRegistered") is False
        and type(binding.get("actorVersion")) is int
        and binding["actorVersion"] >= 1)
    _id(binding["reportId"])
    _sha(binding["reportBindingDigest"])
    _sha(binding["sealedDigest"])
    _sha(binding["sourcesDigest"])
    _sha(binding["sourceInfosDigest"])
    _sha(candidate["contextDigest"])
    selector = candidate["selector"]
    _need(type(selector) is dict and set(selector) == market.SELECTOR_FIELDS
        and selector["priceBandSourceKey"] == selector["rankCurrentSourceKey"]
        and selector["rankCurrentSourceKey"] != selector["rankBaselineKey"]
        and type(candidate.get("sources")) is dict
        and candidate["sources"].get("current",{}).get("key") ==
            selector["rankCurrentSourceKey"]
        and candidate["sources"].get("baseline",{}).get("key") ==
            selector["rankBaselineKey"]
        and candidate.get("algorithms") == {"priceBand":market.BAND_ALGORITHM,
            "rankEntryExit":market.RANK_ALGORITHM}
        and type(binding.get("selectedProofDigests")) is dict
        and set(binding["selectedProofDigests"]) ==
            {selector["rankCurrentSourceKey"],selector["rankBaselineKey"]})
    for value_digest in binding["selectedProofDigests"].values():
        _sha(value_digest)
    return value


def _policy():
    return {role: {"marketToolAllowed":role in MARKET_ROLES,
        "summaryRequired":role in MARKET_ROLES,
        "firstPageForEachNonemptyViewRequired":role == "market_b2b",
        "numericCitationRequiresOwnPersistedRead":role in MARKET_ROLES,
        "canClaimAllMarketRowsPersonallyRead":False,
        "mayAttributeTopSampleToOwnSales":False}
        for role in ROLES}


def graph(with_budget=False):
    _need(type(with_budget) is bool)
    value = previous.graph(with_budget)
    _need(digest(value) == _PREVIOUS_GRAPH_DIGEST[with_budget],
        "词货v1固定工作流图已变化，须显式重审市场v2")
    agent_roles = []
    for node in value["nodes"]:
        if node["type"] != "agent":
            node["instruction"] += "市场TOP样本不代表本店销售；缺观察日不能当作零销量。"
            continue
        role = node["key"]
        agent_roles.append(role)
        if role == "market_b2b":
            node["instruction"] += ("市场v2本人必须先用"+MARKET_TOOL+
                "的summary读取固定价格带与两日榜单的完整来源覆盖/三表摘要；对每个非空"
                "price_band、rank_entry_exit视图至少读取offset=0的本人页。"
                "摘要由服务端完整重放，不代表本人读完全部市场明细。市场数值只引用本人"
                "持久工具回执实际读到的页或精确行，不得借其他角色读取证明。"
                "缺日期、未入TOP和零销量三者不同；市场样本不能归属本店、ERP或B端销售。")
        elif role in ("independent_review", "report"):
            node["instruction"] += ("市场v2本人须先用"+MARKET_TOOL+
                "的summary核对市场样本边界；若引用市场数值，须本人已持久读取对应页/行。"
                "不得借market_b2b回执或将市场TOP样本金额加到本店销售。")
        else:
            node["instruction"] += "本角色不得调用市场v2工具或提出市场数值引用。"
    _need(tuple(agent_roles) == ROLES and len(value["nodes"]) == 6)
    return value


def prepare(admission, *, with_budget=False):
    fixed = _admission(admission)
    value = {"schemaVersion":SCHEMA,"executionProfile":PROFILE,
        "withBudget":with_budget,
        "reportId":fixed["binding"]["reportId"],
        "admissionDigest":fixed["bindingDigest"],
        "marketContextDigest":fixed["candidate"]["contextDigest"],
        "marketSelector":deepcopy(fixed["candidate"]["selector"]),
        "allowedTools":list(TOOL_ORDER),"toolModes":["summary","page","row"],
        "toolViews":list(TOOL_VIEWS),"materialTables":list(MATERIAL_TABLES),
        "roleReadPolicy":_policy(),"graph":graph(with_budget),
        "marketRowsReadByAgent":False,"agentReadPersisted":False,
        "sourceCoverageVerified":False,"humanReviewRequired":True,
        "marketAndOwnSalesAdditive":False,
        "authorityVerified":False,"registered":False,
        "limitations":["市场summary由未来拥有方完整重放；Agent本人只证明所读摘要及页/行。",
            "价格带汇总/成员是同一TOP样本，不能相加；市场金额不归本店/ERP/B端。",
            "缺观察日、未入TOP与零值不同；两日观察须固定在各自比较窗口。",
            "本合同没有真实Agent派发、持久结果、人审或renderer授权。"]}
    value["graphDigest"] = digest(value["graph"])
    value["runtimeDigest"] = digest(value)
    _need(len(canonical(value).encode("utf-8")) <= MAX_BYTES,
        "市场v2五角色运行候选超过容量")
    return value


def _runtime(value):
    value = _copy(value,MAX_BYTES)
    _need(type(value) is dict and value.get("schemaVersion") == SCHEMA
        and value.get("executionProfile") == PROFILE
        and type(value.get("withBudget")) is bool
        and value.get("allowedTools") == list(TOOL_ORDER)
        and value.get("toolModes") == ["summary","page","row"]
        and value.get("toolViews") == list(TOOL_VIEWS)
        and value.get("materialTables") == list(MATERIAL_TABLES)
        and value.get("roleReadPolicy") == _policy()
        and value.get("graph") == graph(value["withBudget"])
        and value.get("graphDigest") == digest(value["graph"])
        and value.get("authorityVerified") is False
        and value.get("registered") is False
        and value.get("runtimeDigest") == digest({key:item for key,item
            in value.items() if key != "runtimeDigest"}))
    return value


def arguments(runtime, role, raw):
    """Pure exact tool-call shape; actual job/dispatch must authorize later."""
    runtime = _runtime(runtime)
    value = _copy(raw,4096)
    _need(role in MARKET_ROLES and type(value) is dict
        and value.get("reportId") == runtime["reportId"]
        and value.get("marketContextDigest") == runtime["marketContextDigest"])
    mode = value.get("mode")
    base = {"reportId","marketContextDigest","mode"}
    if mode == "summary":
        _need(set(value) == base)
    elif mode == "page":
        _need(set(value) == base | {"view","offset","limit"}
            and value["view"] in TOOL_VIEWS
            and type(value["offset"]) is int and 0 <= value["offset"] <= MAX_ROWS
            and type(value["limit"]) is int and value["limit"] == PAGE_SIZE)
    elif mode == "row":
        _need(set(value) == base | {"view","rowIndex","rowId"}
            and value["view"] in TOOL_VIEWS
            and type(value["rowIndex"]) is int and 0 <= value["rowIndex"] < MAX_ROWS)
        _sha(value["rowId"])
    else:
        _need(False,"市场工具只能使用固定summary/page/row模式")
    return value


def reference_requirements(runtime, role, job_id, raw):
    """Bind a proposed numeric citation; return requirements, not a read grant."""
    runtime = _runtime(runtime)
    _need(role in MARKET_ROLES)
    ref = market_numeric_claims.reference(raw)
    _need((ref["role"],ref["jobId"],ref["reportId"]) ==
        (role,_id(job_id),runtime["reportId"]),
        "市场数值引用不得借其他角色、任务或报告")
    selector = runtime["marketSelector"]
    if ref["view"] == "price_band":
        _need(ref["sourceKey"] == selector["priceBandSourceKey"]
            and ref["bandsDigest"] == digest(selector["bands"]))
    else:
        _need(ref["sourceKey"] == selector["rankCurrentSourceKey"]
            and ref["baselineKey"] == selector["rankBaselineKey"]
            and ref["currentObservationDate"] == selector["currentObservationDate"]
            and ref["baselineObservationDate"] == selector["baselineObservationDate"])
    return {"schemaVersion":"business-promotion-market-citation-requirements-v2",
        "reference":ref,"admissionDigest":runtime["admissionDigest"],
        "sameJobPersistedDispatchRequired":True,
        "ownSuccessfulToolResultRequired":True,
        "owningReadRowRecomputationRequired":True,
        "marketTopSampleOnly":True,"ownSalesAttributionVerified":False,
        "agentReadPersisted":False,"authorityVerified":False}
