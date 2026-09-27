"""Owning candidate for adding market v2 context to one sealed promotion report.

This reads actual Reader.info controls from the report's own v2 evidence. It
does not persist a new profile, grant an Agent tool, or certify market rows.
"""
import json

from business_analysis.contracts import AnalysisContractError
from . import business_diagnostic_screening as report_binding
from . import business_evidence, business_evidence_store
from . import business_evidence_v3 as actor_service
from . import business_promotion_market_runtime_contract as market_contract
from . import business_promotion_runtime_contract as promotion_contract
from . import models as m
from .policy import AiError, canonical, digest, identifier


SCHEMA = "business-promotion-market-report-admission-candidate-v1"
MAX_RESULT_BYTES = 64 * 1024


def _reject(message="市场补充候选与同报告封存来源不一致"):
    raise AiError(message, "conflict", 409)


def _roots(report_id, principal):
    actor = actor_service._actor(principal)
    fixed, reader, _, _, sources, infos = report_binding._load(
        identifier(report_id, "reportId"), principal)
    if (fixed["executionProfile"] != promotion_contract.PROFILE
            or fixed["scope"] is not None or fixed["ownerEmail"] != actor["email"]):
        _reject("市场v2准入候选仅接受现有封存词货报告及无范围管理员")
    evidence = business_evidence.get_run(fixed["evidenceRunId"], principal)
    if evidence.status != "sealed" or not business_evidence_store.is_v2(evidence):
        _reject("市场候选须属于同一已封存v2证据")
    report = m.AiReportRun.objects.filter(pk=fixed["reportId"]).first()
    if report is None:
        _reject("市场候选报告已不存在")
    snapshot = json.loads(report.snapshot_json)
    if (canonical(snapshot) != report.snapshot_json
            or snapshot.get("executionProfile") != promotion_contract.PROFILE
            or snapshot.get("evidenceRunId") != evidence.id
            or snapshot.get("evidenceVersion") != evidence.version
            or snapshot.get("sealedDigest") != fixed["sealedDigest"]):
        _reject("市场候选报告快照或封存版本变化")
    screening_id = snapshot["screeningIntent"]["id"]
    context = {"reportId": report.id, "runId": evidence.id,
        "screeningId": screening_id, "sealedDigest": fixed["sealedDigest"]}
    return actor, fixed, sources, infos, context


def _proof(key, infos):
    info = infos.get(key)
    if info is None:
        _reject("市场当前或基期来源不在本报告的完整封存目录")
    expected = info["expected"]
    metadata = info["metadata"]
    if (expected.get("reconciled") is not True
            or type(metadata.get("coverage")) is not dict):
        _reject("市场完整来源控制证明或日期覆盖缺失")
    return {"sourceKey": key, "sourceRef": expected["sourceRef"],
        "evidenceDigest": expected["evidenceDigest"],
        "rowCount": expected["rowCount"], "reconciled": True,
        "coverage": metadata["coverage"]}


def describe(report_id, selector, principal, *, checkpoint=None):
    """Return an owner-bound candidate; missing selected days remain explicit."""
    actor, fixed, sources, infos, context = _roots(report_id, principal)
    try:
        if type(selector) is not dict:
            _reject("市场来源、价格带和双观察日须显式选择")
        selected = {key: selector[key] for key in
            ("rankCurrentSourceKey", "rankBaselineKey")}
        proofs = {key: _proof(key, infos) for key in selected.values()}
        candidate = market_contract.prepare_candidate(sources, context,
            selector, proofs)
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, RecursionError) as error:
        raise AiError("市场候选类别、榜单范围、基期或观察日期无效",
            "conflict", 409) from error
    observed = candidate["observationCoverage"]
    statuses = {"current": "observed_date" if observed["currentDatePresent"]
        else "date_not_covered",
        "baseline": "observed_date" if observed["baselineDatePresent"]
        else "date_not_covered"}
    binding = {"schemaVersion": SCHEMA,
        "reportId": fixed["reportId"],
        "reportBindingDigest": digest(fixed),
        "evidenceRunId": fixed["evidenceRunId"],
        "evidenceVersion": fixed["evidenceVersion"],
        "sealedDigest": fixed["sealedDigest"],
        "sourcesDigest": fixed["sourcesDigest"],
        "sourceInfosDigest": fixed["sourceInfosDigest"],
        "ownerEmail": actor["email"], "actorVersion": actor["version"],
        "marketCandidateDigest": candidate["candidateDigest"],
        "selectedProofDigests": candidate["coverageProofDigests"]}
    result = {"schemaVersion": SCHEMA, "binding": binding,
        "candidate": candidate, "observationStatus": statuses,
        "candidateEligible": observed["bothDatesPresent"],
        "reportProfileRegistered": False,
        "sourceCoverageVerified": False,
        "marketRowsReplayed": False,
        "marketAndOwnSalesAdditive": False,
        "ownProductIdentityVerified": False,
        "authorityVerified": False,
        "limitations": ["仅固定同报告封存目录和Reader.info的覆盖声明；三张市场事实表仍须拥有方完整重放。",
            "缺观察日为date_not_covered，不等于未进入TOP样本或零销量。",
            "市场样本金额不能归属本店、ERP或B端销售；价格带汇总与成员不可相加。"]}
    result["bindingDigest"] = digest(result)
    if checkpoint is not None:
        checkpoint({"stage": "market_admission", "phase": "before_final_fence"})
    if (actor_service._actor(principal) != actor
            or canonical(report_binding._revalidate(fixed, principal)[0]) !=
                canonical(fixed)):
        _reject("市场候选计算期间账号、报告或封存来源变化")
    if len(canonical(result).encode("utf-8")) > MAX_RESULT_BYTES:
        raise AiError("市场候选完整绑定超过容量", "payload_too_large", 413)
    return result


def require_observed(report_id, selector, principal, *, checkpoint=None):
    """Gate future creation input; this still grants no profile authority."""
    value = describe(report_id, selector, principal, checkpoint=checkpoint)
    if value["candidateEligible"] is not True:
        _reject("市场本期或基期指定观察日缺覆盖，不能作为分析准入")
    return value
