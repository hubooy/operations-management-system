"""Bounded exact identities from published batch metadata, never fact rows."""
from datetime import date
import json
import unicodedata

from django.core import signing
from django.db import connection
from django.db.models import Max, Min, Q
from django.db.models.functions import Collate

from business_analysis.contracts import digest
from .analysis import SOURCES
from .errors import NetshopApiError
from .models import NetshopImportBatch
from .query import revision_value

SCHEMA = "business-analysis-options-v1"
SALT = "netshop-analysis-options-v1"
PAGE_SIZE = 20
MAX_BYTES = 38000
MAX_CURSOR = 4096
KEY_FIELDS = ("platform", "shop_name", "source", "dataset")
SUPPORTED = {(platform, source, raw): name for name, platforms in SOURCES.items()
             for platform, (source, raw) in platforms.items()}


def _text(value, name, maximum):
    if (type(value) is not str or not value or value != value.strip() or len(value) > maximum
            or any(unicodedata.category(c) in {"Cc", "Cs"} for c in value)):
        raise NetshopApiError(f"{name}须为精确、非空的有界文本")
    return value


def validate_request(params):
    allowed = {"platform", "shop", "dataset", "q", "limit", "cursor"}
    if set(params) - allowed or any(len(params.getlist(key)) != 1 for key in params):
        raise NetshopApiError("来源选项包含未知或重复参数")
    query = {key: _text(params[key], key, 100) for key in ("platform", "shop", "dataset", "q") if key in params}
    if "platform" in query and query["platform"] not in {key[0] for key in SUPPORTED}:
        raise NetshopApiError("分析平台不支持", status=422, code="unsupported_source")
    if "shop" in query and "platform" not in query:
        raise NetshopApiError("店铺筛选必须同时指定精确平台")
    if "dataset" in query and (query["dataset"] not in SOURCES or
            "platform" in query and query["platform"] not in SOURCES[query["dataset"]]):
        raise NetshopApiError("分析数据集不支持", status=422, code="unsupported_source")
    if "limit" in params and params["limit"] != "20":
        raise NetshopApiError("来源选项每页固定20项")
    cursor = params.get("cursor")
    if cursor is not None:
        _text(cursor, "cursor", MAX_CURSOR)
    return query, cursor


def _date(value):
    if value in (None, ""):
        return None
    try:
        if type(value) is not str or len(value) != 10 or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError as error:
        raise NetshopApiError("已发布批次日期元数据无效", code="invalid_source_metadata", status=409) from error
    return value


def _identity(row):
    try:
        for key, maximum in zip(KEY_FIELDS, (100, 100, 64, 64)):
            _text(row[key], key, maximum)
    except NetshopApiError as error:
        raise NetshopApiError("已发布批次身份元数据无效", code="invalid_source_metadata", status=409) from error
    name = SUPPORTED[(row["platform"], row["source"], row["dataset"])]
    return {"platform": row["platform"], "shop": row["shop_name"], "dataset": name}


