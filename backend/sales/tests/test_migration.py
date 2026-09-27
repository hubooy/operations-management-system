from __future__ import annotations

import io
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.db import IntegrityError, connection as target_connection
from django.test import TestCase, override_settings
from django.utils import timezone

from erp_reference.models import ErpReferenceSyncCheckpoint
from sales.management.commands import migrate_sales_from_d1 as migration_command
from sales.management.commands.migrate_sales_from_d1 import (
    BATCH_COLUMNS,
    CANONICAL_FORMAT_VERSION,
    LEGACY_UPLOAD_CHUNK_COUNT_KEY,
    LEGACY_UPLOAD_COUNT_KEY,
    LINE_COLUMNS,
    PRODUCT_COLUMNS,
    _target_binary_collation,
)
from sales.models import (
    ErpProductMaster,
    SalesDataRevision,
    SalesImportAttempt,
    SalesImportBatch,
    SalesImportFingerprint,
    SalesImportScopeHead,
    SalesLegacyUploadAudit,
    SalesMigrationRun,
    SalesOrderLine,
    SalesRawUploadSession,
    SalesStagedImportSession,
    SalesWriteAuthority,
)


INTEGER_COLUMNS = {
    "id", "file_size_bytes", "row_count", "inserted_count", "duplicate_count", "warning_count",
    "source_row_number", "quantity", "list_unit_price_cents", "cost_amount_cents",
    "allocated_unit_price_cents", "allocated_amount_cents", "fee_allocation_cents",
    "gross_profit_cents", "gross_margin_bps", "untaxed_gross_profit_cents", "untaxed_gross_margin_bps",
}


def create_table(connection: sqlite3.Connection, name: str, columns: tuple[str, ...], primary: str) -> None:
    definitions = []
    for column in columns:
        sql_type = "INTEGER" if column in INTEGER_COLUMNS and (column != "id" or name == "sales_order_lines") else "TEXT"
        if name == "sales_order_lines" and column == "source_line_key":
            # Make the source's declared/default order differ from the target's
            # binary order so digest tests prove the migration overrides it.
            sql_type += " COLLATE NOCASE"
        suffix = " PRIMARY KEY" if column == primary else ""
        definitions.append(f'"{column}" {sql_type}{suffix}')
    connection.execute(f'CREATE TABLE "{name}" ({", ".join(definitions)})')


def line_values(identifier: int, key: str) -> tuple[object, ...]:
    values = {
        "id": identifier, "source_line_key": key, "source_row_hash": f"hash-{key}",
        "first_import_batch_id": "batch-1", "last_import_batch_id": "batch-1", "source_row_number": identifier,
        "order_no": f"order-{identifier}", "online_order_no": "", "channel": "渠道A", "platform": "京东",
        "shop_name": "京东一店", "logistics_company": "物流", "warehouse": "主仓", "product_code": f"P{identifier}",
        "online_spec_code": "", "product_name": f"商品{identifier}", "specification": "", "barcode": "", "supplier": "",
        "category": "品类", "quantity": 1, "list_unit_price_cents": 1000, "cost_amount_cents": 600,
        "allocated_unit_price_cents": 1000, "allocated_amount_cents": 1000, "fee_allocation_cents": 0,
        "gross_profit_cents": 400, "gross_margin_bps": 4000, "untaxed_gross_profit_cents": 400,
        "untaxed_gross_margin_bps": 4000, "order_time": "2026-08-01 00:00:00", "sales_time": "2026-08-01 00:00:00",
        "ship_time": "2026-08-01 00:00:00", "line_ship_time": "2026-08-01 00:00:00", "business_type": "销售",
        "created_at": "2026-08-01 00:00:00", "updated_at": "2026-08-01 00:00:00",
    }
    return tuple(values[column] for column in LINE_COLUMNS)


