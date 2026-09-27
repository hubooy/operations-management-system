"""Unregistered, data-only shop/day and SKU/day column alignment candidate.

Every source keeps its own metric namespace. This never attributes advertising
to ERP orders, turns product-day visitors into shop UV, or combines SKU and
native SPU payment totals.
"""
from datetime import date, timedelta
import hashlib
import json
import re

from . import cross_source_kpi_plan as plan_contract, erp_fact_rollups
from .contracts import (AnalysisContractError, MAX_SAFE_INTEGER, PageReconciler,
    canonical, comparison_periods, coverage, digest, strict_date)


SCHEMA = "business-cross-source-daily-columns-candidate-v1"
ERP_SCHEMA = "business-erp-report-rollup-materials-candidate-v1"
MAX_SOURCE_ROWS = plan_contract.MAX_V2_SOURCE_PAGES * plan_contract.MAX_V2_PAGE_ROWS
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_ROW_BYTES = 38_000
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_NATIVE = {"netshopSku": ("jd_sku_daily", "sku_daily"),
    "netshopSpu": ("jd_sku_daily", "spu_daily"),
    "promotion": ("jd_promotion", "ad")}
_ERP_KINDS = ("shop_day", "sku_day", "unassigned_day")


def _need(ok, message="跨来源逐日列对齐材料不一致"):
    if not ok:
        raise AnalysisContractError(message)


def _zero(keys):
    return {key: 0 for key in sorted(keys)}


def _null(keys):
    return {key: {"value": None, "presentRows": 0, "missingRows": 0}
        for key in sorted(keys)}


def _add(target, values, *, weight=1):
    _need(type(values) is dict and set(values) == set(target), "来源指标字段集合变化")
    _need(type(weight) is int and 1 <= weight <= MAX_SOURCE_ROWS)
    for key, source in values.items():
        if type(source) is dict:
            _need(set(source) == {"value", "presentRows", "missingRows"}
                and type(source["presentRows"]) is int
                and type(source["missingRows"]) is int
                and 0 <= source["presentRows"] <= MAX_SOURCE_ROWS
                and 0 <= source["missingRows"] <= MAX_SOURCE_ROWS
                and ((source["value"] is None and source["presentRows"] == 0)
                    or (type(source["value"]) is int
                        and abs(source["value"]) <= MAX_SAFE_INTEGER
                        and source["presentRows"] > 0)),
                "逐日指标有值/缺值证明无效")
            amount, present, missing = (source[name] for name in
                ("value", "presentRows", "missingRows"))
        else:
            _need(source is None or type(source) is int
                and abs(source) <= MAX_SAFE_INTEGER,
                "来源指标不是有界整数或null")
            amount, present, missing = (source, weight, 0) if source is not None else (None, 0, weight)
        cell = target[key]
        cell["presentRows"] += present
        cell["missingRows"] += missing
        _need(cell["presentRows"] + cell["missingRows"] <= MAX_SOURCE_ROWS,
            "逐日指标行数超过当前封存容量")
        if amount is not None:
            cell["value"] = (cell["value"] or 0) + amount
            _need(abs(cell["value"]) <= MAX_SAFE_INTEGER,
                "逐日指标加总超出无损范围")


def _window_days(period):
    first, last = strict_date(period["startDate"]), strict_date(period["endDate"])
    _need(0 <= (last-first).days < 94)
    return [(first+timedelta(days=offset)).isoformat()
        for offset in range((last-first).days+1)]


def _source_proof(plan, source, info, proof, dates):
    key = source["key"]
    bound = plan["sourceBindings"][key]
    _need(proof == info["expected"] and proof["reconciled"] is True
        and proof["sourceRef"] == bound["sourceRef"]
        and proof["evidenceDigest"] == bound["evidenceDigest"]
        and proof["rowCount"] == bound["rowCount"]
        and canonical(info["metadata"]["coverage"]) == canonical(bound["coverage"])
        and digest(bound["coverage"]) == bound["coverageDigest"]
        and info["metadata"]["sourceRevision"] == bound["sourceRevision"]
        and canonical(coverage(plan["periods"][source["query"]["window"]], dates))
            == canonical(bound["coverage"]), "来源完整页、控制总额或业务日覆盖与固定计划不同")


