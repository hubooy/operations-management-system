"""Three bounded typed tables from complete composite market NDJSON material.

The manifest and rows are untrusted copies until an owning caller compares
their bindings with the live sealed report. No delivery authority is issued.
"""
from contextlib import contextmanager
import hashlib
import json
import sqlite3
from tempfile import TemporaryDirectory

from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest
from .evidence_v2 import normalize_sources
from .market_dynamics import ALGORITHM_VERSION as BAND_ALGORITHM, METRICS
from .market_dynamics_v2 import ALGORITHM_VERSION as RANK_ALGORITHM, observation_dates
from . import market_report_tables as previous
from .report_files import Column, Table, text


SCHEMA = "business-market-composite-materials-v2"
VIEWS = previous.VIEWS
MAX_ROWS, MAX_BYTES, MAX_CHUNK_BYTES = previous.MAX_ROWS, previous.MAX_BYTES, previous.MAX_CHUNK_BYTES
NOTE = ("仅封存京东逐日TOP样本；区间价格带汇总与成员不可相加，缺榜或缺日期不等于零销量；"
    "市场SKU/SPU不证明本店、ERP或B端销售归属。")


def _need(ok, message="市场组合类型表与材料或封存来源不一致"):
    if not ok:
        raise AnalysisContractError(message)


def columns(view):
    if view != "rank_entry_exit":
        return previous.columns(view)
    old = previous.columns(view)
    return (*old[:-1], Column("current.sourceRowHash", "本期市场源行摘要"),
        Column("baseline.sourceRowHash", "基期市场源行摘要"), old[-1])


def _identity(row):
    sku, spu = row.get("skuId"), row.get("spuId")
    _need((type(sku) is str and 0 < len(sku) <= 500 and bool(sku.strip()) and spu is None)
        or (type(spu) is str and 0 < len(spu) <= 500 and bool(spu.strip()) and sku is None),
        "市场SKU/SPU须恰有一个非空有界文本身份")


def _side(value, day, date_present):
    _need(type(value) is dict and set(value) ==
        {"status", "date", "rank", "metrics", "sourceRowHash"})
    status = value["status"]
    _need((status in {"observed", "not_observed_in_top_sample"} and date_present)
        or (status == "date_not_covered" and not date_present),
        "市场单侧观察状态与日期覆盖不一致")
    if status != "observed":
        _need(all(value[key] is None for key in ("date", "rank", "metrics", "sourceRowHash")))
        return status, None, None, None, None, None
    _need(value["date"] == day and type(value["metrics"]) is dict
        and set(value["metrics"]) == METRICS)
    if value["rank"] is not None:
        previous._int(value["rank"], 1)
    for metric in value["metrics"].values():
        if metric is not None:
            previous._int(metric)
    for lower, upper in (("sampleGmvLowerCents", "sampleGmvUpperCents"),
            ("sampleQuantityLower", "sampleQuantityUpper"),
            ("productDayVisitorsLower", "productDayVisitorsUpper")):
        a, b = value["metrics"][lower], value["metrics"][upper]
        _need(a is None or b is None or a <= b, "市场区间上下界倒置")
    return (status, day, value["rank"], value["metrics"]["sampleGmvLowerCents"],
        value["metrics"]["sampleGmvUpperCents"], previous._sha(value["sourceRowHash"]))


def _project_rank(row, root, index, dates, observed):
    _need(type(row) is dict and set(row) == {"rowIndex", "id", "sourceGroupId",
        "skuId", "spuId", "current", "baseline", "status", "rankImprovement"}
        and row["rowIndex"] == index)
    body = {key: value for key, value in row.items() if key not in {"rowIndex", "id"}}
    _need(row["id"] == digest([root, "rank_entry_exit", index, body]))
    _identity(row)
    group = previous._sha(row["sourceGroupId"])
    current = _side(row["current"], dates["current"], observed["currentDatePresent"])
    baseline = _side(row["baseline"], dates["baseline"], observed["baselineDatePresent"])
    status = row["status"]
    if observed["bothDatesPresent"]:
        expected = ("both_observed" if current[0] == baseline[0] == "observed" else
            "entered_observed_top_sample" if current[0] == "observed" else
            "left_observed_top_sample" if baseline[0] == "observed" else None)
        _need(status == expected and expected is not None)
    else:
        _need(status == "insufficient_date_coverage")
    improvement = row["rankImprovement"]
    if improvement is not None:
        previous._int(improvement, -MAX_SAFE_INTEGER)
        _need(current[2] is not None and baseline[2] is not None
            and improvement == baseline[2] - current[2])
    else:
        _need(current[2] is None or baseline[2] is None)
    values = [index, previous._sha(row["id"]), group,
        previous._cell(row["skuId"]), previous._cell(row["spuId"]), text(status),
        current[0], baseline[0], current[1], baseline[1], current[2], baseline[2],
        improvement, current[3], current[4], baseline[3], baseline[4],
        current[5], baseline[5], text(canonical(row))]
    _need(len(values) == len(columns("rank_entry_exit")))
    return values


