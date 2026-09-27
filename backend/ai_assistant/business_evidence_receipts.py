"""Rebuild per-agent directory coverage from immutable, bounded tool receipts.

No model text, sibling job, caller-supplied page hash or declared coverage is a
proof. These helpers only read the ledger and never retry a tool invocation.
"""
import json

from django.db.models import BigIntegerField, F, Func
from django.db.models.functions import Substr
from business_analysis.evidence_v2 import directory_page
from . import business_evidence_store as store, models as m
from .policy import AiError, digest

DIRECTORY_TOOL = "get_business_evidence_directory_v2"
DIRECTORY_LIMIT = 20
MAX_DISPATCHES = 40
MAX_ARGUMENT_BYTES = 8192
MAX_RESULT_BYTES = 256 * 1024


def _reject(message="来源目录读取证明无效，保留原回执，不自动重放"):
    raise AiError(message, "conflict", 409)


def _json(raw, expected_digest, maximum):
    if type(raw) is not str or len(raw) > maximum or len(raw.encode("utf-8")) > maximum or digest(raw) != expected_digest:
        _reject("工具回执摘要或字节容量无效")
    try:
        value = json.loads(raw)
    except (ValueError, TypeError, RecursionError) as error:
        raise AiError("工具回执不是有效JSON", "conflict", 409) from error
    if type(value) is not dict:
        _reject()
    return value


def _same(actual, expected):
    """Traverse only the trusted page shape, never attacker-controlled depth."""
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return len(actual) == len(expected) and all(key in actual and _same(actual[key], value) for key, value in expected.items())
    if type(expected) is list:
        return len(actual) == len(expected) and all(_same(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _trusted(job, snapshot):
    if type(snapshot) is not dict or snapshot.get("executionProfile") != "business-agent-reference-v2" or snapshot.get("evidenceProtocol") != "reference-v2":
        _reject("目录读取证明仅适用于固定的v2执行配置")
    row = m.AiBusinessEvidenceRun.objects.filter(pk=snapshot.get("evidenceRunId")).first()
    if (row is None or row.status != "sealed" or row.owner_email.lower() != job.owner_email.lower()
            or row.scope_json != "null" or job.scope_json != "null" or not store.is_v2(row)):
        _reject("目录证明须绑定本任务所有者的封存v2证据")
    store.verify_seal(row)
    header, seal = json.loads(row.plan_json), json.loads(row.state_json)
    expected = {"evidenceRunId": row.id, "evidenceVersion": row.version,
        "evidencePlanDigest": digest(row.plan_json), "catalogDigest": header["catalogDigest"],
        "sealedDigest": seal["sealedDigest"], "sourceCount": header["sourceCount"]}
    if any(type(snapshot.get(key)) is not type(value) or snapshot[key] != value for key, value in expected.items()):
        _reject("报告与封存目录的身份、版本或摘要不一致")
    return row, header, store.catalog(row)


def directory_progress(job, snapshot):
    """Return nextOffset/complete/pages/catalogDigest for this exact job only.

At most 40 dispatch descriptors and one <=256 KiB result at a time are read.
Failed, audited tool results add no coverage; an unknown or unfinished dispatch
blocks progress rather than authorizing another invocation of its offset.
"""
    row, header, sources = _trusted(job, snapshot)
    dispatches = list(m.AiAgentToolDispatches.objects.filter(job_id=job.id).order_by("tool_call_ordinal").annotate(
        argument_bytes=Func(F("arguments_json"), function="OCTET_LENGTH", output_field=BigIntegerField()),
        arguments_text=Substr("arguments_json", 1, MAX_ARGUMENT_BYTES+1)).values(
        "id", "job_id", "provider_dispatch__job_id", "tool_call_ordinal", "tool_name", "state",
        "arguments_text", "argument_bytes", "arguments_digest")[:MAX_DISPATCHES+1])
    if len(dispatches) > MAX_DISPATCHES:
        _reject("目录证明的工具派发数量超限")
    offset, pages = 0, 0
    for ordinal, dispatched in enumerate(dispatches, 1):
        if (dispatched["job_id"] != job.id or dispatched["provider_dispatch__job_id"] != job.id
                or type(dispatched["tool_call_ordinal"]) is not int or dispatched["tool_call_ordinal"] != ordinal):
            _reject("工具回执跨任务、缺号或顺序无效")
        if dispatched["argument_bytes"] > MAX_ARGUMENT_BYTES:
            _reject("工具参数字节容量超限")
        args = _json(dispatched["arguments_text"], dispatched["arguments_digest"], MAX_ARGUMENT_BYTES)
        if dispatched["state"] in {"calling", "unknown"}:
            raise AiError("工具派发结果未知，禁止重放目录请求", "tool_dispatch_unknown", 409)
        if dispatched["state"] not in {"succeeded", "failed"}:
            _reject()
        receipt = m.AiAgentToolResults.objects.filter(tool_dispatch_id=dispatched["id"]).annotate(
            result_bytes=Func(F("result_json"), function="OCTET_LENGTH", output_field=BigIntegerField()),
            result_text=Substr("result_json", 1, MAX_RESULT_BYTES+1)).values(
            "tool_dispatch_id", "result_text", "result_bytes", "result_digest").first()
        if receipt is None:
            if dispatched["state"] == "failed":
                continue
            _reject("成功工具派发缺少持久回执")
        if receipt["tool_dispatch_id"] != dispatched["id"]:
            _reject("工具回执不属于当前派发")
        if receipt["result_bytes"] > MAX_RESULT_BYTES:
            _reject("工具结果字节容量超限")
        result = _json(receipt["result_text"], receipt["result_digest"], MAX_RESULT_BYTES)
        if result.get("toolName") != dispatched["tool_name"] or result.get("auditStatus") != "recorded" or type(result.get("ok")) is not bool:
            _reject("工具回执缺少同名已记录审计")
        if not result["ok"]:
            continue
        if dispatched["state"] != "succeeded":
            _reject("失败派发不能提供成功覆盖证明")
        if dispatched["tool_name"] != DIRECTORY_TOOL:
            if offset != len(sources):
                _reject("完整来源目录读取前不得开展分析")
            continue
        # The model supplies no page size; the v2 tool adapter pins API limit=20.
        if (set(args) - {"runId", "offset"} or args.get("runId") != row.id
                or type(args.get("offset", 0)) is not int or args.get("offset", 0) != offset or offset >= len(sources)):
            _reject("目录请求缺页、重复、乱序或跨证据")
        expected_page = directory_page(sources, run_id=row.id, evidence_version=row.version,
            offset=offset, limit=DIRECTORY_LIMIT, analysis_request=header.get("analysisRequest"))
        # Recompute every field from the sealed directory, including pageDigest.
        # Self-consistent hashes in a forged/reordered page are insufficient.
        if not _same(result.get("data"), expected_page):
            _reject("目录回执与封存来源及精确分页不一致")
        offset += expected_page["returned"]
        pages += 1
    return {"nextOffset": offset if offset < len(sources) else None, "complete": offset == len(sources),
        "pages": pages, "catalogDigest": header["catalogDigest"]}


def validate_directory_complete(job, snapshot):
    """Require this agent's own entire directory before completion or review."""
    proof = directory_progress(job, snapshot)
    if not proof["complete"]:
        _reject("当前Agent尚未逐页完整读取封存来源目录")
    return proof
