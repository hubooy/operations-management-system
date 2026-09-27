"""Pure, fully verified promotion materials -> streaming typed report tables.

Private temporary SQLite holds projected rows. No tables are yielded until all
input hashes/counts and capacity checks pass. This grants no report authority.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import sqlite3
from tempfile import TemporaryDirectory
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest
from .promotion_keyword_sku import ALGORITHM_VERSION, VIEWS
from .promotion_views import _copy
from .report_files import Column, Table, text

MAX_BYTES, MAX_ROWS, MAX_CHUNK_BYTES = 64 * 1024 * 1024, 250_000, 38_000
METRICS = (("spendCents", "推广费用（分）"), ("impressions", "曝光次数"), ("clicks", "点击次数"),
    ("reportedOrderLines", "平台归因订单口径"), ("reportedGmvCents", "平台归因成交（分）"), ("cartQuantity", "加购数量"),
    ("directGmvCents", "直接归因成交（分）"), ("indirectGmvCents", "间接归因成交（分）"), ("newCustomerGmvCents", "新客归因成交（分）"))
IDENTITY = (("platform", "平台"), ("shopName", "店铺"), ("keyword", "关键词"), ("promotedSkuId", "明确推广SKU"),
    ("planId", "计划ID"), ("unitId", "单元ID"), ("matchType", "匹配方式"))
NOTE = "两表为同一推广事实的不同分组，费用不可相加；平台归因成交不是ERP销售或利润。空值不补零，缺SKU桶不可作为具体商品操作对象。汇总表的计划/单元/匹配方式为空表示已跨上下文汇总。"


def _need(condition, message="推广报告材料不完整或被篡改"):
    if not condition: raise AnalysisContractError(message)


def _integer(value, minimum=0):
    _need(type(value) is int and minimum <= value <= MAX_SAFE_INTEGER)
    return value


def _scalars(value, depth=0):
    _need(depth <= 16)
    if type(value) is dict:
        _need(len(value) <= 128)
        for key, child in value.items(): text(key); _scalars(child, depth+1)
    elif type(value) is list:
        _need(len(value) <= 128)
        for child in value: _scalars(child, depth+1)
    elif type(value) is str: text(value)
    elif type(value) is int: _integer(value, -MAX_SAFE_INTEGER)
    elif type(value) is float: _need(math.isfinite(value))
    else: _need(value is None or type(value) is bool)


def columns():
    result = [Column(key, label) for key,label in IDENTITY]
    result += [Column("qualified", "身份可定位"), Column("missing", "缺失身份字段"),
               Column("rowIndex", "分析行位置", "integer"), Column("rowId", "完整分析行ID")]
    for side, title in (("metrics", "本期"), ("baselineMetrics", "基期")):
        for metric, label in METRICS:
            for field, suffix in (("value", ""), ("presentRows", "·有值源行数"), ("missingRows", "·缺值源行数")):
                result.append(Column(side+"."+metric+"."+field, title+"·"+label+suffix, "integer", total=False))
    result += [Column("raw", "完整原始分析行JSON（含比率与比较状态）")]
    return tuple(result)


def _project(row, view, binding_digest, index):
    _scalars(row)
    _need(type(row) is dict and row.get("rowIndex") == index and type(row["rowIndex"]) is int)
    entity = row["entity"]
    _need(type(entity) is dict and set(entity) == {"platform", "shopName", *VIEWS[view]})
    for value in entity.values(): _need(value is None or type(value) is str)
    _need(row["id"] == digest([binding_digest, entity]))
    missing = [key for key in VIEWS[view] if entity[key] is None]
    _need(type(row["identityQualified"]) is bool and row["identityQualified"] == (not missing)
          and row["missingIdentityFields"] == missing)
    result = [entity.get(key) for key,_ in IDENTITY]
    result += ["是" if row["identityQualified"] else "否", "、".join(missing), index, row["id"]]
    for side, count_key in (("metrics", "currentRowCount"), ("baselineMetrics", "baselineRowCount")):
        metrics = row[side]; count = row[count_key]
        _need((metrics is None) == (count is None))
        if metrics is not None:
            _integer(count, 1); _need(type(metrics) is dict and set(metrics) == {key for key,_ in METRICS})
        for metric,_ in METRICS:
            cell = metrics[metric] if metrics is not None else None
            if cell is None: result += [None, None, None]; continue
            _need(type(cell) is dict and set(cell) == {"value", "presentRows", "missingRows"})
            present, absent = _integer(cell["presentRows"]), _integer(cell["missingRows"])
            _need(present+absent == count and (cell["value"] is None) == (present == 0))
            if cell["value"] is not None: _integer(cell["value"], -MAX_SAFE_INTEGER)
            result += [cell["value"], present, absent]
    result.append(text(canonical(row)))  # Fail, never split/truncate an overwide cell.
    return result


@contextmanager
def tables(manifest, pages_by_view):
    """Yield (summary, two Tables). Consume rows only inside this context.

