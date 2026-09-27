"""Disposable per-ERP-fact assignment against one explicit current master.

Only a unique complete current SKU identity may receive an ERP fact. Every
ambiguous or unmatched fact keeps its original signed amounts in an unassigned
row. This is a pure, temporary candidate ledger, not historical ownership.
"""
from collections import defaultdict
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, comparison_periods, coverage, digest, strict_date
from .cross_source_kpi_plan import ERP_METRICS
from .evidence_v2 import normalize_sources
from . import identity_partitioned as previous, mapping_plan
from .partitioned import Checkpoint


SCHEMA = "business-erp-fact-assignment-candidate-v1"
ALGORITHM = "exact-online-spec-to-current-master-per-fact-v1"
MAX_SCRATCH_BYTES = previous.MAX_SCRATCH_BYTES
MAX_FACT_ROWS = previous.MAX_SOURCE_PAGES * 100
MAX_ROW_BYTES = 38_000
MAX_CANDIDATE_ROWS = 256
MAX_CANDIDATE_BYTES = 30_000


def _need(ok, message="ERP逐事实归属与封存来源不一致"):
    if not ok:
        raise AnalysisContractError(message)


def _string(value, maximum=600, *, empty=False):
    _need(type(value) is str and len(value) <= maximum
        and (empty or bool(value.strip())), "ERP或主数据身份文本无效")
    return value


