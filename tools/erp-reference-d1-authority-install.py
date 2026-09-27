"""Install the operator-only D1 ERP write fence with a verified backup."""

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


FORMAT_VERSION = "erp-reference-d1-authority-install-v1"
TRIGGER_RE = re.compile(r"CREATE TRIGGER IF NOT EXISTS `([^`]+)`")
REQUIRED_TABLES = (
    "erp_reference_import_batches", "erp_product_master", "erp_combo_items",
    "erp_reference_projection_source_state", "erp_product_projection_state",
    "erp_reference_projection_outbox", "import_content_fingerprints",
    "import_content_attempts", "import_scope_heads", "inventory_import_uploads",
    "inventory_import_upload_chunks", "inventory_import_upload_results",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.close(descriptor)
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def authority(connection: sqlite3.Connection) -> dict[str, object] | None:
    if not table_exists(connection, "erp_reference_write_authority"):
        return None
    row = connection.execute(
        "SELECT id,owner,epoch,cutover_id FROM erp_reference_write_authority WHERE id=1"
    ).fetchone()
    if row is None:
        raise RuntimeError("ERP authority singleton is missing")
    return {"id": int(row[0]), "owner": str(row[1]), "epoch": int(row[2]), "cutoverId": str(row[3])}


def preflight(connection: sqlite3.Connection) -> dict[str, int]:
    missing = [name for name in REQUIRED_TABLES if not table_exists(connection, name)]
    if missing:
        raise RuntimeError(f"D1 ERP tables missing: {', '.join(missing)}")
    processing = int(connection.execute(
        "SELECT COUNT(*) FROM erp_reference_import_batches "
        "WHERE source_key IN ('products','combos') AND status='processing'"
    ).fetchone()[0])
    processing += int(connection.execute(
        "SELECT COUNT(*) FROM import_content_fingerprints "
        "WHERE domain='erp-reference' AND status='processing'"
    ).fetchone()[0])
    processing += int(connection.execute(
        "SELECT COUNT(*) FROM import_content_attempts "
        "WHERE domain='erp-reference' AND outcome='processing'"
    ).fetchone()[0])
    processing += int(connection.execute(
        "SELECT COUNT(*) FROM import_scope_heads "
        "WHERE domain='erp-reference' AND (status<>'ready' OR COALESCE(owner_token,'')<>'')"
    ).fetchone()[0])
    processing += int(connection.execute(
        "SELECT COUNT(*) FROM inventory_import_uploads "
        "WHERE (fingerprint LIKE 'erp:products:%' OR fingerprint LIKE 'erp:combos:%') "
        "AND status<>'completed'"
    ).fetchone()[0])
    if processing:
        raise RuntimeError("D1 ERP has processing state")
    return {
        "batches": int(connection.execute("SELECT COUNT(*) FROM erp_reference_import_batches WHERE source_key IN ('products','combos')").fetchone()[0]),
        "products": int(connection.execute("SELECT COUNT(*) FROM erp_product_master").fetchone()[0]),
        "combos": int(connection.execute("SELECT COUNT(*) FROM erp_combo_items").fetchone()[0]),
        "fingerprints": int(connection.execute("SELECT COUNT(*) FROM import_content_fingerprints WHERE domain='erp-reference'").fetchone()[0]),
        "attempts": int(connection.execute("SELECT COUNT(*) FROM import_content_attempts WHERE domain='erp-reference'").fetchone()[0]),
        "heads": int(connection.execute("SELECT COUNT(*) FROM import_scope_heads WHERE domain='erp-reference'").fetchone()[0]),
        "outbox": int(connection.execute("SELECT COUNT(*) FROM erp_reference_projection_outbox").fetchone()[0]),
        "uploads": int(connection.execute("SELECT COUNT(*) FROM inventory_import_uploads WHERE fingerprint LIKE 'erp:products:%' OR fingerprint LIKE 'erp:combos:%'").fetchone()[0]),
    }


def backup_database(connection: sqlite3.Connection, target: Path) -> None:
    if target.exists() or target.is_symlink():
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
            connection.backup(destination)
            if str(destination.execute("PRAGMA integrity_check").fetchone()[0]).lower() != "ok":
                raise RuntimeError("D1 ERP backup integrity check failed")
        finally:
            destination.close()
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def install(source_path: Path, sql_path: Path, backup_path: Path, receipt_path: Path) -> dict[str, object]:
    if not source_path.is_file() or source_path.is_symlink() or source_path.suffix.lower() not in {".sqlite", ".sqlite3"}:
        raise RuntimeError("source must be an ordinary SQLite file")
    if not sql_path.is_file() or sql_path.is_symlink():
        raise RuntimeError("ERP authority SQL is missing")
    if backup_path in {source_path, receipt_path} or receipt_path == source_path or backup_path.exists() or receipt_path.exists():
        raise RuntimeError("authority output paths are invalid or already exist")
    sql_text = sql_path.read_text(encoding="utf-8")
    expected_triggers = sorted(set(TRIGGER_RE.findall(sql_text)))
    if len(expected_triggers) != 39:
        raise RuntimeError("ERP authority SQL trigger contract is incomplete")
    connection = sqlite3.connect(source_path, timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        before_counts = preflight(connection)
        before_authority = authority(connection)
        if before_authority not in (None, {"id": 1, "owner": "legacy", "epoch": 1, "cutoverId": ""}):
            raise RuntimeError("D1 ERP authority is no longer legacy-owned")
        backup_database(connection, backup_path)
        connection.execute("BEGIN IMMEDIATE")
        try:
            if preflight(connection) != before_counts:
                raise RuntimeError("D1 ERP changed during authority installation")
            for raw in sql_text.split("--> statement-breakpoint"):
                statement = raw.strip()
                if statement:
                    connection.execute(statement)
            after_authority = authority(connection)
            if after_authority != {"id": 1, "owner": "legacy", "epoch": 1, "cutoverId": ""}:
                raise RuntimeError("authority install changed the legacy owner state")
            installed = sorted(str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'erp_reference_%'"
            ) if str(row[0]) in expected_triggers)
            if installed != expected_triggers:
                raise RuntimeError("ERP authority trigger set is incomplete")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    finally:
        connection.close()
    payload: dict[str, object] = {
        "formatVersion": FORMAT_VERSION, "status": "installed",
        "installedAt": datetime.now(timezone.utc).isoformat(),
        "sourcePathSha256": hashlib.sha256(str(source_path).lower().encode()).hexdigest(),
        "sqlSha256": sha256_file(sql_path), "backupPath": str(backup_path),
        "backupSha256": sha256_file(backup_path), "preflight": before_counts,
        "authority": after_authority, "triggerCount": len(expected_triggers),
    }
    atomic_json(receipt_path, payload)
    payload["receiptPath"] = str(receipt_path)
    payload["receiptSha256"] = sha256_file(receipt_path)
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
        print(json.dumps({"status": "failed", "error": type(error).__name__, "message": str(error)}, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
