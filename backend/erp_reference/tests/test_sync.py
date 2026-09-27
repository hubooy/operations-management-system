from __future__ import annotations

import io
import json
import sqlite3
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from erp_reference.models import ErpReferenceSyncCheckpoint
from erp_reference.sync import (
    CANONICAL_FORMAT_VERSION,
    ERP_PRODUCT_COLUMNS,
    ERP_SCOPE_JSON,
    ErpReferenceSyncError,
    initialize_checkpoint,
    inspect_sync_status,
    read_checkpoint,
    sync_reference_once,
)
from sales.models import (
    ErpProductMaster,
    SalesDataRevision,
    SalesOrderLine,
)
from sales.tests.factories import make_line


SOURCE_EPOCH = "0123456789abcdef0123456789abcdef"
BASELINE_HASH = "a" * 64


def product(
    code: str,
    category: str,
    *,
    batch_id: str,
    name: str | None = None,
) -> tuple[object, ...]:
    return (
        code,
        name or f"商品-{code}",
        "品牌",
        "规格",
        f"barcode-{code}",
        category,
        "供应商",
        "在售",
        int(code[1:]),
        batch_id,
        "2026-08-01 00:00:00",
        "2026-08-01 00:00:00",
    )


def install_source(path: Path, products: list[tuple[object, ...]]) -> None:
    source = sqlite3.connect(path)
    source.executescript(
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
          event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
          event_id TEXT NOT NULL, source_epoch TEXT NOT NULL,
          domain TEXT NOT NULL, operation TEXT NOT NULL, scope_json TEXT NOT NULL,
          source_batch_id TEXT NOT NULL, erp_revision INTEGER NOT NULL,
          row_count INTEGER NOT NULL, content_hash TEXT NOT NULL,
          canonical_format_version TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE erp_reference_import_batches (
          id TEXT PRIMARY KEY, source_key TEXT NOT NULL, status TEXT NOT NULL,
          row_count INTEGER NOT NULL, totals_json TEXT NOT NULL
        );
        CREATE TABLE erp_product_master (
          product_code TEXT PRIMARY KEY, product_name TEXT NOT NULL,
          brand TEXT NOT NULL, specification TEXT NOT NULL, barcode TEXT NOT NULL,
          category TEXT NOT NULL, supplier TEXT NOT NULL, product_status TEXT NOT NULL,
          source_row_number INTEGER NOT NULL, last_import_batch_id TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    source.execute(
        "INSERT INTO erp_reference_projection_source_state VALUES (1, ?)",
        (SOURCE_EPOCH,),
    )
    source.execute(
        "INSERT INTO erp_product_projection_state VALUES (1, 1, 'erp-baseline', ?, ?)",
        (len(products), BASELINE_HASH),
    )
    source.execute(
        "INSERT INTO erp_reference_import_batches VALUES "
        "('erp-baseline', 'products', 'completed', ?, ?)",
        (len(products), json.dumps({"contentHash": BASELINE_HASH})),
    )
    placeholders = ", ".join(["?"] * len(ERP_PRODUCT_COLUMNS))
    source.executemany(
        f"INSERT INTO erp_product_master ({', '.join(ERP_PRODUCT_COLUMNS)}) "
        f"VALUES ({placeholders})",
        products,
    )
    source.commit()
    source.close()


def publish_erp(
    path: Path,
    products: list[tuple[object, ...]],
    *,
    sequence: int = 1,
    revision: int = 2,
    batch_id: str = "erp-next",
    content_hash: str = "b" * 64,
    domain: str = "erp",
) -> None:
    source = sqlite3.connect(path)
    source.execute("DELETE FROM erp_product_master")
    placeholders = ", ".join(["?"] * len(ERP_PRODUCT_COLUMNS))
    source.executemany(
        f"INSERT INTO erp_product_master ({', '.join(ERP_PRODUCT_COLUMNS)}) "
        f"VALUES ({placeholders})",
        products,
    )
    source.execute(
        "INSERT INTO erp_reference_import_batches VALUES (?, 'products', 'completed', ?, ?)",
        (batch_id, len(products), json.dumps({"contentHash": content_hash})),
    )
    source.execute(
        "UPDATE erp_product_projection_state SET erp_revision = ?, "
        "source_batch_id = ?, row_count = ?, content_hash = ? WHERE id = 1",
        (revision, batch_id, len(products), content_hash),
    )
    source.execute(
        "INSERT INTO erp_reference_projection_outbox ("
        "event_sequence, event_id, source_epoch, domain, operation, scope_json, "
        "source_batch_id, erp_revision, row_count, content_hash, "
        "canonical_format_version, created_at) VALUES (?, ?, ?, ?, 'replace_all', ?, ?, ?, ?, ?, ?, ?)",
        (
            sequence,
            f"{SOURCE_EPOCH}:erp:{batch_id}",
            SOURCE_EPOCH,
            domain,
            ERP_SCOPE_JSON,
            batch_id,
            revision,
            len(products),
            content_hash,
            CANONICAL_FORMAT_VERSION,
            "2026-08-28T12:00:00+08:00",
        ),
    )
    source.commit()
    source.close()


class ErpReferenceSyncTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.source = Path(self.temporary.name) / "erp-authority.sqlite"
        self.baseline_products = [
            product("P1", "旧ERP类目1", batch_id="erp-baseline"),
            product("P2", "旧ERP类目2", batch_id="erp-baseline"),
        ]
        install_source(self.source, self.baseline_products)
        SalesDataRevision.objects.update_or_create(
            domain="sales", defaults={"revision": 7, "source_digest": "c" * 64}
        )
        SalesDataRevision.objects.update_or_create(
            domain="erp", defaults={"revision": 1, "source_digest": "d" * 64}
        )
        for row in self.baseline_products:
            ErpProductMaster.objects.create(
                **dict(zip(ERP_PRODUCT_COLUMNS, row)), migration_generation="baseline"
            )
        SalesOrderLine.objects.bulk_create(
            [
                make_line(1, "L1", product_code="P1", category="源类目1"),
                make_line(2, "L2", product_code="P2", category="源类目2"),
            ]
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def initialize(self) -> None:
        result = initialize_checkpoint(self.source)
        self.assertEqual(result["status"], "initialized")

    def test_initializes_from_erp_only_source_without_any_sales_d1_tables(self) -> None:
        result = initialize_checkpoint(self.source)

        self.assertEqual(result["rowCount"], 2)
        checkpoint = read_checkpoint()
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint.erp_revision, 1)
        self.assertEqual(checkpoint.content_hash, BASELINE_HASH)
        self.assertEqual(
            SalesDataRevision.objects.get(domain="erp").source_digest,
            BASELINE_HASH,
        )
        source = sqlite3.connect(self.source)
        self.assertEqual(
            source.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'table' AND name LIKE 'sales_%'"
            ).fetchone()[0],
            0,
        )
        source.close()

    def test_replace_all_is_atomic_idempotent_and_recomputes_only_derived_categories(self) -> None:
        self.initialize()
        next_products = [
            product("P1", "新ERP类目1", batch_id="erp-next", name="商品P1更新"),
            product("P3", "新ERP类目3", batch_id="erp-next"),
        ]
        publish_erp(self.source, next_products)

        result = sync_reference_once(self.source, batch_size=100)

        self.assertEqual(result["status"], "synchronized")
        self.assertEqual(result["eventCount"], 1)
        self.assertEqual(
            list(ErpProductMaster.objects.values_list("product_code", flat=True)),
            ["P1", "P3"],
        )
        self.assertEqual(
            SalesOrderLine.objects.get(source_line_key="L1").resolved_category,
            "新ERP类目1",
        )
        self.assertEqual(
            SalesOrderLine.objects.get(source_line_key="L2").resolved_category,
            "源类目2",
        )
        self.assertEqual(SalesDataRevision.objects.get(domain="sales").revision, 7)
        self.assertEqual(SalesDataRevision.objects.get(domain="erp").revision, 2)
        checkpoint = read_checkpoint()
        self.assertEqual(checkpoint.last_event_sequence, 1)

        replay = sync_reference_once(self.source, batch_size=100)
        self.assertEqual(replay["status"], "up_to_date")
        self.assertEqual(replay["eventCount"], 0)

    def test_failure_after_target_replace_rolls_back_products_revision_and_checkpoint(self) -> None:
        self.initialize()
        publish_erp(
            self.source,
            [product("P1", "不应发布", batch_id="erp-next")],
        )

        with patch(
            "erp_reference.sync._publish_checkpoint",
            side_effect=ErpReferenceSyncError("injected checkpoint failure"),
        ):
            with self.assertRaisesMessage(
                ErpReferenceSyncError, "injected checkpoint failure"
            ):
                sync_reference_once(self.source, batch_size=100)

        self.assertEqual(
            dict(ErpProductMaster.objects.values_list("product_code", "category")),
            {"P1": "旧ERP类目1", "P2": "旧ERP类目2"},
        )
        self.assertEqual(SalesDataRevision.objects.get(domain="erp").revision, 1)
        checkpoint = read_checkpoint()
        self.assertEqual(checkpoint.last_event_sequence, 0)

    def test_zero_row_source_state_is_rejected_without_deleting_target_products(self) -> None:
        self.initialize()
        publish_erp(self.source, [])

        with self.assertRaisesMessage(ErpReferenceSyncError, "revision 或行数水位无效"):
            sync_reference_once(self.source, batch_size=100)

        self.assertEqual(
            list(ErpProductMaster.objects.values_list("product_code", flat=True)),
            ["P1", "P2"],
        )
        self.assertEqual(read_checkpoint().last_event_sequence, 0)

    def test_zero_row_checkpoint_is_rejected_without_deleting_target_products(self) -> None:
        self.initialize()
        ErpReferenceSyncCheckpoint.objects.filter(id=1).update(row_count=0)

        with self.assertRaisesMessage(ErpReferenceSyncError, "checkpoint 水位无效"):
            sync_reference_once(self.source, batch_size=100)

        self.assertEqual(
            list(ErpProductMaster.objects.values_list("product_code", flat=True)),
            ["P1", "P2"],
        )

    def test_zero_row_outbox_event_is_rejected_without_deleting_target_products(self) -> None:
        self.initialize()
        publish_erp(
            self.source,
            [],
            sequence=1,
            revision=2,
            batch_id="erp-empty",
            content_hash="b" * 64,
        )
        publish_erp(
            self.source,
            [product("P3", "最终ERP类目", batch_id="erp-final")],
            sequence=2,
            revision=3,
            batch_id="erp-final",
            content_hash="e" * 64,
        )

        with self.assertRaisesMessage(ErpReferenceSyncError, "outbox 行数无效"):
            sync_reference_once(self.source, batch_size=100)

        self.assertEqual(
            list(ErpProductMaster.objects.values_list("product_code", flat=True)),
            ["P1", "P2"],
        )
        self.assertEqual(read_checkpoint().last_event_sequence, 0)

    def test_empty_decoded_snapshot_is_rejected_before_target_replace(self) -> None:
        self.initialize()
        publish_erp(
            self.source,
            [product("P3", "新ERP类目", batch_id="erp-next")],
        )

        with patch("erp_reference.sync._read_products", return_value=[]):
            with self.assertRaisesMessage(
                ErpReferenceSyncError, "replace_all 拒绝空产品全集"
            ):
                sync_reference_once(self.source, batch_size=100)

        self.assertEqual(
            list(ErpProductMaster.objects.values_list("product_code", flat=True)),
            ["P1", "P2"],
        )
        self.assertEqual(read_checkpoint().last_event_sequence, 0)

    def test_sales_domain_event_is_rejected_and_never_applied(self) -> None:
        self.initialize()
        publish_erp(
            self.source,
            [product("P1", "销售事件伪装", batch_id="erp-next")],
            domain="sales",
        )

        with self.assertRaisesMessage(
            ErpReferenceSyncError, "outbox head 与当前权威水位不一致"
        ):
            sync_reference_once(self.source, batch_size=100)

        self.assertEqual(SalesDataRevision.objects.get(domain="erp").revision, 1)
        self.assertEqual(
            ErpProductMaster.objects.get(product_code="P1").category,
            "旧ERP类目1",
        )

    def test_management_command_uses_durable_checkpoint(self) -> None:
        initialized = io.StringIO()
        call_command(
            "sync_erp_reference",
            source=str(self.source),
            initialize_checkpoint=True,
            source_change_retries=1,
            stdout=initialized,
        )
        self.assertEqual(json.loads(initialized.getvalue())["status"], "initialized")

        output = io.StringIO()
        call_command(
            "sync_erp_reference",
            source=str(self.source),
            source_change_retries=1,
            transient_db_retries=0,
            batch_size=100,
            stdout=output,
        )
        self.assertEqual(json.loads(output.getvalue())["status"], "up_to_date")

        status_output = io.StringIO()
        checked_at = read_checkpoint().last_checked_at
        call_command(
            "sync_erp_reference",
            source=str(self.source),
            status=True,
            max_age_seconds=60,
            stdout=status_output,
        )
        status = json.loads(status_output.getvalue())
        self.assertEqual(status["status"], "caught_up")
        self.assertEqual(status["headSequence"], 0)
        self.assertEqual(read_checkpoint().last_checked_at, checked_at)

    def test_read_only_status_rejects_stale_and_divergent_checkpoint(self) -> None:
        self.initialize()
        result = inspect_sync_status(self.source, max_age_seconds=60)
        self.assertEqual(result["status"], "caught_up")

        ErpReferenceSyncCheckpoint.objects.filter(id=1).update(
            last_checked_at=timezone.now() - timedelta(seconds=61)
        )
        with self.assertRaisesMessage(ErpReferenceSyncError, "心跳已过期"):
            inspect_sync_status(self.source, max_age_seconds=60)

        ErpReferenceSyncCheckpoint.objects.filter(id=1).update(
            last_checked_at=timezone.now()
        )
        publish_erp(
            self.source,
            [product("P1", "尚未同步", batch_id="erp-next")],
        )
        with self.assertRaisesMessage(ErpReferenceSyncError, "尚未追平"):
            inspect_sync_status(self.source, max_age_seconds=60)

    def test_checkpoint_is_singleton(self) -> None:
        self.initialize()
        self.assertEqual(ErpReferenceSyncCheckpoint.objects.count(), 1)
        with self.assertRaisesMessage(ErpReferenceSyncError, "不得重新绑定源"):
            initialize_checkpoint(self.source)