Inputs are ordinary candidate DTOs, not authorization; production adapters must
provide completed owning materials and reauthorize before file publication.
"""
    manifest = _copy(manifest, 128*1024)
    _need(type(manifest) is dict)
    _integer(manifest.get("rowCount")); _integer(manifest.get("ndjsonBytes"))
    _need(manifest.get("schemaVersion") == "business-promotion-export-materials-v1"
          and manifest.get("algorithmVersion") == ALGORITHM_VERSION
          and manifest.get("manifestDigest") == digest({k:v for k,v in manifest.items() if k != "manifestDigest"})
          and manifest.get("tableExpensesAreAdditive") is False and manifest.get("registeredRenderer") is False)
    specs = manifest.get("tables")
    _need(type(specs) is list and all(type(s) is dict for s in specs) and [s.get("view") for s in specs] == list(VIEWS))
    _need(type(pages_by_view) is dict and set(pages_by_view) == set(VIEWS))
    input_bytes = len(canonical(manifest).encode("utf-8")); output_bytes = 0; total_rows = 0; total_raw = 0
    with TemporaryDirectory(prefix="teruisi-promotion-tables-") as folder:
        db = sqlite3.connect(folder+"/tables.sqlite")
        active = [True]
        try:
            db.execute("PRAGMA cache_size=-2048"); db.execute("PRAGMA max_page_count=32768")
            db.execute("CREATE TABLE rows(view INTEGER, ordinal INTEGER, payload TEXT, PRIMARY KEY(view,ordinal)) WITHOUT ROWID")
            for table_index, spec in enumerate(specs):
                view, binding, header = spec["view"], spec["binding"], spec["header"]
                for key in ("rowCount", "pageCount", "ndjsonBytes", "missingPromotedSkuGroups", "unqualifiedIdentityGroups"):
                    _integer(spec[key])
                _integer(header["total"])
                _need(binding["reportBinding"] == manifest["reportBinding"] and binding["sourceKey"] == manifest["sourceKey"]
                    and binding["baselineKey"] == manifest["baselineKey"] and binding["view"] == view
                    and binding["algorithmVersion"] == ALGORITHM_VERSION and header["algorithmVersion"] == ALGORITHM_VERSION
                    and binding["tableBindingDigest"] == header["tableBindingDigest"] and header["view"] == view)
                _need(header["source"]["key"] == manifest["sourceKey"]
                    and (header["baselineSource"]["key"] if header["baselineSource"] is not None else None) == manifest["baselineKey"])
                sha = hashlib.sha256(); count = size = pages = missing_sku = unqualified = 0
                sums = {side: {"value": None, "presentGroups": 0, "missingFactRows": 0} for side in ("current", "baseline")}
                for raw in pages_by_view[view]:
                    _need(type(raw) is bytes and len(raw) <= MAX_CHUNK_BYTES)
                    pages += 1; size += len(raw); input_bytes += len(raw)
                    _need(pages <= 20000 and input_bytes <= MAX_BYTES)
                    sha.update(raw)
                    _need(not raw or raw.endswith(b"\n"))
                    for line in raw.splitlines():
                        try: row = json.loads(line.decode("utf-8"))
                        except (ValueError, UnicodeError, RecursionError) as error: raise AnalysisContractError("推广NDJSON无效") from error
                        _need(canonical(row).encode("utf-8") == line, "推广行不是唯一规范JSON")
                        projected = _project(row, view, header["tableBindingDigest"], count)
                        missing_sku += row["entity"]["promotedSkuId"] is None
                        unqualified += row["identityQualified"] is not True
                        for side, key in (("current", "metrics"), ("baseline", "baselineMetrics")):
                            cell = (row[key] or {}).get("spendCents")
                            if cell is not None:
                                sums[side]["missingFactRows"] += cell["missingRows"]
                                if cell["value"] is not None:
                                    sums[side]["value"] = _integer((sums[side]["value"] or 0)+cell["value"], -MAX_SAFE_INTEGER)
                                    sums[side]["presentGroups"] += 1
                        payload = canonical(projected); output_bytes += len(payload.encode("utf-8"))
                        count += 1; total_rows += 1
                        _need(total_rows <= MAX_ROWS and output_bytes <= MAX_BYTES)
                        db.execute("INSERT INTO rows VALUES (?,?,?)", (table_index, count-1, payload))
                _need(type(spec["rowCount"]) is int and count == spec["rowCount"] == header["total"]
                    and size == spec["ndjsonBytes"] and pages == spec["pageCount"] and sha.hexdigest() == spec["ndjsonSha256"])
                _need(sums == spec["spendTotals"] and missing_sku == spec["missingPromotedSkuGroups"]
                      and unqualified == spec["unqualifiedIdentityGroups"])
                total_raw += size
            _need(total_rows == manifest["rowCount"] and total_raw == manifest["ndjsonBytes"])
            for side in ("current", "baseline"):
                a,b = (spec["spendTotals"][side] for spec in specs)
                _need((a["value"],a["missingFactRows"]) == (b["value"],b["missingFactRows"]))
            db.commit()
            def stream(index):
                _need(active[0], "推广表已离开临时生命周期")
                cursor = db.execute("SELECT payload FROM rows WHERE view=? ORDER BY ordinal", (index,))
                try:
                    while True:
                        _need(active[0], "推广表已离开临时生命周期")
                        row = cursor.fetchone()
                        if row is None: break
                        yield json.loads(row[0])
                finally:
                    if active[0]: cursor.close()
            titles = ("关键词与推广SKU", "关键词商品及投放上下文")
            result = tuple(Table("promotion-"+spec["view"], titles[index], NOTE, columns(), stream(index), spec["rowCount"])
                           for index,spec in enumerate(specs))
            yield {"sourceMaterials": manifest, "authorityVerified": False, "expensesAreAdditive": False,
                   "projectedBytes": output_bytes, "tableColumns": len(columns())}, result
        except sqlite3.DatabaseError as error:
            raise AnalysisContractError("推广表临时空间不可用或超限") from error
        except (KeyError, TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as error:
            raise AnalysisContractError("推广材料字段或编码无效") from error
        finally:
            active[0] = False; db.close()
