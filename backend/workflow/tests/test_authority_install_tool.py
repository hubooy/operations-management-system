from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile

from django.test import SimpleTestCase

from .test_migration import create_source


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "workflow_d1_authority_install", ROOT / "tools" / "workflow-d1-authority-install.py"
)
assert SPEC and SPEC.loader
INSTALL_TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL_TOOL)


class WorkflowD1AuthorityInstallTests(SimpleTestCase):
    def test_installs_guard_after_verified_backup_without_changing_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.sqlite"
            backup = directory / "before.sqlite"
            receipt = directory / "receipt.json"
            create_source(source, install_authority=False)
            result = INSTALL_TOOL.install(
                source,
                ROOT / "drizzle" / "0103_workflow_launch_write_authority.sql",
                backup,
                receipt,
            )
            self.assertEqual(result["preflight"], {"launchRecords": 2, "launchActivities": 2})
            self.assertEqual(result["triggerCount"], 9)
            self.assertEqual(result["authority"]["owner"], "legacy")
            self.assertTrue(backup.is_file())
            self.assertEqual(
                json.loads(receipt.read_text(encoding="utf-8"))["backupSha256"],
                result["backupSha256"],
            )
            connection = sqlite3.connect(source)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM workflow_operation_records WHERE record_type='launch'"
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                connection.close()