def _native_pages(plan, source, info, pages):
    family = next(family for family in _NATIVE
        if plan["sourceKeys"][family][source["query"]["window"]] == source["key"])
    expected_source, expected_dataset = _NATIVE[family]
    query = source["query"]
    expected_filters = {key: query[key] for key in ("platform", "shop", "dataset", "window")}
    expected_filters["periods"] = comparison_periods(query["startDate"], query["endDate"])
    window = plan["periods"][query["window"]]
    verifier, rows, observed, source_hashes, page_count, source_bytes = (
        PageReconciler(), [], set(), set(), 0, 0)
    for page in pages:
        page_count += 1
        _need(type(page) is dict and type(page.get("items")) is list)
        page_bytes = len(canonical(page).encode("utf-8"))
        source_bytes += page_bytes
        _need(len(page["items"]) <= plan_contract.MAX_V2_PAGE_ROWS
            and page_bytes <= 131072 and source_bytes <= 64*1024*1024,
            "封存网店页超出当前收集容量")
        coverage_ok = (canonical(page.get("coverage")) ==
            canonical(info["metadata"]["coverage"]) if page_count == 1
            else page.get("coverage") is None)
        _need(page_count <= info["pageCount"]
            and page.get("filters") == expected_filters
            and page.get("source") == expected_source
            and page.get("sourceDataset") == expected_dataset
            and page.get("sourceRevision") == info["metadata"]["sourceRevision"]
            and page.get("monetaryUnit") == "CNY_CENT"
            and type(page.get("pagination")) is dict
            and page["pagination"].get("limit") == 100
            and coverage_ok,
            "网店来源页身份、窗口或首页覆盖无效")
        verifier.consume(page, request_cursor=verifier.expected_cursor)
        for item in page["items"]:
            _need(type(item) is dict)
            source_hash = item.get("sourceRowHash")
            _need(type(source_hash) is str and 1 <= len(source_hash) <= 256
                and source_hash not in source_hashes,
                "网店/推广源内容摘要重复或缺失，候选无法证明完整唯一事实")
            source_hashes.add(source_hash)
            _need(item.get("platform") == query["platform"]
                and item.get("shopName") == query["shop"]
                and type(item.get("date")) is str
                and window["startDate"] <= item["date"] <= window["endDate"]
                and type(item.get("metrics")) is dict
                and set(item["metrics"]) == set(info["expected"]["metrics"]),
                "网店事实跨店铺、日期或指标口径")
            strict_date(item["date"])
            if family == "netshopSku":
                _need(type(item.get("skuId")) is str and bool(item["skuId"].strip()))
                _need(item.get("spuId") is None or type(item["spuId"]) is str)
            elif family == "netshopSpu":
                _need(type(item.get("spuId")) is str and bool(item["spuId"].strip()))
            else:
                dimensions = item.get("dimensions")
                _need(type(dimensions) is dict)
                promoted = dimensions.get("promotedSkuId")
                _need(promoted is None or type(promoted) is str and
                    bool(promoted.strip()) and len(promoted) <= 600,
                    "推广SKU身份缺失只能进入明确缺失桶")
            observed.add(item["date"])
            rows.append(item)
            _need(len(rows) <= MAX_SOURCE_ROWS)
    _need(page_count == info["pageCount"])
    proof = verifier.result()
    _source_proof(plan, source, info, proof, observed)
    return family, rows, source_bytes


