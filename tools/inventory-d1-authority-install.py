#!/usr/bin/env python3
"""Install the operator-only inventory D1 authority guard with a verified backup."""

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


FORMAT_VERSION = "inventory-d1-authority-install-v1"
TRIGGER_RE = re.compile(
    r"CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+`([^`]+)`", re.IGNORECASE
)
REQUIRED_TABLES = (
    "inventory_import_batches",
    "inventory_stock_lines",
    "inventory_age_metrics",
    "replenishment_plan_items",
    "erp_reference_import_batches",
    "erp_inventory_age_lines",
    "system_settings",
    "import_content_fingerprints",
    "import_content_attempts",
    "import_scope_heads",
    "inventory_import_uploads",
    "inventory_import_upload_chunks",
    "inventory_import_upload_results",
)
AGE_SCOPE_KEYS = (
    "ce499a195aa16f0f763b11768a0c897ff8f51beb6d4c3a35e6f5dcbb8795055d",
    "c8d8ffcac2953c3a5b5e4cec882a9553048c2d95564642441939ae6bb007b8a4",
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
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def authority(connection: sqlite3.Connection) -> dict[str, object] | None:
    if not table_exists(connection, "inventory_write_authority"):
        return None
    rows = connection.execute(
        "SELECT id,owner,epoch,cutover_id FROM inventory_write_authority"
    ).fetchall()
    if len(rows) != 1 or int(rows[0][0]) != 1:
        raise RuntimeError("inventory authority singleton is invalid")
    result = {
        "owner": str(rows[0][1]),
        "epoch": int(rows[0][2]),
        "cutoverId": str(rows[0][3]),
    }
    if result["owner"] not in {"d1", "pending", "postgresql"} or result["epoch"] < 1:
        raise RuntimeError("inventory authority state is invalid")
    return result


def preflight(connection: sqlite3.Connection) -> dict[str, int]:
    missing = [table for table in REQUIRED_TABLES if not table_exists(connection, table)]
    if missing:
        raise RuntimeError("inventory D1 schema is incomplete")
    quick_check = connection.execute("PRAGMA quick_check").fetchone()
    if quick_check is None or str(quick_check[0]).lower() != "ok":
        raise RuntimeError("inventory D1 quick_check failed")
    placeholders = ",".join("?" for _ in AGE_SCOPE_KEYS)
    result = {
        "inventoryBatches": int(connection.execute(
            "SELECT COUNT(*) FROM inventory_import_batches"
        ).fetchone()[0]),
        "inventoryStockRows": int(connection.execute(
            "SELECT COUNT(*) FROM inventory_stock_lines"
        ).fetchone()[0]),
        "inventoryAgeBatches": int(connection.execute(
            "SELECT COUNT(*) FROM erp_reference_import_batches WHERE source_key='inventory_age'"
        ).fetchone()[0]),
        "inventoryAgeRows": int(connection.execute(
            "SELECT COUNT(*) FROM erp_inventory_age_lines"
        ).fetchone()[0]),
        "inventoryPlans": int(connection.execute(
            "SELECT COUNT(*) FROM replenishment_plan_items"
        ).fetchone()[0]),
        "inventoryUploads": int(connection.execute(
            "SELECT COUNT(*) FROM inventory_import_uploads WHERE "
            "fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%'"
        ).fetchone()[0]),
        "inventoryUploadChunks": int(connection.execute(
            "SELECT COUNT(*) FROM inventory_import_upload_chunks c "
            "JOIN inventory_import_uploads u ON u.id=c.upload_id WHERE "
            "u.fingerprint LIKE 'inventory-v1:%' OR u.fingerprint LIKE 'erp:inventory_age:%'"
        ).fetchone()[0]),
        "processingBatches": int(connection.execute(
            "SELECT COUNT(*) FROM inventory_import_batches WHERE status='processing'"
        ).fetchone()[0]),
        "processingAgeBatches": int(connection.execute(
            "SELECT COUNT(*) FROM erp_reference_import_batches "
            "WHERE source_key='inventory_age' AND status='processing'"
        ).fetchone()[0]),
        "processingAttempts": int(connection.execute(
            "SELECT COUNT(*) FROM import_content_attempts WHERE "
            "(domain='inventory-stock' OR (domain='erp-reference' AND "
            "json_extract(scope_json,'$.source')='inventory_age')) AND outcome='processing'"
        ).fetchone()[0]),
        "busyScopeHeads": int(connection.execute(
            "SELECT COUNT(*) FROM import_scope_heads WHERE "
            "(domain='inventory-stock' OR (domain='erp-reference' AND "
            f"(scope_key IN ({placeholders}) OR COALESCE(current_batch_id,'') LIKE 'inventory_age:%'))) "
            "AND (status<>'ready' OR COALESCE(owner_token,'')<>'')",
            AGE_SCOPE_KEYS,
        ).fetchone()[0]),
    }
    if any(
        result[key]
        for key in (
            "inventoryUploadChunks",
            "processingBatches",
            "processingAgeBatches",
            "processingAttempts",
            "busyScopeHeads",
        )
    ):
        raise RuntimeError("inventory D1 is not quiet or still owns R2 chunks")
    return result


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
            connection.backup(destination, pages=4096)
            check = destination.execute("PRAGMA integrity_check").fetchone()
            if check is None or str(check[0]).lower() != "ok":
                raise RuntimeError("inventory D1 backup integrity check failed")
        finally:
            destination.close()
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def install(source: Path, sql_path: Path, backup: Path, receipt: Path) -> dict[str, object]:
    if not source.is_file() or source.suffix.lower() != ".sqlite" or source.is_symlink():
        raise RuntimeError("source must be an ordinary absolute .sqlite file")
    if not sql_path.is_file() or sql_path.is_symlink():
        raise RuntimeError("authority SQL is missing")
    if backup == source or receipt in {source, backup} or backup.exists() or receipt.exists():
        raise RuntimeError("inventory authority output paths are invalid or already exist")
    sql_text = sql_path.read_text(encoding="utf-8")
    expected_triggers = sorted(set(TRIGGER_RE.findall(sql_text)))
    if len(expected_triggers) != 42:
        raise RuntimeError("inventory authority SQL trigger contract is incomplete")

    connection = sqlite3.connect(source, timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        before_counts = preflight(connection)
        before_authority = authority(connection)
        if before_authority is not None and before_authority["owner"] != "d1":
            raise RuntimeError("inventory authority is no longer D1-owned")
        backup_database(connection, backup)
        connection.execute("BEGIN IMMEDIATE")
        try:
            if preflight(connection) != before_counts:
                raise RuntimeError("inventory D1 changed during authority installation")
            for raw_statement in sql_text.split("--> statement-breakpoint"):
                statement = raw_statement.strip()
                if statement:
                    connection.execute(statement)
            after_authority = authority(connection)
            if after_authority != {"owner": "d1", "epoch": 1, "cutoverId": ""}:
                if before_authority is None or after_authority != before_authority:
                    raise RuntimeError("inventory authority install changed an existing state")
            installed = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE 'inventory_authority_%'"
                )
            }
            if installed != set(expected_triggers):
                raise RuntimeError("inventory authority trigger set is incomplete or unexpected")
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
            *(value.resolve() for value in (args.source, args.sql, args.backup, args.receipt))
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
