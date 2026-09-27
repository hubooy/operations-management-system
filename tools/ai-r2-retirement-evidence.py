"""Read-only proof of an empty AI namespace in the local shared R2 bucket.

This operator never deletes objects or changes Miniflare metadata. A nonempty
namespace requires a separate reviewed migration, not a cleanup fallback.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import closing

PREFIX = "ai-space/"


def collect(root):
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Ordinary local R2 metadata directory required")
    candidates = []
    for path in sorted(root.glob("*.sqlite")):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Invalid R2 metadata file")
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='_mf_objects'").fetchone():
                candidates.append(path)
    if len(candidates) != 1:
        raise ValueError("Missing or ambiguous R2 object database")
    db = sqlite3.connect(candidates[0].resolve().as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        count, size = db.execute(
            "SELECT count(*),coalesce(sum(size),0) FROM _mf_objects WHERE substr(key,1,?)=?",
            (len(PREFIX), PREFIX),
        ).fetchone()
        uploads = db.execute(
            "SELECT count(*) FROM _mf_multipart_uploads WHERE substr(key,1,?)=?",
            (len(PREFIX), PREFIX),
        ).fetchone()[0]
        parts = db.execute(
            "SELECT count(*) FROM _mf_multipart_parts WHERE substr(object_key,1,?)=?",
            (len(PREFIX), PREFIX),
        ).fetchone()[0]
        if any((count, size, uploads, parts)):
            raise ValueError("AI R2 namespace is not empty; no mutation performed")
        preserved = hashlib.sha256()
        preserved_count = 0
        for row in db.execute("SELECT key,size,etag,version,blob_id FROM _mf_objects ORDER BY key"):
            preserved.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode())
            preserved.update(b"\n")
            preserved_count += 1
        return {
            "version": "ai-r2-retirement-evidence-v1", "status": "passed",
            "prefix": PREFIX, "objectCount": count, "objectBytes": size,
            "multipartUploadCount": uploads, "multipartPartCount": parts,
            "preservedObjectCount": preserved_count,
            "preservedMetadataDigest": preserved.hexdigest(),
            "productionWrites": False,
            "recordedAt": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r2-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = collect(args.r2_root)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
        out.write("\n")
    print(json.dumps(result, ensure_ascii=False))
