"""Create a consistent, read-only D1 customer-service migration snapshot."""

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


FORMAT_VERSION = "customer-service-d1-snapshot-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.close(descriptor)
        temporary = Path(temporary_name)
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def snapshot(source_path: Path, output_path: Path, manifest_path: Path) -> dict[str, object]:
    if not source_path.is_file() or source_path.is_symlink() or source_path.suffix.lower() not in {".sqlite", ".sqlite3"}:
        raise RuntimeError("source must be an ordinary SQLite file")
    if output_path.exists() or output_path.is_symlink() or manifest_path.exists() or manifest_path.is_symlink():
        raise RuntimeError("snapshot output already exists")
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        authority_row = source.execute(
            "SELECT id,owner,epoch,cutover_id FROM customer_service_write_authority WHERE id=1"
        ).fetchone()
        if authority_row is None or dict(authority_row) != {"id": 1, "owner": "legacy", "epoch": 1, "cutover_id": ""}:
            raise RuntimeError("D1 customer-service authority is not the frozen legacy state")
        counts = {
            "batches": int(source.execute("SELECT COUNT(*) FROM customer_service_import_batches").fetchone()[0]),
            "conversations": int(source.execute("SELECT COUNT(*) FROM customer_service_conversations").fetchone()[0]),
            "versions": int(source.execute("SELECT COUNT(*) FROM customer_service_conversation_versions").fetchone()[0]),
            "audits": int(source.execute("SELECT COUNT(*) FROM customer_service_deletion_audits").fetchone()[0]),
            "fingerprints": int(source.execute("SELECT COUNT(*) FROM import_content_fingerprints WHERE domain='customer-service'").fetchone()[0]),
            "attempts": int(source.execute("SELECT COUNT(*) FROM import_content_attempts WHERE domain='customer-service'").fetchone()[0]),
            "heads": int(source.execute("SELECT COUNT(*) FROM import_scope_heads WHERE domain='customer-service'").fetchone()[0]),
        }
        source.execute("BEGIN")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        destination = sqlite3.connect(output_path)
        try:
            source.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0].lower() != "ok":
                raise RuntimeError("customer-service snapshot integrity check failed")
        finally:
            destination.close()
    finally:
        source.close()
    payload: dict[str, object] = {
        "formatVersion": FORMAT_VERSION,
        "status": "created",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourcePathSha256": hashlib.sha256(str(source_path).lower().encode("utf-8")).hexdigest(),
        "outputSha256": sha256_file(output_path),
        "authority": {"owner": "legacy", "epoch": 1, "cutoverId": ""},
        "counts": counts,
    }
    atomic_json(manifest_path, payload)
    payload["manifestPath"] = str(manifest_path)
    payload["manifestSha256"] = sha256_file(manifest_path)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = snapshot(*(value.resolve() for value in (args.source, args.output, args.manifest)))
    except Exception as error:
        print(json.dumps({"status": "failed", "error": type(error).__name__, "message": str(error)}, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
