#!/usr/bin/env python3
"""Create a sealed, verified workflow launch D1 snapshot for migration."""

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
import re


FORMAT_VERSION = "workflow-launch-d1-snapshot-v1"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def create_snapshot(
    source: Path,
    output: Path,
    manifest: Path,
    *,
    rehearsal_bootstrap_legacy_authority: bool = False,
) -> dict[str, object]:
    if not source.is_file() or source.suffix.lower() != ".sqlite" or source.is_symlink():
        raise RuntimeError("source must be an ordinary .sqlite file")
    if output == source or manifest in {source, output} or output.exists() or manifest.exists():
        raise RuntimeError("snapshot output paths are invalid or already exist")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        connection.execute("BEGIN")
        schemas: dict[str, str] = {}
        authority_synthetic = False
        for table in (
            "workflow_operation_records",
            "workflow_operation_activities",
            "workflow_launch_write_authority",
        ):
            row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if (
                table == "workflow_launch_write_authority"
                and row is None
                and rehearsal_bootstrap_legacy_authority
            ):
                schemas[table] = (
                    "CREATE TABLE workflow_launch_write_authority ("
                    "id integer PRIMARY KEY NOT NULL CHECK (id=1),"
                    "owner text NOT NULL CHECK (owner IN ('legacy','pending','postgresql')) ,"
                    "epoch integer NOT NULL DEFAULT 1 CHECK (epoch>=1),"
                    "cutover_id text NOT NULL DEFAULT '',updated_at text NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
                authority_synthetic = True
                continue
            if row is None or not str(row[0] or "").strip():
                raise RuntimeError("workflow snapshot source schema is incomplete")
            schemas[table] = str(row[0])
        records = connection.execute(
            "SELECT * FROM workflow_operation_records WHERE record_type=? ORDER BY id", ("launch",)
        ).fetchall()
        activities = connection.execute(
            "SELECT a.* FROM workflow_operation_activities a "
            "JOIN workflow_operation_records r ON r.id=a.record_id "
            "WHERE r.record_type=? ORDER BY a.id", ("launch",)
        ).fetchall()
        authority_rows = (
            [(1, "legacy", 1, "", datetime.now(timezone.utc).isoformat())]
            if authority_synthetic
            else connection.execute(
                "SELECT * FROM workflow_launch_write_authority ORDER BY id"
            ).fetchall()
        )
        destination = sqlite3.connect(temporary)
        try:
            for table in (
                "workflow_operation_records",
                "workflow_operation_activities",
                "workflow_launch_write_authority",
            ):
                destination.execute(schemas[table])
            for table, rows in (
                ("workflow_operation_records", records),
                ("workflow_operation_activities", activities),
                ("workflow_launch_write_authority", authority_rows),
            ):
                columns = (
                    ["id", "owner", "epoch", "cutover_id", "updated_at"]
                    if table == "workflow_launch_write_authority" and authority_synthetic
                    else [str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")]
                )
                if not columns or any(IDENTIFIER.fullmatch(column) is None for column in columns):
                    raise RuntimeError("workflow snapshot source columns are invalid")
                if rows:
                    destination.executemany(
                        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                        rows,
                    )
            destination.commit()
            check = destination.execute("PRAGMA integrity_check").fetchone()
            if check is None or str(check[0]).lower() != "ok":
                raise RuntimeError("workflow snapshot integrity check failed")
        finally:
            destination.close()
        connection.rollback()
        os.replace(temporary, output)
    finally:
        connection.close()
        temporary.unlink(missing_ok=True)

    sealed = sqlite3.connect(f"file:{output.as_posix()}?mode=ro", uri=True)
    try:
        sealed.row_factory = sqlite3.Row
        authority = sealed.execute(
            "SELECT owner,epoch,cutover_id FROM workflow_launch_write_authority WHERE id=1"
        ).fetchone()
        counts = {
            "records": int(sealed.execute(
                "SELECT COUNT(*) FROM workflow_operation_records WHERE record_type=?", ("launch",)
            ).fetchone()[0]),
            "activities": int(sealed.execute(
                "SELECT COUNT(*) FROM workflow_operation_activities a "
                "JOIN workflow_operation_records r ON r.id=a.record_id WHERE r.record_type=?", ("launch",)
            ).fetchone()[0]),
        }
        if authority is None:
            raise RuntimeError("workflow authority is missing from snapshot")
        authority_payload = {
            "owner": str(authority["owner"]),
            "epoch": int(authority["epoch"]),
            "cutoverId": str(authority["cutover_id"]),
        }
    finally:
        sealed.close()
    payload: dict[str, object] = {
        "formatVersion": FORMAT_VERSION,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourcePathSha256": hashlib.sha256(str(source).encode("utf-8")).hexdigest(),
        "outputPath": str(output),
        "outputSha256": sha256_file(output),
        "counts": counts,
        "authority": authority_payload,
        "authoritySynthetic": authority_synthetic,
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
    parser.add_argument("--rehearsal-bootstrap-legacy-authority", action="store_true")
    args = parser.parse_args()
    try:
        result = create_snapshot(
            args.source.resolve(), args.output.resolve(), args.manifest.resolve(),
            rehearsal_bootstrap_legacy_authority=args.rehearsal_bootstrap_legacy_authority,
        )
    except Exception as error:
        print(json.dumps({"status": "failed", "error": type(error).__name__}, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
