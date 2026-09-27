"""ERP-only signed-value rollups from one fully verified per-fact ledger.

Shop/day includes every ERP fact. Category/SPU/SKU/day include only complete
matched identities; ambiguous, incomplete and unmatched amounts remain in a
separate status/day table. No netshop, advertising, B2B or finance value enters.
"""
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory

from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, comparison_periods, digest, strict_date
from .cross_source_kpi_plan import ERP_METRICS
from . import erp_fact_assignment as assignment
from .report_files import Column, Table


SCHEMA = "business-erp-only-rollup-materials-v1"
KINDS = ("shop_day", "category_day", "spu_day", "sku_day", "unassigned_day")
MAX_SCRATCH_BYTES = 256 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_ROWS = 1_000_000
MAX_CHUNK_BYTES = 38_000


def _need(ok, message="ERP逐事实回卷未通过完整性校验"):
    if not ok:
        raise AnalysisContractError(message)


def _empty_metrics():
    return {metric: 0 for metric in sorted(ERP_METRICS)}


def _summary_totals(value):
    _need(type(value) is dict and set(value) <= ERP_METRICS)
    _need(all(type(amount) is int and abs(amount) <= MAX_SAFE_INTEGER
        for amount in value.values()))
    return {metric: value.get(metric, 0) for metric in sorted(ERP_METRICS)}


def _fact(row, index, seen, source, window):
    _need(type(row) is dict and type(row.get("rowIndex")) is int
        and row["rowIndex"] == index
        and row.get("mappingStatus") in
            {"matched", "ambiguous", "incomplete", "unmatched"}
        and type(row.get("sourceRowId")) is str
        and re.fullmatch(r"[1-9][0-9]{0,15}", row["sourceRowId"]) is not None
        and int(row["sourceRowId"]) <= MAX_SAFE_INTEGER
        and type(row.get("id")) is str and re.fullmatch(r"[0-9a-f]{64}", row["id"]) is not None
        and type(row.get("sourceRowHash")) is str
        and 1 <= len(row["sourceRowHash"]) <= 256)
    db = seen
    db.execute("INSERT INTO seen VALUES (?)", (row["sourceRowId"],))
    _need(type(row.get("metrics")) is dict and set(row["metrics"]) == ERP_METRICS
        and all(type(amount) is int and abs(amount) <= MAX_SAFE_INTEGER
            for amount in row["metrics"].values()),
        "ERP逐事实十一个指标缺失或超出无损范围")
    day = row.get("businessDate")
    strict_date(day)
    _need(window["startDate"] <= day <= window["endDate"]
        and (row.get("platform"), row.get("shopName")) ==
            (source["platform"], source["shopName"]),
        "ERP回卷事实跨店铺或实际比较窗口")
    for key in ("platform", "shopName"):
        _need(type(row.get(key)) is str and bool(row[key].strip()))
    if row["mappingStatus"] == "matched":
        _need(row.get("unassigned") is False and all(type(row.get(key)) is str
            and bool(row[key].strip()) for key in
            ("assignedSkuId", "assignedSpuId", "assignedPlatformCategory")))
    else:
        _need(row.get("unassigned") is True and all(row.get(key) is None
            for key in ("assignedSkuId", "assignedSpuId", "assignedPlatformCategory")))
    metrics = row["metrics"]
    net, positive, refund, cost = (metrics[key] for key in
        ("netSalesCents", "positiveSalesCents", "refundCents", "costCents"))
    _need(positive == max(net, 0) and refund == max(-net, 0)
        and metrics["grossProfitCents"] == net-cost,
        "ERP回卷退款、成本或毛利符号与源事实不同")


def _key(row, kind, period):
    key = {"period": period, "platform": row["platform"],
        "shopName": row["shopName"], "date": row["businessDate"],
        "category": None, "spuId": None, "skuId": None, "status": None}
    if kind in {"category_day", "spu_day", "sku_day"}:
        key["category"] = row["assignedPlatformCategory"]
    if kind in {"spu_day", "sku_day"}:
        key["spuId"] = row["assignedSpuId"]
    if kind == "sku_day":
        key["skuId"] = row["assignedSkuId"]
    if kind == "unassigned_day":
        key["status"] = row["mappingStatus"]
    return key


