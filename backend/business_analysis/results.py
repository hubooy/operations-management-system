"""Versioned report tables from complete evidence, never model arithmetic."""
from .aggregation import DimensionAccumulator
from .contracts import AnalysisContractError, PageReconciler, canonical, compare, digest, ratio
from contextlib import contextmanager
from itertools import islice

VIEWS = {"shop": ["shopName"], "category": ["category"], "spu": ["spuId"],
         "sku": ["skuId"], "keyword": ["keyword"], "searchTerm": ["searchTerm"], "daily": ["date"], "brand": ["brand"]}
RATE_METRICS = {"ctr", "orderLineConversionRate"}


@contextmanager
def stream_table(pages, dimension, expected, *, baseline_pages=None, baseline_expected=None, checkpoint=None):
    """Full export of the same API rows; callers must consume inside the context."""
    from .partitioned import PartitionedGroups, Checkpoint
    checkpoint = Checkpoint.wrap(checkpoint)
    if dimension not in VIEWS or baseline_pages is not None and dimension == "daily":
        raise AnalysisContractError("分析导出维度或期间无效")
    with PartitionedGroups(**({"checkpoint":checkpoint} if checkpoint is not None else {})) as store:
        header, _ = _group(pages, dimension, expected, store=store)
        previous = None
        if baseline_pages is not None:
            previous, _ = _group(baseline_pages, dimension, baseline_expected, store=store, side=1)
            if not _compatible(header, previous):
                raise AnalysisContractError("比较来源、身份、口径或日期窗口不一致")
        total, pairs = store.scan()
        table = _assemble(header, previous, dimension, expected, baseline_expected, [], total, 0)
        def rows():
            offset = 0
            while batch := list(islice(pairs, 100)):
                if checkpoint is not None: checkpoint({"stage":"native_rows","rowOffset":offset})
                result = _assemble(header, previous, dimension, expected, baseline_expected, batch, total, offset)
                yield from result["rows"]
                offset += len(batch)
            if offset != total:
                raise AnalysisContractError("完整表导出行数变化")
        iterator = rows()
        try:
            yield table, iterator
        finally:
            iterator.close()
            pairs.close()


def _group(pages, dimension, expected, *, store=None, side=0):
    verifier = PageReconciler()
    metrics = sorted(expected["metrics"])
    if not metrics:
        raise AnalysisContractError("身份主数据没有可加总经营指标")
    accumulator = DimensionAccumulator(VIEWS[dimension], metrics) if store is None else None
    if store is not None:
        store.configure(side, VIEWS[dimension], metrics)
    header = None
    for page in pages:
        if header is None:
            header = {key: page.get(key) for key in ("filters", "source", "sourceDataset", "coverage", "metricSemantics")}
        elif any(page.get(key) != header[key] for key in ("filters", "source", "sourceDataset", "metricSemantics")):
            raise AnalysisContractError("来源页范围或指标口径变化")
        scope = (header["filters"]["platform"], header["filters"]["shop"])
        if any((r["platform"], r["shopName"]) != scope for r in page["items"]):
            raise AnalysisContractError("来源包含其他店铺")
        verifier.consume(page, request_cursor=verifier.expected_cursor)
        if store is None:
            accumulator.consume(page["items"])
        else:
            store.consume(side, page["items"])
    actual = verifier.result()
    if actual != expected:
        raise AnalysisContractError("分析结果与封存核对记录不一致")
    if store is not None:
        store.verify(side, actual)
        return header, None
    return header, accumulator.result(actual)["items"]


def _compatible(current, baseline):
    a, b = current["filters"], baseline["filters"]
    if (current["source"], current["sourceDataset"]) != (baseline["source"], baseline["sourceDataset"]):
        return False
    if current["metricSemantics"] != baseline["metricSemantics"]:
        return False
    ignored = {"window", "limit"}
    return (a.get("window", "current") == "current" and b.get("window") in {"previous", "yearAgo"}
            and {k: v for k, v in a.items() if k not in ignored} == {k: v for k, v in b.items() if k not in ignored})


