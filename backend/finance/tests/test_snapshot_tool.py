from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from .test_migration import create_source


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "finance_d1_rehearsal_snapshot",
    ROOT / "tools" / "finance_d1_rehearsal_snapshot.py",
)
assert SPEC and SPEC.loader
SNAPSHOT_TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SNAPSHOT_TOOL)


class FinanceD1RehearsalSnapshotTests(SimpleTestCase):
    def test_snapshot_is_finance_only_exact_and_authority_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.sqlite"
            output = directory / "finance.sqlite"
            manifest = directory / "finance.json"
            create_source(source)
            connection = sqlite3.connect(source)
            try:
                connection.execute("CREATE TABLE unrelated_domain (id INTEGER PRIMARY KEY)")
                connection.execute("INSERT INTO unrelated_domain VALUES (1)")
                connection.execute(
                    "INSERT INTO import_content_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        99, "00000000-0000-4000-8000-000000000099", "inventory", "", "",
                        "{}", "e" * 64, "f" * 64, "1" * 64, 1, "other.xlsx", 10,
                        "", "[]", "imported", "", "", "2026-08-30 01:00:00",
                        "2026-08-30 01:00:00",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            result = SNAPSHOT_TOOL.create_snapshot(
                source,
                output,
                ROOT / "drizzle" / "0093_finance_write_authority.sql",
                manifest,
            )
            self.assertEqual(result["counts"]["finance_lines"], 2)
            self.assertTrue(output.is_file())
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["outputSha256"], result["outputSha256"])
            copy = sqlite3.connect(output)
            try:
                tables = {
                    row[0]
                    for row in copy.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                self.assertNotIn("unrelated_domain", tables)
                self.assertEqual(
                    copy.execute(
                        "SELECT COUNT(*) FROM import_content_attempts WHERE domain='inventory'"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    copy.execute(
                        "SELECT owner, epoch FROM finance_write_authority WHERE id=1"
                    ).fetchone(),
                    ("d1", 1),
                )
            finally:
                copy.close()