class _Ledger(previous._Result):
    def __init__(self, db, source, master, plan_digest, pair_key, checkpoint=None):
        super().__init__(db, checkpoint)
        self.sales_source, self.master_source = source, master
        self.plan_digest, self.pair_key = plan_digest, pair_key
        self._fact_rows = 0
        self._master_snapshot_date = None
        self._master_declared_status = None
        self._sales_claimed_coverage = None
        self._sales_observed_dates = set()

    def _checked_pages(self, pages, source, kind):
        query = source["query"]
        periods = comparison_periods(query["startDate"], query["endDate"])
        revision = None
        for index, page in enumerate(pages):
            filters = page.get("filters")
            expected_filters = {"platform": query["platform"], "shop": query["shop"],
                "window": query["window"], "periods": periods}
            if kind == "sales":
                expected_filters.update(channel=query["channel"],
                    startDate=query["startDate"], endDate=query["endDate"], limit=100)
            else:
                expected_filters["dataset"] = "master"
            pagination = page.get("pagination")
            _need(type(filters) is dict and filters == expected_filters
                and type(pagination) is dict and pagination.get("limit") == 100,
                "ERP与当前主数据页使用了其他查询或比较期")
            current_revision = page.get("sourceRevision")
            _need(type(current_revision) is str and bool(current_revision)
                and (revision is None or revision == current_revision),
                "ERP或主数据来源revision在页间变化")
            revision = current_revision
            declared = page.get("coverage")
            if index == 0 and kind == "sales":
                _need(type(declared) is dict)
                self._sales_claimed_coverage = declared
            elif index == 0:
                _need(type(declared) is dict
                    and declared.get("historicalMapping") is False
                    and declared.get("status") in {"current_master", "no_records"}
                    and ((declared["status"] == "current_master"
                        and type(declared.get("snapshotDate")) is str)
                        or (declared["status"] == "no_records"
                            and declared.get("snapshotDate") is None)),
                    "主数据不是明确的本期完整快照")
                self._master_snapshot_date = declared.get("snapshotDate")
                self._master_declared_status = declared["status"]
            else:
                _need(declared is None, "后续页不能覆盖首页日期与快照声明")
            yield page

    def _masters(self, rows):
        for row in rows:
            _need(type(row) is dict and type(row.get("dimensions")) is dict
                and type(row.get("metrics")) is dict and not row["metrics"]
                and type(row.get("rowId")) is str
                and type(row.get("sourceRowHash")) is str,
                "当前主数据行缺少固定身份或源摘要")
            _need(row.get("snapshotDate") == self._master_snapshot_date,
                "主数据源行混入另一快照日期")
            code = row["dimensions"].get("merchantCode")
            if code is None or code == "":
                continue
            _string(code, 240)
            sku, spu, category = row.get("skuId"), row.get("spuId"), row.get("category")
            for value in (sku, spu, category):
                if value is not None:
                    _string(value)
            candidate = {"masterRowId": row["rowId"],
                "masterSourceRowHash": row["sourceRowHash"],
                "merchantCode": code, "skuId": sku,
                "spuId": spu, "platformCategory": category,
                "snapshotDate": row.get("snapshotDate")}
            self._db.execute("INSERT INTO candidates VALUES (?,?,?)",
                (code, row["rowId"], canonical(candidate)))

    def _sales(self, rows):
        for row in rows:
            _need(type(row) is dict and type(row.get("rowId")) is str
                and type(row.get("sourceRowHash")) is str
                and type(row.get("metrics")) is dict
                and set(row["metrics"]) == ERP_METRICS,
                "ERP逐事实字段或十一项指标缺失")
            day = _string(row.get("date"), 10)
            strict_date(day)
            self._sales_observed_dates.add(day)
            period = comparison_periods(self.sales_source["query"]["startDate"],
                self.sales_source["query"]["endDate"])[self.sales_source["query"]["window"]]
            _need(period["startDate"] <= day <= period["endDate"],
                "ERP业务日期不在所选实际比较窗口")
            online = row.get("onlineSpecCode")
            if online is not None:
                _string(online, 240, empty=True)
            product = row.get("productCode")
            if product is not None:
                _string(product, 240, empty=True)
            category = row.get("category")
            if category is not None:
                _string(category)
            metrics = row["metrics"]
            _need(all(type(value) is int and abs(value) <= MAX_SAFE_INTEGER
                for value in metrics.values()), "ERP指标不是有符号无损整数")
            net, positive, refund, cost = (metrics[key] for key in
                ("netSalesCents", "positiveSalesCents", "refundCents", "costCents"))
            _need(positive == max(net, 0) and refund == max(-net, 0)
                and metrics["grossProfitCents"] == net - cost,
                "退款符号、正向销售或源毛利关系不一致")
            candidates = self._candidate_rows(online)
            identities = {canonical([item["skuId"], item["spuId"],
                item["platformCategory"]]) for item in candidates}
            complete = all(all(type(item[key]) is str and item[key].strip()
                for key in ("skuId", "spuId", "platformCategory"))
                for item in candidates)
            status = ("unmatched" if not candidates else
                "incomplete" if not complete else
                "matched" if len(identities) == 1 else "ambiguous")
            assigned = candidates[0] if status == "matched" else None
            payload = {"sourceRowId": row["rowId"],
                "sourceRowHash": row["sourceRowHash"],
                "businessDate": day, "platform": row["platform"],
                "shopName": row["shopName"], "channel": row.get("channel"),
                "onlineSpecCode": online, "productCode": product,
                "erpCategory": category, "mappingStatus": status,
                "assignedSkuId": assigned["skuId"] if assigned else None,
                "assignedSpuId": assigned["spuId"] if assigned else None,
                "assignedPlatformCategory": assigned["platformCategory"] if assigned else None,
                "metrics": metrics, "unassigned": status != "matched"}
            self._fact_rows += 1
            _need(self._fact_rows <= MAX_FACT_ROWS,
                "ERP逐事实数量超过当前收集容量，禁止截断")
            self._db.execute("INSERT INTO facts VALUES (?,?,?)",
                (self._fact_rows, row["rowId"], canonical(payload)))

    def _candidate_rows(self, code):
        if code is None or code == "":
            return []
        count, size = self._db.execute(
            "SELECT COUNT(*),COALESCE(SUM(LENGTH(CAST(payload AS BLOB))),0) "
            "FROM candidates WHERE code=?", (code,)).fetchone()
        _need(count <= MAX_CANDIDATE_ROWS and size <= MAX_CANDIDATE_BYTES,
            "完整主数据候选超过单ERP行固定容量，禁止截断")
        return [json.loads(raw) for (raw,) in self._db.execute(
            "SELECT payload FROM candidates WHERE code=? ORDER BY master_row_id COLLATE BINARY",
            (code,))]

    def _row(self, payload, index):
        item = json.loads(payload)
        candidates = self._candidate_rows(item["onlineSpecCode"])
        value = {**item, "masterCandidates": candidates, "rowIndex": index,
            "historicalOwnershipVerified": False,
            "erpFactAssignedOnce": True}
        value["id"] = digest([self.plan_digest, self.pair_key,
            self._source_stats["sales"]["proof"]["evidenceDigest"], value])
        _need(previous._json(value)[1] <= MAX_ROW_BYTES,
            "完整ERP行和全部主数据候选超过单行容量，禁止截断")
        return value

    def scan(self):
        self._available()
        cursor = self._db.execute("SELECT payload FROM facts ORDER BY sequence")
        self._cursors.add(cursor)
        index = 0
        try:
            while True:
                self._available()
                record = cursor.fetchone()
                if record is None:
                    break
                (payload,) = record
                if self._checkpoint is not None and index % 100 == 0:
                    self._checkpoint({"stage": "erp_fact_assignment_scan",
                        "rowOffset": index})
                yield self._row(payload, index)
                index += 1
            _need(index == self._fact_rows,
                "ERP逐事实扫描数量与完整封存来源不同")
        finally:
            if cursor in self._cursors:
                self._cursors.remove(cursor)
                cursor.close()

    def _build(self, sales_pages, master_pages, sales_expected, master_expected):
        master_scope, master = self._read(self._checked_pages(master_pages,
            self.master_source, "master"), "master", self._masters, master_expected)
        _need((self._master_declared_status == "no_records") ==
            (master["rowCount"] == 0),
            "主数据快照有无记录与完整页不一致")
        sales_scope, sales = self._read(self._checked_pages(sales_pages,
            self.sales_source, "sales"), "sales", self._sales, sales_expected)
        period = comparison_periods(self.sales_source["query"]["startDate"],
            self.sales_source["query"]["endDate"])[self.sales_source["query"]["window"]]
        _need(canonical(self._sales_claimed_coverage) == canonical(
            coverage(period, self._sales_observed_dates)),
            "ERP业务日覆盖与完整源行不一致")
        self._source_stats["sales"]["proof"] = sales
        self._source_stats["master"]["proof"] = master
        _need(master_scope == sales_scope and master_scope ==
            (self.sales_source["query"]["platform"], self.sales_source["query"]["shop"]),
            "ERP事实与当前主数据跨平台或店铺")
        sums, unassigned_sums, statuses, count = defaultdict(int), defaultdict(int), defaultdict(int), 0
        cursor = self._db.execute("SELECT payload FROM facts ORDER BY sequence")
        self._cursors.add(cursor)
        for (payload,) in cursor:
            item = json.loads(payload)
            count += 1
            statuses[item["mappingStatus"]] += 1
            for metric, value in item["metrics"].items():
                sums[metric] += value
                _need(abs(sums[metric]) <= MAX_SAFE_INTEGER,
                    "ERP分配账本累计指标溢出")
                if item["mappingStatus"] != "matched":
                    unassigned_sums[metric] += value
                    _need(abs(unassigned_sums[metric]) <= MAX_SAFE_INTEGER,
                        "ERP未分配池累计指标溢出")
            self._row(payload, count-1)
        self._cursors.remove(cursor)
        cursor.close()
        _need(count == self._fact_rows == sales["rowCount"]
            and set(sales["metrics"]) == ERP_METRICS
            and all(not cell["missingRows"] and sums[key] == (cell["value"] or 0)
                for key, cell in sales["metrics"].items()),
            "ERP逐事实与来源控制总额或行数不一致")
        self._groups = count
        summary = {"schemaVersion": SCHEMA,
            "algorithmVersion": ALGORITHM, "mappingPlanDigest": self.plan_digest,
            "pairKey": self.pair_key, "platform": sales_scope[0],
            "shopName": sales_scope[1], "period": self.sales_source["query"]["window"],
            "rowCount": count, "coverage": {name: statuses[name] for name in
                ("matched", "ambiguous", "unmatched", "incomplete")},
            "totals": dict(sums), "unassignedTotals": dict(unassigned_sums),
            "sourceProofs": {"sales": sales, "master": master},
            "assignmentBasis": "onlineSpecCode_exact_to_current_master_merchantCode",
            "productCodeFallbackUsed": False,
            "historicalOwnershipVerified": False,
            "ambiguousSameSpuAssigned": False,
            "netshopAdFinanceCombined": False,
            "authorityVerified": False}
        summary["resultDigest"] = digest(summary)
        self._summary = canonical(summary)
        self._active = True


