"""Disposable derived grouping scratch; PostgreSQL evidence remains authoritative.

Only a page of groups is resident in Python. SQLite is a private temporary sort
and merge file, never a source, durable checkpoint or alternate application DB.
All SQL is fixed application code and every result is reconciled before reading.
"""
from collections import defaultdict
import json
import sqlite3
from tempfile import TemporaryDirectory

from .aggregation import DimensionAccumulator, group_result
from .contracts import AnalysisContractError, canonical

MAX_RESULT_GROUPS = 250000
MAX_SCRATCH_BYTES = 256 * 1024 * 1024


class Checkpoint:
    """Explicit cooperative cancellation, including SQLite's swallowed errors.

    Return values confer no authority. Only an exception stops work; the same
    exception is restored after SQLite reports its generic interrupted error.
    """
    def __init__(self, callback):
        if not callable(callback): raise AnalysisContractError("checkpoint须为内部可调用对象")
        self.callback, self.error = callback, None

    @classmethod
    def wrap(cls, callback):
        return callback if callback is None or type(callback) is cls else cls(callback)

    def __call__(self, event):
        self.raise_if_failed()
        try:
            self.callback(dict(event))
        except BaseException as error:
            self.error = error
            raise

    def raise_if_failed(self):
        if self.error is not None: raise self.error

    def attach(self, db, stage):
        def progress():
            try: self({"stage":stage,"phase":"sqlite"})
            except BaseException: return 1
            return 0
        db.set_progress_handler(progress,10000)


