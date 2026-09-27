"""Pure candidate ERP source-option DTOs. No Django, I/O, auth or readiness.

Inputs are grouped current SalesOrderLine projections, not import scope metadata.
The owning reader must enforce is_business_row and verify live authorization,
revision, SQL limits and signed cursors. A valid DTO proves none of those facts.
"""
from datetime import date
import hashlib
import json
import re
import unicodedata

PAGE_SIZE = 20
MAX_PAGE_BYTES = 38_000
MAX_CURSOR_LENGTH = 1_600
MAX_SAFE_INTEGER = 9_007_199_254_740_991
IDENTITY_FIELDS = ("platform", "shop", "channel")
GROUP_FIELDS = {"platform", "shop_name", "channel", "platform_key", "shop_key", "channel_key", "firstDate", "lastDate", "rowCount"}


class OptionsContractError(ValueError):
    pass


def _fields(value, required, optional=()):
    if type(value) is not dict or len(value) > len(required) + len(optional) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise OptionsContractError("字段集合无效")


def _text(value, maximum=200):
    if type(value) is not str or not 1 <= len(value) <= maximum or value != value.strip() or any(unicodedata.category(c) in {"Cc", "Cs"} for c in value):
        raise OptionsContractError("须提供有界、原始精确身份，不补空值或别名")
    return value


def _date(value):
    _text(value, 10)
    try:
        if len(value) != 10 or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError as error:
        raise OptionsContractError("日期必须为真实 YYYY-MM-DD") from error
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def normalize_identity(value):
    _fields(value, IDENTITY_FIELDS)
    return {key: _text(value[key]) for key in IDENTITY_FIELDS}


def normalize_query(value):
    """Exact optional filters only. No q/casefold or inferred platform aliases."""
    _fields(value, (), IDENTITY_FIELDS)
    result = {key: _text(value[key]) for key in IDENTITY_FIELDS if key in value}
    if "shop" in result and "platform" not in result:
        raise OptionsContractError("店铺筛选须同时提供精确平台")
    return result


def normalize_group(value):
    """Check one complete raw+projection group supplied by a trusted SQL reader.

    No arbitrary raw rows or successful batch scopes can substitute for a group.
    is_business_row must be enforced by owning SQL; it is not self-certified here.
    """
    _fields(value, GROUP_FIELDS)
    identity = normalize_identity({"platform": value["platform"], "shop": value["shop_name"], "channel": value["channel"]})
    for key in IDENTITY_FIELDS:
        if type(value[key+"_key"]) is not str or value[key+"_key"] != identity[key]:
            raise OptionsContractError("原始身份与查询投影不相同，不能回退补店铺或渠道")
    first, last = _date(value["firstDate"]), _date(value["lastDate"])
    if first > last:
        raise OptionsContractError("业务日期包络倒置")
    count = value["rowCount"]
    if type(count) is not int or not 1 <= count <= MAX_SAFE_INTEGER:
        raise OptionsContractError("来源组须有真实有界记录数")
    return {"identity": identity, "firstDate": first, "lastDate": last, "rowCount": count}


def make_page(groups, *, query, revision, has_more, next_cursor, previous_identity=None):
    """Build an all-or-error page; never truncate a selected group or identity.

    This pure layer cannot validate a cursor signature or claim source authority.
    `previous_identity` must come from the verified cursor at the owning boundary.
    """
    query = normalize_query(query)
    _text(revision, 64)
    if not re.fullmatch(r"(0|[1-9][0-9]*):(0|[1-9][0-9]*)", revision) or any(int(v) > MAX_SAFE_INTEGER for v in revision.split(":")):
        raise OptionsContractError("须绑定 sales:erp 双修订号")
    if type(groups) is not list or len(groups) > PAGE_SIZE or type(has_more) is not bool:
        raise OptionsContractError("页容量或分页类型无效")
    if has_more:
        if len(groups) != PAGE_SIZE:
            raise OptionsContractError("有后页时本页须有完整20组")
        _text(next_cursor, MAX_CURSOR_LENGTH)
    elif next_cursor is not None:
        raise OptionsContractError("末页不得声明下一游标")
    previous = tuple(normalize_identity(previous_identity)[key] for key in IDENTITY_FIELDS) if previous_identity is not None else None
    items = []
    for raw in groups:
        group = normalize_group(raw)
        identity = group["identity"]
        key = tuple(identity[field] for field in IDENTITY_FIELDS)
        if previous is not None and key <= previous:
            raise OptionsContractError("精确身份重复、乱序或跨页边界倒退")
        if any(identity[field] != expected for field, expected in query.items()):
            raise OptionsContractError("返回身份不匹配精确筛选")
        items.append({"optionKey": digest({"domain": "sales", "identity": identity}), "identity": identity,
            "source": "erp_sales", "sourceDataset": "sales_order_lines",
            "dateMetadata": {"kind": "current_fact_business_date_envelope", "firstDate": group["firstDate"], "lastDate": group["lastDate"], "snapshotDate": None, "coverageVerified": False},
            "provenance": {"kind": "current_exact_business_projection", "revision": revision,
                "meaning": "observed_current_identity_not_complete_period_coverage"}})
        previous = key
    page = {"schemaVersion": "business-analysis-options-v1", "domain": "sales", "authorityVerified": False,
        "revision": revision, "query": query, "queryDigest": digest(query), "items": items,
        "pagination": {"returned": len(items), "limit": PAGE_SIZE, "hasMore": has_more, "nextCursor": next_cursor},
        "limitations": ["纯格式合同未核验实际权限、修订号或游标签名。", "候选仅来自当前可精确查询的业务投影，不能从导入渠道或ERP商品主数据推断店铺。",
            "发货业务日最早/最晚包络不证明中间日期连续；空页不等于零经营。", "原始字段为空、别名或投影回退身份不能作为精确分析来源。"]}
    page["pageDigest"] = digest(page)
    if len(canonical(page).encode("utf-8")) > MAX_PAGE_BYTES:
        raise OptionsContractError("完整来源页超过38000 UTF-8字节；禁止裁剪身份或伪造末页")
    return page
