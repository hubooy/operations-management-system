"""Closed, renderer-neutral diagnostic action-plan candidate.

The composition manifest proves table-level provenance, not an entity row or
an Agent's numeric citation. Consequently this contract only admits dimension
review tasks with KPI *definitions*. It cannot authorize bids, prices, spend,
publication, or a quantified improvement promise.
"""
from __future__ import annotations

import re
from copy import deepcopy

from . import report_composition_v1 as composition
from .contracts import AnalysisContractError, canonical, digest


SCHEMA = "business-diagnostic-action-plan-candidate-v1"
DIMENSION_TABLES = {
    "store": frozenset(("store_comparison", "store_daily")),
    "category": frozenset(("category",)),
    "spu": frozenset(("spu_erp", "spu_native")),
    "sku": frozenset(("sku",)),
    "keyword": frozenset(("keyword_sku",)),
}
EXTERNAL_TABLES = frozenset(("finance_month", "b2b_daily", "market_sample"))
REVIEW_DIRECTIONS = {
    "store": frozenset(("repair_source", "reconcile_sales_scope", "review_store_mix")),
    "category": frozenset(("repair_source", "reconcile_identity", "review_category_mix")),
    "spu": frozenset(("repair_source", "reconcile_identity", "review_spu_mix")),
    "sku": frozenset(("repair_source", "reconcile_identity", "review_sku_mix")),
    "keyword": frozenset(("repair_source", "review_keyword_match")),
}
KPI_BY_DIMENSION = {
    "store": frozenset(("source_coverage", "erp_net_sales_cents")),
    "category": frozenset(("source_coverage", "erp_net_sales_cents")),
    "spu": frozenset(("source_coverage", "native_sales_cents", "erp_net_sales_cents")),
    "sku": frozenset(("source_coverage", "native_sales_cents", "erp_net_sales_cents")),
    "keyword": frozenset(("source_coverage", "promotion_spend_cents",
                          "attributed_order_amount_cents")),
}
PHASES = frozenset(("D01-D07", "D08-D14", "D15-D30"))
OWNERS = frozenset(("business_owner", "data_owner", "erp_owner",
                    "ads_analyst", "finance_owner", "merchandising_owner"))
STOP_CONDITIONS = ("source_revision_changed", "coverage_missing",
                   "kpi_deterioration", "budget_cap_exceeded", "manual_stop")
MARKET_RESULT_SCHEMA = "business-market-v2-five-agent-result-candidate-v1"
MARKET_ROLES = ("commerce", "promotion", "market_b2b",
                "independent_review", "report")
MARKET_RESULT_FIELDS = frozenset(("schemaVersion", "executionReportId",
    "roles", "candidateChecksPassed", "persistedSourceIndependentlyLoaded",
    "owningRowsIndependentlyReplayed", "proseNumbersVerified",
    "agentExecutionAuthorized", "numericCitationAllowed",
    "humanReviewApproved", "reportPublishAuthorized"))
ACTION_FIELDS = frozenset(("id", "dimension", "targetIdentityStatus", "phase",
    "direction", "ownerRole", "primaryEvidence", "contextEvidence", "factBasis",
    "budget", "kpi", "observation", "stopConditions", "rollback",
    "executionAllowed", "automaticBidAllowed", "automaticPriceChangeAllowed"))
REF_FIELDS = frozenset(("tableKey", "sourceDigest", "status"))
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
MAX_BYTES = 128 * 1024


def _need(ok, message="深度诊断行动候选缺少可核来源或安全边界"):
    if not ok:
        raise AnalysisContractError(message)


def _sha(value):
    return type(value) is str and _HEX.fullmatch(value) is not None


def _ref(raw, manifests, allowed):
    _need(type(raw) is dict and set(raw) == REF_FIELDS
          and raw.get("tableKey") in allowed)
    found = manifests.get(raw["tableKey"])
    _need(found is not None and raw == {key: found[key] for key in REF_FIELDS},
          "行动引用必须等于同一组合报告的表级来源摘要")
    return found


