"""Prospective large-evidence capacity contract; no source authority or DB writes.

The supplied row-width upper bounds are measurements, not owning-source proof.
A future v4 collector must independently bind live source revisions and refuse
an over-capacity source without publishing a partial run.
"""
from __future__ import annotations

import re

from . import evidence_v2, evidence_v3, finance_source
from .contracts import AnalysisContractError, canonical, digest

PLAN_SCHEMA = "business-evidence-v4-capacity-plan-v1"
RUN_IDENTITY_SCHEMA = "business-evidence-v4-run-identity-v1"
SOURCE_IDENTITY_SCHEMA = "business-evidence-v4-source-identity-v1"
CHUNK_IDENTITY_SCHEMA = "business-evidence-v4-chunk-identity-v1"
RECEIPT_IDENTITY_SCHEMA = "business-evidence-v4-receipt-identity-v1"
CAPACITY_PROFILE = "business-evidence-v4-provisional-16k-2g-65k-8g-v1"

MAX_SOURCES = 48
MAX_ROWS_PER_PAGE = 100
MAX_DAILY_PAGE_BYTES = 131_072
MAX_FINANCE_PAGE_BYTES = 38_000
MAX_SOURCE_PAGES = 16_384
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
MAX_RUN_PAGES = 65_536
MAX_RUN_BYTES = 8 * 1024 * 1024 * 1024
MAX_SAFE_INTEGER = 9_007_199_254_740_991

MEASUREMENT_FIELDS = frozenset({"sourceKey", "measuredRowCount", "maxRowUtf8Bytes",
    "pageEnvelopeUtf8Bytes", "sourceRevisionHint"})
OPTIONAL_MEASUREMENT_FIELDS = frozenset({"sampleRows"})


def _need(condition, message):
    if not condition:
        raise AnalysisContractError(message)


def _id(value, name):
    _need(type(value) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value), f"{name}无效")
    return value


def _sha(value, name):
    _need(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), f"{name}无效")
    return value


def _integer(value, lo, hi, name):
    _need(type(value) is int and lo <= value <= hi, f"{name}无效")
    return value


def _utf8_length(value, name):
    try:
        return len(value.encode("utf-8"))
    except (UnicodeError, AttributeError) as error:
        raise AnalysisContractError(f"{name}不能编码为UTF-8") from error


