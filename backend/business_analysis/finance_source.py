"""Pure monthly finance projection. This module grants no read authority.

Inputs are explicit scalar projections of FinanceLine/Month/ImportBatch. An
owning reader must obtain and fence them; caller-supplied hashes prove no origin.
No existing finance dashboard, import semantics or evidence protocol is changed.
"""
from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass
from datetime import date

from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest

SCHEMA = "business-finance-monthly-source-v1"
PAGE_SCHEMA = "business-finance-monthly-page-v1"
MAX_MONTHS = 24
MAX_ROWS = 100_000
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_PAGE_BYTES = 128 * 1024
PAGE_ROWS = 100
ROW_FIELDS = frozenset(("id", "month", "section", "metric_key", "subject_name",
    "scope_key", "scope_type", "scope_name", "group_name", "value_type",
    "amount_cents", "rate_bps", "raw_value", "source_row_count", "sort_order", "is_total"))
SCOPE_FIELDS = frozenset(("scope_key", "scope_type", "scope_name", "group_name"))
MONTH_FIELDS = frozenset(("month", "batch_id", "status"))
BATCH_FIELDS = frozenset(("id", "status", "content_hash", "raw_file_hash", "published_state_token"))
CORE_METRICS = {
    "gross_sales": "amount", "return_amount": "amount", "net_sales": "amount",
    "net_cost": "amount", "gross_profit": "amount", "gross_margin": "rate",
    "selling_expense_total": "amount", "small_profit": "amount", "small_margin": "rate",
    "other_expense_total": "amount", "profit": "amount", "profit_margin": "rate",
}
LIMITATIONS = (
    "月度财报是自然月账面口径；不得按日摊算部分月份。",
    "未提供、空值、非数值及同指标多科目分别披露，不补零。",
    "事业部、组、店铺，以及经营汇总、金蝶科目及合计明细不得混合加总。",
    "金额分与比率基点保留源值；不累加月比率，不从缺失项推利润。",
    "财报与销售/推广的记账日期、税费及净额口径未对齐，不直接相加或推因果。",
    "source_row_count可能反映导入合并；本投影不能恢复已合并的原单元格或跨组身份。",
    "该纯合同未验证账号权限、来源真实性、业务完整性或模型阅读。",
)


def _require(ok, message):
    if not ok:
        raise AnalysisContractError(message)


def _fields(value, fields):
    _require(type(value) is dict and len(value) == len(fields) and set(value) == set(fields), "财报字段集合无效")


def _text(value, maximum, *, empty=False):
    _require(type(value) is str and (empty or bool(value)) and len(value) <= maximum,
             "财报文本类型或长度无效")
    _require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value), "财报文本包含非法代理字符")


def _integer(value, lo=0, hi=MAX_SAFE_INTEGER):
    _require(type(value) is int and lo <= value <= hi, "财报整数类型或范围无效")


def _sha(value):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "财报摘要无效")


def _month(value):
    _require(type(value) is str and re.fullmatch(r"(?:19|20|21)\d{2}-(?:0[1-9]|1[0-2])", value) is not None,
             "财报月份须为1900—2199年的YYYY-MM")
    return int(value[:4]) * 12 + int(value[5:]) - 1


def _scope(value):
    _fields(value, SCOPE_FIELDS)
    _require(value["scope_type"] in ("business", "group", "shop"), "财报范围类型无效")
    for key, maximum in (("scope_key", 2000), ("scope_name", 1000), ("group_name", 1000)):
        _text(value[key], maximum, empty=key != "scope_key")
    return dict(value)


def _query(value):
    _fields(value, ("months", "scope"))
    months = value["months"]
    _require(type(months) is list and 1 <= len(months) <= MAX_MONTHS, "财报须显式选择1—24个自然月")
    ordinals = [_month(month) for month in months]
    _require(ordinals == list(range(ordinals[0], ordinals[0] + len(months))), "财报月份须按升序连续且不重复")
    return {"months": list(months), "scope": _scope(value["scope"])}


