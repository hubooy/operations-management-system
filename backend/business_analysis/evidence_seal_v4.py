"""Pure canonical v4 internal parent-seal body; no signing or DB authority."""
from __future__ import annotations

import hashlib
import json
import re

from .contracts import AnalysisContractError, canonical, digest

SCHEMA = "business-v4-parent-seal-internal-v1"
ADMISSION_SCHEMA = "business-v4-seal-admission-candidate-v1"
MAX_BODY_BYTES = 131_072
TOP_FIELDS = frozenset(("schemaVersion", "runId", "attemptId",
    "evidenceVersion", "planDigest", "directoryDigest", "actorVersion",
    "keyId", "sourceCount", "sources", "crossDomainSnapshotAtomic",
    "financeDailyProrationAllowed", "inferSkuProfit",
    "sumOverlappingErpB2bAdsAllowed", "upstreamSignatureVerified",
    "reportGenerationSupported", "agentDispatchSupported", "humanReviewRequired"))
COMMON = frozenset(("sourceKey", "domain", "queryDigest", "sourceRef",
    "sourceRevision", "sourceVersion", "pageCount", "rowCount",
    "storedBytes", "segmentCount", "terminalSegmentDigest",
    "receiptChainDigest", "revisionFreshness", "liveRevision"))
PROMOTION_FIELDS = COMMON | {"window", "coverage"}
FINANCE_FIELDS = COMMON | {"scope", "analysisPeriod", "missingMonths"}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX16 = re.compile(r"[0-9a-f]{16}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")


def _need(condition, reason="v4封存规范正文无效"):
    if not condition:
        raise AnalysisContractError(reason)


def _pairs(items):
    result = {}
    for key, value in items:
        _need(key not in result, "v4封存正文包含重复JSON键")
        result[key] = value
    return result


def _sha(value, length=64):
    return type(value) is str and (HEX64 if length == 64 else HEX16).fullmatch(value) is not None


def _revision(value, domain):
    width = 64 if domain == "finance" else 12
    if type(value) is not str or re.fullmatch(
            rf"(?:0|[1-9][0-9]*):[0-9a-f]{{{width}}}", value) is None:
        return None
    number = int(value.split(":", 1)[0])
    return number if number <= 9_007_199_254_740_991 else None


