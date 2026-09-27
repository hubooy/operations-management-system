"""Bounded, read-only projection of n8n execution metadata; never reads node data."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, time as day_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings

from .errors import WorkflowApiError

SHANGHAI = ZoneInfo("Asia/Shanghai")
CATALOG = Path(__file__).with_name("import_chain_catalog.json")
MAX_ROWS = 2000
SYNTHETIC_APPLICATION_ID = 0x54525349


def _unavailable():
    return WorkflowApiError("暂时无法读取 n8n 执行状态，不能据此判断今天是否完成。", code="n8n_status_unavailable", status=503)


def _instant(value):
    if value is None:
        return None
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def summarize_workflow(workflow_id, active, rows):
    # All rows have already been restricted to today's terminal executions or
    # currently active executions, and to full automatic trigger/webhook runs.
    # n8n SQLite timestamps may omit the zone or include milliseconds. Return
    # explicit UTC instants so browsers never interpret database UTC as local.
    rows = [{**r, "startedAt": _instant(r["startedAt"]).isoformat() if r["startedAt"] else None,
             "stoppedAt": _instant(r["stoppedAt"]).isoformat() if r["stoppedAt"] else None} for r in rows]
    success = [r for r in rows if r["status"] == "success" and r["stoppedAt"]]
    success.sort(key=lambda r: _instant(r["stoppedAt"]), reverse=True)
    ongoing = [r for r in rows if r["status"] in {"new", "running", "waiting"} and not r["stoppedAt"]]
    rows = sorted(rows, key=lambda r: (_instant(r["stoppedAt"] or r["startedAt"]), int(r["id"])), reverse=True)
    latest = max(ongoing, key=lambda r: int(r["id"])) if ongoing else (rows[0] if rows else None)
    state = "no_record"
    if latest:
        state = {"success": "completed", "new": "pending", "running": "running", "waiting": "waiting",
                 "error": "failed", "crashed": "failed", "canceled": "cancelled"}.get(latest["status"], "unknown")
        if state == "completed" and not latest["stoppedAt"]:
            state = "unknown"
    return {"workflowId": workflow_id, "active": active, "state": state,
            "completedToday": bool(success), "completedAt": success[0]["stoppedAt"] if success else None,
            "completedMode": success[0]["mode"] if success else None,
            "executionId": str(latest["id"]) if latest else None,
            "executionMode": latest["mode"] if latest else None,
            "startedAt": latest["startedAt"] if latest else None,
            "finishedAt": latest["stoppedAt"] if latest else None}


def read_today_status(*, now=None):
    now = now or datetime.now(timezone.utc)
    local_day = now.astimezone(SHANGHAI).date()
    start = datetime.combine(local_day, day_time(), SHANGHAI).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    configured = getattr(settings, "N8N_STATUS_DATABASE_PATH", "")
    if not configured:
        raise _unavailable()
    database = Path(configured)
    if not database.is_absolute() or not database.is_file():
        raise _unavailable()
    if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in (database, *database.parents)):
        raise _unavailable()
    workflow_ids = json.loads(CATALOG.read_text(encoding="utf-8"))["workflowIds"]
    if not workflow_ids or len(workflow_ids) > 32:
        raise _unavailable()
    deadline = time.monotonic() + 2
    connection = None
    try:
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0.5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        connection.execute("BEGIN")
        marks = ",".join("?" for _ in workflow_ids)
        workflows = {r["id"]: bool(r["active"]) for r in connection.execute(
            f'SELECT id, active FROM workflow_entity WHERE id IN ({marks}) AND "isArchived" = 0', workflow_ids)}
        rows = connection.execute(f'''
            SELECT id, "workflowId", status, mode, COALESCE("startedAt", "createdAt") AS "startedAt", "stoppedAt"
            FROM execution_entity
            WHERE "workflowId" IN ({marks}) AND "deletedAt" IS NULL
              AND mode IN ('trigger', 'webhook')
              AND datetime(COALESCE("startedAt", "createdAt")) <= datetime(?)
              AND ((datetime("stoppedAt") >= datetime(?) AND datetime("stoppedAt") < datetime(?)
                    AND datetime("stoppedAt") <= datetime(?))
                   OR (status IN ('new','running','waiting') AND "stoppedAt" IS NULL))
            ORDER BY id DESC LIMIT ?
        ''', [*workflow_ids, now.isoformat(), start.isoformat(), end.isoformat(), now.isoformat(), MAX_ROWS + 1]).fetchall()
        if len(rows) > MAX_ROWS:
            raise _unavailable()  # Never infer a missing success from truncated history.
        synthetic = connection.execute("PRAGMA application_id").fetchone()[0] == SYNTHETIC_APPLICATION_ID
        items = []
        for workflow_id in workflow_ids:
            if workflow_id not in workflows:
                items.append({"workflowId": workflow_id, "active": None, "state": "unavailable", "completedToday": False,
                              "completedAt": None, "completedMode": None, "executionId": None, "executionMode": None,
                              "startedAt": None, "finishedAt": None})
            else:
                items.append(summarize_workflow(workflow_id, workflows[workflow_id], [dict(r) for r in rows if r["workflowId"] == workflow_id]))
        return {"date": local_day.isoformat(), "timezone": "Asia/Shanghai", "checkedAt": now.isoformat(),
                "source": "synthetic_n8n" if synthetic else "n8n_execution_metadata", "items": items}
    except (sqlite3.Error, ValueError, TypeError, OSError):
        raise _unavailable() from None
    finally:
        if connection:
            connection.close()
