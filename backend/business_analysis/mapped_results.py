"""SKU/SPU ERP tables from trusted, completely verified mapping results.

The opener is internal application code backed by one sealed Reader, never a
model-supplied callback, path, SQL, summary or group stream. Current and baseline
mapping contexts are opened serially. Each gets at most 128 MiB; this module's
single disposable aggregation database gets the other 128 MiB.
"""
from collections import defaultdict
from contextlib import contextmanager
from .partitioned import Checkpoint
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory

from . import identity_partitioned as identity
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, compare, comparison_periods, coverage, digest

SCHEMA_VERSION = "business-mapped-result-table-v1"
ALGORITHM_VERSION = "business-mapped-results-v1"
MAX_SCRATCH_BYTES = 128 * 1024 * 1024
MAX_MAPPING_SCRATCH_BYTES = 128 * 1024 * 1024
MAX_COMBINED_SCRATCH_BYTES = 256 * 1024 * 1024
MAX_GROUPS = 250000
MAX_RESPONSE_BYTES = 38000
MAX_DESCRIPTOR_BYTES = 128 * 1024
METRICS = frozenset({"netSalesCents", "positiveSalesCents", "refundCents", "costCents", "grossProfitCents",
    "reportedGrossProfitCents", "feeCents", "netQuantity", "positiveQuantity", "returnQuantity", "netSalesExcludingAccessoriesCents"})
GROUP_FIELDS = frozenset({"status", "skuId", "spuId", "rowCount", "metrics"})
BINDING_FIELDS = frozenset({"schemaVersion", "evidenceRunId", "evidenceVersion", "evidencePlanDigest",
    "catalogDigest", "sealedDigest", "algorithmVersion", "sales", "master"})


def _require(condition, message="映射分析输入或完整性无效"):
    if not condition: raise AnalysisContractError(message)


def _copy(value, *, depth=0, count=None):
    count = [0, 0] if count is None else count
    count[0] += 1
    _require(depth <= 12 and count[0] <= 20000, "映射描述结构超过容量")
    if type(value) is dict:
        _require(len(value) <= 256 and all(type(k) is str and len(k) <= 200 for k in value))
        count[1] += 2 + max(0, len(value)-1) + sum(len(canonical(k).encode())+1 for k in value)
        _require(count[1] <= MAX_DESCRIPTOR_BYTES)
        result = {k: _copy(v, depth=depth+1, count=count) for k,v in value.items()}
    elif type(value) is list:
        _require(len(value) <= 1000)
        count[1] += 2+max(0, len(value)-1)
        result = [_copy(v, depth=depth+1, count=count) for v in value]
    else:
        _require(value is None or type(value) in (str, int, float, bool))
        if type(value) is str: _require(len(value) <= MAX_DESCRIPTOR_BYTES)
        if type(value) is int: _require(abs(value) <= MAX_SAFE_INTEGER)
        count[1] += len(canonical(value).encode())
        result = value
    _require(count[1] <= MAX_DESCRIPTOR_BYTES, "映射描述超过字节容量")
    return result


def _same(a, b): return canonical(a) == canonical(b)


@dataclass(frozen=True, init=False)
class MappingSource:
    _json: str
    open_mapping: object

    def __init__(self, binding, sales_source, master_source, sales_info, master_info, open_mapping):
        _require(callable(open_mapping), "须提供内部受信映射读取器")
        try:
            value = _copy({"binding": binding, "salesSource": sales_source, "masterSource": master_source,
                "salesInfo": sales_info, "masterInfo": master_info})
            _validate_source(value)
            object.__setattr__(self, "_json", canonical(value))
            object.__setattr__(self, "open_mapping", open_mapping)
        except AnalysisContractError:
            raise
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as error:
            raise AnalysisContractError("映射来源描述无效") from error

    @property
    def descriptor(self): return json.loads(self._json)


