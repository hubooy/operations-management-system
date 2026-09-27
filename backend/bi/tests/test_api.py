from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from inventory.models import (
    InventoryAgeLine,
    InventoryDataRevision,
    InventoryImportBatch,
    InventoryStockLine,
)
from sales.tests.factories import TEST_SECRET, install_fixture, signed_headers

from bi.models import BiMigrationRun
from bi.query import _inventory_health_score


class BiFixtureMixin:
    def install_bi_fixture(self) -> None:
        install_fixture()
        InventoryDataRevision.objects.filter(domain="inventory").update(
            revision=4,
            source_digest="b" * 64,
        )
        InventoryImportBatch.objects.create(
            id="inventory-batch-1",
            dataset="stock",
            source="test",
            file_name="inventory.xlsx",
            file_size_bytes=100,
            file_hash="c" * 64,
            raw_file_hash="d" * 64,
            content_hash="e" * 64,
            scope_key="f" * 64,
            published_state_token="1" * 64,
            sheet_name="分仓库存",
            snapshot_date=timezone.localdate(),
            status="completed",
            source_row_count=1,
            row_count=1,
            inserted_count=1,
            completed_at=timezone.now(),
        )
        InventoryStockLine.objects.create(
            batch_id="inventory-batch-1",
            row_key="主仓\x1fP1",
            source_row_number=1,
            snapshot_date=timezone.localdate(),
            warehouse="主仓",
            warehouse_type="owned",
            warehouse_category="selfOperated",
            include_in_inventory=True,
            product_code="P1",
            product_name="饮水机",
            category="饮水设备",
            on_hand_quantity=4,
            available_quantity=2,
            locked_quantity=2,
            unit_cost_cents=7_000,
        )
        InventoryImportBatch.objects.create(
            id="inventory-age-batch-1",
            dataset="age",
            source="test",
            file_name="inventory-age.xlsx",
            file_size_bytes=100,
            file_hash="2" * 64,
            raw_file_hash="3" * 64,
            content_hash="4" * 64,
            scope_key="5" * 64,
            published_state_token="6" * 64,
            sheet_name="库存库龄",
            snapshot_date=timezone.localdate(),
            status="completed",
            source_row_count=1,
            row_count=1,
            inserted_count=1,
            completed_at=timezone.now(),
        )
        InventoryAgeLine.objects.create(
            batch_id="inventory-age-batch-1",
            row_key="主仓\x1fP1",
            source_row_number=1,
            snapshot_date=timezone.localdate(),
            warehouse="主仓",
            warehouse_type="owned",
            product_code="P1",
            product_name="饮水机",
            category="饮水设备",
            available_quantity=2,
            inventory_age_days=120,
            sales_30d_quantity=0,
            unit_cost_cents=7_000,
            stock_value_cents=14_000,
        )


