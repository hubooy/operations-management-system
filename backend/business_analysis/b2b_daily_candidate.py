"""Pure JD B2B daily/window candidate over complete, caller-supplied pages.

The catalogue and reader info must be supplied by an owning caller. Checking
their internal consistency does not authenticate that caller or prove B2B is
included in (or disjoint from) ERP or platform SKU sales.
"""
from __future__ import annotations

from datetime import timedelta
import re
import unicodedata

from . import cross_source_window_compare as comparison
from . import evidence_v2
from .contracts import (AnalysisContractError, MAX_SAFE_INTEGER, PageReconciler,
    SCHEMA_VERSION,
    canonical, comparison_periods, coverage, digest, strict_date)
from .cross_source_kpi_plan import PRODUCT_METRICS


SCHEMA = "business-b2b-daily-candidate-v1"
WINDOWS = ("current", "previous", "yearAgo")
MAX_PAGES = 2_000
MAX_ROWS = 200_000
MAX_PAGE_BYTES = 131_072
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_RESULT_BYTES = 512 * 1024
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _need(ok, message="B端来源、日期或完整分页证据不一致"):
    if not ok:
        raise AnalysisContractError(message)


def _empty():
    return {metric: {"value": None, "presentRows": 0, "missingRows": 0}
        for metric in sorted(PRODUCT_METRICS)}


def _days(period):
    first = strict_date(period["startDate"])
    return [(first + timedelta(days=index)).isoformat()
        for index in range(period["days"])]


def _add(cells, metrics):
    _need(type(metrics) is dict and set(metrics) == PRODUCT_METRICS)
    for metric, value in metrics.items():
        _need(value is None or type(value) is int and abs(value) <= MAX_SAFE_INTEGER,
            "B端指标须为有界整数或null")
        cell = cells[metric]
        if value is None:
            cell["missingRows"] += 1
        else:
            cell["presentRows"] += 1
            cell["value"] = (cell["value"] or 0) + value
            _need(abs(cell["value"]) <= MAX_SAFE_INTEGER)
        _need(cell["presentRows"] + cell["missingRows"] <= MAX_ROWS)