class PartitionedGroups:
    def __init__(self, *, checkpoint=None):
        self.checkpoint = Checkpoint.wrap(checkpoint)
        self._cursors = set()

    def __enter__(self):
        self.directory = TemporaryDirectory(prefix="teruisi-analysis-")
        try:
            self.db = sqlite3.connect(self.directory.name + "/groups.sqlite")
            if self.checkpoint is not None: self.checkpoint.attach(self.db,"native_sqlite")
            self.db.execute("PRAGMA cache_size=-2048")
            self.db.execute("PRAGMA temp_store=FILE")
            page_size = self.db.execute("PRAGMA page_size").fetchone()[0]
            self.db.execute(f"PRAGMA max_page_count={MAX_SCRATCH_BYTES // page_size}")
            self.db.execute("CREATE TABLE groups(side INTEGER, entity TEXT COLLATE BINARY, payload TEXT NOT NULL, PRIMARY KEY(side, entity)) WITHOUT ROWID")
            self.dimensions, self.metrics, self.verified, self.counts = {}, {}, set(), {}
            return self
        except BaseException:
            if hasattr(self, "db"):
                self.db.set_progress_handler(None,0)
                self.db.close()
            self.directory.cleanup()
            if self.checkpoint is not None: self.checkpoint.raise_if_failed()
            raise

    def __exit__(self, error_type, error, traceback):
        try:
            if self.checkpoint is not None: self.db.set_progress_handler(None,0)
            for cursor in self._cursors: cursor.close()
            self._cursors.clear()
            self.db.close()
        finally:
            self.directory.cleanup()
        if self.checkpoint is not None: self.checkpoint.raise_if_failed()
        if isinstance(error, sqlite3.DatabaseError):
            raise AnalysisContractError("分区临时计算空间不可用或已达到容量") from error

    def configure(self, side, dimensions, metrics):
        DimensionAccumulator(dimensions, metrics)
        if side not in (0, 1) or side in self.dimensions:
            raise AnalysisContractError("分区来源配置无效")
        self.dimensions[side], self.metrics[side] = dimensions, metrics
        self.counts[side] = 0

    def consume(self, side, records):
        if side in self.verified:
            raise AnalysisContractError("核对后禁止修改分组")
        dimensions, metrics = self.dimensions[side], self.metrics[side]
        count = self.counts[side]
        # Roll back the entire source page even if its last partition is invalid.
        try:
            with self.db:
                for start in range(0, len(records), 100):
                    if self.checkpoint is not None: self.checkpoint({"stage":"native_aggregate","side":side,"rowOffset":start})
                    rows = records[start:start+100]
                    accumulator = DimensionAccumulator(dimensions, metrics)
                    seen = set()
                    for row in rows:
                        entity = {"platform": row["platform"], "shopName": row["shopName"],
                            **{d: row.get(d) if d in row else row.get("dimensions", {}).get(d) for d in dimensions}}
                        key = (row["platform"], row["shopName"], *(entity[d] for d in dimensions))
                        if key in seen:
                            continue
                        seen.add(key)
                        saved = self.db.execute("SELECT payload FROM groups WHERE side=? AND entity=?", (side, canonical(entity))).fetchone()
                        if saved:
                            group = json.loads(saved[0])
                            group["sums"], group["present"] = defaultdict(int, group["sums"]), defaultdict(int, group["present"])
                            accumulator.groups[key] = group
                        else:
                            count += 1
                    accumulator.consume(rows)
                    self.db.executemany("INSERT INTO groups VALUES (?,?,?) ON CONFLICT(side,entity) DO UPDATE SET payload=excluded.payload",
                        ((side, canonical(group["entity"]), canonical(group)) for group in accumulator.groups.values()))
                if count > MAX_RESULT_GROUPS:
                    raise AnalysisContractError("分区分组超过容量，禁止截断后声称全量")
            self.counts[side] = count
        except sqlite3.DatabaseError as error:
            if self.checkpoint is not None: self.checkpoint.raise_if_failed()
            raise AnalysisContractError("分区临时计算空间不可用或已达到容量") from error

    def verify(self, side, expected):
        total, sums, present = 0, defaultdict(int), defaultdict(int)
        cursor = self.db.execute("SELECT payload FROM groups WHERE side=?", (side,))
        self._cursors.add(cursor)
        for index,(payload,) in enumerate(cursor):
            if self.checkpoint is not None and index % 100 == 0:
                self.checkpoint({"stage":"native_verify","side":side,"rowOffset":index})
            group = json.loads(payload)
            total += group["rowCount"]
            for metric in self.metrics[side]:
                sums[metric] += group["sums"].get(metric, 0)
                present[metric] += group["present"].get(metric, 0)
        self._cursors.remove(cursor); cursor.close()
        if expected.get("reconciled") is not True or total != expected["rowCount"]:
            raise AnalysisContractError("分区行数与源核对记录不一致")
        for metric in self.metrics[side]:
            source = expected["metrics"].get(metric)
            if source is None or source["presentRows"] != present[metric] or (source["value"] or 0) != sums[metric]:
                raise AnalysisContractError("分区金额或缺失数与源核对记录不一致")
        self.verified.add(side)

    def page(self, offset, limit):
        if self.verified != set(self.dimensions):
            raise AnalysisContractError("分区未完成核对")
        total = self.db.execute("SELECT count(*) FROM (SELECT entity FROM groups GROUP BY entity)").fetchone()[0]
        if total > MAX_RESULT_GROUPS:
            raise AnalysisContractError("比较分组并集超过容量，禁止截断")
        # Binary UTF-8 ordering equals Python Unicode ordering for canonical JSON.
        keys = self.db.execute("SELECT entity FROM groups GROUP BY entity ORDER BY entity COLLATE BINARY LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        result = []
        for (key,) in keys:
            pair = []
            for side in (0, 1):
                record = self.db.execute("SELECT payload FROM groups WHERE side=? AND entity=?", (side, key)).fetchone()
                pair.append(group_result(json.loads(record[0]), self.metrics[side]) if record else None)
            result.append(pair)
        return total, result

    def scan(self):
        """One sorted pass for full-file exports, without repeated OFFSET scans."""
        if self.verified != set(self.dimensions):
            raise AnalysisContractError("分区未完成核对")
        total = self.db.execute("SELECT count(*) FROM (SELECT entity FROM groups GROUP BY entity)").fetchone()[0]
        if total > MAX_RESULT_GROUPS:
            raise AnalysisContractError("比较分组并集超过容量，禁止截断")
        def rows():
            cursor = self.db.execute("SELECT entity FROM groups GROUP BY entity ORDER BY entity COLLATE BINARY")
            self._cursors.add(cursor)
            try:
                for index,(key,) in enumerate(cursor):
                    if self.checkpoint is not None and index % 100 == 0:
                        self.checkpoint({"stage":"native_scan","rowOffset":index})
                    pair = []
                    for side in (0, 1):
                        record = self.db.execute("SELECT payload FROM groups WHERE side=? AND entity=?", (side, key)).fetchone()
                        pair.append(group_result(json.loads(record[0]), self.metrics[side]) if record else None)
                    yield pair
            finally:
                if cursor in self._cursors: self._cursors.remove(cursor); cursor.close()
        return total, rows()
