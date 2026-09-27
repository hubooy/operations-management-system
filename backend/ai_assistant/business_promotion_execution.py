"""Unregistered final-answer validator for one running promotion Agent step.

It uses the actual four-tool receipt prefix and resolves structured numbers,
then returns a process-local token. It does not reserve, call or commit a model,
advance the workflow, approve review, or grant runtime admission.

The token is valid only while this Agent remains running. Completed-job
content, human review and file delivery need a separate persisted-ledger proof;
they must never deserialize or reuse this process-local token.
"""
from dataclasses import dataclass
import json

from django.db import connection

from . import business_promotion_diagnosis as diagnosis
from . import business_promotion_full_receipts as receipts
from . import business_promotion_read_receipts as fourth
from . import business_promotion_tools as tools
from . import models as m
from .policy import AiError, canonical, current_principal, digest


_TOKEN = object()
MAX_TOKEN_BYTES = 2 * 1024 * 1024 + 128 * 1024


def _reject(message="词货Agent最终回答验证状态已变化"):
    raise AiError(message, "promotion_final_unverified", 409)


def _root(job, principal):
    report, role, guard = tools._job(job.id, principal)
    current_principal(principal, admin=True)
    actual = m.AiAgentJobs.objects.filter(pk=job.id).first()
    fixed = m.AiReportRun.objects.select_related("workflow").filter(pk=report.id).first()
    if actual is None or fixed is None or actual.status != "running":
        _reject("最终回答只接受当前运行中的实际Agent")
    value = {"jobId": actual.id, "reportId": fixed.id, "role": role,
        "ownerEmail": principal.email.lower(), "scope": principal.scope,
        "jobGuard": list(guard), "jobVersion": actual.version,
        "jobLeaseEpoch": actual.lease_epoch, "jobLeaseToken": actual.lease_token,
        "reportSnapshotDigest": digest(fixed.snapshot_json),
        "workflowInputDigest": digest(fixed.workflow.input_json),
        "workflowGraphDigest": digest(fixed.workflow.graph_json),
        "workflowPolicyDigest": fixed.workflow.tool_policy_digest,
        "ledgerDigest": fourth._ledger_fence(actual.id)}
    return actual, value


@dataclass(frozen=True, slots=True, init=False)
class ValidatedFinal:
    _root_json: str
    _answer_digest: str
    _read_json: str
    _diagnosis_json: str
    _token_digest: str

    def __init__(self, token, root, answer, read, result):
        if token is not _TOKEN:
            _reject("最终回答证明只能由本进程实际校验创建")
        values = (canonical(root), digest(answer), canonical(read), canonical(result))
        if sum(len(value.encode("utf-8")) for value in values) > MAX_TOKEN_BYTES:
            raise AiError("最终回答证明超过容量", "payload_too_large", 413)
        for key, value in zip(("_root_json", "_answer_digest", "_read_json",
                               "_diagnosis_json"), values):
            object.__setattr__(self, key, value)
        object.__setattr__(self, "_token_digest", digest(values))

    @property
    def proof(self):
        _sealed(self)
        root = json.loads(self._root_json)
        return {"schemaVersion": "business-promotion-final-validation-v1",
            "jobId": root["jobId"], "reportId": root["reportId"], "role": root["role"],
            "answerDigest": self._answer_digest,
            "readProofDigest": digest(self._read_json),
            "diagnosisDigest": digest(self._diagnosis_json),
            "fullAgentReadComplete": True, "answerValidated": True,
            "runtimeAdmissionGranted": False, "modelDispatchGranted": False,
            "modelAnswerSourceVerified": False, "durableContentProof": False,
            "allAgentsReadVerified": False, "independentReviewApproved": False,
            "humanReviewRequired": True}

    @property
    def diagnosis(self):
        _sealed(self)
        return json.loads(self._diagnosis_json)


def _sealed(value):
    if type(value) is not ValidatedFinal:
        _reject("公开JSON不能恢复为最终回答证明")
    values = (value._root_json, value._answer_digest, value._read_json, value._diagnosis_json)
    try:
        if (any(type(part) is not str for part in values)
                or sum(len(part.encode("utf-8")) for part in values) > MAX_TOKEN_BYTES
                or digest(values) != value._token_digest):
            _reject("本进程最终回答证明已损坏")
    except (AttributeError, UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise AiError("最终回答证明编码或结构无效", "promotion_final_unverified", 409) from error
    return json.loads(value._root_json)


def validate_final(job, answer, principal):
    """Resolve read and numeric claims before any caller-owned mutation."""
    if connection.in_atomic_block:
        raise AiError("词货最终回答完整验证须在最外层事务之外", "invalid_request", 400)
    actual, before = _root(job, principal)
    read = receipts.progress(actual, principal)
    if (read["jobId"] != actual.id or read["reportId"] != before["reportId"]
            or read["role"] != before["role"]
            or read["fullAgentReadComplete"] is not True
            or read["agentReadVerified"] is not True):
        _reject("当前Agent尚未完整读取必需的固定角色材料")
    resolved = diagnosis.validate(actual, answer, principal)
    if (resolved["reportId"] != before["reportId"]
            or resolved["role"] != before["role"]
            or resolved["humanReviewRequired"] is not True):
        _reject("数值诊断与当前Agent或人工复核边界不一致")
    if _root(actual, principal)[1] != before:
        _reject("读取和诊断期间Agent、租约或持久账本已变化")
    return ValidatedFinal(_TOKEN, before, answer, read, resolved)


def check(validated, job, answer, principal):
    """Recheck only live metadata and bounded ledger before caller mutation."""
    root = _sealed(validated)
    if (type(answer) is not str or digest(answer) != validated._answer_digest
            or principal.email.lower() != root["ownerEmail"]
            or canonical(principal.scope) != canonical(root["scope"])):
        _reject("回答、所有者或范围与本进程证明不同")
    if _root(job, principal)[1] != root:
        _reject("最终回答准备后Agent、报告、租约或回执发生变化")
    return validated.proof
