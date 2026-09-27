"""Bounded, non-authoritative market option DTOs; no Django, I/O or readiness.

Inputs must originate from a completed batch's normalized scope. A status string
cannot prove publication. Future owning code must verify publication, generation,
revision, permissions and cursor signatures independently.
"""
from datetime import date
import hashlib
import json
import re
import unicodedata

MAX_BATCH_RANGES = 5_000
MAX_IDENTITIES = 10_000
MAX_DIRECTORY_BYTES = 16 * 1024 * 1024
PAGE_SIZE = 20
MAX_PAGE_BYTES = 38_000
MAX_CURSOR_LENGTH = 1_600
IDENTITY_FIELDS = ("platform", "category", "scope", "rankingDimension", "priceBandFilter")
RANGE_FIELDS = frozenset(IDENTITY_FIELDS[1:]) | {"periodStart", "periodEnd"}
ENTRY_FIELDS = {"identity", "firstDate", "lastDate"}
QUERY_FIELDS = frozenset(IDENTITY_FIELDS) | {"q"}
HEX = re.compile(r"[0-9a-f]{64}\Z")


class OptionsContractError(ValueError):
    """Invalid or over-capacity metadata; never interpret this as an empty source."""


def _object(value, fields):
    if type(value) is not dict or len(value) != len(fields) or set(value) != set(fields):
        raise OptionsContractError("字段集合无效")


def _text(value, maximum=200):
    if (type(value) is not str or not value or len(value) > maximum
            or value.strip() != value
            or any(unicodedata.category(char) in {"Cc", "Cs"} for char in value)):
        raise OptionsContractError("须为精确、非空、有界文本")
    return value


def _date(value):
    _text(value, 10)
    try:
        if len(value) != 10 or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError as error:
        raise OptionsContractError("批次日期不是有效 YYYY-MM-DD") from error
    return value


def _json(value):
    # Only fixed-shape, validated scalar DTOs reach serialization.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _hex(value):
    if type(value) is not str or len(value) != 64 or not HEX.fullmatch(value):
        raise OptionsContractError("摘要无效")
    return value


def normalize_identity(value):
    _object(value, IDENTITY_FIELDS)
    result = {key: _text(value[key]) for key in IDENTITY_FIELDS}
    if result["platform"] != "京东" or result["rankingDimension"] not in ("SKU", "SPU"):
        raise OptionsContractError("市场平台或榜单维度不支持")
    return result


def _key(identity):
    # Unicode scalar ordering equals UTF-8 C/BINARY ordering (surrogates rejected).
    return tuple(identity[field] for field in IDENTITY_FIELDS)


def _entry(value):
    _object(value, ENTRY_FIELDS)
    result = {"identity": normalize_identity(value["identity"]),
              "firstDate": _date(value["firstDate"]), "lastDate": _date(value["lastDate"])}
    if result["firstDate"] > result["lastDate"]:
        raise OptionsContractError("日期包络倒置")
    return result


def _entries(values, maximum=MAX_IDENTITIES):
    if type(values) is not list or len(values) > maximum:
        raise OptionsContractError("身份数量超过完整目录容量")
    result, previous, byte_count = [], None, 2
    for value in values:
        item = _entry(value)
        key = _key(item["identity"])
        if previous is not None and key <= previous:
            raise OptionsContractError("身份重复或未按完整身份排序")
        byte_count += len(_json(item).encode("utf-8")) + bool(result)
        if byte_count > MAX_DIRECTORY_BYTES:
            raise OptionsContractError("完整目录规范载荷超过字节容量")
        result.append(item)
        previous = key
    return result


def normalize_completed_scope(scope, *, status):
    """Validate ALL normalized ranges, then return daily identity envelopes.

    A valid weekly/monthly-only scope yields zero entries with explicit excluded
    counts. Missing/empty/malformed historical scopes always raise instead.
    """
    if type(status) is not str or status != "completed":
        raise OptionsContractError("只接受已完成批次元数据")
    _object(scope, {"sourceType", "ranges"})
    _text(scope["sourceType"], 64)  # Owning import accepts an exact, nonempty sourceType.
    ranges = scope["ranges"]
    if type(ranges) is not list or not 1 <= len(ranges) <= MAX_BATCH_RANGES:
        raise OptionsContractError("成功批次须包含1—5000项完整范围")
    grouped, previous, excluded = {}, None, 0
    for raw in ranges:
        _object(raw, RANGE_FIELDS)
        identity = normalize_identity({"platform": "京东", **{key: raw[key] for key in IDENTITY_FIELDS[1:]}})
        start, end = _date(raw["periodStart"]), _date(raw["periodEnd"])
        if start > end:
            raise OptionsContractError("批次周期倒置")
        normalized = {**{key: identity[key] for key in IDENTITY_FIELDS[1:]}, "periodStart": start, "periodEnd": end}
        ordering = _json(normalized)
        if previous is not None and ordering <= previous:
            raise OptionsContractError("范围重复或并非导入规范顺序")
        previous = ordering
        if start != end:
            excluded += 1
            continue
        key = _key(identity)
        old = grouped.get(key)
        grouped[key] = {"identity": identity, "firstDate": min(start, old["firstDate"]) if old else start,
                        "lastDate": max(end, old["lastDate"]) if old else end}
    return {"authorityVerified": False, "entries": _entries([grouped[key] for key in sorted(grouped)]),
            "rangeCount": len(ranges), "excludedNonDailyRanges": excluded}