def read_page(principal, query, cursor=None):
    if principal.role != "admin" or principal.scope is not None:
        raise NetshopApiError("来源选项仅向无范围限制管理员开放", code="access_denied", status=403)
    # Internal callers share the exact same validation as the public endpoint.
    from django.http import QueryDict
    params = QueryDict(mutable=True)
    if type(query) is not dict or set(query) - {"platform", "shop", "dataset", "q"}:
        raise NetshopApiError("来源选项筛选无效")
    for key, value in query.items():
        if type(value) is not str:
            raise NetshopApiError("来源选项筛选必须为文本")
        params[key] = value
    if cursor is not None:
        params["cursor"] = _text(cursor, "cursor", MAX_CURSOR)
    query, cursor = validate_request(params)
    before = revision_value()
    authority = digest({"email": principal.email.lower(), "role": principal.role, "scope": principal.scope})
    binding = digest({"schemaVersion": SCHEMA, "query": query, "revision": before, "principal": authority, "limit": PAGE_SIZE})
    last = None
    if cursor:
        try:
            old = signing.loads(cursor, salt=SALT, max_age=3600)
        except (signing.BadSignature, ValueError, TypeError, RecursionError) as error:
            raise NetshopApiError("来源选项游标无效或过期", code="invalid_cursor", status=409) from error
        if (type(old) is not dict or set(old) != {"binding", "last"} or old["binding"] != binding
                or type(old["last"]) is not list or len(old["last"]) != 4
                or any(type(value) is not str for value in old["last"])):
            raise NetshopApiError("来源目录、账号或筛选已变化，请从首页重读", code="options_revision_changed", status=409)
        last = old["last"]
        for key, value, maximum in zip(KEY_FIELDS, last, (100, 100, 64, 64)):
            _text(value, key, maximum)
        if (last[0], last[2], last[3]) not in SUPPORTED:
            raise NetshopApiError("来源选项游标身份无效", code="invalid_cursor", status=409)
    supported = Q(pk__in=[])
    for (platform, source, raw), dataset in SUPPORTED.items():
        if "dataset" not in query or query["dataset"] == dataset:
            supported |= Q(platform=platform, source=source, dataset=raw)
    rows = NetshopImportBatch.objects.filter(supported, status="completed").exclude(shop_name="")
    for key, field in (("platform", "platform"), ("shop", "shop_name")):
        if key in query:
            rows = rows.filter(**{field: query[key]})
    if "q" in query:
        rows = rows.filter(Q(platform__icontains=query["q"]) | Q(shop_name__icontains=query["q"]))
    collation = "C" if connection.vendor == "postgresql" else "BINARY"
    order = [f"key_{i}" for i in range(4)]
    rows = rows.annotate(**{key: Collate(field, collation) for key, field in zip(order, KEY_FIELDS)})
    if last:
        after = Q(pk__in=[])
        for index, key in enumerate(order):
            part = Q(**{f"{key}__gt": last[index]})
            for previous in range(index):
                part &= Q(**{order[previous]: last[previous]})
            after |= part
        rows = rows.filter(after)
    # LIMIT applies to grouped identities in the database, not a Python scan.
    grouped = list(rows.values(*KEY_FIELDS, *order).annotate(first_date=Min("date_min"),
        last_date=Max("date_max"), snapshot=Max("snapshot_date")).order_by(*order)[:PAGE_SIZE+1])
    items = []
    for row in grouped[:PAGE_SIZE]:
        identity = _identity(row)
        first, end, snapshot = (_date(row[key]) for key in ("first_date", "last_date", "snapshot"))
        if first and end and first > end:
            raise NetshopApiError("批次日期包络倒置", code="invalid_source_metadata", status=409)
        if identity["dataset"] == "master":
            first, end = None, None
        items.append({"optionKey": digest({"domain": "netshop", "identity": identity}), "identity": identity,
            "source": row["source"], "sourceDataset": row["dataset"],
            "dateMetadata": {"kind": "published_import_envelope", "firstDate": first, "lastDate": end,
                "snapshotDate": snapshot, "coverageVerified": False},
            "provenance": {"kind": "completed_import_metadata", "revision": before, "meaning": "historically_published_not_current_fact_coverage"}})
    more = len(grouped) > PAGE_SIZE
    result = {"schemaVersion": SCHEMA, "domain": "netshop", "revision": before,
        "query": query, "queryDigest": digest(query), "items": items,
        "pagination": {"returned": len(items), "limit": PAGE_SIZE, "hasMore": more,
            "nextCursor": signing.dumps({"binding": binding, "last": [grouped[PAGE_SIZE-1][key] for key in KEY_FIELDS]}, salt=SALT) if more else None},
        "limitations": ["仅列已完成导入中出现的精确来源身份，不代表当前事实仍存在或所选日期完整。",
            "日期仅为已发布批次元数据包络，缺日、结算及真实覆盖须采集后核验。",
            "商品主数据快照不证明历史商品归属；未列身份不等于没有经营活动。"]}
    result["pageDigest"] = digest(result)
    if len(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")) > MAX_BYTES:
        raise NetshopApiError("完整来源选项页超过容量，未截断身份", code="payload_too_large", status=413)
    if revision_value() != before:
        raise NetshopApiError("来源目录在读取期间变化，请重新读取", code="options_revision_changed", status=409)
    return result
