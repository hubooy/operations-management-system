"""Non-authorizing market-v2 tariff and required-reservation snapshot."""
from business_analysis import market_model_cost_envelope as cost
from business_analysis.contracts import AnalysisContractError, digest
from business_analysis.promotion_views import _copy


SCHEMA = "business-market-v2-cost-ledger-candidate-v1"
MODEL_FIELDS = {"id", "version", "status", "modelType", "protocol",
    "maxTokens", "maxToolRounds", "maxTotalToolCalls"}


def _need(ok):
    if not ok:
        raise AnalysisContractError("市场v2模型费率或预留候选不满足固定边界")


def build(plan_id, model, tariff, jobs, *, at_utc, cap_claim_cents,
          approval_claim_digest, chargeable_tools=False):
    _need(type(plan_id) is str and len(plan_id)==64
        and all(ch in "0123456789abcdef" for ch in plan_id))
    fixed = _copy(model, 2048)
    _need(type(fixed) is dict and set(fixed)==MODEL_FIELDS
        and fixed["status"]=="enabled" and fixed["modelType"]=="text"
        and fixed["protocol"] in {"openai_compatible","anthropic"}
        and type(fixed["version"]) is int and fixed["version"]>=1
        and type(fixed["maxTokens"]) is int and fixed["maxTokens"]>=1
        and type(fixed["maxToolRounds"]) is int and 1<=fixed["maxToolRounds"]<=20
        and type(fixed["maxTotalToolCalls"]) is int
        and 1<=fixed["maxTotalToolCalls"]<=40)
    value = cost.reserve(tariff, jobs, model_id=fixed["id"],
        model_version=fixed["version"], at_utc=at_utc,
        approved_cap_cents=cap_claim_cents,
        approval_digest=approval_claim_digest,
        chargeable_tools=chargeable_tools)
    _need(all(item["maxRounds"]<=fixed["maxToolRounds"]
        and item["maxOutputTokensPerRound"]<=fixed["maxTokens"]
        for item in value["jobs"])
        and sum(item["maxRounds"] for item in value["jobs"])
            <= 5*fixed["maxToolRounds"]
        and fixed["maxTotalToolCalls"]>=5)
    result = {"schemaVersion":SCHEMA,"planId":plan_id,
        "model":fixed,"modelDigest":digest(fixed),
        "tariff":_copy(tariff,4096),"tariffDigest":value["tariffDigest"],
        "envelope":value,"envelopeDigest":value["envelopeDigest"],
        "requiredCents":value["reservationRequiredCents"],
        "approvedCapClaimCents":cap_claim_cents,
        "reservedCents":0,"status":"pending_rate_and_approval_verification",
        "tariffAuthorityVerified":False,
        "humanApprovalAuthorityVerified":False,
        "extraChargeCategoryCoverageVerified":False,
        "currencyConversionVerified":False,
        "fundsReserved":False,"providerCallsAllowed":False}
    return {**result,"candidateDigest":digest(result)}
