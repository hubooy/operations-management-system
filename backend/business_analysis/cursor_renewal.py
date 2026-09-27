"""Unpublished v2 bulk-cursor renewal proposal; never a signing authority.

Owning code must verify the expired token's original signature, real immutable
last chunk, current permissions and source revision independently. No public
JSON (including this proposal) authorizes renewal or changes a checkpoint.
"""
import hashlib
import json
import math
import re

from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, PageReconciler, canonical, digest
from .evidence_v2 import normalize_sources, _same

SCHEMA = "business-cursor-renewal-proposal-v1"
MAX_CHECKPOINT_BYTES = 32768


def _require(condition, message):
    if not condition:
        raise AnalysisContractError(message)


def _fields(value, fields):
    _require(type(value) is dict and set(value) == set(fields), "续签字段集合无效")


def _integer(value, lo=1, hi=MAX_SAFE_INTEGER):
    _require(type(value) is int and lo <= value <= hi, "续签整数范围无效")


def _text(value, maximum):
    _require(type(value) is str and 0 < len(value) <= maximum, "续签文本无效")
    _require(not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value), "续签文本含非法字符")


def _sha(value):
    _require(type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None, "续签摘要无效")


def _raw_sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(raw):
    _text(raw, MAX_CHECKPOINT_BYTES)
    _require(len(raw.encode("utf-8")) <= MAX_CHECKPOINT_BYTES, "检查点字节容量超限")

    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "检查点重复JSON键")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(AnalysisContractError("检查点非有限数")))
    except (ValueError, RecursionError) as error:
        raise AnalysisContractError("检查点JSON无效") from error
    remaining = [4096]

    def bounded(item, depth=0):
        remaining[0] -= 1
        _require(remaining[0] >= 0 and depth <= 12, "检查点结构超限")
        if type(item) is dict:
            for key, child in item.items():
                _text(key, 200)
                bounded(child, depth+1)
        elif type(item) is list:
            for child in item:
                bounded(child, depth+1)
        elif type(item) is str:
            # Metadata may contain newlines; reject lone surrogates, not data.
            _require(not any(0xD800 <= ord(c) <= 0xDFFF for c in item), "检查点字符无效")
        elif type(item) is int:
            _integer(item, -MAX_SAFE_INTEGER)
        elif type(item) is float:
            _require(math.isfinite(item), "检查点非有限数")
        else:
            _require(item is None or type(item) is bool, "检查点须保留无损整数")
    bounded(value)
    return value


def _descriptor(value):
    _fields(value, {"source", "revision", "sourceRef", "pageSize"})
    entry = normalize_sources([value["source"]])[0]
    source = {key: entry[key] for key in ("key", "domain", "query")}
    _require(_same(value["source"], source), "续签查询须为显式窗口的完整规范查询")
    _text(value["revision"], 128)
    _sha(value["sourceRef"])
    _integer(value["pageSize"], 1, 100)
    return {**value, "source": source}


def prepare(snapshot, current_source, expired_payload):
    """Compare owning-supplied observations; returns an inert, bounded DTO.

snapshot is freshly loaded *at renewal time*, not the parent version at the
last page commit. current_source must be independently recomputed by the owning
domain reader, including master snapshot selection in sourceRef. expired_payload
is signature-decoded data supplied by that caller, never a client trust anchor.
"""
    _fields(snapshot, {"runId", "runVersion", "sourceVersion", "principalDigest", "scopeDigest",
        "planDigest", "evidenceSchema", "status", "descriptor", "checkpointJson", "lastPage"})
    _text(snapshot["runId"], 160)
    _require(re.fullmatch(r"[A-Za-z0-9_-]+", snapshot["runId"]), "证据ID无效")
    for key in ("runVersion", "sourceVersion"):
        _integer(snapshot[key])
    for key in ("principalDigest", "scopeDigest", "planDigest"):
        _sha(snapshot[key])
    _require(snapshot["evidenceSchema"] == "business-evidence-v2" and snapshot["status"] == "collecting",
        "本候选只支持仍在采集的v2证据")
    original, current = _descriptor(snapshot["descriptor"]), _descriptor(current_source)
    _require(_same(original, current), "原查询、窗口、页长或来源版本变化，禁止拼接")
    entry = _json(snapshot["checkpointJson"])
    _fields(entry, {"pageCount", "verifier", "metadata"})
    _integer(entry["pageCount"], 1, 1999)
    state = entry["verifier"]
    _fields(state, PageReconciler().__dict__)
    _require(type(entry["metadata"]) is dict and entry["metadata"].get("sourceRevision") == original["revision"],
        "检查点来源版本不一致")
    _require(state["finished"] is False and state["source_ref"] == original["sourceRef"], "来源已结束或身份不符")
    _integer(state["last_id"])
    _integer(state["rows"], 1, 199900)
    _require(entry["pageCount"] <= state["rows"] <= entry["pageCount"]*original["pageSize"], "检查点行数与页长不符")
    _require(state["last_id"] >= state["rows"], "源主键不是合法递增正整数")
    _require(type(state["control"]) is dict and type(state["control"].get("typedTotals")) is dict,
        "检查点缺少首页控制汇总")
    _integer(state["control"].get("rowCount"))
    _require(state["control"]["rowCount"] > state["rows"], "完整行数已耗尽，不应仍有下一页")
    _require(type(state["totals"]) is dict and type(state["present"]) is dict,
        "检查点累计指标无效")
    for values in (state["totals"], state["control"]["typedTotals"]):
        for value in values.values():
            _integer(value, -MAX_SAFE_INTEGER)
    _require(set(state["present"]) <= set(state["totals"]), "检查点指标计数无对应累计值")
    for value in state["present"].values():
        _integer(value, 0, state["rows"])
    _text(state["expected_cursor"], 1600)
    _sha(state["evidence_digest"])
    _fields(expired_payload, {"binding", "lastId"})
    _integer(expired_payload["lastId"])
    _require(expired_payload["binding"] == original["sourceRef"] and expired_payload["lastId"] == state["last_id"],
        "原签名载荷与检查点不一致")
    last = snapshot["lastPage"]
    _fields(last, {"sequence", "payloadDigest", "lastId", "nextCursor", "sourceRef", "sourceRevision"})
    _integer(last["sequence"], 1, 1999)
    _integer(last["lastId"])
    _sha(last["payloadDigest"])
    _require(last["sequence"] == entry["pageCount"] and last["lastId"] == state["last_id"]
        and last["nextCursor"] == state["expected_cursor"] and last["sourceRef"] == original["sourceRef"]
        and last["sourceRevision"] == original["revision"], "持久末页与检查点不一致")
    result = {"schemaVersion": SCHEMA, "state": "prepared_unpublished", "authorityVerified": False,
        "renewalAuthorized": False, "modelReplayAllowed": False, "logicalCursorMustRemainUnchanged": True,
        "runId": snapshot["runId"], "runVersion": snapshot["runVersion"], "sourceVersion": snapshot["sourceVersion"],
        "snapshotDigest": digest(snapshot), "sourceDigest": digest(original),
        "checkpointDigest": _raw_sha(snapshot["checkpointJson"]), "lastPageDigest": last["payloadDigest"],
        "logicalCursorDigest": _raw_sha(state["expected_cursor"]), "cursorPayload": dict(expired_payload)}
    return {**result, "proposalDigest": digest(result)}


def validate(proposal, snapshot, current_source, expired_payload):
    """Rebuild against fresh observations; never interpret caller flags as proof."""
    expected = prepare(snapshot, current_source, expired_payload)
    _require(_same(proposal, expected), "续签候选与当前检查点、身份或版本不一致")
    return expected
