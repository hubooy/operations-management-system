"""Pure, unpublished renderer-11 owning-verification assertion contract.

The database can recheck manifest and root digests, but cannot independently
prove that the protected Python verifier parsed HTML, ZIP OPC and formulas.
This assertion is deliberately insufficient to publish or download a file.
"""
from __future__ import annotations

import hashlib
import json
import re

from .contracts import AnalysisContractError, canonical, digest


SCHEMA = "business-promotion-budget-v11-staged-attestation-v1"
VERIFIER = "business-promotion-budget-v11-owning-verifier-v1"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def _need(condition):
    if not condition:
        raise AnalysisContractError("v11暂存独立校验证明与当前文件不一致")


def compose(*, run_id, attempt, run_version, binding_digest,
            compact_json, full_json_bytes, full_manifest,
            approved_content_digest, human_review_digest,
            budget_present, budget_plan_digest, budget_roots_digest,
            report_snapshot_sha256, workflow_input_sha256, actor_version,
            file_byte_verification_digest, html_rows_digest,
            xlsx_opc_formula_digest, fresh_semantics_verified):
    _need(type(run_id) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", run_id)
          and type(attempt) is int and 1 <= attempt <= 5
          and type(run_version) is int and run_version >= 1
          and type(actor_version) is int and actor_version >= 1
          and type(budget_present) is bool
          and type(compact_json) is str and type(full_json_bytes) is bytes
          and 1 <= len(full_json_bytes) <= 16 * 1024 * 1024
          and type(full_manifest) is dict and fresh_semantics_verified is True)
    for value in (binding_digest, approved_content_digest, human_review_digest,
                  budget_roots_digest, report_snapshot_sha256,
                  workflow_input_sha256, file_byte_verification_digest,
                  html_rows_digest, xlsx_opc_formula_digest):
        _need(type(value) is str and HEX64.fullmatch(value) is not None)
    _need((budget_plan_digest is None) is (not budget_present))
    if budget_present:
        _need(HEX64.fullmatch(budget_plan_digest) is not None)
    try:
        compact = json.loads(compact_json)
        _need(canonical(compact) == compact_json and
              json.loads(full_json_bytes) == full_manifest)
        budget = full_manifest["promotionBudgetProof"]
        slim = full_manifest["promotionSlimProof"]
        _need(compact["rendererVersion"] == 11 and
              full_manifest["rendererVersion"] == 11 and
              compact["attempt"] == attempt and
              compact["bindingDigest"] == binding_digest and
              full_manifest["status"] == "complete" and
              full_manifest["manifestDigest"] == digest({k: v for k, v in
                  full_manifest.items() if k != "manifestDigest"}) and
              full_manifest["promotionFileProof"]["contentDtoDigest"] ==
                  approved_content_digest and
              full_manifest["promotionFileProof"]["humanReviewDigest"] ==
                  human_review_digest and
              budget["approvedContentDigest"] == approved_content_digest and
              budget["humanReviewDigest"] == human_review_digest and
              budget["candidateOnly"] is True and
              budget["budgetPlanDigest"] == budget_plan_digest and
              slim["candidateOnly"] is True and
              slim["publicationStatus"] == "unpublished" and
              slim["sourceBudgetProofDigest"] == budget["proofDigest"] and
              slim["proofDigest"] == digest({k: v for k, v in slim.items()
                  if k != "proofDigest"}) and
              budget["proofDigest"] == digest({k: v for k, v in budget.items()
                  if k != "proofDigest"}))
        files = {"files": compact["files"],
                 "manifestFile": compact["manifestFile"]}
        _need(hashlib.sha256(full_json_bytes).hexdigest() ==
              files["manifestFile"]["sha256"])
        if budget_present:
            _need(budget["status"] == "reconciled_fixed_budget_candidate" and
                  full_manifest["budgetPlanDigest"] == budget_plan_digest)
        else:
            _need(budget["status"] == "missing_fixed_budget" and
                  "budgetPlanDigest" not in full_manifest)
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("v11暂存独立证明缺少字段") from error
    compact_sha = hashlib.sha256(compact_json.encode("utf-8")).hexdigest()
    full_sha = hashlib.sha256(full_json_bytes).hexdigest()
    owning = digest({"schemaVersion": "budget-v11-owning-process-assertion-v1",
        "runId": run_id, "attempt": attempt, "bindingDigest": binding_digest,
        "compactJsonSha256": compact_sha,
        "fullManifestSha256": full_sha,
        "fullManifestDigest": full_manifest["manifestDigest"],
        "fileDescriptors": files,
        "approvedContentDigest": approved_content_digest,
        "humanReviewDigest": human_review_digest,
        "budgetProofDigest": budget["proofDigest"],
        "slimProofDigest": slim["proofDigest"],
        "budgetRootsDigest": budget_roots_digest,
        "fileByteVerificationDigest": file_byte_verification_digest,
        "htmlRowsDigest": html_rows_digest,
        "xlsxOpcFormulaDigest": xlsx_opc_formula_digest,
        "freshSemanticAndFileBytesRebuilt": True})
    body = {"schemaVersion": SCHEMA, "verifierVersion": VERIFIER,
        "runId": run_id, "attempt": attempt, "runVersion": run_version,
        "bindingDigest": binding_digest,
        "compactJsonSha256": compact_sha,
        "fullManifestSha256": full_sha,
        "fullManifestDigest": full_manifest["manifestDigest"],
        "files": files,
        "approvedContentDigest": approved_content_digest,
        "humanReviewDigest": human_review_digest,
        "budgetPresent": budget_present,
        "budgetPlanDigest": budget_plan_digest,
        "budgetProofDigest": budget["proofDigest"],
        "slimProofDigest": slim["proofDigest"],
        "fileByteVerificationDigest": file_byte_verification_digest,
        "htmlRowsDigest": html_rows_digest,
        "xlsxOpcFormulaDigest": xlsx_opc_formula_digest,
        "owningVerificationDigest": owning,
        "reportSnapshotSha256": report_snapshot_sha256,
        "workflowInputSha256": workflow_input_sha256,
        "actorVersion": actor_version}
    raw = canonical(body)
    _need(len(raw.encode("utf-8")) <= 131072)
    return {"attestationText": raw,
        "attestationSha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "owningVerificationDigest": owning,
        "candidateOnly": True, "readyAuthorized": False,
        "databaseCanIndependentlyVerifyProcessAssertions": False}
