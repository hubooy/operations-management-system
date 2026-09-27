"""Pure, non-authorizing shop/source/metric comparisons over three daily materials.

No ERP, netshop or advertising amount is added to another domain. A rate is
only offered when both whole windows have observed, non-partial metric rows.
"""
from decimal import Decimal, ROUND_HALF_UP
import json

from . import cross_source_daily_columns as daily, cross_source_kpi_plan as planning
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest


SCHEMA = "business-cross-source-window-comparison-candidate-v1"
WINDOWS = planning.WINDOWS
COLUMNS = {"erpSales": ("erpSales", planning.ERP_METRICS),
    "erpUnassigned": ("erpSales", planning.ERP_METRICS),
    "netshopSku": ("netshopSku", planning.PRODUCT_METRICS),
    "netshopSpuNative": ("netshopSpu", planning.PRODUCT_METRICS),
    "promotion": ("promotion", planning.PROMOTION_METRICS)}
MAX_MATERIAL_BYTES = daily.MAX_OUTPUT_BYTES
MAX_RESULT_BYTES = 128 * 1024
MATERIAL_FIELDS = frozenset(("schemaVersion", "planDigest", "reportId", "window", "period",
    "platform", "shop", "sourceKeys", "sourceBindings", "erpRollupManifestDigest",
    "shopDayRows", "skuDayRows", "sourceTotalConserved", "nativeSpuNotSkuRolled",
    "promotionMissingSkuBucketPreserved", "shopUniqueVisitorsAvailable",
    "orderAttributionVerified", "crossDomainAmountsAdded", "authorityVerified",
    "registeredRenderer", "limitations", "materialDigest"))


def _need(value, message="跨来源比较材料与固定三窗口不一致"):
    if not value:
        raise AnalysisContractError(message)


def _cell(value, metric):
    _need(type(value) is dict and set(value) == {"value", "presentRows", "missingRows"},
        "来源指标单元结构无效")
    amount, present, missing = (value[key] for key in ("value", "presentRows", "missingRows"))
    _need(type(present) is int and type(missing) is int
        and 0 <= present <= daily.MAX_SOURCE_ROWS and 0 <= missing <= daily.MAX_SOURCE_ROWS
        and present + missing <= daily.MAX_SOURCE_ROWS
        and ((amount is None and present == 0)
            or (type(amount) is int and abs(amount) <= MAX_SAFE_INTEGER and present > 0)),
        "来源指标有值/缺值状态无效：" + metric)
    return {"value": amount, "presentRows": present, "missingRows": missing}


def _fixed_material(material, plan, window):
    _need(type(material) is dict and set(material) == MATERIAL_FIELDS
        and type(material["shopDayRows"]) is list
        and len(material["shopDayRows"]) == plan["periods"][window]["days"]
        and type(material["skuDayRows"]) is list
        and len(material["skuDayRows"]) <= daily.MAX_SOURCE_ROWS,
        "比较窗口材料字段、日数或SKU容量无效")
    raw = canonical(material)
    _need(len(raw.encode("utf-8")) <= MAX_MATERIAL_BYTES,
        "比较窗口材料超过固定UTF-8容量")
    body = {key: value for key, value in material.items() if key != "materialDigest"}
    _need(material["materialDigest"] == digest(body)
        and material["schemaVersion"] == daily.SCHEMA
        and material["planDigest"] == plan["planDigest"]
        and material["reportId"] == plan["reportId"]
        and material["window"] == window
        and material["period"] == plan["periods"][window]
        and material["platform"] == plan["platform"]
        and material["shop"] == plan["shop"]
        and material["sourceKeys"] == {family: plan["sourceKeys"][family][window]
            for family in planning.FAMILIES}
        and material["sourceBindings"] == {family: plan["sourceBindings"].get(key) if key else None
            for family, key in material["sourceKeys"].items()}
        and material["sourceTotalConserved"] is True
        and material["nativeSpuNotSkuRolled"] is True
        and material["promotionMissingSkuBucketPreserved"] is True
        and material["shopUniqueVisitorsAvailable"] is False
        and material["orderAttributionVerified"] is False
        and material["crossDomainAmountsAdded"] is False
        and material["authorityVerified"] is False
        and material["registeredRenderer"] is False,
        "比较窗口摘要、计划、来源或口径不一致")
    return json.loads(raw)


