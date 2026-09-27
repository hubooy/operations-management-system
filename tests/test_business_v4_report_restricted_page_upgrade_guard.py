"""Pure negative replay of the 0075 test-only archive preflight functions."""
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
SCRIPT = ROOT / "tools/business-v4-report-restricted-page-upgrade-rehearsal.py"
TREE = ast.parse(SCRIPT.read_text(encoding="utf-8"))
FUNCTIONS = {node.name: node for node in TREE.body
    if isinstance(node, ast.FunctionDef)}


def isolated_functions(*names, **bindings):
    code = compile(ast.Module(body=[FUNCTIONS[name] for name in names],
        type_ignores=[]), str(SCRIPT), "exec")
    scope = dict(bindings)
    exec(code, scope)
    return scope


class _Source:
    def __init__(self, tables, nonempty=False):
        self.tables = tables
        self.nonempty = nonempty
        self.calls = 0

    def execute(self, query):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(fetchall=lambda: [(name,) for name in
                self.tables])
        return SimpleNamespace(fetchone=lambda: (self.nonempty,))


class RestrictedPageUpgradeGuardTests(unittest.TestCase):
    def test_nonempty_protected_source_refuses_before_archive(self):
        # Evaluate only the two pure guard functions, never script top-level
        # cluster setup or migration work.
        expected = frozenset({
            "protected_business_budget_v11_verifier_keys",
            "protected_business_budget_v11_proof_tickets",
        })
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "restricted_page_before.dump"
            commands = []
            source = _Source(expected, nonempty=True)

            @contextmanager
            def db():
                yield source

            scope = isolated_functions("assert_empty_protected",
                "archive_restore", PROTECTED_TABLES=expected,
                sql=sql, folder=Path(temporary), db=db,
                native=lambda command: commands.append(command),
                BIN=Path("/synthetic"), database={"NAME": "synthetic"})
            with self.assertRaisesRegex(RuntimeError,
                    "protected table is not empty"):
                scope["archive_restore"]("restricted_page_before")
            self.assertFalse(target.exists())
            self.assertEqual(commands, [])
            source = _Source(expected, nonempty=False)
            scope["archive_restore"]("restricted_page_before")
            self.assertEqual(len(commands), 3)

    def test_formal_backup_refusal_requires_exact_protected_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            expected = hashlib.sha256(
                b"protected AI daily backup is not admitted").hexdigest()

            class Runner:
                def __init__(self, digest):
                    self.digest = digest

                def run(self, command, **kwargs):
                    if "protected-preflight" in command:
                        return SimpleNamespace(returncode=0,
                            stdout=json.dumps({"appliedProtectedMigrations":
                            ["0075_business_v4_report_restricted_page"]
                            }).encode(), stderr=b"")
                    return SimpleNamespace(returncode=1, stdout=b"",
                        stderr=json.dumps({"status": "failed",
                            "errorType": "RuntimeError",
                            "errorSha256": self.digest}).encode())

            def prepared(digest):
                return isolated_functions("assert_formal_backup_closed",
                    os=os,
                    database={"HOST": "127.0.0.1", "PORT": 55793,
                        "USER": "synthetic", "PASSWORD": "unused",
                        "NAME": "synthetic"},
                    ROOT=ROOT, BIN=Path("/synthetic"), folder=folder,
                    NEW=("ai_assistant",
                        "0075_business_v4_report_restricted_page"),
                    sys=SimpleNamespace(executable="python"),
                    subprocess=SimpleNamespace(run=Runner(digest).run,
                        CREATE_NO_WINDOW=0),
                    hashlib=hashlib, json=json)

            with self.assertRaisesRegex(AssertionError,
                    "formal backup did not reject"):
                prepared("0" * 64)["assert_formal_backup_closed"]()
            prepared(expected)["assert_formal_backup_closed"]()
            self.assertFalse((folder /
                "restricted_page_unadmitted_formal.dump").exists())


if __name__ == "__main__":
    unittest.main()
