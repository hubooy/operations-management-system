"""Pure JD plan/unit views over complete supplied fact streams.

No authority, API, model, durable cache or legacy view registration. An owning
caller must supply sealed sources and reauthorize after this context exits.
The temporary partition is readable only after both source streams reconcile.
"""
from contextlib import contextmanager
import json
import math
import re

from .contracts import (AnalysisContractError, MAX_SAFE_INTEGER, PageReconciler,
    SCHEMA_VERSION as SOURCE_SCHEMA, canonical, compare, comparison_periods, coverage, digest)
from .evidence_v2 import normalize_sources
from .partitioned import PartitionedGroups, MAX_RESULT_GROUPS, MAX_SCRATCH_BYTES

SCHEMA_VERSION = "business-promotion-result-table-v1"
ALGORITHM_VERSION = "promotion-plan-unit-v1"
VIEWS = {"plan": ("planId",), "unit": ("planId", "unitId"),
    "unit_match": ("planId", "unitId", "matchType")}
BASE_METRICS = {"spendCents", "impressions", "clicks", "reportedOrderLines", "reportedGmvCents", "cartQuantity"}
METRICS = BASE_METRICS | {"directGmvCents", "indirectGmvCents", "newCustomerGmvCents"}
DIMENSIONS = {"keyword", "searchTerm", "planId", "planName", "unitId", "unitName", "matchType",
    "promotedSkuId", "triggerSkuId", "attributedSkuId", "merchantCode"}
ROW_FIELDS = {"rowId", "sourceRowHash", "batchId", "platform", "shopName", "date", "snapshotDate",
    "skuId", "spuId", "productCode", "productName", "category", "dimensions", "metrics"}
LIMITS = {"maxGroups": MAX_RESULT_GROUPS, "maxScratchBytes": MAX_SCRATCH_BYTES,
    "maxPages": 2000, "maxSourceRows": 200000, "maxResponseBytes": 38000}
RATE_METRICS = {"ctr", "orderLineConversionRate"}


def _require(ok, text="推广派生视图合同无效"):
    if not ok:
        raise AnalysisContractError(text)


def _integer(value, maximum, minimum=0):
    _require(type(value) is int and minimum <= value <= maximum, "推广视图整数或容量无效")
    return value


def _copy(value, maximum):
    # Bound structure before serialization: cycles/deep input fail as contracts,
    # never as RecursionError; no caller-owned mutable values survive an await/next.
    nodes, size = 0, 0
    def visit(item, depth=0):
        nonlocal nodes, size
        nodes += 1
        _require(depth <= 16 and nodes <= 20000, "推广视图结构超限")
        if type(item) is dict:
            _require(len(item) <= 128, "推广视图字段过多")
            for key, child in item.items():
                _require(type(key) is str and len(key) <= 200)
                visit(key, depth+1); visit(child, depth+1)
        elif type(item) is list:
            _require(len(item) <= 1024, "推广视图数组超限")
            for child in item: visit(child, depth+1)
        else:
            _require(item is None or type(item) in (str, int, bool, float))
            if type(item) is str: _require(len(item) <= maximum)
            if type(item) is int: _integer(item, MAX_SAFE_INTEGER, -MAX_SAFE_INTEGER)
            if type(item) is float: _require(math.isfinite(item))
            try: size += len(canonical(item).encode("utf-8"))
            except (UnicodeError, ValueError) as error:
                raise AnalysisContractError("推广视图文本或数值无效") from error
            _require(size <= maximum, "推广视图字节超限")
    visit(value)
    raw = canonical(value)
    _require(len(raw.encode("utf-8")) <= maximum, "推广视图字节超限")
    return json.loads(raw)


def _sha(value):
    _require(type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None)