def _composition(value, verify_current_composition):
    _need(type(value) is dict and value.get("schemaVersion") == composition.SCHEMA
          and _sha(value.get("compositionDigest"))
          and value["compositionDigest"] == digest({key: item for key, item
              in value.items() if key != "compositionDigest"})
          and _sha(value.get("owningBindingDigest"))
          and _sha(value.get("planDigest"))
          and value.get("sameReportPrimaryBindingRechecked") is True
          and value.get("externalContextSameReportClaimed") is False
          and value.get("historicalErpIdentityVerified") is False
          and value.get("shopUniqueVisitorsAvailable") is False
          and value.get("crossDomainAmountsAdded") is False
          and value.get("agentReadPersisted") is False
          and value.get("authorityVerified") is False
          and value.get("registeredRenderer") is False
          and type(value.get("reportId")) is str
          and 0 < len(value["reportId"]) <= 160
          and callable(verify_current_composition),
          "未能核定当前同报告组合根")
    _need(verify_current_composition(value) is True,
          "拥有方没有确认当前封存报告与来源版本")
    manifest = value.get("tableManifest")
    _need(type(manifest) is list and len(manifest) == len(composition.TABLES)
          and all(type(item) is dict for item in manifest)
          and [item.get("tableKey") for item in manifest] ==
              list(composition.TABLES)
          and all(set(item) ==
              {"tableKey", "status", "rowCount", "sourceDigest", "note"}
              and item["status"] in {"candidate_rows", "missing_source",
                  "not_supplied", "sample_only", "context_only"}
              and (item["rowCount"] is None or
                   type(item["rowCount"]) is int and item["rowCount"] >= 0)
              and (item["sourceDigest"] is None or _sha(item["sourceDigest"]))
              for item in manifest), "组合表目录不完整")
    components = value.get("componentDigests")
    _need(type(components) is dict and set(components) ==
          {"store", "sku", "categorySpu", "keyword", "financeB2b", "market"})
    # store_daily holds digest(materialDigests), which is only inside the
    # owning proof; it is checked by the trusted callback, not a component.
    expected = {"store_comparison": "store", "erp_unassigned": "store",
        "category": "categorySpu",
        "spu_erp": "categorySpu", "spu_native": "categorySpu",
        "sku": "sku", "keyword_sku": "keyword", "finance_month": "financeB2b",
        "b2b_daily": "financeB2b", "market_sample": "market"}
    for item in manifest:
        key = item["tableKey"]
        if key in expected:
            _need(item["sourceDigest"] == components[expected[key]],
                  "表目录来源与同报告组件摘要不符")
    finance = value.get("financeB2bProof")
    _need(type(finance) is dict
          and finance.get("candidateDigest") == components["financeB2b"]
          and finance.get("b2bIncludedInErpSales") == "unknown"
          and finance.get("b2bIncludedInPlatformSkuSales") == "unknown"
          and finance.get("b2bShareOfErpSales") is None
          and finance.get("b2bIncrementalSalesCents") is None
          and finance.get("financeDailyProrationAllowed") is False
          and finance.get("crossDomainAmountsAdded") is False,
          "财报、B端与店铺销售包含关系未获可核证明")
    return {item["tableKey"]: item for item in manifest}


def _action(raw, manifests, market_result):
    _need(type(raw) is dict and set(raw) == ACTION_FIELDS
          and type(raw.get("id")) is str and _ID.fullmatch(raw["id"]) is not None
          and type(raw.get("dimension")) is str
          and raw.get("dimension") in DIMENSION_TABLES
          and raw.get("targetIdentityStatus") == "dimension_only_no_row_citation"
          and raw.get("phase") in PHASES
          and type(raw.get("direction")) is str
          and raw.get("direction") in REVIEW_DIRECTIONS[raw["dimension"]]
          and type(raw.get("ownerRole")) is str
          and raw.get("ownerRole") in OWNERS
          and raw.get("executionAllowed") is False
          and raw.get("automaticBidAllowed") is False
          and raw.get("automaticPriceChangeAllowed") is False)
    primary = _ref(raw["primaryEvidence"], manifests,
                   DIMENSION_TABLES[raw["dimension"]])
    contexts = raw["contextEvidence"]
    _need(type(contexts) is list and len(contexts) <= 3
          and all(type(item) is dict and type(item.get("tableKey")) is str
                  for item in contexts)
          and len({item["tableKey"] for item in contexts}) == len(contexts))
    for item in contexts:
        context = _ref(item, manifests, EXTERNAL_TABLES)
        _need(context["status"] in {"context_only", "sample_only"},
              "缺源市场或财报不得伪装成已核背景")
        if context["tableKey"] == "market_sample":
            _need(market_result is not None,
                  "市场样本背景须有独立核验的五Agent候选")
    status = primary["status"]
    _need(type(raw["kpi"]) is dict and type(raw["factBasis"]) is str)
    if status in {"not_supplied", "missing_source"}:
        _need(raw["direction"] == "repair_source"
              and raw["factBasis"] == "source_gap"
              and raw["kpi"].get("metric") == "source_coverage",
              "缺源只能形成补源复核，不能生成经营优化判断")
    else:
        _need(status == "candidate_rows" and type(primary["rowCount"]) is int
              and primary["rowCount"] > 0 and raw["factBasis"] in
              {"table_level_candidate_only", "historical_identity_unverified",
               "keyword_header_only"})
        if raw["dimension"] == "keyword":
            _need(raw["factBasis"] == "keyword_header_only",
                  "关键词表头不证明具体词/商品行")
        elif raw["factBasis"] == "historical_identity_unverified":
            _need(raw["dimension"] in {"category", "spu", "sku"}
                  and raw["direction"] == "reconcile_identity")
        else:
            _need(raw["factBasis"] == "table_level_candidate_only")
    budget = raw["budget"]
    _need(type(budget) is dict and set(budget) ==
          {"principle", "capCents", "capSource"}
          and (type(budget["capCents"]) is int or budget["capCents"] is None)
          and (budget == {"principle": "no_new_spend", "capCents": 0,
                          "capSource": "no_new_spend_policy"}
               or budget == {"principle": "manual_cap_required",
                              "capCents": None,
                              "capSource": "pending_approved_budget_plan"}),
          "无已批准预算来源，不得编造非零预算上限")
    kpi = raw["kpi"]
    _need(type(kpi) is dict and set(kpi) == {"metric", "sourceTableKey",
          "baselineValue", "targetValue", "verificationStatus"}
          and type(kpi["metric"]) is str
          and kpi["metric"] in KPI_BY_DIMENSION[raw["dimension"]]
          and kpi["sourceTableKey"] == primary["tableKey"]
          and kpi["baselineValue"] is None and kpi["targetValue"] is None
          and kpi["verificationStatus"] ==
              "definition_only_pending_owner_row_and_settlement",
          "无行级数值证明，不得设定KPI基线或数值目标")
    observation = raw["observation"]
    _need(type(observation) is dict and set(observation) ==
          {"days", "startGate"} and type(observation["days"]) is int
          and 1 <= observation["days"] <= 30 and observation["startGate"] ==
          "after_owner_source_recheck_and_human_approval")
    _need(raw["stopConditions"] == list(STOP_CONDITIONS)
          and raw["rollback"] in {"discard_unapproved_plan",
              "manual_restore_last_approved_configuration"},
          "试验必须具备停止及人工回退条件")
    return deepcopy(raw)


