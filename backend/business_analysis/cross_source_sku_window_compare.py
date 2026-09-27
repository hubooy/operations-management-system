"""Unregistered SKU/window comparison over three bounded daily materials.

No current master identity is copied into historical windows. Missing explicit
promotion SKU stays one non-actionable bucket, never a real SKU assignment.
"""
from __future__ import annotations

import json

from . import cross_source_daily_columns as daily, cross_source_kpi_plan as planning
from . import cross_source_window_compare as shops
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest


SCHEMA = "business-cross-source-sku-window-comparison-candidate-v2"
WINDOWS = planning.WINDOWS
COLUMNS = {"erpMatched": ("erpSales", planning.ERP_METRICS),
    "netshopSku": ("netshopSku", planning.PRODUCT_METRICS),
    "promotion": ("promotion", planning.PROMOTION_METRICS)}
MAX_SKU_GROUPS = 20_000
MAX_OUTPUT_ROWS = 50_000
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_ROW_BYTES = 38_000
ROW_FIELDS = {"id", "window", "platform", "shop", "date", "skuId",
    "promotionSkuStatus", "erpAssignedSpuId", "netshopSkuNativeSpuId",
    "erpMatched", "netshopSku", "promotion"}


def _need(ok, message="SKU三窗口逐来源材料不一致"):
    if not ok:
        raise AnalysisContractError(message)


def _empty(keys):
    return daily._null(keys)


def _active(cells):
    return next(iter({cell["presentRows"] + cell["missingRows"]
        for cell in cells.values()}))


def _sku_rows(material, plan, infos, window):
    days = set(daily._window_days(plan["periods"][window]))
    selected = {column: material["sourceKeys"][family]
        for column, (family, _) in COLUMNS.items()}
    metrics = {column: (infos[key]["expected"]["metrics"] if key is not None
        else keys) for column, (family, keys) in COLUMNS.items()
        for key in (selected[column],)}
    seen, identities, by_day, indexed = set(), {"erp": {}, "netshop": {}}, {}, {}
    for raw in material["skuDayRows"]:
        _need(type(raw) is dict and set(raw) == ROW_FIELDS
            and raw["window"] == window and raw["platform"] == plan["platform"]
            and raw["shop"] == plan["shop"] and raw["date"] in days
            and raw["id"] == digest([plan["planDigest"], "sku_day",
                {key: value for key, value in raw.items() if key != "id"}]),
            "SKU日行位置、报告、日期或摘要无效")
        missing = raw["promotionSkuStatus"] == "missing_explicit_promotion_sku"
        _need((missing and raw["skuId"] is None) or
            (not missing and raw["promotionSkuStatus"] == "explicit_sku"
             and type(raw["skuId"]) is str and bool(raw["skuId"].strip())
             and len(raw["skuId"]) <= 600),
            "缺明确推广SKU只能进入独立空身份桶")
        identity = (raw["date"], raw["skuId"], missing)
        _need(identity not in seen, "同一窗口SKU日身份重复")
        seen.add(identity)
        indexed.setdefault((raw["skuId"], missing), {})[raw["date"]] = raw
        checked = {}
        for column in COLUMNS:
            cells = raw[column]
            _need(type(cells) is dict and set(cells) == set(metrics[column]),
                "SKU日列来源指标集合变化")
            checked[column] = {metric: shops._cell(value, metric)
                for metric, value in cells.items()}
            _need(len({cell["presentRows"] + cell["missingRows"]
                for cell in checked[column].values()}) == 1)
        erp_count, net_count, promo_count = (_active(checked[column]) for column in COLUMNS)
        if missing:
            _need(erp_count == net_count == 0 and promo_count > 0
                and raw["erpAssignedSpuId"] is None
                and raw["netshopSkuNativeSpuId"] is None,
                "推广缺SKU桶不得借用ERP或商智商品身份")
        else:
            for label, column, field in (("erp", "erpMatched", "erpAssignedSpuId"),
                                         ("netshop", "netshopSku", "netshopSkuNativeSpuId")):
                count = erp_count if label == "erp" else net_count
                spu = raw[field]
                _need(spu is None if count == 0 else
                    (type(spu) is str and bool(spu.strip()) if label == "erp" else
                     spu is None or type(spu) is str and bool(spu.strip())),
                    "ERP或商智SKU原生SPU身份与该来源事实不一致")
                if count and spu is not None:
                    prior = identities[label].setdefault(raw["skuId"], spu)
                    _need(prior == spu, "同窗口SKU的来源SPU身份冲突")
        day = by_day.setdefault(raw["date"], {column: _empty(metrics[column])
            for column in COLUMNS})
        for column in COLUMNS:
            daily._add(day[column], checked[column])
    return seen, by_day, metrics, indexed


