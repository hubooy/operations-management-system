"""Process-internal candidate recomputation for market sample numbers.

This checks a real report/job identity and replays owning market pages. It does
not validate an Agent dispatch ledger or prove the Agent saw the signed GET.
The private prepared object cannot be reconstructed from caller JSON, yet it
still grants no report/Agent/file publication authority.
"""
from dataclasses import dataclass
import json

from business_analysis import market_numeric_claims as pure
from business_analysis.contracts import AnalysisContractError
from . import business_market_dynamics as price_owning
from . import business_market_observation as rank_owning
from . import business_promotion_market_runtime_contract as selection
from . import models as m
from .policy import AiError, authorize_owner, canonical, current_principal, digest, identifier


_TOKEN = object()


def _reject(message="市场样本数值未通过当前同Agent候选核验"):
    raise AiError(message, "market_numeric_candidate_unverified", 409)


def _job(job_id, principal):
    current_principal(principal, admin=True)
    if principal.scope is not None:
        _reject("市场样本候选仅允许无范围管理员")
    job = m.AiAgentJobs.objects.filter(pk=identifier(job_id, "jobId")).first()
    if job is None:
        _reject("市场候选实际Agent不存在")
    authorize_owner(job, principal)
    if (job.workflow_node_key not in pure.ROLES
            or job.status not in {"running", "completed"}
            or job.cancel_requested
            or not job.workflow_run_id):
        _reject("市场候选角色或任务状态无效")
    report = m.AiReportRun.objects.select_related("workflow").filter(
        workflow_id=job.workflow_run_id).first()
    if report is None:
        _reject("市场候选缺少实际报告")
    authorize_owner(report, principal)
    if (job.owner_email != report.owner_email
            or job.scope_json != report.scope_json
            or report.workflow.owner_email != report.owner_email
            or report.workflow.scope_json != report.scope_json):
        _reject("市场候选Agent和报告归属不同")
    return job, report, (job.id, job.version, job.status, job.cancel_requested,
        job.workflow_node_key, job.owner_email, job.scope_json,
        report.id, digest(report.snapshot_json), report.workflow.version)


def _recompute(report_id, ref, receipt, principal, bands):
    if ref["view"] == "price_band":
        if bands is None or digest(selection._bands(bands)) != ref["bandsDigest"]:
            _reject("市场价格带参数与固定引用不一致")
        found = price_owning.read_row(report_id, ref["sourceKey"],
            "price_band", ref["rowIndex"], ref["rowId"], principal,
            bands=bands)
        coverage = None
    else:
        if bands is not None:
            _reject("进出榜引用不能混入价格带")
        found = rank_owning.read_row(report_id, ref["sourceKey"],
            ref["baselineKey"], ref["currentObservationDate"],
            ref["baselineObservationDate"], ref["rowIndex"],
            ref["rowId"], principal)
        page = rank_owning.page(report_id, {"currentSourceKey": ref["sourceKey"],
            "baselineSourceKey": ref["baselineKey"],
            "currentObservationDate": ref["currentObservationDate"],
            "baselineObservationDate": ref["baselineObservationDate"],
            "offset": ref["rowIndex"], "limit": 20}, principal)
        if (page["bindingDigest"] != found["bindingDigest"]
                or page["table"]["tableDigest"] != ref["tableBindingDigest"]
                or not page["table"]["rows"]
                or page["table"]["rows"][0]["rowId"] != ref["rowId"]):
            _reject("市场两日观察页与精确行不同源")
        coverage = page["table"]["observationCoverage"]
    binding = found["binding"]
    if (found["bindingDigest"] != receipt["bindingDigest"]
            or found["responseDigest"] != receipt["responseDigest"]
            or binding["tableBindingDigest"] != ref["tableBindingDigest"]
            or binding["reportBinding"]["reportId"] != report_id
            or found["row"]["rowIndex"] != ref["rowIndex"]
            or found["row"]["rowId"] != ref["rowId"]):
        _reject("市场签名读取候选与当前封存行不一致")
    if ref["view"] == "price_band":
        if (binding["sourceKey"] != ref["sourceKey"]
                or binding["bandsDigest"] != ref["bandsDigest"]):
            _reject("市场价格带来源或分组不同")
    elif (binding["currentSourceKey"] != ref["sourceKey"]
            or binding["baselineSourceKey"] != ref["baselineKey"]
            or binding["currentObservationDate"] != ref["currentObservationDate"]
            or binding["baselineObservationDate"] != ref["baselineObservationDate"]):
        _reject("市场进出榜来源或观察日期不同")
    return found, pure.number(ref, found["row"], observation_coverage=coverage)


