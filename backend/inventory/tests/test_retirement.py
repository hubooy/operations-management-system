from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from inventory.management.commands.retire_inventory_d1 import (
    REQUIRED_SMOKE_CHECKS,
    RETIREMENT_GUARDS,
    _shared_receipt,
)
from inventory.tests.test_migration import create_source


def add_shared_schema(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE import_content_fingerprints (
              sequence INTEGER NOT NULL, domain TEXT NOT NULL, scope_json TEXT,
              status TEXT, payload TEXT
            );
            CREATE TABLE import_content_attempts (
              sequence INTEGER NOT NULL, domain TEXT NOT NULL, scope_json TEXT,
              outcome TEXT, payload TEXT
            );
            CREATE TABLE inventory_import_uploads (
              id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT, payload TEXT
            );
            CREATE TABLE inventory_import_upload_chunks (
              upload_id TEXT NOT NULL, chunk_index INTEGER NOT NULL, payload TEXT
            );
            CREATE TABLE inventory_import_upload_results (
              upload_id TEXT PRIMARY KEY, payload TEXT
            );
            INSERT INTO import_content_fingerprints VALUES
              (1,'product-shipping-rates','{}','completed','preserve-fingerprint');
            INSERT INTO import_content_attempts VALUES
              (1,'finance','{}','completed','preserve-attempt');
            INSERT INTO import_scope_heads VALUES
              ('finance','finance-scope','5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f','ready','','finance-batch',1,'2026-09-01T00:00:00+00:00');
            INSERT INTO inventory_import_uploads VALUES
              ('product-upload','sku-shipping-rates:preserve','completed','preserve-upload');
            INSERT INTO inventory_import_upload_chunks VALUES
              ('product-upload',0,'preserve-chunk');
            INSERT INTO inventory_import_upload_results VALUES
              ('product-upload','preserve-result');
            INSERT INTO system_settings VALUES
              ('non-inventory-setting','{}','system','2026-09-01T00:00:00+00:00');
            """
        )
        connection.commit()
    finally:
        connection.close()


class InventoryRetirementTests(TestCase):
    def test_terminal_retirement_is_plan_bound_and_preserves_other_domains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "inventory.sqlite"
            smoke_path = root / "inventory-smoke.json"
            r2_path = root / "inventory-r2.json"
            create_source(source)
            add_shared_schema(source)

            planned_output = StringIO()
            call_command(
                "migrate_inventory_from_d1",
                source=str(source),
                mode="plan",
                stdout=planned_output,
            )
            migration_plan = json.loads(planned_output.getvalue())
            applied_output = StringIO()
            call_command(
                "migrate_inventory_from_d1",
                source=str(source),
                mode="apply",
                approve_run_id=migration_plan["runId"],
                stdout=applied_output,
            )
            applied = json.loads(applied_output.getvalue())
            run_id = applied["runId"]
            call_command(
                "migrate_inventory_from_d1",
                source=str(source),
                mode="verify",
                verify_run_id=run_id,
                stdout=StringIO(),
            )
            cutover_id = "inventory-test-retirement"
            for action in ("prepare", "activate"):
                call_command(
                    "inventory_write_authority",
                    source=str(source),
                    **{action: True},
                    approved_run_id=run_id,
                    cutover_id=cutover_id,
                    stdout=StringIO(),
                )

            smoke_path.write_text(
                json.dumps(
                    {
                        "version": "inventory-system-test-receipt-v1",
                        "status": "passed",
                        "cutoverId": cutover_id,
                        "migrationRunId": run_id,
                        "sourceDigest": migration_plan["sourceDigest"],
                        "targetDigest": migration_plan["sourceDigest"],
                        "workerBuildSha256": "9" * 64,
                        "checks": {name: "passed" for name in REQUIRED_SMOKE_CHECKS},
                        "recordedAt": timezone.now().isoformat(),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            r2_path.write_text(
                json.dumps(
                    {
                        "version": "inventory-r2-retirement-evidence-v1",
                        "status": "passed",
                        "prefix": "inventory-upload/",
                        "objectCount": 0,
                        "objectBytes": 0,
                        "multipartUploadCount": 0,
                        "multipartPartCount": 0,
                        "objectsDigest": "8" * 64,
                        "sourcePathSha256": "7" * 64,
                        "recordedAt": timezone.now().isoformat(),
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            common = {
                "source": str(source),
                "cutover_id": cutover_id,
                "approved_run_id": run_id,
                "smoke_receipt": str(smoke_path),
                "r2_evidence": str(r2_path),
            }
            retirement_plan_output = StringIO()
            call_command(
                "retire_inventory_d1", **common, stdout=retirement_plan_output
            )
            retirement_plan = json.loads(retirement_plan_output.getvalue())
            self.assertEqual(retirement_plan["status"], "planned")

            retired_output = StringIO()
            call_command(
                "retire_inventory_d1",
                **common,
                apply=True,
                approved_plan_id=retirement_plan["planId"],
                stdout=retired_output,
            )
            retired = json.loads(retired_output.getvalue())
            self.assertEqual(retired["status"], "retired")

            connection = sqlite3.connect(source)
            connection.row_factory = sqlite3.Row
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM domain_retirement_receipts WHERE domain='inventory'"
                    ).fetchone()[0],
                    "completed",
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM inventory_stock_lines").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM import_content_fingerprints WHERE domain='product-shipping-rates'"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM inventory_import_uploads WHERE fingerprint='sku-shipping-rates:preserve'"
                    ).fetchone()[0],
                    1,
                )
                with self.assertRaisesRegex(sqlite3.DatabaseError, "inventory_domain_retired"):
                    connection.execute(
                        "INSERT INTO system_settings VALUES "
                        "('operating','{}','bad','2026-09-01T00:00:00+00:00')"
                    )
                self.assertEqual(len(RETIREMENT_GUARDS), 24)
                self.assertRegex(str(_shared_receipt(connection)["digest"]), r"^[0-9a-f]{64}$")
            finally:
                connection.close()

            duplicate_output = StringIO()
            call_command(
                "retire_inventory_d1",
                **common,
                apply=True,
                approved_plan_id=retirement_plan["planId"],
                stdout=duplicate_output,
            )
            self.assertEqual(
                json.loads(duplicate_output.getvalue())["status"], "duplicate"
            )