def _validate_source(value):
    binding = value["binding"]
    _require(set(binding) == BINDING_FIELDS and binding["schemaVersion"] == "business-product-mapping-binding-v1"
        and binding["algorithmVersion"] == identity.ALGORITHM_VERSION)
    _require(type(binding["evidenceVersion"]) is int and binding["evidenceVersion"] >= 1)
    _require(type(binding["evidenceRunId"]) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", binding["evidenceRunId"]))
    for key in ("evidencePlanDigest", "catalogDigest", "sealedDigest"):
        _require(type(binding[key]) is str and re.fullmatch(r"[a-f0-9]{64}", binding[key]))
    for kind in ("sales", "master"):
        source, info, ref = value[kind+"Source"], value[kind+"Info"], binding[kind]
        _require(type(source) is dict and set(source) == {"key", "domain", "query"})
        _require(type(info) is dict and set(info) == {"metadata", "expected", "pageCount"})
        _require(type(info["pageCount"]) is int and 1 <= info["pageCount"] <= 2000)
        _require(type(ref) is dict and set(ref) == {"sourceKey", "queryDigest", "sourceRef"})
        _require(ref["sourceKey"] == source["key"] and ref["queryDigest"] == digest(source["query"])
            and ref["sourceRef"] == info["expected"]["sourceRef"] and info["expected"]["reconciled"] is True)
        _require(type(info["metadata"].get("sourceRevision")) is str and info["metadata"]["sourceRevision"])
    sales, master = value["salesSource"], value["masterSource"]
    a, b = sales["query"], master["query"]
    _require(sales["domain"] == "sales" and master["domain"] == "netshop" and b.get("dataset") == "master"
        and b.get("window", "current") == "current" and sales["key"] != master["key"])
    _require(all(type(a.get(k)) is str and a[k] and a[k] == b.get(k) for k in ("platform", "shop")))
    _require(type(a.get("channel")) is str and a["channel"])
    periods = comparison_periods(a["startDate"], a["endDate"])
    _require(a.get("window", "current") in {"current", "previous", "yearAgo"})
    cov = value["salesInfo"]["metadata"]["coverage"]
    _require(_same(cov, coverage(periods[a.get("window", "current")], cov["presentDates"])), "销售日期覆盖与固定窗口不一致")
    expected = value["salesInfo"]["expected"]
    _require(set(expected["metrics"]) == METRICS or (expected["rowCount"] == 0 and not expected["metrics"]),
        "有事实的ERP映射必须完整保留十一项原始指标")
    master_cov = value["masterInfo"]["metadata"]["coverage"]
    _require(master_cov.get("historicalMapping") is False and master_cov.get("status") in {"current_master", "no_records"})
    _require(set(value["masterInfo"]["expected"]["metrics"]) == set())


def _compatible(current, baseline):
    a, b = current.descriptor, baseline.descriptor
    for key in ("evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest", "algorithmVersion", "master"):
        _require(_same(a["binding"][key], b["binding"][key]), "比较必须绑定同一封存证据及当前主数据")
    _require(_same(a["masterSource"], b["masterSource"]) and _same(a["masterInfo"], b["masterInfo"]), "比较主数据版本或覆盖不一致")
    left, right = a["salesSource"]["query"], b["salesSource"]["query"]
    _require(left.get("window", "current") == "current" and right.get("window") in {"previous", "yearAgo"})
    _require(_same({k:v for k,v in left.items() if k != "window"}, {k:v for k,v in right.items() if k != "window"}),
        "比较平台、店铺、渠道或原始日期基准不同")
    _require(a["salesSource"]["key"] != b["salesSource"]["key"])
    _require(_same(a["salesInfo"]["metadata"].get("metricSemantics"), b["salesInfo"]["metadata"].get("metricSemantics")), "比较指标口径不一致")


class _Table:
    def __init__(self, db, dimension, checkpoint=None):
        self._db, self._dimension, self._active = db, dimension, False
        self._checkpoint = checkpoint
        self._cursors, self._infos, self._proofs = set(), [], []
        self._total, self._high_water, self._mapping_high_water = 0, 0, 0
        self._header = None

    def _bytes(self):
        return self._db.execute("PRAGMA page_count").fetchone()[0]*self._db.execute("PRAGMA page_size").fetchone()[0]

    def _available(self): _require(self._active, "映射分析尚未完成或已离开临时生命周期")

    def _ingest(self, source, side):
        descriptor = source.descriptor
        with source.open_mapping(max_scratch_bytes=MAX_MAPPING_SCRATCH_BYTES) as opened:
            _require(type(opened) is tuple and len(opened) == 2, "读取器须返回已核验映射与实际绑定")
            result, binding = opened
            _require(type(result) is identity._Result and result._active, "拒绝自报映射摘要或任意组流")
            _require(_same(binding, descriptor["binding"]), "实际映射绑定与冻结描述不同")
            actual_cap = result.stats()["scratchLimitBytes"]
            _require(type(actual_cap) is int and 0 < actual_cap <= MAX_MAPPING_SCRATCH_BYTES, "映射读取器未执行组合磁盘额度")
            summary = result.summary()
            _require(summary["schemaVersion"] == identity.SCHEMA_VERSION and summary["algorithmVersion"] == identity.ALGORITHM_VERSION
                and summary["reconciled"] is True and summary["resultDigest"] == digest({k:v for k,v in summary.items() if k != "resultDigest"}))
            for kind in ("sales", "master"):
                _require(_same(summary["sources"][kind], descriptor[kind+"Info"]["expected"]), "映射与可信来源核对记录不同")
            query = descriptor["salesSource"]["query"]
            _require((summary["platform"], summary["shopName"]) == (query["platform"], query["shop"]))
            sums, statuses, row_count, group_count, chain, previous_key = defaultdict(int), defaultdict(int), 0, 0, digest([]), None
            for raw in result.scan():
                if self._checkpoint is not None and group_count % 100 == 0:
                    self._checkpoint({"stage":"mapped_aggregate","side":side,"rowOffset":group_count})
                _require(type(raw) is dict and set(raw) == GROUP_FIELDS | {"rowIndex", "id"})
                group = {k: raw[k] for k in GROUP_FIELDS}
                key = canonical([group["status"], group["skuId"], group["spuId"]])
                _require(type(raw["rowIndex"]) is int and raw["rowIndex"] == group_count
                    and raw["id"] == digest([summary["resultDigest"], key]) and (previous_key is None or key > previous_key), "映射组重复、乱序或行身份变化")
                previous_key = key
                _require(group["status"] in {"matched", "ambiguous", "unmatched"}
                    and type(group["rowCount"]) is int and group["rowCount"] > 0 and set(group["metrics"]) == METRICS)
                _require((group["status"] == "matched" and type(group["skuId"]) is str and group["skuId"])
                    or (group["status"] != "matched" and group["skuId"] is None and group["spuId"] is None))
                _require(group["spuId"] is None or type(group["spuId"]) is str)
                for metric, amount in group["metrics"].items():
                    _require(type(amount) is int and abs(amount) <= MAX_SAFE_INTEGER)
                    sums[metric] += amount
                row_count += group["rowCount"]; statuses[group["status"]] += group["rowCount"]; group_count += 1
                chain = digest([chain, group, group_count])
                entity = {"platform": summary["platform"], "shopName": summary["shopName"],
                    "mappingStatus": group["status"], self._dimension+"Id": group[self._dimension+"Id"]}
                encoded = canonical(entity)
                saved = self._db.execute("SELECT payload FROM groups WHERE entity=? AND side=?", (encoded, side)).fetchone()
                aggregate = json.loads(saved[0]) if saved else {"rowCount": 0, "metrics": {metric:0 for metric in sorted(METRICS)}}
                aggregate["rowCount"] += group["rowCount"]
                for metric, amount in group["metrics"].items(): aggregate["metrics"][metric] += amount
                # Python keeps exact integers while partial groups can cancel;
                # final aggregates are range-checked after the complete stream.
                self._db.execute("INSERT INTO groups VALUES (?,?,?) ON CONFLICT(entity,side) DO UPDATE SET payload=excluded.payload", (encoded, side, canonical(aggregate)))
                if group_count % 100 == 0: self._db.commit()
            self._db.commit()
            _require(group_count == summary["groupCount"] and row_count == summary["rowCount"] and chain == summary["groupsDigest"], "映射组链或总行数未通过完整核验")
            expected = descriptor["salesInfo"]["expected"]
            _require(set(summary["totals"]) == set(expected["metrics"]))
            _require(_same({k:sums[k] for k in summary["totals"]}, summary["totals"])
                and _same({k:statuses[k] for k in ("matched", "ambiguous", "unmatched")}, summary["coverage"]), "映射金额或匹配覆盖未通过核验")
            _require(row_count == expected["rowCount"] and all(not expected["metrics"][k]["missingRows"]
                and sums[k] == (expected["metrics"][k]["value"] or 0) for k in expected["metrics"]))
            mapping_bytes = result.stats()["scratchBytes"]
            self._mapping_high_water = max(self._mapping_high_water, mapping_bytes)
            self._high_water = max(self._high_water, mapping_bytes+self._bytes())
            _require(self._high_water <= MAX_COMBINED_SCRATCH_BYTES, "组合临时空间超过容量")
            self._proofs.append(summary)
        # Exit completes the owning reader's late permission/binding fence
        # before the next source context can be opened.
        self._infos.append(descriptor)

    def _raw_rows(self):
        cursor = self._db.execute("SELECT entity,side,payload FROM groups ORDER BY entity COLLATE BINARY,side")
        self._cursors.add(cursor)
        try:
            previous, pair, count = None, [None,None], 0
            while True:
                if self._checkpoint is not None and count % 100 == 0:
                    self._checkpoint({"stage":"mapped_scan","rowOffset":count})
                record = cursor.fetchone()
                if record is None: break
                entity, side, payload = record
                if previous is not None and entity != previous:
                    yield previous, pair
                    pair = [None,None]
                pair[side] = json.loads(payload); previous = entity
                count += 1
            if previous is not None: yield previous, pair
        finally:
            if cursor in self._cursors:
                self._cursors.remove(cursor); cursor.close()

    def _row(self, encoded, pair, index):
        entity, (current, baseline) = json.loads(encoded), pair
        identity_complete = entity["mappingStatus"] == "matched" and entity[self._dimension+"Id"] not in (None, "")
        comparable = self._head["dateCoverageComparable"] and identity_complete
        def values(value):
            if value is None: return None
            _require(type(value["rowCount"]) is int and value["rowCount"] > 0)
            _require(all(type(n) is int and abs(n) <= MAX_SAFE_INTEGER for n in value["metrics"].values()), "映射维度金额超过精确整数范围")
            return {key:{"value": value["metrics"][key], "presentRows":value["rowCount"], "missingRows":0} for key in sorted(METRICS)}
        metrics, before = values(current), values(baseline)
        return {"id": digest([self._head["bindingDigest"], entity]), "rowIndex":index, "entity":entity,
            "currentRowCount":current["rowCount"] if current else None, "baselineRowCount":baseline["rowCount"] if baseline else None,
            "metrics": metrics or {key:None for key in sorted(METRICS)}, "baselineMetrics":before,
            "ratios":{}, "comparisons":{key:compare(metrics[key]["value"] if metrics else None,
                before[key]["value"] if before else None, comparable=comparable and bool(current and baseline))
                for key in sorted(METRICS)} if len(self._infos)==2 else {},
            "dimensionMissing":not identity_complete}

    def _finish(self):
        current = self._infos[0]; baseline = self._infos[1] if len(self._infos)==2 else None
        self._total = self._db.execute("SELECT count(*) FROM (SELECT entity FROM groups GROUP BY entity)").fetchone()[0]
        _require(self._total <= MAX_GROUPS, "映射结果分组并集超过容量")
        self._head = {"schemaVersion":SCHEMA_VERSION, "algorithmVersion":ALGORITHM_VERSION, "dimension":self._dimension,
            "mappingAlgorithmVersion":identity.ALGORITHM_VERSION, "total":self._total,
            "binding":current["binding"], "baselineBinding":baseline["binding"] if baseline else None,
            "source":current["salesInfo"]["expected"], "baselineSource":baseline["salesInfo"]["expected"] if baseline else None,
            "sourceMetadata":current["salesInfo"]["metadata"], "baselineMetadata":baseline["salesInfo"]["metadata"] if baseline else None,
            "masterMetadata":current["masterInfo"]["metadata"],
            "mappingProofs":[{k:p[k] for k in ("resultDigest", "groupsDigest", "rowCount", "groupCount", "coverage", "totals")} for p in self._proofs],
            "sourceWindow":current["salesSource"]["query"].get("window","current"),
            "comparisonWindow":baseline["salesSource"]["query"]["window"] if baseline else None,
            "dateCoverageComparable":bool(baseline and all(i["salesInfo"]["metadata"]["coverage"]["status"]=="dates_present" for i in self._infos)),
            "historicalMapping":False, "limitations":[*identity.LIMITATIONS, "历史期间按同一当前主数据回溯归属，不代表历史真实商品归属", "缺身份、歧义、未匹配、缺侧或缺日不计算比较，基期为零或负数不计算增长率", "毛利为净销售减源成本，不扣费用，不是广告归因利润"]}
        self._head["bindingDigest"] = digest([ALGORITHM_VERSION, self._dimension, self._infos, [p["resultDigest"] for p in self._proofs]])
        self._header = canonical(self._head)
        totals = [defaultdict(int), defaultdict(int)]; counts = [0,0]; scanned = 0
        _require(len(canonical(self._page([],0,100)).encode()) <= MAX_RESPONSE_BYTES, "映射分析元信息超过单页容量")
        for index, (encoded,pair) in enumerate(self._raw_rows()):
            row = self._row(encoded,pair,index); scanned += 1
            _require(len(canonical(self._page([row],index,100)).encode()) <= MAX_RESPONSE_BYTES, "单个完整映射分析行超过容量")
            for side, item in enumerate(pair):
                if item:
                    counts[side] += item["rowCount"]
                    for k,v in item["metrics"].items(): totals[side][k] += v
        _require(scanned == self._total)
        for side, proof in enumerate(self._proofs):
            _require(counts[side] == proof["rowCount"] and _same({k:totals[side][k] for k in proof["totals"]}, proof["totals"]), "映射再汇总不守恒")
        self._high_water = max(self._high_water,self._bytes())
        self._active = True

    def header(self): self._available(); return json.loads(self._header)

    def stats(self):
        self._available()
        return {"derivedScratchBytes":self._bytes(), "mappingScratchPeakBytes":self._mapping_high_water,
            "combinedScratchHighWaterBytes":self._high_water, "sourceContexts":len(self._infos),
            "maxConcurrentMappingContexts":1, "total":self._total}

    def _page(self, rows, offset, limit):
        end=offset+len(rows)
        value={**json.loads(self._header), "rows":rows, "pagination":{"offset":offset,"limit":limit,"total":self._total,
            "hasMore":end<self._total,"nextOffset":end if end<self._total else None}}
        value["pageDigest"]=digest(value)
        return value

    def page(self, offset=0, limit=20):
        self._available()
        _require(type(offset) is int and 0<=offset<=self._total and type(limit) is int and 1<=limit<=100, "映射分析分页参数无效")
        rows=[]; value=self._page(rows,offset,limit)
        keys=self._db.execute("SELECT entity FROM groups GROUP BY entity ORDER BY entity COLLATE BINARY LIMIT ? OFFSET ?",(limit,offset)).fetchall()
        for (key,) in keys:
            pair=[None,None]
            for side,payload in self._db.execute("SELECT side,payload FROM groups WHERE entity=? ORDER BY side",(key,)): pair[side]=json.loads(payload)
            rows.append(self._row(key,pair,offset+len(rows)))
            candidate=self._page(rows,offset,limit)
            if len(canonical(candidate).encode())>MAX_RESPONSE_BYTES:
                rows.pop(); _require(bool(rows),"单行超过映射分页容量"); break
            value=candidate
        return value

    def scan(self):
        self._available()
        iterator=self._raw_rows(); index=0
        try:
            while True:
                self._available()
                try: encoded,pair=next(iterator)
                except StopIteration: break
                yield self._row(encoded,pair,index); index+=1
            _require(index==self._total)
        finally: iterator.close()


@contextmanager
def mapped_table(current, dimension, *, baseline=None, checkpoint=None):
    checkpoint = Checkpoint.wrap(checkpoint)
    _require(type(current) is MappingSource and (baseline is None or type(baseline) is MappingSource))
    _require(type(dimension) is str and dimension in {"sku","spu"}, "映射分析仅支持SKU或SPU")
    if baseline is not None: _compatible(current,baseline)
    table=None; db=None
    with TemporaryDirectory(prefix="teruisi-mapped-result-") as directory:
        try:
            db=sqlite3.connect(str(Path(directory)/"mapped.sqlite"))
            if checkpoint is not None: checkpoint.attach(db,"mapped_sqlite")
            db.execute("PRAGMA journal_mode=OFF"); db.execute("PRAGMA synchronous=OFF")
            db.execute("PRAGMA cache_size=-2048"); db.execute("PRAGMA temp_store=FILE")
            size=db.execute("PRAGMA page_size").fetchone()[0]
            _require(MAX_SCRATCH_BYTES>=size and MAX_SCRATCH_BYTES+MAX_MAPPING_SCRATCH_BYTES<=MAX_COMBINED_SCRATCH_BYTES)
            db.execute(f"PRAGMA max_page_count={MAX_SCRATCH_BYTES//size}")
            db.execute("CREATE TABLE groups(entity TEXT COLLATE BINARY,side INTEGER,payload TEXT NOT NULL,PRIMARY KEY(entity,side)) WITHOUT ROWID")
            table=_Table(db,dimension,checkpoint) if checkpoint is not None else _Table(db,dimension)
            table._ingest(current,0)
            if baseline is not None: table._ingest(baseline,1)
            table._finish()
            yield table
            if checkpoint is not None: checkpoint.raise_if_failed()
        except BaseException as error:
            if checkpoint is not None: checkpoint.raise_if_failed()
            if isinstance(error,AnalysisContractError): raise
            if isinstance(error,(sqlite3.DatabaseError, KeyError, TypeError, ValueError, UnicodeError, RecursionError)):
                raise AnalysisContractError("映射分析未通过完整计算或临时容量核验") from error
            raise
        finally:
            if db is not None and checkpoint is not None: db.set_progress_handler(None,0)
            if table is not None:
                table._active=False
                for cursor in table._cursors: cursor.close()
                table._cursors.clear()
            if db is not None: db.close()
