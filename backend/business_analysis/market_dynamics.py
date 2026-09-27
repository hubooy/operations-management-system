"""Pure bounded daily TOP-sample views. Supplied hashes grant no authority."""
import json
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, PageReconciler, SCHEMA_VERSION, canonical, comparison_periods, coverage, digest, strict_date
from .evidence_v2 import normalize_sources, _sha
from .promotion_views import _copy

ALGORITHM_VERSION = "market-daily-dynamics-v1"
MAX_ROWS, MAX_PAGES, MAX_BYTES = 200_000, 2000, 64 * 1024 * 1024
METRICS = frozenset(("sampleGmvLowerCents", "sampleGmvUpperCents", "sampleQuantityLower", "sampleQuantityUpper", "productDayVisitorsLower", "productDayVisitorsUpper"))
PAIRS = (("sampleGmvLowerCents", "sampleGmvUpperCents"), ("sampleQuantityLower", "sampleQuantityUpper"), ("productDayVisitorsLower", "productDayVisitorsUpper"))
ROW_FIELDS = frozenset(("rowId", "sourceRowHash", "batchId", "platform", "shopName", "date", "category", "skuId", "spuId", "productName", "dimensions", "sample", "metrics"))
LIMITATIONS = ["固定榜单逐日TOP样本，不是全行业、真实份额或用户店铺。", "缺席仅表示未在该TOP样本观察到，不代表零销量、退市或无需求。",
    "上下界分别保留，缺界不补零、不取中点；商品日访客不是去重UV。", "纯层无法从date还原源period_start；单日来源资格须由owning reader保证。", "输入摘要不是权限，尚未接工具、模型或文件。"]


def _need(ok, message="市场动态合同无效"):
    if not ok: raise AnalysisContractError(message)


def _int(value, minimum=0):
    _need(type(value) is int and minimum <= value <= MAX_SAFE_INTEGER, "市场整数无效")
    return value


