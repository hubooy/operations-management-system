"""Default-disabled, injected-connection caller for the 0052 final step.

This module has no connection factory, ticket issuer, credential loader or
role switch. A protected external signer must provide a genuine parent MAC
key and an already authenticated, fenced, autocommit sealer connection.
Neither a local candidate nor this receipt grants report authority.
"""
from __future__ import annotations

import hashlib

from .contracts import AnalysisContractError
from .v4_final_commit_contract import (HEX64, commit_seal_request_digest,
    verify_parent_seal_body)
from .v4_sealer_step_core import CONTEXT, _one, _require


COMMIT = "run_id evidence_version sealed_digest consumed_at".split()
CONSUMPTION = "run_id attempt_id evidence_version body_digest consumed_at".split()


def _connection(db, enabled):
    _require(enabled is True, "v4最终封印调用默认关闭")
    _require(getattr(db, "autocommit", None) is True,
             "v4最终封印要求已认证autocommit连接")


def _receipt(db, run_id, attempt_id, request_digest, nonce, claim):
    return _one(db, "ai_v4_sealer_consumption_result",
                [run_id, attempt_id, request_digest, nonce, claim],
                CONSUMPTION)


def _verify_receipt(row, *, run_id, attempt_id, parent_version,
                    body_digest):
    _require(row is not None and row["run_id"] == run_id
             and row["attempt_id"] == attempt_id
             and row["evidence_version"] == parent_version + 1
             and row["body_digest"] == body_digest
             and row["consumed_at"] is not None,
             "v4最终消费回执与提交意图不符")


def commit_claimed_seal(db, derived_parent_key, *, run_id, attempt_id,
                        actor_email, actor_version, nonce, claim,
                        issued_request_digest, canonical_body, body_mac,
                        enabled=False):
    """Call 0052 exactly once; unknown driver outcome is never retried here.

    `issued_request_digest` is the immutable digest supplied when the caller
    obtained the ticket. 0052 independently checks it against the real ticket.
    An exception during the DB write can mean either rollback or success;
    only explicit `recover_claimed_seal` may inspect that case later.
    """
    _connection(db, enabled)
    _require(type(issued_request_digest) is str
             and HEX64.fullmatch(issued_request_digest) is not None,
             "v4最终票据请求摘要无效")
    args = [run_id, attempt_id, actor_email, actor_version, nonce, claim]
    context = _one(db, "ai_v4_sealer_ticket_context", args, CONTEXT)
    _require(context is not None
             and context["run_bound_capability_verified"] is True
             and context["run_id"] == run_id
             and context["attempt_id"] == attempt_id
             and context["parent_status"] == "collecting"
             and type(context["parent_version"]) is int
             and context["parent_version"] >= 1
             and type(context["source_count"]) is int
             and 2 <= context["source_count"] <= 4
             and context["seal_body_json"] is None
             and context["seal_body_digest"] is None
             and context["seal_body_mac"] is None,
             "v4最终claim上下文不是未封印完整父任务")
    _require(type(context["plan_json"]) is str
             and hashlib.sha256(context["plan_json"].encode("utf-8")).hexdigest()
                 == context["plan_digest"],
             "v4最终父计划原文摘要不符")
    body_digest = verify_parent_seal_body(canonical_body, body_mac,
                                          derived_parent_key,
                                          context["key_id"])
    request_digest = commit_seal_request_digest(
        run_id=run_id, attempt_id=attempt_id,
        actor_email=actor_email, actor_version=actor_version,
        parent_version=context["parent_version"],
        plan_digest=context["plan_digest"],
        directory_digest=context["directory_digest"],
        body_json=canonical_body, body_mac=body_mac,
        key_id=context["key_id"], derived_key=derived_parent_key)
    _require(request_digest == issued_request_digest,
             "v4最终封印正文与已发行票据请求不一致")
    again = _one(db, "ai_v4_sealer_ticket_context", args, CONTEXT)
    _require(again == context, "v4最终提交前claim上下文变化")
    try:
        committed = _one(db, "ai_v4_sealer_commit_with_consumption",
            [run_id, attempt_id, actor_email, actor_version, nonce, claim,
             canonical_body, body_digest, body_mac, context["key_id"],
             request_digest], COMMIT)
    except Exception:
        return {"status": "unknown_commit_result", "authorityVerified": False,
                "requestDigest": request_digest, "bodyDigest": body_digest}
    if not (committed is not None and committed["run_id"] == run_id
            and committed["evidence_version"] == context["parent_version"] + 1
            and committed["sealed_digest"] == body_digest
            and committed["consumed_at"] is not None):
        return {"status": "unknown_commit_result", "authorityVerified": False,
                "requestDigest": request_digest, "bodyDigest": body_digest}
    try:
        receipt = _receipt(db, run_id, attempt_id, request_digest,
                           nonce, claim)
    except Exception:
        return {"status": "unknown_readback_result", "authorityVerified": False,
                "requestDigest": request_digest, "bodyDigest": body_digest}
    try:
        _verify_receipt(receipt, run_id=run_id, attempt_id=attempt_id,
                        parent_version=context["parent_version"],
                        body_digest=body_digest)
        _require(receipt["consumed_at"] == committed["consumed_at"],
                 "v4最终消费回执时间与提交结果不符")
    except (AnalysisContractError, KeyError, TypeError):
        return {"status": "unknown_readback_result", "authorityVerified": False,
                "requestDigest": request_digest, "bodyDigest": body_digest}
    return {"status": "consumed_receipt", "authorityVerified": False,
            "requestDigest": request_digest, "bodyDigest": body_digest,
            "evidenceVersion": committed["evidence_version"]}


def recover_claimed_seal(db, *, run_id, attempt_id, issued_request_digest,
                         nonce, claim, expected_body_digest,
                         expected_evidence_version, enabled=False):
    """Separate exact 0043 lookup after an unknown commit; never calls 0052."""
    _connection(db, enabled)
    _require(type(issued_request_digest) is str
             and HEX64.fullmatch(issued_request_digest) is not None
             and type(expected_body_digest) is str
             and HEX64.fullmatch(expected_body_digest) is not None
             and type(expected_evidence_version) is int
             and expected_evidence_version >= 2,
             "v4恢复查询的精确摘要或版本无效")
    try:
        receipt = _receipt(db, run_id, attempt_id, issued_request_digest,
                           nonce, claim)
    except Exception:
        return {"status": "unknown_recovery_result", "authorityVerified": False}
    _verify_receipt(receipt, run_id=run_id, attempt_id=attempt_id,
                    parent_version=expected_evidence_version - 1,
                    body_digest=expected_body_digest)
    return {"status": "consumed_receipt", "authorityVerified": False,
            "requestDigest": issued_request_digest,
            "bodyDigest": expected_body_digest,
            "evidenceVersion": expected_evidence_version}
