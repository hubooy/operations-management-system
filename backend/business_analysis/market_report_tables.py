"""Typed HTML/XLSX tables from complete unregistered market NDJSON materials.

This checks the material's internal integrity. Its manifest is not source or
report authority; the owning file writer must recheck the live report at commit.
"""
from contextlib import contextmanager
import hashlib
import json
import sqlite3
from tempfile import TemporaryDirectory

from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest
from .market_dynamics import ALGORITHM_VERSION, METRICS
from .report_files import Column, Table, text


VIEWS = ("price_band_summary", "price_band_members", "rank_entry_exit")
MAX_ROWS, MAX_BYTES, MAX_CHUNK_BYTES = 200_000, 64 * 1024 * 1024, 38_000
NOTE = "仅京东逐日TOP榜单样本；缺席不等于零销量，区间上下界不取中点，亦不证明本店经营或全行业份额。"
METRIC_LABELS = (("sampleGmvLowerCents", "样本成交下界（分）"),
    ("sampleGmvUpperCents", "样本成交上界（分）"),
    ("sampleQuantityLower", "样本件数下界"), ("sampleQuantityUpper", "样本件数上界"),
    ("productDayVisitorsLower", "商品日访客下界"),
    ("productDayVisitorsUpper", "商品日访客上界"))


def _need(ok, message="市场报告材料不完整或被篡改"):
    if not ok: raise AnalysisContractError(message)


def _int(value, minimum=0):
    _need(type(value) is int and minimum <= value <= MAX_SAFE_INTEGER)
    return value


def _sha(value):
    _need(type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value))
    return value


def columns(view):
    base = (Column("rowIndex", "材料行位置", "integer"), Column("id", "完整材料行ID"),
        Column("sourceGroupId", "原市场动态行ID"))
    if view == "price_band_summary":
        return base + (Column("bandKey", "价格带"), Column("memberCount", "商品日样本行数", "integer")) + tuple(
            Column("metrics."+key+"."+field, label+suffix, "integer")
            for key,label in METRIC_LABELS
            for field,suffix in (("value", ""), ("presentRows", "·有值行数"), ("missingRows", "·缺值行数"))) + (Column("raw", "完整规范分析行JSON"),)
    if view == "price_band_members":
        return base + (Column("bandKey", "价格带"), Column("date", "榜单日期"),
            Column("skuId", "榜单SKU"), Column("spuId", "榜单SPU"),
            Column("sample.rank", "TOP名次", "integer"),
            Column("sample.priceLowerCents", "价格下界（分）", "integer"),
            Column("sample.priceUpperCents", "价格上界（分）", "integer"),
            Column("priceEstimated", "价格为估算"), Column("sourceRowHash", "市场源行摘要"),
            Column("raw", "完整规范分析行JSON"))
    _need(view == "rank_entry_exit")
    return base + (Column("skuId", "榜单SKU"), Column("spuId", "榜单SPU"),
        Column("status", "TOP样本观察状态"), Column("current.status", "本期观察"),
        Column("baseline.status", "基期观察"),
        Column("current.date", "本期日期"), Column("baseline.date", "基期日期"),
        Column("current.rank", "本期名次", "integer"), Column("baseline.rank", "基期名次", "integer"),
        Column("rankImprovement", "名次改善", "integer"),
        Column("current.gmvLower", "本期样本成交下界（分）", "integer"),
        Column("current.gmvUpper", "本期样本成交上界（分）", "integer"),
        Column("baseline.gmvLower", "基期样本成交下界（分）", "integer"),
        Column("baseline.gmvUpper", "基期样本成交上界（分）", "integer"),
        Column("raw", "完整规范分析行JSON"))


def _cell(value):
    if value is None or type(value) is bool: return value
    if type(value) is str: return text(value)
    return _int(value, -MAX_SAFE_INTEGER)


def _metric_cell(cell, count):
    _need(type(cell) is dict and set(cell) == {"value", "presentRows", "missingRows"})
    present, missing = _int(cell["presentRows"]), _int(cell["missingRows"])
    _need(present+missing == count and (cell["value"] is None) == (present == 0))
    return (_int(cell["value"]) if cell["value"] is not None else None, present, missing)


