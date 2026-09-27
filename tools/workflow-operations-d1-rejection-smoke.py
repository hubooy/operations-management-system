#!/usr/bin/env python
"""Prove that the activated legacy workflow namespace rejects writes."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys


EXPECTED_ERROR = "workflow_operations_authority_not_legacy"


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: workflow-operations-d1-rejection-smoke.py <d1.sqlite>")
    source = Path(sys.argv[1]).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in {".sqlite", ".sqlite3"}:
        raise SystemExit("workflow D1 source is missing or invalid")

    connection = sqlite3.connect(source, timeout=10, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN")
        row = connection.execute(
            "SELECT owner,epoch,cutover_id FROM workflow_operations_write_authority WHERE id=1"
        ).fetchone()
        if row is None or row[0] != "postgresql" or int(row[1]) < 2 or not str(row[2] or ""):
            raise RuntimeError("workflow operations D1 authority is not terminally activated")
        rejected = False
        try:
            connection.execute(
                "INSERT INTO workflow_task_bootstrap(key) VALUES (?)",
                ("workflow-operations-production-smoke-must-fail",),
            )
        except sqlite3.DatabaseError as error:
            rejected = EXPECTED_ERROR in str(error)
        finally:
            connection.rollback()
        if not rejected:
            raise RuntimeError("legacy workflow D1 accepted a forbidden write")
        print(json.dumps({
            "status": "passed",
            "owner": "postgresql",
            "epoch": int(row[1]),
            "cutoverId": str(row[2]),
            "rejection": EXPECTED_ERROR,
            "taskCount": int(connection.execute(
                "SELECT COUNT(*) FROM workflow_tasks"
            ).fetchone()[0]),
            "activeTaskCount": int(connection.execute(
                "SELECT COUNT(*) FROM workflow_tasks t "
                "JOIN workflow_task_states s ON s.task_id=t.id WHERE s.deleted_at IS NULL"
            ).fetchone()[0]),
            "attachmentCount": int(connection.execute(
                "SELECT COUNT(*) FROM workflow_task_attachments"
            ).fetchone()[0]),
            "activeAttachmentCount": int(connection.execute(
                "SELECT COUNT(*) FROM workflow_task_attachments a "
                "JOIN workflow_task_states s ON s.task_id=a.task_id WHERE s.deleted_at IS NULL"
            ).fetchone()[0]),
        }, separators=(",", ":")))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