def _validate_manifest(manifest, pages_by_view):
    _need(type(manifest) is dict and len(canonical(manifest).encode("utf-8")) <= 128*1024)
    _need(manifest.get("schemaVersion") == SCHEMA
        and manifest.get("algorithms") == {"priceBand": BAND_ALGORITHM,
            "rankEntryExit": RANK_ALGORITHM}
        and manifest.get("authorityVerified") is False
        and manifest.get("registeredRenderer") is False
        and manifest.get("manifestDigest") == digest({key: child for key, child
            in manifest.items() if key != "manifestDigest"}))
    authority = manifest.get("authority")
    _need(type(authority) is dict and authority ==
        {"selectedTopSampleReconciled": True, "wholeMarketCoverageVerified": False,
         "ownProductIdentityVerified": False, "priceSummaryAndMembersAdditive": False,
         "marketAndOwnSalesAdditive": False})
    _need(manifest.get("priceBandSourceKey") == manifest.get("rankCurrentSourceKey")
        and manifest["rankCurrentSourceKey"] != manifest.get("rankBaselineKey"))
    descriptors = manifest.get("sourceDescriptors")
    _need(type(descriptors) is dict and set(descriptors) ==
        {"priceBand", "rankCurrent", "rankBaseline"}
        and descriptors["priceBand"] == descriptors["rankCurrent"]
        and manifest.get("sourceDescriptorsDigest") == digest(descriptors))
    for source in descriptors.values():
        _need(type(source) is dict and set(source) ==
            {"ordinal", "key", "domain", "query", "queryDigest"}
            and source["ordinal"] == 1
            and source["queryDigest"] == digest(source["query"])
            and normalize_sources([{key: source[key] for key in
                ("key", "domain", "query")}])[0] == source)
    current, baseline = descriptors["rankCurrent"], descriptors["rankBaseline"]
    _need(current["key"] == manifest["rankCurrentSourceKey"]
        and baseline["key"] == manifest["rankBaselineKey"]
        and current["domain"] == baseline["domain"] == "market"
        and current["query"]["window"] == "current"
        and baseline["query"]["window"] in ("previous", "yearAgo")
        and {key: value for key, value in current["query"].items() if key != "window"}
            == {key: value for key, value in baseline["query"].items() if key != "window"})
    dates = manifest.get("observationDates")
    observed = manifest.get("rankObservationCoverage")
    _need(type(dates) is dict and set(dates) == {"current", "baseline"}
        and type(observed) is dict and set(observed) ==
            {"currentDatePresent", "baselineDatePresent", "bothDatesPresent"}
        and all(type(value) is bool for value in observed.values())
        and observed["bothDatesPresent"] ==
            (observed["currentDatePresent"] and observed["baselineDatePresent"]))
    observation_dates(current["query"], dates["current"], dates["baseline"],
        baseline["query"]["window"])
    specs = manifest.get("tables")
    _need(type(specs) is list and [spec.get("view") for spec in specs] == list(VIEWS)
        and type(pages_by_view) is dict and set(pages_by_view) == set(VIEWS))
    _need(specs[0]["sourceTableDigest"] == specs[1]["sourceTableDigest"]
        and specs[0]["binding"] == specs[1]["binding"])
    for spec in specs:
        _need(type(spec) is dict and set(spec) ==
            {"view", "binding", "bindingDigest", "sourceDescriptorDigest",
             "sourceTableDigest", "rowCount", "pageCount",
             "ndjsonBytes", "ndjsonSha256"})
        binding = spec["binding"]
        _need(type(binding) is dict and binding.get("reportBinding") == manifest["reportBinding"]
            and binding.get("tableBindingDigest") == spec["sourceTableDigest"]
            and spec["bindingDigest"] == digest(binding)
            and spec["sourceDescriptorDigest"] == digest(
                [descriptors["priceBand"]] if spec["view"] != "rank_entry_exit"
                else [descriptors["rankCurrent"], descriptors["rankBaseline"]]))
        if spec["view"] == "rank_entry_exit":
            _need(binding.get("schemaVersion") == "business-market-observation-binding-v2"
                and binding.get("currentSourceKey") == manifest["rankCurrentSourceKey"]
                and binding.get("baselineSourceKey") == manifest["rankBaselineKey"]
                and binding.get("currentObservationDate") == dates["current"]
                and binding.get("baselineObservationDate") == dates["baseline"]
                and binding.get("algorithmVersion") == RANK_ALGORITHM
                and type(binding.get("sourceProofDigests")) is dict
                and set(binding["sourceProofDigests"]) ==
                    {manifest["rankCurrentSourceKey"], manifest["rankBaselineKey"]}
                and all(previous._sha(value) for value in binding["sourceProofDigests"].values()))
        else:
            _need(binding.get("schemaVersion") == "business-market-dynamics-binding-v1"
                and binding.get("sourceKey") == manifest["priceBandSourceKey"]
                and binding.get("baselineKey") is None
                and binding.get("view") == "price_band"
                and binding.get("algorithmVersion") == BAND_ALGORITHM
                and binding.get("bandsDigest") == digest(manifest["bands"]))
    return specs, dates, observed


