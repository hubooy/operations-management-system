#!/usr/bin/env python3
"""Create a finance-only, immutable D1 rehearsal snapshot.

The source is always opened read-only.  Only finance rows are copied from the
shared import-control tables, then the operator-only authority DDL is installed
in the new snapshot.  The source is re-read before publication so a concurrent
finance change cannot produce an approved but stale migration material.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator


FORMAT_VERSION = "finance-d1-rehearsal-snapshot-v1"
MAX_ROWS = 500_000
DOMAIN_TABLES = (
    "finance_import_batches",
    "finance_months",
    "finance_lines",
    "finance_targets",
    "finance_target_versions",
    "finance_targets_scoped",
    "finance_target_scoped_versions",
    "finance_target_deletion_audits",
    "finance_target_scoped_deletion_audits",
    "finance_target_legacy_migrations",
)
SHARED_TABLES = (
    "import_content_fingerprints",
    "import_content_attempts",
    "import_scope_heads",
)
ALL_TABLES = (*DOMAIN_TABLES, *SHARED_TABLES)


class SnapshotError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _open_source(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({_quote(table)})")]


def _order_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    info = list(connection.execute(f"PRAGMA table_info({_quote(table)})"))
    primary = [str(row[1]) for row in sorted(info, key=lambda item: int(item[5])) if int(row[5])]
    if primary:
        return primary
    columns = [str(row[1]) for row in info]
    if "sequence" in columns:
        return ["sequence"]
    if "id" in columns:
        return ["id"]
    return columns


def _rows(
    connection: sqlite3.Connection, table: str
) -> Iterator[tuple[object, ...]]:
    columns = _table_columns(connection, table)
    select = ",".join(_quote(column) for column in columns)
    order = ",".join(_quote(column) + " COLLATE BINARY" for column in _order_columns(connection, table))
    where = " WHERE domain=?" if table in SHARED_TABLES else ""
    parameters: tuple[object, ...] = ("finance",) if table in SHARED_TABLES else ()
    cursor = connection.execute(
        f"SELECT {select} FROM {_quote(table)}{where} ORDER BY {order}", parameters
    )
    while True:
        batch = cursor.fetchmany(1_000)
        if not batch:
            return
        for row in batch:
            values = tuple(row)
            if any(isinstance(value, (bytes, bytearray, memoryview)) for value in values):
                raise SnapshotError("财务迁移材料包含不支持的二进制字段。")
            yield values


def _schema(connection: sqlite3.Connection, table: str) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row is None or not str(row[0] or "").lstrip().upper().startswith("CREATE TABLE"):
        raise SnapshotError(f"D1 源缺少财务表 {table}。")
    return str(row[0])


def _capture(
    source: sqlite3.Connection,
    target: sqlite3.Connection | None = None,
) -> tuple[str, dict[str, int]]:
    digest = hashlib.sha256()
    counts: dict[str, int] = {}
    total = 0
    for table in ALL_TABLES:
        schema = _schema(source, table)
        columns = _table_columns(source, table)
        if target is not None:
            target.execute(schema)
        digest.update(json.dumps([table, schema, columns], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        placeholders = ",".join("?" for _ in columns)
        insert = f"INSERT INTO {_quote(table)} ({','.join(_quote(item) for item in columns)}) VALUES ({placeholders})"
        count = 0
        for row in _rows(source, table):
            digest.update(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
            digest.update(b"\n")
            if target is not None:
                target.execute(insert, row)
            count += 1
            total += 1
            if total > MAX_ROWS:
                raise SnapshotError("财务迁移副本超过受控行数上限。")
        counts[table] = count
    return digest.hexdigest(), counts


def _copy_indexes_and_triggers(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    placeholders = ",".join("?" for _ in ALL_TABLES)
    rows = source.execute(
        f"SELECT type, name, sql FROM sqlite_master "
        f"WHERE type IN ('index','trigger') AND tbl_name IN ({placeholders}) "
        "AND sql IS NOT NULL ORDER BY type, name COLLATE BINARY",
        ALL_TABLES,
    )
    for kind, name, sql in rows:
        if kind == "trigger" and str(name).startswith("finance_authority_"):
            continue
        target.execute(str(sql))


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)


def create_snapshot(
    source_path: Path,
    output_path: Path,
    authority_sql_path: Path,
    manifest_path: Path,
) -> dict[str, object]:
    source_path = source_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    authority_sql_path = authority_sql_path.expanduser().resolve()
    manifest_path = manifest_path.expanduser().resolve()
    if not source_path.is_file() or source_path.suffix.lower() != ".sqlite":
        raise SnapshotError("D1 源必须是存在的绝对 .sqlite 文件。")
    if not authority_sql_path.is_file():
        raise SnapshotError("财务 authority SQL 不存在。")
    if output_path == source_path or output_path.exists() or manifest_path.exists():
        raise SnapshotError("输出必须是尚不存在且不同于源文件的新路径。")
    if output_path.suffix.lower() != ".sqlite" or manifest_path.suffix.lower() != ".json":
        raise SnapshotError("输出扩展名必须分别为 .sqlite 和 .json。")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    authority_sql = authority_sql_path.read_text(encoding="utf-8")
    source = _open_source(source_path)
    target: sqlite3.Connection | None = None
    try:
        target = sqlite3.connect(output_path, timeout=30, isolation_level=None)
        target.execute("PRAGMA journal_mode=DELETE")
        target.execute("PRAGMA foreign_keys=OFF")
        target.execute("BEGIN IMMEDIATE")
        source_digest, counts = _capture(source, target)
        _copy_indexes_and_triggers(source, target)
        for statement in authority_sql.split("--> statement-breakpoint"):
            if statement.strip():
                target.execute(statement)
        foreign_key_errors = list(target.execute("PRAGMA foreign_key_check"))
        if foreign_key_errors:
            raise SnapshotError("财务演练副本存在外键不一致。")
        target.commit()
        target.close()
        target = None
    except Exception:
        if target is not None:
            target.rollback()
            target.close()
        if output_path.exists() and output_path.is_file():
            output_path.unlink()
        raise
    finally:
        source.rollback()
        source.close()

    current = _open_source(source_path)
    try:
        current_digest, current_counts = _capture(current)
    finally:
        current.rollback()
        current.close()
    if current_digest != source_digest or current_counts != counts:
        output_path.unlink()
        raise SnapshotError("D1 财务材料在副本发布前发生变化。")

    verification = _open_source(output_path)
    try:
        output_digest, output_counts = _capture(verification)
        authority = verification.execute(
            "SELECT owner, epoch, cutover_id FROM finance_write_authority WHERE id=1"
        ).fetchone()
    finally:
        verification.rollback()
        verification.close()
    if output_digest != source_digest or output_counts != counts:
        output_path.unlink()
        raise SnapshotError("财务演练副本与只读源的业务投影不一致。")
    if authority is None or tuple(authority) != ("d1", 1, ""):
        output_path.unlink()
        raise SnapshotError("财务演练副本 authority 初始化失败。")

    result: dict[str, object] = {
        "formatVersion": FORMAT_VERSION,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourcePathSha256": _sha256_text(str(source_path)),
        "sourceFinanceDigest": source_digest,
        "authoritySqlSha256": _sha256_file(authority_sql_path),
        "outputSha256": _sha256_file(output_path),
        "counts": counts,
        "authority": {"owner": "d1", "epoch": 1, "cutoverId": ""},
    }
    try:
        _write_manifest(manifest_path, result)
    except Exception:
        if output_path.exists() and output_path.is_file():
            output_path.unlink()
        raise
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--authority-sql", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    output = args.output.expanduser().resolve()
    manifest = args.manifest.expanduser().resolve()
    try:
        result = create_snapshot(args.source, output, args.authority_sql, manifest)
    except Exception as error:
        for path in (output, manifest):
            try:
                if path.exists() and path.is_file():
                    path.unlink()
            except OSError:
                pass
        print(json.dumps({"status": "failed", "error": type(error).__name__}, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps({"status": "succeeded", **result}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
