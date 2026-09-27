"""Pure candidate catalog contract; no persistence, collection or model dispatch.

Trusted source arguments must come from the caller's authoritative request or
immutable directory. Hashes supplied inside a page are never trust anchors.
Fact capacity remains 64 MiB / 2000 pages; 48 is a directory count only.
"""
from copy import deepcopy
import re
import unicodedata

from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, comparison_periods, digest
from .planning import NETSHOP_SOURCES, WINDOWS, validate_analysis_request


MAX_SOURCES = 48
MAX_QUERY_BYTES = 4096
MAX_DIRECTORY_QUERY_BYTES = 128 * 1024
MAX_HEADER_BYTES = 16000
MAX_WORKFLOW_BYTES = 8000
MAX_DIRECTORY_PAGE_BYTES = 38000
CAPACITY_PROFILE = "catalog-48-facts-v1"
HEADER_SCHEMA = "business-evidence-v2"
PAGE_SCHEMA = "business-evidence-directory-page-v2"


def _fields(value, fields, name):
    if type(value) is not dict or len(value) != len(fields) or set(value) != set(fields):
        raise AnalysisContractError(name+"字段集合无效")


def _integer(value, lo, hi, name):
    if type(value) is not int or not lo <= value <= hi:
        raise AnalysisContractError(name+"整数范围无效")
    return value


def _text(value, maximum, name):
    if type(value) is not str or not value or len(value) > maximum or value != value.strip():
        raise AnalysisContractError(name+"须为有界精确文本")
    if any(unicodedata.category(c) in {"Cc", "Cs"} for c in value):
        raise AnalysisContractError(name+"含无效控制字符")
    return value


def _identifier(value, name):
    value = _text(value, 160, name)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise AnalysisContractError(name+"格式无效")
    return value


def _sha(value, name):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise AnalysisContractError(name+"须为小写SHA256")
    return value


def _bounded(value, maximum, name):
    if len(canonical(value).encode("utf-8")) > maximum:
        raise AnalysisContractError(name+"超过字节容量，不得截断")
    return value


def normalize_sources(sources):
    """Return deterministic ordered directory entries, rejecting duplicate facts.

Domain support is the current pure planning contract, not proof of source data
availability or owning-reader authorization. Inputs are copied, not mutated.
"""
    if type(sources) is not list or not 1 <= len(sources) <= MAX_SOURCES:
        raise AnalysisContractError("v2目录来源数量须为1—48；不代表事实容量扩大")
    entries, keys, identities, dates, query_bytes = [], set(), set(), set(), 0
    for source in sources:
        _fields(source, {"key", "domain", "query"}, "来源")
        key = _identifier(source["key"], "来源key")
        domain = source["domain"]
        if type(domain) is not str or domain not in {"sales", "netshop", "market"}:
            raise AnalysisContractError("来源域无效")
        query = source["query"]
        required = {"platform", "startDate", "endDate"} | (
            {"category", "scope", "rankingDimension", "priceBandFilter"} if domain == "market"
            else {"shop", "channel" if domain == "sales" else "dataset"})
        if type(query) is not dict or len(query) not in (len(required), len(required)+1) or set(query) not in (required, required | {"window"}):
            raise AnalysisContractError("来源查询字段集合无效")
        for name, value in query.items():
            _text(value, 200 if name in {"category", "scope", "priceBandFilter"} else 100, "来源"+name)
        result = dict(query)
        result.setdefault("window", "current")
        comparison_periods(result["startDate"], result["endDate"])
        if result["window"] not in WINDOWS:
            raise AnalysisContractError("来源窗口无效")
        if domain == "netshop":
            if result["platform"] not in NETSHOP_SOURCES.get(result["dataset"], ()):
                raise AnalysisContractError("网店规范来源组合不支持")
            if result["dataset"] == "master" and result["window"] != "current":
                raise AnalysisContractError("当前主数据不能声明历史快照")
        if domain == "market" and (result["platform"] != "京东" or result["rankingDimension"] not in {"SKU", "SPU"}):
            raise AnalysisContractError("市场规范来源组合不支持")
        _bounded(result, MAX_QUERY_BYTES, "单来源查询")
        query_bytes += len(canonical(result).encode("utf-8"))
        if query_bytes > MAX_DIRECTORY_QUERY_BYTES:
            raise AnalysisContractError("来源目录查询总字节超限")
        identity = digest({"domain": domain, "query": result})
        if key in keys or identity in identities:
            raise AnalysisContractError("来源key或精确事实身份重复，不得合并")
        keys.add(key)
        identities.add(identity)
        dates.add((result["startDate"], result["endDate"]))
        entries.append({"key": key, "domain": domain, "query": result, "queryDigest": digest(result)})
    if len(dates) != 1:
        raise AnalysisContractError("来源须使用同一原始比较区间")
    entries.sort(key=lambda item: canonical({"domain": item["domain"], "query": item["query"], "key": item["key"]}))
    return [{"ordinal": i+1, **entry} for i, entry in enumerate(entries)]


def build_catalog(trusted_sources, *, analysis_request=None):
    """Rebuild a compact header and full bounded catalog from trusted sources."""
    entries = normalize_sources(trusted_sources)
    metadata = validate_analysis_request(analysis_request) if analysis_request is not None else None
    if metadata is not None:
        expected, by_identity = set(metadata["requestedWindows"]), {}
        for entry in entries:
            query = entry["query"]
            if entry["domain"] == "netshop" and query.get("dataset") == "master":
                continue
            identity = digest({"domain": entry["domain"], "query": {k: v for k, v in query.items() if k != "window"}})
            by_identity.setdefault(identity, set()).add(query["window"])
        if any(windows != expected for windows in by_identity.values()):
            raise AnalysisContractError("目录未完整且仅安排请求的比较窗口")
    catalog_digest = digest({"schemaVersion": "business-evidence-directory-v2", "entries": entries})
    header = {"schemaVersion": HEADER_SCHEMA, "sourceCount": len(entries), "catalogDigest": catalog_digest,
        "capacityProfile": CAPACITY_PROFILE, "collector": {"version": 1, "surface": "business_collection", "pageSize": 100},
        "limits": {"factBytes": 64*1024*1024, "factPages": 2000},
        **({"analysisRequest": metadata} if metadata is not None else {})}
    _bounded(header, MAX_HEADER_BYTES, "证据header")
    return {"header": header, "planDigest": digest(header), "entries": entries}


