"""Unregistered single-window v4 followed-SKU relation over complete pages.

The caller's replay proof is checked against every page, but cannot grant
source, seal, account, Agent, or report authority. Windows remain separate.
"""
from contextlib import contextmanager
import json
import sqlite3
import time

from . import evidence_v4, promotion_attributed_sku_relation as old
from . import promotion_relation_v4 as v4, promotion_views as native
from .aggregation import group_result
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, PageReconciler, canonical, coverage, digest
from .partitioned import Checkpoint, PartitionedGroups, MAX_RESULT_GROUPS, MAX_SCRATCH_BYTES


SCHEMA = "business-promotion-attributed-sku-relation-candidate-v4"
ALGORITHM = "promotion-attributed-sku-single-window-v4"
VIEWS = dict(old.VIEWS)
MAX_SECONDS = 600
MAX_OUTPUT_BYTES = 512 * 1024 * 1024
MAX_RESPONSE_BYTES = 38_000


def _need(condition, message="v4跟单SKU完整关系无效"):
    if not condition:
        raise AnalysisContractError(message)


class _OrderedStore:
    """Read the primary-key order directly; no GROUP BY/temporary sort."""
    def __init__(self, store):
        self.store = store
        self.db = store.db
        self.metrics = store.metrics[0]
        plan = self.db.execute("EXPLAIN QUERY PLAN SELECT payload FROM groups "
            "WHERE side=0 ORDER BY entity COLLATE BINARY").fetchall()
        _need(not any("TEMP B-TREE" in str(row).upper() for row in plan),
            "v4跟单SKU排序需要未计量的临时B树")

    def _count(self):
        return self.db.execute("SELECT count(*) FROM groups WHERE side=0").fetchone()[0]

    def page(self, offset, limit):
        rows = self.db.execute("SELECT payload FROM groups WHERE side=0 "
            "ORDER BY entity COLLATE BINARY LIMIT ? OFFSET ?",
            (limit, offset)).fetchall()
        return self._count(), [[group_result(json.loads(raw), self.metrics), None]
            for (raw,) in rows]

    def scan(self):
        total = self._count()
        def values():
            cursor = self.db.execute("SELECT payload FROM groups WHERE side=0 "
                "ORDER BY entity COLLATE BINARY")
            self.store._cursors.add(cursor)
            try:
                for (raw,) in cursor:
                    yield [group_result(json.loads(raw), self.metrics), None]
            finally:
                self.store._cursors.discard(cursor)
                cursor.close()
        return total, values()


