"""Default-closed, signed-request-bound renderer-10 volume chunk candidate.

The reader gets only 0059's narrow current-state receipt. It never SELECTs the
SQL-owned attestation table or reuses renderer-9's publication code path.
"""
import base64
import hashlib
import json
import re

from django.conf import settings
from django.db import DatabaseError, connection

from business_analysis import volume_delivery
from business_analysis.contracts import AnalysisContractError
from . import business_files as files, models as m
from .policy import AiError, canonical, current_principal, digest


SCHEMA = "business-volume-chunk-v10-v1"
RECEIPT_SCHEMA = "business-promotion-budget-v10-download-receipt-v1"
HEX = re.compile(r"[0-9a-f]{64}\Z")
RECEIPT_FIELDS = frozenset({"schemaVersion", "runId", "reportId", "ownerEmail",
    "readyVersion", "attempt", "bindingDigest", "attestationId",
    "attestationSha256", "publishRequestDigest", "publicationFenceDigest",
    "manifestFileSha256", "fullManifestDigest", "budgetProofDigest",
    "budgetPresent", "budgetPlanDigest"})


def _conflict(message="预算分卷文件与当前已批准报告不一致"):
    raise AiError(message, "conflict", 409)


def _enabled():
    if getattr(settings, "AI_PROMOTION_BUDGET_V10_DOWNLOAD_ENABLED", False) is not True:
        _conflict("版本10分卷下载尚未启用")


def _narrow_receipt(row, principal):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT public.ai_budget_v10_download_receipt(%s,%s,%s,%s)",
                [row.id, principal.email.lower(), row.attempt, row.binding_digest])
            returned = cursor.fetchone()
            result = returned[0] if returned else None
    except DatabaseError as error:
        raise AiError("预算下载证明或当前栅栏未通过核验", "conflict", 409) from error
    try:
        if type(result) is str:
            result = json.loads(result)
        if (type(result) is not dict or set(result) != RECEIPT_FIELDS or
                len(canonical(result).encode("utf-8")) > 8192 or
                result["schemaVersion"] != RECEIPT_SCHEMA or
                result["runId"] != row.id or result["reportId"] != row.report_id or
                result["ownerEmail"] != principal.email.lower() or
                type(result["attempt"]) is not int or result["attempt"] != row.attempt or
                type(result["readyVersion"]) is not int or
                result["readyVersion"] != row.version or
                type(result["budgetPresent"]) is not bool or
                any(type(result[key]) is not str or HEX.fullmatch(result[key]) is None
                    for key in ("bindingDigest", "attestationId",
                        "attestationSha256", "publishRequestDigest",
                        "publicationFenceDigest", "manifestFileSha256",
                        "fullManifestDigest", "budgetProofDigest")) or
                result["bindingDigest"] != row.binding_digest or
                (result["budgetPresent"] and
                    (type(result["budgetPlanDigest"]) is not str or
                    HEX.fullmatch(result["budgetPlanDigest"]) is None)) or
                (not result["budgetPresent"] and result["budgetPlanDigest"] is not None)):
            _conflict("数据库预算下载回执字段或身份无效")
        return result
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise AiError("数据库预算下载回执无效", "conflict", 409) from error


def _current(row, principal):
    current_principal(principal, admin=True)
    actual = files.get(row.id, principal)
    if (actual.renderer_version != 10 or actual.draft or actual.status != "ready" or
            actual.error_code or actual.report_id != row.report_id or
            actual.owner_email != row.owner_email or
            actual.version != row.version or
            actual.attempt != row.attempt or
            actual.binding_digest != row.binding_digest):
        _conflict("版本10文件不再是当前已批准的同一尝试")
    try:
        compact = volume_delivery.validate(json.loads(actual.manifest_json),
            binding_digest=actual.binding_digest, attempt=actual.attempt,
            draft=False, renderer_version=10)
    except (AnalysisContractError, ValueError, TypeError, KeyError) as error:
        raise AiError("预算完整分卷清单无效", "conflict", 409) from error
    receipt = _narrow_receipt(actual, principal)
    if receipt["manifestFileSha256"] != compact["manifestFile"]["sha256"]:
        _conflict("预算交付清单文件摘要变化")
    return actual, compact, receipt


def chunk(row, index, kind, sequence, principal):
    """Return one verified 512 KiB-or-smaller part, or no content."""
    _enabled()
    if (type(index) is not int or type(sequence) is not int or
            not 0 <= index <= 100 or not 1 <= sequence <= 512 or
            not ((index == 0 and kind == "json") or
                (index > 0 and kind in {"html", "xlsx"}))):
        raise AiError("预算卷号、格式或分片序号无效")
    current, compact, receipt = _current(row, principal)
    descriptor = next((item for item in [*compact["files"], compact["manifestFile"]]
        if item["volumeIndex"] == index and item["format"] == kind), None)
    if descriptor is None or sequence > descriptor["chunkCount"]:
        raise AiError("预算卷或分片不存在", "not_found", 404)
    part = m.AiBusinessVolumeChunk.objects.filter(run_id=current.id,
        attempt=current.attempt, volume_index=index, format=kind,
        sequence=sequence).first()
    if part is None:
        raise AiError("预算卷分片不存在", "not_found", 404)
    content = bytes(part.content)
    expected_length = min(files.CHUNK_BYTES,
        descriptor["bytes"] - (sequence - 1) * files.CHUNK_BYTES)
    if (expected_length < 1 or len(content) != expected_length or
            hashlib.sha256(content).hexdigest() != part.content_digest):
        _conflict("预算卷分片长度或内容摘要不符")
    latest, latest_compact, latest_receipt = _current(row, principal)
    if (latest.version != current.version or
            canonical(latest_compact) != canonical(compact) or
            canonical(latest_receipt) != canonical(receipt)):
        _conflict("预算卷读取期间任务、审批或预算栅栏发生变化")
    return {"schemaVersion": SCHEMA, "runId": row.id,
        "volumeIndex": index, "format": kind, "attempt": current.attempt,
        "readyVersion": current.version, "sequence": sequence,
        "bytes": len(content), "sha256": part.content_digest,
        "fileSha256": descriptor["sha256"],
        "bindingDigest": current.binding_digest,
        "manifestFileSha256": receipt["manifestFileSha256"],
        "receiptDigest": digest(receipt),
        "base64": base64.b64encode(content).decode("ascii")}
