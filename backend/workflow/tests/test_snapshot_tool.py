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
    "workflow_d1_snapshot", ROOT / "tools" / "workflow-d1-snapshot.py"
)
assert SPEC and SPEC.loader
SNAPSHOT_TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SNAPSHOT_TOOL)


class WorkflowD1SnapshotTests(SimpleTestCase):
    def test_snapshot_is_exact_integral_and_manifest_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.sqlite"
            output = directory / "workflow.sqlite"
            manifest = directory / "workflow-source-manifest.json"
            create_source(source)
            result = SNAPSHOT_TOOL.create_snapshot(source, output, manifest)
            self.assertEqual(result["counts"], {"records": 2, "activities": 2})
            self.assertEqual(result["authority"]["owner"], "legacy")
            self.assertEqual(
                json.loads(manifest.read_text(encoding="utf-8"))["outputSha256"],
                result["outputSha256"],
            )
            copy = sqlite3.connect(f"file:{output.as_posix()}?mode=ro", uri=True)
            try:
                self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(
                    copy.execute(
                        "SELECT COUNT(*) FROM workflow_operation_records WHERE record_type='review'"
                    ).fetchone()[0],
                    0,
                )
            finally:
                copy.close()
