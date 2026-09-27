"""Create one consistent, verified SQLite backup without mutating the source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import uuid
from pathlib import Path
from urllib.parse import quote


SAFE_COUNT_TABLES = (
    "sales_order_lines",
    "sales_import_batches",
    "erp_product_master",
    "sales_import_uploads",
    "sales_import_upload_chunks",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (name,),
        ).fetchone()
        is not None
    )


def _safe_evidence(connection: sqlite3.Connection) -> dict[str, object]:
    quick_check = connection.execute("PRAGMA quick_check").fetchone()
    if quick_check is None or quick_check[0] != "ok":
        raise RuntimeError("SQLite backup quick_check failed")
    counts = {
        name: int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
        for name in SAFE_COUNT_TABLES
        if _table_exists(connection, name)
    }
    revisions: dict[str, int] = {}
    if _table_exists(connection, "sales_overview_cache_state"):
        row = connection.execute(
            "SELECT sales_revision, erp_product_revision "
            "FROM sales_overview_cache_state WHERE id=1"
        ).fetchone()
        if row is not None:
            revisions = {"sales": int(row[0]), "erp": int(row[1])}
    return {"quickCheck": "ok", "counts": counts, "revisions": revisions}


def create_backup(source_value: str, destination_value: str) -> dict[str, object]:
    source = Path(source_value)
    destination = Path(destination_value)
    if not source.is_absolute() or source.suffix.lower() != ".sqlite":
        raise RuntimeError("source must be an absolute .sqlite file")
    if not destination.is_absolute() or destination.suffix.lower() != ".sqlite":
        raise RuntimeError("destination must be an absolute .sqlite file")
    if source.is_symlink() or destination.is_symlink():
        raise RuntimeError("symbolic links are not accepted")
    source = source.resolve(strict=True)
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    if destination.exists() or source == destination:
        raise RuntimeError("destination must be a new file distinct from source")
    if not source.is_file():
        raise RuntimeError("source is not a file")

    temporary = parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    source_connection: sqlite3.Connection | None = None
    target_connection: sqlite3.Connection | None = None
    try:
        source_uri = f"file:{quote(source.as_posix(), safe='/:')}?mode=ro"
        source_connection = sqlite3.connect(source_uri, uri=True, timeout=30)
        source_connection.execute("PRAGMA query_only=ON")
        target_connection = sqlite3.connect(str(temporary), timeout=30)
        source_connection.backup(target_connection, pages=16_384, sleep=0.05)
        target_connection.commit()
        evidence = _safe_evidence(target_connection)
        target_connection.close()
        target_connection = None
        source_connection.close()
        source_connection = None

        # Windows requires a writable handle for FlushFileBuffers/fsync.
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        size = destination.stat().st_size
        if size <= 0:
            raise RuntimeError("SQLite backup is empty")
        return {
            "status": "completed",
            "version": "teruisi-sqlite-backup-v1",
            "destinationName": destination.name,
            "sizeBytes": size,
            "sha256": _sha256_file(destination),
            **evidence,
        }
    finally:
        if target_connection is not None:
            target_connection.close()
        if source_connection is not None:
            source_connection.close()
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    arguments = parser.parse_args()
    print(
        json.dumps(
            create_backup(arguments.source, arguments.destination),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