def workflow_reference(trusted_sources, *, run_id, evidence_version, sealed_digest, question, analysis_request=None):
    """Build <=8k input; sealed_digest must be the caller's verified seal digest.

This pure function checks its representation, not whether facts are sealed.
"""
    catalog = build_catalog(trusted_sources, analysis_request=analysis_request)
    normalized_question = validate_analysis_request({"schemaVersion": "business-analysis-request-v1", "question": question,
        "requestedDimensions": ["shop"], "requestedWindows": ["current"]})["question"]
    result = {"inputMode": "reference-v2", "evidenceRunId": _identifier(run_id, "证据ID"),
        "evidenceVersion": _integer(evidence_version, 1, MAX_SAFE_INTEGER, "证据版本"),
        "evidencePlanDigest": catalog["planDigest"], "catalogDigest": catalog["header"]["catalogDigest"],
        "sealedDigest": _sha(sealed_digest, "封存摘要"), "sourceCount": len(catalog["entries"]), "question": normalized_question}
    return _bounded(result, MAX_WORKFLOW_BYTES, "轻量工作流输入")


def directory_page(trusted_sources, *, run_id, evidence_version, offset=0, limit=10, analysis_request=None):
    """Make a byte-bounded complete prefix; nextOffset uses actual returned rows."""
    catalog = build_catalog(trusted_sources, analysis_request=analysis_request)
    _identifier(run_id, "证据ID")
    _integer(evidence_version, 1, MAX_SAFE_INTEGER, "证据版本")
    _integer(offset, 0, len(catalog["entries"])-1, "目录偏移")
    _integer(limit, 1, 20, "目录页长")
    def assemble(items):
        end = offset+len(items)
        page = {"schemaVersion": PAGE_SCHEMA, "runId": run_id, "evidenceVersion": evidence_version,
            "planDigest": catalog["planDigest"], "catalogDigest": catalog["header"]["catalogDigest"],
            "offset": offset, "requestedLimit": limit, "total": len(catalog["entries"]), "returned": len(items),
            "nextOffset": end if end < len(catalog["entries"]) else None, "items": items}
        return {**page, "pageDigest": digest(page)}
    items = []
    for entry in catalog["entries"][offset:offset+limit]:
        trial = assemble([*items, entry])
        if len(canonical(trial).encode("utf-8")) > MAX_DIRECTORY_PAGE_BYTES:
            if not items:
                raise AnalysisContractError("单目录来源超过页面字节容量，不得截断")
            break
        items.append(entry)
    return assemble(items)


def _same(actual, expected):
    # Visit only the rebuilt trusted shape; do not serialize attacker nesting.
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return len(actual) == len(expected) and all(k in actual and _same(actual[k], v) for k, v in expected.items())
    if type(expected) is list:
        return len(actual) == len(expected) and all(_same(a, b) for a, b in zip(actual, expected))
    if type(expected) is str:
        return len(actual) == len(expected) and actual == expected
    return actual == expected


def validate_header(actual, trusted_sources, *, analysis_request=None):
    expected = build_catalog(trusted_sources, analysis_request=analysis_request)["header"]
    if not _same(actual, expected):
        raise AnalysisContractError("证据header与可信目录不一致")
    return deepcopy(expected)


def validate_directory_pages(pages, trusted_sources, *, run_id, evidence_version, analysis_request=None):
    """Verify an entire ordered page stream against independently rebuilt facts.

An empty/missing/replayed/reordered stream fails even when attacker recomputes
all page hashes. The trusted caller must supply the expected run/version too.
"""
    catalog = build_catalog(trusted_sources, analysis_request=analysis_request)
    # Freeze the trusted material before advancing any externally supplied
    # iterator; an iterator may otherwise mutate its caller's source list.
    frozen_sources = [{k: deepcopy(entry[k]) for k in ("key", "domain", "query")} for entry in catalog["entries"]]
    frozen_request = deepcopy(catalog["header"].get("analysisRequest"))
    _identifier(run_id, "证据ID")
    _integer(evidence_version, 1, MAX_SAFE_INTEGER, "证据版本")
    offset, page_count = 0, 0
    for page in pages:
        if page_count >= MAX_SOURCES or offset >= len(catalog["entries"]):
            raise AnalysisContractError("目录页重复或超过完整范围")
        if type(page) is not dict:
            raise AnalysisContractError("目录页结构无效")
        limit = _integer(page.get("requestedLimit"), 1, 20, "目录页长")
        expected = directory_page(frozen_sources, run_id=run_id, evidence_version=evidence_version,
            offset=offset, limit=limit, analysis_request=frozen_request)
        if not _same(page, expected):
            raise AnalysisContractError("目录页与可信来源、顺序或绑定不一致")
        offset += expected["returned"]
        page_count += 1
    if offset != len(catalog["entries"]):
        raise AnalysisContractError("目录页缺失，不能声明已完整读取")
    return {"complete": True, "sourceCount": offset, "pageCount": page_count,
        "catalogDigest": catalog["header"]["catalogDigest"], "planDigest": catalog["planDigest"]}