def _source(value, expected):
    value, expected = _copy(value, 8192), _copy(expected, 8192)
    _require(type(value) is dict and set(value) == {"key", "domain", "query", "sourceRef", "evidenceDigest"})
    entry = normalize_sources([{key:value[key] for key in ("key", "domain", "query")}])[0]
    query = entry["query"]
    _require(entry["domain"] == "netshop" and query["platform"] == "京东" and query["dataset"] == "promotion",
        "unsupported_source_grain：计划/单元视图仅支持京东推广ad原始粒度")
    for key in ("sourceRef", "evidenceDigest"): _sha(value[key])
    _require(type(expected) is dict and set(expected) == {"sourceRef", "rowCount", "metrics", "reconciled", "evidenceDigest"})
    _require(expected["reconciled"] is True and all(expected[key] == value[key] for key in ("sourceRef", "evidenceDigest")))
    count = _integer(expected["rowCount"], LIMITS["maxSourceRows"])
    _require(type(expected["metrics"]) is dict and set(expected["metrics"]) == (METRICS if count else BASE_METRICS),
        "京东推广源指标集合无效")
    for cell in expected["metrics"].values():
        _require(type(cell) is dict and set(cell) == {"value", "presentRows", "missingRows"})
        present = _integer(cell["presentRows"], count)
        missing = _integer(cell["missingRows"], count)
        _require(present+missing == count and (cell["value"] is None) == (present == 0))
        if cell["value"] is not None: _integer(cell["value"], MAX_SAFE_INTEGER, -MAX_SAFE_INTEGER)
    return {**{key:entry[key] for key in ("key", "domain", "query")},
        **{key:value[key] for key in ("sourceRef", "evidenceDigest")}}, expected


def _ingest(store, side, source, expected, pages, dimensions, limits, counters):
    verifier, first, metadata, actual_dates, qualified = PageReconciler(), None, None, set(), 0
    query = source["query"]
    periods = comparison_periods(query["startDate"], query["endDate"])
    period = periods[query["window"]]
    filters = {key:query[key] for key in ("platform", "shop", "dataset", "window")}
    filters["periods"] = periods
    store.configure(side, list(dimensions), sorted(expected["metrics"]))
    for raw in pages:
        counters["pages"] += 1
        _require(counters["pages"] <= limits["maxPages"], "推广源页数超过完整额度")
        page = _copy(raw, 128*1024)
        _require(type(page) is dict and page.get("schemaVersion") == SOURCE_SCHEMA
            and page.get("source") == "jd_promotion" and page.get("sourceDataset") == "ad"
            and page.get("sourceRef") == source["sourceRef"] and page.get("monetaryUnit") == "CNY_CENT")
        _require(type(page.get("filters")) is dict and {"window":"current", **page["filters"]} == filters,
            "推广源查询与固定描述不一致")
        revision = page.get("sourceRevision")
        _require(type(revision) is str and 1 <= len(revision) <= 128)
        _require(type(page.get("metricSemantics")) is dict)
        pagination = page.get("pagination")
        _require(type(pagination) is dict and set(pagination) == {"hasMore", "nextCursor", "limit"})
        limit = _integer(pagination["limit"], 100, 1)
        _require(type(pagination["hasMore"]) is bool and (pagination["nextCursor"] is None
            or type(pagination["nextCursor"]) is str and 0 < len(pagination["nextCursor"]) <= 1600))
        fixed = {key:page.get(key) for key in ("sourceRevision", "filters", "metricSemantics", "consistency")}
        fixed["limit"] = limit
        if first is None:
            first = fixed
            cov = page.get("coverage")
            _require(type(cov) is dict and type(cov.get("presentDates")) is list)
            _require(cov == coverage(period, cov["presentDates"]), "推广日期覆盖与固定期间不一致")
            control = page.get("control")
            _require(type(control) is dict and set(control) <= {"rowCount", "typedTotals", "note"}
                and {"rowCount", "typedTotals"} <= set(control))
            _integer(control["rowCount"], limits["maxSourceRows"])
            _require(type(control["typedTotals"]) is dict and set(control["typedTotals"]) == BASE_METRICS)
            for value in control["typedTotals"].values(): _integer(value, MAX_SAFE_INTEGER, -MAX_SAFE_INTEGER)
            metadata = {key:page.get(key) for key in ("source", "sourceDataset", "sourceRevision", "filters",
                "coverage", "availableDates", "metricSemantics", "consistency")}
        else:
            _require(fixed == first and page.get("coverage") is None and page.get("availableDates") is None,
                "推广来源页元信息变化")
        items = page.get("items")
        _require(type(items) is list and len(items) <= limit)
        proof = page.get("pageEvidence")
        _require(type(proof) is dict and set(proof) == {"rowCount", "sha256"})
        _integer(proof["rowCount"], limit); _sha(proof["sha256"])
        for row in items:
            _require(type(row) is dict and set(row) == ROW_FIELDS, "推广规范行字段无效")
            _require(type(row["rowId"]) is str and re.fullmatch(r"[1-9][0-9]{0,19}", row["rowId"]) is not None)
            _sha(row["sourceRowHash"])
            _require(type(row["batchId"]) is str and 0 < len(row["batchId"]) <= 160)
            _require((row["platform"], row["shopName"]) == (query["platform"], query["shop"]), "推广行跨店")
            _require(type(row["date"]) is str and row["date"] in coverage(period, [row["date"]])["presentDates"])
            actual_dates.add(row["date"])
            _require(type(row["dimensions"]) is dict and set(row["dimensions"]) == DIMENSIONS)
            for value in row["dimensions"].values():
                _require(value is None or type(value) is str and 0 < len(value) <= 240 and value == value.strip(),
                    "推广身份不是规范文本或空值")
            _require(type(row["metrics"]) is dict and set(row["metrics"]) == METRICS)
            qualified += int(all(row["dimensions"][key] is not None for key in dimensions))
        counters["rows"] += len(items)
        _require(counters["rows"] <= limits["maxSourceRows"], "推广源行数超过完整额度")
        verifier.consume(page, request_cursor=verifier.expected_cursor)
        store.consume(side, items)
        _require(store.counts[side] <= limits["maxGroups"], "推广分组超过容量，不截断")
    actual = verifier.result()
    _require(actual == expected, "推广源完整核对记录不一致")
    _require(metadata["coverage"] == coverage(period, actual_dates), "推广记录日期与覆盖声明不一致")
    store.verify(side, expected)
    return metadata, {"rowCount":actual["rowCount"], "qualifiedRows":qualified,
        "unqualifiedRows":actual["rowCount"]-qualified}