def _period(query, value):
    first, last = query["months"][0], query["months"][-1]
    last_day = calendar.monthrange(int(last[:4]), int(last[5:]))[1]
    period = {"startDate": first + "-01", "endDate": f"{last}-{last_day:02d}"}
    result = {"financePeriod": period, "analysisPeriod": None,
              "alignment": "not_requested", "dailyProrationAllowed": False}
    if value is None:
        return result
    _fields(value, ("startDate", "endDate"))
    parsed = []
    for key in ("startDate", "endDate"):
        _text(value[key], 10)
        try:
            day = date.fromisoformat(value[key])
        except ValueError as error:
            raise AnalysisContractError("分析日期无效") from error
        _require(day.isoformat() == value[key], "分析日期须为YYYY-MM-DD")
        parsed.append(day)
    _require(parsed[0] <= parsed[1], "分析日期区间倒置")
    result.update(analysisPeriod=dict(value), alignment="exact_full_months" if value == period else "different_or_partial_months")
    return result


def _row(value, query, available):
    _fields(value, ROW_FIELDS)
    _integer(value["id"], 1)
    _require(type(value["month"]) is str and value["month"] in query["months"] and value["month"] in available,
             "财报行月份未选择或未完成发布")
    scope = _scope({key: value[key] for key in SCOPE_FIELDS})
    _require(scope == query["scope"], "财报行不属于精确所选范围")
    _require(type(value["section"]) is str and value["section"] in ("summary", "kingdee"), "财报区段无效")
    _require(type(value["value_type"]) is str and value["value_type"] in ("amount", "rate", "number", "text"), "财报值类型无效")
    for key, maximum in (("metric_key", 500), ("subject_name", 2000), ("raw_value", 4000)):
        _text(value[key], maximum, empty=key != "subject_name")
    for key in ("amount_cents", "rate_bps"):
        if value[key] is not None:
            _integer(value[key], -MAX_SAFE_INTEGER)
    _require(value["amount_cents"] is None or value["value_type"] == "amount", "金额与源值类型不符")
    _require(value["rate_bps"] is None or value["value_type"] == "rate", "比率与源值类型不符")
    _integer(value["source_row_count"], 1, 1_000_000)
    _integer(value["sort_order"], 0, 10_000_000)
    _require(type(value["is_total"]) is bool, "财报合计标识须为布尔值")
    return dict(value)


@dataclass(frozen=True, slots=True)
class FinanceSource:
    """Immutable byte snapshots, not an authority token; page returns copies."""
    _manifest_json: str
    _pages: tuple[tuple[int, str], ...]

    @property
    def manifest(self):
        return json.loads(self._manifest_json)

    def page(self, offset=0):
        _integer(offset)
        for start, raw in self._pages:
            if offset == start:
                return json.loads(raw)
        raise AnalysisContractError("财报页偏移必须是本源实际分页边界")