def prepare_candidate(report, actions, *, enabled=False,
                      verify_current_composition=None, market_result=None,
                      verify_market_result=None):
    """Validate review-only Agent proposals against a trusted current root.

    ``verify_current_composition`` must be an owning-service callback that
    rereads the persisted report. A caller-supplied digest alone has no trust.
    """
    _need(enabled is True, "深度诊断调整计划候选默认关闭")
    manifests = _composition(report, verify_current_composition)
    if market_result is not None:
        _need(type(market_result) is dict
              and set(market_result) == MARKET_RESULT_FIELDS
              and market_result.get("schemaVersion") == MARKET_RESULT_SCHEMA
              and type(market_result.get("executionReportId")) is str
              and type(market_result.get("roles")) is list
              and tuple(item.get("role") for item in market_result["roles"]
                        if type(item) is dict) == MARKET_ROLES
              and market_result.get("candidateChecksPassed") is True
              and all(market_result.get(key) is False for key in
                  ("persistedSourceIndependentlyLoaded",
                   "owningRowsIndependentlyReplayed", "proseNumbersVerified",
                   "agentExecutionAuthorized", "numericCitationAllowed",
                   "humanReviewApproved", "reportPublishAuthorized"))
              and manifests["market_sample"]["status"] == "sample_only"
              and callable(verify_market_result)
              and verify_market_result(market_result, report) is True,
              "市场五Agent候选未与外部市场样本做拥有方复核")
    _need(type(actions) is list and 1 <= len(actions) <= 30,
          "调整计划应有1至30条有界动作")
    checked = [_action(action, manifests, market_result) for action in actions]
    _need(len({action["id"] for action in checked}) == len(checked),
          "调整动作身份重复")
    root = {key: report[key] for key in ("reportId", "planDigest",
        "owningBindingDigest", "compositionDigest")}
    value = {"schemaVersion": SCHEMA, "reportRoot": root,
        "actions": checked, "actionCount": len(checked),
        "primaryEvidenceSameReportRootRechecked": True,
        "externalContextSameReportClaimed": False,
        "marketResultCandidateDigest": digest(market_result)
            if market_result is not None else None,
        "marketResultSamePrimaryReportClaimed": False,
        "marketSampleEqualsOwnSales": False,
        "b2bIncrementalSalesCents": None,
        "financeDailyProrationAllowed": False,
        "numericClaimsVerified": False,
        "agentReadPersisted": False,
        "executionAllowed": False,
        "automaticBidAllowed": False,
        "automaticPriceChangeAllowed": False,
        "humanApprovalRequired": True,
        "registeredRenderer": False,
        "publicationAllowed": False}
    _need(len(canonical(value).encode("utf-8")) <= MAX_BYTES,
          "调整计划候选超过固定容量")
    return {**value, "candidateDigest": digest(value)}


def check_candidate(candidate, report, *, verify_current_composition,
                    market_result=None, verify_market_result=None):
    """Rebuild a serialized candidate against the current owning report.

    A saved candidate digest is only a change detector. The supplied callback
    must reread the owning state, or be bound to a report already rechecked in
    the same call stack (as in the unregistered table preview).
    """
    _need(type(candidate) is dict and type(candidate.get("actions")) is list,
          "调整计划候选正文缺失")
    expected = prepare_candidate(report, candidate["actions"], enabled=True,
        verify_current_composition=verify_current_composition,
        market_result=market_result, verify_market_result=verify_market_result)
    _need(candidate == expected, "调整计划候选与当前报告或严格合同不一致")
    return expected