def _side(value):
    _need(type(value) is dict and set(value) == {"status", "date", "rank", "metrics"}
        and value["status"] in {"observed", "not_observed_in_top_sample"})
    rank = value["rank"]
    if rank is not None: _int(rank, 1)
    metrics = value["metrics"]
    if value["status"] == "observed":
        _need(type(value["date"]) is str and type(metrics) is dict and set(metrics) == METRICS)
        lo, hi = metrics["sampleGmvLowerCents"], metrics["sampleGmvUpperCents"]
        if lo is not None: _int(lo)
        if hi is not None: _int(hi)
    else:
        _need(value["date"] is None and rank is None and metrics is None)
        lo = hi = None
    return value["status"], value["date"], rank, lo, hi


def _project(row, view, root, index):
    _need(type(row) is dict and row.get("rowIndex") == index and type(row["rowIndex"]) is int)
    body = {key:value for key,value in row.items() if key not in {"rowIndex", "id"}}
    _need(row.get("id") == digest([root, view, index, body]))
    group = _sha(row.get("sourceGroupId"))
    prefix = [index, _sha(row["id"]), group]
    if view == "price_band_summary":
        _need(set(body) == {"bandKey", "memberCount", "metrics", "sourceGroupId"})
        text(row["bandKey"]); count = _int(row["memberCount"])
        metrics = row["metrics"]
        _need(type(metrics) is dict and set(metrics) == METRICS)
        values = prefix + [row["bandKey"], count]
        for key,_ in METRIC_LABELS: values.extend(_metric_cell(metrics[key], count))
    elif view == "price_band_members":
        _need(set(body) == {"bandKey", "date", "skuId", "spuId", "sample", "sourceRowHash", "sourceGroupId"})
        text(row["bandKey"]); text(row["date"]); _sha(row["sourceRowHash"])
        _need((row["skuId"] is None) != (row["spuId"] is None))
        sample = row["sample"]
        _need(type(sample) is dict and set(sample) == {"rank", "priceLowerCents", "priceUpperCents", "priceEstimated"}
            and type(sample["priceEstimated"]) is bool)
        for key in ("rank", "priceLowerCents", "priceUpperCents"):
            if sample[key] is not None: _int(sample[key], 1 if key == "rank" else 0)
        _need(sample["priceLowerCents"] is None or sample["priceUpperCents"] is None
            or sample["priceLowerCents"] <= sample["priceUpperCents"])
        values = prefix + [row["bandKey"], row["date"], _cell(row["skuId"]), _cell(row["spuId"]),
            sample["rank"], sample["priceLowerCents"], sample["priceUpperCents"],
            sample["priceEstimated"], row["sourceRowHash"]]
    else:
        _need(view == "rank_entry_exit" and set(body) == {"skuId", "spuId", "current", "baseline",
            "status", "rankImprovement", "sourceGroupId"})
        _need((row["skuId"] is None) != (row["spuId"] is None))
        current, baseline = _side(row["current"]), _side(row["baseline"])
        _need(row["status"] in {"both_observed", "entered_observed_top_sample",
            "left_observed_top_sample", "insufficient_date_coverage"})
        improvement = row["rankImprovement"]
        if improvement is not None:
            _int(improvement, -MAX_SAFE_INTEGER)
            _need(current[2] is not None and baseline[2] is not None
                and improvement == baseline[2]-current[2])
        values = prefix + [_cell(row["skuId"]), _cell(row["spuId"]), text(row["status"]),
            current[0], baseline[0], current[1], baseline[1], current[2], baseline[2], improvement,
            current[3], current[4], baseline[3], baseline[4]]
    values.append(text(canonical(row)))
    _need(len(values) == len(columns(view)))
    return values