def _window_columns(material, plan, infos, window):
    days = daily._window_days(plan["periods"][window])
    totals = {column: {metric: {"value": None, "presentRows": 0, "missingRows": 0}
        for metric in sorted(metrics if column != "promotion" or
            material["sourceKeys"]["promotion"] is None else
            infos[material["sourceKeys"]["promotion"]]["expected"]["metrics"])}
        for column, (_, metrics) in COLUMNS.items()}
    statuses = {column: [] for column in COLUMNS}
    for index, row in enumerate(material["shopDayRows"]):
        _need(type(row) is dict and set(row) == {"window", "date", "platform", "shop",
            "sourceDayStatus", "erpSales", "erpUnassigned", "netshopSku",
            "netshopSpuNative", "promotion", "id"}
            and row["window"] == window and row["date"] == days[index]
            and row["platform"] == plan["platform"] and row["shop"] == plan["shop"]
            and row["id"] == digest([plan["planDigest"], "shop_day",
                {key: value for key, value in row.items() if key != "id"}])
            and type(row["sourceDayStatus"]) is dict
            and set(row["sourceDayStatus"]) == set(planning.FAMILIES),
            "店铺日行与固定日期或材料摘要不一致")
        for column, (family, _) in COLUMNS.items():
            cells = row[column]
            _need(type(cells) is dict and set(cells) == set(totals[column]),
                "店铺日列指标集合变化")
            checked = {metric: _cell(value, metric) for metric, value in cells.items()}
            source_key = material["sourceKeys"][family]
            bound = plan["sourceBindings"].get(source_key) if source_key else None
            state = row["sourceDayStatus"][family]
            expected_state = ("missing_source" if source_key is None else
                "selected_no_records" if bound["rowCount"] == 0 else
                "date_not_covered" if row["date"] not in bound["coverage"]["presentDates"] else
                "partial_metric_coverage" if any(cell["missingRows"] for cell in checked.values()) else
                "observed_rows")
            _need(state == expected_state,
                "店铺日缺源、缺日或部分缺值状态与实际单元不符")
            row_count = {cell["presentRows"] + cell["missingRows"] for cell in checked.values()}
            empty_source_day = state in {"missing_source", "selected_no_records", "date_not_covered"}
            _need(len(row_count) == 1 and (not empty_source_day or next(iter(row_count)) == 0)
                and (column == "erpUnassigned" or empty_source_day
                    or next(iter(row_count)) > 0),
                "店铺日来源行数与指标缺口不一致")
            for metric, cell in checked.items():
                total = totals[column][metric]
                total["presentRows"] += cell["presentRows"]
                total["missingRows"] += cell["missingRows"]
                if cell["value"] is not None:
                    total["value"] = (total["value"] or 0) + cell["value"]
                    _need(abs(total["value"]) <= MAX_SAFE_INTEGER,
                        "比较窗口指标加总超出无损范围")
            statuses[column].append(state)
    result = {}
    for column, (family, _) in COLUMNS.items():
        key = material["sourceKeys"][family]
        if column != "erpUnassigned" and key is not None:
            expected = infos[key]["expected"]["metrics"]
            _need(totals[column] == expected,
                "逐日窗口指标与封存来源控制汇总不一致")
        states = statuses[column]
        state = ("missing_source" if key is None else
            "selected_no_records" if all(value == "selected_no_records" for value in states) else
            "date_not_covered" if "date_not_covered" in states else
            "partial_metric_coverage" if "partial_metric_coverage" in states else
            "observed_rows")
        dates_covered = bool(key is not None and all(value in
            {"observed_rows", "partial_metric_coverage"} for value in states))
        result[column] = {"sourceKey": key, "status": state,
            "datesCovered": dates_covered,
            "dayStatusCounts": {value: states.count(value) for value in
                ("missing_source", "selected_no_records", "date_not_covered",
                 "partial_metric_coverage", "observed_rows") if value in states},
            "metrics": totals[column]}
    return result


