"""Target isolated PG checks for sealed-v2 three-window SKU materials."""
from types import SimpleNamespace
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from access_control.models import AppUser
from netshop.models import NetshopRow
from sales.tests.factories import make_line

from . import business_cross_source_sku_materials as owner
from . import test_business_cross_source_daily_materials as daily_fixture
from .business_sealed import Reader
from .test_business_evidence import versioned_netshop_facts
from .policy import AiError, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class BusinessCrossSourceSkuMaterialTests(djtest.TransactionTestCase):
    user = daily_fixture.BusinessCrossSourceDailyMaterialTests.user
    call = daily_fixture.BusinessCrossSourceDailyMaterialTests.call
    bundle = daily_fixture.BusinessCrossSourceDailyMaterialTests.bundle
    input_for = daily_fixture.BusinessCrossSourceDailyMaterialTests.input_for
    insert = daily_fixture.BusinessCrossSourceDailyMaterialTests.insert
    seed = daily_fixture.BusinessCrossSourceDailyMaterialTests.seed
    collect_body = daily_fixture.BusinessCrossSourceDailyMaterialTests.collect_body
    _native = daily_fixture.BusinessCrossSourceDailyMaterialTests._native
    _recollect = daily_fixture.BusinessCrossSourceDailyMaterialTests._recollect

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated sealed v2 source guards")
        daily_fixture.BusinessCrossSourceDailyMaterialTests.setUp(self)

    def prepared(self, **kwargs):
        return owner.prepare(self.report.id, self.keys, self.admin,
            enabled=True, **kwargs)

    def test_same_sealed_report_three_windows_with_missing_sources_and_no_addition(self):
        with patch("ai_assistant.transport.execute_tool") as remote:
            value = self.prepared()
        remote.assert_not_called()
        compared = value["comparison"]
        self.assertEqual(set(compared["sourceMaterialDigests"]),
            {"current", "previous", "yearAgo"})
        self.assertEqual(value["binding"]["reportBinding"]["reportId"],
            self.report.id)
        self.assertTrue(value["sealedSelectedSourcesFullyReplayed"])
        self.assertTrue(value["binding"]["erpUnassignedPoolPreserved"])
        self.assertFalse(value["historicalErpSkuOwnershipVerified"])
        self.assertFalse(value["authorityVerified"])
        self.assertFalse(compared["crossDomainAmountsAdded"])
        self.assertTrue(any(row["windows"]["previous"]["status"] ==
            "missing_source" for row in compared["rows"]))
        self.assertTrue(all(row["comparisons"]["previous"]["growthRateBps"]
            is None for row in compared["rows"] if row["column"] == "erpMatched"))
        self.assertEqual(value["bindingDigest"], digest(value["binding"]))
        self.assertEqual(value["resultDigest"], digest({key:child for key,child
            in value.items() if key != "resultDigest"}))

    def test_ambiguous_master_refund_remains_in_daily_unassigned_pool(self):
        with versioned_netshop_facts():
            NetshopRow.objects.create(source_row_key="sku-owning-second-master",
                source_row_hash=digest(["sku-owning", "second-master"]),
                first_import_batch_id="master", last_import_batch_id="master",
                source_row_number=2, source="jd_product_master",
                dataset="product_master", platform="京东",
                shop_name=self.query["shop"], sku_id="SKU2", spu_id="SPU1",
                category="平台类目", snapshot_date="2026-08-01",
                raw_json={"商家编码":"M1"}, created_at="2026-08-01",
                updated_at="2026-08-01")
        make_line(1001, "sku-owning-refund", channel=self.query["channel"],
            online_spec_code="M1", quantity=-1,
            allocated_amount_cents=-5000, cost_amount_cents=-1000,
            gross_profit_cents=-4000, business_type="退货").save()
        self._recollect("sku-owning-ambiguous-refund")
        original = owner.cross_source_sku_window_compare.prepare_candidate
        with patch.object(owner.cross_source_sku_window_compare,
                "prepare_candidate", wraps=original) as compared:
            value = self.prepared()
        current = compared.call_args.args[5]["current"]
        shop = current["shopDayRows"][0]
        self.assertEqual(shop["erpSales"]["refundCents"]["value"], 5000)
        self.assertEqual(shop["erpUnassigned"]["netSalesCents"],
            shop["erpSales"]["netSalesCents"])
        self.assertEqual(shop["erpUnassigned"]["refundCents"]["value"], 5000)
        # Other already matched ERP facts may still produce SKU rows. This
        # ambiguous refund must remain entirely in the unassigned pool.
        self.assertEqual(sum((row["windows"]["current"]["value"] or 0)
            for row in value["comparison"]["rows"]
            if row["column"] == "erpMatched" and
               row["metric"] == "refundCents"), 0)
        self.assertFalse(value["comparison"]["crossDomainAmountsAdded"])

    def test_default_closed_wrong_report_actor_and_revoked_final_fence(self):
        with patch.object(owner.daily_owning, "prepare",
                side_effect=AssertionError("closed must not read")) as read:
            with self.assertRaises(AiError):
                owner.prepare(self.report.id, self.keys, self.admin)
            read.assert_not_called()
        other = self.user("sku-owning-other@example.invalid", "admin", None)
        for report_id, actor in (("missing-report", self.admin),
                                 (self.report.id, other),
                                 (self.report.id, self.viewer)):
            with self.subTest(report_id=report_id, actor=actor), \
                    self.assertRaises(AiError):
                owner.prepare(report_id, self.keys, actor, enabled=True)
        def revoke(event):
            if event == {"stage":"cross_source_sku_owning", "phase":"complete"}:
                AppUser.objects.filter(email=self.admin.email).update(
                    status="disabled")
        with self.assertRaises(AiError):
            self.prepared(checkpoint=revoke)

    def test_final_source_revision_drift_refuses_result(self):
        constructions = 0
        def reader_with_final_drift(evidence, principal):
            nonlocal constructions
            constructions += 1
            reader = Reader(evidence, principal)
            if constructions == 1:
                return reader
            def changed_info(key):
                info = reader.info(key)
                info["metadata"]["sourceRevision"] = "synthetic-revision-drift"
                return info
            return SimpleNamespace(info=changed_info)
        with patch.object(owner, "Reader", side_effect=reader_with_final_drift), \
                self.assertRaises(AiError):
            self.prepared()
        self.assertEqual(constructions, 2)
