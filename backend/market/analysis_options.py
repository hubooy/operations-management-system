"""Exact, live-authorized market option pages from the published metadata index."""
import hashlib
import json
from django.core import signing
from django.db import connection
from django.db.models import Q
from django.db.models.functions import Collate
from . import analysis_options_contract as contract
from .analysis_options_projection import current_principal, row_entry, state_snapshot, FIELDS
from .errors import MarketApiError
from .models import MarketAnalysisOption
from .revisions import canonical_json, revision_value

SALT = "market-analysis-options-v1"


def _digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_request(params):
    allowed = contract.QUERY_FIELDS | {"cursor", "limit"}
    if len(params) > len(allowed) or set(params) - allowed or any(len(params.getlist(key)) != 1 for key in params):
        raise MarketApiError("来源选项包含未知或重复参数")
    try:
        query = contract.normalize_query({key: params[key] for key in contract.QUERY_FIELDS if key in params})
        if "limit" in params and params["limit"] != "20":
            raise contract.OptionsContractError("每页固定20项")
        cursor = params.get("cursor")
        if cursor is not None:
            contract._text(cursor, contract.MAX_CURSOR_LENGTH)
    except contract.OptionsContractError as error:
        raise MarketApiError(str(error)) from error
    return query, cursor


def read_page(principal, query, cursor=None):
    actor = current_principal(principal)
    try:
        query = contract.normalize_query(query)
        if cursor is not None:
            contract._text(cursor, contract.MAX_CURSOR_LENGTH)
    except contract.OptionsContractError as error:
        raise MarketApiError(str(error)) from error
    before, state = revision_value(), state_snapshot()
    material = {"schemaVersion": "business-analysis-options-v1", "domain": "market", "actor": actor,
        "query": query, "revision": before, "generation": state["generation"],
        "directoryDigest": state["directory_digest"], "limit": 20}
    binding = _digest(material)
    last = None
    if cursor is not None:
        try:
            value = signing.loads(cursor, salt=SALT, max_age=3600)
            if type(value) is not dict or set(value) != {"binding", "last", "actor"}:
                raise ValueError()
            if value["actor"] != actor:
                raise MarketApiError("账号或权限版本已变化", code="access_denied", status=403)
            if value["binding"] != binding:
                raise ValueError()
            last = contract.normalize_identity(value["last"])
        except (signing.BadSignature, ValueError, TypeError, RecursionError) as error:
            raise MarketApiError("选项游标、来源或权限已变化，请从首页重新读取", code="options_revision_changed", status=409) from error
    values = MarketAnalysisOption.objects.all()
    mapping = {"category": "category", "scope": "scope", "rankingDimension": "ranking_dimension", "priceBandFilter": "price_band_filter"}
    # Collate exact identity fields for filters and keyset order, not aliases or
    # independent facet combinations. Literal contains is parameterized by ORM.
    collation = "C" if connection.vendor == "postgresql" else "BINARY"
    keys = [f"key_{i}" for i in range(4)]
    values = values.annotate(**{key: Collate(field, collation) for key, field in zip(keys, FIELDS)})
    for name, field in mapping.items():
        if name in query:
            values = values.filter(**{keys[FIELDS.index(field)]: query[name]})
    if "q" in query:
        values = values.annotate(search_exact=Collate("search_casefold", collation)).filter(search_exact__contains=query["q"].casefold())
    if last is not None:
        last_values = [last[name] for name in mapping]
        after = Q(pk__in=[])
        for index, key in enumerate(keys):
            clause = Q(**{f"{key}__gt": last_values[index]})
            for prior in range(index):
                clause &= Q(**{keys[prior]: last_values[prior]})
            after |= clause
        values = values.filter(after)
    selected = list(values.order_by(*keys).values("category", "scope", "ranking_dimension", "price_band_filter",
        "first_date", "last_date", "entry_json", "entry_digest", "search_casefold")[:21])
    entries = [row_entry(row) for row in selected[:20]]
    more = len(selected) > 20
    next_cursor = signing.dumps({"binding": binding, "actor": actor, "last": entries[-1]["identity"]}, salt=SALT, compress=True) if more else None
    try:
        result = contract.make_page(entries, revision=before, generation=state["generation"],
            directory_digest=state["directory_digest"], query=query, has_more=more, next_cursor=next_cursor)
    except contract.OptionsContractError as error:
        raise MarketApiError(str(error), code="invalid_source_metadata", status=413) from error
    # Authority only covers this owning index read; date/fact coverage remains false.
    result["authorityVerified"] = True
    result["limitations"][0] = "已复验当前账号与已完整初始化的历史元数据目录；不证明当前事实或所选日期完整。"
    result.pop("pageDigest")
    result["pageDigest"] = _digest(result)
    if len(canonical_json(result).encode("utf-8")) > contract.MAX_PAGE_BYTES:
        raise MarketApiError("完整来源页超过38000字节，未截断", code="payload_too_large", status=413)
    if actor != current_principal(principal):
        raise MarketApiError("读取期间账号或权限版本变化", code="access_denied", status=403)
    if before != revision_value() or state != state_snapshot():
        raise MarketApiError("目录在读取期间变化，请从首页重读", code="options_revision_changed", status=409)
    return result