def _pages(source, info, pages, periods):
    """Consume all selected B2B pages and return date-keyed typed cells."""
    _need(type(info) is dict and set(info) == {"metadata", "expected", "pageCount"}
        and type(info["metadata"]) is dict
        and type(info["expected"]) is dict
        and type(info["pageCount"]) is int and 1 <= info["pageCount"] <= MAX_PAGES
        and type(pages) is list and len(pages) == info["pageCount"])
    expected = info["expected"]
    _need(set(expected) == {"sourceRef", "rowCount", "metrics", "reconciled",
        "evidenceDigest"} and expected["reconciled"] is True
        and type(expected["rowCount"]) is int and 0 <= expected["rowCount"] <= MAX_ROWS
        and set(expected["metrics"]) == PRODUCT_METRICS
        and type(info["metadata"].get("sourceRevision")) is str
        and bool(info["metadata"]["sourceRevision"])
        and type(expected["sourceRef"]) is str and _SHA.fullmatch(expected["sourceRef"])
        and type(expected["evidenceDigest"]) is str
        and _SHA.fullmatch(expected["evidenceDigest"]))
    q = source["query"]
    period = periods[q["window"]]
    filters = {key: q[key] for key in ("platform", "shop", "dataset", "window")}
    filters["periods"] = periods
    bound_source_ref = digest({"schemaVersion": SCHEMA_VERSION, "query": filters,
        "limit": 100, "revision": info["metadata"]["sourceRevision"],
        "masterBatch": None})
    _need(expected["sourceRef"] == bound_source_ref,
        "B端来源Ref未绑定精确店铺、比较窗口或修订")
    verifier, daily, observed, hashes = PageReconciler(), {}, set(), set()
    total_bytes = 0
    for index, page in enumerate(pages):
        _need(type(page) is dict and type(page.get("items")) is list
            and len(page["items"]) <= 100
            and {"pageEvidence", "pagination", "control", "sourceRef"} <= set(page))
        try:
            page_bytes = len(canonical(page).encode("utf-8"))
        except (TypeError, ValueError, OverflowError) as error:
            raise AnalysisContractError("B端来源页含非规范JSON值") from error
        total_bytes += page_bytes
        _need(total_bytes <= MAX_SOURCE_BYTES and page_bytes <= MAX_PAGE_BYTES)
        _need(page.get("schemaVersion") == "business-analysis-v1"
            and page.get("source") == "jd_b2b"
            and page.get("sourceDataset") == "b2b"
            and page.get("filters") == filters
            and page.get("monetaryUnit") == "CNY_CENT"
            and page.get("sourceRevision") == info["metadata"]["sourceRevision"]
            and type(page.get("pagination")) is dict
            and page["pagination"].get("limit") == 100
            and (page.get("coverage") == info["metadata"].get("coverage")
                if index == 0 else page.get("coverage") is None),
            "B端来源页身份、日期窗口或版本不一致")
        for row in page["items"]:
            _need(type(row) is dict and type(row.get("sourceRowHash")) is str
                and _SHA.fullmatch(row["sourceRowHash"]) is not None
                and row["sourceRowHash"] not in hashes
                and type(row.get("rowId")) is str
                and re.fullmatch(r"[1-9][0-9]{0,15}", row["rowId"]) is not None
                and int(row["rowId"]) <= MAX_SAFE_INTEGER
                and row.get("platform") == q["platform"]
                and row.get("shopName") == q["shop"]
                and type(row.get("date")) is str
                and period["startDate"] <= row["date"] <= period["endDate"])
            strict_date(row["date"])
            hashes.add(row["sourceRowHash"])
            observed.add(row["date"])
            _add(daily.setdefault(row["date"], _empty()), row.get("metrics"))
        try:
            verifier.consume(page, request_cursor=verifier.expected_cursor)
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise AnalysisContractError("B端页控制、游标或源行结构无效") from error
    proof = verifier.result()
    _need(proof == expected and proof["rowCount"] <= MAX_ROWS
        and info["metadata"].get("coverage") == coverage(period, observed),
        "B端完整页、控制汇总或逐日覆盖与固定来源不一致")
    return daily, observed


def _metric_status(source_key, row_count, day, observed, cell):
    return ("catalogue_only_missing_source" if source_key is None else
        "selected_no_records" if row_count == 0 else
        "date_not_covered" if day not in observed else
        "partial_metric_coverage" if cell["missingRows"] else
        "observed_rows")


