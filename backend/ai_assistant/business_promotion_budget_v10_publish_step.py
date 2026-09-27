"""Default-disabled injected-connection 0057/0058 orchestration candidate.

No connection factory, credential loader, role switch, route or automatic tick.
An external protected runtime must supply an authenticated attestor-equivalent
autocommit connection. This module never grants owner/download authority.
"""
from __future__ import annotations

import hashlib
import json

from business_analysis.contracts import AnalysisContractError, canonical
from business_analysis import promotion_budget_v10_publish_request as request

SCHEMA = "business-promotion-budget-v10-staged-attestation-v1"
PREFLIGHT_SCHEMA = "business-promotion-budget-v10-owning-preflight-v1"
PUBLICATION_SCHEMA = "business-budget-v10-publication-v1"


def _need(value, message="v10暂存证明或发布回执与固定请求不一致"):
    if not value:
        raise AnalysisContractError(message)


def _connection(db, enabled):
    _need(enabled is True, "v10证明与发布编排默认关闭")
    _need(getattr(db, "autocommit", None) is True,
          "v10证明与发布要求已认证的autocommit连接")


def _fresh_preflight(run_id, principal):
    # Import lazily so fake-driver tests need neither Django nor a database.
    from . import business_promotion_budget_v10_preflight as preflight
    return preflight.prepare(run_id, principal, enabled=True)


def _one(db, function, args):
    with db.cursor() as cursor:
        cursor.execute("SELECT public." + function + "(" +
            ",".join(["%s"] * len(args)) + ")", args)
        rows = cursor.fetchmany(2)
    _need(len(rows) == 1 and len(rows[0]) == 1,
          "v10数据库函数必须返回唯一标量")
    return rows[0][0]


def _prepared(prepared, run_id):
    _need(type(prepared) is dict and set(prepared) == {
        "schemaVersion", "runId", "attempt", "runVersion", "bindingDigest",
        "attestationText", "attestationSha256", "owningVerificationDigest",
        "publicationFenceDigest", "candidateOnly",
        "databaseCanIndependentlyVerifyProcessAssertions", "readyAuthorized"}
        and prepared["schemaVersion"] == PREFLIGHT_SCHEMA
        and prepared["runId"] == run_id
        and prepared["candidateOnly"] is True
        and prepared["databaseCanIndependentlyVerifyProcessAssertions"] is False
        and prepared["readyAuthorized"] is False,
        "v10 owning预检候选未绑定当前任务")
    raw = prepared["attestationText"]
    _need(type(raw) is str and 1 <= len(raw.encode("utf-8")) <= 131_072
          and hashlib.sha256(raw.encode("utf-8")).hexdigest() ==
              prepared["attestationSha256"],
          "v10证明规范字节摘要不符")
    try:
        body = json.loads(raw)
        _need(type(body) is dict and canonical(body) == raw
              and set(body) == {"schemaVersion", "runId", "attempt",
                  "bindingDigest", "compactJsonSha256", "fullManifestSha256",
                  "fullManifestDigest", "files", "approvedContentDigest",
                  "humanReviewDigest", "budgetPresent", "budgetPlanDigest",
                  "budgetProofDigest", "owningVerificationDigest",
                  "publicationFenceDigest", "verifierVersion"}
              and body["schemaVersion"] == SCHEMA
              and body["verifierVersion"] ==
                  "business-promotion-budget-v10-owning-verifier-v1"
              and body["runId"] == run_id
              and body["attempt"] == prepared["attempt"]
              and body["bindingDigest"] == prepared["bindingDigest"]
              and body["owningVerificationDigest"] ==
                  prepared["owningVerificationDigest"]
              and body["publicationFenceDigest"] ==
                  prepared["publicationFenceDigest"])
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("v10证明正文不是精确规范版本") from error
    _need(type(prepared["runVersion"]) is int and prepared["runVersion"] >= 1
          and type(prepared["attempt"]) is int
          and 1 <= prepared["attempt"] <= 5)
    return body


def _committed(value, *, run_id, attempt, expected_version, request_digest,
               attestation_id, full_manifest_sha256):
    _need(type(value) is dict and set(value) == {"schemaVersion", "status",
        "runId", "attempt", "version", "requestDigest",
        "attestationId", "manifestFileSha256"}
        and value["schemaVersion"] == PUBLICATION_SCHEMA
        and value["status"] == "committed"
        and value["runId"] == run_id
        and value["attempt"] == attempt
        and value["version"] == expected_version + 1
        and value["requestDigest"] == request_digest
        and value["attestationId"] == attestation_id
        and value["manifestFileSha256"] == full_manifest_sha256,
        "v10发布结果未对应原始请求")


