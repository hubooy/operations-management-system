"""Pure, non-authorizing replay of one claimed v4 finance segment.

The callbacks must use protected claim, 0036 MAC, and previous-result receipt
authority. A self-consistent candidate digest never proves an owning fact.
"""
from __future__ import annotations

import hashlib
import re

from . import evidence_v4, finance_collection_state_v4 as finance
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest
from .v4_sealer_segment_replay import (_bounded_previous, _json, _need,
    ATTEMPT_SCHEMA, PROGRESS_SCHEMA, ZERO, HEX16, HEX64, IDENTIFIER)

SCHEMA = "business-v4-sealer-finance-segment-candidate-v2"
IDENTITY_FIELDS = frozenset({"runId", "attemptId", "actorEmail", "actorVersion",
    "sourceId", "sourceKey", "sourceRoot", "sourceVersion", "sourcePageCount",
    "sourceRowCount", "sourceStoredBytes", "sourceRef", "sourceRevision",
    "keyId", "query"})
REVISION = re.compile(r"(?:0|[1-9][0-9]*):[0-9a-f]{64}\Z")


def _identity(value):
    _need(type(value) is dict and set(value) == IDENTITY_FIELDS,
          "v4财报段固定来源身份字段无效")
    for key in ("runId", "attemptId", "sourceId", "sourceKey"):
        _need(type(value[key]) is str and IDENTIFIER.fullmatch(value[key]),
              "v4财报段来源标识无效")
    for key in ("sourceRoot", "sourceRef"):
        _need(type(value[key]) is str and HEX64.fullmatch(value[key]),
              "v4财报段来源摘要无效")
    _need(type(value["keyId"]) is str and HEX16.fullmatch(value["keyId"]),
          "v4财报段密钥版本无效")
    _need(type(value["actorEmail"]) is str and 1 <= len(value["actorEmail"]) <= 320,
          "v4财报段账号身份无效")
    for key, maximum in (("actorVersion", MAX_SAFE_INTEGER),
                         ("sourceVersion", evidence_v4.MAX_SOURCE_PAGES + 1),
                         ("sourcePageCount", evidence_v4.MAX_SOURCE_PAGES),
                         ("sourceStoredBytes", evidence_v4.MAX_SOURCE_BYTES)):
        _need(type(value[key]) is int and 1 <= value[key] <= maximum,
              "v4财报段来源容量或版本无效")
    _need(value["sourceVersion"] == value["sourcePageCount"] + 1,
          "v4财报段完成来源版本无效")
    _need(type(value["sourceRowCount"]) is int
          and 0 <= value["sourceRowCount"] <= evidence_v4.MAX_SOURCE_PAGES * 100,
          "v4财报段来源行数无效")
    _need(type(value["sourceRevision"]) is str
          and REVISION.fullmatch(value["sourceRevision"]),
          "v4财报段完整版本无效")
    # The owner directory supplies this query; the pure layer only checks shape.
    finance._query(value["query"])


def _previous(value, identity, index, verify_previous_result):
    if index == 1:
        _need(value is None, "v4财报首段不能继承其他段")
        return None, 0, 0, digest([]), None, ZERO
    _bounded_previous(value)
    _need(type(value) is dict and value.get("schemaVersion") == SCHEMA
          and value.get("candidateOnly") is True
          and value.get("authorityVerified") is False
          and value.get("financeReplayed") is True
          and value.get("sealCommitted") is False
          and type(value.get("candidateDigest")) is str
          and HEX64.fullmatch(value["candidateDigest"])
          and value["candidateDigest"] == digest({key: item for key, item
              in value.items() if key != "candidateDigest"}),
          "v4财报前段候选摘要无效")
    for key in ("runId", "attemptId", "sourceId", "sourceRoot", "sourceKey",
                "sourceRef", "sourceRevision", "sourceVersion", "keyId"):
        _need(value.get(key) == identity[key], "v4财报前段跨来源或密钥复用")
    _need(type(value.get("segmentIndex")) is int
          and value["segmentIndex"] == index - 1
          and value.get("endSequence") == (index - 1) * 16
          and type(value.get("progress")) is dict,
          "v4财报前段次序不连续")
    progress = value["progress"]
    _need(set(progress) == {"pageCount", "rowCount", "storedBytes",
        "lastChunkDigest", "receiptChainDigest", "financeState"}
        and progress["pageCount"] == value["endSequence"]
        and type(progress["rowCount"]) is int and progress["rowCount"] >= 0
        and type(progress["storedBytes"]) is int and progress["storedBytes"] > 0
        and type(progress["lastChunkDigest"]) is str
        and HEX64.fullmatch(progress["lastChunkDigest"])
        and type(progress["receiptChainDigest"]) is str
        and HEX64.fullmatch(progress["receiptChainDigest"]),
          "v4财报前段有限进度无效")
    state = finance._state(progress["financeState"], identity["query"])
    _need(state["pageCount"] == progress["pageCount"]
          and state["rowsRead"] == progress["rowCount"]
          and state["storedBytes"] == progress["storedBytes"]
          and state["finished"] is False
          and type(value.get("segmentProofDigest")) is str
          and HEX64.fullmatch(value["segmentProofDigest"]),
          "v4财报前段月度状态或证明摘要无效")
    _need(callable(verify_previous_result)
          and verify_previous_result(value, identity, index) is True,
          "v4财报前段缺独立受保护回执核验")
    return (state, progress["storedBytes"], progress["rowCount"],
            progress["receiptChainDigest"], progress["lastChunkDigest"],
            value["segmentProofDigest"])