@dataclass(frozen=True, slots=True, init=False)
class PreparedMarketClaim:
    _raw: str
    _digest: str

    def __init__(self, token, value):
        if token is not _TOKEN:
            _reject("不能从JSON恢复市场数值准备")
        raw = canonical(value)
        if len(raw.encode("utf-8")) > 16384:
            _reject("市场数值准备超过固定容量")
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_digest", digest(raw))

    @property
    def value(self):
        if digest(self._raw) != self._digest:
            _reject("市场数值准备已损坏")
        return json.loads(self._raw)


def prepare(job_id, raw_reference, raw_receipt, principal, *, bands=None):
    """Bind one actual job to a candidate read, then recompute its one number."""
    job, report, guard = _job(job_id, principal)
    try:
        candidate = pure.bind_read(raw_reference, raw_receipt,
            job_id=job.id, role=job.workflow_node_key,
            report_id=report.id, owner_email=report.owner_email)
        ref = candidate["reference"]
        fixed_bands = selection._bands(bands) if bands is not None else None
        found, number = _recompute(report.id, ref, raw_receipt,
            principal, fixed_bands)
    except AnalysisContractError as error:
        raise AiError("市场数值候选字段或数字无效", "market_numeric_candidate_unverified", 409) from error
    if _job(job_id, principal)[2] != guard:
        _reject("市场数值读取期间实际Agent或报告已变化")
    return PreparedMarketClaim(_TOKEN, {"candidate": candidate,
        "reference": ref, "receipt": raw_receipt,
        "bands": fixed_bands, "jobGuard": guard,
        "owningResponseDigest": found["responseDigest"],
        "number": number})


def resolve(prepared, principal):
    """Re-read the sealed number, returning a non-authoritative candidate."""
    if type(prepared) is not PreparedMarketClaim:
        _reject("市场数值解析缺少进程内实际准备")
    value = prepared.value
    ref, receipt = value["reference"], value["receipt"]
    job, report, guard = _job(ref["jobId"], principal)
    if guard != tuple(value["jobGuard"]):
        _reject("市场候选实际Agent或报告已变化")
    try:
        found, number = _recompute(report.id, ref, receipt, principal,
            value["bands"])
    except AnalysisContractError as error:
        raise AiError("市场数值候选已失去当前来源或数值", "market_numeric_candidate_unverified", 409) from error
    if (found["responseDigest"] != value["owningResponseDigest"]
            or number != value["number"]
            or _job(job.id, principal)[2] != guard):
        _reject("市场候选封存行或任务在二次核验时变化")
    return {"schemaVersion": "business-market-numeric-resolution-candidate-v1",
        "jobId": job.id, "role": job.workflow_node_key,
        "reportId": report.id, "reference": ref,
        "value": number["value"], "partial": number["partial"],
        "population": pure.ATTRIBUTION, "ownSalesAttributionVerified": False,
        "candidateDigest": value["candidate"]["candidateDigest"],
        "verification": {"sameJobCandidateMatched": True,
            "owningRowRecomputed": True, "signedTransportVerified": False,
            "agentReadPersisted": False, "authorityVerified": False},
        "limitations": value["candidate"]["limitations"]}
