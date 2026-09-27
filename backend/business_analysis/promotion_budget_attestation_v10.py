"""Pure shape and digest contract for a staged renderer-10 assertion.

These two process digests are not independently verifiable by PostgreSQL and
do not grant a file publication or report authority.
"""
from __future__ import annotations

import hashlib
import re

from .contracts import AnalysisContractError, canonical, digest


SCHEMA = "business-promotion-budget-v10-staged-attestation-v1"
VERIFIER = "business-promotion-budget-v10-owning-verifier-v1"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
MAX_ATTESTATION_BYTES = 131_072


def _need(value):
    if not value:
        raise AnalysisContractError("v10暂存文件预检正文与已核材料不一致")


def _sha(value):
    _need(type(value) is str and HEX64.fullmatch(value) is not None)
    return value


def compose(*, run_id, attempt, run_version, binding_digest,
            compact_json, full_json_bytes, full_manifest,
            approved_content_digest, human_review_digest,
            budget_present, budget_plan_digest, budget_roots_digest,
            report_snapshot_sha256, workflow_input_sha256,
            actor_version, fresh_semantics_verified):
    """Build exactly 0057's canonical fields from already verified inputs."""
    _need(type(run_id) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", run_id)
          and type(attempt) is int and 1 <= attempt <= 5
          and type(run_version) is int and run_version >= 1
          and type(actor_version) is int and actor_version >= 1
          and fresh_semantics_verified is True
          and type(budget_present) is bool
          and type(compact_json) is str
          and type(full_json_bytes) is bytes
          and 1 <= len(full_json_bytes) <= 16 * 1024 * 1024
          and type(full_manifest) is dict)
    for value in (binding_digest, approved_content_digest, human_review_digest,
                  budget_roots_digest, report_snapshot_sha256,
                  workflow_input_sha256):
        _sha(value)
    _need((budget_plan_digest is None) is (not budget_present))
    if budget_present:
        _sha(budget_plan_digest)
    import json
    try:
        compact = json.loads(compact_json)
        _need(type(compact) is dict and canonical(compact) == compact_json)
        _need(json.loads(full_json_bytes) == full_manifest)
        _need(type(full_manifest.get("promotionBudgetProof")) is dict)
        proof = full_manifest["promotionBudgetProof"]
        _need(compact["bindingDigest"] == binding_digest
              and compact["attempt"] == attempt
              and compact["rendererVersion"] == 10
              and full_manifest["rendererVersion"] == 10
              and full_manifest["status"] == "complete"
              and full_manifest["manifestDigest"] == digest({key: value
                  for key, value in full_manifest.items() if key != "manifestDigest"})
              and full_manifest["promotionFileProof"]["contentDtoDigest"] ==
                  approved_content_digest
              and full_manifest["promotionFileProof"]["humanReviewDigest"] ==
                  human_review_digest
              and proof["approvedContentDigest"] == approved_content_digest
              and proof["humanReviewDigest"] == human_review_digest
              and proof["candidateOnly"] is True
              and proof["budgetPlanDigest"] == budget_plan_digest)
        _sha(proof["proofDigest"])
        _need(proof["proofDigest"] == digest({key: value for key, value
            in proof.items() if key != "proofDigest"}))
        if budget_present:
            _need(proof["status"] == "reconciled_fixed_budget_candidate"
                  and full_manifest["budgetPlanDigest"] == budget_plan_digest)
        else:
            _need(proof["status"] == "missing_fixed_budget"
                  and "budgetPlanDigest" not in full_manifest)
        files = {"files": compact["files"],
            "manifestFile": compact["manifestFile"]}
        _need(type(files["files"]) is list
              and type(files["manifestFile"]) is dict
              and hashlib.sha256(full_json_bytes).hexdigest() ==
                  files["manifestFile"]["sha256"])
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("v10暂存证明缺字段或无法规范编码") from error
    compact_sha = hashlib.sha256(compact_json.encode("utf-8")).hexdigest()
    full_sha = hashlib.sha256(full_json_bytes).hexdigest()
    owning_digest = digest({"schemaVersion": "budget-v10-owning-process-assertion-v1",
        "runId": run_id, "attempt": attempt,
        "bindingDigest": binding_digest,
        "compactJsonSha256": compact_sha,
        "fullManifestSha256": full_sha,
        "fullManifestDigest": full_manifest["manifestDigest"],
        "fileDescriptors": files,
        "approvedContentDigest": approved_content_digest,
        "humanReviewDigest": human_review_digest,
        "budgetProofDigest": proof["proofDigest"],
        "budgetRootsDigest": budget_roots_digest,
        "freshSemanticAndFileBytesRebuilt": True})
    fence_digest = digest({"schemaVersion": "budget-v10-publication-fence-candidate-v1",
        "runId": run_id, "attempt": attempt, "runVersion": run_version,
        "bindingDigest": binding_digest,
        "compactJsonSha256": compact_sha,
        "fullManifestSha256": full_sha,
        "approvedContentDigest": approved_content_digest,
        "humanReviewDigest": human_review_digest,
        "budgetRootsDigest": budget_roots_digest,
        "reportSnapshotSha256": report_snapshot_sha256,
        "workflowInputSha256": workflow_input_sha256,
        "actorVersion": actor_version})
    body = {"schemaVersion": SCHEMA, "runId": run_id,
        "attempt": attempt, "bindingDigest": binding_digest,
        "compactJsonSha256": compact_sha,
        "fullManifestSha256": full_sha,
        "fullManifestDigest": full_manifest["manifestDigest"],
        "files": files,
        "approvedContentDigest": approved_content_digest,
        "humanReviewDigest": human_review_digest,
        "budgetPresent": budget_present,
        "budgetPlanDigest": budget_plan_digest,
        "budgetProofDigest": proof["proofDigest"],
        "owningVerificationDigest": owning_digest,
        "publicationFenceDigest": fence_digest,
        "verifierVersion": VERIFIER}
    raw = canonical(body)
    _need(len(raw.encode("utf-8")) <= MAX_ATTESTATION_BYTES)
    return {"attestationText": raw,
        "attestationSha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "owningVerificationDigest": owning_digest,
        "publicationFenceDigest": fence_digest,
        "candidateOnly": True,
        "databaseCanIndependentlyVerifyProcessAssertions": False}