def replay_finance_segment(identity, segment, pages, *, previous=None,
                           verify_claim=None, verify_segment_mac=None,
                           verify_previous_result=None):
    """Replay at most 16 claimed pages into a non-authorizing checkpoint."""
    _identity(identity)
    _need(callable(verify_claim) and callable(verify_segment_mac),
          "v4财报段缺受保护claim或0036 MAC验证器")
    _need(type(segment) is dict and type(pages) is list,
          "v4财报段被动记录结构无效")
    index, count = segment.get("segment_index"), identity["sourcePageCount"]
    _need(type(index) is int and 1 <= index <= (count + 15) // 16,
          "v4财报段序号越界")
    start, end = (index - 1) * 16 + 1, min(index * 16, count)
    _need(segment.get("start_sequence") == start
          and segment.get("end_sequence") == end
          and segment.get("source_version") == identity["sourceVersion"]
          and segment.get("source_ref") == identity["sourceRef"]
          and segment.get("source_revision") == identity["sourceRevision"]
          and segment.get("run_bound_capability_verified") is True
          and type(segment.get("segment_id")) is str
          and IDENTIFIER.fullmatch(segment["segment_id"])
          and len(pages) == end - start + 1,
          "v4财报段范围或领用读取不连续")
    state, size, rows, chain, last_digest, prior_digest = _previous(
        previous, identity, index, verify_previous_result)
    _need(segment.get("previous_segment_digest") == prior_digest,
          "v4财报段前驱证明不连续")
    progress = _json(segment.get("progress_json"), 32_768)
    _need(type(progress) is dict and set(progress) == {"schemaVersion", "sourceKey",
              "domain", "sourceRef", "sourceRevision", "pageCount", "rowCount",
              "storedBytes", "lastChunkDigest", "receiptChainDigest", "domainState"}
          and progress.get("schemaVersion") == PROGRESS_SCHEMA
          and progress.get("sourceKey") == identity["sourceKey"]
          and progress.get("domain") == "finance"
          and progress.get("sourceRef") == identity["sourceRef"]
          and progress.get("sourceRevision") == identity["sourceRevision"],
          "v4财报段进度来源身份无效")
    progress_digest = hashlib.sha256(segment["progress_json"].encode("utf-8")).hexdigest()
    _need(segment.get("progress_digest") == progress_digest,
          "v4财报段进度原文摘要无效")
    payload = {"schemaVersion": ATTEMPT_SCHEMA,
        "attemptId": identity["attemptId"], "runId": identity["runId"],
        "sourceId": identity["sourceId"], "segmentIndex": index,
        "startSequence": start, "endSequence": end,
        "sourceVersion": identity["sourceVersion"],
        "sourceRef": identity["sourceRef"],
        "sourceRevision": identity["sourceRevision"],
        "previousSegmentDigest": prior_digest,
        "progressDigest": progress_digest, "keyId": identity["keyId"]}
    _need(type(segment.get("proof_digest")) is str
          and segment["proof_digest"] == digest(payload)
          and type(segment.get("proof_mac")) is str
          and HEX64.fullmatch(segment["proof_mac"]),
          "v4财报段证明摘要或MAC格式无效")
    _need(verify_claim(identity, segment) is True,
          "v4财报段claim未由受保护调用方核验")
    _need(verify_segment_mac(payload, segment["proof_mac"]) is True,
          "v4财报段0036 MAC未由受保护调用方核验")
    for offset, item in enumerate(pages):
        sequence = start + offset
        _need(type(item) is dict and item.get("page_sequence") == sequence
              and item.get("source_key") == identity["sourceKey"]
              and item.get("domain") == "finance"
              and item.get("source_version") == identity["sourceVersion"]
              and item.get("source_ref") == identity["sourceRef"]
              and item.get("source_revision") == identity["sourceRevision"]
              and item.get("segment_id") == segment["segment_id"]
              and item.get("run_bound_capability_verified") is True
              and type(item.get("row_count")) is int
              and 0 <= item["row_count"] <= 100,
              "v4财报页序号或来源身份无效")
        raw = item.get("payload_json")
        page = _json(raw, finance.PAGE_BYTES)
        encoded = raw.encode("utf-8")
        actual_digest = hashlib.sha256(encoded).hexdigest()
        _need(item.get("payload_digest") == actual_digest
              and item.get("payload_bytes") == len(encoded)
              and item.get("response_digest") == actual_digest
              and type(item.get("audit_id")) is str and bool(item["audit_id"])
              and type(item.get("invocation_id")) is str and bool(item["invocation_id"])
              and type(item.get("request_id")) is str and bool(item["request_id"])
              and type(item.get("chunk_id")) is str and bool(item["chunk_id"])
              and item.get("tool_name") == "get_business_finance_source_page",
              "v4财报页原文或审计摘要无效")
        expected = ({"query": identity["query"], "offset": 0, "afterId": 0}
                    if sequence == 1 else finance.next_arguments(
                        state, trusted_query=identity["query"]))
        _need(item.get("arguments_digest") == digest(expected),
              "v4财报页工具请求摘要或偏移参数无效")
        state = finance.consume(state, page, trusted_query=identity["query"])
        _need(len(page["rows"]) == item["row_count"],
              "v4财报页行数与原文不一致")
        size += len(encoded)
        rows += item["row_count"]
        last_digest = actual_digest
        chain = digest([chain, sequence, actual_digest, item["audit_id"],
            item["invocation_id"], identity["sourceRef"], identity["sourceRevision"]])
    finite = {"pageCount": end, "rowCount": rows, "storedBytes": size,
        "lastChunkDigest": last_digest, "receiptChainDigest": chain,
        "financeState": state}
    _need(progress.get("pageCount") == end
          and progress.get("rowCount") == rows
          and progress.get("storedBytes") == size
          and progress.get("lastChunkDigest") == last_digest
          and progress.get("receiptChainDigest") == chain
          and progress.get("domainState") == state,
          "v4财报段页行字节、收据或月度状态与0036进度不符")
    _need(rows <= identity["sourceRowCount"]
          and size <= identity["sourceStoredBytes"],
          "v4财报段累计容量越过来源目录")
    if end == count:
        _need(rows == identity["sourceRowCount"]
              and size == identity["sourceStoredBytes"]
              and state["finished"] is True,
              "v4财报末段未覆盖来源全部行或字节")
        finance.result(state, trusted_query=identity["query"])
    else:
        _need(state["finished"] is False,
              "v4财报来源在末段前提前结束")
    result = {"schemaVersion": SCHEMA, "runId": identity["runId"],
        "attemptId": identity["attemptId"], "sourceId": identity["sourceId"],
        "sourceRoot": identity["sourceRoot"], "sourceKey": identity["sourceKey"],
        "sourceRef": identity["sourceRef"],
        "sourceRevision": identity["sourceRevision"],
        "sourceVersion": identity["sourceVersion"], "keyId": identity["keyId"],
        "segmentIndex": index, "endSequence": end,
        "segmentProofDigest": segment["proof_digest"], "progress": finite,
        "previousCandidateDigest": ZERO if index == 1 else previous["candidateDigest"],
        "candidateOnly": True, "authorityVerified": False,
        "financeReplayed": True, "upstreamSignatureVerified": False,
        "sealCommitted": False}
    return {**result, "candidateDigest": digest(result)}
