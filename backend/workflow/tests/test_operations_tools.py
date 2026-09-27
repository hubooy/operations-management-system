from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile

from django.test import SimpleTestCase

from .test_operations_migration import SCHEMA


ROOT = Path(__file__).resolve().parents[3]


def _load_tool(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSTALL_TOOL = _load_tool(
    "workflow_operations_d1_authority_install",
    ROOT / "tools" / "workflow-operations-d1-authority-install.py",
)
SNAPSHOT_TOOL = _load_tool(
    "workflow_operations_d1_snapshot",
    ROOT / "tools" / "workflow-operations-d1-snapshot.py",
)


def create_source(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA)
        stamp = "2026-09-03 00:00:00"
        connection.execute(
            "INSERT INTO workflow_tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "task-1", "工具测试", "", "工作计划", "运营组", "", "2026-09-03",
                "2026-09-04", "待开始", "normal", "operator@example.test",
                "operator@example.test", stamp, stamp,
            ),
        )
        connection.execute(
            "INSERT INTO workflow_task_states VALUES (?,?,?,?,?)",
            ("task-1", 1, "", None, ""),
        )
        connection.commit()
    finally:
        connection.close()


class WorkflowOperationsToolsTests(SimpleTestCase):
    def test_authority_installer_creates_verified_backup_and_all_guards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.sqlite"
            backup = directory / "before.sqlite"
            receipt = directory / "receipt.json"
            create_source(source)
            result = INSTALL_TOOL.install(
                source,
                ROOT / "drizzle" / "0105_workflow_operations_write_authority.sql",
                backup,
                receipt,
            )
            self.assertEqual(result["triggerCount"], 42)
            self.assertEqual(result["authority"]["owner"], "legacy")
            self.assertEqual(result["preflight"]["tasks"], 1)
            self.assertTrue(backup.is_file())
            self.assertEqual(
                json.loads(receipt.read_text(encoding="utf-8"))["backupSha256"],
                result["backupSha256"],
            )
            source_db = sqlite3.connect(source)
            backup_db = sqlite3.connect(backup)
            try:
                self.assertEqual(source_db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(backup_db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertIsNone(backup_db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='workflow_operations_write_authority'"
                ).fetchone())
            finally:
                source_db.close()
                backup_db.close()

    def test_snapshot_is_integral_and_bound_to_legacy_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.sqlite"
            backup = directory / "before.sqlite"
            receipt = directory / "authority.json"
            output = directory / "workflow.sqlite"
            manifest = directory / "workflow-source-manifest.json"
            create_source(source)
            INSTALL_TOOL.install(
                source,
                ROOT / "drizzle" / "0105_workflow_operations_write_authority.sql",
                backup,
                receipt,
            )
            result = SNAPSHOT_TOOL.create_snapshot(source, output, manifest)
            self.assertEqual(result["counts"]["tasks"], 1)
            self.assertEqual(result["authority"]["owner"], "legacy")
            self.assertEqual(
                json.loads(manifest.read_text(encoding="utf-8"))["outputSha256"],
                result["outputSha256"],
            )
            copy = sqlite3.connect(f"file:{output.as_posix()}?mode=ro", uri=True)
            try:
                self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(copy.execute("SELECT COUNT(*) FROM workflow_tasks").fetchone()[0], 1)
                self.assertEqual(
                    copy.execute(
                        "SELECT owner FROM workflow_operations_write_authority WHERE id=1"
                    ).fetchone()[0],
                    "legacy",
                )
            finally:
                copy.close()