def build_table(pages, dimension, expected, *, baseline_pages=None, baseline_expected=None,
                offset=0, limit=None, checkpoint=None):
    from .partitioned import MAX_RESULT_GROUPS, PartitionedGroups, Checkpoint
    check = Checkpoint.wrap(checkpoint)
    def checked_pages(source, side):
        for index, page in enumerate(source):
            if check is not None:
                check({"stage": "native_source_page", "side": side, "page": index})
            yield page
    if not isinstance(dimension, str) or dimension not in VIEWS:
        raise AnalysisContractError("分析表维度无效")
    if type(offset) is not int or not 0 <= offset <= MAX_RESULT_GROUPS or (limit is not None and (type(limit) is not int or not 1 <= limit <= 100)):
        raise AnalysisContractError("分析分页参数无效")
    if baseline_pages is not None and dimension == "daily":
        raise AnalysisContractError("日表不得按日期字符串直接比较不同期间")
    if limit is not None:
        with PartitionedGroups(checkpoint=check) as store:
            header, _ = _group(checked_pages(pages, 0), dimension, expected, store=store)
            previous_header = None
            if baseline_pages is not None:
                previous_header, _ = _group(checked_pages(baseline_pages, 1),
                    dimension, baseline_expected, store=store, side=1)
                if not _compatible(header, previous_header):
                    raise AnalysisContractError("比较来源、身份、口径或日期窗口不一致")
            total, pairs = store.page(offset, limit)
        return _assemble(header, previous_header, dimension, expected, baseline_expected, pairs, total, offset)
    if offset:
        raise AnalysisContractError("分页偏移须指定页长")
    header, current = _group(checked_pages(pages, 0), dimension, expected)
    baseline, previous_header = [], None
    if baseline_pages is not None:
        if dimension == "daily":
            raise AnalysisContractError("日表不得按日期字符串直接比较不同期间")
        previous_header, baseline = _group(checked_pages(baseline_pages, 1),
            dimension, baseline_expected)
        if not _compatible(header, previous_header):
            raise AnalysisContractError("比较来源、身份、口径或日期窗口不一致")
    indexed = [{canonical(item["entity"]): item for item in rows} for rows in (current, baseline)]
    keys = sorted(set(indexed[0]) | set(indexed[1]))
    return _assemble(header, previous_header, dimension, expected, baseline_expected,
        [(indexed[0].get(key), indexed[1].get(key)) for key in keys], len(keys), 0)


def _assemble(header, previous_header, dimension, expected, baseline_expected, pairs, total, offset):
    complete_dates = bool(previous_header and all((h.get("coverage") or {}).get("status") == "dates_present" for h in (header, previous_header)))
    market_sample = header["source"] == "market_daily_top"
    result = []
    for row_index, (a, b) in enumerate(pairs, offset):
        item = a or b
        metrics, rates, comparisons = {}, {}, {}
        for metric in sorted(set((a or {}).get("metrics", {})) | set((b or {}).get("metrics", {}))):
            left, right = (record["metrics"][metric] if record and metric in record["metrics"] else None for record in (a, b))
            metrics[metric] = left
            if previous_header:
                comparisons[metric] = compare(left["value"] if left else None, right["value"] if right else None,
                    comparable=complete_dates and not market_sample and bool(left and right and not left["missingRows"] and not right["missingRows"]))
        for metric in sorted(set((a or {}).get("ratios", {})) | set((b or {}).get("ratios", {}))):
            left, right = ((record or {}).get("ratios", {}).get(metric) for record in (a, b))
            rates[metric] = left
            if previous_header:
                comparisons[metric] = compare(left, right, comparable=complete_dates, is_rate=metric in RATE_METRICS)
        sample_comparisons = {}
        if market_sample and previous_header:
            for measure, lower, upper in (("gmv", "sampleGmvLowerCents", "sampleGmvUpperCents"), ("quantity", "sampleQuantityLower", "sampleQuantityUpper")):
                bounds = [(record or {}).get("metrics", {}).get(key) for record, key in ((a, lower), (a, upper), (b, lower), (b, upper))]
                valid = complete_dates and all(bound and not bound["missingRows"] and bound["value"] is not None for bound in bounds)
                values = [bound["value"] if bound else None for bound in bounds]
                if valid and values[2] > 0 and values[3] > 0:
                    sample_comparisons[measure] = {"status": "observed_sample_interval", "lowerChangeRate": ratio(values[0], values[3])-1,
                        "upperChangeRate": ratio(values[1], values[2])-1, "meaning": "TOP样本区间变化；不代表全行业增长或份额，样本成员可能变化。"}
                else:
                    sample_comparisons[measure] = {"status": "unavailable", "lowerChangeRate": None, "upperChangeRate": None}
        result.append({"id": digest([expected["evidenceDigest"], baseline_expected, dimension, item["entity"]]), "rowIndex": row_index,
            "entity": item["entity"], "currentRowCount": a["rowCount"] if a else None,
            "baselineRowCount": b["rowCount"] if b else None, "metrics": metrics, "ratios": rates,
            "baselineMetrics": b["metrics"] if b else None,
            "comparisons": comparisons, "sampleComparisons": sample_comparisons,
            "dimensionMissing": any(item["entity"].get(d) in (None, "") for d in VIEWS[dimension])})
    return {"schemaVersion": "business-result-table-v1", "dimension": dimension, "source": expected,
        "baselineSource": baseline_expected, "sourceMetadata": header, "baselineMetadata": previous_header,
        "comparisonWindow": previous_header["filters"]["window"] if previous_header else None,
        "dateCoverageComparable": complete_dates, "total": total, "rows": result,
        "limitations": ["缺失分组不补零，缺日或字段缺失不计算增长率", "店铺表为所选来源金额汇总，不是跨源相加或店铺去重UV",
            "关键词与搜索词分开，原生SPU不等于SKU映射，空身份单独保留", "数据日期存在不证明结算完成或因果关系"]}
