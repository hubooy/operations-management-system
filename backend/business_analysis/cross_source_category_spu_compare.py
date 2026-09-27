"""Pure category/SPU window candidate; source material never grants authority.

ERP category and SPU labels come from a *current* master snapshot. Their
historical ownership is not proved, so this module deliberately withholds ERP
cross-window deltas even when the displayed labels happen to match. Native
netshop SPU rows have their own source identity and metric namespace.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re

from . import cross_source_daily_columns as daily
from . import cross_source_kpi_plan as planning
from . import cross_source_window_compare as shop_compare
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest


SCHEMA = "business-category-spu-window-comparison-candidate-v1"
KINDS = ("category_day", "spu_day")
WINDOWS = planning.WINDOWS
MAX_RESULT_ROWS = 50_000
MAX_RESULT_BYTES = 64 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _need(ok, message="品类/SPU逐日材料与三窗口计划不一致"):
    if not ok:
        raise AnalysisContractError(message)


def _zero():
    return {key: 0 for key in sorted(planning.ERP_METRICS)}


def _extra_rows(manifest, chunks, kind, window, plan):
    """Read one of the two previously unconsumed ERP rollup streams in full."""
    spec = next((value for value in manifest["tables"] if value["kind"] == kind), None)
    _need(spec is not None and type(chunks) is list)
    sha, size, pending, count, rows = hashlib.sha256(), 0, bytearray(), 0, []
    prior_key = None
    valid_days = set(daily._window_days(plan["periods"][window]))
    for chunk in chunks:
        count += 1
        _need(type(chunk) is bytes and len(chunk) <= daily.erp_fact_rollups.MAX_CHUNK_BYTES)
        size += len(chunk)
        _need(size <= daily.erp_fact_rollups.MAX_OUTPUT_BYTES)
        sha.update(chunk)
        pending.extend(chunk)
        while b"\n" in pending:
            line, _, rest = pending.partition(b"\n")
            pending = bytearray(rest)
            _need(0 < len(line)+1 <= daily.MAX_ROW_BYTES)
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError) as error:
                raise AnalysisContractError("ERP品类/SPU完整行不是有效JSON") from error
            _need(type(row) is dict and set(row) == {"period", "platform",
                "shopName", "date", "category", "spuId", "skuId", "status",
                "sourceFactCount", "metrics", "firstSourceRowId",
                "lastSourceRowId", "sourceRowDigest", "rowIndex", "id"}
                and canonical(row).encode("utf-8") == line
                and row.get("rowIndex") == len(rows)
                and type(row.get("rowIndex")) is int
                and row.get("period") == window
                and row.get("platform") == plan["platform"]
                and row.get("shopName") == plan["shop"]
                and row.get("date") in valid_days
                and type(row.get("category")) is str and bool(row["category"].strip())
                and len(row["category"]) <= 600
                and row.get("skuId") is None and row.get("status") is None
                and type(row.get("sourceFactCount")) is int
                and 0 < row["sourceFactCount"] <= daily.MAX_SOURCE_ROWS
                and type(row.get("firstSourceRowId")) is str
                and type(row.get("lastSourceRowId")) is str
                and type(row.get("sourceRowDigest")) is str
                and _SHA.fullmatch(row["sourceRowDigest"]) is not None
                and type(row.get("metrics")) is dict
                and set(row["metrics"]) == planning.ERP_METRICS
                and all(type(amount) is int and abs(amount) <= MAX_SAFE_INTEGER
                    for amount in row["metrics"].values()))
            if kind == "category_day":
                _need(row.get("spuId") is None)
            else:
                _need(type(row.get("spuId")) is str and bool(row["spuId"].strip())
                    and len(row["spuId"]) <= 600)
            key = {name: row[name] for name in
                ("period", "platform", "shopName", "date", "category", "spuId", "skuId", "status")}
            identity = canonical(key)
            body = {name: value for name, value in row.items() if name != "id"}
            _need(prior_key is None or prior_key < identity)
            _need(type(row.get("id")) is str
                and row["id"] == digest([manifest["assignmentSummaryDigest"],
                    kind, identity, body]),
                "ERP品类/SPU行身份未绑定逐事实回卷摘要")
            prior_key = identity
            rows.append(row)
            _need(len(rows) <= daily.MAX_SOURCE_ROWS)
        _need(len(pending) < daily.MAX_ROW_BYTES)
    _need(not pending and count == spec["pageCount"]
        and len(rows) == spec["rowCount"]
        and size == spec["ndjsonBytes"]
        and sha.hexdigest() == spec["ndjsonSha256"])
    return rows


def _erp_window(plan, sources, infos, context, window, material):
    key = plan["sourceKeys"]["erpSales"][window]
    if key is None:
        _need(material is None)
        return None, {kind: [] for kind in KINDS}, set()
    _need(type(material) is tuple and len(material) == 2)
    manifest, streams = material
    _need(type(streams) is dict and set(streams) == set(daily.erp_fact_rollups.KINDS))
    base = daily._erp_material(plan, context, window, sources, infos, manifest,
        {kind: streams[kind] for kind in daily._ERP_KINDS})
    extras = {kind: _extra_rows(manifest, streams[kind], kind, window, plan)
        for kind in KINDS}
    _need(sum(item["rowCount"] for item in manifest["tables"]) == manifest["rowCount"]
        and sum(item["ndjsonBytes"] for item in manifest["tables"]) == manifest["ndjsonBytes"])
    def aggregate(rows, kind):
        result = defaultdict(lambda: {"count": 0, "metrics": _zero()})
        for row in rows:
            identity = ((row["date"], row["category"]) if kind == "category_day" else
                (row["date"], row["category"], row["spuId"]))
            entry = result[identity]
            entry["count"] += row["sourceFactCount"]
            _need(entry["count"] <= daily.MAX_SOURCE_ROWS)
            for metric, amount in row["metrics"].items():
                entry["metrics"][metric] += amount
                _need(abs(entry["metrics"][metric]) <= MAX_SAFE_INTEGER)
        return dict(result)
    for kind in KINDS:
        _need(aggregate(extras[kind], kind) == aggregate(base["sku_day"], kind),
            "ERP品类/SPU每日身份、事实数与已分配SKU事实不守恒")
    observed = {row["date"] for row in base["shop_day"]}
    return manifest["manifestDigest"], extras, observed


def _native_window(plan, sources, infos, window, pages):
    key = plan["sourceKeys"]["netshopSpu"][window]
    if key is None:
        _need(pages is None)
        return [], set()
    _need(type(pages) is list and
        plan["sourceBindings"][key]["capacityStatus"] == "within_current_v2_page_budget")
    family, rows, _ = daily._native_pages(plan, sources[key], infos[key], pages)
    _need(family == "netshopSpu")
    totals = daily._null(planning.PRODUCT_METRICS)
    for row in rows:
        daily._add(totals, row["metrics"])
    _need(totals == infos[key]["expected"]["metrics"],
        "原生SPU完整事实未守恒于封存来源控制汇总")
    return rows, {row["date"] for row in rows}


def _group_window(rows, days, source_key, source_days, identity):
    grouped = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[identity(row)][row["date"]].append(row)
    values = {}
    for key, by_day in grouped.items():
        metrics = daily._null(planning.ERP_METRICS if "sourceFactCount" in rows[0]
            else planning.PRODUCT_METRICS)
        for day_rows in by_day.values():
            for row in day_rows:
                daily._add(metrics, row["metrics"],
                    weight=row.get("sourceFactCount", 1))
        _need(all(cell["presentRows"] + cell["missingRows"]
            <= daily.MAX_SOURCE_ROWS for cell in metrics.values()))
        status = ("date_not_covered" if any(day not in source_days for day in days) else
            "entity_not_observed_on_covered_day" if any(day not in by_day for day in days) else
            "observed_rows")
        values[key] = {"sourceKey": source_key, "status": status,
            "observedDays": len(by_day), "metrics": metrics}
    return values


def _cell(entry, metric, period, source_key, source_days):
    if entry is None:
        status = ("missing_source" if source_key is None else
            "selected_no_records" if not source_days else
            "date_not_covered" if any(day not in source_days
                for day in daily._window_days(period)) else
            "entity_not_observed_on_covered_day")
        cell = {"value": None, "presentRows": 0, "missingRows": 0}
        observed = 0
    else:
        status, cell, observed = entry["status"], entry["metrics"][metric], entry["observedDays"]
        if status == "observed_rows" and cell["missingRows"]:
            status = "partial_metric_coverage"
    return {"sourceKey": source_key, "period": period, "status": status,
        "observedDays": observed, **cell}


def prepare_candidate(plan, sources, infos, context, source_keys,
                      erp_materials, native_spu_pages):
    """Return non-authoritative per-source category/SPU observations and comparisons.

    ERP material value: (outer manifest, five-kind chunk map), or None when
    the plan has no ERP source. Native SPU value: complete pages, or None when
    unselected. Both dictionaries must explicitly include all three windows.
    """
    plan = planning.check_candidate(sources, infos, context, source_keys, plan)
    _need(type(erp_materials) is dict and set(erp_materials) == set(WINDOWS)
        and type(native_spu_pages) is dict and set(native_spu_pages) == set(WINDOWS))
    by_source = {source["key"]: source for source in sources}
    windows, proofs = {}, {}
    for window in WINDOWS:
        proof, erp, erp_days = _erp_window(plan, by_source, infos, context,
            window, erp_materials[window])
        native, native_days = _native_window(plan, by_source, infos, window,
            native_spu_pages[window])
        days = daily._window_days(plan["periods"][window])
        erp_key = plan["sourceKeys"]["erpSales"][window]
        native_key = plan["sourceKeys"]["netshopSpu"][window]
        windows[window] = {
            "erpCategory": _group_window(erp["category_day"], days, erp_key,
                erp_days, lambda row: row["category"]),
            "erpSpu": _group_window(erp["spu_day"], days, erp_key,
                erp_days,
                lambda row: (row["category"], row["spuId"])),
            "netshopSpuNative": _group_window(native, days, native_key,
                native_days, lambda row: row["spuId"])}
        windows[window]["sourceDays"] = {"erpSales": erp_days,
            "netshopSpu": native_days}
        proofs[window] = {"erpRollupManifestDigest": proof,
            "netshopSpuEvidenceDigest": infos[native_key]["expected"]["evidenceDigest"]
                if native_key else None}
    rows = []
    for dimension, family, metric_keys in (("erpCategory", "erpSales", planning.ERP_METRICS),
                                           ("erpSpu", "erpSales", planning.ERP_METRICS),
                                           ("netshopSpuNative", "netshopSpu", planning.PRODUCT_METRICS)):
        identities = set().union(*(windows[window][dimension] for window in WINDOWS))
        for identity in sorted(identities):
            for metric in sorted(metric_keys):
                cells = {window: _cell(windows[window][dimension].get(identity),
                    metric, plan["periods"][window], plan["sourceKeys"][family][window],
                    windows[window]["sourceDays"][family])
                    for window in WINDOWS}
                comparisons = {window: (
                    {"status": "historical_identity_unverified", "difference": None,
                     "growthRateBps": None} if dimension != "netshopSpuNative" else
                    shop_compare._comparison(cells["current"], cells[window]))
                    for window in WINDOWS[1:]}
                rows.append({"dimension": dimension,
                    "identity": list(identity) if type(identity) is tuple else identity,
                    "metric": metric, "sourceFamily": family,
                    "identityBasis": "current_master_assignment" if family == "erpSales"
                        else "native_spu_id", "windows": cells,
                    "comparisons": comparisons})
                _need(len(rows) <= MAX_RESULT_ROWS)
    result = {"schemaVersion": SCHEMA, "planDigest": plan["planDigest"],
        "reportId": plan["reportId"], "platform": plan["platform"],
        "shop": plan["shop"], "comparisonRule": plan["comparisonRule"],
        "sourceMaterialProofs": proofs, "rows": rows, "rowCount": len(rows),
        "authorityVerified": False, "registeredRenderer": False,
        "historicalErpOwnershipVerified": False, "crossDomainAmountsAdded": False,
        "limitations": ["ERP品类及SPU使用当次当前主数据映射，历史归属无法证明，跨期差额和增长率均不输出。",
            "商智原生SPU以来源自身的SPU ID分列；只有实体每日可见、指标完整、基期为正时输出增长率。",
            "ERP退款后销售与商智支付不相加；缺源、缺日、部分指标null及实体未出现均不补零。",
            "纯候选不授予封存来源、报告、Agent或文件发布权威。"]}
    _need(len(canonical(result).encode("utf-8")) <= MAX_RESULT_BYTES,
        "品类/SPU三窗口结果超过固定UTF-8容量")
    return {**result, "comparisonDigest": digest(result)}