def install_legacy_uploads(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE sales_import_uploads (
        id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, file_name TEXT NOT NULL,
        file_size_bytes INTEGER NOT NULL, chunk_size_bytes INTEGER NOT NULL,
        chunk_count INTEGER NOT NULL, received_chunk_count INTEGER NOT NULL,
        received_bytes INTEGER NOT NULL, status TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT NOT NULL)"""
    )
    connection.execute(
        """CREATE TABLE sales_import_upload_chunks (
        upload_id TEXT NOT NULL, chunk_index INTEGER NOT NULL, object_key TEXT NOT NULL,
        size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL)"""
    )
    connection.execute(
        "CREATE UNIQUE INDEX legacy_upload_chunk_uq "
        "ON sales_import_upload_chunks(upload_id, chunk_index)"
    )
    created_at = "2000-01-01 00:00:00+00:00"
    updated_at = "2000-01-02 00:00:00+00:00"
    expired_at = "2000-01-03 00:00:00+00:00"
    future_at = "2999-01-03 00:00:00+00:00"
    upload_sql = (
        "INSERT INTO sales_import_uploads "
        "(id,fingerprint,file_name,file_size_bytes,chunk_size_bytes,chunk_count,"
        "received_chunk_count,received_bytes,status,created_at,updated_at,expires_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    for index in range(84):
        connection.execute(
            upload_sql,
            (
                f"completed-{index:03d}", f"fingerprint-completed-{index:03d}",
                f"completed-{index:03d}.xlsx", 5, 5, 1, 1, 5, "completed",
                created_at, updated_at, future_at,
            ),
        )
    for index in range(13):
        chunk_count = 8 if index == 12 else 7
        upload_id = f"ready-{index:03d}"
        connection.execute(
            upload_sql,
            (
                upload_id, f"fingerprint-ready-{index:03d}", f"ready-{index:03d}.xlsx",
                chunk_count * 5, 5, chunk_count, chunk_count, chunk_count * 5,
                "ready", created_at, updated_at, expired_at,
            ),
        )
        for chunk_index in range(chunk_count):
            object_key = f"sales-upload/{upload_id}/{chunk_index:06d}-fixture"
            connection.execute(
                "INSERT INTO sales_import_upload_chunks "
                "(upload_id,chunk_index,object_key,size_bytes,sha256,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    upload_id,
                    chunk_index,
                    object_key,
                    5,
                    hashlib.sha256(
                        f"payload-{upload_id}-{chunk_index}".encode("utf-8")
                    ).hexdigest(),
                    created_at,
                ),
            )
    for index in range(2):
        connection.execute(
            upload_sql,
            (
                f"uploading-{index:03d}", f"fingerprint-uploading-{index:03d}",
                f"uploading-{index:03d}.xlsx", 10, 5, 2, 0, 0, "uploading",
                created_at, updated_at, expired_at,
            ),
        )


def install_source(path: Path) -> None:
    connection = sqlite3.connect(path)
    create_table(connection, "sales_import_batches", BATCH_COLUMNS, "id")
    create_table(connection, "sales_order_lines", LINE_COLUMNS, "id")
    connection.execute("CREATE UNIQUE INDEX source_line_key_unique ON sales_order_lines(source_line_key)")
    create_table(connection, "erp_product_master", PRODUCT_COLUMNS, "product_code")
    connection.execute("CREATE TABLE sales_overview_cache_state (id INTEGER PRIMARY KEY, sales_revision INTEGER NOT NULL, erp_product_revision INTEGER NOT NULL)")
    connection.execute(
        """CREATE TABLE import_content_fingerprints (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT, batch_id TEXT,
        scope_key TEXT, scope_json TEXT, import_hash TEXT, raw_file_hash TEXT,
        content_hash TEXT, row_count INTEGER, status TEXT,
        publication_sequence INTEGER, created_at TEXT)"""
    )
    connection.execute(
        """CREATE TABLE import_content_attempts (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT, domain TEXT,
        batch_id TEXT, scope_key TEXT, scope_json TEXT, import_hash TEXT,
        raw_file_hash TEXT, content_hash TEXT, row_count INTEGER, file_name TEXT,
        file_size_bytes INTEGER, actor TEXT, warnings_json TEXT, outcome TEXT,
        error_code TEXT, recovered_from_attempt_id TEXT, created_at TEXT,
        updated_at TEXT)"""
    )
    connection.execute(
        """CREATE TABLE import_scope_heads (
        domain TEXT, scope_key TEXT, state_token TEXT, status TEXT,
        owner_token TEXT, current_batch_id TEXT, generation INTEGER,
        updated_at TEXT)"""
    )
    install_legacy_uploads(connection)
    connection.execute("INSERT INTO sales_overview_cache_state VALUES (1, 8, 5)")
    batch = {
        "id": "batch-1", "source": "test", "file_name": "sales.xlsx", "file_size_bytes": 100,
        "file_hash": "a" * 64, "sheet_name": "销售", "status": "completed", "row_count": 2,
        "inserted_count": 2, "duplicate_count": 0, "warning_count": 0, "warnings_json": "[]",
        "totals_json": "{}", "created_at": "2026-08-01 00:00:00", "completed_at": "2026-08-01 01:00:00",
    }
    product = {
        "product_code": "P1", "product_name": "商品1", "brand": "", "specification": "", "barcode": "",
        "category": "品类", "supplier": "", "product_status": "", "source_row_number": 1,
        "last_import_batch_id": "erp-1", "created_at": "2026-08-01 00:00:00", "updated_at": "2026-08-01 00:00:00",
    }
    connection.execute(f'INSERT INTO sales_import_batches ({", ".join(BATCH_COLUMNS)}) VALUES ({", ".join("?" for _ in BATCH_COLUMNS)})', tuple(batch[column] for column in BATCH_COLUMNS))
    control_time = "2026-08-01 01:00:00+00:00"
    connection.execute(
        "INSERT INTO import_content_fingerprints "
        "(domain,batch_id,scope_key,scope_json,import_hash,raw_file_hash,content_hash,row_count,status,publication_sequence,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("sales", "batch-1", "scope-1", '{"source":"sales_ledger"}', "a" * 64,
         "b" * 64, "c" * 64, 2, "completed", 1, control_time),
    )
    connection.execute(
        "INSERT INTO import_content_attempts "
        "(attempt_id,domain,batch_id,scope_key,scope_json,import_hash,raw_file_hash,content_hash,row_count,file_name,file_size_bytes,actor,warnings_json,outcome,error_code,recovered_from_attempt_id,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("attempt-1", "sales", "batch-1", "scope-1", '{"source":"sales_ledger"}',
         "a" * 64, "b" * 64, "c" * 64, 2, "sales.xlsx", 100,
         "admin@example.test", "[]", "imported", "", "", control_time, control_time),
    )
    connection.execute(
        "INSERT INTO import_scope_heads VALUES (?,?,?,?,?,?,?,?)",
        ("sales", "scope-1", "state-1", "ready", "", "batch-1", 3, control_time),
    )
    connection.execute(f'INSERT INTO erp_product_master ({", ".join(PRODUCT_COLUMNS)}) VALUES ({", ".join("?" for _ in PRODUCT_COLUMNS)})', tuple(product[column] for column in PRODUCT_COLUMNS))
    placeholders = ", ".join("?" for _ in LINE_COLUMNS)
    connection.execute(f'INSERT INTO sales_order_lines ({", ".join(LINE_COLUMNS)}) VALUES ({placeholders})', line_values(1, "L1"))
    connection.execute(f'INSERT INTO sales_order_lines ({", ".join(LINE_COLUMNS)}) VALUES ({placeholders})', line_values(2, "L2"))
    connection.commit()
    connection.close()


class SalesMigrationTests(TestCase):
    def test_latest_schema_preserves_legacy_projection_checkpoint_for_rollback(self) -> None:
        with target_connection.cursor() as cursor:
            self.assertIn(
                "sales_projection_sync_checkpoint",
                target_connection.introspection.table_names(cursor),
            )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.source = Path(self.temporary.name) / "d1.sqlite"
        install_source(self.source)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_command(self, *, source: Path | None = None, **options) -> str:
        stdout = io.StringIO()
        call_command(
            "migrate_sales_from_d1",
            source=str(source or self.source),
            batch_size=100,
            stdout=stdout,
            **options,
        )
        return stdout.getvalue()

    def approved_dry_run(self, *, source: Path | None = None) -> str:
        payload = json.loads(self.run_command(source=source, dry_run=True))
        self.assertEqual(payload["canonicalFormatVersion"], CANONICAL_FORMAT_VERSION)
        return str(payload["runId"])

    def apply_approved(self, approved_run_id: str, *, source: Path | None = None) -> str:
        return self.run_command(
            source=source,
            apply=True,
            approved_run_id=approved_run_id,
        )

    def install_legacy_v2_revision_evidence(self) -> None:
        approval = self.approved_dry_run()
        self.apply_approved(approval)
        completed = SalesMigrationRun.objects.get(status="completed")
        counts = dict(completed.source_counts)
        digests = dict(completed.source_digests)
        source_revision = completed.source_revision
        SalesMigrationRun.objects.all().delete()
        now = timezone.now()
        legacy_approval = SalesMigrationRun.objects.create(
            id="a" * 32,
            status="dry_run_completed",
            dry_run=True,
            source_fingerprint="legacy-source",
            source_path_digest="1" * 64,
            generation="2" * 32,
            source_revision=source_revision,
            canonical_format_version="sales-projection-v2",
            source_counts=counts,
            source_digests=digests,
            completed_at=now,
        )
        legacy_apply = SalesMigrationRun.objects.create(
            id="b" * 32,
            status="completed",
            dry_run=False,
            source_fingerprint="legacy-source",
            source_path_digest="1" * 64,
            generation="3" * 32,
            source_revision=source_revision,
            target_revision=source_revision,
            canonical_format_version="sales-projection-v2",
            approved_run_id=legacy_approval.id,
            source_counts=counts,
            target_counts=counts,
            source_digests=digests,
            target_digests=digests,
            completed_at=now,
        )
        legacy_approval.consumed_by_run_id = legacy_apply.id
        legacy_approval.approval_consumed_at = now
        legacy_approval.save(
            update_fields=["consumed_by_run_id", "approval_consumed_at"]
        )
        SalesMigrationRun.objects.create(
            id="c" * 32,
            status="verified",
            dry_run=False,
            source_fingerprint="legacy-source",
            source_path_digest="1" * 64,
            generation="4" * 32,
            source_revision=source_revision,
            target_revision=source_revision,
            canonical_format_version="sales-projection-v2",
            source_counts=counts,
            target_counts=counts,
            source_digests=digests,
            target_digests=digests,
            completed_at=now,
        )
        SalesDataRevision.objects.filter(domain="sales").update(
            source_digest=migration_command._domain_digest_for_format(
                "sales",
                digests,
                "sales-projection-v2",
            )
        )
        SalesDataRevision.objects.filter(domain="erp").update(
            source_digest=migration_command._domain_digest("erp", digests)
        )

    def install_erp_bridge_checkpoint(self, *, checkpoint_epoch: str | None = None) -> str:
        source_epoch = "e" * 32
        content_hash = "d" * 64
        source_batch_id = "erp-source-batch"
        connection = sqlite3.connect(self.source)
        connection.executescript(
            """
            CREATE TABLE erp_reference_projection_source_state (
              id INTEGER PRIMARY KEY, source_epoch TEXT NOT NULL
            );
            CREATE TABLE erp_product_projection_state (
              id INTEGER PRIMARY KEY, erp_revision INTEGER NOT NULL,
              source_batch_id TEXT NOT NULL, row_count INTEGER NOT NULL,
              content_hash TEXT NOT NULL
            );
            CREATE TABLE erp_reference_projection_outbox (
              event_sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO erp_reference_projection_source_state VALUES (1, ?)",
            (source_epoch,),
        )
        connection.execute(
            "INSERT INTO erp_product_projection_state VALUES (1, 5, ?, 1, ?)",
            (source_batch_id, content_hash),
        )
        connection.commit()
        connection.close()
        path_digest = hashlib.sha256(str(self.source.resolve()).encode("utf-8")).hexdigest()
        ErpReferenceSyncCheckpoint.objects.update_or_create(
            id=1,
            defaults={
                "source_epoch": checkpoint_epoch or source_epoch,
                "source_path_digest": path_digest,
                "last_event_sequence": 0,
                "last_event_id": "",
                "erp_revision": 5,
                "content_hash": content_hash,
                "row_count": 1,
                "source_batch_id": source_batch_id,
            },
        )
        SalesDataRevision.objects.filter(domain="erp").update(
            revision=5,
            source_digest=content_hash,
        )
        return content_hash

    def snapshot_with_second_read_drift(self, digest_key: str):
        original = migration_command._complete_source_snapshot
        calls = 0

        def read(connection, batch_size):
            nonlocal calls
            calls += 1
            revision, counts, digests, control_records, legacy_records = original(
                connection, batch_size
            )
            if calls == 2:
                digests = dict(digests)
                digests[digest_key] = "0" * 64
            return revision, counts, digests, control_records, legacy_records

        return read

    @override_settings(DJANGO_PROCESS_ROLE="migration_writer")
    def test_managed_cutover_upgrades_proven_legacy_sales_digest_once(self) -> None:
        self.install_legacy_v2_revision_evidence()
        approval = self.approved_dry_run()
        with patch.dict(
            os.environ,
            {
                "TERUISI_DJANGO_CUTOVER_MANAGED": "1",
                "TERUISI_DJANGO_CUTOVER_REHEARSAL_MANAGED": "",
            },
        ):
            payload = json.loads(
                self.run_command(
                    apply=True,
                    approved_run_id=approval,
                    allow_legacy_digest_upgrade=True,
                )
            )
        self.assertEqual(payload["status"], "completed")
        completed = SalesMigrationRun.objects.get(id=payload["runId"])
        self.assertEqual(
            SalesDataRevision.objects.get(domain="sales").source_digest,
            migration_command._domain_digest("sales", completed.source_digests),
        )

    def test_legacy_digest_upgrade_flag_rejects_non_managed_cli(self) -> None:
        self.install_legacy_v2_revision_evidence()
        approval = self.approved_dry_run()
        with self.assertRaisesMessage(CommandError, "受控 cutover migration_writer"):
            self.run_command(
                apply=True,
                approved_run_id=approval,
                allow_legacy_digest_upgrade=True,
            )
        self.assertFalse(SalesMigrationRun.objects.get(id=approval).consumed_by_run_id)

    @override_settings(DJANGO_PROCESS_ROLE="migration_writer")
    def test_managed_legacy_digest_upgrade_rejects_drifted_pg_target(self) -> None:
        self.install_legacy_v2_revision_evidence()
        SalesOrderLine.objects.filter(source_line_key="L1").update(product_name="PG drift")
        approval = self.approved_dry_run()
        with patch.dict(
            os.environ,
            {
                "TERUISI_DJANGO_CUTOVER_MANAGED": "1",
                "TERUISI_DJANGO_CUTOVER_REHEARSAL_MANAGED": "",
            },
        ):
            with self.assertRaisesMessage(CommandError, "未提升版本水位"):
                self.run_command(
                    apply=True,
                    approved_run_id=approval,
                    allow_legacy_digest_upgrade=True,
                )
        self.assertEqual(
            SalesOrderLine.objects.get(source_line_key="L1").product_name,
            "PG drift",
        )
        self.assertFalse(SalesMigrationRun.objects.get(id=approval).consumed_by_run_id)

    def test_apply_and_verify_preserve_exact_erp_checkpoint_digest(self) -> None:
        first_approval = self.approved_dry_run()
        self.apply_approved(first_approval)
        checkpoint_digest = self.install_erp_bridge_checkpoint()
        approval = self.approved_dry_run()
        self.apply_approved(approval)
        self.assertEqual(
            SalesDataRevision.objects.get(domain="erp").source_digest,
            checkpoint_digest,
        )
        self.assertIn('"status": "verified"', self.run_command(verify_only=True))

    def test_apply_rejects_erp_checkpoint_epoch_mismatch(self) -> None:
        first_approval = self.approved_dry_run()
        self.apply_approved(first_approval)
        self.install_erp_bridge_checkpoint(checkpoint_epoch="f" * 32)
        approval = self.approved_dry_run()
        with self.assertRaisesMessage(CommandError, "D1 0091"):
            self.apply_approved(approval)
        self.assertFalse(SalesMigrationRun.objects.get(id=approval).consumed_by_run_id)

    def test_apply_is_verified_idempotent_and_prunes_stale_rows(self) -> None:
        first_approval = self.approved_dry_run()
        self.apply_approved(first_approval)
        self.assertEqual(SalesOrderLine.objects.count(), 2)
        self.assertEqual(SalesImportBatch.objects.count(), 1)
        self.assertEqual(ErpProductMaster.objects.count(), 1)
        self.assertEqual(SalesImportFingerprint.objects.count(), 1)
        self.assertEqual(SalesImportAttempt.objects.count(), 1)
        self.assertEqual(SalesImportScopeHead.objects.get().state_token, "state-1")
        self.assertEqual(SalesLegacyUploadAudit.objects.count(), 99)
        self.assertEqual(
            sum(
                SalesLegacyUploadAudit.objects.values_list(
                    "manifest_chunk_count", flat=True
                )
            ),
            92,
        )
        migrated_batch = SalesImportBatch.objects.get(id="batch-1")
        self.assertEqual(migrated_batch.content_hash, "c" * 64)
        self.assertEqual(migrated_batch.published_state_token, "state-1")
        self.assertEqual(dict(SalesDataRevision.objects.values_list("domain", "revision")), {"sales": 8, "erp": 5})
        second_approval = self.approved_dry_run()
        self.apply_approved(second_approval)
        self.assertEqual(SalesOrderLine.objects.count(), 2)
        self.assertEqual(dict(SalesDataRevision.objects.values_list("domain", "revision")), {"sales": 8, "erp": 5})
        connection = sqlite3.connect(self.source)
        connection.execute("DELETE FROM sales_order_lines WHERE source_line_key = 'L2'")
        connection.execute("UPDATE sales_overview_cache_state SET sales_revision = 9 WHERE id = 1")
        connection.commit()
        connection.close()
        third_approval = self.approved_dry_run()
        self.apply_approved(third_approval)
        self.assertEqual(list(SalesOrderLine.objects.values_list("source_line_key", flat=True)), ["L1"])
        self.assertEqual(SalesDataRevision.objects.get(domain="sales").revision, 9)
        self.assertEqual(SalesMigrationRun.objects.filter(status="completed").count(), 3)
        latest = SalesMigrationRun.objects.filter(status="completed").order_by("-started_at").first()
        self.assertEqual(latest.source_revision, "9:5")
        self.assertEqual(latest.target_revision, "9:5")

    def test_legacy_upload_provenance_is_complete_private_and_not_resumable(self) -> None:
        dry_run = json.loads(self.run_command(dry_run=True))
        self.assertEqual(dry_run["sourceCounts"][LEGACY_UPLOAD_COUNT_KEY], 99)
        self.assertEqual(dry_run["sourceCounts"][LEGACY_UPLOAD_CHUNK_COUNT_KEY], 92)
        self.apply_approved(str(dry_run["runId"]))

        self.assertEqual(SalesLegacyUploadAudit.objects.count(), 99)
        self.assertEqual(
            SalesLegacyUploadAudit.objects.filter(archive_reason="completed").count(),
            84,
        )
        self.assertEqual(
            SalesLegacyUploadAudit.objects.filter(archive_reason="expired").count(),
            15,
        )
        ready = SalesLegacyUploadAudit.objects.get(source_upload_id="ready-012")
        self.assertEqual(ready.manifest_chunk_count, 8)
        self.assertEqual(ready.manifest_bytes, 40)
        self.assertEqual(len(ready.manifest_sha256), 64)
        self.assertEqual(len(ready.source_fingerprint_sha256), 64)
        self.assertEqual(len(ready.file_name_sha256), 64)

        audit_fields = {field.name for field in SalesLegacyUploadAudit._meta.get_fields()}
        self.assertNotIn("object_key", audit_fields)
        self.assertNotIn("file_name", audit_fields)
        self.assertNotIn("fingerprint", audit_fields)
        self.assertEqual(SalesRawUploadSession.objects.count(), 0)
        self.assertEqual(SalesStagedImportSession.objects.count(), 0)

        second_approval = self.approved_dry_run()
        self.apply_approved(second_approval)
        self.assertEqual(SalesLegacyUploadAudit.objects.count(), 99)

    def test_legacy_upload_active_unexpired_session_fails_closed(self) -> None:
        connection = sqlite3.connect(self.source)
        connection.execute(
            "UPDATE sales_import_uploads SET expires_at = '2999-01-03 00:00:00+00:00' "
            "WHERE id = 'ready-000'"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesMessage(CommandError, "尚未过期的活动"):
            self.run_command(dry_run=True)
        self.assertEqual(SalesLegacyUploadAudit.objects.count(), 0)

    def test_legacy_upload_orphan_and_non_contiguous_chunks_fail_closed(self) -> None:
        connection = sqlite3.connect(self.source)
        connection.execute(
            "INSERT INTO sales_import_upload_chunks "
            "(upload_id,chunk_index,object_key,size_bytes,sha256,created_at) "
            "VALUES ('missing-owner',0,'sales-upload/orphan/0',5,?,?)",
            ("a" * 64, "2000-01-01 00:00:00+00:00"),
        )
        connection.commit()
        connection.close()
        with self.assertRaisesMessage(CommandError, "无属主"):
            self.run_command(dry_run=True)

        connection = sqlite3.connect(self.source)
        connection.execute(
            "DELETE FROM sales_import_upload_chunks WHERE upload_id = 'missing-owner'"
        )
        connection.execute(
            "DELETE FROM sales_import_upload_chunks "
            "WHERE upload_id = 'ready-000' AND chunk_index = 0"
        )
        connection.execute(
            "UPDATE sales_import_uploads SET received_chunk_count = 6, received_bytes = 30 "
            "WHERE id = 'ready-000'"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesMessage(CommandError, "索引不连续"):
            self.run_command(dry_run=True)

    def test_legacy_upload_manifest_tampering_invalidates_approval_and_verify(self) -> None:
        approval = self.approved_dry_run()
        connection = sqlite3.connect(self.source)
        connection.execute(
            "UPDATE sales_import_upload_chunks SET object_key = object_key || '-changed' "
            "WHERE upload_id = 'ready-000' AND chunk_index = 0"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesMessage(CommandError, "source_digests"):
            self.apply_approved(approval)
        self.assertEqual(SalesLegacyUploadAudit.objects.count(), 0)

        new_approval = self.approved_dry_run()
        self.apply_approved(new_approval)
        SalesLegacyUploadAudit.objects.filter(source_upload_id="ready-000").update(
            manifest_sha256="f" * 64
        )
        with self.assertRaisesMessage(CommandError, "行数或摘要不一致"):
            self.run_command(verify_only=True)

    def test_v4_canonical_json_ignores_object_key_order_and_normalizes_null_owner(self) -> None:
        connection = sqlite3.connect(self.source)
        connection.execute(
            "UPDATE import_content_fingerprints SET scope_json = ? WHERE batch_id = 'batch-1'",
            ('{"z":1,"a":2}',),
        )
        connection.execute(
            "UPDATE import_content_attempts SET scope_json = ? WHERE attempt_id = 'attempt-1'",
            ('{"z":1,"a":2}',),
        )
        connection.execute(
            "UPDATE import_scope_heads SET owner_token = NULL WHERE scope_key = 'scope-1'"
        )
        connection.commit()
        connection.close()

        approval = self.approved_dry_run()
        self.apply_approved(approval)
        SalesImportFingerprint.objects.filter(batch_id="batch-1").update(
            scope_json={"a": 2, "z": 1}
        )
        SalesImportAttempt.objects.filter(id="attempt-1").update(
            scope_json={"a": 2, "z": 1}
        )
        self.assertEqual(SalesImportScopeHead.objects.get().owner_token, "")
        self.assertIn('"status": "verified"', self.run_command(verify_only=True))

    def test_verify_only_matches_after_apply(self) -> None:
        approval = self.approved_dry_run()
        self.apply_approved(approval)
        output = self.run_command(verify_only=True)
        self.assertIn('"status": "verified"', output)

    def test_dry_run_rejects_control_material_drift_before_approval(self) -> None:
        with patch.object(
            migration_command,
            "_complete_source_snapshot",
            side_effect=self.snapshot_with_second_read_drift("import_scope_heads"),
        ):
            with self.assertRaisesMessage(CommandError, "完整迁移材料"):
                self.run_command(dry_run=True)
        self.assertEqual(SalesMigrationRun.objects.get().status, "failed")
        self.assertEqual(SalesOrderLine.objects.count(), 0)

    def test_apply_rejects_primary_material_drift_and_rolls_back(self) -> None:
        approval = self.approved_dry_run()
        with patch.object(
            migration_command,
            "_complete_source_snapshot",
            side_effect=self.snapshot_with_second_read_drift("sales_order_lines"),
        ):
            with self.assertRaisesMessage(CommandError, "完整迁移材料"):
                self.apply_approved(approval)
        self.assertEqual(SalesOrderLine.objects.count(), 0)
        self.assertEqual(SalesLegacyUploadAudit.objects.count(), 0)
        approval_run = SalesMigrationRun.objects.get(id=approval)
        self.assertFalse(approval_run.consumed_by_run_id)
        self.assertIsNone(approval_run.approval_consumed_at)

    def test_verify_rejects_erp_material_drift_before_success(self) -> None:
        approval = self.approved_dry_run()
        self.apply_approved(approval)
        with patch.object(
            migration_command,
            "_complete_source_snapshot",
            side_effect=self.snapshot_with_second_read_drift("erp_product_master"),
        ):
            with self.assertRaisesMessage(CommandError, "完整迁移材料"):
                self.run_command(verify_only=True)
        self.assertEqual(
            SalesMigrationRun.objects.filter(status="failed").count(),
            1,
        )

    def test_positive_erp_revision_without_rows_fails_closed(self) -> None:
        connection = sqlite3.connect(self.source)
        connection.execute("DELETE FROM erp_product_master")
        connection.commit()
        connection.close()
        with self.assertRaisesMessage(CommandError, "缺少受控空集证明"):
            self.run_command(dry_run=True)
        self.assertEqual(SalesOrderLine.objects.count(), 0)
        self.assertEqual(ErpProductMaster.objects.count(), 0)
        self.assertEqual(SalesMigrationRun.objects.get().status, "failed")

    def test_apply_materializes_and_verifies_query_ready_projection(self) -> None:
        connection = sqlite3.connect(self.source)
        connection.execute(
            """
            UPDATE sales_order_lines
               SET platform = ' 京东 ', channel = ' 渠道A ', shop_name = ' ',
                   warehouse = ' 刷刷仓 ', category = ' 配件 ',
                   order_no = '', online_order_no = ''
             WHERE source_line_key = 'L2'
            """
        )
        connection.commit()
        connection.close()

        approval = self.approved_dry_run()
        self.apply_approved(approval)
        row = SalesOrderLine.objects.get(source_line_key="L2")
        self.assertEqual(row.business_date, date(2026, 8, 1))
        self.assertEqual(row.platform_key, "京东")
        self.assertEqual(row.channel_key, "渠道A")
        self.assertEqual(row.shop_key, "渠道A")
        self.assertEqual(row.resolved_category, "配件")
        self.assertEqual(row.order_identity, "L2")
        self.assertFalse(row.is_business_row)
        self.assertFalse(row.is_net_sales_row)
        self.assertFalse(row.is_net_quantity_row)

        SalesOrderLine.objects.filter(source_line_key="L2").update(platform_key="被篡改")
        with self.assertRaisesMessage(CommandError, "查询投影"):
            self.run_command(verify_only=True)

    def test_dry_run_rejects_ship_time_without_valid_business_date(self) -> None:
        connection = sqlite3.connect(self.source)
        connection.execute(
            "UPDATE sales_order_lines SET ship_time = 'not-a-date' WHERE source_line_key = 'L1'"
        )
        connection.commit()
        connection.close()

        with self.assertRaisesMessage(CommandError, "无法生成业务日期"):
            self.run_command(dry_run=True)
        self.assertEqual(SalesOrderLine.objects.count(), 0)
        self.assertEqual(SalesMigrationRun.objects.get().status, "failed")

    def test_invalid_ship_time_never_exposes_the_raw_source_line_identity(self) -> None:
        secret_key = "CUSTOMER-SECRET-LINE-0091"
        connection = sqlite3.connect(self.source)
        connection.execute(
            "UPDATE sales_order_lines SET source_line_key = ?, ship_time = 'not-a-date' "
            "WHERE source_line_key = 'L1'",
            (secret_key,),
        )
        connection.commit()
        connection.close()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with self.assertRaises(CommandError) as raised:
            call_command(
                "migrate_sales_from_d1",
                source=str(self.source),
                batch_size=100,
                dry_run=True,
                stdout=stdout,
                stderr=stderr,
            )
        run = SalesMigrationRun.objects.get()
        combined = "\n".join(
            [str(raised.exception), stdout.getvalue(), stderr.getvalue(), run.error_message]
        )
        self.assertNotIn(secret_key, combined)
        self.assertEqual(run.error_code, "migration_rejected")
        self.assertEqual(run.error_message, "D1 销售行 ship_time 无法生成业务日期")

    def test_uncontrolled_database_error_is_redacted_from_cli_and_audit(self) -> None:
        approval = self.approved_dry_run()
        secret = "SECRET-ROW-CONTENT postgresql://user:password@host/database"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(
            migration_command,
            "_apply_table",
            side_effect=IntegrityError(secret),
        ):
            with self.assertRaises(CommandError) as raised:
                call_command(
                    "migrate_sales_from_d1",
                    source=str(self.source),
                    batch_size=100,
                    apply=True,
                    approved_run_id=approval,
                    stdout=stdout,
                    stderr=stderr,
                )
        failed = SalesMigrationRun.objects.get(dry_run=False)
        combined = "\n".join(
            [str(raised.exception), stdout.getvalue(), stderr.getvalue(), failed.error_message]
        )
        self.assertNotIn("SECRET-ROW-CONTENT", combined)
        self.assertNotIn("postgresql://", combined)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "migration_internal_error")
        self.assertEqual(
            failed.error_message,
            "销售数据迁移发生内部错误；业务表事务已回滚",
        )
        self.assertFalse(SalesMigrationRun.objects.get(id=approval).consumed_by_run_id)
        self.assertEqual(SalesOrderLine.objects.count(), 0)

    def test_apply_output_failure_preserves_committed_run_and_consumed_approval(self) -> None:
        approval = self.approved_dry_run()

        class LostOutput(io.StringIO):
            def write(self, value):
                raise OSError("SECRET-PIPE-FAILURE")

        with self.assertRaisesMessage(CommandError, "已完成提交") as raised:
            call_command(
                "migrate_sales_from_d1",
                source=str(self.source),
                batch_size=100,
                apply=True,
                approved_run_id=approval,
                stdout=LostOutput(),
                stderr=io.StringIO(),
            )
        self.assertNotIn("SECRET-PIPE-FAILURE", str(raised.exception))
        approval_run = SalesMigrationRun.objects.get(id=approval)
        completed = SalesMigrationRun.objects.get(approved_run_id=approval)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.error_code, "")
        self.assertEqual(completed.error_message, "")
        self.assertEqual(approval_run.consumed_by_run_id, completed.id)
        self.assertIsNotNone(approval_run.approval_consumed_at)
        self.assertEqual(SalesOrderLine.objects.count(), 2)
        with self.assertRaisesMessage(CommandError, "已被消费"):
            self.apply_approved(approval)
        self.assertEqual(SalesOrderLine.objects.count(), 2)
        run_count = SalesMigrationRun.objects.count()
        recovered = json.loads(
            self.run_command(
                recover_approved_apply=True,
                approved_run_id=approval,
            )
        )
        self.assertEqual(recovered["status"], "recovered_completed_apply")
        self.assertEqual(recovered["runId"], completed.id)
        self.assertEqual(recovered["approvedRunId"], approval)
        self.assertEqual(
            recovered["canonicalFormatVersion"], CANONICAL_FORMAT_VERSION
        )
        self.assertEqual(SalesMigrationRun.objects.count(), run_count)

        connection = sqlite3.connect(self.source)
        connection.execute(
            "UPDATE sales_overview_cache_state SET sales_revision = sales_revision + 1 WHERE id = 1"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesMessage(CommandError, "当前 D1 全量快照"):
            self.run_command(
                recover_approved_apply=True,
                approved_run_id=approval,
            )
        self.assertEqual(SalesMigrationRun.objects.count(), run_count)

    def test_source_row_id_changes_and_target_id_collisions_do_not_change_identity_or_digest(self) -> None:
        approval = self.approved_dry_run()
        self.apply_approved(approval)
        target_ids = dict(SalesOrderLine.objects.values_list("source_line_key", "id"))
        first_digest = SalesMigrationRun.objects.get(status="completed").source_digests["sales_order_lines"]

        connection = sqlite3.connect(self.source)
        # Move L2 away, then assign L1 a D1 id that is already owned by L2 in
        # Django. The D1 id is deliberately neither payload nor identity.
        connection.execute("UPDATE sales_order_lines SET id = 1000000 WHERE source_line_key = 'L2'")
        connection.execute(
            "UPDATE sales_order_lines SET id = ? WHERE source_line_key = 'L1'",
            (target_ids["L2"],),
        )
        connection.commit()
        connection.close()

        second_approval = self.approved_dry_run()
        self.apply_approved(second_approval)
        second_run = SalesMigrationRun.objects.filter(status="completed").order_by("-started_at").first()
        self.assertEqual(second_run.source_digests["sales_order_lines"], first_digest)
        self.assertEqual(
            dict(SalesOrderLine.objects.values_list("source_line_key", "id")),
            target_ids,
        )
        self.assertIn('"status": "verified"', self.run_command(verify_only=True))

    def test_non_ascii_stable_keys_use_explicit_binary_digest_order(self) -> None:
        connection = sqlite3.connect(self.source)
        connection.execute("UPDATE sales_order_lines SET source_line_key = 'a-中文' WHERE id = 1")
        connection.execute("UPDATE sales_order_lines SET source_line_key = 'B-éclair' WHERE id = 2")
        connection.commit()
        connection.close()

        approval = self.approved_dry_run()
        self.apply_approved(approval)
        self.assertEqual(
            list(
                SalesOrderLine.objects.order_by("source_line_key").values_list(
                    "source_line_key", flat=True
                )
            ),
            ["B-éclair", "a-中文"],
        )
        self.assertIn('"status": "verified"', self.run_command(verify_only=True))

    def test_target_binary_collation_is_fixed_for_supported_databases(self) -> None:
        self.assertEqual(_target_binary_collation("sqlite"), "BINARY")
        self.assertEqual(_target_binary_collation("postgresql"), "C")

    def test_dry_run_does_not_change_business_tables(self) -> None:
        output = self.run_command(dry_run=True)
        self.assertIn('"status": "dry_run_completed"', output)
        self.assertIn(f'"canonicalFormatVersion": "{CANONICAL_FORMAT_VERSION}"', output)
        self.assertEqual(SalesOrderLine.objects.count(), 0)
        run = SalesMigrationRun.objects.get()
        self.assertEqual(run.status, "dry_run_completed")
        self.assertEqual(run.canonical_format_version, CANONICAL_FORMAT_VERSION)
        self.assertTrue(run.source_counts)
        self.assertTrue(run.source_digests)

    def test_dry_run_requires_quiet_sales_control_state(self) -> None:
        connection = sqlite3.connect(self.source)
        connection.execute(
            "UPDATE import_content_attempts SET outcome = 'processing' WHERE attempt_id = 'attempt-1'"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesMessage(CommandError, "不是静默终态"):
            self.run_command(dry_run=True)
        self.assertEqual(SalesOrderLine.objects.count(), 0)

    def test_omitted_mode_and_apply_without_approval_never_write(self) -> None:
        with self.assertRaisesMessage(CommandError, "必须显式选择"):
            self.run_command()
        with self.assertRaisesMessage(CommandError, "--approved-run-id"):
            self.run_command(apply=True)
        self.assertEqual(SalesOrderLine.objects.count(), 0)
        self.assertFalse(SalesMigrationRun.objects.exists())

    def test_apply_rejects_approval_from_another_source_path(self) -> None:
        approval = self.approved_dry_run()
        alternate = Path(self.temporary.name) / "same-content-other-path.sqlite"
        alternate.write_bytes(self.source.read_bytes())
        with self.assertRaisesMessage(CommandError, "source_path_digest"):
            self.apply_approved(approval, source=alternate)
        self.assertEqual(SalesOrderLine.objects.count(), 0)
        self.assertFalse(SalesMigrationRun.objects.get(id=approval).consumed_by_run_id)

    def test_apply_rejects_content_changed_after_approval_even_when_stat_fingerprint_is_restored(self) -> None:
        approval = self.approved_dry_run()
        approved_stat = self.source.stat()
        connection = sqlite3.connect(self.source)
        connection.execute("UPDATE sales_order_lines SET product_name = 'changed' WHERE source_line_key = 'L1'")
        connection.commit()
        connection.close()
        os.utime(self.source, ns=(approved_stat.st_atime_ns, approved_stat.st_mtime_ns))

        with self.assertRaisesMessage(CommandError, "source_digests"):
            self.apply_approved(approval)
        self.assertEqual(SalesOrderLine.objects.count(), 0)
        self.assertFalse(SalesMigrationRun.objects.get(id=approval).consumed_by_run_id)

    def test_unrelated_file_mtime_change_does_not_replace_full_business_digest_approval(self) -> None:
        approval = self.approved_dry_run()
        stat = self.source.stat()
        os.utime(
            self.source,
            ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000),
        )
        self.apply_approved(approval)
        self.assertEqual(SalesOrderLine.objects.count(), 2)
        self.assertTrue(SalesMigrationRun.objects.get(id=approval).consumed_by_run_id)

    def test_apply_rejects_revision_changed_after_approval(self) -> None:
        approval = self.approved_dry_run()
        approved_stat = self.source.stat()
        connection = sqlite3.connect(self.source)
        connection.execute("UPDATE sales_overview_cache_state SET sales_revision = 9 WHERE id = 1")
        connection.commit()
        connection.close()
        os.utime(self.source, ns=(approved_stat.st_atime_ns, approved_stat.st_mtime_ns))

        with self.assertRaisesMessage(CommandError, "source_revision"):
            self.apply_approved(approval)
        self.assertEqual(SalesOrderLine.objects.count(), 0)
        self.assertFalse(SalesMigrationRun.objects.get(id=approval).consumed_by_run_id)

    def test_apply_refuses_to_overwrite_an_active_postgres_writer(self) -> None:
        approval = self.approved_dry_run()
        SalesWriteAuthority.objects.filter(id=1).update(status="active")
        with self.assertRaisesMessage(CommandError, "必须保持 pending"):
            self.apply_approved(approval)
        self.assertEqual(SalesOrderLine.objects.count(), 0)
        self.assertFalse(SalesMigrationRun.objects.get(id=approval).consumed_by_run_id)

    def test_approval_is_consumed_atomically_and_only_once(self) -> None:
        approval_id = self.approved_dry_run()
        completed = json.loads(self.apply_approved(approval_id))
        approval = SalesMigrationRun.objects.get(id=approval_id)
        apply_run = SalesMigrationRun.objects.get(id=completed["runId"])
        self.assertEqual(approval.consumed_by_run_id, apply_run.id)
        self.assertIsNotNone(approval.approval_consumed_at)
        self.assertEqual(apply_run.approved_run_id, approval.id)

        with self.assertRaisesMessage(CommandError, "已被消费"):
            self.apply_approved(approval_id)
        self.assertEqual(SalesOrderLine.objects.count(), 2)
        approval.refresh_from_db()
        self.assertEqual(approval.consumed_by_run_id, apply_run.id)

    def test_source_cannot_alias_the_django_sqlite_target(self) -> None:
        with patch.dict(target_connection.settings_dict, {"NAME": str(self.source)}):
            with self.assertRaisesMessage(CommandError, "不能与 Django SQLite 目标使用同一文件"):
                self.run_command(dry_run=True)
        self.assertFalse(SalesMigrationRun.objects.exists())

    def test_source_revision_drift_rolls_back_business_snapshot(self) -> None:
        approval = self.approved_dry_run()
        with patch(
            "sales.management.commands.migrate_sales_from_d1._read_live_source_revision",
            return_value=(9, 5),
        ):
            with self.assertRaisesMessage(Exception, "版本水位在迁移期间变化"):
                self.apply_approved(approval)
        self.assertEqual(SalesOrderLine.objects.count(), 0)
        approval_run = SalesMigrationRun.objects.get(id=approval)
        self.assertFalse(approval_run.consumed_by_run_id)
        self.assertIsNone(approval_run.approval_consumed_at)
        self.assertEqual(SalesMigrationRun.objects.filter(status="failed").count(), 1)