def build(rows, *, query, revision, months, batches, analysis_period=None):
    """Consume rows once; reject the entire build on late errors/overcapacity.

    months/batches are bounded lists of explicit ORM scalar projections, not
    entire models or dashboard JSON. Missing selected months are allowed gaps.
    """
    query = _query(query)
    _fields(revision, ("revision", "source_digest"))
    _integer(revision["revision"])
    _sha(revision["source_digest"])
    revision = dict(revision)
    alignment = _period(query, analysis_period)
    _require(type(months) is list and len(months) <= MAX_MONTHS, "财报月份元数据超限")
    _require(type(batches) is list and len(batches) <= MAX_MONTHS, "财报批次元数据超限")
    available, batch_map = {}, {}
    for raw in batches:
        _fields(raw, BATCH_FIELDS)
        _text(raw["id"], 64)
        _require(raw["id"] not in batch_map and raw["status"] == "completed", "财报批次未完成或重复")
        for key in ("content_hash", "raw_file_hash", "published_state_token"):
            _sha(raw[key])
        batch_map[raw["id"]] = dict(raw)
    for raw in months:
        _fields(raw, MONTH_FIELDS)
        _month(raw["month"])
        _text(raw["batch_id"], 64)
        _require(raw["month"] in query["months"] and raw["month"] not in available and raw["status"] == "completed"
                 and raw["batch_id"] in batch_map, "财报月份未绑定唯一完成批次")
        available[raw["month"]] = dict(raw)
    _require(set(batch_map) == {item["batch_id"] for item in available.values()}, "财报存在未引用批次")
    fixed = {"schemaVersion": SCHEMA, "query": query, "revision": revision,
             "months": [available[m] for m in sorted(available)],
             "batches": [batch_map[key] for key in sorted(batch_map)], "periodAlignment": alignment}
    records, seen_ids, seen_keys, metric_rows, counts = [], set(), set(), {}, {}
    consumed_bytes = len(canonical(fixed).encode("utf-8"))
    chain = "0" * 64
    for raw in rows:
        _require(len(records) < MAX_ROWS, "财报源超过行数上限，整源拒绝")
        row = _row(raw, query, available)
        key = (row["month"], row["section"], row["scope_key"], row["subject_name"])
        _require(row["id"] not in seen_ids and key not in seen_keys, "财报行ID或业务身份重复")
        seen_ids.add(row["id"]); seen_keys.add(key)
        record = {"rowId": digest({"revision": revision, "row": row}), **row}
        encoded = canonical(record)
        consumed_bytes += len(encoded.encode("utf-8")) + 1
        _require(consumed_bytes <= MAX_SOURCE_BYTES, "财报源超过字节上限，整源拒绝")
        chain = digest({"previous": chain, "row": record})
        records.append(record)
        counts[row["month"]] = counts.get(row["month"], 0) + 1
        if row["section"] == "summary" and row["metric_key"] in CORE_METRICS:
            metric_rows.setdefault((row["month"], row["metric_key"]), []).append(row)
    coverage = []
    for month in query["months"]:
        metrics = {}
        for metric, kind in CORE_METRICS.items():
            matching = metric_rows.get((month, metric), [])
            field = "amount_cents" if kind == "amount" else "rate_bps"
            status = ("missing_month" if month not in available else "missing_subject" if not matching
                else "ambiguous_subject" if len(matching) != 1 else "non_numeric" if matching[0]["value_type"] != kind
                else "missing_value" if matching[0][field] is None else "present")
            metrics[metric] = {"status": status, "value": matching[0][field] if status == "present" else None,
                               "unit": "CNY_cent" if kind == "amount" else "basis_point"}
        coverage.append({"month": month, "published": month in available, "scopeRows": counts.get(month, 0), "metrics": metrics})
    manifest = {**fixed, "rowCount": len(records), "rowChainDigest": chain, "coverage": coverage,
                "allRequestedMonthsPublished": len(available) == len(query["months"]),
                "allCoreMetricsPresent": all(m["status"] == "present" for c in coverage for m in c["metrics"].values()),
                "sourceAuthorityVerified": False, "businessCoverageVerified": False,
                "limitations": list(LIMITATIONS)}
    source_digest = digest(manifest)
    manifest["sourceDigest"] = source_digest
    pages = []
    offset = 0
    while offset < len(records) or not pages:
        selected = []
        def make_page(items):
            next_offset = offset + len(items)
            page = {"schemaVersion": PAGE_SCHEMA, "sourceDigest": source_digest,
                    "rows": items, "pagination": {"offset": offset, "returned": len(items),
                    "total": len(records), "nextOffset": next_offset if next_offset < len(records) else None}}
            page["pageDigest"] = digest(page)
            return page
        for record in records[offset:offset + PAGE_ROWS]:
            trial = make_page([*selected, record])
            if len(canonical(trial).encode("utf-8")) > MAX_PAGE_BYTES:
                _require(bool(selected), "单条财报记录超过完整页上限")
                break
            selected.append(record)
        page = make_page(selected)
        _require(len(canonical(page).encode("utf-8")) <= MAX_PAGE_BYTES,
                 "财报完整页超过字节上限")
        pages.append((offset, canonical(page)))
        offset += len(selected)
        if offset == len(records):
            break
    # The materialized return (including repeated page envelopes) has a separate
    # hard bound; limits are not a promise that every row-count maximum fits.
    raw_manifest = canonical(manifest)
    _require(len(raw_manifest.encode("utf-8")) + sum(len(raw.encode("utf-8")) for _, raw in pages) <= MAX_SOURCE_BYTES,
             "财报交付总字节超过上限，整源拒绝")
    return FinanceSource(raw_manifest, tuple(pages))