class _Table:
    def __init__(self, store, header, limits):
        self._store, self._header, self._limits, self._active = store, canonical(header), limits, True
        self._view, self._binding = header["view"], header["tableBindingDigest"]
        self._comparable = header["dateCoverageComparable"]
        self._has_baseline = header["baselineSource"] is not None
        self._scans = set()

    def _close(self):
        self._active = False
        # Release suspended SQLite cursors before PartitionedGroups closes its
        # connection and removes the temporary directory (required on Windows).
        for pairs in tuple(self._scans):
            pairs.close()
        self._scans.clear()

    def _check(self):
        _require(self._active, "推广视图已离开临时生命周期")

    def header(self):
        self._check()
        return json.loads(self._header)

    def _row(self, pair, index):
        self._check()
        left, right = pair
        entity = (left or right)["entity"]
        missing = [key for key in VIEWS[self._view] if entity[key] is None]
        complete = not missing and self._comparable
        metrics = left["metrics"] if left else None
        before = right["metrics"] if right else None
        comparisons = {}
        if self._has_baseline:
            for key in sorted(set(metrics or {}) | set(before or {})):
                a, b = (metrics or {}).get(key), (before or {}).get(key)
                comparisons[key] = compare(a["value"] if a else None, b["value"] if b else None,
                    comparable=complete and bool(a and b and not a["missingRows"] and not b["missingRows"]))
            for key in sorted(set((left or {}).get("ratios", {})) | set((right or {}).get("ratios", {}))):
                comparisons[key] = compare((left or {}).get("ratios", {}).get(key), (right or {}).get("ratios", {}).get(key),
                    comparable=complete, is_rate=key in RATE_METRICS)
        return {"id":digest([self._binding, entity]), "rowIndex":index, "entity":entity,
            "currentRowCount":left["rowCount"] if left else None, "baselineRowCount":right["rowCount"] if right else None,
            "metrics":metrics, "baselineMetrics":before, "ratios":left["ratios"] if left else {},
            "comparisons":comparisons, "identityQualified":not missing, "missingIdentityFields":missing}

    def page(self, offset=0, limit=20):
        h = self.header()
        _integer(offset, h["total"])
        _require(type(limit) is int and limit == 20, "推广页长固定20")
        _, pairs = self._store.page(offset, limit)
        rows = []
        def output():
            end = offset+len(rows)
            value = {**h, "rows":rows, "pagination":{"offset":offset, "limit":20, "returned":len(rows),
                "total":h["total"], "nextOffset":end if end < h["total"] else None}}
            value["pageDigest"] = digest(value)
            return value
        for pair in pairs:
            rows.append(self._row(pair, offset+len(rows)))
            if len(canonical(output()).encode("utf-8")) > self._limits["maxResponseBytes"]:
                rows.pop(); break
        value = output()
        _require((offset == h["total"] or bool(rows)) and len(canonical(value).encode("utf-8")) <= self._limits["maxResponseBytes"],
            "推广完整行或元信息超过响应容量")
        return value

    def scan(self):
        self._check()
        total, pairs = self._store.scan()
        self._scans.add(pairs)
        count = 0
        try:
            while True:
                # Check before advancing the cursor, including a scan resumed
                # after the enclosing table context has already been closed.
                self._check()
                try:
                    pair = next(pairs)
                except StopIteration:
                    break
                yield self._row(pair, count)
                count += 1
            _require(count == total, "推广完整分组遍历中断")
        finally:
            pairs.close()
            self._scans.discard(pairs)


