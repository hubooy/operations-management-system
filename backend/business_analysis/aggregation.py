"""Deterministic, bounded grouping and comparisons over verified source facts.

The caller supplies only pages accepted by PageReconciler. This is an owning
application algorithm, not execution of model-generated Python or SQL.
"""
from collections import defaultdict
from copy import deepcopy

from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, PageReconciler, compare, ratio


DIMENSIONS = {"platform", "shopName", "category", "skuId", "spuId", "date", "keyword", "searchTerm",
              "planId", "unitId", "matchType", "promotedSkuId", "triggerSkuId", "attributedSkuId", "brand", "marketScope", "operationMode"}
RATIOS = {"ctr": ("clicks", "impressions"), "cpcCents": ("spendCents", "clicks"),
          "roas": ("reportedGmvCents", "spendCents"), "orderLineConversionRate": ("reportedOrderLines", "clicks")}


def group_result(group, metric_keys):
    metrics = {key: {"value": group["sums"].get(key, 0) if group["present"].get(key) else None,
                     "presentRows": group["present"].get(key, 0),
                     "missingRows": group["rowCount"]-group["present"].get(key, 0)} for key in metric_keys}
    rates = {}
    for name, (numerator, denominator) in RATIOS.items():
        if numerator in metrics and denominator in metrics:
            a, b = metrics[numerator], metrics[denominator]
            rates[name] = ratio(a["value"], b["value"]) if not a["missingRows"] and not b["missingRows"] else None
    return {"entity": group["entity"], "rowCount": group["rowCount"], "metrics": metrics, "ratios": rates}


class DimensionAccumulator:
    def __init__(self, dimensions, metric_keys, *, max_groups=25000):
        if not dimensions or len(dimensions) > 8 or len(set(dimensions)) != len(dimensions) or not set(dimensions) <= DIMENSIONS:
            raise AnalysisContractError("分组维度无效")
        if not metric_keys or len(set(metric_keys)) != len(metric_keys) or len(metric_keys) > 32:
            raise AnalysisContractError("指标配置无效")
        if type(max_groups) is not int or not 1 <= max_groups <= 25000:
            raise AnalysisContractError("分组容量无效")
        self.dimensions, self.metric_keys, self.max_groups = list(dimensions), list(metric_keys), max_groups
        self.groups = {}

    def consume(self, records):
        # Stage just the affected groups. A bad page must neither leave partial
        # sums nor require copying all previously accumulated groups.
        updates = {}
        for record in records:
            # Platform + shop are always part of the identity, even when the
            # requested view groups only by keyword or merchant product.
            entity = {key: record.get(key) if key in record else record.get("dimensions", {}).get(key) for key in self.dimensions}
            key = (record["platform"], record["shopName"], *(entity[d] for d in self.dimensions))
            if key not in updates:
                if key in self.groups:
                    updates[key] = deepcopy(self.groups[key])
                else:
                    updates[key] = {"entity": {"platform": record["platform"], "shopName": record["shopName"], **entity},
                                    "rowCount": 0, "sums": defaultdict(int), "present": defaultdict(int)}
                if len(self.groups) + sum(k not in self.groups for k in updates) > self.max_groups:
                    raise AnalysisContractError("分组超过容量，应分区处理；禁止截断后声称全量")
            group = updates[key]
            group["rowCount"] += 1
            for metric in self.metric_keys:
                value = record["metrics"].get(metric)
                if value is not None:
                    if type(value) is not int or abs(value) > MAX_SAFE_INTEGER:
                        raise AnalysisContractError("指标必须为整数分或整数计数")
                    group["sums"][metric] += value
                    if abs(group["sums"][metric]) > MAX_SAFE_INTEGER:
                        raise AnalysisContractError("分组累计超出无损传输范围")
                    group["present"][metric] += 1
        self.groups.update(updates)

    def result(self, reconciliation):
        if reconciliation.get("reconciled") is not True or sum(g["rowCount"] for g in self.groups.values()) != reconciliation["rowCount"]:
            raise AnalysisContractError("未完成全量核对，不能发布分组结果")
        for metric in self.metric_keys:
            source = reconciliation["metrics"].get(metric)
            present = sum(g["present"].get(metric, 0) for g in self.groups.values())
            total = sum(g["sums"].get(metric, 0) for g in self.groups.values())
            if source is None or source["presentRows"] != present or (source["value"] or 0) != total:
                raise AnalysisContractError("分组结果与源核对记录不一致")
        output = []
        for group in self.groups.values():
            output.append(group_result(group, self.metric_keys))
        return {"sourceRef": reconciliation["sourceRef"], "evidenceDigest": reconciliation["evidenceDigest"],
                "dimensions": self.dimensions, "items": output, "rowCount": reconciliation["rowCount"], "truncated": False}


class VerifiedAnalysis:
    """Feed a signed-reader page once; verification and grouping commit together.

    The later durable job executor owns fetch/retry/checkpoint behavior. This
    pure component does not fetch, execute model code or persist source rows.
    """
    def __init__(self, dimensions, metric_keys, *, max_groups=25000):
        self.verifier = PageReconciler()
        self.accumulator = DimensionAccumulator(dimensions, metric_keys, max_groups=max_groups)

    def consume(self, page, *, request_cursor=None):
        candidate = deepcopy(self.verifier)
        candidate.consume(page, request_cursor=request_cursor)
        if candidate.finished:
            candidate.result()
        self.accumulator.consume(page["items"])
        self.verifier = candidate

    def result(self):
        return self.accumulator.result(self.verifier.result())


def metric_comparison(current, baseline, *, dates_complete):
    """Partial values can be shown, but must not acquire a comparable growth rate."""
    return compare(current.get("value"), baseline.get("value"),
                   comparable=dates_complete and current.get("missingRows") == 0 and baseline.get("missingRows") == 0)