@contextmanager
def tables(manifest, pages_by_view):
    """Validate all three complete streams before yielding one-pass typed tables."""
    specs, dates, observed = _validate_manifest(manifest, pages_by_view)
    totals = {"rows": 0, "pages": 0, "bytes": 0, "projected": 0}
    with TemporaryDirectory(prefix="teruisi-market-composite-tables-") as folder:
        db = sqlite3.connect(folder+"/tables.sqlite")
        active = [True]
        try:
            db.execute("PRAGMA cache_size=-2048")
            db.execute("PRAGMA max_page_count=32768")
            db.execute("CREATE TABLE rows(view INTEGER,ordinal INTEGER,payload TEXT,PRIMARY KEY(view,ordinal)) WITHOUT ROWID")
            for index, spec in enumerate(specs):
                view, root = spec["view"], previous._sha(spec["sourceTableDigest"])
                sha, count, pages, size = hashlib.sha256(), 0, 0, 0
                for raw in pages_by_view[view]:
                    _need(type(raw) is bytes and len(raw) <= MAX_CHUNK_BYTES
                        and (not raw or raw.endswith(b"\n")))
                    sha.update(raw); size += len(raw); pages += 1
                    totals["bytes"] += len(raw); totals["pages"] += 1
                    _need(totals["bytes"] <= MAX_BYTES and totals["pages"] <= 20_000)
                    for line in raw.splitlines():
                        try:
                            row = json.loads(line.decode("utf-8"))
                        except (ValueError, UnicodeError, RecursionError) as error:
                            raise AnalysisContractError("市场组合NDJSON编码无效") from error
                        _need(canonical(row).encode("utf-8") == line)
                        if view == "price_band_members":
                            _identity(row)
                        projected = (previous._project(row, view, root, count)
                            if view != "rank_entry_exit" else
                            _project_rank(row, root, count, dates, observed))
                        payload = canonical(projected)
                        totals["projected"] += len(payload.encode("utf-8"))
                        totals["rows"] += 1; count += 1
                        _need(totals["rows"] <= MAX_ROWS and totals["projected"] <= MAX_BYTES)
                        db.execute("INSERT INTO rows VALUES (?,?,?)", (index, count-1, payload))
                _need(count == previous._int(spec["rowCount"])
                    and pages == previous._int(spec["pageCount"], 1)
                    and size == previous._int(spec["ndjsonBytes"])
                    and sha.hexdigest() == previous._sha(spec["ndjsonSha256"]))
            _need(totals["rows"] == previous._int(manifest["rowCount"])
                and totals["pages"] == previous._int(manifest["pageCount"])
                and totals["bytes"] == previous._int(manifest["ndjsonBytes"]))
            db.commit()
            def stream(index):
                _need(active[0], "市场组合表已离开临时生命周期")
                cursor = db.execute("SELECT payload FROM rows WHERE view=? ORDER BY ordinal", (index,))
                try:
                    for (payload,) in cursor:
                        _need(active[0], "市场组合表已离开临时生命周期")
                        yield json.loads(payload)
                finally:
                    if active[0]: cursor.close()
            titles = ("市场区间价格带样本汇总", "市场区间价格带商品日样本", "市场固定两日进出榜")
            result = tuple(Table("market-v2-"+spec["view"], titles[index], NOTE,
                columns(spec["view"]), stream(index), spec["rowCount"])
                for index, spec in enumerate(specs))
            yield {"sourceMaterials": json.loads(canonical(manifest)),
                "authorityVerified": False, "projectedBytes": totals["projected"],
                "tableColumns": [len(table.columns) for table in result]}, result
        except sqlite3.DatabaseError as error:
            raise AnalysisContractError("市场组合表临时空间不可用或超限") from error
        except (KeyError, TypeError, ValueError, UnicodeError, OverflowError,
                RecursionError) as error:
            raise AnalysisContractError("市场组合材料字段或编码无效") from error
        finally:
            active[0] = False
            db.close()
