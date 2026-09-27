"""Real sealed v2 Reader -> report-bound ERP-only five-grain materials."""
from copy import deepcopy
import hashlib
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from access_control.models import AppUser
from netshop.models import NetshopRow
from sales.models import SalesOrderLine
from sales.tests.factories import make_line

from . import business_erp_rollup_materials as owning
from . import test_business_integrated_guard as fixtures
from .test_business_evidence import versioned_netshop_facts
from .policy import AiError, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessErpRollupMaterialTests(djtest.TransactionTestCase):
    user = fixtures.BusinessIntegratedGuardTests.user
    call = fixtures.BusinessIntegratedGuardTests.call
    bundle = fixtures.BusinessIntegratedGuardTests.bundle
    input_for = fixtures.BusinessIntegratedGuardTests.input_for
    insert = fixtures.BusinessIntegratedGuardTests.insert
    seed = fixtures.BusinessIntegratedGuardTests.seed
    collect_body = fixtures.BusinessIntegratedGuardTests.collect_body

    def setUp(self):
        fixtures.BusinessIntegratedGuardTests.setUp(self)
        self.report, _ = self.seed()
        self.pair = self.plan["plan"]["pairs"][0]["pairKey"]

    def opened(self, *, actor=None, report_id=None, pair=None, sales="sales", master="master", **kwargs):
        return owning.prepare(report_id or self.report.id, pair or self.pair,
            sales, master, actor or self.admin, **kwargs)

    def recollect(self, suffix):
        body = deepcopy(self.evidence_body)
        body["clientRequestId"] = "erp-rollup-"+suffix
        self.parent = self.collect_body(body)
        self.report, _ = self.seed()

    def test_exact_report_five_typed_tables_and_complete_sealed_ndjson(self):
        with versioned_netshop_facts():
            NetshopRow.objects.filter(source_row_key="master-1").update(category="平台类目")
        self.recollect("matched")
        with patch("ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            with self.opened() as prepared:
                manifest = prepared.manifest
                self.assertEqual(manifest["reportBinding"]["reportId"], self.report.id)
                self.assertEqual(manifest["pairKey"], self.pair)
                self.assertEqual([item["kind"] for item in manifest["tables"]],
                    ["shop_day", "category_day", "spu_day", "sku_day", "unassigned_day"])
                self.assertEqual(manifest["sourceRowCount"], 12)
                self.assertEqual(manifest["sourceTotals"]["netSalesCents"], 120000)
                self.assertFalse(manifest["authorityVerified"])
                self.assertFalse(manifest["netshopAdFinanceCombined"])
                for spec in manifest["tables"]:
                    raw = b"".join(prepared.ndjson_pages(spec["kind"]))
                    self.assertEqual(len(raw), spec["ndjsonBytes"])
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), spec["ndjsonSha256"])
                self.assertEqual([len(list(table.rows)) for table in prepared.tables()],
                    [item["rowCount"] for item in manifest["tables"]])
                self.assertEqual(manifest["sourceTotals"]["netSalesCents"],
                    manifest["matchedTotals"]["netSalesCents"]
                    + manifest["unassignedTotals"]["netSalesCents"])
            with self.assertRaises(Exception):
                prepared.manifest
        remote.assert_not_called()
        self.assertFalse(any(table in query["sql"].lower() for query in queries
            for table in ("sales_order_lines", "netshop_rows")))

    def test_same_spu_two_skus_remains_unassigned_and_refund_zero_cost_conserve(self):
        with versioned_netshop_facts():
            NetshopRow.objects.filter(source_row_key="master-1").update(category="平台类目")
            NetshopRow.objects.create(source_row_key="erp-rollup-second-sku",
                source_row_hash=digest(["master", "second"]), first_import_batch_id="master",
                last_import_batch_id="master", source_row_number=2,
                source="jd_product_master", dataset="product_master", platform="京东",
                shop_name="京东一店", sku_id="SKU2", spu_id="SPU1",
                category="平台类目", snapshot_date="2026-08-01",
                raw_json={"商家编码": "M1"}, created_at="2026-08-01", updated_at="2026-08-01")
        make_line(1001, "erp-rollup-refund", channel=self.query["channel"],
            online_spec_code="M1", quantity=-1, allocated_amount_cents=-5000,
            cost_amount_cents=-1000, gross_profit_cents=-4000,
            business_type="退货").save()
        make_line(1002, "erp-rollup-zero-cost", channel=self.query["channel"],
            online_spec_code="M1", cost_amount_cents=0,
            gross_profit_cents=10000).save()
        self.recollect("ambiguous-refund")
        with self.opened() as prepared:
            manifest = prepared.manifest
            self.assertEqual(manifest["sourceRowCount"], 14)
            self.assertEqual(manifest["sourceTotals"]["netSalesCents"], 125000)
            self.assertEqual(manifest["sourceTotals"]["refundCents"], 5000)
            self.assertEqual(manifest["sourceTotals"]["costCents"], 83000)
            self.assertEqual(manifest["matchedTotals"]["netSalesCents"], 0)
            self.assertEqual(manifest["unassignedTotals"]["netSalesCents"], 125000)
            self.assertEqual([spec["sourceFactCount"] for spec in manifest["tables"]],
                [14, 0, 0, 0, 14])
            unassigned = list(prepared.tables()[-1].rows)
            keys = [column.key for column in prepared.tables()[-1].columns]
            self.assertIn("ambiguous", {row[keys.index("status")] for row in unassigned})

    def test_other_actor_wrong_pair_source_or_report_are_rejected(self):
        other = self.user("erp-rollup-other@example.invalid", "admin", None)
        for kwargs in ({"actor": other}, {"actor": self.viewer},
                {"pair": "0"*64}, {"sales": "ads"}, {"master": "ads"},
                {"report_id": "missing-erp-report"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AiError):
                with self.opened(**kwargs):
                    pass

    def test_late_revocation_and_capacity_refuse_before_material_escape(self):
        with self.assertRaises(AiError):
            with self.opened(rollup_scratch_bytes=4096):
                pass
        def revoke(event):
            if event == {"stage": "erp_report_rollups", "phase": "complete"}:
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with self.assertRaises(AiError):
            with self.opened(checkpoint=revoke):
                pass
