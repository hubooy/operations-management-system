"""Unregistered JD keyword/search-term × attributed-SKU source views.

These are distinct groupings of the same complete promotion fact stream.  They
must never be added to the promoted-SKU views or treated as product-master
ownership, incremental sales, or an Agent reading receipt.
"""
from contextlib import contextmanager

from . import promotion_views as native
from . import promotion_keyword_sku as promoted
from .contracts import AnalysisContractError, compare, comparison_periods, digest
from .partitioned import Checkpoint, PartitionedGroups


SCHEMA_VERSION = "business-promotion-attributed-sku-relation-candidate-v1"
ALGORITHM_VERSION = "promotion-attributed-sku-relation-v1"
VIEWS = {
    "keyword_attributed_sku": ("keyword", "attributedSkuId"),
    "keyword_searchterm_plan_unit_match_attributed_sku": (
        "planId", "unitId", "matchType", "keyword", "searchTerm",
        "attributedSkuId"),
}
LIMITS = dict(native.LIMITS)


class _Table(native._Table):
    def __init__(self, store, header, limits, checkpoint):
        super().__init__(store, header, limits)
        self._checkpoint = checkpoint

    def _check(self):
        super()._check()
        if self._checkpoint is not None:
            self._checkpoint({"stage": "attributed_sku_relation", "phase": "read"})

    def _row(self, pair, index):
        self._check()
        left, right = pair
        entity = (left or right)["entity"]
        missing = [key for key in VIEWS[self._view] if entity[key] is None]
        comparable = not missing and self._comparable
        metrics = (left or {}).get("metrics")
        baseline = (right or {}).get("metrics")
        comparisons = {}
        if self._has_baseline:
            for key in sorted(set(metrics or {}) | set(baseline or {})):
                current_cell = (metrics or {}).get(key)
                before_cell = (baseline or {}).get(key)
                comparisons[key] = compare(
                    current_cell["value"] if current_cell else None,
                    before_cell["value"] if before_cell else None,
                    comparable=comparable and bool(current_cell and before_cell
                        and not current_cell["missingRows"]
                        and not before_cell["missingRows"]))
            for key in sorted(set((left or {}).get("ratios", {})) |
                    set((right or {}).get("ratios", {}))):
                comparisons[key] = compare(
                    (left or {}).get("ratios", {}).get(key),
                    (right or {}).get("ratios", {}).get(key),
                    comparable=comparable, is_rate=key in native.RATE_METRICS)
        return {"id": digest([self._binding, entity]), "rowIndex": index,
            "entity": entity,
            "currentRowCount": left["rowCount"] if left else None,
            "baselineRowCount": right["rowCount"] if right else None,
            "metrics": metrics, "baselineMetrics": baseline,
            "ratios": (left or {}).get("ratios", {}),
            "comparisons": comparisons, "identityQualified": not missing,
            "missingIdentityFields": missing}

    def read_row(self, row_index, row_id):
        header = self.header()
        native._integer(row_index, header["total"] - 1)
        native._require(type(row_id) is str and len(row_id) == 64,
            "跟单SKU关系行引用无效")
        row = self.page(row_index)["rows"][0]
        native._require(row["id"] == row_id,
            "跟单SKU关系行引用不匹配")
        return row


