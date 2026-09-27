"""Unregistered complete JD promotion relation with distinct SKU roles.

All selected current/previous/yearAgo facts stream through bounded temporary
SQLite. Three views independently partition the same facts and must never be
added together. No supplied sourceRef or digest confers report authority.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory

from . import promotion_views as native
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, PageReconciler, canonical, comparison_periods, coverage, digest
from .partitioned import Checkpoint


SCHEMA = "business-promotion-relation-candidate-v2"
ALGORITHM = "jd-keyword-searchterm-plan-unit-match-three-sku-roles-v2"
WINDOWS = ("current", "previous", "yearAgo")
BASE = ("keyword", "searchTerm", "planId", "planName", "unitId", "unitName", "matchType")
VIEWS = {"full_relation": (*BASE, "promotedSkuId", "triggerSkuId", "attributedSkuId"),
    "promoted_sku": (*BASE, "promotedSkuId"),
    "attributed_sku": (*BASE, "attributedSkuId")}
MAX_SOURCE_ROWS = 2000 * 100  # Current sealed-v2 collector, not reference scale.
MAX_PAGES = 2000
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_SCRATCH_BYTES = 256 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_ROW_BYTES = 38_000
MAX_GROUP_ROWS = MAX_SOURCE_ROWS * len(VIEWS)


def _need(ok, message="推广完整词链与封存来源不一致"):
    if not ok:
        raise AnalysisContractError(message)


def _empty(keys):
    return {key: {"value": None, "presentRows": 0, "missingRows": 0}
        for key in sorted(keys)}


def _add(target, values):
    _need(type(values) is dict and set(values) == set(target))
    for key, value in values.items():
        _need(value is None or type(value) is int and abs(value) <= MAX_SAFE_INTEGER)
        cell = target[key]
        if value is None:
            cell["missingRows"] += 1
        else:
            cell["presentRows"] += 1
            cell["value"] = (cell["value"] or 0) + value
            _need(abs(cell["value"]) <= MAX_SAFE_INTEGER)


def _merge(target, source):
    _need(set(target) == set(source))
    for key, cell in source.items():
        _need(type(cell) is dict and set(cell) == {"value", "presentRows", "missingRows"})
        current = target[key]
        current["presentRows"] += cell["presentRows"]
        current["missingRows"] += cell["missingRows"]
        if cell["value"] is not None:
            current["value"] = (current["value"] or 0) + cell["value"]
            _need(abs(current["value"]) <= MAX_SAFE_INTEGER)


def _group(db, window, view, row, group_counts):
    entity = {key: row["dimensions"][key] for key in VIEWS[view]}
    key = canonical([row["date"], entity])
    saved = db.execute("SELECT payload FROM groups WHERE window=? AND view=? AND entity=?",
        (window, view, key)).fetchone()
    if saved:
        value = json.loads(saved[0])
    else:
        group_counts[(window, view)] = group_counts.get((window, view), 0) + 1
        _need(sum(group_counts.values()) <= MAX_GROUP_ROWS,
            "完整词链分组超过v2事实容量，禁止截断")
        value = {"date": row["date"], "entity": entity,
            "sourceFactCount": 0, "metrics": _empty(native.METRICS),
            "sourceRowDigest": digest([])}
    value["sourceFactCount"] += 1
    value["sourceRowDigest"] = digest([value["sourceRowDigest"],
        row["rowId"], row["sourceRowHash"]])
    _add(value["metrics"], row["metrics"])
    _need(len(canonical(value).encode("utf-8")) <= MAX_OUTPUT_ROW_BYTES)
    db.execute("INSERT INTO groups VALUES (?,?,?,?) "
        "ON CONFLICT(window,view,entity) DO UPDATE SET payload=excluded.payload",
        (window, view, key, canonical(value)))


def _read_source(db, window, source, pages, expected, counters, group_counts, check):
    query = source["query"]
    periods = comparison_periods(query["startDate"], query["endDate"])
    period = periods[window]
    filters = {key: query[key] for key in ("platform", "shop", "dataset", "window")}
    filters["periods"] = periods
    verifier, observed, first, declared_coverage, page_count = (
        PageReconciler(), set(), None, None, 0)
    for raw in pages:
        if check is not None:
            check({"stage": "promotion_relation", "phase": "page", "window": window})
        page = native._copy(raw, 128*1024)
        page_count += 1
        counters["pages"] += 1
        counters["bytes"] += len(canonical(page).encode("utf-8"))
        _need(counters["pages"] <= MAX_PAGES and counters["bytes"] <= MAX_SOURCE_BYTES,
            "推广完整事实超出当前封存v2页/字节容量")
        _need(page.get("schemaVersion") == native.SOURCE_SCHEMA
            and page.get("source") == "jd_promotion"
            and page.get("sourceDataset") == "ad"
            and page.get("sourceRef") == source["sourceRef"]
            and page.get("monetaryUnit") == "CNY_CENT"
            and page.get("filters") == filters,
            "推广词链来源、查询或期间错配")
        pagination = page.get("pagination")
        _need(type(pagination) is dict and type(pagination.get("limit")) is int
            and 1 <= pagination["limit"] <= 100)
        fixed = {key: page.get(key) for key in
            ("sourceRevision", "filters", "metricSemantics", "consistency")}
        fixed["limit"] = pagination["limit"]
        if first is None:
            first = fixed
            _need(type(page.get("coverage")) is dict
                and type(page["coverage"].get("presentDates")) is list
                and page["coverage"] == coverage(period, page["coverage"].get("presentDates", []))
                and type(page.get("control")) is dict
                and page["control"].get("rowCount") == expected["rowCount"]
                and type(page["control"].get("typedTotals")) is dict
                and set(page["control"]["typedTotals"]) == native.BASE_METRICS
                and all(type(amount) is int and abs(amount) <= MAX_SAFE_INTEGER
                    for amount in page["control"]["typedTotals"].values()))
            declared_coverage = page["coverage"]
        else:
            _need(fixed == first and page.get("coverage") is None
                and page.get("control") is None and page.get("availableDates") is None,
                "推广词链后续页元数据变化")
        _need(type(page.get("items")) is list
            and len(page["items"]) <= pagination["limit"])
        verifier.consume(page, request_cursor=verifier.expected_cursor)
        for row in page["items"]:
            _need(type(row) is dict and set(row) == native.ROW_FIELDS
                and type(row["rowId"]) is str
                and re.fullmatch(r"[1-9][0-9]{0,19}", row["rowId"]) is not None
                and type(row["sourceRowHash"]) is str
                and re.fullmatch(r"[0-9a-f]{64}", row["sourceRowHash"]) is not None
                and type(row.get("batchId")) is str
                and 1 <= len(row["batchId"]) <= 160
                and (row["platform"], row["shopName"]) ==
                    (query["platform"], query["shop"])
                and type(row["date"]) is str
                and period["startDate"] <= row["date"] <= period["endDate"]
                and type(row["dimensions"]) is dict
                and set(row["dimensions"]) == native.DIMENSIONS
                and type(row["metrics"]) is dict
                and set(row["metrics"]) == native.METRICS,
                "推广规范行或三种SKU身份无效")
            for identity in row["dimensions"].values():
                _need(identity is None or type(identity) is str and
                    0 < len(identity) <= 240 and identity == identity.strip(),
                    "推广身份须保持源原值或明确缺失")
            db.execute("INSERT INTO seen_ids VALUES (?)", (row["rowId"],))
            db.execute("INSERT INTO seen_hashes VALUES (?,?)",
                (window, row["sourceRowHash"]))
            observed.add(row["date"])
            counters["rows"] += 1
            _need(counters["rows"] <= MAX_SOURCE_ROWS,
                "reference_scale_capacity_gap：当前v2收集器无法覆盖完整推广源")
            for view in VIEWS:
                _group(db, window, view, row, group_counts)
        db.commit()
    proof = verifier.result()
    _need(proof == expected and page_count >= 1
        and first is not None
        and type(first["sourceRevision"]) is str
        and 1 <= len(first["sourceRevision"]) <= 128
        and type(first["metricSemantics"]) is dict
        and page_count <= MAX_PAGES
        and declared_coverage == coverage(period, observed)
        and source["sourceRef"] == proof["sourceRef"],
        "推广完整词链源页、控制汇总或目录摘要不一致")
    return {"source": source, "expected": expected,
        "sourceRevision": first["sourceRevision"],
        "metricSemantics": first["metricSemantics"],
        "coverage": coverage(period, observed), "pageCount": page_count}


def _row(source_ref, window, view, payload, index):
    value = json.loads(payload)
    missing = [key for key, item in value["entity"].items() if item is None]
    row = {"window": window, "view": view, "rowIndex": index,
        **value, "identityQualified": not missing,
        "missingIdentityFields": missing}
    row["id"] = digest([SCHEMA, source_ref, window, view,
        row["date"], row["entity"]])
    return row


class _Result:
    def __init__(self, db, manifest, active):
        self._db, self._manifest, self._active = db, canonical(manifest), active
        self._cursors = set()

    @property
    def manifest(self):
        _need(self._active[0], "推广词链已离开临时生命周期")
        return json.loads(self._manifest)

    def scan(self, window, view):
        header = self.manifest
        _need(window in WINDOWS and view in VIEWS
            and header["sources"][window] is not None)
        source_ref = header["sources"][window]["source"]["sourceRef"]
        cursor = self._db.execute("SELECT payload FROM groups WHERE window=? AND view=? "
            "ORDER BY entity COLLATE BINARY", (window, view))
        self._cursors.add(cursor)
        try:
            index = 0
            while True:
                _need(self._active[0], "推广词链扫描已关闭")
                record = cursor.fetchone()
                if record is None:
                    break
                (payload,) = record
                yield _row(source_ref, window, view, payload, index)
                index += 1
        finally:
            if cursor in self._cursors:
                self._cursors.remove(cursor)
                cursor.close()

    def _close(self):
        self._active[0] = False
        for cursor in tuple(self._cursors):
            cursor.close()
        self._cursors.clear()


@contextmanager
def prepare(selections, *, max_scratch_bytes=None, checkpoint=None):
    """Consume exact selected windows fully before exposing three disjoint views."""
    _need(type(selections) is dict and set(selections) == set(WINDOWS)
        and selections["current"] is not None)
    selected, total = {}, 0
    for window in WINDOWS:
        item = selections[window]
        if item is None:
            selected[window] = None
            continue
        _need(type(item) is tuple and len(item) == 3)
        source, pages, expected = item
        _need(type(expected) is dict and type(expected.get("rowCount")) is int)
        if expected["rowCount"] > MAX_SOURCE_ROWS:
            raise AnalysisContractError(
                "reference_scale_capacity_gap：当前v2收集器无法覆盖完整推广源")
        source, expected = native._source(source, expected)
        _need(source["query"]["window"] == window)
        selected[window] = (source, pages, expected)
        total += expected["rowCount"]
    _need(total <= MAX_SOURCE_ROWS,
        "reference_scale_capacity_gap：当前v2完整目录事实容量不足")
    keys = [item[0]["key"] for item in selected.values() if item is not None]
    _need(len(keys) == len(set(keys)), "推广三期来源key重复")
    current = selected["current"][0]["query"]
    for window in WINDOWS[1:]:
        if selected[window] is None:
            continue
        query = selected[window][0]["query"]
        _need(selected[window][0]["key"] != selected["current"][0]["key"]
            and {key:value for key,value in query.items() if key != "window"}
                == {key:value for key,value in current.items() if key != "window"},
            "推广三期来源跨店铺、原始期间或广告粒度")
    check = Checkpoint.wrap(checkpoint)
    limit = MAX_SCRATCH_BYTES if max_scratch_bytes is None else max_scratch_bytes
    _need(type(limit) is int and 0 < limit <= MAX_SCRATCH_BYTES)
    with TemporaryDirectory(prefix="teruisi-promotion-relation-v2-") as folder:
        db, result = None, None
        try:
            db = sqlite3.connect(str(Path(folder)/"relation.sqlite"))
            db.execute("PRAGMA journal_mode=OFF")
            db.execute("PRAGMA synchronous=OFF")
            db.execute("PRAGMA cache_size=-2048")
            db.execute("PRAGMA temp_store=FILE")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            _need(limit >= page_size)
            db.execute(f"PRAGMA max_page_count={limit // page_size}")
            if check is not None:
                check.attach(db, "promotion_relation_sqlite")
            db.execute("CREATE TABLE seen_ids(row_id TEXT PRIMARY KEY) WITHOUT ROWID")
            db.execute("CREATE TABLE seen_hashes(window TEXT,source_hash TEXT,"
                "PRIMARY KEY(window,source_hash)) WITHOUT ROWID")
            db.execute("CREATE TABLE groups(window TEXT,view TEXT,entity TEXT,payload TEXT,"
                "PRIMARY KEY(window,view,entity)) WITHOUT ROWID")
            counters, group_counts, sources = {"rows":0,"pages":0,"bytes":0}, {}, {}
            semantics = None
            for window, item in selected.items():
                if item is None:
                    sources[window] = None
                    continue
                source, pages, expected = item
                metadata = _read_source(db, window, source, pages, expected,
                    counters, group_counts, check)
                _need(semantics is None or metadata["metricSemantics"] == semantics,
                    "推广三期源指标语义变化")
                semantics = metadata["metricSemantics"]
                sources[window] = metadata
            tables, output_bytes = [], 0
            for window, metadata in sources.items():
                if metadata is None:
                    continue
                expected = metadata["expected"]
                for view in VIEWS:
                    totals, count, groups, sha, size = (
                        _empty(expected["metrics"]), 0, 0, hashlib.sha256(), 0)
                    for (payload,) in db.execute("SELECT payload FROM groups WHERE window=? AND view=? "
                            "ORDER BY entity COLLATE BINARY", (window, view)):
                        row = _row(metadata["source"]["sourceRef"], window, view,
                            payload, groups)
                        raw = (canonical(row)+"\n").encode("utf-8")
                        _need(len(raw) <= MAX_OUTPUT_ROW_BYTES)
                        size += len(raw); output_bytes += len(raw)
                        _need(output_bytes <= MAX_OUTPUT_BYTES,
                            "完整推广词链材料超过容量，禁止截断")
                        sha.update(raw); groups += 1
                        count += row["sourceFactCount"]
                        _merge(totals, row["metrics"])
                    _need(count == expected["rowCount"] and totals == expected["metrics"],
                        "推广每种关系视图未与独立来源控制总额守恒")
                    tables.append({"window":window,"view":view,"groupCount":groups,
                        "sourceFactCount":count,"metrics":totals,
                        "ndjsonBytes":size,"ndjsonSha256":sha.hexdigest()})
            manifest = {"schemaVersion": SCHEMA, "algorithmVersion": ALGORITHM,
                "sources": sources, "sourceTraversal": counters,
                "tables": tables, "outputBytes": output_bytes,
                "viewsAreNonAdditive": True,
                "eachSourceFactAssignedOncePerView": True,
                "missingIdentitiesRemainBuckets": True,
                "promotedSkuIsNotAttributedSku": True,
                "referenceScaleCapacityGap": False,
                "authorityVerified": False, "registeredAgentTool": False,
                "registeredRenderer": False,
                "limitations": ["三种关系视图各自包含完整同一来源事实，金额不得跨视图相加。",
                    "推广、触发、跟单SKU保持源角色；跟单SKU不冒充明确推广SKU。",
                    "缺身份桶保留费用及平台归因金额，不自动分配到商品或ERP。",
                    "当前封存v2事实容量最多20万行/2000页/64MiB，575095行参考来源须提升收集协议后才能完整分析。",
                    "平台归因金额不是ERP销售或因果增量；没有正式报告授权。"]}
            manifest["manifestDigest"] = digest(manifest)
            _need(len(canonical(manifest).encode("utf-8")) <= 128*1024)
            result = _Result(db, manifest, [True])
            if check is not None: check({"stage":"promotion_relation","phase":"ready"})
            yield result
            if check is not None: check({"stage":"promotion_relation","phase":"complete"})
        except sqlite3.DatabaseError as error:
            if check is not None: check.raise_if_failed()
            raise AnalysisContractError(
                "推广词链重复源行/内容或临时空间不可用，禁止截断") from error
        finally:
            if result is not None: result._close()
            if db is not None:
                db.set_progress_handler(None, 0)
                db.close()
