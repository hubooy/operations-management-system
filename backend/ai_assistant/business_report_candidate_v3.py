"""Internal read-only report-admission candidate over a verified v3 seal.

This module creates no AiReportRun, workflow, provider call, or file task.
"""
from __future__ import annotations

from business_analysis import report_reference_v3 as contract
from business_analysis.contracts import AnalysisContractError

from . import business_evidence_v3 as plan, business_v3_catalog as catalog
from . import business_v3_seal as seal
from .policy import AiError, fields, identifier, integer

REQUEST_SCHEMA = "business-report-admission-request-v3"


def prepare(body, principal):
    """Bind a non-executable five-agent candidate to one current sealed run."""
    fields(body, {"schemaVersion", "executionProfile", "evidenceRunId",
                  "expectedEvidenceVersion", "expectedSealDigest"},
           {"schemaVersion", "executionProfile", "evidenceRunId",
            "expectedEvidenceVersion", "expectedSealDigest"})
    if body["schemaVersion"] != REQUEST_SCHEMA or body["executionProfile"] != contract.PROFILE:
        raise AiError("v3候选必须采用独立 reference-v3 画像", "conflict", 409)
    run_id = identifier(body["evidenceRunId"])
    version = integer(body["expectedEvidenceVersion"], "expectedEvidenceVersion")
    expected_digest = body["expectedSealDigest"]
    if (type(expected_digest) is not str or len(expected_digest) != 64
            or any(letter not in "0123456789abcdef" for letter in expected_digest)):
        raise AiError("v3封存摘要无效", "invalid_request", 400)
    actor = plan._actor(principal)
    verified = seal.verify(run_id, principal)
    value = verified["seal"]
    if version != verified["version"] or expected_digest != value["sealedDigest"]:
        raise AiError("v3封存版本或摘要已变化", "version_conflict", 409)
    row, built, sources, current_actor = catalog.load(run_id, principal, allow_sealed=True)
    if row.status != "sealed" or current_actor != actor:
        raise AiError("v3候选读取期间账号或封存状态变化", "version_conflict", 409)
    try:
        candidate = contract.build(built["header"], built["entries"], value,
            profile=body["executionProfile"])
    except (AnalysisContractError, KeyError, TypeError, ValueError, RecursionError) as error:
        raise AiError("v3候选来源口径或封存引用无效", "conflict", 409) from error
    catalog.unchanged(row, current_actor, sources, principal)
    if plan._actor(principal) != actor:
        raise AiError("v3候选形成期间账号权限变化", "access_denied", 403)
    return {"candidate": candidate, "verifiedSeal": value,
            "reportGenerationSupported": False,
            "modelDispatchSupported": False, "fileGenerationSupported": False}
