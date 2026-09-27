"""Pure, non-authorizing replay of one claimed v4 JD promotion segment.

The caller must independently verify the 0041/0042 claim and the 0036 segment
MAC using protected credentials. This module never signs, persists or seals.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from . import evidence_v4, promotion_views
from .contracts import (AnalysisContractError, MAX_SAFE_INTEGER, PageReconciler,
                        canonical, comparison_periods, digest, strict_date)

SCHEMA = "business-v4-sealer-promotion-segment-candidate-v2"
ATTEMPT_SCHEMA = "business-v4-validation-attempt-candidate-v1"
PROGRESS_SCHEMA = "business-v4-validation-progress-candidate-v1"
ZERO = "0" * 64
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX16 = re.compile(r"[0-9a-f]{16}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
IDENTITY_FIELDS = frozenset({"runId", "attemptId", "actorEmail", "actorVersion",
    "sourceId", "sourceKey", "sourceRoot", "sourceVersion", "sourcePageCount",
    "sourceRowCount", "sourceStoredBytes", "sourceRef", "sourceRevision",
    "keyId", "query"})
MAX_PREVIOUS_BYTES = 48_000


def _need(condition, reason):
    if not condition:
        raise AnalysisContractError(reason)


def _json(raw, max_bytes):
    _need(type(raw) is str and len(raw.encode("utf-8")) <= max_bytes,
          "v4段规范JSON容量无效")
    def pairs(items):
        value = {}
        for key, item in items:
            _need(key not in value, "v4段JSON有重复键")
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        _need(canonical(value) == raw, "v4段JSON不是规范原文")
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("v4段JSON无法安全解析") from error
    return value


def _identity(value):
    _need(type(value) is dict and set(value) == IDENTITY_FIELDS,
          "v4段固定来源身份字段无效")
    for key in ("runId", "attemptId", "sourceId", "sourceKey"):
        _need(type(value[key]) is str and IDENTIFIER.fullmatch(value[key]) is not None,
              "v4段固定来源标识无效")
    for key in ("sourceRoot", "sourceRef"):
        _need(type(value[key]) is str and HEX64.fullmatch(value[key]) is not None,
              "v4段来源摘要无效")
    _need(type(value["keyId"]) is str and HEX16.fullmatch(value["keyId"]) is not None,
          "v4段密钥版本无效")
    _need(type(value["actorEmail"]) is str and 1 <= len(value["actorEmail"]) <= 320,
          "v4段账号身份无效")
    for key, maximum in (("actorVersion", MAX_SAFE_INTEGER),
                         ("sourceVersion", evidence_v4.MAX_SOURCE_PAGES + 1),
                         ("sourcePageCount", evidence_v4.MAX_SOURCE_PAGES),
                         ("sourceStoredBytes", evidence_v4.MAX_SOURCE_BYTES)):
        _need(type(value[key]) is int and 1 <= value[key] <= maximum,
              "v4段来源容量或版本无效")
    _need(value["sourceVersion"] == value["sourcePageCount"] + 1,
          "v4段完成来源版本无效")
    _need(type(value["sourceRowCount"]) is int
          and 0 <= value["sourceRowCount"] <= evidence_v4.MAX_SOURCE_PAGES * 100,
          "v4段来源行数无效")
    _need(type(value["sourceRevision"]) is str and re.fullmatch(
        r"(?:0|[1-9][0-9]*):[0-9a-f]{12}", value["sourceRevision"]) is not None,
        "v4段来源修订无效")
    query = value["query"]
    _need(type(query) is dict and set(query) == {"platform", "shop", "dataset",
        "startDate", "endDate", "window"} and query["platform"] == "京东"
        and query["dataset"] == "promotion" and type(query["window"]) is str
        and query["window"] in
        {"current", "previous", "yearAgo"} and type(query["shop"]) is str
        and bool(query["shop"]), "v4段只接固定京东推广查询")
    comparison_periods(query["startDate"], query["endDate"])


def _reconciler(state):
    verifier = PageReconciler()
    _need(type(state) is dict and set(state) == set(verifier.__dict__),
          "v4段前序游标状态无效")
    verifier.__dict__.update(copy.deepcopy(state))
    _need(type(verifier.rows) is int and verifier.rows >= 0
          and type(verifier.last_id) is int and verifier.last_id >= 0
          and type(verifier.finished) is bool,
          "v4段前序游标计数无效")
    return verifier


def _bounded_previous(value):
    """Reject oversized/cyclic candidate text before hashing or copying state."""
    seen, nodes, size = set(), 0, 0
    def walk(item, depth):
        nonlocal nodes, size
        nodes += 1
        _need(depth <= 16 and nodes <= 1024, "v4前段候选结构超限")
        if type(item) in (dict, list):
            marker = id(item)
            _need(marker not in seen and len(item) <= 128,
                  "v4前段候选包含循环或超宽结构")
            seen.add(marker)
            if type(item) is dict:
                for key, child in item.items():
                    _need(type(key) is str, "v4前段候选字段名无效")
                    walk(key, depth + 1)
                    walk(child, depth + 1)
            else:
                for child in item:
                    walk(child, depth + 1)
            seen.remove(marker)
        elif type(item) is str:
            size += len(item.encode("utf-8"))
            _need(size <= MAX_PREVIOUS_BYTES,
                  "v4前段候选超过固定字节容量")
        else:
            _need(item is None or type(item) in (bool, int),
                  "v4前段候选包含不支持的值")
    walk(value, 0)
    _need(len(canonical(value).encode("utf-8")) <= MAX_PREVIOUS_BYTES,
          "v4前段候选规范原文超限")


def _previous(value, identity, index, verify_previous_result):
    if index == 1:
        _need(value is None, "v4首段不能继承其他段")
        return (PageReconciler(), 0, 0, digest([]), None, [], ZERO)
    _bounded_previous(value)
    _need(type(value) is dict and value.get("schemaVersion") == SCHEMA
          and value.get("candidateOnly") is True
          and value.get("authorityVerified") is False
          and type(value.get("candidateDigest")) is str
          and HEX64.fullmatch(value["candidateDigest"]) is not None
          and value.get("candidateDigest") == digest({key: item for key, item
              in value.items() if key != "candidateDigest"}),
          "v4前段候选摘要无效")
    for key in ("runId", "attemptId", "sourceId", "sourceRoot", "sourceKey",
                "sourceRef", "sourceRevision", "sourceVersion", "keyId"):
        _need(value.get(key) == identity[key], "v4前段跨来源或密钥复用")
    _need(value.get("segmentIndex") == index - 1
          and value.get("endSequence") == (index - 1) * 16
          and type(value.get("progress")) is dict,
          "v4前段次序不连续")
    progress = value["progress"]
    _need(set(progress) == {"pageCount", "rowCount", "storedBytes",
        "lastChunkDigest", "receiptChainDigest", "verifier", "observedDates"}
        and progress["pageCount"] == value["endSequence"]
        and type(progress["rowCount"]) is int and progress["rowCount"] >= 0
        and type(progress["storedBytes"]) is int and progress["storedBytes"] > 0
        and type(progress["receiptChainDigest"]) is str
        and HEX64.fullmatch(progress["receiptChainDigest"]) is not None
        and type(progress["lastChunkDigest"]) is str
        and HEX64.fullmatch(progress["lastChunkDigest"]) is not None
        and type(progress["observedDates"]) is list
        and progress["observedDates"] == sorted(set(progress["observedDates"])),
          "v4前段有限进度无效")
    _need(type(value.get("segmentProofDigest")) is str
          and HEX64.fullmatch(value["segmentProofDigest"]) is not None,
          "v4前段证明摘要无效")
    _need(callable(verify_previous_result)
          and verify_previous_result(value, identity, index) is True,
          "v4前段缺独立受保护回执核验")
    return (_reconciler(progress["verifier"]), progress["storedBytes"],
            progress["rowCount"], progress["receiptChainDigest"],
            progress["lastChunkDigest"], progress["observedDates"],
            value["segmentProofDigest"])


def _row_shape(page, *, first, shop, period):
    _need(type(page) is dict and type(page.get("items")) is list
          and len(page["items"]) <= 100, "v4推广页行数无效")
    control = page.get("control")
    if first:
        _need(type(control) is dict and type(control.get("rowCount")) is int
              and 0 <= control["rowCount"] <= evidence_v4.MAX_SOURCE_PAGES * 100
              and type(control.get("typedTotals")) is dict
              and set(control["typedTotals"]) == promotion_views.BASE_METRICS
              and all(type(number) is int and abs(number) <= MAX_SAFE_INTEGER
                  for number in control["typedTotals"].values()),
              "v4推广首页控制总额无效")
    else:
        _need(control is None, "v4推广后续页重复控制总额")
    for row in page["items"]:
        _need(type(row) is dict and set(row) == promotion_views.ROW_FIELDS
              and type(row["rowId"]) is str
              and re.fullmatch(r"[1-9][0-9]{0,19}", row["rowId"]) is not None
              and type(row["sourceRowHash"]) is str
              and HEX64.fullmatch(row["sourceRowHash"]) is not None
              and type(row["batchId"]) is str and 1 <= len(row["batchId"]) <= 160
              and row["platform"] == "京东" and row["shopName"] == shop
              and type(row["date"]) is str
              and period["startDate"] <= row["date"] <= period["endDate"]
              and type(row["dimensions"]) is dict
              and set(row["dimensions"]) == promotion_views.DIMENSIONS
              and all(value is None or type(value) is str
                  and 0 < len(value) <= 240 and value == value.strip()
                  for value in row["dimensions"].values())
              and type(row["metrics"]) is dict
              and set(row["metrics"]) == promotion_views.METRICS
              and all(number is None or type(number) is int
                  and abs(number) <= MAX_SAFE_INTEGER
                  for number in row["metrics"].values()),
              "v4推广页行形状、店铺或日期无效")
        strict_date(row["date"])


def replay_promotion_segment(identity, segment, pages, *, previous=None,
                             verify_claim=None, verify_segment_mac=None,
                             verify_previous_result=None):
    """Replay at most 16 passive 0042 rows; return a non-authorizing checkpoint.

    Both verification callbacks are required and must use protected external
    authority. Passing test stubs cannot turn this result into an accepted seal.
    """
    _identity(identity)
    _need(callable(verify_claim) and callable(verify_segment_mac),
          "v4段缺受保护claim或0036 MAC验证器")
    _need(type(segment) is dict and type(pages) is list,
          "v4段被动记录结构无效")
    index = segment.get("segment_index")
    count = identity["sourcePageCount"]
    _need(type(index) is int and 1 <= index <= (count + 15) // 16,
          "v4段序号越界")
    start, end = (index - 1) * 16 + 1, min(index * 16, count)
    _need(segment.get("start_sequence") == start
          and segment.get("end_sequence") == end
          and segment.get("source_version") == identity["sourceVersion"]
          and segment.get("source_ref") == identity["sourceRef"]
          and segment.get("source_revision") == identity["sourceRevision"]
          and segment.get("run_bound_capability_verified") is True
          and type(segment.get("segment_id")) is str
          and IDENTIFIER.fullmatch(segment["segment_id"]) is not None
          and len(pages) == end - start + 1,
          "v4段范围或领用读取不连续")
    verifier, size, rows, chain, last_digest, observed, prior_digest = (
        _previous(previous, identity, index, verify_previous_result))
    _need(segment.get("previous_segment_digest") == prior_digest,
          "v4段前驱证明不连续")
    progress = _json(segment.get("progress_json"), 32_768)
    _need(type(progress) is dict and progress.get("schemaVersion") == PROGRESS_SCHEMA
          and progress.get("sourceKey") == identity["sourceKey"]
          and progress.get("domain") == "netshop"
          and progress.get("sourceRef") == identity["sourceRef"]
          and progress.get("sourceRevision") == identity["sourceRevision"]
          and type(progress.get("domainState")) is dict,
          "v4段进度来源身份无效")
    progress_raw_digest = hashlib.sha256(segment["progress_json"].encode("utf-8")).hexdigest()
    _need(segment.get("progress_digest") == progress_raw_digest,
          "v4段进度原文摘要无效")
    payload = {"schemaVersion": ATTEMPT_SCHEMA,
        "attemptId": identity["attemptId"], "runId": identity["runId"],
        "sourceId": identity["sourceId"], "segmentIndex": index,
        "startSequence": start, "endSequence": end,
        "sourceVersion": identity["sourceVersion"],
        "sourceRef": identity["sourceRef"],
        "sourceRevision": identity["sourceRevision"],
        "previousSegmentDigest": prior_digest,
        "progressDigest": progress_raw_digest, "keyId": identity["keyId"]}
    _need(type(segment.get("proof_digest")) is str
          and segment["proof_digest"] == digest(payload)
          and type(segment.get("proof_mac")) is str
          and HEX64.fullmatch(segment["proof_mac"]) is not None,
          "v4段证明摘要或MAC格式无效")
    _need(verify_claim(identity, segment) is True,
          "v4段claim未由受保护调用方核验")
    _need(verify_segment_mac(payload, segment["proof_mac"]) is True,
          "v4段0036 MAC未由受保护调用方核验")
    period = comparison_periods(identity["query"]["startDate"],
                                identity["query"]["endDate"])[identity["query"]["window"]]
    seen_dates = set(observed)
    for offset, item in enumerate(pages):
        sequence = start + offset
        _need(type(item) is dict and item.get("page_sequence") == sequence
              and item.get("source_key") == identity["sourceKey"]
              and item.get("domain") == "netshop"
              and item.get("source_version") == identity["sourceVersion"]
              and item.get("source_ref") == identity["sourceRef"]
              and item.get("source_revision") == identity["sourceRevision"]
              and item.get("segment_id") == segment.get("segment_id")
              and item.get("run_bound_capability_verified") is True
              and type(item.get("row_count")) is int
              and 0 <= item["row_count"] <= 100,
              "v4推广页序号或来源身份无效")
        raw = item.get("payload_json")
        page = _json(raw, evidence_v4.MAX_DAILY_PAGE_BYTES)
        encoded = raw.encode("utf-8")
        actual_digest = hashlib.sha256(encoded).hexdigest()
        _need(item.get("payload_digest") == actual_digest
              and item.get("payload_bytes") == len(encoded)
              and item.get("response_digest") == actual_digest
              and type(item.get("audit_id")) is str and bool(item["audit_id"])
              and type(item.get("invocation_id")) is str and bool(item["invocation_id"])
              and type(item.get("request_id")) is str and bool(item["request_id"])
              and type(item.get("chunk_id")) is str and bool(item["chunk_id"])
              and item.get("tool_name") == ("get_business_source_page" if sequence == 1
                                            else "get_business_netshop_continuation_page"),
              "v4推广页原文或审计摘要无效")
        _row_shape(page, first=sequence == 1, shop=identity["query"]["shop"],
                   period=period)
        cursor = verifier.expected_cursor
        expected = ({"domain": "netshop", **identity["query"], "limit": 100}
                    if sequence == 1 else {**identity["query"], "limit": 100,
                        "cursor": cursor, "expectedSourceRef": identity["sourceRef"],
                        "expectedRevision": identity["sourceRevision"],
                        "expectedLastId": verifier.last_id})
        _need(item.get("arguments_digest") == digest(expected),
              "v4推广页工具请求摘要或游标参数无效")
        verifier.consume(page, request_cursor=cursor)
        _need(len(page["items"]) == item["row_count"],
              "v4推广页行数与原文不一致")
        seen_dates.update(row["date"] for row in page["items"])
        size += len(encoded)
        rows += item["row_count"]
        last_digest = actual_digest
        chain = digest([chain, sequence, actual_digest, item["audit_id"],
            item["invocation_id"], identity["sourceRef"], identity["sourceRevision"]])
    finite = {"pageCount": end, "rowCount": rows, "storedBytes": size,
        "lastChunkDigest": last_digest, "receiptChainDigest": chain,
        "verifier": verifier.__dict__, "observedDates": sorted(seen_dates)}
    domain_state = progress["domainState"]
    _need(progress.get("pageCount") == end
          and progress.get("rowCount") == rows
          and progress.get("storedBytes") == size
          and progress.get("lastChunkDigest") == last_digest
          and progress.get("receiptChainDigest") == chain
          and domain_state.get("verifier") == finite["verifier"]
          and domain_state.get("observedDates") == finite["observedDates"],
          "v4段页行字节、收据或游标重放与0036进度不符")
    _need(rows <= identity["sourceRowCount"]
          and size <= identity["sourceStoredBytes"],
          "v4段累计容量越过来源目录")
    if end == count:
        _need(rows == identity["sourceRowCount"]
              and size == identity["sourceStoredBytes"]
              and verifier.finished is True,
              "v4推广末段未覆盖来源全部行或字节")
        verifier.result()
    else:
        _need(verifier.finished is False,
              "v4推广来源在末段前提前结束")
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
        "financeReplayed": False, "sourceMetadataVerified": False,
        "upstreamSignatureVerified": False, "sealCommitted": False}
    return {**result, "candidateDigest": digest(result)}
