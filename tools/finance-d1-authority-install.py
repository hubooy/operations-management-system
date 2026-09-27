#!/usr/bin/env python3
"""Install the operator-only finance authority guard with an online D1 backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


FORMAT_VERSION = "finance-d1-authority-install-v1"
TRIGGER_RE = re.compile(
    r"CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+`([^`]+)`", re.IGNORECASE
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM pragma_table_info(?)", (table,)
        )
    }


def scalar(connection: sqlite3.Connection, statement: str, parameters=()) -> int:
    row = connection.execute(statement, parameters).fetchone()
    return int(row[0] if row else 0)


def preflight(connection: sqlite3.Connection) -> dict[str, int]:
    required = {
        "finance_import_batches": {"status"},
        "finance_months": {"status"},
        "finance_lines": set(),
        "finance_targets_scoped": set(),
        "import_content_attempts": {"domain", "outcome"},
        "import_scope_heads": {"domain", "status", "owner_token"},
    }
    missing = sorted(table for table in required if not table_exists(connection, table))
    malformed = sorted(
        table
        for table, columns in required.items()
        if table not in missing and not columns.issubset(table_columns(connection, table))
    )
    if missing or malformed:
        raise RuntimeError("finance D1 schema is incomplete")
    counts = {
        "processingBatches": scalar(
            connection,
            "SELECT COUNT(*) FROM finance_import_batches WHERE status='processing'",
        ),
        "nonCompletedMonths": scalar(
            connection,
            "SELECT COUNT(*) FROM finance_months WHERE status<>'completed'",
        ),
        "processingAttempts": scalar(
            connection,
            "SELECT COUNT(*) FROM import_content_attempts "
            "WHERE domain='finance' AND outcome='processing'",
        ),
        "nonReadyHeads": scalar(
            connection,
            "SELECT COUNT(*) FROM import_scope_heads "
            "WHERE domain='finance' AND "
            "(COALESCE(status, '')<>'ready' OR COALESCE(owner_token, '')<>'')",
        ),
    }
    if any(counts.values()):
        raise RuntimeError("finance D1 has in-flight or non-terminal writes")
    return counts


def authority(connection: sqlite3.Connection) -> dict[str, object] | None:
    if not table_exists(connection, "finance_write_authority"):
        return None
    row = connection.execute(
        "SELECT id, owner, epoch, cutover_id FROM finance_write_authority"
    ).fetchall()
    if len(row) != 1 or int(row[0][0]) != 1:
        raise RuntimeError("finance authority singleton is invalid")
    owner = str(row[0][1])
    epoch = int(row[0][2])
    cutover_id = str(row[0][3])
    if owner not in {"d1", "pending", "postgresql"} or epoch < 1:
        raise RuntimeError("finance authority state is invalid")
    return {"owner": owner, "epoch": epoch, "cutoverId": cutover_id}


def backup_database(connection: sqlite3.Connection, target: Path) -> None:
    if target.exists():
        raise RuntimeError("backup path already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        destination = sqlite3.connect(temporary)
        try:
            connection.backup(destination)
            check = destination.execute("PRAGMA integrity_check").fetchone()
            if check is None or str(check[0]).lower() != "ok":
                raise RuntimeError("D1 backup integrity check failed")
        finally:
            destination.close()
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def install(source: Path, sql_path: Path, backup: Path, receipt: Path) -> dict[str, object]:
    if not source.is_file() or source.suffix.lower() != ".sqlite":
        raise RuntimeError("source must be an existing absolute .sqlite file")
    if not sql_path.is_file():
        raise RuntimeError("authority SQL is missing")
    if backup == source or receipt in {source, backup}:
        raise RuntimeError("source, backup and receipt paths must be distinct")
    if receipt.exists():
        raise RuntimeError("receipt path already exists")
    sql_text = sql_path.read_text(encoding="utf-8")
    expected_triggers = sorted(set(TRIGGER_RE.findall(sql_text)))
    if len(expected_triggers) < 10:
        raise RuntimeError("authority SQL trigger contract is incomplete")

    connection = sqlite3.connect(source, timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        before_counts = preflight(connection)
        before_authority = authority(connection)
        if before_authority is not None and before_authority["owner"] != "d1":
            raise RuntimeError("finance authority is no longer owned by D1")
        backup_database(connection, backup)
        connection.execute("BEGIN IMMEDIATE")
        try:
            preflight(connection)
            for raw_statement in sql_text.split("--> statement-breakpoint"):
                statement = raw_statement.strip()
                if statement:
                    connection.execute(statement)
            after_authority = authority(connection)
            if after_authority != {"owner": "d1", "epoch": 1, "cutoverId": ""}:
                if before_authority is None or after_authority != before_authority:
                    raise RuntimeError("finance authority install changed an existing state")
            installed_triggers = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE 'finance_authority_%'"
                )
            }
            if set(expected_triggers) != installed_triggers:
                raise RuntimeError("finance authority trigger set is incomplete or unexpected")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    finally:
        connection.close()

    payload: dict[str, object] = {
        "formatVersion": FORMAT_VERSION,
        "status": "installed",
        "installedAt": datetime.now(timezone.utc).isoformat(),
        "sourcePathSha256": hashlib.sha256(str(source).encode("utf-8")).hexdigest(),
        "sqlSha256": sha256_file(sql_path),
        "backupPath": str(backup),
        "backupSha256": sha256_file(backup),
        "preflight": before_counts,
        "authority": after_authority,
        "triggerCount": len(expected_triggers),
    }
    atomic_json(receipt, payload)
    payload["receiptPath"] = str(receipt)
    payload["receiptSha256"] = sha256_file(receipt)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--sql", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = install(
            args.source.resolve(),
            args.sql.resolve(),
            args.backup.resolve(),
            args.receipt.resolve(),
        )
    except Exception as error:
        print(
            json.dumps(
                {"status": "failed", "error": type(error).__name__},
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
