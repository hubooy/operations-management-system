from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
import sqlite3
import tempfile

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from products.models import (
    ProductInventoryProjection,
    ProductInventoryProjectionControl,
    ProductMigrationRun,
    ProductShippingRate,
    ProductWriteAuthority,
)


def create_source(path: Path, *, batch_status: str = "completed") -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE product_shipping_rate_import_batches (
              id TEXT PRIMARY KEY, source TEXT, file_name TEXT, file_size_bytes INTEGER,
              file_hash TEXT, raw_file_hash TEXT, content_hash TEXT, sheet_name TEXT,
              actor TEXT, status TEXT, source_row_count INTEGER, row_count INTEGER,
              inserted_count INTEGER, updated_count INTEGER, duplicate_count INTEGER,
              warning_count INTEGER, warnings_json TEXT, totals_json TEXT,
              created_at TEXT, completed_at TEXT
            );
            CREATE TABLE product_shipping_rates (
              product_code TEXT PRIMARY KEY, shipping_rate REAL, source_row_number INTEGER,
              last_import_batch_id TEXT
            );
            CREATE TABLE inventory_import_batches (
              id TEXT PRIMARY KEY, snapshot_date TEXT, status TEXT
            );
            CREATE TABLE inventory_stock_lines (
              batch_id TEXT, warehouse TEXT, product_code TEXT, brand TEXT,
              available_quantity INTEGER, unit_cost_cents INTEGER
            );
            CREATE TABLE inventory_import_uploads (
              id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL
            );
            CREATE TABLE inventory_import_upload_chunks (
              upload_id TEXT NOT NULL, chunk_index INTEGER NOT NULL,
              object_key TEXT NOT NULL
            );
            CREATE TABLE inventory_import_upload_results (
              upload_id TEXT PRIMARY KEY, result_json TEXT NOT NULL
            );
            CREATE TABLE import_content_fingerprints (
              sequence INTEGER NOT NULL, domain TEXT NOT NULL, status TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE TABLE import_content_attempts (
              sequence INTEGER NOT NULL, domain TEXT NOT NULL, outcome TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE TABLE import_scope_heads (
              domain TEXT NOT NULL, scope_key TEXT NOT NULL, status TEXT NOT NULL,
              owner_token TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE product_write_authority (
              id INTEGER PRIMARY KEY, owner TEXT NOT NULL, epoch INTEGER NOT NULL,
              cutover_id TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO product_write_authority VALUES (1,'d1',1,'',CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO product_shipping_rate_import_batches VALUES "
            "('batch-1','sku_cumulative','SKU累计.xlsx',1024,?,?,?,'SKU累计','operator@example.test',?,2,2,2,0,0,0,'[]','{}',?,?)",
            (
                "a" * 64,
                "b" * 64,
                "c" * 64,
                batch_status,
                "2026-08-31T10:00:00+08:00",
                "2026-08-31T10:01:00+08:00" if batch_status == "completed" else None,
            ),
        )
        connection.executemany(
            "INSERT INTO product_shipping_rates VALUES (?,?,?,?)",
            [("SKU-A", 0.05, 2, "batch-1"), ("SKU-B", -0.2, 3, "batch-1")],
        )
        connection.execute(
            "INSERT INTO inventory_import_batches VALUES ('inventory-1','2026-08-31','completed')"
        )
        connection.executemany(
            "INSERT INTO inventory_stock_lines VALUES (?,?,?,?,?,?)",
            [
                ("inventory-1", "上海仓", "SKU-A", "品牌 A", 2, 500),
                ("inventory-1", "上海仓", "SKU-B", "品牌 B", 3, 0),
                ("inventory-1", "刷刷仓", "SKU-A", "品牌 A", 99, 500),
            ],
        )
        connection.commit()
    finally:
        connection.close()


class ProductsMigrationTests(TestCase):
    def test_plan_apply_verify_rehearsal_preserves_digest_and_excludes_shuashuacang(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite"
            create_source(source)
            planned_output = StringIO()
            call_command(
                "migrate_products_from_d1",
                source=str(source),
                mode="plan",
                stdout=planned_output,
            )
            planned = json.loads(planned_output.getvalue())
            self.assertEqual(planned["counts"], {
                "attempts": 0, "batches": 1, "fingerprints": 1, "inventory": 2, "rates": 2,
            })

            applied_output = StringIO()
            call_command(
                "migrate_products_from_d1",
                source=str(source),
                mode="apply",
                approve_run_id=planned["runId"],
                stdout=applied_output,
            )
            applied = json.loads(applied_output.getvalue())
            self.assertEqual(applied["targetDigest"], planned["sourceDigest"])
            self.assertEqual(ProductShippingRate.objects.count(), 2)
            self.assertEqual(ProductInventoryProjection.objects.count(), 2)
            sku_a = ProductInventoryProjection.objects.get(product_code="SKU-A")
            self.assertEqual(sku_a.available_quantity, 2)
            self.assertEqual(sku_a.known_stock_value_cents, 1000)
            sku_b = ProductInventoryProjection.objects.get(product_code="SKU-B")
            self.assertEqual(sku_b.known_stock_value_cents, 0)
            self.assertEqual(sku_b.priced_available_quantity, 0)
            control = ProductInventoryProjectionControl.objects.get(id=1)
            self.assertEqual(control.active_total, 2)

            verified_output = StringIO()
            call_command(
                "migrate_products_from_d1",
                source=str(source),
                mode="verify",
                verify_run_id=applied["runId"],
                stdout=verified_output,
            )
            verified = json.loads(verified_output.getvalue())
            self.assertEqual(verified["targetDigest"], planned["sourceDigest"])
            self.assertEqual(ProductMigrationRun.objects.get(id=applied["runId"]).status, "verified")
            self.assertEqual(
                ProductWriteAuthority.objects.get(id=1).migration_verify_run_id,
                applied["runId"],
            )

            cutover_id = "products-test-cutover"
            prepared_output = StringIO()
            call_command(
                "products_write_authority",
                source=str(source),
                prepare=True,
                approved_run_id=applied["runId"],
                cutover_id=cutover_id,
                stdout=prepared_output,
            )
            self.assertEqual(json.loads(prepared_output.getvalue())["status"], "prepared")

            aborted_output = StringIO()
            call_command(
                "products_write_authority",
                source=str(source),
                abort_pending=True,
                approved_run_id=applied["runId"],
                cutover_id=cutover_id,
                stdout=aborted_output,
            )
            self.assertEqual(json.loads(aborted_output.getvalue())["status"], "aborted")

            call_command(
                "products_write_authority",
                source=str(source),
                prepare=True,
                approved_run_id=applied["runId"],
                cutover_id=cutover_id,
                stdout=StringIO(),
            )
            activated_output = StringIO()
            call_command(
                "products_write_authority",
                source=str(source),
                activate=True,
                approved_run_id=applied["runId"],
                cutover_id=cutover_id,
                stdout=activated_output,
            )
            self.assertEqual(json.loads(activated_output.getvalue())["status"], "activated")
            self.assertEqual(ProductWriteAuthority.objects.get(id=1).status, "postgres")
            source_connection = sqlite3.connect(source)
            try:
                self.assertEqual(
                    source_connection.execute(
                        "SELECT owner FROM product_write_authority WHERE id=1"
                    ).fetchone()[0],
                    "postgresql",
                )
            finally:
                source_connection.close()

    def test_plan_fails_closed_while_source_has_processing_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "processing.sqlite"
            create_source(source, batch_status="processing")
            with self.assertRaisesRegex(CommandError, "processing"):
                call_command(
                    "migrate_products_from_d1",
                    source=str(source),
                    mode="plan",
                )

    def test_plan_fails_closed_while_legacy_product_chunk_keys_remain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "product-chunks.sqlite"
            create_source(source)
            connection = sqlite3.connect(source)
            try:
                connection.execute(
                    "INSERT INTO inventory_import_uploads VALUES (?,?)",
                    ("upload-1", "sku-shipping-rates:legacy"),
                )
                connection.execute(
                    "INSERT INTO inventory_import_upload_chunks VALUES (?,?,?)",
                    ("upload-1", 0, "product-upload/chunk-0"),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(CommandError, "authority prepare"):
                call_command(
                    "migrate_products_from_d1",
                    source=str(source),
                    mode="plan",
                )

    def test_terminal_retirement_is_plan_bound_and_preserves_inventory_domain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "products.sqlite"
            smoke_path = root / "system-test-receipt.json"
            create_source(source)

            connection = sqlite3.connect(source)
            try:
                connection.execute(
                    "INSERT INTO import_content_fingerprints VALUES "
                    "(1,'inventory','completed','preserve')"
                )
                connection.execute(
                    "INSERT INTO import_content_attempts VALUES "
                    "(1,'inventory','imported','preserve')"
                )
                connection.execute(
                    "INSERT INTO import_scope_heads VALUES "
                    "('inventory','inventory-scope','ready','','preserve')"
                )
                connection.execute(
                    "INSERT INTO inventory_import_uploads VALUES "
                    "('inventory-upload','inventory:current')"
                )
                connection.execute(
                    "INSERT INTO inventory_import_upload_chunks VALUES "
                    "('inventory-upload',0,'inventory/chunk-0')"
                )
                connection.execute(
                    "INSERT INTO inventory_import_upload_results VALUES "
                    "('inventory-upload','{}')"
                )
                connection.commit()
            finally:
                connection.close()

            planned_output = StringIO()
            call_command(
                "migrate_products_from_d1",
                source=str(source),
                mode="plan",
                stdout=planned_output,
            )
            migration_plan = json.loads(planned_output.getvalue())
            applied_output = StringIO()
            call_command(
                "migrate_products_from_d1",
                source=str(source),
                mode="apply",
                approve_run_id=migration_plan["runId"],
                stdout=applied_output,
            )
            applied = json.loads(applied_output.getvalue())
            run_id = applied["runId"]
            call_command(
                "migrate_products_from_d1",
                source=str(source),
                mode="verify",
                verify_run_id=run_id,
                stdout=StringIO(),
            )
            cutover_id = "products-test-retirement"
            call_command(
                "products_write_authority",
                source=str(source),
                prepare=True,
                approved_run_id=run_id,
                cutover_id=cutover_id,
                stdout=StringIO(),
            )
            call_command(
                "products_write_authority",
                source=str(source),
                activate=True,
                approved_run_id=run_id,
                cutover_id=cutover_id,
                stdout=StringIO(),
            )
            smoke_path.write_text(
                json.dumps(
                    {
                        "version": "products-system-test-receipt-v1",
                        "status": "passed",
                        "cutoverId": cutover_id,
                        "migrationRunId": run_id,
                        "sourceDigest": migration_plan["sourceDigest"],
                        "targetDigest": migration_plan["sourceDigest"],
                        "workerBuildSha256": "9" * 64,
                        "checks": {
                            "djangoReader": "passed",
                            "djangoWriterNegative": "passed",
                            "publicSummary": "passed",
                            "publicShippingImport": "passed",
                            "publicChunkUpload": "passed",
                            "inventoryProjection": "passed",
                            "aiConsumer": "passed",
                            "globalSearchConsumer": "passed",
                            "legacyD1Rejected": "passed",
                            "inventoryD1Preserved": "passed",
                        },
                        "recordedAt": timezone.now().isoformat(),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            common = {
                "source": str(source),
                "cutover_id": cutover_id,
                "approved_run_id": run_id,
                "smoke_receipt": str(smoke_path),
            }
            retirement_plan_output = StringIO()
            call_command(
                "retire_products_d1",
                **common,
                stdout=retirement_plan_output,
            )
            retirement_plan = json.loads(retirement_plan_output.getvalue())
            self.assertEqual(retirement_plan["status"], "planned")

            retired_output = StringIO()
            call_command(
                "retire_products_d1",
                **common,
                apply=True,
                approved_plan_id=retirement_plan["planId"],
                stdout=retired_output,
            )
            retired = json.loads(retired_output.getvalue())
            self.assertEqual(retired["status"], "retired")
            connection = sqlite3.connect(source)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM domain_retirement_receipts "
                        "WHERE domain='products'"
                    ).fetchone()[0],
                    "completed",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM inventory_stock_lines"
                    ).fetchone()[0],
                    3,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM import_content_fingerprints "
                        "WHERE domain='inventory'"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM inventory_import_uploads "
                        "WHERE fingerprint='inventory:current'"
                    ).fetchone()[0],
                    1,
                )
                with self.assertRaisesRegex(sqlite3.DatabaseError, "product_domain_retired"):
                    connection.execute(
                        "INSERT INTO import_scope_heads VALUES "
                        "('product-shipping-rates','reanimate','ready','','bad')"
                    )
            finally:
                connection.close()

            duplicate_output = StringIO()
            call_command(
                "retire_products_d1",
                **common,
                apply=True,
                approved_plan_id=retirement_plan["planId"],
                stdout=duplicate_output,
            )
            self.assertEqual(json.loads(duplicate_output.getvalue())["status"], "duplicate")