@override_settings(DJANGO_PROCESS_ROLE="development")
class BiApiContractTests(BiFixtureMixin, TestCase):
    def setUp(self) -> None:
        self.install_bi_fixture()

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_overview_is_one_bounded_consistent_projection(self) -> None:
        url = "/api/bi/overview?range=custom&startDate=2026-08-01&endDate=2026-08-02"
        response = self.client.get(url, headers=signed_headers(url))

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["projection"], "dashboard")
        self.assertEqual(payload["contractVersion"], "bi-dashboard-read-model-v1")
        self.assertEqual(payload["sales"]["current"]["netSalesCents"], 14_000)
        self.assertEqual(payload["sales"]["previousStartDate"], "2026-07-01")
        self.assertEqual(payload["sales"]["previousEndDate"], "2026-07-02")
        self.assertEqual(payload["sales"]["latestBatch"]["id"], "batch-1")
        self.assertEqual(payload["inventory"]["sync"]["latestInventoryBatchId"], "inventory-batch-1")
        self.assertEqual(payload["sourceRevisions"], {"salesErp": "7:3", "inventory": "4:bbbbbbbbbbbb"})
        self.assertEqual(payload["revision"], "7:3|4:bbbbbbbbbbbb")
        self.assertEqual(response["X-Bi-Data-Revision"], payload["revision"])
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertNotIn("items", payload["inventory"])
        self.assertNotIn("filterOptions", payload["sales"])
        self.assertTrue(
            payload["inventoryHealthScore"] is None
            or isinstance(payload["inventoryHealthScore"], int)
        )

    def test_health_score_is_a_server_owned_metric(self) -> None:
        score = _inventory_health_score({
            "metrics": {
                "inventoryAlertsEnabled": True,
                "recommendationsSuppressed": False,
                "urgentCount": 2,
            },
            "health": {"stale": 3},
        })
        self.assertEqual(score, 78)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_overview_rejects_scope_unknown_parameters_and_wrong_process(self) -> None:
        scoped_url = "/api/bi/overview?range=month"
        scoped = self.client.get(
            scoped_url,
            headers=signed_headers(
                scoped_url,
                scope={"warehouses": [], "channels": ["京东"], "platforms": ["京东"]},
            ),
        )
        self.assertEqual(scoped.status_code, 403)
        self.assertEqual(scoped.json()["code"], "access_denied")

        unknown_url = "/api/bi/overview?range=month&view=dashboard"
        unknown = self.client.get(unknown_url, headers=signed_headers(unknown_url))
        self.assertEqual(unknown.status_code, 400)

        with override_settings(DJANGO_PROCESS_ROLE="inventory_reader"):
            unavailable = self.client.get(scoped_url, headers=signed_headers(scoped_url))
        self.assertEqual(unavailable.status_code, 503)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    @patch("bi.query.source_revisions")
    def test_overview_fails_closed_when_source_revisions_keep_changing(self, revisions) -> None:
        revisions.side_effect = [
            {"salesErp": "7:3", "inventory": "4:bbbbbbbbbbbb"},
            {"salesErp": "8:3", "inventory": "4:bbbbbbbbbbbb"},
            {"salesErp": "8:3", "inventory": "4:bbbbbbbbbbbb"},
            {"salesErp": "8:3", "inventory": "5:cccccccccccc"},
        ]
        url = "/api/bi/overview?range=custom&startDate=2026-08-01&endDate=2026-08-02"
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "service_unavailable")

    @override_settings(DJANGO_PROCESS_ROLE="bi_reader", DJANGO_EXPECT_READ_ONLY=False)
    @patch("teruisi_backend.health._validate_reader_state")
    @patch("teruisi_backend.health._validate_inventory_revision")
    @patch("teruisi_backend.health._validate_inventory_schema")
    def test_bi_readiness_requires_a_verified_migration_receipt(
        self,
        _inventory_schema,
        _inventory_revision,
        _sales_reader,
    ) -> None:
        missing = self.client.get("/health/ready")
        self.assertEqual(missing.status_code, 503)
        self.assertEqual(missing.json()["code"], "bi_reader_unavailable")

        BiMigrationRun.objects.create(
            id="bi-apply-" + "a" * 32,
            plan_id="bi-plan-" + "b" * 32,
            status="verified",
            contract_version="bi-dashboard-read-model-v1",
            source_digest="c" * 64,
            source_revisions_json={},
            source_counts_json={},
            source_snapshot_json={},
            verified_at=timezone.now(),
        )
        ready = self.client.get("/health/ready")
        self.assertEqual(ready.status_code, 200, ready.content)
        self.assertEqual(ready.json()["biReader"], "ready")


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BiMigrationCommandTests(BiFixtureMixin, TestCase):
    def setUp(self) -> None:
        self.install_bi_fixture()

    def _run(self, *arguments: str) -> dict[str, object]:
        output = StringIO()
        call_command("migrate_bi_read_model", *arguments, stdout=output)
        return json.loads(output.getvalue())

    def test_plan_apply_verify_records_only_audit_evidence(self) -> None:
        plan = self._run("--plan")
        self.assertEqual(plan["status"], "planned")
        self.assertFalse(plan["factCopyRequired"])
        self.assertEqual(plan["sourceAuthorities"]["erp"], "d1-via-postgresql-read-projection")
        self.assertEqual(plan["sourceCounts"]["legacyBiFactRows"], 0)
        self.assertEqual(plan["sourceCounts"]["latestInventoryAgeRows"], 1)
        self.assertEqual(plan["latestInventoryAgeBatchId"], "inventory-age-batch-1")
        self.assertEqual(BiMigrationRun.objects.count(), 0)

        applied = self._run("--apply", "--approved-plan-id", str(plan["planId"]))
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(BiMigrationRun.objects.count(), 1)

        verified = self._run("--verify", "--approved-run-id", str(applied["runId"]))
        self.assertEqual(verified["status"], "verified")
        run = BiMigrationRun.objects.get(id=applied["runId"])
        self.assertEqual(run.status, "verified")
        self.assertIsNotNone(run.verified_at)

    def test_apply_rejects_stale_plan(self) -> None:
        plan = self._run("--plan")
        InventoryDataRevision.objects.filter(domain="inventory").update(revision=5)
        with self.assertRaises(CommandError):
            self._run("--apply", "--approved-plan-id", str(plan["planId"]))

    @patch("bi.management.commands.migrate_bi_read_model.source_revisions")
    def test_plan_rejects_a_continuously_changing_source(self, revisions) -> None:
        revisions.side_effect = [
            {"salesErp": "7:3", "inventory": "4:bbbbbbbbbbbb"},
            {"salesErp": "8:3", "inventory": "4:bbbbbbbbbbbb"},
            {"salesErp": "8:3", "inventory": "4:bbbbbbbbbbbb"},
            {"salesErp": "8:3", "inventory": "5:cccccccccccc"},
        ]
        with self.assertRaises(CommandError):
            self._run("--plan")