def row_utf8_bytes(value):
    """Measure one exact canonical JSON row, including multibyte characters."""
    _need(type(value) is dict, "测量行须为JSON对象")
    try:
        result = len(canonical(value).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("测量行不能规范编码为UTF-8 JSON") from error
    _need(result >= 2, "测量行字节无效")
    return result


def _measurement(value):
    _need(type(value) is dict and MEASUREMENT_FIELDS <= value.keys()
          and value.keys() <= MEASUREMENT_FIELDS | OPTIONAL_MEASUREMENT_FIELDS,
          "来源容量测量字段无效")
    key = _id(value["sourceKey"], "测量来源key")
    rows = _integer(value["measuredRowCount"], 0, MAX_SAFE_INTEGER, "测量行数")
    width = _integer(value["maxRowUtf8Bytes"], 0 if rows == 0 else 2,
                     MAX_RUN_BYTES, "最大行UTF-8字节")
    overhead = _integer(value["pageEnvelopeUtf8Bytes"], 1, MAX_RUN_BYTES,
                        "页固定开销UTF-8字节")
    revision = value["sourceRevisionHint"]
    _need(type(revision) is str and 1 <= _utf8_length(revision, "来源修订提示") <= 128
          and not any(ord(character) < 32 for character in revision),
          "来源修订提示无效")
    samples = value.get("sampleRows", [])
    _need(type(samples) is list and len(samples) <= 10 and len(samples) <= rows,
          "测量样本行数无效")
    _need(all(row_utf8_bytes(item) <= width for item in samples),
          "实测规范行字节超过声明上界")
    return {"sourceKey": key, "measuredRowCount": rows,
            "maxRowUtf8Bytes": width, "pageEnvelopeUtf8Bytes": overhead,
            "sourceRevisionHint": revision,
            "sampleRowCount": len(samples),
            "sampleMaxUtf8Bytes": max((row_utf8_bytes(item) for item in samples), default=0)}


def _estimate(entry, measured):
    page_limit = MAX_FINANCE_PAGE_BYTES if entry["domain"] == "finance" else MAX_DAILY_PAGE_BYTES
    rows, width, overhead = (measured[key] for key in
        ("measuredRowCount", "maxRowUtf8Bytes", "pageEnvelopeUtf8Bytes"))
    # One extra byte per row conservatively reserves JSON array separators.
    available = page_limit - overhead
    rows_per_page = min(MAX_ROWS_PER_PAGE, max(0, available // (width + 1)))
    reasons = []
    if available <= 0 or rows and rows_per_page < 1:
        reasons.append("single_row_or_page_envelope_exceeds_page_bytes")
    pages = max(1, (rows + rows_per_page - 1) // rows_per_page) if rows_per_page else None
    bytes_upper = rows * (width + 1) + (pages if pages is not None else max(1, rows)) * overhead
    if pages is not None and pages > MAX_SOURCE_PAGES:
        reasons.append("source_page_cap_exceeded")
    if entry["domain"] == "finance" and rows > finance_source.MAX_ROWS:
        reasons.append("finance_owning_row_cap_exceeded")
    if bytes_upper > MAX_SOURCE_BYTES:
        reasons.append("source_byte_cap_exceeded")
    if bytes_upper > MAX_SAFE_INTEGER:
        reasons.append("estimate_exceeds_safe_integer")
    identity = {"schemaVersion": SOURCE_IDENTITY_SCHEMA, "key": entry["key"],
        "domain": entry["domain"], "queryDigest": entry["queryDigest"],
        "temporalRole": entry["temporalRole"]}
    return {"sourceKey": entry["key"], "ordinal": entry["ordinal"],
        "domain": entry["domain"], "temporalRole": entry["temporalRole"],
        "query": entry["query"], "queryDigest": entry["queryDigest"],
        "sourceIdentityDigest": digest(identity),
        **({"periodAlignment": entry["periodAlignment"]} if entry["domain"] == "finance" else {}),
        "sourceRevisionHint": measured["sourceRevisionHint"],
        "measurementAuthorityVerified": False,
        "measuredRowCount": rows, "maxRowUtf8Bytes": width,
        "pageEnvelopeUtf8Bytes": overhead, "sampleRowCount": measured["sampleRowCount"],
        "sampleMaxUtf8Bytes": measured["sampleMaxUtf8Bytes"],
        "maximumRowsPerPage": MAX_ROWS_PER_PAGE, "maximumPageUtf8Bytes": page_limit,
        "conservativeRowsPerPage": rows_per_page,
        "estimatedPageCount": pages,
        "estimatedBytesUpperBound": bytes_upper if bytes_upper <= MAX_SAFE_INTEGER else None,
        "sourceCapacitySupported": not reasons,
        "unsupportedReasons": reasons}


def build_plan(*, client_request_id, sources, measurements, analysis_request):
    """Return a deterministic complete plan or explicit unsupported reasons."""
    client = _id(client_request_id, "客户端请求ID")
    entries = evidence_v3.normalize_sources(sources, analysis_request=analysis_request)
    request = evidence_v2.validate_analysis_request(analysis_request)
    _need(2 <= len(entries) <= MAX_SOURCES, "来源目录数量无效")
    _need(type(measurements) is list and len(measurements) == len(entries),
          "来源容量测量数量无效")
    normalized = [_measurement(item) for item in measurements]
    by_key = {item["sourceKey"]: item for item in normalized}
    _need(len(by_key) == len(entries) and set(by_key) == {item["key"] for item in entries},
          "来源测量与精确目录不一致")
    source_plans = [_estimate(entry, by_key[entry["key"]]) for entry in entries]
    source_pages = [item["estimatedPageCount"] for item in source_plans]
    total_pages = sum(source_pages) if all(value is not None for value in source_pages) else None
    byte_values = [item["estimatedBytesUpperBound"] for item in source_plans]
    total_bytes = sum(byte_values) if all(value is not None for value in byte_values) else None
    total_rows = sum(item["measuredRowCount"] for item in source_plans)
    reasons = []
    if any(not item["sourceCapacitySupported"] for item in source_plans):
        reasons.append("one_or_more_sources_unsupported")
    if total_pages is None or total_pages > MAX_RUN_PAGES:
        reasons.append("run_page_cap_exceeded_or_unknown")
    if total_bytes is None or total_bytes > MAX_RUN_BYTES:
        reasons.append("run_byte_cap_exceeded_or_unknown")
    if total_rows > MAX_SAFE_INTEGER:
        reasons.append("run_row_count_exceeds_safe_integer")
    identity = {"schemaVersion": RUN_IDENTITY_SCHEMA, "clientRequestId": client,
        "capacityProfile": CAPACITY_PROFILE,
        "sourceIdentityDigests": [item["sourceIdentityDigest"] for item in source_plans],
        "analysisRequestDigest": digest(request)}
    base = {"schemaVersion": PLAN_SCHEMA, "capacityProfile": CAPACITY_PROFILE,
        "clientRequestId": client, "runIdentityDigest": digest(identity),
        "analysisRequest": request,
        "sourcePlans": source_plans, "sourceCount": len(source_plans),
        "estimatedTotalRows": total_rows if total_rows <= MAX_SAFE_INTEGER else None,
        "estimatedTotalPages": total_pages, "estimatedBytesUpperBound": total_bytes,
        "sourcePageCap": MAX_SOURCE_PAGES, "sourceByteCap": MAX_SOURCE_BYTES,
        "runPageCap": MAX_RUN_PAGES, "runByteCap": MAX_RUN_BYTES,
        "maximumRowsPerPage": MAX_ROWS_PER_PAGE,
        "runCapacitySupported": not reasons, "unsupportedReasons": reasons,
        "sourceAuthorityVerified": False, "measurementAuthorityVerified": False,
        "productionRowWidthApprovalRequired": True,
        "crossDomainSnapshotAtomic": False, "financeDailyProrationAllowed": False,
        "sumOverlappingErpB2bAdsAllowed": False, "skuFinanceProfitInferenceAllowed": False,
        "reportGenerationSupported": False, "modelDispatchSupported": False,
        "meaning": "候选按来源各自修订和自然月/日窗口计算；测量上界待拥有方全量核验，超容量不得截断或封存。"}
    return {**base, "planDigest": digest(base)}


def chunk_identity(*, run_identity_digest, source_identity_digest, sequence,
                   source_ref, source_revision, payload_digest):
    """Prospective immutable chunk identity; payload is never accepted here."""
    _sha(run_identity_digest, "任务身份摘要")
    _sha(source_identity_digest, "来源身份摘要")
    _integer(sequence, 1, MAX_SOURCE_PAGES, "来源页序号")
    _sha(source_ref, "拥有方来源引用")
    _sha(payload_digest, "原始页摘要")
    _need(type(source_revision) is str and 1 <= _utf8_length(source_revision, "拥有方来源修订") <= 128,
          "拥有方来源修订无效")
    body = {"schemaVersion": CHUNK_IDENTITY_SCHEMA,
        "runIdentityDigest": run_identity_digest,
        "sourceIdentityDigest": source_identity_digest,
        "sequence": sequence, "sourceRef": source_ref,
        "sourceRevision": source_revision, "payloadDigest": payload_digest}
    return {**body, "chunkIdentityDigest": digest(body)}


def receipt_identity(*, chunk, audit_id, invocation_id, actor_email,
                     tool_name, response_digest):
    """Only a future owning collector may turn this shape into real authority."""
    _need(type(chunk) is dict and set(chunk) == {"schemaVersion", "runIdentityDigest",
        "sourceIdentityDigest", "sequence", "sourceRef", "sourceRevision",
        "payloadDigest", "chunkIdentityDigest"}
          and chunk.get("schemaVersion") == CHUNK_IDENTITY_SCHEMA
          and chunk.get("chunkIdentityDigest") == digest({k: v for k, v in chunk.items()
              if k != "chunkIdentityDigest"}), "事实块身份无效")
    expected_chunk = chunk_identity(run_identity_digest=chunk["runIdentityDigest"],
        source_identity_digest=chunk["sourceIdentityDigest"], sequence=chunk["sequence"],
        source_ref=chunk["sourceRef"], source_revision=chunk["sourceRevision"],
        payload_digest=chunk["payloadDigest"])
    _need(expected_chunk == chunk, "事实块字段与版本化身份摘要不一致")
    _id(audit_id, "工具审计ID")
    _need(type(invocation_id) is str and re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", invocation_id),
          "工具调用ID无效")
    _need(type(actor_email) is str and actor_email == actor_email.lower()
          and 3 <= len(actor_email) <= 320 and "@" in actor_email,
          "工具审计账号无效")
    _id(tool_name, "工具名")
    _sha(response_digest, "工具响应摘要")
    _need(response_digest == chunk["payloadDigest"], "工具响应与不可变页摘要不同")
    body = {"schemaVersion": RECEIPT_IDENTITY_SCHEMA,
        "chunkIdentityDigest": chunk["chunkIdentityDigest"],
        "auditId": audit_id, "invocationId": invocation_id,
        "actorEmail": actor_email, "toolName": tool_name,
        "responseDigest": response_digest, "authorityVerified": False}
    return {**body, "receiptIdentityDigest": digest(body)}
