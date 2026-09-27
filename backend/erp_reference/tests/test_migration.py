from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from erp_reference.models import (
    ErpComboItem,
    ErpProductMaster,
    ErpReferenceImportBatch,
    ErpReferenceMigrationRun,
    ErpReferenceWriteAuthority,
)
from sales.models import SalesDataRevision


def install_source(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE erp_reference_import_batches (
          id TEXT PRIMARY KEY, source_key TEXT, source_label TEXT, file_name TEXT,
          file_size_bytes INTEGER, file_hash TEXT, sheet_name TEXT, snapshot_date TEXT,
          status TEXT, row_count INTEGER, inserted_count INTEGER, updated_count INTEGER,
          excluded_count INTEGER, warning_count INTEGER, warnings_json TEXT,
          totals_json TEXT, created_at TEXT, completed_at TEXT
        );
        CREATE TABLE erp_product_master (
          product_code TEXT PRIMARY KEY, product_name TEXT, brand TEXT, specification TEXT,
          barcode TEXT, category TEXT, supplier TEXT, product_status TEXT,
          source_row_number INTEGER, last_import_batch_id TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE erp_combo_items (
          id INTEGER PRIMARY KEY, parent_code TEXT, parent_name TEXT, child_code TEXT,
          child_name TEXT, child_quantity_milli INTEGER, source_row_number INTEGER,
          last_import_batch_id TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE erp_product_projection_state (
          id INTEGER PRIMARY KEY, erp_revision INTEGER, source_batch_id TEXT,
          row_count INTEGER, content_hash TEXT
        );
        CREATE TABLE erp_reference_projection_source_state (
          id INTEGER PRIMARY KEY, source_epoch TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE erp_reference_projection_outbox (
          event_sequence INTEGER PRIMARY KEY, event_id TEXT, source_epoch TEXT, domain TEXT,
          operation TEXT, scope_json TEXT, source_batch_id TEXT, erp_revision INTEGER,
          row_count INTEGER, content_hash TEXT, canonical_format_version TEXT, created_at TEXT
        );
        CREATE TABLE import_content_fingerprints (
          sequence INTEGER PRIMARY KEY, domain TEXT, batch_id TEXT, scope_key TEXT,
          scope_json TEXT, import_hash TEXT, raw_file_hash TEXT, content_hash TEXT,
          row_count INTEGER, status TEXT, publication_sequence INTEGER, created_at TEXT
        );
        CREATE TABLE import_content_attempts (
          sequence INTEGER PRIMARY KEY, attempt_id TEXT, domain TEXT, batch_id TEXT,
          scope_key TEXT, scope_json TEXT, import_hash TEXT, raw_file_hash TEXT,
          content_hash TEXT, row_count INTEGER, file_name TEXT, file_size_bytes INTEGER,
          actor TEXT, warnings_json TEXT, outcome TEXT, error_code TEXT,
          recovered_from_attempt_id TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE import_scope_heads (
          domain TEXT, scope_key TEXT, state_token TEXT, status TEXT, owner_token TEXT,
          current_batch_id TEXT, generation INTEGER, updated_at TEXT
        );
        CREATE TABLE inventory_import_uploads (
          id TEXT PRIMARY KEY, fingerprint TEXT, file_name TEXT, file_size_bytes INTEGER,
          chunk_size_bytes INTEGER, chunk_count INTEGER, received_chunk_count INTEGER,
          received_bytes INTEGER, status TEXT, expires_at TEXT
        );
        CREATE TABLE inventory_import_upload_chunks (
          upload_id TEXT, chunk_index INTEGER, object_key TEXT, size_bytes INTEGER,
          sha256 TEXT, created_at TEXT
        );
        CREATE TABLE inventory_import_upload_results (
          upload_id TEXT PRIMARY KEY, result_json TEXT, created_at TEXT
        );
        """
    )
    timestamp = "2026-09-05T08:00:00+08:00"
    product_batch = "products:" + "1" * 64
    combo_batch = "combos:" + "2" * 64
    connection.executemany(
        "INSERT INTO erp_reference_import_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (product_batch, "products", "吉客云 ERP · 货品导出", "货品.xlsx", 100,
             "1" * 64, "货品", None, "completed", 1, 1, 0, 0, 0, "[]", "{}", timestamp, timestamp),
            (combo_batch, "combos", "吉客云 ERP · 组合装及子件", "组合装.xlsx", 80,
             "2" * 64, "组合装", None, "completed", 1, 1, 0, 0, 0, "[]", "{}", timestamp, timestamp),
        ],
    )
    connection.execute(
        "INSERT INTO erp_product_master VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("P1", "饮水机", "TERUISI", "标准", "6901", "饮水设备", "供应商A", "在售",
         2, product_batch, timestamp, timestamp),
    )
    connection.execute(
        "INSERT INTO erp_combo_items VALUES (?,?,?,?,?,?,?,?,?,?)",
        (1, "KIT-1", "饮水组合", "P1", "饮水机", 2000, 2, combo_batch, timestamp, timestamp),
    )
    connection.execute(
        "INSERT INTO erp_product_projection_state VALUES (1,7,?,1,?)",
        (product_batch, "a" * 64),
    )
    connection.execute(
        "INSERT INTO erp_reference_projection_source_state VALUES (1,?,?,?)",
        ("a" * 32, timestamp, timestamp),
    )
    for sequence, source, batch_id, digest in (
        (1, "products", product_batch, "1" * 64),
        (2, "combos", combo_batch, "2" * 64),
    ):
        scope_json = json.dumps({"source": source, "snapshotDate": None}, separators=(",", ":"))
        connection.execute(
            "INSERT INTO import_content_fingerprints VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (sequence, "erp-reference", batch_id, digest, scope_json, digest, digest,
             digest, 1, "completed", sequence, timestamp),
        )
        connection.execute(
            "INSERT INTO import_content_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sequence, f"00000000-0000-4000-8000-00000000000{sequence}", "erp-reference",
             batch_id, digest, scope_json, digest, digest, digest, 1,
             f"{source}.xlsx", 100, "admin@example.test", "[]", "imported", "", "", timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO import_scope_heads VALUES (?,?,?,?,?,?,?,?)",
            ("erp-reference", digest, digest, "ready", "", batch_id, 1, timestamp),
        )
    connection.commit()
    connection.close()


class ErpReferenceMigrationTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.source = Path(self.temporary.name) / "erp.sqlite"
        install_source(self.source)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_command(self, **options) -> dict[str, object]:
        output = StringIO()
        call_command("migrate_erp_reference_from_d1", source=str(self.source), stdout=output, **options)
        return json.loads(output.getvalue())

    def install_authority(self) -> None:
        sql_path = Path(__file__).parents[3] / "drizzle" / "0109_erp_reference_write_authority.sql"
        sql = sql_path.read_text(encoding="utf-8")
        connection = sqlite3.connect(self.source)
        try:
            for raw in sql.split("--> statement-breakpoint"):
                statement = raw.strip()
                if statement:
                    connection.execute(statement)
            connection.commit()
        finally:
            connection.close()

    def test_plan_apply_verify_migrates_products_combos_and_history(self) -> None:
        plan = self.run_command(mode="plan")
        applied = self.run_command(mode="apply", approve_run_id=plan["runId"])
        self.assertEqual(applied["counts"], {
            "products": 1, "combos": 1, "batches": 2,
            "fingerprints": 2, "attempts": 2, "heads": 2,
        })
        verified = self.run_command(mode="verify", verify_run_id=applied["runId"])
        self.assertEqual(verified["targetDigest"], plan["sourceDigest"])
        self.assertEqual(ErpProductMaster.objects.get().category, "饮水设备")
        self.assertEqual(ErpComboItem.objects.get().child_quantity_milli, 2000)
        self.assertEqual(ErpReferenceImportBatch.objects.count(), 2)
        self.assertEqual(SalesDataRevision.objects.get(domain="erp").revision, 7)
        self.assertEqual(
            ErpReferenceWriteAuthority.objects.get(id=1).migration_verify_run_id,
            applied["runId"],
        )
        self.assertEqual(ErpReferenceMigrationRun.objects.get(id=applied["runId"]).status, "verified")

    def test_apply_adopts_only_an_exact_existing_bridge_projection(self) -> None:
        ErpProductMaster.objects.create(
            product_code="WRONG", product_name="错误货品", brand="", specification="",
            barcode="", category="", supplier="", product_status="", source_row_number=1,
            last_import_batch_id="wrong", created_at="2026-09-05", updated_at="2026-09-05",
        )
        plan = self.run_command(mode="plan")
        with self.assertRaisesRegex(CommandError, "bridge 货品投影与 D1 不一致"):
            self.run_command(mode="apply", approve_run_id=plan["runId"])
        self.assertEqual(list(ErpProductMaster.objects.values_list("product_code", flat=True)), ["WRONG"])

    def test_authority_prepare_and_activate_fence_d1_writes(self) -> None:
        self.install_authority()
        plan = self.run_command(mode="plan")
        applied = self.run_command(mode="apply", approve_run_id=plan["runId"])
        self.run_command(mode="verify", verify_run_id=applied["runId"])
        cutover = "erp-reference-test-cutover"
        call_command(
            "erp_reference_write_authority", source=str(self.source), prepare=True,
            approved_run_id=applied["runId"], cutover_id=cutover,
        )
        connection = sqlite3.connect(self.source)
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "erp_reference_authority_not_legacy"):
                connection.execute(
                    "INSERT INTO erp_combo_items VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (2, "KIT-2", "错误组合", "P1", "饮水机", 1000, 2,
                     "combos:" + "3" * 64, "2026-09-05", "2026-09-05"),
                )
        finally:
            connection.close()
        call_command(
            "erp_reference_write_authority", source=str(self.source), activate=True,
            approved_run_id=applied["runId"], cutover_id=cutover,
        )
        self.assertEqual(ErpReferenceWriteAuthority.objects.get(id=1).status, "postgres")
        connection = sqlite3.connect(self.source)
        try:
            row = connection.execute(
                "SELECT owner,cutover_id FROM erp_reference_write_authority WHERE id=1"
            ).fetchone()
            self.assertEqual(row, ("postgresql", cutover))
        finally:
            connection.close()

    def test_terminal_retirement_replaces_d1_tables_with_guarded_tombstones(self) -> None:
        self.install_authority()
        plan = self.run_command(mode="plan")
        applied = self.run_command(mode="apply", approve_run_id=plan["runId"])
        self.run_command(mode="verify", verify_run_id=applied["runId"])
        cutover = "erp-reference-test-cutover"
        call_command(
            "erp_reference_write_authority", source=str(self.source), prepare=True,
            approved_run_id=applied["runId"], cutover_id=cutover,
        )
        call_command(
            "erp_reference_write_authority", source=str(self.source), activate=True,
            approved_run_id=applied["runId"], cutover_id=cutover,
        )
        sql_path = Path(__file__).parents[3] / "drizzle" / "0110_erp_reference_domain_retirement.sql"
        statements = [
            raw.strip()
            for raw in sql_path.read_text(encoding="utf-8").split("--> statement-breakpoint")
            if raw.strip()
        ]
        connection = sqlite3.connect(self.source, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            receipt_installed = False
            for statement in statements:
                if statement.startswith("SELECT CASE WHEN") and not receipt_installed:
                    connection.execute(
                        "INSERT INTO domain_retirement_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                        (
                            "erp-reference", "erp-reference-domain-retirement-receipt-v1",
                            "approved", cutover, "1" * 64, "2" * 64, "3" * 64,
                            "4" * 64, "5" * 64, "6" * 64, "7" * 64,
                            "2026-09-05T08:30:00Z",
                        ),
                    )
                    receipt_installed = True
                connection.execute(statement)
            connection.commit()
            for name in (
                "erp_reference_import_batches", "erp_product_master", "erp_combo_items",
                "erp_reference_projection_source_state", "erp_product_projection_state",
                "erp_reference_projection_outbox", "erp_reference_write_authority",
            ):
                self.assertEqual(
                    connection.execute(
                        "SELECT type FROM sqlite_master WHERE name=?", (name,)
                    ).fetchone(),
                    ("view",),
                )
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0], 0)
            self.assertEqual(
                connection.execute(
                    "SELECT status FROM domain_retirement_receipts WHERE domain='erp-reference'"
                ).fetchone(),
                ("completed",),
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "erp_reference_domain_retired"):
                connection.execute(
                    "INSERT INTO import_scope_heads VALUES (?,?,?,?,?,?,?,?)",
                    ("erp-reference", "x" * 64, "y" * 64, "ready", "", "", 0, "2026-09-05"),
                )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def test_retirement_command_binds_recent_smoke_and_preserves_other_shared_rows(self) -> None:
        self.install_authority()
        plan = self.run_command(mode="plan")
        applied = self.run_command(mode="apply", approve_run_id=plan["runId"])
        self.run_command(mode="verify", verify_run_id=applied["runId"])
        cutover = "erp-reference-command-cutover"
        call_command(
            "erp_reference_write_authority", source=str(self.source), prepare=True,
            approved_run_id=applied["runId"], cutover_id=cutover,
        )
        call_command(
            "erp_reference_write_authority", source=str(self.source), activate=True,
            approved_run_id=applied["runId"], cutover_id=cutover,
        )
        connection = sqlite3.connect(self.source)
        connection.execute(
            "INSERT INTO import_scope_heads VALUES (?,?,?,?,?,?,?,?)",
            ("unrelated-domain", "scope", "token", "ready", "", "batch", 3, "2026-09-05"),
        )
        connection.commit()
        connection.close()
        smoke = Path(self.temporary.name) / "smoke.json"
        r2 = Path(self.temporary.name) / "r2.json"
        smoke.write_text(json.dumps({
            "version": "erp-reference-system-test-receipt-v1",
            "status": "passed",
            "cutoverId": cutover,
            "migrationRunId": applied["runId"],
            "sourceDigest": plan["sourceDigest"],
            "targetDigest": plan["sourceDigest"],
            "workerBuildSha256": "a" * 64,
            "checks": {
                "djangoReader": "passed", "djangoWriterNegative": "passed",
                "publicImportHistory": "passed", "publicDirectImport": "passed",
                "publicChunkUpload": "passed", "globalSearchConsumer": "passed",
                "aiConsumer": "passed", "legacyD1Rejected": "passed",
                "legacyR2Rejected": "passed", "otherDomainsPreserved": "passed",
            },
            "recordedAt": timezone.now().isoformat(),
        }), encoding="utf-8")
        r2.write_text(json.dumps({
            "version": "erp-reference-r2-retirement-evidence-v1",
            "status": "passed", "prefix": "inventory-upload/",
            "objectCount": 0, "objectBytes": 0,
            "multipartUploadCount": 0, "multipartPartCount": 0,
            "objectsDigest": "b" * 64, "sourcePathSha256": "c" * 64,
            "recordedAt": timezone.now().isoformat(),
        }), encoding="utf-8")
        planned_output = StringIO()
        call_command(
            "retire_erp_reference_d1", source=str(self.source), cutover_id=cutover,
            approved_run_id=applied["runId"], smoke_receipt=str(smoke),
            r2_evidence=str(r2), stdout=planned_output,
        )
        retirement = json.loads(planned_output.getvalue())
        applied_output = StringIO()
        call_command(
            "retire_erp_reference_d1", source=str(self.source), cutover_id=cutover,
            approved_run_id=applied["runId"], smoke_receipt=str(smoke),
            r2_evidence=str(r2), apply=True,
            approved_plan_id=retirement["planId"], stdout=applied_output,
        )
        self.assertEqual(json.loads(applied_output.getvalue())["status"], "retired")
        connection = sqlite3.connect(self.source)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT generation FROM import_scope_heads WHERE domain='unrelated-domain'"
                ).fetchone(),
                (3,),
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM erp_product_master").fetchone(),
                (0,),
            )
        finally:
            connection.close()