def _add(db, kind, key, row):
    fixed = canonical(key)
    previous = db.execute("SELECT payload FROM groups WHERE kind=? AND key=?",
        (kind, fixed)).fetchone()
    value = json.loads(previous[0]) if previous else {**key,
        "sourceFactCount": 0, "metrics": _empty_metrics(),
        "firstSourceRowId": row["sourceRowId"],
        "lastSourceRowId": row["sourceRowId"],
        "sourceRowDigest": digest([])}
    value["sourceFactCount"] += 1
    value["lastSourceRowId"] = row["sourceRowId"]
    value["sourceRowDigest"] = digest([value["sourceRowDigest"],
        row["sourceRowId"], row["sourceRowHash"], row["id"]])
    for metric, amount in row["metrics"].items():
        value["metrics"][metric] += amount
        _need(abs(value["metrics"][metric]) <= MAX_SAFE_INTEGER,
            "ERP分组指标加总超出无损范围")
    db.execute("INSERT INTO groups VALUES (?,?,?) ON CONFLICT(kind,key) DO UPDATE SET payload=excluded.payload",
        (kind, fixed, canonical(value)))


def _columns():
    return (Column("period", "比较期间"), Column("platform", "平台"),
        Column("shopName", "精确店铺"), Column("date", "ERP发货业务日"),
        Column("category", "当前主数据平台类目"), Column("spuId", "当前主数据SPU"),
        Column("skuId", "当前主数据SKU"), Column("status", "未分配原因"),
        Column("sourceFactCount", "ERP事实行数", "integer"),
        *(Column(metric, metric, "integer") for metric in sorted(ERP_METRICS)),
        Column("firstSourceRowId", "首ERP源行ID"),
        Column("lastSourceRowId", "末ERP源行ID"),
        Column("sourceRowDigest", "ERP源行链摘要"),
        Column("raw", "完整规范行JSON"))


def _file_proof(path):
    size, sha = 0, hashlib.sha256()
    with path.open("rb") as stream:
        while part := stream.read(512*1024):
            size += len(part)
            _need(size <= MAX_OUTPUT_BYTES)
            sha.update(part)
    return size, sha.hexdigest()


@dataclass(frozen=True, slots=True)
class PreparedRollups:
    _manifest: str
    _paths: dict
    _active: list
    _readers: list

    @property
    def manifest(self):
        _need(self._active[0], "ERP回卷已离开临时生命周期")
        return json.loads(self._manifest)

    def ndjson_pages(self, kind):
        _need(self._active[0] and kind in KINDS)
        def pages():
            pending = bytearray()
            stream = self._paths[kind].open("rb")
            self._readers.append(stream)
            try:
                while True:
                    _need(self._active[0])
                    line = stream.readline()
                    if not line:
                        break
                    _need(len(line) <= MAX_CHUNK_BYTES)
                    if pending and len(pending)+len(line) > MAX_CHUNK_BYTES:
                        yield bytes(pending)
                        pending = bytearray()
                    pending.extend(line)
                yield bytes(pending)
                _need(self._active[0])
            finally:
                if stream in self._readers:
                    self._readers.remove(stream)
                    stream.close()
        return pages()

    def tables(self):
        _need(self._active[0])
        specs = {item["kind"]: item for item in self.manifest["tables"]}
        columns = _columns()
        def rows(kind):
            stream = self._paths[kind].open("rb")
            self._readers.append(stream)
            try:
                while True:
                    _need(self._active[0])
                    line = stream.readline()
                    if not line:
                        break
                    item = json.loads(line)
                    yield [item[key] if key in item else item["metrics"].get(key)
                        if key in ERP_METRICS else canonical(item) for key in
                        (column.key for column in columns)]
            finally:
                if stream in self._readers:
                    self._readers.remove(stream)
                    stream.close()
        titles = {"shop_day": "ERP店铺逐日", "category_day": "ERP已归属平台品类逐日",
            "spu_day": "ERP已归属SPU逐日", "sku_day": "ERP已归属SKU逐日",
            "unassigned_day": "ERP未分配原因逐日"}
        return tuple(Table("erp-"+kind, titles[kind],
            "仅ERP退款后销售与源成本；当前主数据归属非历史所有权，未合并网店/广告/财务。",
            columns, rows(kind), specs[kind]["rowCount"]) for kind in KINDS)


