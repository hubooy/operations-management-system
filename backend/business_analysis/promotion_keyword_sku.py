"""Unregistered keyword × explicitly promoted SKU tables over complete JD facts.

Uses the existing projection/reconciliation contract without registering another
legacy dimension. A supplied sourceRef is not authorization. Only a later owning
service can attach sealed-report authority after this context exits normally.
"""
from contextlib import contextmanager

from . import promotion_views as native
from .contracts import AnalysisContractError, compare, comparison_periods, digest
from .partitioned import Checkpoint, PartitionedGroups

SCHEMA_VERSION = "business-promotion-keyword-sku-table-v1"
ALGORITHM_VERSION = "promotion-keyword-promoted-sku-v1"
VIEWS = {
    "keyword_sku": ("keyword", "promotedSkuId"),
    "keyword_sku_context": ("planId", "unitId", "matchType", "keyword", "promotedSkuId"),
}
LIMITS = dict(native.LIMITS)


class _Table(native._Table):
    def __init__(self, store, header, limits, check):
        super().__init__(store, header, limits)
        self._checkpoint = check

    def _check(self):
        super()._check()
        if self._checkpoint is not None:
            self._checkpoint({"stage": "keyword_sku", "phase": "read"})

    def _row(self, pair, index):
        self._check()
        left, right = pair
        entity = (left or right)["entity"]
        missing = [key for key in VIEWS[self._view] if entity[key] is None]
        comparable = not missing and self._comparable
        metrics, before = (left or {}).get("metrics"), (right or {}).get("metrics")
        comparisons = {}
        if self._has_baseline:
            for key in sorted(set(metrics or {}) | set(before or {})):
                a, b = (metrics or {}).get(key), (before or {}).get(key)
                comparisons[key] = compare(a["value"] if a else None, b["value"] if b else None,
                    comparable=comparable and bool(a and b and not a["missingRows"] and not b["missingRows"]))
            for key in sorted(set((left or {}).get("ratios", {})) | set((right or {}).get("ratios", {}))):
                comparisons[key] = compare((left or {}).get("ratios", {}).get(key), (right or {}).get("ratios", {}).get(key),
                    comparable=comparable, is_rate=key in native.RATE_METRICS)
        return {"id": digest([self._binding, entity]), "rowIndex": index, "entity": entity,
            "currentRowCount": left["rowCount"] if left else None,
            "baselineRowCount": right["rowCount"] if right else None,
            "metrics": metrics, "baselineMetrics": before, "ratios": (left or {}).get("ratios", {}),
            "comparisons": comparisons, "identityQualified": not missing,
            "missingIdentityFields": missing}

    def read_row(self, row_index, row_id):
        """Exact deterministic row lookup, never a claim of source authority."""
        header = self.header()
        native._integer(row_index, header["total"] - 1)
        native._require(type(row_id) is str and len(row_id) == 64, "关键词商品行引用无效")
        row = self.page(row_index)["rows"][0]
        native._require(row["id"] == row_id, "关键词商品行引用不匹配")
        return row


def _pages(pages, check, side):
    iterator = iter(pages)
    while True:
        if check is not None: check({"stage": "keyword_sku", "phase": "page", "side": side})
        try:
            page = next(iterator)
        except StopIteration:
            break
        yield page
    if check is not None: check({"stage": "keyword_sku", "phase": "source_complete", "side": side})