def _erp_rows(manifest, chunks, kind, period, shop, platform):
    spec = next((item for item in manifest["tables"] if item["kind"] == kind), None)
    _need(spec is not None)
    sha, size, rows, pending, page_count = hashlib.sha256(), 0, [], bytearray(), 0
    for raw in chunks:
        page_count += 1
        _need(type(raw) is bytes and len(raw) <= erp_fact_rollups.MAX_CHUNK_BYTES)
        size += len(raw); sha.update(raw); pending.extend(raw)
        _need(size <= erp_fact_rollups.MAX_OUTPUT_BYTES)
        while b"\n" in pending:
            line, _, rest = pending.partition(b"\n")
            pending = bytearray(rest)
            _need(0 < len(line) < MAX_ROW_BYTES)
            item = json.loads(line)
            _need(type(item) is dict and type(item.get("rowIndex")) is int
                and item["rowIndex"] == len(rows)
                and type(item.get("id")) is str and _SHA.fullmatch(item["id"]) is not None
                and item.get("period") == period and item.get("shopName") == shop
                and item.get("platform") == platform
                and type(item.get("date")) is str
                and type(item.get("sourceFactCount")) is int
                and item["sourceFactCount"] > 0
                and type(item.get("metrics")) is dict
                and set(item["metrics"]) == plan_contract.ERP_METRICS,
                "ERP五层级回卷行、身份或摘要无效")
            strict_date(item["date"])
            _need(all(type(value) is int and abs(value) <= MAX_SAFE_INTEGER
                for value in item["metrics"].values()))
            if kind == "shop_day":
                _need(all(item.get(name) is None for name in ("skuId", "spuId", "category", "status")))
            elif kind == "sku_day":
                _need(all(type(item.get(name)) is str and item[name].strip()
                    for name in ("skuId", "spuId", "category")) and item.get("status") is None)
            else:
                _need(item.get("status") in {"ambiguous", "incomplete", "unmatched"}
                    and all(item.get(name) is None for name in ("skuId", "spuId", "category")))
            rows.append(item)
            _need(len(rows) <= MAX_SOURCE_ROWS)
        _need(len(pending) < MAX_ROW_BYTES,
            "ERP单行NDJSON超过固定容量")
    _need(not pending and page_count == spec["pageCount"]
        and len(rows) == spec["rowCount"]
        and size == spec["ndjsonBytes"] and sha.hexdigest() == spec["ndjsonSha256"],
        "ERP回卷完整NDJSON与固定材料摘要不同")
    return rows