@contextmanager
def table(source, pages, replay_proof, *, view, max_scratch_bytes=None,
          max_output_bytes=None, max_seconds=None, checkpoint=None):
    """Check one complete current/previous/yearAgo source before any read.

    A synchronous caller-owned iterator can block in next(); the deadline is
    cooperative at every boundary and SQLite progress call, not a process
    timeout. A production caller must also bound its page supplier.
    """
    _need(type(view) is str and view in VIEWS, "v4跟单SKU视图未知")
    source, proof, query, periods = v4._source(source, replay_proof)
    _need(proof.get("payloadCursorChainVerified") is True
        and proof.get("requestCursorAuditVerified") is True,
        "v4跟单SKU缺独立请求游标或持久页链核验标志")
    scratch = MAX_SCRATCH_BYTES if max_scratch_bytes is None else max_scratch_bytes
    output = MAX_OUTPUT_BYTES if max_output_bytes is None else max_output_bytes
    seconds = MAX_SECONDS if max_seconds is None else max_seconds
    _need(type(scratch) is int and 0 < scratch <= MAX_SCRATCH_BYTES
        and type(output) is int and 0 < output <= MAX_OUTPUT_BYTES
        and type(seconds) is int and 0 < seconds <= MAX_SECONDS,
        "v4跟单SKU容量与时间只能收紧")
    external = Checkpoint.wrap(checkpoint)
    deadline = time.monotonic() + seconds
    def tick(event):
        _need(time.monotonic() <= deadline, "v4跟单SKU超过合作式时间边界")
        if external is not None:
            external(event)
    check = Checkpoint.wrap(tick)
    count = size = qualified = 0
    observed_dates = set()
    verifier = PageReconciler()
    first = None
    filters = {key: query[key] for key in ("platform", "shop", "dataset", "window")}
    filters["periods"] = periods
    with PartitionedGroups(checkpoint=check) as store:
        db = store.db
        db.execute("PRAGMA journal_mode=OFF")
        db.execute("PRAGMA temp_store=MEMORY")
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        _need(scratch >= page_size)
        applied = db.execute(f"PRAGMA max_page_count={scratch // page_size}").fetchone()[0]
        _need(applied * page_size <= scratch,
            "v4跟单SKU主SQLite页预算未生效")
        db.execute("CREATE TABLE seen_hashes(hash TEXT PRIMARY KEY) WITHOUT ROWID")
        store.configure(0, list(VIEWS[view]), sorted(proof["reconciliation"]["metrics"]))
        iterator = iter(pages)
        while True:
            check({"stage": "attributed_sku_v4", "phase": "before_next", "page": count+1})
            try:
                page = next(iterator)
            except StopIteration:
                break
            check({"stage": "attributed_sku_v4", "phase": "after_next", "page": count+1})
            count += 1
            _need(count <= evidence_v4.MAX_SOURCE_PAGES,
                "v4跟单SKU页数超过来源限额")
            page = native._copy(page, evidence_v4.MAX_DAILY_PAGE_BYTES)
            raw_size = len(canonical(page).encode("utf-8"))
            size += raw_size
            _need(size <= evidence_v4.MAX_SOURCE_BYTES,
                "v4跟单SKU来源字节超过限额")
            _need(type(page) is dict and page.get("schemaVersion") == native.SOURCE_SCHEMA
                and page.get("source") == "jd_promotion"
                and page.get("sourceDataset") == "ad"
                and page.get("sourceRef") == proof["sourceRef"]
                and page.get("sourceRevision") == proof["sourceRevision"]
                and page.get("monetaryUnit") == "CNY_CENT"
                and page.get("filters") == filters
                and type(page.get("pagination")) is dict
                and set(page["pagination"]) == {"limit", "hasMore", "nextCursor"}
                and page["pagination"].get("limit") == 100
                and type(page["pagination"].get("hasMore")) is bool
                and (page["pagination"].get("nextCursor") is None or
                    type(page["pagination"]["nextCursor"]) is str and
                    0 < len(page["pagination"]["nextCursor"]) <= 1600)
                and type(page.get("items")) is list
                and len(page["items"]) <= 100
                and type(page.get("pageEvidence")) is dict
                and set(page["pageEvidence"]) == {"rowCount", "sha256"}
                and type(page["pageEvidence"]["rowCount"]) is int
                and page["pageEvidence"]["rowCount"] == len(page["items"])
                and type(page["pageEvidence"]["sha256"]) is str
                and v4._SHA.fullmatch(page["pageEvidence"]["sha256"]) is not None,
                "v4跟单SKU来源、修订或页形状不一致")
            fixed = {key:page.get(key) for key in
                ("sourceRevision", "filters", "metricSemantics", "consistency")}
            if first is None:
                first = fixed
                _need(type(page.get("control")) is dict
                    and type(page["control"].get("rowCount")) is int
                    and page["control"]["rowCount"] == proof["rowCount"]
                    and type(page["control"].get("typedTotals")) is dict
                    and set(page["control"]["typedTotals"]) == native.BASE_METRICS
                    and all(type(value) is int and abs(value) <= MAX_SAFE_INTEGER
                        for value in page["control"]["typedTotals"].values())
                    and type(page.get("metricSemantics")) is dict
                    and type(page.get("coverage")) is dict,
                    "v4跟单SKU首页控制或覆盖无效")
                declared_coverage = page["coverage"]
            else:
                _need(first == fixed and page.get("control") is None
                    and page.get("coverage") is None
                    and page.get("availableDates") is None,
                    "v4跟单SKU后续页元数据变化")
            for row in page["items"]:
                v4._row_shape(row, query, periods[query["window"]])
                qualified += int(all(row["dimensions"][key] is not None
                    for key in VIEWS[view]))
            verifier.consume(page, request_cursor=verifier.expected_cursor)
            for row in page["items"]:
                db.execute("INSERT INTO seen_hashes VALUES (?)", (row["sourceRowHash"],))
                observed_dates.add(row["date"])
            store.consume(0, page["items"])
            _need(store.counts[0] <= MAX_RESULT_GROUPS,
                "v4跟单SKU分组超过容量，不裁剪")
        reconciled = verifier.result()
        _need(count == proof["pageCount"] and size == proof["storedBytes"]
            and reconciled == proof["reconciliation"]
            and declared_coverage == proof["coverage"]
            and declared_coverage == coverage(periods[query["window"]], observed_dates),
            "v4跟单SKU完整页链、日期或来源控制金额不一致")
        store.verify(0, reconciled)
        ordered = _OrderedStore(store)
        total = ordered._count()
        binding = {"algorithmVersion": ALGORITHM, "view": view,
            "source": source, "replayProofDigest": proof["proofDigest"],
            "sourceMetadata": first, "coverage": declared_coverage}
        header = {"schemaVersion": SCHEMA, "algorithmVersion": ALGORITHM,
            "view": view, "groupingKeys": ["platform", "shopName", *VIEWS[view]],
            "source": source, "sourceRef": proof["sourceRef"],
            "sourceRevision": proof["sourceRevision"],
            "sourcePageCount": count, "sourceRowCount": reconciled["rowCount"],
            "sourceBytes": size, "sourceMetrics": reconciled["metrics"],
            "sourceWindow": query["window"], "periods": periods,
            "coverage": declared_coverage, "identityCoverage": {
                "qualifiedRows": qualified,
                "unqualifiedRows": reconciled["rowCount"] - qualified},
            "sourceTraversal": {"pages": count, "rows": reconciled["rowCount"]},
            "baselineSource": None, "dateCoverageComparable": False,
            "tableBindingDigest": digest(binding), "replayProofDigest": proof["proofDigest"],
            "total": total, "authorityVerified": False,
            "sourceAuthorityVerified": False, "sealed": False,
            "agentReadPersisted": False, "registeredRenderer": False,
            "crossViewAdditive": False, "attributedSkuIsProductMasterOwnership": False,
            "requestCursorAuditIndependentlyVerified": False,
            "limitations": ["调用方v4证明经逐页核对，但纯模块不授予来源、封存或报告权威",
                "三个窗口须分别读完；来源日期覆盖不证明词货逐日完整",
                "跟单SKU不等于推广SKU、触发SKU、商品主数据或增量销售",
                "主SQLite文件有页预算；页提供方阻塞不受合作式超时约束"]}
        result = old._Table(ordered, header,
            {"maxResponseBytes": MAX_RESPONSE_BYTES}, check)
        result.page(total)
        output_bytes = 0
        for row in result.scan():
            output_bytes += len(canonical(row).encode("utf-8")) + 1
            _need(output_bytes <= output,
                "v4跟单SKU完整关系输出超过容量，不截断")
        header["outputBytes"] = output_bytes
        result._header = canonical(header)
        try:
            check({"stage": "attributed_sku_v4", "phase": "ready"})
            yield result
            check({"stage": "attributed_sku_v4", "phase": "complete"})
        finally:
            result._close()
