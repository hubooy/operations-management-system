"""Canonical, purpose-bound *candidate* receipt format; never authorizes ready.

Only a separately protected verifier may hold a provisioned key. Callers cannot
pass digests to ``sign_after_preflight``: it rebuilds and checks every staged
HTML/XLSX byte through the owning 0067 preflight before it signs anything.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re

from .contracts import AnalysisContractError, canonical


SCHEMA = "business-promotion-budget-v11-protected-verifier-receipt-v1"
PURPOSE = "teruisi:business-promotion-budget-v11:stage-to-ready-once:v1"
DOMAIN = b"teruisi:budget-v11:protected-verifier:v1\x00"
KEY_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def _need(condition):
    if not condition:
        raise AnalysisContractError("v11受保护验证器回执与当前暂存证明不一致")


def body(*, key_id, run_id, attempt, report_id, owner_email,
         attestation_id, attestation_text):
    """Bind every 0067 assertion, including each file SHA and process digest."""
    _need(type(key_id) is str and KEY_ID.fullmatch(key_id) is not None
          and type(run_id) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", run_id)
          and type(attempt) is int and 1 <= attempt <= 5
          and type(report_id) is str and 1 <= len(report_id) <= 160
          and type(owner_email) is str and 1 <= len(owner_email) <= 320
          and type(attestation_id) is str and HEX64.fullmatch(attestation_id)
          and type(attestation_text) is str)
    try:
        attestation = json.loads(attestation_text)
        _need(canonical(attestation) == attestation_text
              and attestation["schemaVersion"] ==
                  "business-promotion-budget-v11-staged-attestation-v1"
              and attestation["runId"] == run_id
              and attestation["attempt"] == attempt)
        descriptors = attestation["files"]
        _need(type(descriptors) is dict and
              type(descriptors["files"]) is list and
              type(descriptors["manifestFile"]) is dict)
        # The 0067 preflight independently checked these bytes. Never replace
        # its assertions with a caller-supplied digest or success flag.
        for field in ("compactJsonSha256", "fullManifestSha256",
                      "fullManifestDigest", "approvedContentDigest",
                      "humanReviewDigest", "budgetProofDigest",
                      "slimProofDigest", "fileByteVerificationDigest",
                      "htmlRowsDigest", "xlsxOpcFormulaDigest",
                      "owningVerificationDigest", "reportSnapshotSha256",
                      "workflowInputSha256"):
            _need(type(attestation[field]) is str and
                  HEX64.fullmatch(attestation[field]) is not None)
        _need(type(attestation["bindingDigest"]) is str and
              HEX64.fullmatch(attestation["bindingDigest"]) is not None)
    except (KeyError, TypeError, ValueError, RecursionError) as error:
        raise AnalysisContractError("v11受保护回执缺少文件或拥有方字段") from error
    return {"schemaVersion": SCHEMA, "purpose": PURPOSE, "keyId": key_id,
        "runId": run_id, "attempt": attempt,
        "runVersion": attestation["runVersion"],
        "reportId": report_id, "ownerEmail": owner_email,
        "bindingDigest": attestation["bindingDigest"],
        "attestationId": attestation_id,
        "attestationSha256": hashlib.sha256(
            attestation_text.encode("utf-8")).hexdigest(),
        "attestation": attestation}


def _mac(secret, receipt_text):
    _need(type(secret) is bytes and 32 <= len(secret) <= 128)
    return hmac.new(secret, DOMAIN + receipt_text.encode("utf-8"),
        hashlib.sha256).hexdigest()


def sign_after_preflight(run_id, principal, *, enabled=False,
                         key_id=None, secret=None):
    """Protected-runtime-only entry; no arbitrary message signing endpoint.

    A future protected deployment must inject the key out of band and limit
    this process and its database identity independently of Django/web roles.
    This candidate has no caller, route, background job or key provisioning.
    """
    _need(enabled is True and type(key_id) is str and
          KEY_ID.fullmatch(key_id) is not None and type(secret) is bytes and
          32 <= len(secret) <= 128)
    from ai_assistant import business_promotion_budget_v11_preflight as preflight
    from ai_assistant import models as m

    fresh = preflight.prepare(run_id, principal, enabled=True)
    _need(fresh["schemaVersion"] ==
          "business-promotion-budget-v11-owning-preflight-v1" and
          fresh["candidateOnly"] is True and
          fresh["readyAuthorized"] is False and fresh["runId"] == run_id)
    attest = m.AiBusinessPromotionBudgetV11Attestation.objects.get(
        run_id=run_id, attempt=fresh["attempt"])
    _need(attest.attestation_json == fresh["attestationText"] and
          attest.attestation_sha256 == fresh["attestationSha256"] and
          attest.binding_digest == fresh["bindingDigest"] and
          attest.report_id == attest.run.report_id and
          attest.owner_email == attest.run.owner_email)
    value = body(key_id=key_id, run_id=run_id, attempt=fresh["attempt"],
        report_id=attest.report_id, owner_email=attest.owner_email,
        attestation_id=attest.id,
        attestation_text=fresh["attestationText"])
    raw = canonical(value)
    _need(len(raw.encode("utf-8")) <= 262144)
    return {"receiptText": raw, "receiptMac": _mac(secret, raw),
        "keyId": key_id, "candidateOnly": True, "readyAuthorized": False}
