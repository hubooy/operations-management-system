#!/usr/bin/env python3
"""Create a sealed, verified D1 snapshot for the remaining workflow migration."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile


FORMAT_VERSION = "workflow-operations-d1-snapshot-v1"
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
    "workflow_operations_write_authority",
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


def inspect(connection: sqlite3.Connection) -> tuple[dict[str, int], dict[str, object]]:
    present = {
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if set(SOURCE_TABLES) - present:
        raise RuntimeError("workflow operations snapshot source schema is incomplete")
    quick = connection.execute("PRAGMA quick_check").fetchone()
    if quick is None or str(quick[0]).lower() != "ok":
        raise RuntimeError("workflow operations snapshot quick_check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("workflow operations snapshot foreign key check failed")
    if int(connection.execute(
        "SELECT COUNT(*) FROM workflow_operation_records WHERE record_type='launch'"
    ).fetchone()[0]):
        raise RuntimeError("retired workflow launch facts are still present")
    task_count = int(connection.execute("SELECT COUNT(*) FROM workflow_tasks").fetchone()[0])
    template_count = int(connection.execute("SELECT COUNT(*) FROM workflow_task_templates").fetchone()[0])
    if task_count != int(connection.execute("SELECT COUNT(*) FROM workflow_task_states").fetchone()[0]):
        raise RuntimeError("workflow task state rows are incomplete")
    if template_count != int(connection.execute("SELECT COUNT(*) FROM workflow_task_template_states").fetchone()[0]):
        raise RuntimeError("workflow template state rows are incomplete")
    if int(connection.execute(
        "SELECT COUNT(*) FROM workflow_operation_activities a "
        "LEFT JOIN workflow_operation_records r ON r.id=a.record_id WHERE r.id IS NULL"
    ).fetchone()[0]):
        raise RuntimeError("workflow operation activity orphans exist")
    authority_rows = connection.execute(
        "SELECT owner,epoch,cutover_id FROM workflow_operations_write_authority WHERE id=1"
    ).fetchall()
    if len(authority_rows) != 1 or str(authority_rows[0][0]) != "legacy" or int(authority_rows[0][1]) < 1:
        raise RuntimeError("workflow operations snapshot requires legacy-owned authority")
    counts = {
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
    authority = {
        "owner": str(authority_rows[0][0]),
        "epoch": int(authority_rows[0][1]),
        "cutoverId": str(authority_rows[0][2]),
    }
    return counts, authority


def create_snapshot(source: Path, output: Path, manifest: Path) -> dict[str, object]:
    if not source.is_file() or source.suffix.lower() not in {".sqlite", ".sqlite3"} or source.is_symlink():
        raise RuntimeError("source must be an ordinary SQLite file")
    if output == source or manifest in {source, output} or output.exists() or manifest.exists():
        raise RuntimeError("snapshot output paths are invalid or already exist")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        source_connection.execute("BEGIN")
        before_counts, before_authority = inspect(source_connection)
        destination = sqlite3.connect(temporary)
        try:
            source_connection.backup(destination)
            destination.commit()
            after_counts, after_authority = inspect(destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).lower() != "ok":
                raise RuntimeError("workflow operations snapshot integrity check failed")
            if after_counts != before_counts or after_authority != before_authority:
                raise RuntimeError("workflow operations snapshot readback differs from source")
        finally:
            destination.close()
        source_connection.rollback()
        os.replace(temporary, output)
    finally:
        source_connection.close()
        temporary.unlink(missing_ok=True)

    payload: dict[str, object] = {
        "formatVersion": FORMAT_VERSION,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourcePathSha256": hashlib.sha256(str(source).encode("utf-8")).hexdigest(),
        "outputPath": str(output),
        "outputSha256": sha256_file(output),
        "counts": before_counts,
        "authority": before_authority,
    }
    atomic_json(manifest, payload)
    payload["manifestPath"] = str(manifest)
    payload["manifestSha256"] = sha256_file(manifest)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = create_snapshot(args.source.resolve(), args.output.resolve(), args.manifest.resolve())
    except Exception as error:
        print(json.dumps({"status": "failed", "error": type(error).__name__}, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
