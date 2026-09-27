"""Disposable disk partitions for exact, fully reconciled product mapping.

This module is not a persistent cache or an authority for source identity. Its
caller supplies trusted sealed pages/expected proofs. Nothing is exposed until
both streams and the complete derived totals have passed verification.
"""
from collections import defaultdict
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, PageReconciler, canonical, digest
from .partitioned import Checkpoint

SCHEMA_VERSION = "business-product-mapping-v2"
ALGORITHM_VERSION = "exact-product-partition-v1"
MAX_SCRATCH_BYTES = 256 * 1024 * 1024
MAX_RESULT_GROUPS = 250000
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_PAGES = 2000
MAX_PAGE_BYTES = 128 * 1024
MAX_RESULT_BYTES = 38000
MAX_METRICS = 128
LIMITATIONS = ["歧义和未匹配金额保留独立组，不复制给任何SKU", "当前主数据不是历史主数据",
    "未分摊关键词利润", "不是广告成交与ERP销售口径一致的证明"]


def _require(value, message):
    if not value:
        raise AnalysisContractError(message)


def _json(value):
    try:
        encoded = canonical(value)
        return encoded, len(encoded.encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("分区关联数据无法完整编码") from error


class _Result:
    def __init__(self, db, checkpoint=None):
        self._db, self._active, self._summary = db, False, None
        self._checkpoint = checkpoint
        self._source_pages, self._source_bytes = 0, 0
        self._groups = 0
        self._source_stats = {}
        self._cursors = set()

    def _available(self):
        _require(self._active, "关联结果尚未完整核对或已离开临时分区生命周期")

    def summary(self):
        self._available()
        return json.loads(self._summary)

    def stats(self):
        self._available()
        return {"sourcePages": self._source_pages, "sourceBytes": self._source_bytes,
            "sources": json.loads(canonical(self._source_stats)), "groupCount": self._groups,
            "scratchLimitBytes": self._db.execute("PRAGMA max_page_count").fetchone()[0]
                * self._db.execute("PRAGMA page_size").fetchone()[0],
            "scratchBytes": self._db.execute("PRAGMA page_count").fetchone()[0]
                * self._db.execute("PRAGMA page_size").fetchone()[0]}

    def _row(self, key, payload, index):
        return {**json.loads(payload), "rowIndex": index,
            "id": digest([json.loads(self._summary)["resultDigest"], key])}

    def _page(self, rows, offset, limit=20):
        end = offset + len(rows)
        value = {**json.loads(self._summary), "rows": rows,
            "pagination": {"offset": offset, "limit": limit, "total": self._groups,
                "hasMore": end < self._groups, "nextOffset": end if end < self._groups else None}}
        value["pageDigest"] = digest(value)
        return value

    def page(self, offset=0, limit=20):
        self._available()
        _require(type(offset) is int and 0 <= offset <= self._groups
            and type(limit) is int and 1 <= limit <= 100, "关联分页参数无效")
        rows = []
        value = self._page(rows, offset, limit)
        for key, payload in self._db.execute("SELECT identity,payload FROM groups ORDER BY identity COLLATE BINARY LIMIT ? OFFSET ?", (limit, offset)):
            rows.append(self._row(key, payload, offset+len(rows)))
            candidate = self._page(rows, offset, limit)
            if _json(candidate)[1] > MAX_RESULT_BYTES:
                rows.pop()
                _require(bool(rows), "单个完整关联行超过分页字节容量")
                break
            value = candidate
        value["pageDigest"] = digest({k: v for k, v in value.items() if k != "pageDigest"})
        _require(_json(value)[1] <= MAX_RESULT_BYTES, "关联分页超过字节容量")
        return value

    def scan(self):
        self._available()
        count = 0
        cursor = self._db.execute("SELECT identity,payload FROM groups ORDER BY identity COLLATE BINARY")
        self._cursors.add(cursor)
        try:
            while True:
                self._available()
                if self._checkpoint is not None and count % 100 == 0:
                    self._checkpoint({"stage":"identity_scan","rowOffset":count})
                record = cursor.fetchone()
                if record is None:
                    break
                key, payload = record
                yield self._row(key, payload, count)
                count += 1
            _require(count == self._groups, "完整关联结果扫描数量不一致")
        finally:
            if cursor in self._cursors:
                self._cursors.remove(cursor)
                cursor.close()

    def _read(self, pages, kind, consume, expected):
        verifier, scope, count = PageReconciler(), None, 0
        for page in pages:
            if self._checkpoint is not None: self._checkpoint({"stage":"identity_source_page","sourceKind":kind,"sourcePage":count+1})
            _require(type(page) is dict and type(page.get("items")) is list and len(page["items"]) <= 100,
                "来源页超过固定行容量")
            size = _json(page)[1]
            self._source_pages += 1; self._source_bytes += size; count += 1
            _require(size <= MAX_PAGE_BYTES and self._source_bytes <= MAX_SOURCE_BYTES
                and self._source_pages <= MAX_SOURCE_PAGES, "关联来源超过固定证据容量")
            _require((kind == "sales" and page.get("source") == "erp_sales")
                or (kind == "master" and page.get("sourceDataset") == "product_master"), "商品关联来源类型不匹配")
            current = (page["filters"]["platform"], page["filters"]["shop"])
            _require(scope is None or current == scope, "来源店铺身份变化")
            scope = current
            _require(all((item["platform"], item["shopName"]) == scope for item in page["items"]), "来源页包含其他店铺")
            metric_keys = set(verifier.totals) | set((page.get("control") or {}).get("typedTotals", {}))
            for item in page["items"]:
                metric_keys.update(item["metrics"])
            _require(len(metric_keys) <= MAX_METRICS, "关联指标目录超过有界容量")
            verifier.consume(page, request_cursor=verifier.expected_cursor)
            consume(page["items"])
            self._db.commit()
        proof = verifier.result()
        _require(expected is None or canonical(expected) == canonical(proof), "关联来源与可信封存核对记录不一致")
        self._source_stats[kind] = {"rowCount": proof["rowCount"], "pageCount": count}
        return scope, proof

    def _masters(self, rows):
        for row in rows:
            code = row.get("dimensions", {}).get("merchantCode")
            if not code or not row.get("skuId"):
                continue
            # Two distinct candidates are enough to prove ambiguity. This is
            # never exposed as a count of all candidates or a complete list.
            encoded_code = canonical(code)
            pair = canonical([row["skuId"], row.get("spuId")])
            candidates = self._db.execute("SELECT pair FROM candidates WHERE code=?", (encoded_code,)).fetchall()
            if len(candidates) < 2:
                self._db.execute("INSERT OR IGNORE INTO candidates VALUES (?,?)", (encoded_code, pair))

    def _sales(self, rows):
        for row in rows:
            candidates = self._db.execute("SELECT pair FROM candidates WHERE code=?", (canonical(row.get("onlineSpecCode")),)).fetchall()
            status = "matched" if len(candidates) == 1 else "ambiguous" if candidates else "unmatched"
            sku, spu = json.loads(candidates[0][0]) if status == "matched" else (None, None)
            key = canonical([status, sku, spu])
            saved = self._db.execute("SELECT payload FROM groups WHERE identity=?", (key,)).fetchone()
            if saved:
                group = json.loads(saved[0])
            else:
                self._groups += 1
                _require(self._groups <= MAX_RESULT_GROUPS, "关联分组超过容量，禁止截断")
                group = {"status": status, "skuId": sku, "spuId": spu, "rowCount": 0, "metrics": {}}
            group["rowCount"] += 1
            for metric, value in row["metrics"].items():
                _require(type(value) is int, "销售核对指标缺失或无效")
                group["metrics"][metric] = group["metrics"].get(metric, 0) + value
                _require(abs(group["metrics"][metric]) <= MAX_SAFE_INTEGER, "关联汇总超过无损整数范围")
            self._db.execute("INSERT INTO groups VALUES (?,?) ON CONFLICT(identity) DO UPDATE SET payload=excluded.payload", (key, canonical(group)))

    def _build(self, sales_pages, master_pages, sales_expected, master_expected):
        master_scope, master = self._read(master_pages, "master", self._masters, master_expected)
        sales_scope, sales = self._read(sales_pages, "sales", self._sales, sales_expected)
        _require(master_scope == sales_scope, "销售与主数据店铺不一致")
        sums, counts, rows, count = defaultdict(int), defaultdict(int), 0, 0
        chain = digest([])
        cursor = self._db.execute("SELECT identity,payload FROM groups ORDER BY identity COLLATE BINARY")
        self._cursors.add(cursor)
        for key, payload in cursor:
            if self._checkpoint is not None and count % 100 == 0:
                self._checkpoint({"stage":"identity_verify","rowOffset":count})
            group = json.loads(payload)
            rows += group["rowCount"]; count += 1; counts[group["status"]] += group["rowCount"]
            for metric, value in group["metrics"].items(): sums[metric] += value
            chain = digest([chain, group, count])
        self._cursors.remove(cursor); cursor.close()
        _require(rows == sales["rowCount"] and count == self._groups, "关联行数未通过来源核对")
        _require(not any(value["missingRows"] or sums[key] != (value["value"] or 0)
            for key, value in sales["metrics"].items()), "关联金额未通过源核对")
        summary = {"schemaVersion": SCHEMA_VERSION, "algorithmVersion": ALGORITHM_VERSION,
            "platform": sales_scope[0], "shopName": sales_scope[1], "sources": {"sales": sales, "master": master},
            "rowCount": rows, "groupCount": count, "reconciled": True,
            "mappingBasis": "exact_online_spec_code_to_current_master_merchant_code_not_historical_mapping",
            "totals": dict(sums), "coverage": {status: counts[status] for status in ("matched", "ambiguous", "unmatched")},
            "limitations": LIMITATIONS.copy(), "groupsDigest": chain}
        summary["resultDigest"] = digest(summary)
        self._summary = canonical(summary)
        # Validate every possible singleton response before publishing anything.
        _require(_json(self._page([], 0))[1] <= MAX_RESULT_BYTES, "关联汇总超过分页容量")
        cursor = self._db.execute("SELECT identity,payload FROM groups ORDER BY identity COLLATE BINARY")
        self._cursors.add(cursor)
        for index, (key, payload) in enumerate(cursor):
            if self._checkpoint is not None and index % 100 == 0:
                self._checkpoint({"stage":"identity_rows","rowOffset":index})
            _require(_json(self._page([self._row(key, payload, index)], index, 100))[1] <= MAX_RESULT_BYTES,
                "单个完整关联行超过分页字节容量")
        self._cursors.remove(cursor); cursor.close()
        self._active = True


@contextmanager
def reconcile_products(sales_pages, master_pages, *, sales_expected=None, master_expected=None, max_scratch_bytes=None, checkpoint=None):
    """Yield fully verified results; all scans/pages must stay inside this block."""
    checkpoint = Checkpoint.wrap(checkpoint)
    scratch_limit = MAX_SCRATCH_BYTES if max_scratch_bytes is None else max_scratch_bytes
    _require(type(scratch_limit) is int and 0 < scratch_limit <= MAX_SCRATCH_BYTES,
        "临时关联空间额度无效或超过固定上限")
    result = None
    with TemporaryDirectory(prefix="teruisi-product-mapping-") as directory:
        db = None
        try:
            db = sqlite3.connect(str(Path(directory) / "mapping.sqlite"))
            if checkpoint is not None: checkpoint.attach(db,"identity_sqlite")
            db.execute("PRAGMA journal_mode=OFF")
            db.execute("PRAGMA synchronous=OFF")
            db.execute("PRAGMA cache_size=-2048")
            db.execute("PRAGMA temp_store=FILE")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            _require(scratch_limit >= page_size, "临时关联空间不足")
            db.execute(f"PRAGMA max_page_count={scratch_limit // page_size}")
            db.execute("CREATE TABLE candidates(code TEXT COLLATE BINARY,pair TEXT COLLATE BINARY,PRIMARY KEY(code,pair)) WITHOUT ROWID")
            db.execute("CREATE TABLE groups(identity TEXT COLLATE BINARY PRIMARY KEY,payload TEXT NOT NULL) WITHOUT ROWID")
            result = _Result(db,checkpoint) if checkpoint is not None else _Result(db)
            result._build(sales_pages, master_pages, sales_expected, master_expected)
            yield result
            if checkpoint is not None: checkpoint.raise_if_failed()
        except BaseException as error:
            if checkpoint is not None: checkpoint.raise_if_failed()
            if isinstance(error,(sqlite3.DatabaseError, KeyError, ValueError, TypeError, UnicodeError, RecursionError)):
                raise AnalysisContractError("分区商品关联数据或临时空间无效") from error
            raise
        finally:
            if db is not None and checkpoint is not None: db.set_progress_handler(None,0)
            if result is not None:
                result._active = False
                # sqlite connections retain open statement handles; explicitly
                # release abandoned streaming cursors before Windows cleanup.
                for cursor in result._cursors: cursor.close()
                result._cursors.clear()
            if db is not None: db.close()