@contextmanager
def tables(manifest, pages_by_view):
    """Yield summary and three one-pass typed Tables after full NDJSON validation."""
    _need(type(manifest) is dict and len(canonical(manifest).encode("utf-8")) <= 128*1024)
    _need(manifest.get("schemaVersion") == "business-market-export-materials-v1"
        and manifest.get("algorithmVersion") == ALGORITHM_VERSION
        and manifest.get("registeredRenderer") is False
        and manifest.get("manifestDigest") == digest({k:v for k,v in manifest.items() if k != "manifestDigest"}))
    specs = manifest.get("tables")
    _need(type(specs) is list and [spec.get("view") for spec in specs] == list(VIEWS)
        and type(pages_by_view) is dict and set(pages_by_view) == set(VIEWS))
    _need(specs[0]["sourceTableDigest"] == specs[1]["sourceTableDigest"]
        and specs[0]["binding"] == specs[1]["binding"])
    total_bytes, total_rows, total_pages, projected_bytes = 0, 0, 0, 0
    with TemporaryDirectory(prefix="teruisi-market-tables-") as folder:
        db = sqlite3.connect(folder+"/tables.sqlite")
        active = [True]
        try:
            db.execute("PRAGMA cache_size=-2048"); db.execute("PRAGMA max_page_count=32768")
            db.execute("CREATE TABLE rows(view INTEGER,ordinal INTEGER,payload TEXT,PRIMARY KEY(view,ordinal)) WITHOUT ROWID")
            for index, spec in enumerate(specs):
                view = spec["view"]
                _need(set(spec) == {"view", "binding", "sourceTableDigest", "rowCount", "pageCount", "ndjsonBytes", "ndjsonSha256"})
                binding = spec["binding"]
                _need(type(binding) is dict and binding.get("reportBinding") == manifest["reportBinding"]
                    and binding.get("sourceKey") == manifest["sourceKey"]
                    and binding.get("baselineKey") == (manifest["baselineKey"] if view == "rank_entry_exit" else None)
                    and binding.get("view") == ("rank_entry_exit" if view == "rank_entry_exit" else "price_band")
                    and binding.get("algorithmVersion") == ALGORITHM_VERSION
                    and binding.get("tableBindingDigest") == spec["sourceTableDigest"])
                if view != "rank_entry_exit": _need(binding.get("bandsDigest") == digest(manifest["bands"]))
                else: _need(binding.get("bandsDigest") is None)
                root = _sha(spec["sourceTableDigest"])
                sha, count, pages, size = hashlib.sha256(), 0, 0, 0
                for raw in pages_by_view[view]:
                    _need(type(raw) is bytes and len(raw) <= MAX_CHUNK_BYTES and (not raw or raw.endswith(b"\n")))
                    sha.update(raw); size += len(raw); total_bytes += len(raw); pages += 1; total_pages += 1
                    _need(total_bytes <= MAX_BYTES and total_pages <= 20_000)
                    for line in raw.splitlines():
                        try: row = json.loads(line.decode("utf-8"))
                        except (ValueError, UnicodeError, RecursionError) as error: raise AnalysisContractError("市场NDJSON编码无效") from error
                        _need(canonical(row).encode("utf-8") == line, "市场行不是规范JSON")
                        projected = _project(row, view, root, count)
                        payload = canonical(projected)
                        projected_bytes += len(payload.encode("utf-8")); count += 1; total_rows += 1
                        _need(total_rows <= MAX_ROWS and projected_bytes <= MAX_BYTES)
                        db.execute("INSERT INTO rows VALUES (?,?,?)", (index, count-1, payload))
                _need(count == _int(spec["rowCount"]) and pages == _int(spec["pageCount"], 1)
                    and size == _int(spec["ndjsonBytes"]) and sha.hexdigest() == _sha(spec["ndjsonSha256"]))
            _need(total_rows == _int(manifest["rowCount"])
                and total_bytes == _int(manifest["ndjsonBytes"])
                and total_pages == _int(manifest["pageCount"]))
            db.commit()
            def stream(index):
                _need(active[0], "市场表已离开临时生命周期")
                cursor = db.execute("SELECT payload FROM rows WHERE view=? ORDER BY ordinal", (index,))
                try:
                    for (payload,) in cursor:
                        _need(active[0], "市场表已离开临时生命周期")
                        yield json.loads(payload)
                finally:
                    if active[0]: cursor.close()
            titles = ("市场价格带样本汇总", "市场价格带商品日样本", "市场单日进出榜")
            result = tuple(Table("market-"+spec["view"], titles[index], NOTE, columns(spec["view"]),
                stream(index), spec["rowCount"]) for index,spec in enumerate(specs))
            yield {"sourceMaterials": json.loads(canonical(manifest)), "authorityVerified": False,
                "projectedBytes": projected_bytes, "tableColumns": [len(t.columns) for t in result]}, result
        except sqlite3.DatabaseError as error:
            raise AnalysisContractError("市场表临时空间不可用或超限") from error
        except (KeyError, TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as error:
            raise AnalysisContractError("市场材料字段或编码无效") from error
        finally:
            active[0] = False; db.close()