def read(raw):
    """Reject duplicate keys, noncanonical text and unsupported assertions."""
    _need(type(raw) is str and len(raw.encode("utf-8")) <= MAX_BODY_BYTES,
        "v4封存正文超过固定UTF-8容量")
    try:
        body = json.loads(raw, object_pairs_hook=_pairs)
        _need(canonical(body) == raw, "v4封存正文不是唯一规范JSON")
        _need(type(body) is dict and set(body) == TOP_FIELDS)
        _need(body["schemaVersion"] == SCHEMA)
        _need(type(body["runId"]) is str and IDENTIFIER.fullmatch(body["runId"]) is not None)
        _need(type(body["attemptId"]) is str and IDENTIFIER.fullmatch(body["attemptId"]) is not None)
        for field in ("planDigest", "directoryDigest"):
            _need(_sha(body[field]))
        _need(_sha(body["keyId"], 16))
        for field in ("evidenceVersion", "actorVersion", "sourceCount"):
            _need(type(body[field]) is int and 1 <= body[field] <= 9_007_199_254_740_991)
        _need(2 <= body["sourceCount"] <= 4 and type(body["sources"]) is list
            and len(body["sources"]) == body["sourceCount"])
        _need(all(body[field] is False for field in (
            "crossDomainSnapshotAtomic", "financeDailyProrationAllowed",
            "inferSkuProfit", "sumOverlappingErpB2bAdsAllowed",
            "upstreamSignatureVerified", "reportGenerationSupported",
            "agentDispatchSupported")) and body["humanReviewRequired"] is True)
        keys, windows, finance_count = set(), set(), 0
        for source in body["sources"]:
            _need(type(source) is dict)
            domain = source.get("domain")
            _need(domain in {"netshop", "finance"}
                and set(source) == (FINANCE_FIELDS if domain == "finance"
                    else PROMOTION_FIELDS))
            key = source["sourceKey"]
            _need(type(key) is str and IDENTIFIER.fullmatch(key) is not None
                and key not in keys)
            keys.add(key)
            for field in ("queryDigest", "sourceRef", "terminalSegmentDigest",
                          "receiptChainDigest"):
                _need(_sha(source[field]))
            for field in ("sourceVersion", "pageCount", "segmentCount", "storedBytes"):
                _need(type(source[field]) is int and 1 <= source[field] <= 9_007_199_254_740_991)
            _need(type(source["rowCount"]) is int and 0 <= source["rowCount"] <= 9_007_199_254_740_991
                and source["segmentCount"] == (source["pageCount"] + 15) // 16)
            captured = _revision(source["sourceRevision"], domain)
            live = _revision(source["liveRevision"], domain)
            _need(captured is not None and live is not None and live >= captured)
            _need(live > captured or
                source["liveRevision"] == source["sourceRevision"],
                "v4封存正文同修订号的内容不得变化")
            status = source["revisionFreshness"]
            _need(status == ("current_revision" if
                source["liveRevision"] == source["sourceRevision"]
                else "historical_revision"))
            if domain == "finance":
                finance_count += 1
                scope, period = source["scope"], source["analysisPeriod"]
                _need(type(scope) is dict and set(scope) == {
                    "scope_key", "scope_type", "scope_name", "group_name"}
                    and type(period) is dict and set(period) == {
                        "startDate", "endDate"}
                    and type(source["missingMonths"]) is list
                    and len(source["missingMonths"]) <= 24
                    and len(set(source["missingMonths"])) == len(source["missingMonths"]))
            else:
                window = source["window"]
                _need(window in {"current", "previous", "yearAgo"}
                    and window not in windows and type(source["coverage"]) is dict)
                windows.add(window)
        _need(finance_count == 1 and "current" in windows
            and len(windows) == body["sourceCount"] - 1)
        return body
    except (UnicodeError, TypeError, ValueError, KeyError, RecursionError) as error:
        raise AnalysisContractError("v4封存正文无法安全解析") from error


def make(candidate, queries_by_source):
    """Build the exact 0038 body from one verified, unsealed admission."""
    _need(type(candidate) is dict and type(queries_by_source) is dict
        and candidate.get("schemaVersion") == ADMISSION_SCHEMA
        and candidate.get("sealed") is False
        and candidate.get("sourceRevisionWriteFencesVerified") is True
        and candidate.get("segmentedReceiptAndRequestProofVerified") is True
        and candidate.get("upstreamSignatureVerified") is False
        and candidate.get("reportGenerationSupported") is False
        and candidate.get("agentDispatchSupported") is False
        and candidate.get("crossDomainSnapshotAtomic") is False
        and candidate.get("financeDailyProrationAllowed") is False
        and candidate.get("inferSkuProfit") is False
        and candidate.get("sumOverlappingErpB2bAdsAllowed") is False)
    _need(candidate.get("candidateDigest") == digest({key: value for key, value
        in candidate.items() if key != "candidateDigest"}))
    sources = []
    for proof in candidate["sources"]:
        query = queries_by_source[proof["sourceKey"]]
        _need(digest(query) == proof["queryDigest"])
        item = {key: proof[key] for key in COMMON}
        if proof["domain"] == "finance":
            item.update(scope=query["scope"], analysisPeriod=query["analysisPeriod"],
                missingMonths=proof["missingMonths"])
        else:
            item.update(window=query["window"], coverage=proof["coverage"])
        sources.append(item)
    body = {"schemaVersion": SCHEMA, "runId": candidate["runId"],
        "attemptId": candidate["attemptId"],
        "evidenceVersion": candidate["runVersion"] + 1,
        "planDigest": candidate["planDigest"],
        "directoryDigest": candidate["directoryDigest"],
        "actorVersion": candidate["actorVersion"],
        "keyId": candidate["keyId"], "sourceCount": len(sources),
        "sources": sources, "crossDomainSnapshotAtomic": False,
        "financeDailyProrationAllowed": False, "inferSkuProfit": False,
        "sumOverlappingErpB2bAdsAllowed": False,
        "upstreamSignatureVerified": False,
        "reportGenerationSupported": False,
        "agentDispatchSupported": False, "humanReviewRequired": True}
    raw = canonical(body)
    read(raw)
    return {"body": body, "bodyJson": raw,
        "bodyDigest": hashlib.sha256(raw.encode("utf-8")).hexdigest()}
