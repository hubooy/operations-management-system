"""Run only against an explicitly enabled, separate PostgreSQL test cluster."""
import os
from contextlib import contextmanager
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import quote

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from bi.models import BiMigrationRun
from bi.tests.test_api import BiFixtureMixin
from inventory.models import (
    GuangdongMonitorItem, GuangdongSupplierCycle, InventoryAgeLine,
    InventoryDataRevision, InventoryOperatingSettings, InventoryStockLine,
)
from sales.tests.factories import TEST_SECRET, signed_headers


@skipUnless(os.getenv("TERUISI_BI_PERMISSION_TEST") == "1", "requires isolated PostgreSQL cluster")
@override_settings(DJANGO_PROCESS_ROLE="bi_reader", DJANGO_EXPECT_READ_ONLY=True)
class BiPostgresPermissionTests(BiFixtureMixin, TransactionTestCase):
    def setUp(self):
        config = connection.settings_dict
        if (connection.vendor != "postgresql" or str(config["PORT"]) == "5432"
                or config["HOST"] != "127.0.0.1" or not config["NAME"].startswith("test_")):
            raise RuntimeError("BI permission tests require a separate loopback PostgreSQL test database")
        InventoryDataRevision.objects.update_or_create(domain="inventory", defaults={"revision": 4, "source_digest": "b" * 64})
        InventoryOperatingSettings.objects.get_or_create(id=1)
        self.install_bi_fixture()
        InventoryStockLine.objects.update(warehouse="广东仓", supplier="test supplier")
        InventoryAgeLine.objects.update(warehouse="广东仓")
        GuangdongMonitorItem.objects.create(product_code="P1", risk_override="healthy", risk_reason_override="test")
        GuangdongSupplierCycle.objects.create(supplier="test supplier", lead_days=10)
        BiMigrationRun.objects.create(
            id="bi-apply-" + "a" * 32, plan_id="bi-plan-" + "b" * 32,
            status="verified", contract_version="bi-dashboard-read-model-v1",
            source_digest="c" * 64, source_revisions_json={}, source_counts_json={},
            source_snapshot_json={}, verified_at=timezone.now(),
        )
        self.password = "isolated-bi-role-test-password-592849"
        # Execute the exact provisioning payload used by the runtime operator.
        source = (Path(__file__).resolve().parents[3] / "tools" / "django-bi-service.ps1").read_text(encoding="utf-8")
        code = source.split("$code = @'\n", 1)[1].split("\n'@", 1)[0]
        url = (f"postgresql://{quote(config['USER'], safe='')}:{quote(config['PASSWORD'], safe='')}"
               f"@127.0.0.1:{config['PORT']}/{quote(config['NAME'], safe='')}")
        with patch.dict(os.environ, {"TERUISI_PROVISION_DATABASE_URL": url,
                                    "TERUISI_PROVISION_BI_READER_PASSWORD": self.password}):
            exec(compile(code, "bi_role_provision.py", "exec"), {})

    @contextmanager
    def reader(self):
        saved = connection.settings_dict.copy()
        connection.close()
        connection.settings_dict.update(USER="teruisi_bi_reader", PASSWORD=self.password)
        try:
            yield
        finally:
            connection.close()
            connection.settings_dict.clear()
            connection.settings_dict.update(saved)

    def readiness(self):
        # Source authority validation has its own tests; keep the real inventory
        # schema, grants, transaction mode and BI receipt checks here.
        with self.reader(), patch("teruisi_backend.health._validate_reader_state"):
            return self.client.get("/health/ready")

    def test_real_reader_loads_guangdong_dashboard_and_is_ready(self):
        with self.reader(), patch.dict(os.environ, {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}):
            url = "/api/bi/overview?range=month"
            response = self.client.get(url, headers=signed_headers(method="GET", url=url))
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["inventory"]["health"]["healthy"], 1)
            self.assertEqual(response.json()["revision"], response.headers["X-Bi-Data-Revision"])
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                self.assertEqual(cursor.fetchone()[0], "on")
                for table in ("inventory_guangdong_monitor_items", "inventory_guangdong_supplier_cycles"):
                    cursor.execute("SELECT has_table_privilege(current_user,%s,'INSERT,UPDATE,DELETE,TRUNCATE')", [table])
                    self.assertFalse(cursor.fetchone()[0])
                cursor.execute("SELECT has_table_privilege(current_user,'inventory_guangdong_monitor_audits','SELECT')")
                self.assertFalse(cursor.fetchone()[0])
        self.assertEqual(self.readiness().status_code, 200)

    def test_readiness_rejects_each_missing_configuration_grant(self):
        for table in ("inventory_guangdong_monitor_items", "inventory_guangdong_supplier_cycles"):
            with self.subTest(table=table):
                with connection.cursor() as cursor:
                    cursor.execute(f'REVOKE SELECT ON "{table}" FROM teruisi_bi_reader')
                try:
                    self.assertEqual(self.readiness().status_code, 503)
                finally:
                    with connection.cursor() as cursor:
                        cursor.execute(f'GRANT SELECT ON "{table}" TO teruisi_bi_reader')

    def test_readiness_rejects_column_write_privilege_even_in_readonly_transactions(self):
        with connection.cursor() as cursor:
            cursor.execute("GRANT UPDATE (notes) ON inventory_guangdong_monitor_items TO teruisi_bi_reader")
        try:
            response = self.readiness()
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["code"], "bi_reader_unavailable")
        finally:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE UPDATE (notes) ON inventory_guangdong_monitor_items FROM teruisi_bi_reader")
