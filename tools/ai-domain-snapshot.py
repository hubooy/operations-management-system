"""Read-only exported AI table snapshot; never copies unrelated business rows."""

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from ai_assistant.table_manifest import AI_TABLES

TABLES = set(AI_TABLES) - {
    "ai_business_file_runs", "ai_business_file_chunks", "ai_business_volume_chunks", "ai_business_budget_plans",
    "ai_business_evidence_runs", "ai_business_evidence_chunks", "ai_business_evidence_sources",
    "ai_library_revisions", "ai_execution_guidance", "ai_report_runs", "ai_report_deliveries",
    "ai_prompt_settings_revisions",
    "ai_dingtalk_settings",
    "ai_dingtalk_sessions", "ai_dingtalk_receipts",
    "ai_dingtalk_schedules", "ai_dingtalk_schedule_runs",
    "ai_conversation_workspaces",  # New PostgreSQL metadata has no historical D1 counterpart.
    "ai_data_revisions",
    "ai_write_authority",
    "ai_write_request_receipts",
    "ai_mutation_audits",
    "ai_migration_runs",
}


def snapshot(source, destination):
    source, destination = Path(source), Path(destination)
    if (
        not source.is_absolute()
        or not source.is_file()
        or not destination.is_absolute()
        or destination.exists()
        or source.resolve() == destination.resolve()
    ):
        raise ValueError("Exact source and create-only destination required")
    for path in [
        source,
        *source.parents,
        destination.parent,
        *destination.parent.parents,
    ]:
        if path.is_symlink() or path.is_junction():
            raise ValueError("Reparse paths are forbidden")
    with destination.open("xb"):
        pass
    source_db = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        source_db.execute("PRAGMA query_only=ON")
        source_db.execute("BEGIN")
        target.execute("BEGIN")
        counts, size = {}, 0
        for table in sorted(TABLES):
            definition = source_db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not definition:
                raise ValueError("AI source table missing or retired")
            target.execute(definition[0])
            names = [
                row[1] for row in source_db.execute(f'PRAGMA table_info("{table}")')
            ]
            columns = ",".join('"' + name.replace('"', '""') + '"' for name in names)
            insert = (
                f'INSERT INTO "{table}" ({"rowid," if table == "ai_conversation_messages" else ""}{columns}) VALUES ('
                + ",".join(
                    "?"
                    for _ in range(
                        len(names) + int(table == "ai_conversation_messages")
                    )
                )
                + ")"
            )
            counts[table] = 0
            for row in source_db.execute(
                f'SELECT {"rowid," if table == "ai_conversation_messages" else ""}* FROM "{table}"'
            ):
                size += len(json.dumps(row, ensure_ascii=False).encode())
                counts[table] += 1
                if size > 256 * 1024 * 1024 or sum(counts.values()) > 1_000_000:
                    raise ValueError("AI snapshot bound exceeded")
                target.execute(insert, tuple(row))
        if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("AI snapshot integrity check failed")
        target.commit()
        source_db.rollback()
    finally:
        target.close()
        source_db.close()
    hasher = hashlib.sha256()
    with destination.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return {
        "status": "completed",
        "tableCount": len(counts),
        "counts": counts,
        "sha256": hasher.hexdigest(),
        "sourceWrites": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    try:
        print(
            json.dumps(snapshot(args.source, args.destination), separators=(",", ":"))
        )
    except Exception:
        print(json.dumps({"status": "failed", "code": "ai_snapshot_failed"}))
        sys.exit(1)