def _ingest(source, pages, expected):
    source = _copy(source, 8192); expected = _copy(expected, 8192)
    _need(type(source) is dict and set(source) == {"key", "domain", "query"})
    entry = normalize_sources([source])[0]
    _need(entry["domain"] == "market")
    query = entry["query"]; periods = comparison_periods(query["startDate"], query["endDate"])
    window = periods[query["window"]]
    reconciler, records, identities, observed = PageReconciler(), [], set(), set()
    cursor, header, byte_count = None, None, 0
    for page_number, supplied in enumerate(pages):
        _need(page_number < MAX_PAGES, "市场页数超限")
        page = _copy(supplied, 131_072)
        byte_count += len(canonical(page).encode("utf-8")); _need(byte_count <= MAX_BYTES, "市场源字节超限")
        _need(page.get("schemaVersion") == SCHEMA_VERSION and page.get("source") == "market_daily_top"
              and page.get("sourceDataset") == "market_daily_top" and page.get("monetaryUnit") == "CNY_CENT")
        filters = page.get("filters"); _need(type(filters) is dict)
        limit = filters.get("limit"); _need(type(limit) is int and 1 <= limit <= 100)
        fixed = {k: query[k] for k in ("platform", "category", "scope", "rankingDimension", "priceBandFilter", "window")}
        _need(filters == {**fixed, "limit": limit, "periods": periods, "shop": ""}, "市场榜单范围不一致")
        _need(type(page.get("items")) is list and len(page["items"]) <= limit)
        pagination = page.get("pagination"); _need(type(pagination) is dict and set(pagination) == {"hasMore", "nextCursor", "limit"})
        _need(type(pagination["hasMore"]) is bool and pagination["limit"] == limit)
        _need(pagination["nextCursor"] is None or type(pagination["nextCursor"]) is str and 0 < len(pagination["nextCursor"]) <= 1600)
        stable = {k: page.get(k) for k in ("sourceRef", "sourceRevision", "filters", "metricSemantics")}
        _sha(stable["sourceRef"], "sourceRef")
        _need(type(stable["sourceRevision"]) is str and bool(stable["sourceRevision"]))
        if header is None:
            header = {**stable, "coverage": page.get("coverage"), "excludedOverlappingPeriodRows": _int(page.get("excludedOverlappingPeriodRows"))}
            control = page.get("control")
            _need(type(control) is dict and set(control) == {"rowCount", "typedTotals"} and type(control["typedTotals"]) is dict and set(control["typedTotals"]) == METRICS)
            _int(control["rowCount"])
            for value in control["typedTotals"].values(): _int(value)
        else:
            _need(all(header[k] == v for k,v in stable.items()), "市场页间来源变化")
            _need(page.get("coverage") is None and page.get("excludedOverlappingPeriodRows") is None)
        for row in page["items"]:
            _need(type(row) is dict and set(row) == ROW_FIELDS, "仅支持规范单日行，不接受周月区间字段")
            _need(type(row["rowId"]) is str and row["rowId"].isascii() and row["rowId"].isdigit()
                  and str(_int(int(row["rowId"]), 1)) == row["rowId"])
            _sha(row["sourceRowHash"], "sourceRowHash")
            day = row["date"]; _need(type(day) is str and strict_date(day).isoformat() == day and window["startDate"] <= day <= window["endDate"])
            _need(row["platform"] == "京东" and row["shopName"] == "" and row["category"] == query["category"])
            dimension = "skuId" if query["rankingDimension"] == "SKU" else "spuId"
            other = "spuId" if dimension == "skuId" else "skuId"
            _need(type(row[dimension]) is str and 0 < len(row[dimension]) <= 500 and row[other] is None, "市场商品身份无效")
            dimensions = row["dimensions"]
            _need(type(dimensions) is dict and set(dimensions) == {"brand", "marketScope", "operationMode"} and dimensions["marketScope"] == query["scope"])
            identity = (day, row[dimension]); _need(identity not in identities, "市场商品日重复")
            identities.add(identity); observed.add(day)
            sample = row["sample"]
            _need(type(sample) is dict and set(sample) == {"rank", "priceLowerCents", "priceUpperCents", "priceEstimated"})
            if sample["rank"] is not None: _int(sample["rank"], 1)
            _need(type(sample["priceEstimated"]) is bool)
            _need(type(row["metrics"]) is dict and set(row["metrics"]) == METRICS)
            for value, pairs in ((sample, (("priceLowerCents", "priceUpperCents"),)), (row["metrics"], PAIRS)):
                for lower, upper in pairs:
                    for key in (lower, upper):
                        if value[key] is not None: _int(value[key])
                    _need(value[lower] is None or value[upper] is None or value[lower] <= value[upper], "市场区间倒置")
            _need(len(records) < MAX_ROWS, "市场源行数超限")
            records.append(row)
        reconciler.consume(page, request_cursor=cursor); cursor = pagination["nextCursor"]
    _need(header is not None, "市场来源无首页")
    _need(reconciler.result() == expected, "市场完整页控制或封存摘要不匹配")
    _need(header["coverage"] == coverage(window, observed), "市场日期覆盖不匹配")
    header.update(inputBytes=byte_count, inputPages=page_number + 1, sourceRowCount=len(records))
    return entry, header, records


def _output(view, sources, headers, rows, extra=None):
    _need(len(rows) <= MAX_ROWS, "市场输出行数超限")
    value = {"schemaVersion": "market-dynamics-table-v1", "algorithmVersion": ALGORITHM_VERSION, "view": view,
        "sources": sources, "sourceMetadata": headers, "rows": rows, "rowCount": len(rows), "authorityVerified": False,
        "limitations": LIMITATIONS, **(extra or {})}
    value["tableDigest"] = digest(value)
    raw = canonical(value); _need(len(raw.encode("utf-8")) <= MAX_BYTES, "市场输出字节超限，整表拒绝")
    return json.loads(raw)