@contextmanager
def table(source, pages, expected, *, view, baseline_source=None,
          baseline_pages=None, baseline_expected=None, limits=None,
          checkpoint=None):
    """Reconcile all selected facts before exposing a bounded temporary view."""
    native._require(type(view) is str and view in VIEWS,
        "跟单SKU关系视图未知")
    bounds = dict(LIMITS)
    if limits is not None:
        native._require(type(limits) is dict and not set(limits) - set(bounds))
        for key, value in limits.items():
            bounds[key] = native._integer(value, bounds[key], 1)
    check = Checkpoint.wrap(checkpoint)
    source, expected = native._source(source, expected)
    supplied = [baseline_source is not None, baseline_pages is not None,
        baseline_expected is not None]
    native._require(all(supplied) or not any(supplied),
        "跟单SKU基期参数必须完整")
    if all(supplied):
        baseline_source, baseline_expected = native._source(
            baseline_source, baseline_expected)
        current_query, before_query = source["query"], baseline_source["query"]
        native._require(source["key"] != baseline_source["key"]
            and current_query["window"] == "current"
            and before_query["window"] in {"previous", "yearAgo"}
            and {key: value for key, value in current_query.items()
                if key != "window"} ==
                {key: value for key, value in before_query.items()
                if key != "window"},
            "跟单SKU基期来源、店铺或范围不一致")
    result = None
    counters = {"pages": 0, "rows": 0}
    try:
        with PartitionedGroups(checkpoint=check) as store:
            page_size = store.db.execute("PRAGMA page_size").fetchone()[0]
            actual = store.db.execute(
                f'PRAGMA max_page_count={max(1,bounds["maxScratchBytes"]//page_size)}'
            ).fetchone()[0]
            native._require(actual * page_size <= bounds["maxScratchBytes"],
                "跟单SKU临时空间不足")
            current, current_identity = native._ingest(store, 0, source,
                expected, promoted._pages(pages, check, 0), VIEWS[view],
                bounds, counters)
            previous = previous_identity = None
            if all(supplied):
                previous, previous_identity = native._ingest(store, 1,
                    baseline_source, baseline_expected,
                    promoted._pages(baseline_pages, check, 1), VIEWS[view],
                    bounds, counters)
                native._require(current["metricSemantics"] ==
                    previous["metricSemantics"],
                    "跟单SKU两期指标语义变化")
            total, _ = store.page(0, 1)
            native._require(total <= bounds["maxGroups"],
                "跟单SKU两期并集超过容量，不裁剪")
            binding = {"algorithmVersion": ALGORITHM_VERSION, "view": view,
                "source": source, "expected": expected,
                "baselineSource": baseline_source,
                "baselineExpected": baseline_expected,
                "sourceMetadata": current, "baselineMetadata": previous}
            header = {"schemaVersion": SCHEMA_VERSION,
                "algorithmVersion": ALGORITHM_VERSION,
                "authorityVerified": False, "view": view,
                "groupingKeys": ["platform", "shopName", *VIEWS[view]],
                "source": source, "baselineSource": baseline_source,
                "sourceQueryDigest": digest(source["query"]),
                "baselineQueryDigest": digest(baseline_source["query"])
                    if previous else None,
                "sourceMetadata": current, "baselineMetadata": previous,
                "sourceWindow": source["query"]["window"],
                "comparisonWindow": baseline_source["query"]["window"]
                    if previous else None,
                "periods": comparison_periods(source["query"]["startDate"],
                    source["query"]["endDate"]),
                "dateCoverageComparable": bool(previous and all(
                    item["coverage"]["status"] == "dates_present"
                    for item in (current, previous))),
                "identityCoverage": {"current": current_identity,
                    "baseline": previous_identity},
                "sourceTraversal": counters, "total": total,
                "tableBindingDigest": digest(binding),
                "crossViewAdditive": False,
                "attributedSkuIsProductMasterOwnership": False,
                "agentReadPersisted": False,
                "limitations": [
                    "仅京东推广ad完整规范事实；纯结果不证明来源、封存、账号或Agent本人已读",
                    "跟单SKU只取attributedSkuId；不以明确推广SKU、触发SKU或顶层skuId替代",
                    "缺关键词、搜索词、计划/单元/匹配或跟单SKU的桶保留费用，不指向可调整的唯一商品",
                    "本视图和明确推广SKU视图是同一来源的不同分组，不能相加；归因成交不是ERP净销售或B端增量",
                    "完整来源级日期覆盖不证明每个词×跟单SKU每天有记录或归因窗口成熟"]}
            result = _Table(store, header, bounds, check)
            result.page(total)
            if check is not None:
                check({"stage": "attributed_sku_relation", "phase": "ready"})
            try:
                yield result
                if check is not None:
                    check({"stage": "attributed_sku_relation", "phase": "complete"})
            finally:
                result._close()
    except AnalysisContractError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError,
            OverflowError) as error:
        if check is not None:
            check.raise_if_failed()
        raise AnalysisContractError(
            "跟单SKU关系输入或来源结构无效") from error
    finally:
        if result is not None:
            result._close()