@contextmanager
def assign_facts(sources, plan, pair_key, sales_pages, master_pages,
                 sales_expected, master_expected, *, max_scratch_bytes=None,
                 checkpoint=None):
    """Yield a one-pass temporary ledger only after all pages and rows verify."""
    checkpoint = Checkpoint.wrap(checkpoint)
    checked = mapping_plan._checked_plan(plan, sources)
    selected = next((pair for pair in checked["pairs"] if pair["pairKey"] == pair_key), None)
    _need(selected is not None, "ERP关联pair不在固定完整计划中")
    indexed = {source["key"]: source for source in normalize_sources(sources)}
    sales, master = indexed[selected["salesKey"]], indexed[selected["masterKey"]]
    _need(type(sales_expected) is dict and type(master_expected) is dict)
    for proof in (sales_expected, master_expected):
        _need(len(canonical(proof).encode("utf-8")) <= 8192
            and type(proof.get("sourceRef")) is str
            and type(proof.get("evidenceDigest")) is str,
            "封存来源核对记录缺失或超容量")
    sales_expected = json.loads(canonical(sales_expected))
    master_expected = json.loads(canonical(master_expected))
    limit = MAX_SCRATCH_BYTES if max_scratch_bytes is None else max_scratch_bytes
    _need(type(limit) is int and 0 < limit <= MAX_SCRATCH_BYTES,
        "逐事实临时磁盘额度无效")
    with TemporaryDirectory(prefix="teruisi-erp-fact-assignment-") as directory:
        db = None
        ledger = None
        try:
            db = sqlite3.connect(str(Path(directory) / "facts.sqlite"))
            if checkpoint is not None:
                checkpoint.attach(db, "erp_fact_assignment_sqlite")
            db.execute("PRAGMA journal_mode=OFF")
            db.execute("PRAGMA synchronous=OFF")
            db.execute("PRAGMA cache_size=-2048")
            db.execute("PRAGMA temp_store=FILE")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            _need(limit >= page_size)
            db.execute(f"PRAGMA max_page_count={limit // page_size}")
            db.execute("CREATE TABLE candidates(code TEXT COLLATE BINARY,master_row_id TEXT COLLATE BINARY,payload TEXT NOT NULL,PRIMARY KEY(code,master_row_id)) WITHOUT ROWID")
            db.execute("CREATE TABLE facts(sequence INTEGER PRIMARY KEY,source_row_id TEXT UNIQUE,payload TEXT NOT NULL)")
            ledger = _Ledger(db, sales, master, digest(checked), pair_key, checkpoint)
            ledger._build(sales_pages, master_pages, sales_expected, master_expected)
            yield ledger
            if checkpoint is not None:
                checkpoint.raise_if_failed()
        except BaseException as error:
            if checkpoint is not None:
                checkpoint.raise_if_failed()
            if isinstance(error, (sqlite3.DatabaseError, KeyError, TypeError,
                    ValueError, UnicodeError, RecursionError)):
                raise AnalysisContractError("逐ERP事实源页、候选或临时空间无效") from error
            raise
        finally:
            if db is not None and checkpoint is not None:
                db.set_progress_handler(None, 0)
            if ledger is not None:
                ledger._active = False
                for cursor in ledger._cursors:
                    cursor.close()
                ledger._cursors.clear()
            if db is not None:
                db.close()