def price_band(source, pages, expected, bands):
    """Declared non-overlapping [lower, upperExclusive) bands; never midpoint."""
    bands = _copy(bands, 8192); _need(type(bands) is list and 1 <= len(bands) <= 20)
    end, keys = 0, set()
    for index, band in enumerate(bands):
        _need(type(band) is dict and set(band) == {"key", "lowerCents", "upperExclusiveCents"})
        _need(type(band["key"]) is str and 0 < len(band["key"]) <= 100 and band["key"] not in keys and not band["key"].startswith("unallocated_"))
        keys.add(band["key"]); lower = _int(band["lowerCents"])
        upper = band["upperExclusiveCents"]
        _need(end is not None and lower >= end, "价格段重叠或乱序")
        if upper is not None: _need(_int(upper) > lower)
        else: _need(index == len(bands)-1)
        end = upper
    entry, header, records = _ingest(source, pages, expected)
    groups = {}
    for row in records:
        sample = row["sample"]; lo, hi = sample["priceLowerCents"], sample["priceUpperCents"]
        key = "unallocated_estimated" if sample["priceEstimated"] else "unallocated_missing" if lo is None or hi is None else "unallocated_interval"
        if not sample["priceEstimated"] and lo is not None and hi is not None:
            for band in bands:
                if lo >= band["lowerCents"] and (band["upperExclusiveCents"] is None or hi < band["upperExclusiveCents"]):
                    key = band["key"]; break
        group = groups.setdefault(key, {"bandKey": key, "members": [], "metrics": {m: {"value": None, "presentRows": 0, "missingRows": 0} for m in sorted(METRICS)}})
        group["members"].append({k: row[k] for k in ("skuId", "spuId", "date", "sample", "sourceRowHash")})
        for metric, cell in group["metrics"].items():
            number = row["metrics"][metric]
            if number is None: cell["missingRows"] += 1
            else:
                cell["presentRows"] += 1; cell["value"] = _int((cell["value"] or 0) + number)
    rows = []
    for key in sorted(groups):
        group = groups[key]; group["members"].sort(key=lambda r: (r["date"], r["skuId"] or r["spuId"]))
        group["rowId"] = digest([ALGORITHM_VERSION, entry, header, bands, group]); rows.append(group)
    return _output("price_band", [entry], [header], rows, {"bands": bands, "shareEstimated": False})


def rank_entry_exit(source, pages, expected, baseline_source, baseline_pages, baseline_expected):
    """First slice compares two explicit single-day windows, not period ranks."""
    current, head, rows = _ingest(source, pages, expected)
    previous, before, old = _ingest(baseline_source, baseline_pages, baseline_expected)
    _need(head["inputBytes"] + before["inputBytes"] <= MAX_BYTES
          and head["inputPages"] + before["inputPages"] <= MAX_PAGES
          and len(rows) + len(old) <= MAX_ROWS, "两期市场输入合计超过容量")
    a,b = current["query"], previous["query"]
    _need(current["key"] != previous["key"] and a["window"] == "current" and b["window"] in ("previous", "yearAgo")
        and {k:v for k,v in a.items() if k != "window"} == {k:v for k,v in b.items() if k != "window"}, "进出榜比较范围或标签不一致")
    _need(a["startDate"] == a["endDate"], "首版进出榜只支持明确单日对单日，不能把多日排名混成一期")
    dimension = "skuId" if a["rankingDimension"] == "SKU" else "spuId"
    now, past = {r[dimension]: r for r in rows}, {r[dimension]: r for r in old}
    result = []
    for key in sorted(set(now) | set(past)):
        left, right = now.get(key), past.get(key)
        complete = head["coverage"]["status"] == before["coverage"]["status"] == "dates_present"
        state = "both_observed" if left and right else "entered_observed_top_sample" if left and complete else "left_observed_top_sample" if right and complete else "insufficient_date_coverage"
        def side(row):
            return {"status": "observed" if row else "not_observed_in_top_sample", "date": row["date"] if row else None,
                "rank": row["sample"]["rank"] if row else None, "metrics": row["metrics"] if row else None}
        item = {"skuId": key if dimension == "skuId" else None, "spuId": key if dimension == "spuId" else None,
            "current": side(left), "baseline": side(right), "status": state,
            "rankImprovement": right["sample"]["rank"] - left["sample"]["rank"] if left and right and left["sample"]["rank"] is not None and right["sample"]["rank"] is not None else None}
        item["rowId"] = digest([ALGORITHM_VERSION, current, previous, head, before, item]); result.append(item)
    return _output("rank_entry_exit", [current,previous], [head,before], result)