def prepare_candidate(sources, infos, selected_keys, pages,
                      *, catalog_digest, platform, shop, start_date, end_date):
    """Return B2B-only daily/period metrics with explicit catalogue-only gaps.

    A missing source is a statement about the supplied catalogue, not an
    authenticated absence in the database. The owning caller must separately
    verify the sealed report and principal before using this candidate.
    """
    _need(type(platform) is str and platform == "京东"
        and type(shop) is str and 0 < len(shop) <= 100 and shop == shop.strip()
        and not any(unicodedata.category(char) in {"Cc", "Cs"} for char in shop)
        and type(selected_keys) is dict and set(selected_keys) == set(WINDOWS)
        and type(infos) is dict and type(pages) is dict)
    _need(len(canonical(infos).encode("utf-8")) <= 128 * 1024,
        "B端来源info超过固定容量")
    periods = comparison_periods(start_date, end_date)
    entries = evidence_v2.normalize_sources(sources)
    fixed_digest = digest({"schemaVersion": "business-evidence-directory-v2",
        "entries": entries})
    _need(type(catalog_digest) is str and catalog_digest == fixed_digest
        and all(entry["query"]["startDate"] == start_date
            and entry["query"]["endDate"] == end_date for entry in entries),
        "B端候选不接受不同原始比较区间或目录摘要")
    chosen = {key for key in selected_keys.values() if key is not None}
    _need(set(infos) == chosen and set(pages) == chosen and len(chosen) ==
        sum(key is not None for key in selected_keys.values()))
    windows = {}
    for window in WINDOWS:
        matches = [entry for entry in entries if entry["domain"] == "netshop"
            and entry["query"]["dataset"] == "b2b"
            and entry["query"]["platform"] == platform
            and entry["query"]["shop"] == shop
            and entry["query"]["window"] == window]
        key = selected_keys[window]
        _need((key is None and not matches) or (type(key) is str and
            len(matches) == 1 and matches[0]["key"] == key),
            "B端匹配来源存在时不能声明缺源，亦不得跨店或跨期借用")
        daily, observed = _pages(matches[0], infos[key], pages[key], periods) if key else ({}, set())
        row_count = infos[key]["expected"]["rowCount"] if key else 0
        period_cells = _empty()
        day_rows = []
        for day in _days(periods[window]):
            cells = daily.get(day, _empty())
            for metric, cell in cells.items():
                aggregate = period_cells[metric]
                aggregate["presentRows"] += cell["presentRows"]
                aggregate["missingRows"] += cell["missingRows"]
                if cell["value"] is not None:
                    aggregate["value"] = (aggregate["value"] or 0) + cell["value"]
                    _need(abs(aggregate["value"]) <= MAX_SAFE_INTEGER)
            day_rows.append({"date": day,
                "metrics": {metric: {**cell,
                    "status": _metric_status(key, row_count, day, observed, cell)}
                    for metric, cell in cells.items()}})
        if key:
            _need(period_cells == infos[key]["expected"]["metrics"],
                "B端逐日指标未守恒于完整来源控制汇总")
        period_metrics = {metric: {**cell,
            "status": ("catalogue_only_missing_source" if key is None else
                "selected_no_records" if row_count == 0 else
                "date_not_covered" if any(day not in observed
                    for day in _days(periods[window])) else
                "partial_metric_coverage" if cell["missingRows"] else
                "observed_rows")}
            for metric, cell in period_cells.items()}
        windows[window] = {"sourceKey": key, "period": periods[window],
            "sourceRef": infos[key]["expected"]["sourceRef"] if key else None,
            "evidenceDigest": infos[key]["expected"]["evidenceDigest"] if key else None,
            "coverage": infos[key]["metadata"]["coverage"] if key else None,
            "daily": day_rows, "periodMetrics": period_metrics}
    comparisons = {window: {metric: comparison._comparison(
        windows["current"]["periodMetrics"][metric],
        windows[window]["periodMetrics"][metric]) for metric in sorted(PRODUCT_METRICS)}
        for window in WINDOWS[1:]}
    result = {"schemaVersion": SCHEMA, "catalogDigest": fixed_digest,
        "platform": platform, "shop": shop,
        "comparisonRule": periods["comparisonRule"], "windows": windows,
        "comparisons": comparisons, "b2bIncludedInErpSales": "unknown",
        "b2bIncludedInPlatformSkuSales": "unknown",
        "b2bShareOfErpSales": None, "b2bIncrementalSalesCents": None,
        "crossDomainAmountsAdded": False, "authorityVerified": False,
        "missingSourceAuthorityVerified": False, "registeredRenderer": False,
        "limitations": ["仅原生jd_b2b事实；目录缺源是提供的目录内缺口，仍须owning封存与账号复核。",
            "B端与ERP/平台商品销售包含关系未知，不相加、不算占比或增量。",
            "缺源、缺日、部分指标null及零/负基期不补零；商品日访客不是店铺去重UV。"]}
    _need(len(canonical(result).encode("utf-8")) <= MAX_RESULT_BYTES,
        "B端三窗口日/期候选超过固定UTF-8容量")
    return {**result, "candidateDigest": digest(result)}