@contextmanager
def table(source, pages, expected, *, view, baseline_source=None, baseline_pages=None, baseline_expected=None, limits=None):
    """Own a temporary complete table; inputs are not authorization credentials."""
    _require(type(view) is str and view in VIEWS, "推广视图未知")
    bounds = dict(LIMITS)
    if limits is not None:
        _require(type(limits) is dict and not set(limits)-set(bounds))
        for key, value in limits.items(): bounds[key] = _integer(value, bounds[key], 1)
    source, expected = _source(source, expected)
    supplied = [baseline_source is not None, baseline_pages is not None, baseline_expected is not None]
    _require(all(supplied) or not any(supplied), "推广基期参数必须完整")
    if all(supplied):
        baseline_source, baseline_expected = _source(baseline_source, baseline_expected)
        a, b = source["query"], baseline_source["query"]
        _require(source["key"] != baseline_source["key"] and a["window"] == "current" and b["window"] in {"previous", "yearAgo"}
            and {k:v for k,v in a.items() if k != "window"} == {k:v for k,v in b.items() if k != "window"},
            "推广比较来源、店铺或日期窗口不一致")
    counters = {"pages":0, "rows":0}
    result = None
    try:
        with PartitionedGroups() as store:
            size = store.db.execute("PRAGMA page_size").fetchone()[0]
            actual = store.db.execute(f'PRAGMA max_page_count={max(1,bounds["maxScratchBytes"]//size)}').fetchone()[0]
            _require(actual*size <= bounds["maxScratchBytes"], "推广临时空间不足")
            current, identity = _ingest(store, 0, source, expected, pages, VIEWS[view], bounds, counters)
            previous = before_identity = None
            if all(supplied):
                previous, before_identity = _ingest(store, 1, baseline_source, baseline_expected, baseline_pages, VIEWS[view], bounds, counters)
                _require(current["metricSemantics"] == previous["metricSemantics"], "推广比较指标语义变化")
            total, _ = store.page(0, 1)
            _require(total <= bounds["maxGroups"], "推广两期并集超过容量")
            binding = {"algorithmVersion":ALGORITHM_VERSION, "view":view, "source":source, "expected":expected,
                "baselineSource":baseline_source, "baselineExpected":baseline_expected, "sourceMetadata":current, "baselineMetadata":previous}
            header = {"schemaVersion":SCHEMA_VERSION, "algorithmVersion":ALGORITHM_VERSION, "authorityVerified":False,
                "view":view, "groupingKeys":["platform", "shopName", *VIEWS[view]], "source":source, "baselineSource":baseline_source,
                "sourceQueryDigest":digest(source["query"]), "baselineQueryDigest":digest(baseline_source["query"]) if previous else None,
                "sourceMetadata":current, "baselineMetadata":previous, "sourceWindow":source["query"]["window"],
                "comparisonWindow":baseline_source["query"]["window"] if previous else None,
                "periods":comparison_periods(source["query"]["startDate"], source["query"]["endDate"]),
                "dateCoverageComparable":bool(previous and all(item["coverage"]["status"] == "dates_present" for item in (current,previous))),
                "identityCoverage":{"current":identity, "baseline":before_identity}, "sourceTraversal":counters,
                "total":total, "tableBindingDigest":digest(binding),
                "limitations":["仅京东ad封存规范事实计算；纯模块不核验授权或来源真实性",
                    "缺身份桶仅为未归属金额核查，不是同一个真实投放实体，不计算该桶跨期变化",
                    "计划、单元、匹配方式是相同事实的不同聚合，不得再次加总；名称不作身份",
                    "直接、间接及新客金额缺失不补造；归因金额不是增量效果、ERP净销售或利润",
                    "日期有记录不证明归因窗口成熟或每实体完整；同比原区间总量未按天归一"]}
            result = _Table(store, header, bounds)
            result.page(total)  # Check even an empty table's complete metadata size.
            try:
                yield result
            finally:
                result._close()
    except AnalysisContractError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, OverflowError) as error:
        raise AnalysisContractError("推广视图输入或来源结构无效") from error
    finally:
        if result is not None: result._close()
