#!/usr/bin/env python3
"""Install the operator-only remaining-workflow D1 authority guard with a verified backup."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile


FORMAT_VERSION = "workflow-operations-d1-authority-install-v1"
TRIGGER_RE = re.compile(r"CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+`([^`]+)`", re.IGNORECASE)
SOURCE_TABLES = (
    "workflow_tasks",
    "workflow_task_bootstrap",
    "workflow_task_states",
    "workflow_task_comments",
    "workflow_task_activity_logs",
    "workflow_task_reminders",
    "workflow_task_templates",
    "workflow_task_template_states",
    "workflow_task_entity_links",
    "workflow_task_attachments",
    "workflow_attachment_cleanup_queue",
    "workflow_operation_records",
    "workflow_operation_activities",
)


def sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
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
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def authority(connection: sqlite3.Connection) -> dict[str, object] | None:
    if not table_exists(connection, "workflow_operations_write_authority"):
        return None
    rows = connection.execute(
        "SELECT id,owner,epoch,cutover_id FROM workflow_operations_write_authority"
    ).fetchall()
    if len(rows) != 1 or int(rows[0][0]) != 1:
        raise RuntimeError("workflow operations authority singleton is invalid")
    result = {"owner": str(rows[0][1]), "epoch": int(rows[0][2]), "cutoverId": str(rows[0][3])}
    if result["owner"] not in {"legacy", "pending", "postgresql"} or result["epoch"] < 1:
        raise RuntimeError("workflow operations authority state is invalid")
    return result


def preflight(connection: sqlite3.Connection) -> dict[str, int]:
    missing = [table for table in SOURCE_TABLES if not table_exists(connection, table)]
    if missing:
        raise RuntimeError("workflow D1 schema is incomplete")
    quick_check = connection.execute("PRAGMA quick_check").fetchone()
    if quick_check is None or str(quick_check[0]).lower() != "ok":
        raise RuntimeError("workflow D1 quick_check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("workflow D1 foreign key check failed")
    task_count = int(connection.execute("SELECT COUNT(*) FROM workflow_tasks").fetchone()[0])
    task_state_count = int(connection.execute("SELECT COUNT(*) FROM workflow_task_states").fetchone()[0])
    template_count = int(connection.execute("SELECT COUNT(*) FROM workflow_task_templates").fetchone()[0])
    template_state_count = int(connection.execute("SELECT COUNT(*) FROM workflow_task_template_states").fetchone()[0])
    if task_count != task_state_count or template_count != template_state_count:
        raise RuntimeError("workflow D1 state tables are incomplete")
    launch_count = int(connection.execute(
        "SELECT COUNT(*) FROM workflow_operation_records WHERE record_type='launch'"
    ).fetchone()[0])
    if launch_count != 0:
        raise RuntimeError("retired workflow launch facts are still present")
    orphan_activities = int(connection.execute(
        "SELECT COUNT(*) FROM workflow_operation_activities a "
        "LEFT JOIN workflow_operation_records r ON r.id=a.record_id WHERE r.id IS NULL"
    ).fetchone()[0])
    if orphan_activities != 0:
        raise RuntimeError("workflow operation activity orphans exist")
    return {
        "tasks": task_count,
        "comments": int(connection.execute("SELECT COUNT(*) FROM workflow_task_comments").fetchone()[0]),
        "activityLogs": int(connection.execute("SELECT COUNT(*) FROM workflow_task_activity_logs").fetchone()[0]),
        "reminders": int(connection.execute("SELECT COUNT(*) FROM workflow_task_reminders").fetchone()[0]),
        "templates": template_count,
        "entityLinks": int(connection.execute("SELECT COUNT(*) FROM workflow_task_entity_links").fetchone()[0]),
        "attachments": int(connection.execute("SELECT COUNT(*) FROM workflow_task_attachments").fetchone()[0]),
        "cleanupQueue": int(connection.execute("SELECT COUNT(*) FROM workflow_attachment_cleanup_queue").fetchone()[0]),
        "operationRecords": int(connection.execute("SELECT COUNT(*) FROM workflow_operation_records").fetchone()[0]),
        "operationActivities": int(connection.execute("SELECT COUNT(*) FROM workflow_operation_activities").fetchone()[0]),
    }


def backup_database(connection: sqlite3.Connection, target: Path) -> None:
    if target.exists():
        raise RuntimeError("backup path already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        destination = sqlite3.connect(temporary)
        try:
            connection.backup(destination)
            check = destination.execute("PRAGMA integrity_check").fetchone()
            if check is None or str(check[0]).lower() != "ok":
                raise RuntimeError("workflow D1 backup integrity check failed")
        finally:
            destination.close()
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def install(source: Path, sql_path: Path, backup: Path, receipt: Path) -> dict[str, object]:
    if not source.is_file() or source.suffix.lower() not in {".sqlite", ".sqlite3"} or source.is_symlink():
        raise RuntimeError("source must be an ordinary absolute SQLite file")
    if not sql_path.is_file() or sql_path.is_symlink():
        raise RuntimeError("authority SQL is missing")
    if backup == source or receipt in {source, backup} or backup.exists() or receipt.exists():
        raise RuntimeError("workflow authority output paths are invalid or already exist")
    sql_text = sql_path.read_text(encoding="utf-8")
    expected_triggers = sorted(set(TRIGGER_RE.findall(sql_text)))
    if len(expected_triggers) != 42:
        raise RuntimeError("workflow operations authority SQL trigger contract is incomplete")

    connection = sqlite3.connect(source, timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        before_counts = preflight(connection)
        before_authority = authority(connection)
        if before_authority is not None and before_authority["owner"] != "legacy":
            raise RuntimeError("workflow operations authority is no longer legacy-owned")
        backup_database(connection, backup)
        connection.execute("BEGIN IMMEDIATE")
        try:
            if preflight(connection) != before_counts:
                raise RuntimeError("workflow D1 changed during authority installation")
            for raw_statement in sql_text.split("--> statement-breakpoint"):
                statement = raw_statement.strip()
                if statement:
                    connection.execute(statement)
            after_authority = authority(connection)
            if after_authority != {"owner": "legacy", "epoch": 1, "cutoverId": ""}:
                if before_authority is None or after_authority != before_authority:
                    raise RuntimeError("workflow operations authority install changed existing state")
            installed = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE 'workflow_operations_%'"
                )
            }
            if installed != set(expected_triggers):
                raise RuntimeError("workflow operations authority trigger set is incomplete or unexpected")
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
        result = install(*(value.resolve() for value in (args.source, args.sql, args.backup, args.receipt)))
    except Exception as error:
        print(json.dumps({"status": "failed", "error": type(error).__name__}, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
