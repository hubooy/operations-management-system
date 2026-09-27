#!/usr/bin/env python3
"""Prove that the activated legacy D1 erp-reference writers fail closed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys


def expect_rejected(connection: sqlite3.Connection, sql: str) -> None:
    try:
        connection.execute(sql)
    except sqlite3.DatabaseError as error:
        if "erp_reference_authority_not_legacy" not in str(error):
            raise RuntimeError("legacy D1 write failed for an unexpected reason") from error
        return
    raise RuntimeError("legacy D1 erp-reference write was unexpectedly accepted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args()
    path = args.source.expanduser().resolve()
    try:
        if not path.is_file() or path.is_symlink() or path.suffix.lower() not in {
            ".sqlite",
            ".sqlite3",
        }:
            raise RuntimeError("erp-reference D1 source must be an ordinary SQLite file")
        connection = sqlite3.connect(path, timeout=30, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            authority = connection.execute(
                "SELECT owner,cutover_id FROM erp_reference_write_authority WHERE id=1"
            ).fetchone()
            if authority is None or authority[0] != "postgresql" or not authority[1]:
                raise RuntimeError("erp-reference D1 authority is not activated")
            expect_rejected(
                connection,
                "INSERT INTO erp_reference_import_batches DEFAULT VALUES",
            )
            expect_rejected(
                connection,
                "INSERT INTO inventory_import_uploads "
                "(id,fingerprint,file_name,file_size_bytes,chunk_size_bytes,chunk_count,expires_at) "
                "VALUES ('erp-reference-smoke','erp:products:smoke','smoke.xlsx',1,1,1,'2999-01-01T00:00:00Z')",
            )
            connection.rollback()
        finally:
            connection.close()
        print(
            json.dumps(
                {
                    "status": "passed",
                    "authority": "postgresql",
                    "factsRejected": True,
                    "uploadsRejected": True,
                },
                separators=(",", ":"),
            )
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {"status": "failed", "error": type(error).__name__},
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