def _conservation(material, plan, infos, window):
    seen, by_day, metrics, indexed = _sku_rows(material, plan, infos, window)
    rows = {row["date"]: row for row in material["shopDayRows"]}
    _need(len(rows) == len(material["shopDayRows"]))
    for date, shop in rows.items():
        own = by_day.get(date, {column: _empty(metrics[column]) for column in COLUMNS})
        combined_erp = _empty(metrics["erpMatched"])
        daily._add(combined_erp, own["erpMatched"])
        daily._add(combined_erp, shop["erpUnassigned"])
        _need(combined_erp == shop["erpSales"],
            "ERP本业务日已分配SKU加未分配池不等于店铺源")
        for column in ("netshopSku", "promotion"):
            _need(own[column] == shop[column],
                "SKU日来源金额/行数不守恒于同日店铺来源")
    return seen, indexed


def _cell_for(rows, column, metric):
    result = _empty((metric,))[metric]
    for row in rows:
        daily._add({metric: result}, {metric: row[column][metric]})
    return result


def _window_value(material, plan, window, sku_id, missing, column, metric,
                  indexed, source_days):
    family = COLUMNS[column][0]
    source_key = material["sourceKeys"][family]
    source = material["sourceBindings"][family]
    rows = indexed.get((sku_id, missing), {})
    cell = _cell_for(rows.values(), column, metric)
    source_rows = {date: row for date, row in rows.items() if
        _active(row[column]) > 0}
    source_days = source_days[family]
    all_days = daily._window_days(plan["periods"][window])
    status = ("missing_source" if source_key is None else
        "selected_no_records" if source["rowCount"] == 0 else
        "date_not_covered" if any(source_days[date] == "date_not_covered"
            for date in all_days) else
        "sku_not_observed_on_covered_day" if any(date not in source_rows
            for date in all_days) else
        "partial_metric_coverage" if cell["missingRows"] else
        "observed_rows")
    if missing:
        status = "missing_explicit_promotion_sku" if source_rows else status
    native_spu = ([row["netshopSkuNativeSpuId"] for row in source_rows.values()]
        if column == "netshopSku" else [])
    return {"sourceKey": source_key, "period": plan["periods"][window],
        "status": status, "skuObservedDays": len(source_rows),
        "erpAssignedSpuId": next((row["erpAssignedSpuId"] for row in source_rows.values()
            if row["erpAssignedSpuId"] is not None), None) if column == "erpMatched" else None,
        "netshopSkuNativeSpuId": native_spu[0] if native_spu
            and all(value is not None for value in native_spu) else None,
        **cell}


def _comparison(column, missing, current, baseline):
    if missing:
        return {"status": "missing_explicit_promotion_sku", "difference": None,
            "growthRateBps": None}
    if column == "erpMatched":
        # These SKU assignments use the current master snapshot for every
        # historical ERP fact. Equal SKU text does not prove past ownership.
        return {"status": "historical_identity_unverified", "difference": None,
            "growthRateBps": None}
    return shops._comparison(current, baseline)