def _erp_material(plan, context, window, sources, infos, manifest, pages):
    key = plan["sourceKeys"]["erpSales"][window]
    if key is None:
        _need(manifest is None and pages is None)
        return {kind: [] for kind in _ERP_KINDS}
    _need(type(manifest) is dict and type(pages) is dict
        and set(pages) == set(_ERP_KINDS)
        and manifest.get("schemaVersion") == ERP_SCHEMA
        and manifest.get("manifestDigest") == digest({k:v for k,v in manifest.items()
            if k != "manifestDigest"})
        and manifest.get("authorityVerified") is False
        and manifest.get("registeredRenderer") is False
        and manifest.get("netshopAdFinanceCombined") is False
        and manifest.get("historicalOwnershipVerified") is False)
    binding = manifest.get("reportBinding")
    _need(type(binding) is dict
        and binding.get("reportId") == context["reportId"]
        and binding.get("evidenceRunId") == context["evidenceRunId"]
        and binding.get("evidenceVersion") == context["evidenceVersion"]
        and binding.get("sealedDigest") == context["sealedDigest"]
        and binding.get("principalKey") == digest([context["ownerEmail"], canonical(context["scope"])])
        and binding.get("mappingPlanDigest") == plan["mappingPlanDigest"]
        and manifest.get("pairKey") == (plan["currentMappingPairKey"] if window == "current"
            else (plan["baselineMappingPairs"][window] or {}).get("baselinePairKey"))
        and manifest.get("salesKey") == key
        and manifest.get("masterKey") == plan["sourceKeys"]["master"]
        and manifest.get("salesQueryDigest") == digest(sources[key]["query"])
        and manifest.get("masterQueryDigest") == digest(sources[manifest["masterKey"]]["query"])
        and manifest.get("sourceProofs") == {"sales": infos[key]["expected"],
            "master": infos[manifest["masterKey"]]["expected"]}
        and manifest.get("sourceRowCount") == infos[key]["expected"]["rowCount"]
        and type(manifest.get("assignmentSummaryDigest")) is str
        and _SHA.fullmatch(manifest["assignmentSummaryDigest"]) is not None
        and type(manifest.get("rollupManifestDigest")) is str
        and _SHA.fullmatch(manifest["rollupManifestDigest"]) is not None,
        "ERP回卷材料跨报告、来源或mapping pair")
    _need(type(manifest.get("tables")) is list
        and [item.get("kind") for item in manifest["tables"]] ==
            list(erp_fact_rollups.KINDS))
    result = {kind: _erp_rows(manifest, pages[kind], kind, window,
        plan["shop"], plan["platform"]) for kind in _ERP_KINDS}
    totals, counts, day_totals = {}, {}, {}
    valid_days = set(_window_days(plan["periods"][window]))
    for kind, rows in result.items():
        sums = _zero(plan_contract.ERP_METRICS)
        daily = {}
        seen = set(); count = 0
        for row in rows:
            _need(row["date"] in valid_days)
            identity = ((row["date"],) if kind == "shop_day" else
                (row["date"], row["skuId"]) if kind == "sku_day" else
                (row["date"], row["status"]))
            _need(identity not in seen, "ERP回卷日/商品/未分配身份重复")
            seen.add(identity); count += row["sourceFactCount"]
            cell = daily.setdefault(row["date"],
                {"count": 0, "metrics": _zero(plan_contract.ERP_METRICS)})
            cell["count"] += row["sourceFactCount"]
            for metric, amount in row["metrics"].items():
                sums[metric] += amount
                _need(abs(sums[metric]) <= MAX_SAFE_INTEGER)
                cell["metrics"][metric] += amount
                _need(abs(cell["metrics"][metric]) <= MAX_SAFE_INTEGER)
        totals[kind], counts[kind], day_totals[kind] = sums, count, daily
    _need(totals["shop_day"] == manifest["sourceTotals"]
        and totals["sku_day"] == manifest["matchedTotals"]
        and totals["unassigned_day"] == manifest["unassignedTotals"]
        and counts["shop_day"] == manifest["sourceRowCount"]
        and counts["sku_day"] + counts["unassigned_day"] == counts["shop_day"]
        and all((infos[key]["expected"]["metrics"][metric]["value"] or 0) == amount
            for metric, amount in manifest["sourceTotals"].items()),
        "ERP回卷源→店铺→已分配与未分配不守恒")
    for day in sorted(valid_days):
        day_rows = {kind: day_totals[kind].get(day,
            {"count": 0, "metrics": _zero(plan_contract.ERP_METRICS)})
            for kind in _ERP_KINDS}
        shop_count = day_rows["shop_day"]["count"]
        assigned_count = day_rows["sku_day"]["count"]
        unassigned_count = day_rows["unassigned_day"]["count"]
        _need(shop_count == assigned_count + unassigned_count,
            "ERP同业务日源事实数不守恒")
        for metric in plan_contract.ERP_METRICS:
            _need(day_rows["shop_day"]["metrics"][metric]
                == day_rows["sku_day"]["metrics"][metric]
                + day_rows["unassigned_day"]["metrics"][metric],
                "ERP同业务日十一项源指标不守恒")
    actual_days = {row["date"] for row in result["shop_day"]}
    _need(canonical(coverage(plan["periods"][window], actual_days)) ==
        canonical(plan["sourceBindings"][key]["coverage"]))
    return result


