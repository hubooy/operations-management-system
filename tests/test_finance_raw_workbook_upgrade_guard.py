"""Pure prearchive negative for the finance.0006 isolated restore script."""
from __future__ import annotations

import ast
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/finance-raw-workbook-upgrade-rehearsal.py"
FUNCTIONS = {item.name: item for item in ast.parse(
    SCRIPT.read_text(encoding="utf-8")).body
    if isinstance(item, ast.FunctionDef)}


def isolated(*names, **bindings):
    scope = dict(bindings)
    exec(compile(ast.Module(body=[FUNCTIONS[name] for name in names],
        type_ignores=[]), str(SCRIPT), "exec"), scope)
    return scope


class Source:
    def __init__(self, tables, *, nonempty):
        self.tables, self.nonempty = tables, nonempty
        self.calls = 0

    def execute(self, _query, _params=None):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(fetchall=lambda: [(name,) for name in
                self.tables])
        return SimpleNamespace(fetchone=lambda: (self.nonempty,))


class FinanceRawWorkbookArchiveGuardTests(unittest.TestCase):
    def test_nonempty_or_partial_sidecar_never_invokes_pg_dump(self):
        tables = frozenset({"finance_raw_column_evidence_months",
            "finance_raw_column_evidence_columns",
            "finance_raw_column_evidence_cells"})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            source = Source(tables, nonempty=True)

            @contextmanager
            def db():
                yield source

            calls = []
            scope = isolated("assert_empty_sidecars", "archive_restore",
                SIDE_TABLES=tables, sql=sql, folder=path, db=db,
                native=lambda command: calls.append(command),
                BIN=Path("/synthetic"), database={"NAME": "synthetic"})
            with self.assertRaisesRegex(RuntimeError,
                    "contains source facts"):
                scope["archive_restore"]("finance_bytes_before", tables)
            self.assertEqual(calls, [])
            self.assertFalse((path / "finance_bytes_before.dump").exists())
            source = Source({"finance_raw_column_evidence_months"},
                nonempty=False)
            with self.assertRaisesRegex(RuntimeError,
                    "inventory drift"):
                scope["archive_restore"]("finance_bytes_before", tables)
            self.assertEqual(calls, [])

    def test_formal_backup_and_restore_need_exact_reason_and_toc(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            after = folder / "finance_bytes_after.dump"
            after.write_bytes(b"synthetic archive")
            names = ("finance_raw_workbook_attestations",
                "finance_raw_workbook_columns",
                "finance_raw_workbook_cells")
            backup_digest = hashlib.sha256(
                b"finance raw workbook daily backup is not admitted"
                ).hexdigest()
            restore_digest = hashlib.sha256(
                b"finance raw evidence archive restore is not admitted"
                ).hexdigest()

            class Runner:
                def __init__(self, digest):
                    self.digest = digest

                def run(self, command, **_kwargs):
                    if "--list" in command:
                        return SimpleNamespace(returncode=0,
                            stdout=" ".join(names).encode(), stderr=b"")
                    if "backup" in command:
                        reason = self.digest
                    else:
                        reason = restore_digest
                    return SimpleNamespace(returncode=1, stdout=b"",
                        stderr=json.dumps({"status": "failed",
                            "errorType": "RuntimeError",
                            "errorSha256": reason}).encode())

            def prepared(digest):
                return isolated("formal_preflight", folder=folder,
                    database={"HOST": "127.0.0.1", "PORT": 55796,
                        "USER": "synthetic", "PASSWORD": "unused",
                        "NAME": "synthetic"},
                    ROOT=ROOT, BIN=Path("/synthetic"),
                    sys=SimpleNamespace(executable="python"),
                    subprocess=SimpleNamespace(run=Runner(digest).run,
                        CREATE_NO_WINDOW=0),
                    hashlib=hashlib, json=json, os=os,
                    new=SimpleNamespace(TABLES=names))

            with self.assertRaisesRegex(AssertionError,
                    "formal backup did not fail first"):
                prepared("0" * 64)["formal_preflight"](after)
            prepared(backup_digest)["formal_preflight"](after)


if __name__ == "__main__":
    unittest.main()