def _comparison(current, baseline):
    if current["status"] != "observed_rows" or baseline["status"] != "observed_rows":
        return {"status": "incomplete_coverage", "difference": None, "growthRateBps": None}
    left, right = current["value"], baseline["value"]
    if left is None or right is None or current["missingRows"] or baseline["missingRows"]:
        return {"status": "incomplete_metric", "difference": None, "growthRateBps": None}
    difference = left - right
    if abs(difference) > MAX_SAFE_INTEGER:
        return {"status": "difference_out_of_range", "difference": None, "growthRateBps": None}
    if right <= 0:
        return {"status": "zero_baseline" if right == 0 else "negative_baseline",
            "difference": difference, "growthRateBps": None}
    growth = (Decimal(difference) * 10000 / Decimal(right)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP)
    if abs(growth) > MAX_SAFE_INTEGER:
        return {"status": "rate_out_of_range", "difference": difference,
            "growthRateBps": None}
    return {"status": "comparable", "difference": difference,
        "growthRateBps": int(growth)}


def prepare_candidate(plan, sources, infos, context, source_keys, materials):
    """Return complete shop/source/metric comparisons; no model or file grant."""
    plan = planning.check_candidate(sources, infos, context, source_keys, plan)
    _need(type(materials) is dict and set(materials) == set(WINDOWS),
        "三窗口材料必须明确且完整")
    fixed = {window: _fixed_material(materials[window], plan, window)
        for window in WINDOWS}
    windows = {window: _window_columns(fixed[window], plan, infos, window)
        for window in WINDOWS}
    rows = []
    for column in COLUMNS:
        family = COLUMNS[column][0]
        metrics = set().union(*(windows[window][column]["metrics"] for window in WINDOWS))
        for metric in sorted(metrics):
            values = {}
            for window in WINDOWS:
                entry = windows[window][column]
                cell = entry["metrics"].get(metric)
                metric_status = ("missing_source" if entry["sourceKey"] is None else
                    "missing_metric" if cell is None else
                    entry["status"] if not entry["datesCovered"] else
                    "partial_metric_coverage" if cell["missingRows"] else
                    "observed_rows")
                values[window] = {"sourceKey": entry["sourceKey"],
                    "period": plan["periods"][window],
                    "sourceStatus": entry["status"], "status": metric_status,
                    "dayStatusCounts": entry["dayStatusCounts"],
                    **(cell if cell is not None else {"value": None,
                        "presentRows": 0, "missingRows": 0})}
            rows.append({"column": column, "sourceFamily": family, "metric": metric,
                "subsetOf": "erpSales" if column == "erpUnassigned" else None,
                "windows": values,
                "comparisons": {window: _comparison(values["current"], values[window])
                    for window in WINDOWS[1:]}})
    result = {"schemaVersion": SCHEMA, "planDigest": plan["planDigest"],
        "reportId": plan["reportId"], "platform": plan["platform"],
        "shop": plan["shop"], "comparisonRule": plan["comparisonRule"],
        "sourceMaterialDigests": {window: fixed[window]["materialDigest"]
            for window in WINDOWS}, "rows": rows, "rowCount": len(rows),
        "authorityVerified": False, "registeredRenderer": False,
        "crossDomainAmountsAdded": False, "shopUniqueVisitorsAvailable": False,
        "sourceDatesProveEntityDailyCompleteness": False,
        "skuDayRowsIndependentlyValidated": False,
        "limitations": ["仅逐来源、逐指标并列比较；ERP净销售、商智支付与推广归因金额不可相加。",
            "商品日访客不是店铺去重UV；原生SPU支付与SKU汇卷不互相替代。",
            "缺源、缺日、无记录、部分缺值及零/负基期保留状态，不补零或推断增长。",
            "本对照仅验证店铺日列；输入SKU日行包含在材料摘要中，但未逐行重新核验，不能据此确认SKU维度结论。",
            "同比为原区间总量比较；闰日夹止可能造成基期天数不同，未按天归一。"]}
    _need(len(canonical(result).encode("utf-8")) <= MAX_RESULT_BYTES,
        "三窗口逐指标对照超过交付容量")
    return {**result, "comparisonDigest": digest(result)}
