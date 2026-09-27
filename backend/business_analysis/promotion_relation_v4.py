"""Single-window v4-scale JD promotion relation, pure and unregistered.

An inspect proof supplied by the caller is checked for consistency, never
treated as source authority. All facts, three partitions, and output are
replayed in bounded temporary SQLite; no 200k-row Python list is made.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory

from . import evidence_v4, promotion_views as native
from .contracts import (AnalysisContractError, MAX_SAFE_INTEGER, PageReconciler,
    canonical, comparison_periods, coverage, digest)
from .partitioned import Checkpoint


SCHEMA = "business-promotion-relation-stream-candidate-v4"
ALGORITHM = "jd-promotion-single-window-three-sku-roles-stream-v4"
REPLAY_SCHEMA = "business-v4-jd-promotion-complete-replay-candidate-v1"
WINDOWS = ("current", "previous", "yearAgo")
BASE = ("keyword", "searchTerm", "planId", "planName", "unitId", "unitName", "matchType")
VIEWS = {"full_relation": (*BASE, "promotedSkuId", "triggerSkuId", "attributedSkuId"),
    "promoted_sku": (*BASE, "promotedSkuId"),
    "attributed_sku": (*BASE, "attributedSkuId")}
MAX_SOURCE_ROWS = evidence_v4.MAX_SOURCE_PAGES * evidence_v4.MAX_ROWS_PER_PAGE
MAX_SCRATCH_BYTES = 4 * 1024 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024 * 1024
MAX_CHUNK_BYTES = 38_000
MAX_GROUP_ROWS = MAX_SOURCE_ROWS * len(VIEWS)
MAX_MANIFEST_BYTES = 38_000
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")


def _need(ok, message="v4推广单窗口完整词链不一致"):
    if not ok:
        raise AnalysisContractError(message)


def _cells(keys):
    return {key: {"value": None, "presentRows": 0, "missingRows": 0}
        for key in sorted(keys)}


def _add_raw(target, raw):
    _need(type(raw) is dict and set(raw) == set(target), "v4推广行指标字段变化")
    for key, value in raw.items():
        _need(value is None or type(value) is int and abs(value) <= MAX_SAFE_INTEGER)
        cell = target[key]
        if value is None:
            cell["missingRows"] += 1
        else:
            cell["presentRows"] += 1
            cell["value"] = (cell["value"] or 0) + value
            _need(abs(cell["value"]) <= MAX_SAFE_INTEGER)


def _merge(target, source):
    _need(type(source) is dict and set(source) == set(target))
    for key, cell in source.items():
        _need(type(cell) is dict and set(cell) == {"value", "presentRows", "missingRows"}
            and type(cell["presentRows"]) is int and type(cell["missingRows"]) is int
            and 0 <= cell["presentRows"] <= MAX_SOURCE_ROWS
            and 0 <= cell["missingRows"] <= MAX_SOURCE_ROWS
            and ((cell["value"] is None and cell["presentRows"] == 0)
                or (type(cell["value"]) is int and abs(cell["value"]) <= MAX_SAFE_INTEGER
                    and cell["presentRows"] > 0)))
        target[key]["presentRows"] += cell["presentRows"]
        target[key]["missingRows"] += cell["missingRows"]
        _need(target[key]["presentRows"] + target[key]["missingRows"] <= MAX_SOURCE_ROWS)
        if cell["value"] is not None:
            target[key]["value"] = (target[key]["value"] or 0) + cell["value"]
            _need(abs(target[key]["value"]) <= MAX_SAFE_INTEGER)


def _source(source, proof):
    source = native._copy(source,8192)
    proof = native._copy(proof,MAX_MANIFEST_BYTES)
    _need(type(source) is dict and set(source) == {"key", "domain", "query"}
        and type(source["key"]) is str and _ID.fullmatch(source["key"]) is not None
        and source["domain"] == "netshop"
        and type(source["query"]) is dict)
    query = source["query"]
    _need(set(query) == {"platform", "shop", "dataset", "startDate", "endDate", "window"}
        and query["platform"] == "京东" and query["dataset"] == "promotion"
        and type(query["shop"]) is str and query["shop"] == query["shop"].strip()
        and 1 <= len(query["shop"]) <= 100
        and query["window"] in WINDOWS)
    periods = comparison_periods(query["startDate"], query["endDate"])
    _need(type(proof) is dict and proof.get("schemaVersion") == REPLAY_SCHEMA
        and type(proof.get("proofDigest")) is str
        and proof["proofDigest"] == digest({key:value for key,value in proof.items()
            if key != "proofDigest"})
        and len(canonical(proof).encode("utf-8")) <= MAX_MANIFEST_BYTES
        and proof.get("sourceKey") == source["key"]
        and type(proof.get("runId")) is str
        and _ID.fullmatch(proof["runId"]) is not None
        and type(proof.get("sourceId")) is str
        and _ID.fullmatch(proof["sourceId"]) is not None
        and type(proof.get("runVersion")) is int and proof["runVersion"] >= 1
        and type(proof.get("sourceVersion")) is int and proof["sourceVersion"] >= 1
        and proof.get("queryDigest") == digest(query)
        and type(proof.get("sourceRef")) is str
        and _SHA.fullmatch(proof["sourceRef"]) is not None
        and type(proof.get("sourceRevision")) is str
        and 1 <= len(proof["sourceRevision"]) <= 128
        and type(proof.get("receiptChainDigest")) is str
        and _SHA.fullmatch(proof["receiptChainDigest"]) is not None
        and type(proof.get("pageCount")) is int
        and 1 <= proof["pageCount"] <= evidence_v4.MAX_SOURCE_PAGES
        and type(proof.get("rowCount")) is int
        and 0 <= proof["rowCount"] <= MAX_SOURCE_ROWS
        and type(proof.get("storedBytes")) is int
        and 0 < proof["storedBytes"] <= evidence_v4.MAX_SOURCE_BYTES
        and proof.get("fullSourceReplayVerified") is True
        and proof.get("internalToolAuditBound") is True
        and proof.get("upstreamSignatureVerified") is False
        and proof.get("sealed") is False
        and proof.get("reportGenerationSupported") is False
        and type(proof.get("reconciliation")) is dict
        and type(proof["reconciliation"].get("metrics")) is dict
        and set(proof["reconciliation"]["metrics"]) ==
            (native.METRICS if proof["rowCount"] else native.BASE_METRICS)
        and proof["reconciliation"].get("rowCount") == proof["rowCount"]
        and type(proof.get("coverage")) is dict,
        "v4重放证明自一致性或精确来源身份无效")
    return source, proof, query, periods


def _row_shape(row, query, period):
    _need(type(row) is dict and set(row) == native.ROW_FIELDS
        and type(row["rowId"]) is str
        and re.fullmatch(r"[1-9][0-9]{0,19}", row["rowId"]) is not None
        and type(row["sourceRowHash"]) is str
        and _SHA.fullmatch(row["sourceRowHash"]) is not None
        and type(row["batchId"]) is str and 1 <= len(row["batchId"]) <= 160
        and (row["platform"], row["shopName"]) ==
            (query["platform"], query["shop"])
        and type(row["date"]) is str
        and period["startDate"] <= row["date"] <= period["endDate"]
        and type(row["dimensions"]) is dict
        and set(row["dimensions"]) == native.DIMENSIONS
        and all(value is None or type(value) is str and 0 < len(value) <= 240
            and value == value.strip() for value in row["dimensions"].values())
        and type(row["metrics"]) is dict and set(row["metrics"]) == native.METRICS
        and all(value is None or type(value) is int and abs(value) <= MAX_SAFE_INTEGER
            for value in row["metrics"].values()),
        "v4推广规范行、身份角色或指标字段无效")


def _group(db, view, row, group_counts):
    entity = {name: row["dimensions"][name] for name in VIEWS[view]}
    key = canonical([row["date"], entity])
    previous = db.execute("SELECT payload FROM groups WHERE view=? AND key=?",
        (view, key)).fetchone()
    if previous is None:
        group_counts[view] += 1
        _need(sum(group_counts.values()) <= MAX_GROUP_ROWS,
            "v4推广完整词链分组超出临时容量，禁止截断")
        value = {"date":row["date"], "entity":entity,
            "sourceFactCount":0, "metrics":_cells(native.METRICS),
            "sourceRowDigest":digest([])}
    else:
        value = json.loads(previous[0])
    value["sourceFactCount"] += 1
    value["sourceRowDigest"] = digest([value["sourceRowDigest"],
        row["rowId"], row["sourceRowHash"]])
    _add_raw(value["metrics"], row["metrics"])
    raw = canonical(value)
    _need(len(raw.encode("utf-8")) <= MAX_CHUNK_BYTES,
        "v4推广单个关系分组超过可输出分片容量")
    db.execute("INSERT INTO groups VALUES (?,?,?) ON CONFLICT(view,key) "
        "DO UPDATE SET payload=excluded.payload", (view,key,raw))


def _render(view, payload, index, source_ref, window):
    value = json.loads(payload)
    missing = [name for name,item in value["entity"].items() if item is None]
    row = {"rowIndex":index, "view":view, "window":window,
        **value, "identityQualified":not missing,
        "missingIdentityFields":missing}
    row["id"] = digest([SCHEMA,source_ref,window,view,row["date"],row["entity"]])
    return row


class _Prepared:
    def __init__(self, db, manifest):
        self._db, self._manifest = db, canonical(manifest)
        self._active, self._cursors = True, set()

    @property
    def manifest(self):
        _need(self._active, "v4推广词链已离开临时生命周期")
        return json.loads(self._manifest)

    def _records(self, view):
        header = self.manifest
        _need(view in VIEWS)
        source_ref, window = header["sourceRef"], header["window"]
        cursor = self._db.execute("SELECT payload FROM groups WHERE view=? "
            "ORDER BY key COLLATE BINARY", (view,))
        self._cursors.add(cursor)
        try:
            index = 0
            while True:
                _need(self._active, "v4推广分组扫描已关闭")
                record = cursor.fetchone()
                if record is None:
                    break
                yield _render(view, record[0], index, source_ref, window)
                index += 1
        finally:
            if cursor in self._cursors:
                self._cursors.remove(cursor)
                cursor.close()

    def scan(self, view):
        return self._records(view)

    def ndjson_chunks(self, view):
        spec = next(item for item in self.manifest["tables"] if item["view"] == view)
        def stream():
            sha, size, chunks, pending = hashlib.sha256(), 0, 0, bytearray()
            for row in self._records(view):
                raw = (canonical(row)+"\n").encode("utf-8")
                _need(len(raw) <= MAX_CHUNK_BYTES)
                if pending and len(pending)+len(raw) > MAX_CHUNK_BYTES:
                    part = bytes(pending); sha.update(part); size += len(part)
                    chunks += 1; pending = bytearray()
                    yield part
                pending.extend(raw)
            part = bytes(pending); sha.update(part); size += len(part); chunks += 1
            yield part
            _need((size,sha.hexdigest(),chunks) ==
                (spec["ndjsonBytes"],spec["ndjsonSha256"],spec["pageCount"]),
                "v4推广分组输出与完整证明不同")
        return stream()

    def _close(self):
        self._active = False
        for cursor in tuple(self._cursors):
            cursor.close()
        self._cursors.clear()


@contextmanager
def prepare(source, pages, replay_proof, *, max_scratch_bytes=None,
            max_output_bytes=None, checkpoint=None):
    """One exact source window; current/previous/yearAgo run separately."""
    source, replay_proof, query, periods = _source(source, replay_proof)
    window = query["window"]
    scratch = MAX_SCRATCH_BYTES if max_scratch_bytes is None else max_scratch_bytes
    output = MAX_OUTPUT_BYTES if max_output_bytes is None else max_output_bytes
    _need(type(scratch) is int and 0 < scratch <= MAX_SCRATCH_BYTES
        and type(output) is int and 0 < output <= MAX_OUTPUT_BYTES,
        "v4临时空间与输出上限只能收紧")
    check = Checkpoint.wrap(checkpoint)
    with TemporaryDirectory(prefix="teruisi-promotion-relation-v4-") as folder:
        db, prepared = None, None
        try:
            db = sqlite3.connect(str(Path(folder)/"relation.sqlite"))
            db.execute("PRAGMA journal_mode=OFF")
            db.execute("PRAGMA synchronous=OFF")
            db.execute("PRAGMA cache_size=-2048")
            db.execute("PRAGMA temp_store=FILE")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            _need(scratch >= page_size)
            applied = db.execute(f"PRAGMA max_page_count={scratch // page_size}").fetchone()[0]
            _need(applied * page_size <= scratch,
                "v4主SQLite文件页预算未生效")
            if check is not None:
                check.attach(db,"promotion_relation_v4_sqlite")
            db.execute("CREATE TABLE seen_ids(row_id TEXT PRIMARY KEY) WITHOUT ROWID")
            db.execute("CREATE TABLE seen_hashes(source_hash TEXT PRIMARY KEY) WITHOUT ROWID")
            db.execute("CREATE TABLE groups(view TEXT,key TEXT,payload TEXT,"
                "PRIMARY KEY(view,key)) WITHOUT ROWID")
            verifier, observed, group_counts = PageReconciler(), set(), {view:0 for view in VIEWS}
            count, size, first = 0, 0, None
            filters = {key:query[key] for key in ("platform","shop","dataset","window")}
            filters["periods"] = periods
            for page in pages:
                count += 1
                if check is not None and count % 20 == 1:
                    check({"stage":"promotion_relation_v4","phase":"source_page",
                        "page":count})
                page = native._copy(page,evidence_v4.MAX_DAILY_PAGE_BYTES)
                _need(type(page) is dict)
                raw = canonical(page)
                page_size_bytes = len(raw.encode("utf-8"))
                size += page_size_bytes
                _need(count <= evidence_v4.MAX_SOURCE_PAGES
                    and page_size_bytes <= evidence_v4.MAX_DAILY_PAGE_BYTES
                    and size <= evidence_v4.MAX_SOURCE_BYTES,
                    "v4推广完整来源超过单页/单源容量，禁止截断")
                _need(page.get("schemaVersion") == native.SOURCE_SCHEMA
                    and page.get("source") == "jd_promotion"
                    and page.get("sourceDataset") == "ad"
                    and page.get("sourceRef") == replay_proof["sourceRef"]
                    and page.get("sourceRevision") == replay_proof["sourceRevision"]
                    and page.get("monetaryUnit") == "CNY_CENT"
                    and page.get("filters") == filters
                    and type(page.get("pagination")) is dict
                    and page["pagination"].get("limit") == 100
                    and type(page.get("items")) is list
                    and len(page["items"]) <= 100,
                    "v4推广来源窗口、修订或真实分页不一致")
                fixed = {key:page.get(key) for key in
                    ("sourceRevision","filters","metricSemantics","consistency")}
                if first is None:
                    first = fixed
                    _need(type(page.get("control")) is dict
                        and type(page["control"].get("rowCount")) is int
                        and page["control"]["rowCount"] == replay_proof["rowCount"]
                        and type(page["control"].get("typedTotals")) is dict
                        and set(page["control"]["typedTotals"]) == native.BASE_METRICS
                        and all(type(value) is int and abs(value) <= MAX_SAFE_INTEGER
                            for value in page["control"]["typedTotals"].values())
                        and type(page.get("metricSemantics")) is dict
                        and type(page.get("coverage")) is dict,
                        "v4推广首页控制汇总或日期覆盖无效")
                    declared_coverage = page["coverage"]
                else:
                    _need(fixed == first and page.get("control") is None
                        and page.get("coverage") is None
                        and page.get("availableDates") is None,
                        "v4推广后续页改变固定元数据")
                # PageReconciler stores metric keys, so reject injected fields
                # before it sees the page rather than after its deepcopy.
                for row in page["items"]:
                    _row_shape(row, query, periods[window])
                verifier.consume(page, request_cursor=verifier.expected_cursor)
                for row in page["items"]:
                    db.execute("INSERT INTO seen_ids VALUES (?)",(row["rowId"],))
                    db.execute("INSERT INTO seen_hashes VALUES (?)",(row["sourceRowHash"],))
                    observed.add(row["date"])
                    _need(verifier.rows <= MAX_SOURCE_ROWS)
                    for view in VIEWS:
                        _group(db,view,row,group_counts)
                db.commit()
            proof = verifier.result()
            _need(count == replay_proof["pageCount"]
                and size == replay_proof["storedBytes"]
                and proof == replay_proof["reconciliation"]
                and replay_proof["coverage"] == declared_coverage
                and declared_coverage == coverage(periods[window],observed)
                and set(proof["metrics"]) ==
                    (native.METRICS if proof["rowCount"] else native.BASE_METRICS),
                "v4推广页链、覆盖或源控制总额未与完整重放证明一致")
            specs, total_output = [], 0
            for view in VIEWS:
                totals, facts, groups, sha, bytes_count, pages_count, pending = (
                    _cells(proof["metrics"]),0,0,hashlib.sha256(),0,0,bytearray())
                for (payload,) in db.execute("SELECT payload FROM groups WHERE view=? "
                        "ORDER BY key COLLATE BINARY",(view,)):
                    row = _render(view,payload,groups,replay_proof["sourceRef"],window)
                    encoded = (canonical(row)+"\n").encode("utf-8")
                    _need(len(encoded) <= MAX_CHUNK_BYTES)
                    if pending and len(pending)+len(encoded) > MAX_CHUNK_BYTES:
                        sha.update(pending); pages_count += 1; pending = bytearray()
                    pending.extend(encoded)
                    bytes_count += len(encoded); total_output += len(encoded)
                    _need(total_output <= output,
                        "v4推广完整关系输出超过容量，禁止截断")
                    groups += 1; facts += row["sourceFactCount"]
                    _merge(totals,row["metrics"])
                sha.update(pending); pages_count += 1
                _need(facts == proof["rowCount"] and totals == proof["metrics"],
                    "v4推广三种SKU关系视图未逐项守恒")
                specs.append({"view":view,"groupCount":groups,"sourceFactCount":facts,
                    "metrics":totals,"ndjsonBytes":bytes_count,
                    "ndjsonSha256":sha.hexdigest(),"pageCount":pages_count})
            manifest = {"schemaVersion":SCHEMA,"algorithmVersion":ALGORITHM,
                "runId":replay_proof["runId"],
                "sourceId":replay_proof["sourceId"],
                "runVersion":replay_proof["runVersion"],
                "sourceVersion":replay_proof["sourceVersion"],
                "sourceKey":source["key"],"queryDigest":digest(query),
                "window":window,"period":periods[window],
                "sourceRef":replay_proof["sourceRef"],
                "sourceRevision":replay_proof["sourceRevision"],
                "replayProofDigest":replay_proof["proofDigest"],
                "sourceRowCount":proof["rowCount"],"sourcePageCount":count,
                "sourceBytes":size,"coverage":declared_coverage,
                "tables":specs,"outputBytes":total_output,
                "viewsAreNonAdditive":True,"missingIdentitiesRemainBuckets":True,
                "promotedSkuIsNotAttributedSku":True,
                "sourceAuthorityVerified":False,"upstreamSignatureVerified":False,
                "requestCursorAuditIndependentlyVerified":False,
                "sealed":False,"reportGenerationSupported":False,
                "registeredAgentTool":False,
                "registeredRenderer":False,
                "limitations":["本期/环比/同比各自仅能读取本窗口完整来源，不混页。",
                    "推广SKU、触发SKU、跟单SKU保持源角色；缺词与缺SKU原金额留桶。",
                    "完整关系、推广SKU、跟单SKU是同一事实的三个切面，金额不可相加。",
                    "v4重放证明与内部工具收据不是京东上游独立签名；正式封存和报告未启用。",
                    "4GiB仅约束主SQLite文件；操作系统排序临时文件不受该PRAGMA约束，仍需真实磁盘压测。",
                    "575095行仅在v4理论容量内，真实耗时、磁盘与输出体积尚未验收。"]}
            manifest["manifestDigest"] = digest(manifest)
            _need(len(canonical(manifest).encode("utf-8")) <= MAX_MANIFEST_BYTES,
                "v4推广关系证明超过容量")
            prepared = _Prepared(db,manifest)
            if check is not None:
                check({"stage":"promotion_relation_v4","phase":"ready"})
            yield prepared
            if check is not None:
                check({"stage":"promotion_relation_v4","phase":"complete"})
        except sqlite3.DatabaseError as error:
            if check is not None: check.raise_if_failed()
            raise AnalysisContractError(
                "v4推广源行/内容重复或临时SQLite超限，禁止截断") from error
        finally:
            if prepared is not None:
                prepared._close()
            if db is not None:
                db.set_progress_handler(None,0)
                db.close()