@contextmanager
def prepare(ledger, *, max_scratch_bytes=None):
    """Consume one ERP ledger scan and publish no table until five proofs pass."""
    _need(type(ledger) is assignment._Ledger,
        "ERP回卷只能消费已完成的进程内逐事实账本")
    source = ledger.summary()
    _need(source.get("schemaVersion") == assignment.SCHEMA
        and source.get("resultDigest") == digest({key: value for key, value in source.items()
            if key != "resultDigest"})
        and source.get("authorityVerified") is False)
    totals = _summary_totals(source["totals"])
    unassigned = _summary_totals(source["unassignedTotals"])
    matched = {metric: totals[metric]-unassigned[metric] for metric in totals}
    _need(all(abs(value) <= MAX_SAFE_INTEGER for value in matched.values())
        and sum(source["coverage"].values()) == source["rowCount"])
    period = comparison_periods(ledger.sales_source["query"]["startDate"],
        ledger.sales_source["query"]["endDate"])[source["period"]]
    limit = MAX_SCRATCH_BYTES if max_scratch_bytes is None else max_scratch_bytes
    _need(type(limit) is int and 0 < limit <= MAX_SCRATCH_BYTES)
    with TemporaryDirectory(prefix="teruisi-erp-rollup-") as folder:
        directory = Path(folder)
        db = None
        active = [True]
        paths = {kind: directory/(kind+".ndjson") for kind in KINDS}
        try:
            db = sqlite3.connect(str(directory/"groups.sqlite"))
            db.execute("PRAGMA cache_size=-2048")
            db.execute("PRAGMA temp_store=FILE")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            _need(limit >= page_size)
            db.execute(f"PRAGMA max_page_count={limit // page_size}")
            db.execute("CREATE TABLE seen(source_row_id TEXT PRIMARY KEY) WITHOUT ROWID")
            db.execute("CREATE TABLE groups(kind TEXT,key TEXT,payload TEXT,PRIMARY KEY(kind,key)) WITHOUT ROWID")
            count = 0
            for row in ledger.scan():
                _fact(row, count, db, source, period)
                _add(db, "shop_day", _key(row, "shop_day", source["period"]), row)
                if row["mappingStatus"] == "matched":
                    for kind in ("category_day", "spu_day", "sku_day"):
                        _add(db, kind, _key(row, kind, source["period"]), row)
                else:
                    _add(db, "unassigned_day", _key(row, "unassigned_day", source["period"]), row)
                count += 1
                _need(count <= assignment.MAX_FACT_ROWS)
                if count % 100 == 0:
                    db.commit()
            db.commit()
            _need(count == source["rowCount"],
                "ERP回卷源行数量与逐事实账本不同")
            rollup_totals, rollup_counts, status_counts = {}, {}, defaultdict(int)
            specs, output_bytes, output_rows = [], 0, 0
            for kind in KINDS:
                sha, rows_count, pages_count, bytes_count, pending = hashlib.sha256(), 0, 0, 0, bytearray()
                sums, facts_count = _empty_metrics(), 0
                def emit(stream):
                    nonlocal pending, pages_count
                    raw = bytes(pending)
                    stream.write(raw)
                    sha.update(raw)
                    pages_count += 1
                    pending = bytearray()
                with paths[kind].open("wb") as output:
                    for (key, payload) in db.execute("SELECT key,payload FROM groups WHERE kind=? ORDER BY key COLLATE BINARY", (kind,)):
                        item = json.loads(payload)
                        item["rowIndex"] = rows_count
                        item["id"] = digest([source["resultDigest"], kind, key, item])
                        line = (canonical(item)+"\n").encode("utf-8")
                        _need(len(line) <= MAX_CHUNK_BYTES,
                            "ERP单条完整回卷行超过容量")
                        if pending and len(pending)+len(line) > MAX_CHUNK_BYTES:
                            emit(output)
                        pending.extend(line)
                        rows_count += 1
                        output_rows += 1
                        bytes_count += len(line)
                        output_bytes += len(line)
                        facts_count += item["sourceFactCount"]
                        _need(output_rows <= MAX_OUTPUT_ROWS
                            and output_bytes <= MAX_OUTPUT_BYTES,
                            "ERP回卷完整材料超出容量")
                        for metric, amount in item["metrics"].items():
                            sums[metric] += amount
                            _need(abs(sums[metric]) <= MAX_SAFE_INTEGER)
                        if kind == "unassigned_day":
                            status_counts[item["status"]] += item["sourceFactCount"]
                    emit(output)  # Empty table has one hashable empty page.
                rollup_totals[kind] = sums
                rollup_counts[kind] = facts_count
                specs.append({"kind": kind, "rowCount": rows_count,
                    "sourceFactCount": facts_count, "pageCount": pages_count,
                    "ndjsonBytes": bytes_count, "ndjsonSha256": sha.hexdigest()})
            expected_counts = {"shop_day": count,
                "category_day": source["coverage"]["matched"],
                "spu_day": source["coverage"]["matched"],
                "sku_day": source["coverage"]["matched"],
                "unassigned_day": count-source["coverage"]["matched"]}
            _need(rollup_counts == expected_counts
                and rollup_totals["shop_day"] == totals
                and all(rollup_totals[kind] == matched for kind in
                    ("category_day", "spu_day", "sku_day"))
                and rollup_totals["unassigned_day"] == unassigned
                and all(status_counts[key] == source["coverage"][key]
                    for key in ("ambiguous", "incomplete", "unmatched")),
                "ERP逐日与商品各层或未分配池未守恒")
            manifest = {"schemaVersion": SCHEMA,
                "sourceSummaryDigest": source["resultDigest"],
                "period": source["period"], "platform": source["platform"],
                "shopName": source["shopName"], "sourceRowCount": count,
                "sourceTotals": totals, "matchedTotals": matched,
                "unassignedTotals": unassigned,
                "conservation": {"shopEqualsSource": True,
                    "eachMatchedLevelEqualsMatchedFacts": True,
                    "unassignedEqualsAmbiguousIncompleteUnmatched": True,
                    "sourceRowIdsUnique": True},
                "tables": specs, "outputRows": output_rows,
                "ndjsonBytes": output_bytes,
                "owningDomain": "erp_sales_only",
                "shopUniqueVisitorsAvailable": False,
                "historicalOwnershipVerified": False,
                "netshopAdFinanceCombined": False,
                "authorityVerified": False, "registeredRenderer": False,
                "limitations": ["退款与成本保留ERP源有符号值；不是SKU净利润。",
                    "当前主数据归属不代表历史商品所有权。",
                    "未分配ERP事实不转给品类、SPU或SKU；无网店/广告/B端/财务叠加。"]}
            manifest["manifestDigest"] = digest(manifest)
            _need(len(canonical(manifest).encode("utf-8")) <= 128*1024)
            prepared = PreparedRollups(canonical(manifest), paths, active, [])
            yield prepared
            for spec in specs:
                size, sha = _file_proof(paths[spec["kind"]])
                _need(size == spec["ndjsonBytes"]
                    and sha == spec["ndjsonSha256"],
                    "ERP临时表在交接期间发生变化")
        except sqlite3.DatabaseError as error:
            raise AnalysisContractError("ERP回卷临时空间不可用或超限") from error
        finally:
            active[0] = False
            if "prepared" in locals():
                for stream in prepared._readers:
                    stream.close()
                prepared._readers.clear()
            if db is not None:
                db.close()
