"""Default-off injected-connection 0067 attestation, without publication.

An external protected runtime must supply an authenticated, independent
NOLOGIN-attestor-equivalent autocommit connection. No role switch, credential
loader, route or automatic job is provided here.
"""
from __future__ import annotations

import hashlib
import json

from business_analysis.contracts import AnalysisContractError, canonical


def _need(value):
    if not value:
        raise AnalysisContractError("v11独立证明未绑定当前拥有方文件")


def attest_staged(db, run_id, principal, *, enabled=False,
                  expected_preflight=None):
    _need(enabled is True and getattr(db, "autocommit", None) is True)
    from . import business_promotion_budget_v11_preflight as preflight
    fresh = preflight.prepare(run_id, principal, enabled=True)
    if expected_preflight is not None:
        _need(type(expected_preflight) is dict and
              canonical(expected_preflight) == canonical(fresh))
    _need(type(fresh) is dict and set(fresh) == {
        "schemaVersion", "runId", "attempt", "runVersion",
        "bindingDigest", "attestationText", "attestationSha256",
        "owningVerificationDigest", "candidateOnly", "readyAuthorized",
        "databaseCanIndependentlyVerifyProcessAssertions"})
    _need(fresh["schemaVersion"] ==
          "business-promotion-budget-v11-owning-preflight-v1" and
          fresh["runId"] == run_id and fresh["candidateOnly"] is True and
          fresh["readyAuthorized"] is False and
          fresh["databaseCanIndependentlyVerifyProcessAssertions"] is False)
    raw = fresh["attestationText"]
    _need(type(raw) is str and 1 <= len(raw.encode("utf-8")) <= 131072 and
          hashlib.sha256(raw.encode("utf-8")).hexdigest() ==
              fresh["attestationSha256"])
    body = json.loads(raw)
    _need(canonical(body) == raw and body["schemaVersion"] ==
          "business-promotion-budget-v11-staged-attestation-v1" and
          body["runId"] == run_id and body["attempt"] == fresh["attempt"] and
          body["runVersion"] == fresh["runVersion"] and
          body["bindingDigest"] == fresh["bindingDigest"] and
          body["owningVerificationDigest"] == fresh["owningVerificationDigest"])
    attempt = fresh["attempt"]
    expected_id = hashlib.sha256(f"{run_id}:{attempt}".encode()).hexdigest()
    with db.cursor() as cursor:
        cursor.execute("SELECT session_user,current_user")
        identity = cursor.fetchone()
    _need(identity == ("teruisi_ai_budget_v11_attestor",) * 2)
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT public.ai_budget_v11_attest_staged(%s,%s,%s)",
                [run_id, attempt, raw])
            result = cursor.fetchmany(2)
    except Exception:
        # The statement may have committed before the driver lost its reply.
        # There is no narrow outcome reader yet; never replay it blindly.
        return {"status": "unknown_attestation_result", "runId": run_id,
            "attempt": attempt,
            "attestationSha256": fresh["attestationSha256"],
            "readyAuthorized": False}
    if result != [(expected_id,)]:
        return {"status": "unknown_attestation_result", "runId": run_id,
            "attempt": attempt,
            "attestationSha256": fresh["attestationSha256"],
            "readyAuthorized": False}
    return {"status": "staged_attested_unpublished", "runId": run_id,
        "attempt": attempt, "attestationId": expected_id,
        "attestationSha256": fresh["attestationSha256"],
        "readyAuthorized": False}