def prepare_candidate(plan, sources, infos, context, source_keys, materials):
    """Compare explicit SKU identities only; all results remain non-authorizing."""
    plan = planning.check_candidate(sources, infos, context, source_keys, plan)
    _need(type(materials) is dict and set(materials) == set(WINDOWS),
        "SKU比较须提供全部三个窗口材料")
    fixed = {window: shops._fixed_material(materials[window], plan, window)
        for window in WINDOWS}
    # Reuse the shop-level source/metric status and complete expected-control
    # checks before reading SKU material that was only digest-bound there.
    for window in WINDOWS:
        shops._window_columns(fixed[window], plan, infos, window)
    identities, indexed_by_window, source_days_by_window = set(), {}, {}
    for window in WINDOWS:
        day_ids, indexed_by_window[window] = _conservation(
            fixed[window], plan, infos, window)
        identities.update((sku, missing) for _, sku, missing in day_ids)
        _need(len(identities) <= MAX_SKU_GROUPS,
            "SKU候选分组超过固定容量，整份拒绝而非截断")
        source_days_by_window[window] = {family: {row["date"]:
            row["sourceDayStatus"][family] for row in fixed[window]["shopDayRows"]}
            for family in planning.FAMILIES}
    rows, output_row_bytes = [], 0
    for sku_id, missing in sorted(identities, key=lambda item: (item[0] or "", item[1])):
        for column in COLUMNS:
            if missing and column != "promotion":
                continue
            metrics = set().union(*(fixed[window]["sourceBindings"][COLUMNS[column][0]]
                .get("metricKeys", []) if fixed[window]["sourceBindings"][COLUMNS[column][0]]
                else COLUMNS[column][1] for window in WINDOWS))
            for metric in sorted(metrics):
                values = {window: _window_value(fixed[window], plan,
                    window, sku_id, missing, column, metric,
                    indexed_by_window[window], source_days_by_window[window]) if
                    (fixed[window]["sourceBindings"][COLUMNS[column][0]] is None or
                     metric in infos[fixed[window]["sourceKeys"][COLUMNS[column][0]]]
                        ["expected"]["metrics"]) else
                    {"sourceKey": fixed[window]["sourceKeys"][COLUMNS[column][0]],
                     "period": plan["periods"][window], "status": "missing_metric",
                     "skuObservedDays": 0, "erpAssignedSpuId": None,
                     "netshopSkuNativeSpuId": None, "value": None,
                     "presentRows": 0, "missingRows": 0}
                    for window in WINDOWS}
                comparisons = {window: _comparison(column, missing,
                    values["current"], values[window])
                    for window in WINDOWS[1:]}
                body = {"skuId": sku_id, "promotionSkuStatus":
                    "missing_explicit_promotion_sku" if missing else "explicit_sku",
                    "actionableSku": not missing, "column": column,
                    "sourceFamily": COLUMNS[column][0], "metric": metric,
                    "windows": values, "comparisons": comparisons}
                encoded = canonical(body).encode("utf-8")
                output_row_bytes += len(encoded) + 100  # row ID and array separator reserve
                _need(len(encoded) <= MAX_ROW_BYTES,
                    "SKU三窗口单行超过固定容量")
                _need(len(rows) < MAX_OUTPUT_ROWS
                    and output_row_bytes <= MAX_OUTPUT_BYTES,
                    "SKU三窗口完整输出超过固定容量，整份拒绝而非截断")
                rows.append({**body, "id": digest([SCHEMA, plan["planDigest"], body])})
    result = {"schemaVersion": SCHEMA, "reportId": plan["reportId"],
        "planDigest": plan["planDigest"],
        "sourceMaterialDigests": {window: fixed[window]["materialDigest"]
            for window in WINDOWS}, "rows": rows, "rowCount": len(rows),
        "nativeSpuExcludedFromSku": True, "erpUnassignedExcludedFromSku": True,
        "missingPromotionSkuBucketSeparate": True,
        "currentMasterNotHistoricalOwnership": True,
        "historicalErpSkuOwnershipVerified": False,
        "crossDomainAmountsAdded": False, "authorityVerified": False,
        "registeredRenderer": False,
        "limitations": ["同码SKU只并排展示各来源指标，不证明同一订单或广告因果归因。",
            "原生SPU支付不进入SKU回卷，ERP未分配池不摊至商品。",
            "缺明确推广SKU为不可操作独立桶，不合并到真实SKU。",
            "任一期间缺源、缺日、SKU缺席、部分缺值或非正基期不计算增长率。",
            "ERP SKU归属基于当前主数据，历史拥有关系未经证明，跨期差额和增长率均不输出。",
            "本期主数据SPU归属不得回填为历史归属；各期仅保留原来源身份。"]}
    _need(len(canonical(result).encode("utf-8")) <= MAX_OUTPUT_BYTES,
        "SKU三窗口完整输出超过固定容量")
    return {**result, "comparisonDigest": digest(result)}