def attest_and_publish(db, run_id, principal, *, enabled=False,
                       expected_preflight=None):
    """Verify current owning bytes, call 0057 once, then 0058 once.

    If a caller supplies an already prepared body, this function still calls
    `preflight.prepare` and compares it with a fresh current result before
    either write. A local DTO is never accepted as publication authority.
    """
    _connection(db, enabled)
    fresh = _fresh_preflight(run_id, principal)
    if expected_preflight is not None:
        _need(type(expected_preflight) is dict
              and canonical(expected_preflight) == canonical(fresh),
              "v10先前预检结果已过期")
    body = _prepared(fresh, run_id)
    attempt, expected_version = fresh["attempt"], fresh["runVersion"]
    raw, att_sha = fresh["attestationText"], fresh["attestationSha256"]
    expected_id = hashlib.sha256(f"{run_id}:{attempt}".encode("utf-8")).hexdigest()
    try:
        attestation_id = _one(db, "ai_budget_v10_attest_staged",
            [run_id, attempt, raw])
    except Exception:
        # The SQL statement may have committed before the driver failed.
        # 0057 has no narrow attestor outcome reader, so do not query a table
        # or retry the write. The caller must keep this outcome unknown.
        return {"status": "unknown_attestation_result",
            "authorityVerified": False, "readyAuthorized": False,
            "runId": run_id, "attempt": attempt,
            "attestationSha256": att_sha}
    if attestation_id != expected_id:
        return {"status": "unknown_attestation_result",
            "authorityVerified": False, "readyAuthorized": False,
            "runId": run_id, "attempt": attempt,
            "attestationSha256": att_sha}
    digest_args = {"run_id": run_id, "attempt": attempt,
        "expected_version": expected_version,
        "attestation_id": attestation_id,
        "attestation_sha256": att_sha,
        "binding_digest": body["bindingDigest"],
        "full_manifest_digest": body["fullManifestDigest"],
        "full_manifest_sha256": body["fullManifestSha256"]}
    request_digest = request.request_digest(**digest_args)
    try:
        published = _one(db, "ai_budget_v10_publish",
            [run_id, attempt, expected_version, attestation_id,
             att_sha, request_digest])
    except Exception:
        return {"status": "unknown_publish_result",
            "authorityVerified": False, "readyAuthorized": False,
            "runId": run_id, "attempt": attempt,
            "expectedVersion": expected_version,
            "requestDigest": request_digest,
            "attestationId": attestation_id,
            "attestationSha256": att_sha,
            "bindingDigest": body["bindingDigest"],
            "fullManifestDigest": body["fullManifestDigest"],
            "fullManifestSha256": body["fullManifestSha256"]}
    try:
        outcome = _one(db, "ai_budget_v10_publish_outcome",
            [run_id, attempt, request_digest, attestation_id, att_sha])
        _committed(outcome, run_id=run_id, attempt=attempt,
            expected_version=expected_version, request_digest=request_digest,
            attestation_id=attestation_id,
            full_manifest_sha256=body["fullManifestSha256"])
        _committed(published, run_id=run_id, attempt=attempt,
            expected_version=expected_version, request_digest=request_digest,
            attestation_id=attestation_id,
            full_manifest_sha256=body["fullManifestSha256"])
    except Exception:
        return {"status": "unknown_outcome_readback",
            "authorityVerified": False, "readyAuthorized": False,
            "runId": run_id, "attempt": attempt,
            "requestDigest": request_digest, "attestationId": attestation_id}
    return {"status": "publication_recorded",
        "authorityVerified": False, "readyAuthorized": False,
        "runId": run_id, "attempt": attempt,
        "version": expected_version + 1,
        "requestDigest": request_digest,
        "attestationId": attestation_id}


def recover_publish_outcome(db, *, run_id, attempt, expected_version,
                            attestation_id, attestation_sha256,
                            binding_digest, full_manifest_digest,
                            full_manifest_sha256, original_request_digest,
                            enabled=False):
    """Explicit 0058 read only; `not_committed` never triggers a retry."""
    _connection(db, enabled)
    expected = request.request_digest(run_id=run_id, attempt=attempt,
        expected_version=expected_version,
        attestation_id=attestation_id,
        attestation_sha256=attestation_sha256,
        binding_digest=binding_digest,
        full_manifest_digest=full_manifest_digest,
        full_manifest_sha256=full_manifest_sha256)
    _need(original_request_digest == expected,
          "v10恢复只接受原始发布请求摘要")
    try:
        outcome = _one(db, "ai_budget_v10_publish_outcome",
            [run_id, attempt, original_request_digest,
             attestation_id, attestation_sha256])
    except Exception:
        return {"status": "unknown_recovery_result", "authorityVerified": False,
            "readyAuthorized": False}
    if type(outcome) is not dict:
        return {"status": "unknown_recovery_result", "authorityVerified": False,
            "readyAuthorized": False}
    if outcome.get("status") == "committed":
        _committed(outcome, run_id=run_id, attempt=attempt,
            expected_version=expected_version,
            request_digest=original_request_digest,
            attestation_id=attestation_id,
            full_manifest_sha256=full_manifest_sha256)
        return {"status": "publication_recorded", "authorityVerified": False,
            "readyAuthorized": False, "runId": run_id,
            "attempt": attempt, "version": expected_version + 1,
            "requestDigest": original_request_digest,
            "attestationId": attestation_id}
    if outcome.get("status") == "not_committed":
        _need(set(outcome) == {"status", "runId", "attempt", "version",
            "requestDigest", "attestationId"}
            and outcome["runId"] == run_id and outcome["attempt"] == attempt
            and outcome["version"] == expected_version
            and outcome["requestDigest"] == original_request_digest
            and outcome["attestationId"] == attestation_id)
        return {"status": "not_committed", "authorityVerified": False,
            "readyAuthorized": False, "runId": run_id,
            "attempt": attempt, "requestDigest": original_request_digest}
    return {"status": "unknown_recovery_result", "authorityVerified": False,
        "readyAuthorized": False}