def merge_entries(existing, incoming):
    """Complete deterministic union; no truncation, shared mutable aliases or I/O."""
    left, right = _entries(existing), _entries(incoming)
    combined = {_key(item["identity"]): item for item in left}
    for item in right:
        key = _key(item["identity"])
        if key in combined:
            old = combined[key]
            old["firstDate"] = min(old["firstDate"], item["firstDate"])
            old["lastDate"] = max(old["lastDate"], item["lastDate"])
        else:
            if len(combined) >= MAX_IDENTITIES:
                raise OptionsContractError("身份数量超过完整目录容量")
            combined[key] = item
    return _entries([combined[key] for key in sorted(combined)])


def directory_digest(entries):
    """Hash a validated complete DTO list; this hash is not an authority proof."""
    return _digest(_entries(entries))


def normalize_query(query):
    if type(query) is not dict or len(query) > len(QUERY_FIELDS) or set(query) - QUERY_FIELDS:
        raise OptionsContractError("查询字段无效")
    result = {key: _text(value, 100 if key == "q" else 200) for key, value in query.items()}
    if "platform" in result and result["platform"] != "京东":
        raise OptionsContractError("平台不支持")
    if "rankingDimension" in result and result["rankingDimension"] not in ("SKU", "SPU"):
        raise OptionsContractError("榜单维度不支持")
    return result


def make_page(entries, *, revision, generation, directory_digest, query, has_more, next_cursor):
    """Build an untrusted page DTO, never sign a cursor or declare a ready index.

    Caller supplies an already selected page, at most 20 complete identities. The
    entire response, including digest/provenance/cursor, must fit 38000 UTF-8 bytes.
    """
    revision, generation = _text(revision, 160), _text(generation, 160)
    directory_digest = _hex(directory_digest)
    query = normalize_query(query)
    selected = _entries(entries, PAGE_SIZE)
    if type(has_more) is not bool:
        raise OptionsContractError("hasMore须为布尔值")
    if has_more:
        if len(selected) != PAGE_SIZE:
            raise OptionsContractError("有下一页时必须提供完整20项")
        _text(next_cursor, MAX_CURSOR_LENGTH)
    elif next_cursor is not None:
        raise OptionsContractError("末页不能携带下一游标")
    items = []
    for entry in selected:
        identity = entry["identity"]
        if any(identity[key] != value for key, value in query.items() if key != "q"):
            raise OptionsContractError("返回身份与精确筛选不符")
        if "q" in query and not any(query["q"].casefold() in value.casefold() for value in identity.values()):
            raise OptionsContractError("返回身份与搜索条件不符")
        items.append({"optionKey": _digest({"domain": "market", "identity": identity}), "identity": identity,
                      "source": "market_daily_top", "sourceDataset": "market_daily_top",
                      "dateMetadata": {"kind": "published_import_envelope", "firstDate": entry["firstDate"],
                                       "lastDate": entry["lastDate"], "snapshotDate": None, "coverageVerified": False},
                      "provenance": {"kind": "completed_import_metadata", "revision": revision,
                                     "generation": generation, "directoryDigest": directory_digest,
                                     "meaning": "historically_published_not_current_fact_coverage"}})
    result = {"schemaVersion": "business-analysis-options-v1", "domain": "market", "authorityVerified": False,
              "revision": revision, "directoryGeneration": generation, "directoryDigest": directory_digest,
              "query": query, "queryDigest": _digest(query), "items": items,
              "pagination": {"returned": len(items), "limit": PAGE_SIZE, "hasMore": has_more, "nextCursor": next_cursor},
              "limitations": ["纯合同页不证明批次、目录就绪、账号权限或游标签名；须由实际领域服务核验。",
                              "日期仅为历史已发布单日批次包络，不证明当前事实存在、缺日或完整市场覆盖。",
                              "价格筛选保留源榜单原值；市场TOP样本不代表店铺归属或全行业规模。"]}
    result["pageDigest"] = _digest(result)
    if len(_json(result).encode("utf-8")) > MAX_PAGE_BYTES:
        raise OptionsContractError("完整来源页超过38000字节，未截断")
    return result
