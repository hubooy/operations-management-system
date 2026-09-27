"""Pure contracts for complete, traceable multi-source analysis."""
from __future__ import annotations

import calendar
import copy
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP


SCHEMA_VERSION = "business-analysis-v1"
MAX_WINDOW_DAYS = 93
MAX_SAFE_INTEGER = 9_007_199_254_740_991


class AnalysisContractError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def bounded_page_items(items, has_more, max_bytes=65536):
    """Return a complete row prefix. The cursor must use its final actual row.

    Reserve half of the 128 KiB persisted page for metadata, control totals and
    signatures. Never truncate a field or drop an oversized row silently.
    """
    size, count = 2, 0
    for item in items:
        item_size = len(canonical(item).encode("utf-8")) + (1 if count else 0)
        if size + item_size > max_bytes:
            if not count:
                raise AnalysisContractError("单条规范记录超过证据页容量")
            return items[:count], True
        size += item_size
        count += 1
    return items, has_more


def strict_date(value):
    if not isinstance(value, str):
        raise AnalysisContractError("日期必须为 YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise AnalysisContractError("日期必须为 YYYY-MM-DD") from error
    if parsed.isoformat() != value or not 2000 <= parsed.year <= 2098:
        raise AnalysisContractError("日期范围应在 2000—2098 年")
    return parsed


def comparison_periods(start_date, end_date):
    """User dates are inclusive; every source predicate uses endExclusive."""
    start, end = strict_date(start_date), strict_date(end_date)
    days = (end - start).days + 1
    if not 1 <= days <= MAX_WINDOW_DAYS:
        raise AnalysisContractError("分析窗口必须为连续 1—93 天")

    def previous_year(day):
        year = day.year - 1
        return day.replace(year=year, day=min(day.day, calendar.monthrange(year, day.month)[1]))

    def window(first, last):
        return {"startDate": first.isoformat(), "endDate": last.isoformat(),
                "endExclusive": (last + timedelta(days=1)).isoformat(),
                "days": (last - first).days + 1}

    return {"timezone": "Asia/Shanghai", "comparisonRule": "previous_equal_length_and_previous_year_clamped",
            "current": window(start, end),
            "previous": window(start - timedelta(days=days), start - timedelta(days=1)),
            "yearAgo": window(previous_year(start), previous_year(end))}


def ratio(numerator, denominator):
    if numerator is None or denominator is None or denominator == 0:
        return None
    return float(Decimal(str(numerator)) / Decimal(str(denominator)))


def compare(current, baseline, *, is_rate=False, comparable=True):
    if not comparable or current is None or baseline is None:
        return {"current": current, "baseline": baseline, "difference": None,
                "changeRate": None, "percentagePoints": None, "status": "unavailable"}
    change = Decimal(str(current)) - Decimal(str(baseline))
    if type(current) is int and type(baseline) is int and abs(change) > MAX_SAFE_INTEGER:
        raise AnalysisContractError("比较差额超出无损整数范围")
    status = "comparable" if baseline > 0 else "zero_baseline" if baseline == 0 else "negative_baseline"
    return {"current": current, "baseline": baseline, "difference": int(change) if type(current) is int and type(baseline) is int else float(change),
            "changeRate": ratio(change, baseline) if baseline > 0 else None,
            "percentagePoints": float(change * 100) if is_rate else None, "status": status}


def monetary_cents(value):
    """Only explicit monetary source scalars; missing is not a zero amount."""
    if value is None or isinstance(value, (bool, list, dict)):
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "—", "N/A"}:
        return None
    try:
        number = Decimal(text)
        if not number.is_finite():
            return None
        result = int((number * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        if abs(result) > MAX_SAFE_INTEGER:
            raise AnalysisContractError("金额超过无损传输范围")
        return result
    except ArithmeticError:
        return None


def coverage(window, actual_dates):
    first, last = strict_date(window["startDate"]), strict_date(window["endDate"])
    expected = {(first + timedelta(days=index)).isoformat() for index in range((last-first).days+1)}
    actual = set(actual_dates)
    if not actual <= expected:
        raise AnalysisContractError("来源日期超出请求窗口")
    missing = sorted(expected - actual)
    return {"presentDates": sorted(actual), "missingDates": missing,
            "dataCutoffDate": max(actual) if actual else None,
            "status": "no_records" if not actual else "missing_dates" if missing else "dates_present",
            "meaning": "有记录日期检查；缺少记录的日期不能解释为零业务，有记录也不证明平台已最终结算。"}


def identity_candidates(master_rows, platform, shop, merchant_code):
    """Never replicate one ERP fact across multiple SKU candidates."""
    matches = sorted({(str(row.get("skuId") or ""), str(row.get("spuId") or ""))
                      for row in master_rows
                      if row.get("platform") == platform and row.get("shopName") == shop
                      and merchant_code and (row.get("merchantCode") or row.get("dimensions", {}).get("merchantCode")) == merchant_code
                      and row.get("skuId")})
    status = "matched" if len(matches) == 1 else "ambiguous" if matches else "unmatched"
    return {"status": status, "skuId": matches[0][0] if status == "matched" else None,
            "spuId": matches[0][1] or None if status == "matched" else None,
            "candidates": [{"skuId": sku, "spuId": spu or None} for sku, spu in matches],
            "mappingBasis": "supplied_master_snapshot_not_historical_identity"}


class PageReconciler:
    """Incremental validator: O(1) metadata plus sums, never keeps raw pages.

    Strict monotonic row IDs and cursor continuity detect replay/reordering.
    The caller must persist this state alongside a durable task checkpoint.
    """
    def __init__(self):
        self.source_ref = None
        self.expected_cursor = None
        self.last_id = 0
        self.rows = 0
        self.totals = {}
        self.present = {}
        self.control = None
        self.finished = False
        self.evidence_digest = digest([])

    def consume(self, page, *, request_cursor=None):
        checkpoint = copy.deepcopy(self.__dict__)
        try:
            self._consume(page, request_cursor=request_cursor)
        except (AnalysisContractError, KeyError, ValueError, TypeError):
            self.__dict__ = checkpoint
            raise

    def _consume(self, page, *, request_cursor=None):
        if self.finished or request_cursor != self.expected_cursor:
            raise AnalysisContractError("分页已结束或游标链不连续")
        if page.get("schemaVersion") != SCHEMA_VERSION:
            raise AnalysisContractError("分析数据版本不兼容")
        if self.source_ref is None:
            if request_cursor is not None or not isinstance(page.get("control"), dict):
                raise AnalysisContractError("缺少首页控制汇总")
            self.source_ref, self.control = page["sourceRef"], copy.deepcopy(page["control"])
        elif page["sourceRef"] != self.source_ref or page.get("control") is not None:
            raise AnalysisContractError("来源版本变化或重复控制汇总")
        records = page["items"]
        if len(records) != page["pageEvidence"]["rowCount"] or digest(records) != page["pageEvidence"]["sha256"]:
            raise AnalysisContractError("页内容摘要不匹配")
        for record in records:
            row_id = int(record["rowId"])
            if row_id <= self.last_id:
                raise AnalysisContractError("源记录重复或乱序")
            self.last_id = row_id
            self.rows += 1
            for key, value in record["metrics"].items():
                self.totals.setdefault(key, 0)
                if value is not None:
                    if type(value) is not int or abs(value) > MAX_SAFE_INTEGER:
                        raise AnalysisContractError("源指标不是无损整数")
                    self.totals[key] = self.totals.get(key, 0) + value
                    if abs(self.totals[key]) > MAX_SAFE_INTEGER:
                        raise AnalysisContractError("累计指标超出无损传输范围")
                    self.present[key] = self.present.get(key, 0) + 1
        pagination = page["pagination"]
        self.expected_cursor = pagination["nextCursor"]
        if bool(self.expected_cursor) != pagination["hasMore"] or (pagination["hasMore"] and not records):
            raise AnalysisContractError("分页终态无效")
        self.finished = not pagination["hasMore"]
        self.evidence_digest = digest([self.evidence_digest, page["pageEvidence"]["sha256"], self.rows])

    def result(self):
        if not self.finished or self.control is None or self.rows != self.control["rowCount"]:
            raise AnalysisContractError("源记录尚未完整读取")
        for key, value in self.control["typedTotals"].items():
            if self.totals.get(key, 0) != value:
                raise AnalysisContractError("明细与来源控制汇总不一致：" + key)
        return {"sourceRef": self.source_ref, "rowCount": self.rows,
                "metrics": {key: {"value": value if self.present.get(key) else None,
                                  "presentRows": self.present.get(key, 0),
                                  "missingRows": self.rows-self.present.get(key, 0)}
                            for key, value in {**self.control["typedTotals"], **self.totals}.items()},
                "reconciled": True, "evidenceDigest": self.evidence_digest}
