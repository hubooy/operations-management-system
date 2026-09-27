from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile

from django.core.management import call_command
from django.test import TestCase

from inventory.models import (
    InventoryAgeLine,
    InventoryDataRevision,
    InventoryImportBatch,
    InventoryMigrationRun,
    InventoryOperatingSettings,
    InventoryStockLine,
    InventoryWriteAuthority,
    ReplenishmentPlanItem,
)


def create_source(path: Path, *, with_superseded_stock_batch: bool = False) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE inventory_import_batches (
              id TEXT PRIMARY KEY, source TEXT, file_name TEXT, file_size_bytes INTEGER,
              file_hash TEXT, sheet_name TEXT, snapshot_date TEXT, actor TEXT,
              status TEXT, row_count INTEGER, warning_count INTEGER,
              warnings_json TEXT, totals_json TEXT, created_at TEXT, completed_at TEXT
            );
            CREATE TABLE inventory_stock_lines (
              id INTEGER PRIMARY KEY, batch_id TEXT, row_key TEXT, source_row_number INTEGER,
              snapshot_date TEXT, warehouse TEXT, warehouse_type TEXT, product_code TEXT,
              product_name TEXT, brand TEXT, specification TEXT, barcode TEXT, category TEXT,
              on_hand_quantity INTEGER, available_quantity INTEGER, locked_quantity INTEGER,
              in_transit_quantity INTEGER, unit_cost_cents INTEGER, inventory_age_days INTEGER
            );
            CREATE TABLE inventory_age_metrics (
              batch_id TEXT, row_key TEXT, sales_7d_quantity INTEGER, sales_30d_quantity INTEGER
            );
            CREATE TABLE erp_reference_import_batches (
              id TEXT PRIMARY KEY, source_key TEXT, source_label TEXT, file_name TEXT,
              file_size_bytes INTEGER, file_hash TEXT, sheet_name TEXT, snapshot_date TEXT,
              actor TEXT, status TEXT, row_count INTEGER, warning_count INTEGER,
              warnings_json TEXT, totals_json TEXT, created_at TEXT, completed_at TEXT
            );
            CREATE TABLE erp_inventory_age_lines (
              id INTEGER PRIMARY KEY, last_import_batch_id TEXT, source_row_number INTEGER,
              snapshot_date TEXT, warehouse TEXT, warehouse_type TEXT, product_code TEXT,
              product_name TEXT, specification TEXT, category TEXT, available_quantity INTEGER,
              inventory_age_days INTEGER, sales_7d_quantity INTEGER, sales_30d_quantity INTEGER,
              unit_cost_cents INTEGER, stock_value_cents INTEGER
            );
            CREATE TABLE replenishment_plan_items (
              id TEXT PRIMARY KEY, source_batch_id TEXT, product_code TEXT, product_name TEXT,
              warehouse TEXT, suggested_quantity INTEGER, planned_quantity INTEGER,
              coverage_days_tenths INTEGER, reason TEXT, status TEXT,
              created_at TEXT, updated_at TEXT
            );
            CREATE TABLE system_settings (
              key TEXT PRIMARY KEY, value_json TEXT, updated_by TEXT, updated_at TEXT
            );
            CREATE TABLE inventory_write_authority (
              id INTEGER PRIMARY KEY, owner TEXT, epoch INTEGER, cutover_id TEXT, updated_at TEXT
            );
            CREATE TABLE import_scope_heads (
              domain TEXT, scope_key TEXT, state_token TEXT, status TEXT,
              owner_token TEXT, current_batch_id TEXT, generation INTEGER, updated_at TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO inventory_import_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "stock-batch-1", "inventory_stock", "库存.xlsx", 4096, "a" * 64,
                "分仓库存", "2026-09-01", "admin@example.test", "completed", 2, 0,
                "[]", json.dumps({"sourceRowCount": 2, "rawFileHash": "b" * 64, "contentHash": "c" * 64}),
                "2026-09-01T01:00:00+00:00", "2026-09-01T01:01:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO inventory_stock_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1, "stock-batch-1", "华东仓\x1fP1", 2, "2026-09-01", "华东仓",
                "owned", "P1", "货品一", "品牌甲", "标准装", "BAR-P1", "厨房电器",
                12, 10, 2, 3, 500, 20,
            ),
        )
        if with_superseded_stock_batch:
            connection.execute(
                "INSERT INTO inventory_import_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "stock-batch-2", "inventory_stock", "库存重导.xlsx", 4097, "9" * 64,
                    "分仓库存", "2026-09-01", "admin@example.test", "completed", 1, 0,
                    "[]", json.dumps({"sourceRowCount": 1, "rawFileHash": "8" * 64, "contentHash": "7" * 64}),
                    "2026-09-01T01:02:00+00:00", "2026-09-01T01:03:00+00:00",
                ),
            )
            connection.execute(
                "INSERT INTO inventory_stock_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    3, "stock-batch-2", "华东仓\x1fP1", 2, "2026-09-01", "华东仓",
                    "owned", "P1", "货品一", "品牌甲", "标准装", "BAR-P1", "厨房电器",
                    10, 8, 2, 1, 500, 21,
                ),
            )
        connection.execute(
            "INSERT INTO inventory_age_metrics VALUES (?,?,?,?)",
            ("stock-batch-1", "华东仓\x1fP1", 4, 18),
        )
        connection.execute(
            "INSERT INTO inventory_stock_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                2, "stock-batch-1", "刷刷仓\x1fP2", 3, "2026-09-01", "刷刷仓",
                "excluded", "P2", "不应迁移货品", "品牌乙", "标准装", "BAR-P2", "厨房电器",
                1, 1, 0, 0, 500, 1,
            ),
        )
        connection.execute(
            "INSERT INTO erp_reference_import_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "age-batch-1", "inventory_age", "吉客云 ERP · 库龄分析", "库龄.xlsx",
                2048, "d" * 64, "库龄分析", "2026-09-01", "admin@example.test",
                "completed", 1, 0, "[]",
                json.dumps({"sourceRowCount": 1, "rawFileHash": "e" * 64, "contentHash": "f" * 64}),
                "2026-09-01T02:00:00+00:00", "2026-09-01T02:01:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO erp_inventory_age_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1, "age-batch-1", 2, "2026-09-01", "华东仓", "owned", "P1",
                "货品一", "标准装", "厨房电器", 10, 20, 4, 18, 500, 5000,
            ),
        )
        connection.execute(
            "INSERT INTO replenishment_plan_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "plan-1", "stock-batch-1", "P1", "货品一", "华东仓", 20, 18,
                50, "库存告急", "confirmed", "2026-09-01T03:00:00+00:00",
                "2026-09-01T03:01:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO system_settings VALUES (?,?,?,?)",
            (
                "operating",
                json.dumps({
                    "targetDays": 35,
                    "criticalDays": 8,
                    "slowDays": 50,
                    "stagnantDays": 100,
                    "autoReplenishment": True,
                    "inventoryAlert": True,
                    "allowNegativeInventory": False,
                }),
                "admin@example.test",
                "2026-09-01T04:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO inventory_write_authority VALUES (1,'d1',1,'','2026-09-01T00:00:00+00:00')"
        )
        current_stock_batch = "stock-batch-2" if with_superseded_stock_batch else "stock-batch-1"
        connection.execute(
            "INSERT INTO import_scope_heads VALUES (?,?,?,?,?,?,?,?)",
            (
                "inventory-stock", "1" * 64, "3" * 64, "ready", "",
                current_stock_batch, 2 if with_superseded_stock_batch else 1,
                "2026-09-01T04:01:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO import_scope_heads VALUES (?,?,?,?,?,?,?,?)",
            (
                "erp-reference", "2" * 64, "4" * 64, "ready", "",
                "age-batch-1", 1, "2026-09-01T04:01:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()


class InventoryMigrationTests(TestCase):
    def test_plan_apply_verify_and_authority_rehearsal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "inventory-source.sqlite"
            create_source(source)

            planned_output = StringIO()
            call_command(
                "migrate_inventory_from_d1",
                source=str(source),
                mode="plan",
                stdout=planned_output,
            )
            planned = json.loads(planned_output.getvalue())
            self.assertEqual(
                planned["counts"],
                {"age": 1, "attempts": 0, "batches": 2, "fingerprints": 0, "plans": 1, "stock": 1},
            )
            self.assertEqual(
                planned["exclusions"],
                {"brushWarehouseRows": 1, "supersededStockRows": 0},
            )

            applied_output = StringIO()
            call_command(
                "migrate_inventory_from_d1",
                source=str(source),
                mode="apply",
                approve_run_id=planned["runId"],
                stdout=applied_output,
            )
            applied = json.loads(applied_output.getvalue())
            self.assertEqual(applied["targetDigest"], planned["sourceDigest"])
            self.assertEqual(InventoryImportBatch.objects.count(), 2)
            self.assertEqual(InventoryStockLine.objects.get().product_code, "P1")
            stock_batch = InventoryImportBatch.objects.get(id="stock-batch-1")
            self.assertEqual(stock_batch.row_count, 1)
            self.assertEqual(stock_batch.excluded_count, 1)
            self.assertEqual(stock_batch.totals_json["migrationExcludedBrushWarehouseRows"], 1)
            self.assertEqual(InventoryAgeLine.objects.get().stock_value_cents, 5000)
            self.assertEqual(ReplenishmentPlanItem.objects.get().status, "confirmed")
            self.assertEqual(InventoryOperatingSettings.objects.get(id=1).target_days, 35)
            self.assertEqual(
                InventoryDataRevision.objects.get(domain="inventory").source_digest,
                planned["sourceDigest"],
            )

            verified_output = StringIO()
            call_command(
                "migrate_inventory_from_d1",
                source=str(source),
                mode="verify",
                verify_run_id=applied["runId"],
                stdout=verified_output,
            )
            verified = json.loads(verified_output.getvalue())
            self.assertEqual(verified["targetDigest"], planned["sourceDigest"])
            self.assertEqual(
                InventoryMigrationRun.objects.get(id=applied["runId"]).status,
                "verified",
            )

            cutover_id = "inventory-test-cutover"
            for action in ("prepare", "activate"):
                output = StringIO()
                call_command(
                    "inventory_write_authority",
                    source=str(source),
                    **{action: True},
                    approved_run_id=applied["runId"],
                    cutover_id=cutover_id,
                    stdout=output,
                )
                self.assertEqual(json.loads(output.getvalue())["status"], "prepared" if action == "prepare" else "activated")

            authority = InventoryWriteAuthority.objects.get(id=1)
            self.assertEqual(authority.status, "postgres")
            self.assertEqual(authority.cutover_id, cutover_id)
            source_db = sqlite3.connect(source)
            try:
                owner = source_db.execute(
                    "SELECT owner FROM inventory_write_authority WHERE id=1"
                ).fetchone()[0]
            finally:
                source_db.close()
            self.assertEqual(owner, "postgresql")

    def test_migration_keeps_only_the_latest_d1_stock_batch_per_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "inventory-source.sqlite"
            create_source(source, with_superseded_stock_batch=True)

            planned_output = StringIO()
            call_command(
                "migrate_inventory_from_d1",
                source=str(source),
                mode="plan",
                stdout=planned_output,
            )
            planned = json.loads(planned_output.getvalue())
            self.assertEqual(planned["counts"]["stock"], 1)
            self.assertEqual(
                planned["exclusions"],
                {"brushWarehouseRows": 0, "supersededStockRows": 2},
            )

            applied_output = StringIO()
            call_command(
                "migrate_inventory_from_d1",
                source=str(source),
                mode="apply",
                approve_run_id=planned["runId"],
                stdout=applied_output,
            )
            migrated = InventoryStockLine.objects.get()
            self.assertEqual(migrated.batch_id, "stock-batch-2")
            self.assertEqual(migrated.available_quantity, 8)
            self.assertEqual(
                InventoryImportBatch.objects.get(id="stock-batch-1")
                .totals_json["migrationSupersededStockRows"],
                2,
            )
