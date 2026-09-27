"""Create synthetic n8n metadata for this preview; never reads a live n8n DB."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[2]


def seed(target):
    target = Path(target).resolve()
    if target.parent != (ROOT / ".runtime/preview").resolve() or target.suffix != ".sqlite3" or target.exists():
        raise RuntimeError("A new isolated preview SQLite path is required")
    ids = json.loads((ROOT / "backend/workflow/import_chain_catalog.json").read_text(encoding="utf8"))["workflowIds"]
    now = datetime.now(timezone.utc)
    with closing(sqlite3.connect(target)) as conn, conn:
        conn.execute("PRAGMA application_id=1414681417")  # 0x54525349, explicit synthetic provenance
        conn.execute('CREATE TABLE workflow_entity (id TEXT PRIMARY KEY, active INTEGER, isArchived INTEGER)')
        conn.execute('CREATE TABLE execution_entity (id INTEGER PRIMARY KEY, workflowId TEXT, status TEXT, startedAt TEXT, stoppedAt TEXT, createdAt TEXT, deletedAt TEXT, mode TEXT)')
        for index, workflow_id in enumerate(ids):
            conn.execute('INSERT INTO workflow_entity VALUES (?,1,0)', [workflow_id])
            state = ["success", "success", "running", "error", "waiting", "success", "none"][index % 7]
            if state == "none":
                continue
            started = (now - timedelta(minutes=5)).isoformat()
            stopped = (now - timedelta(minutes=1)).isoformat() if state in {"success", "error"} else None
            conn.execute('INSERT INTO execution_entity VALUES (?,?,?,?,?,?,NULL,?)', [index + 1, workflow_id, state, started, stopped, started, "trigger"])


if __name__ == "__main__":
    seed(sys.argv[1])
