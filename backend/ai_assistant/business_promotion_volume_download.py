"""Bounded immutable renderer-7 chunk read after internal publication.

The ready row already passed a complete owning scan. Each 512KiB read checks
current actor, frozen publication metadata, compact descriptor and chunk SHA;
it never repeats full source or multi-volume fact traversal.
"""
import base64
import hashlib
import json
import re

from business_analysis import volume_delivery
from business_analysis.contracts import AnalysisContractError
from . import business_evidence, business_files as files
from . import business_promotion_volume_stage as stage
from . import models as m
from .policy import AiError, current_principal, digest


def _conflict(message="词货正式分卷下载与当前批准状态不一致"):
    raise AiError(message, "conflict", 409)


def _fence(row, principal, compact):
    current_principal(principal, admin=True)
    if row.renderer_version != 7 or row.draft or row.status != "ready":
        _conflict("词货多卷文件尚未正式就绪")
    try:
        progress = json.loads(row.progress_json)
        snapshot = json.loads(row.report.snapshot_json)
    except (ValueError, TypeError, KeyError) as error:
        raise AiError("文件发布元信息无效", "conflict", 409) from error
    if (type(progress) is not dict
            or set(progress) != {"stage", "publicationFenceDigest", "manifestFileSha256"}
            or progress["stage"] != "ready"
            or type(progress["publicationFenceDigest"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", progress["publicationFenceDigest"]) is None
            or progress["manifestFileSha256"] != compact["manifestFile"]["sha256"]
            or snapshot.get("executionProfile") != "business-agent-screening-promotion-reference-v1"
            or row.report.workflow.status != "completed"):
        _conflict("词货文件没有完整发布回执")
    if stage._publication_fence(row.report_id, principal, write=False) != progress["publicationFenceDigest"]:
        _conflict("文件发布后人审或五角色账本已变化")
    evidence = business_evidence.get_run(snapshot["evidenceRunId"], principal)
    try:
        sealed_digest = json.loads(evidence.state_json).get("sealedDigest")
    except (ValueError, TypeError, AttributeError) as error:
        raise AiError("词货文件封存证据状态无效", "conflict", 409) from error
    if (evidence.status != "sealed" or evidence.version != snapshot["evidenceVersion"]
            or digest(evidence.plan_json) != snapshot["evidencePlanDigest"]
            or sealed_digest != snapshot["sealedDigest"]):
        _conflict("文件封存来源在发布后发生变化")
    return progress


def chunk(row, index, kind, sequence, principal):
    if (type(index) is not int or type(sequence) is not int
            or not 0 <= index <= 100 or not 1 <= sequence <= 512
            or not ((index == 0 and kind == "json")
                or (index > 0 and kind in {"html", "xlsx"}))):
        raise AiError("词货卷号或分块序号无效")
    try:
        compact = volume_delivery.validate(json.loads(row.manifest_json),
            binding_digest=row.binding_digest, attempt=row.attempt,
            draft=False, renderer_version=7)
    except (AnalysisContractError, ValueError, TypeError, KeyError) as error:
        raise AiError("词货文件完整交付清单无效", "conflict", 409) from error
    _fence(row, principal, compact)
    descriptor = next((item for item in [*compact["files"], compact["manifestFile"]]
        if item["volumeIndex"] == index and item["format"] == kind), None)
    if descriptor is None or sequence > descriptor["chunkCount"]:
        raise AiError("词货卷或分块不存在", "not_found", 404)
    part = m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=row.attempt,
        volume_index=index, format=kind, sequence=sequence).first()
    if part is None:
        raise AiError("词货卷分块不存在", "not_found", 404)
    content = bytes(part.content)
    if (len(content) != min(files.CHUNK_BYTES,
                descriptor["bytes"]-(sequence-1)*files.CHUNK_BYTES)
            or hashlib.sha256(content).hexdigest() != part.content_digest):
        _conflict("词货卷分块长度或内容摘要不符")
    _fence(row, principal, compact)
    return {"schemaVersion": "business-volume-chunk-v1", "runId": row.id,
        "volumeIndex": index, "format": kind, "attempt": row.attempt,
        "sequence": sequence, "bytes": len(content), "sha256": part.content_digest,
        "fileSha256": descriptor["sha256"], "bindingDigest": row.binding_digest,
        "base64": base64.b64encode(content).decode()}
