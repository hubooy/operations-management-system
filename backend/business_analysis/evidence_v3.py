"""Pure mixed daily/monthly evidence directory; no collector or authority.

Daily entries retain the v2 query contract. Finance entries describe exact
natural-month context, not daily facts or a transferable owning-reader proof.
The old v1/v2 contracts and their existing digest bytes are never rewritten.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import unicodedata

from . import evidence_v2, finance_source
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest

HEADER_SCHEMA = "business-evidence-v3"
PAGE_SCHEMA = "business-evidence-directory-page-v3"
CATALOG_SCHEMA = "business-evidence-directory-v3"
CAPACITY_PROFILE = "catalog-48-facts-monthly-context-v1"
MAX_SOURCES = evidence_v2.MAX_SOURCES
MAX_QUERY_BYTES = evidence_v2.MAX_QUERY_BYTES
MAX_DIRECTORY_QUERY_BYTES = evidence_v2.MAX_DIRECTORY_QUERY_BYTES
MAX_HEADER_BYTES = evidence_v2.MAX_HEADER_BYTES
MAX_DIRECTORY_PAGE_BYTES = evidence_v2.MAX_DIRECTORY_PAGE_BYTES
FINANCE_POLICY = {
    "temporalRole": "monthly_context", "missingMonthsAreGaps": True,
    "dailyProrationAllowed": False, "sumSourceRates": False,
    "sumTotalsAndDetails": False, "skuProfitAttributionAllowed": False,
    "crossSourceAmountAdditivityVerified": False, "shopIdentityMappingVerified": False,
}


def _require(condition, message):
    if not condition:
        raise AnalysisContractError(message)


def _fields(value, names, label):
    _require(type(value) is dict and set(value) == set(names), label + "字段集合无效")


def _month_ordinal(value):
    return finance_source._month(value)


def _touched_months(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    begin, stop = first.year * 12 + first.month - 1, last.year * 12 + last.month - 1
    return set(range(begin, stop + 1))


def _finance_query(value, daily_dates):
    _fields(value, {"months", "scope", "analysisPeriod"}, "财报来源查询")
    normalized = finance_source._query({key: value[key] for key in ("months", "scope")})
    alignment = finance_source._period(normalized, value["analysisPeriod"])
    _require(alignment["analysisPeriod"] == {"startDate": daily_dates[0], "endDate": daily_dates[1]},
             "财报上下文日期必须等于日来源原始分析区间")
    selected = {_month_ordinal(month) for month in normalized["months"]}
    _require(_touched_months(*daily_dates) <= selected,
             "财报自然月查询必须显式包含日区间涉及的全部月份")
    for field in ("scope_key", "scope_type", "scope_name", "group_name"):
        value = normalized["scope"][field]
        _require(not any(unicodedata.category(char) in {"Cc", "Cs"} for char in value),
                 "财报精确范围含无效控制字符")
    query = {**normalized, "analysisPeriod": dict(alignment["analysisPeriod"])}
    evidence_v2._bounded(query, MAX_QUERY_BYTES, "单财报来源查询")
    return query, alignment


def normalize_sources(sources, *, analysis_request):
    """Rebuild exact source identities and return a bounded, ordered catalog.

    The finance descriptor does not assert a month is published or any source
    exists. Such facts can only come from the future owning collection path.
    """
    _require(type(sources) is list and 2 <= len(sources) <= MAX_SOURCES,
             "v3目录须有日来源和财报来源，总数最多48")
    daily, finance = [], []
    for raw in sources:
        _fields(raw, {"key", "domain", "query"}, "来源")
        evidence_v2._identifier(raw["key"], "来源key")
        _require(type(raw["domain"]) is str, "来源域无效")
        (finance if raw["domain"] == "finance" else daily).append(raw)
    _require(bool(daily) and bool(finance), "v3目录必须同时包含日来源与财报上下文")
    # One actual v2 catalog validates daily identity, windows, combinations,
    # original dates and requested-window completeness without copying its code.
    daily_catalog = evidence_v2.build_catalog(daily, analysis_request=analysis_request)
    daily_entries = daily_catalog["entries"]
    daily_query = daily_entries[0]["query"]
    daily_dates = (daily_query["startDate"], daily_query["endDate"])
    entries = [{"key": item["key"], "domain": item["domain"], "query": item["query"],
                "queryDigest": item["queryDigest"], "temporalRole": "daily_fact"}
               for item in daily_entries]
    keys = {entry["key"] for entry in entries}
    identities = {digest({"domain": entry["domain"], "query": entry["query"]}) for entry in entries}
    query_bytes = sum(len(canonical(entry["query"]).encode("utf-8")) for entry in entries)
    for raw in finance:
        key = raw["key"]
        _require(key not in keys, "财报来源key重复")
        query, alignment = _finance_query(raw["query"], daily_dates)
        identity = digest({"domain": "finance", "query": query})
        _require(identity not in identities, "财报精确事实身份重复，不得合并")
        keys.add(key)
        identities.add(identity)
        query_bytes += len(canonical(query).encode("utf-8"))
        _require(query_bytes <= MAX_DIRECTORY_QUERY_BYTES, "来源目录查询总字节超限")
        entries.append({"key": key, "domain": "finance", "query": query,
                        "queryDigest": digest(query), "temporalRole": "monthly_context",
                        "periodAlignment": alignment})
    entries.sort(key=lambda item: canonical({"domain": item["domain"], "query": item["query"], "key": item["key"]}))
    return [{"ordinal": index, **entry} for index, entry in enumerate(entries, 1)]


def build_catalog(trusted_sources, *, analysis_request):
    """Construct a prospective plan, never an authorized or sealed result."""
    entries = normalize_sources(trusted_sources, analysis_request=analysis_request)
    metadata = evidence_v2.validate_analysis_request(analysis_request)
    catalog_digest = digest({"schemaVersion": CATALOG_SCHEMA, "entries": entries})
    header = {"schemaVersion": HEADER_SCHEMA, "sourceCount": len(entries),
              "catalogDigest": catalog_digest, "capacityProfile": CAPACITY_PROFILE,
              "collector": {"version": 2, "surface": "business_collection", "pageSize": 100},
              "limits": {"factBytes": 64 * 1024 * 1024, "factPages": 2000},
              "analysisRequest": metadata, "dailyContractSchema": evidence_v2.HEADER_SCHEMA,
              "financeSourceSchema": finance_source.SCHEMA, "financePolicy": dict(FINANCE_POLICY),
              "reportGenerationSupported": False, "sourceAuthorityVerified": False,
              "persistentEvidenceVerified": False, "businessCoverageVerified": False,
              "modelAnalysisCompleted": False}
    evidence_v2._bounded(header, MAX_HEADER_BYTES, "v3证据header")
    return {"header": header, "planDigest": digest(header), "entries": entries}


def validate_header(actual, trusted_sources, *, analysis_request):
    expected = build_catalog(trusted_sources, analysis_request=analysis_request)["header"]
    _require(evidence_v2._same(actual, expected), "v3证据header与可信来源不一致")
    return deepcopy(expected)


def directory_page(trusted_sources, *, run_id, evidence_version, offset=0, limit=10, analysis_request):
    catalog = build_catalog(trusted_sources, analysis_request=analysis_request)
    evidence_v2._identifier(run_id, "证据ID")
    evidence_v2._integer(evidence_version, 1, MAX_SAFE_INTEGER, "证据版本")
    evidence_v2._integer(offset, 0, len(catalog["entries"]) - 1, "目录偏移")
    evidence_v2._integer(limit, 1, 20, "目录页长")

    def assemble(items):
        end = offset + len(items)
        value = {"schemaVersion": PAGE_SCHEMA, "runId": run_id,
                 "evidenceVersion": evidence_version, "planDigest": catalog["planDigest"],
                 "catalogDigest": catalog["header"]["catalogDigest"], "offset": offset,
                 "requestedLimit": limit, "total": len(catalog["entries"]), "returned": len(items),
                 "nextOffset": end if end < len(catalog["entries"]) else None, "items": items}
        return {**value, "pageDigest": digest(value)}

    items = []
    for entry in catalog["entries"][offset:offset + limit]:
        trial = assemble([*items, entry])
        if len(canonical(trial).encode("utf-8")) > MAX_DIRECTORY_PAGE_BYTES:
            _require(bool(items), "单目录来源超过页面字节容量，不得截断")
            break
        items.append(entry)
    return assemble(items)


def validate_directory_pages(pages, trusted_sources, *, run_id, evidence_version, analysis_request):
    catalog = build_catalog(trusted_sources, analysis_request=analysis_request)
    frozen = [{key: deepcopy(item[key]) for key in ("key", "domain", "query")} for item in catalog["entries"]]
    request = deepcopy(catalog["header"]["analysisRequest"])
    evidence_v2._identifier(run_id, "证据ID")
    evidence_v2._integer(evidence_version, 1, MAX_SAFE_INTEGER, "证据版本")
    offset, page_count = 0, 0
    for page in pages:
        _require(page_count < MAX_SOURCES and offset < len(frozen), "目录页重复或超出完整范围")
        _require(type(page) is dict, "目录页结构无效")
        limit = evidence_v2._integer(page.get("requestedLimit"), 1, 20, "目录页长")
        expected = directory_page(frozen, run_id=run_id, evidence_version=evidence_version,
                                  offset=offset, limit=limit, analysis_request=request)
        _require(evidence_v2._same(page, expected), "目录页与可信来源、顺序或绑定不一致")
        offset += expected["returned"]
        page_count += 1
    _require(offset == len(frozen), "目录页缺失，不能声明已完整读取")
    return {"complete": True, "sourceCount": offset, "pageCount": page_count,
            "catalogDigest": catalog["header"]["catalogDigest"], "planDigest": catalog["planDigest"]}
