#!/usr/bin/env python3
"""Prove that the workflow launch subdomain owns no local R2 objects."""

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


FORMAT_VERSION = "workflow-launch-r2-retirement-evidence-v1"
PATTERNS = ("workflow-launch/%", "workflow-launch-%", "new-product/%", "new-product-%")


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def atomic_json(path: Path, value: dict[str, object]) -> None:
    if path.exists():
        raise RuntimeError("R2 evidence output already exists")
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


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def collect(root: Path) -> dict[str, object]:
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("R2 metadata root must be an ordinary directory")
    databases: list[Path] = []
    for path in sorted(root.glob("*.sqlite")):
        if path.is_symlink():
            raise RuntimeError("R2 metadata database must not be a symlink")
        if path.is_file():
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            try:
                if table_exists(connection, "_mf_objects"):
                    databases.append(path)
            finally:
                connection.close()
    if len(databases) != 1:
        raise RuntimeError("R2 object metadata database is missing or ambiguous")

    database = databases[0]
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        for table in ("_mf_objects", "_mf_multipart_uploads", "_mf_multipart_parts"):
            if not table_exists(connection, table):
                raise RuntimeError("R2 metadata schema is incomplete")
        matches: dict[str, dict[str, int]] = {}
        for pattern in PATTERNS:
            object_count, object_bytes = connection.execute(
                "SELECT COUNT(*),COALESCE(SUM(size),0) FROM _mf_objects WHERE key LIKE ?", (pattern,)
            ).fetchone()
            multipart_uploads = connection.execute(
                "SELECT COUNT(*) FROM _mf_multipart_uploads WHERE key LIKE ?", (pattern,)
            ).fetchone()[0]
            multipart_parts = connection.execute(
                "SELECT COUNT(*) FROM _mf_multipart_parts WHERE object_key LIKE ?", (pattern,)
            ).fetchone()[0]
            matches[pattern] = {
                "objectCount": int(object_count),
                "objectBytes": int(object_bytes),
                "multipartUploadCount": int(multipart_uploads),
                "multipartPartCount": int(multipart_parts),
            }
        total_objects = int(connection.execute("SELECT COUNT(*) FROM _mf_objects").fetchone()[0])
    finally:
        connection.close()

    if any(any(value for value in counts.values()) for counts in matches.values()):
        raise RuntimeError("workflow launch R2 namespace is not empty")
    return {
        "version": FORMAT_VERSION,
        "status": "passed",
        "patterns": matches,
        "totalObjectCount": total_objects,
        "matchesDigest": hashlib.sha256(canonical_json(matches)).hexdigest(),
        "sourcePathSha256": hashlib.sha256(str(database).encode("utf-8")).hexdigest(),
        "recordedAt": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--r2-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        payload = collect(args.r2_root.resolve())
        atomic_json(args.output.resolve(), payload)
    except Exception as error:
        print(json.dumps({"status": "failed", "error": type(error).__name__}, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps({**payload, "output": str(args.output.resolve())}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