@contextmanager
def table(source, pages, expected, *, view="keyword_sku", baseline_source=None,
          baseline_pages=None, baseline_expected=None, limits=None, checkpoint=None):
    """Fully reconcile both streams before yielding; keep temporary reads scoped.

    Callback exceptions retain identity through SQLite interruption and cannot be
    swallowed by a consumer to obtain a successful context exit. No global hook.
    """
    native._require(type(view) is str and view in VIEWS, "关键词商品视图未知")
    bounds = dict(LIMITS)
    if limits is not None:
        native._require(type(limits) is dict and not set(limits) - set(bounds))
        for key, value in limits.items(): bounds[key] = native._integer(value, bounds[key], 1)
    check = Checkpoint.wrap(checkpoint)
    source, expected = native._source(source, expected)
    supplied = [baseline_source is not None, baseline_pages is not None, baseline_expected is not None]
    native._require(all(supplied) or not any(supplied), "关键词商品基期参数必须完整")
    if all(supplied):
        baseline_source, baseline_expected = native._source(baseline_source, baseline_expected)
        a, b = source["query"], baseline_source["query"]
        native._require(source["key"] != baseline_source["key"] and a["window"] == "current"
            and b["window"] in {"previous", "yearAgo"}
            and {k: v for k, v in a.items() if k != "window"} == {k: v for k, v in b.items() if k != "window"},
            "关键词商品比较来源、店铺或原始日期不一致")
    result = None
    counters = {"pages": 0, "rows": 0}
    try:
        with PartitionedGroups(checkpoint=check) as store:
            size = store.db.execute("PRAGMA page_size").fetchone()[0]
            actual = store.db.execute(f'PRAGMA max_page_count={max(1,bounds["maxScratchBytes"]//size)}').fetchone()[0]
            native._require(actual * size <= bounds["maxScratchBytes"], "关键词商品临时空间不足")
            current, identity = native._ingest(store, 0, source, expected, _pages(pages, check, 0), VIEWS[view], bounds, counters)
            previous = before_identity = None
            if all(supplied):
                previous, before_identity = native._ingest(store, 1, baseline_source, baseline_expected,
                    _pages(baseline_pages, check, 1), VIEWS[view], bounds, counters)
                native._require(current["metricSemantics"] == previous["metricSemantics"], "关键词商品两期指标语义变化")
            total, _ = store.page(0, 1)
            native._require(total <= bounds["maxGroups"], "关键词商品两期并集超过容量，不裁剪")
            binding = {"algorithmVersion": ALGORITHM_VERSION, "view": view, "source": source, "expected": expected,
                "baselineSource": baseline_source, "baselineExpected": baseline_expected,
                "sourceMetadata": current, "baselineMetadata": previous}
            header = {"schemaVersion": SCHEMA_VERSION, "algorithmVersion": ALGORITHM_VERSION, "authorityVerified": False,
                "view": view, "groupingKeys": ["platform", "shopName", *VIEWS[view]], "source": source,
                "baselineSource": baseline_source, "sourceQueryDigest": digest(source["query"]),
                "baselineQueryDigest": digest(baseline_source["query"]) if previous else None,
                "sourceMetadata": current, "baselineMetadata": previous, "sourceWindow": source["query"]["window"],
                "comparisonWindow": baseline_source["query"]["window"] if previous else None,
                "periods": comparison_periods(source["query"]["startDate"], source["query"]["endDate"]),
                "dateCoverageComparable": bool(previous and all(item["coverage"]["status"] == "dates_present" for item in (current, previous))),
                "identityCoverage": {"current": identity, "baseline": before_identity}, "sourceTraversal": counters,
                "total": total, "tableBindingDigest": digest(binding), "limitations": [
                    "仅京东ad完整规范事实；纯模块不验证封存、权限或来源真实性",
                    "关键词仅取keyword，推广SKU仅取promotedSkuId；不以搜索词、触发SKU、跟单SKU、顶层skuId或商品名称替代",
                    "推广SKU来自源明确角色字段，不证明商品主数据唯一归属；原投影多个同角色别名冲突未在本层检测",
                    "缺身份桶保留金额但不代表一个真实商品，不用于具体商品调整动作或该桶跨期比较",
                    "关键词商品汇总合并计划、单元及匹配方式；上下文视图保留这些身份，两视图不可再次加总",
                    "金额与比率缺失不补零；平台归因成交不是ERP净销售、利润、增量效果或因果证据",
                    "日期覆盖是来源级有记录，不证明每个词商品逐日完整或归因窗口成熟；同比原区间总量未按天归一"]}
            result = _Table(store, header, bounds, check)
            result.page(total)
            if check is not None: check({"stage": "keyword_sku", "phase": "ready"})
            try:
                yield result
                if check is not None: check({"stage": "keyword_sku", "phase": "complete"})
            finally:
                result._close()
    except AnalysisContractError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, OverflowError) as error:
        if check is not None: check.raise_if_failed()
        raise AnalysisContractError("关键词商品视图输入或来源结构无效") from error
    finally:
        if result is not None: result._close()