def prepare_candidate(plan, sources, infos, context, source_keys, window,
                      erp_manifest, erp_ndjson, native_pages):
    """Align one selected comparison window; inputs confer no DB authority."""
    plan_contract.check_candidate(sources, infos, context, source_keys, plan)
    _need(window in plan_contract.WINDOWS and type(window) is str)
    by_key = {source["key"]: source for source in sources}
    selected = {family: plan["sourceKeys"][family][window]
        for family in plan_contract.FAMILIES}
    expected_native = {key for family, key in selected.items()
        if family != "erpSales" and key is not None}
    _need(type(native_pages) is dict and set(native_pages) == expected_native
        and not any(plan["sourceBindings"][key]["capacityStatus"] !=
            "within_current_v2_page_budget" for key in expected_native),
        "仅接受完整且在当前封存页容量内的所选来源")
    if selected["erpSales"] is not None:
        _need(plan["sourceBindings"][selected["erpSales"]]["capacityStatus"] ==
            "within_current_v2_page_budget")
    erp = _erp_material(plan, context, window, by_key, infos, erp_manifest, erp_ndjson)
    native, native_input_bytes = {}, 0
    for key in sorted(expected_native):
        family, rows, size = _native_pages(plan, by_key[key], infos[key], native_pages[key])
        native_input_bytes += size
        _need(native_input_bytes <= 64*1024*1024,
            "本窗口网店与推广封存材料超过v2事实容量")
        native[family] = rows
    days = _window_days(plan["periods"][window])
    shop = {day: {"erpSales": _null(plan_contract.ERP_METRICS),
        "erpUnassigned": _null(plan_contract.ERP_METRICS),
        "netshopSku": _null(plan_contract.PRODUCT_METRICS),
        "netshopSpuNative": _null(plan_contract.PRODUCT_METRICS),
        "promotion": _null(infos[selected["promotion"]]["expected"]["metrics"]
            if selected["promotion"] is not None else plan_contract.PROMOTION_METRICS)}
        for day in days}
    sku = {}
    sku_identity = {"erp": {}, "netshop": {}}
    def sku_row(day, sku_id, *, missing=False):
        key = (day, sku_id, missing)
        if key not in sku:
            sku[key] = {"date": day, "skuId": sku_id,
                "promotionSkuStatus": "missing_explicit_promotion_sku" if missing else "explicit_sku",
                "erpAssignedSpuId": None, "netshopSkuNativeSpuId": None,
                "erpMatched": _null(plan_contract.ERP_METRICS),
                "netshopSku": _null(plan_contract.PRODUCT_METRICS),
                "promotion": _null(infos[selected["promotion"]]["expected"]["metrics"]
                    if selected["promotion"] is not None else plan_contract.PROMOTION_METRICS)}
        return sku[key]
    for kind, column in (("shop_day", "erpSales"), ("unassigned_day", "erpUnassigned")):
        for row in erp[kind]:
            _add(shop[row["date"]][column], row["metrics"],
                weight=row["sourceFactCount"])
    for row in erp["sku_day"]:
        key = row["skuId"]
        prior = sku_identity["erp"].setdefault(key, row["spuId"])
        _need(prior == row["spuId"], "ERP同SKU的当前SPU身份冲突")
        item = sku_row(row["date"], key)
        item["erpAssignedSpuId"] = row["spuId"]
        _add(item["erpMatched"], row["metrics"], weight=row["sourceFactCount"])
    for family, column in (("netshopSku", "netshopSku"),
            ("netshopSpu", "netshopSpuNative"), ("promotion", "promotion")):
        for row in native.get(family, []):
            day = row["date"]
            _add(shop[day][column], row["metrics"])
            if family == "netshopSpu":
                continue  # Native SPU facts never become SKU rows.
            if family == "netshopSku":
                key = row["skuId"]
                prior = sku_identity["netshop"].setdefault(key, row.get("spuId"))
                _need(prior == row.get("spuId"), "商智同SKU原生SPU身份冲突")
                item = sku_row(day, key)
                item["netshopSkuNativeSpuId"] = row.get("spuId")
                _add(item["netshopSku"], row["metrics"])
            else:
                promoted = row["dimensions"].get("promotedSkuId")
                item = sku_row(day, promoted, missing=promoted is None)
                _add(item["promotion"], row["metrics"])
    rows = []
    status_column = {"erpSales": "erpSales", "netshopSku": "netshopSku",
        "netshopSpu": "netshopSpuNative", "promotion": "promotion"}
    for day in days:
        statuses = {}
        for family, key in selected.items():
            statuses[family] = ("missing_source" if key is None else
                ("partial_metric_coverage" if any(cell["missingRows"]
                    for cell in shop[day][status_column[family]].values()) else
                    "observed_rows") if day in plan["sourceBindings"][key]["coverage"]["presentDates"] else
                "selected_no_records" if plan["sourceBindings"][key]["rowCount"] == 0 else
                "date_not_covered")
        body = {"window": window, "date": day, "platform": plan["platform"],
            "shop": plan["shop"], "sourceDayStatus": statuses, **shop[day]}
        body["id"] = digest([plan["planDigest"], "shop_day", body])
        rows.append(body)
    sku_rows = []
    for key in sorted(sku, key=lambda value: (value[0], value[1] or "", value[2])):
        body = {"window": window, "platform": plan["platform"],
            "shop": plan["shop"], **sku[key]}
        body["id"] = digest([plan["planDigest"], "sku_day", body])
        sku_rows.append(body)
    _need(len(sku_rows) <= MAX_SOURCE_ROWS)
    for family, column in (("netshopSku", "netshopSku"),
            ("promotion", "promotion")):
        key = selected[family]
        if key is None:
            continue
        expected = infos[key]["expected"]["metrics"]
        sums = _null(expected)
        for row in rows:
            _add(sums, row[column])
        _need(sums == expected,
            "逐日列未守恒于网店或推广封存源")
    key = selected["netshopSpu"]
    if key is not None:
        sums = _null(infos[key]["expected"]["metrics"])
        for row in rows:
            _add(sums, row["netshopSpuNative"])
        _need(sums == infos[key]["expected"]["metrics"],
            "原生SPU逐日列未守恒于其独立来源")
    for family, column in (("netshopSku", "netshopSku"),
            ("promotion", "promotion")):
        key = selected[family]
        if key is None:
            continue
        shop_sums = _null(infos[key]["expected"]["metrics"])
        sku_sums = _null(infos[key]["expected"]["metrics"])
        for row in rows:
            _add(shop_sums, row[column])
        for row in sku_rows:
            _add(sku_sums, row[column])
        _need(shop_sums == sku_sums,
            "SKU逐日与本来源店铺逐日金额不守恒")
    material = {"schemaVersion": SCHEMA, "planDigest": plan["planDigest"],
        "reportId": plan["reportId"], "window": window,
        "period": plan["periods"][window], "platform": plan["platform"],
        "shop": plan["shop"], "sourceKeys": selected,
        "sourceBindings": {family: plan["sourceBindings"].get(key) if key else None
            for family, key in selected.items()},
        "erpRollupManifestDigest": erp_manifest["manifestDigest"] if erp_manifest else None,
        "shopDayRows": rows, "skuDayRows": sku_rows,
        "sourceTotalConserved": True,
        "nativeSpuNotSkuRolled": True,
        "promotionMissingSkuBucketPreserved": True,
        "shopUniqueVisitorsAvailable": False,
        "orderAttributionVerified": False,
        "crossDomainAmountsAdded": False,
        "authorityVerified": False, "registeredRenderer": False,
        "limitations": ["ERP销售/退款/成本、商智原生SKU/SPU支付与商品日访客、推广花费及平台归因金额分列，不跨域相加。",
            "原生SPU与SKU汇卷不可替换；商品日访客累计不是店铺去重UV。",
            "缺来源、缺日期及源指标null不补零；缺明确推广SKU留独立桶。",
            "同码当前主数据归属非历史所有权；并列日期不证明订单级推广归因。"]}
    _need(len(canonical(material).encode("utf-8")) <= MAX_OUTPUT_BYTES
        and all(len(canonical(row).encode("utf-8")) <= MAX_ROW_BYTES
            for row in rows + sku_rows), "完整逐日并列材料超过容量")
    material["materialDigest"] = digest(material)
    return material
