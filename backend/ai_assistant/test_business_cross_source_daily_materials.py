"""Real sealed-v2 integrated report -> internal cross-source daily columns."""
from copy import deepcopy
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from access_control.models import AppUser
from netshop.models import NetshopRow

from business_analysis import mapping_plan
from . import business_cross_source_daily_materials as owning, models as m
from . import test_business_integrated_guard as fixtures
from .test_business_evidence import versioned_netshop_facts
from .policy import AiError, digest


_UNSET = object()


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessCrossSourceDailyMaterialTests(djtest.TransactionTestCase):
    user = fixtures.BusinessIntegratedGuardTests.user
    call = fixtures.BusinessIntegratedGuardTests.call
    bundle = fixtures.BusinessIntegratedGuardTests.bundle
    input_for = fixtures.BusinessIntegratedGuardTests.input_for
    insert = fixtures.BusinessIntegratedGuardTests.insert
    seed = fixtures.BusinessIntegratedGuardTests.seed
    collect_body = fixtures.BusinessIntegratedGuardTests.collect_body

    def setUp(self):
        with versioned_netshop_facts():
            fixtures.BusinessIntegratedGuardTests.setUp(self)
            NetshopRow.objects.filter(source_row_key="master-1").update(category="平台类目")
            self._native("sku-1", "sku_daily", "SKU1", payment=0,
                metrics={"transactionAmountCents": 0, "visitors": 3,
                    "transactionQuantity": 1, "pageViews": 5, "transactionOrders": 1})
            self._native("sku-missing-payment", "sku_daily", "SKU1", payment=0,
                metrics={"visitors": 2, "transactionQuantity": 1,
                    "pageViews": 4, "transactionOrders": 1})
            self._native("spu-1", "spu_daily", "", payment=70,
                metrics={"transactionAmountCents": 70, "visitors": 4,
                    "transactionQuantity": 1, "pageViews": 5, "transactionOrders": 1})
        self.sources = [*self.sources,
            {"key": "sku", "domain": "netshop", "query": {
                **{key: value for key, value in self.query.items() if key != "channel"},
                "dataset": "sku"}},
            {"key": "spu", "domain": "netshop", "query": {
                **{key: value for key, value in self.query.items() if key != "channel"},
                "dataset": "spu"}}]
        self._recollect("cross-source-daily")
        self.keys = {"master": "master",
            "erpSales": {"current": "sales", "previous": None, "yearAgo": None},
            "netshopSku": {"current": "sku", "previous": None, "yearAgo": None},
            "netshopSpu": {"current": "spu", "previous": None, "yearAgo": None},
            "promotion": {"current": "ads", "previous": None, "yearAgo": None}}
        self.pair = self.plan["plan"]["pairs"][0]["pairKey"]

    def _native(self, key, dataset, sku, *, payment, metrics, row_hash=None):
        if not connection.in_atomic_block:
            with versioned_netshop_facts():
                return self._native(key, dataset, sku, payment=payment,
                    metrics=metrics, row_hash=row_hash)
        NetshopRow.objects.create(source_row_key="cross-daily-"+key,
            source_row_hash=row_hash or digest(["cross-daily", key]),
            first_import_batch_id="fixture", last_import_batch_id="fixture",
            source_row_number=1, source="jd_sku_daily", dataset=dataset,
            platform="京东", shop_name=self.query["shop"],
            business_date="2026-08-01", sku_id=sku, spu_id="SPU1",
            category="平台类目", transaction_amount_cents=payment,
            transaction_quantity=1, visitors=metrics.get("visitors", 0),
            page_views=metrics.get("pageViews", 0),
            transaction_orders=metrics.get("transactionOrders", 0),
            metrics_json=metrics, raw_json={})

    def _recollect(self, client):
        body = deepcopy(self.evidence_body)
        body.update(clientRequestId=client, sources=deepcopy(self.sources))
        self.parent = self.collect_body(body)
        self.plan = mapping_plan.build(self.sources,
            [{"salesKey": "sales", "masterKey": "master"}])
        self.report, _ = self.seed()

    def prepared(self, *, keys=None, pair=_UNSET, window="current", actor=None, **kwargs):
        return owning.prepare(self.report.id, keys or self.keys,
            self.pair if pair is _UNSET else pair, window,
            actor or self.admin, **kwargs)

    def test_real_sealed_shop_sku_partial_null_and_old_report_bytes_unchanged(self):
        before_report = self.report.snapshot_json
        before_input = self.report.workflow.input_json
        before_chunks = m.AiBusinessEvidenceChunk.objects.filter(run_id=self.parent.id).count()
        with patch("ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            result = self.prepared()
        remote.assert_not_called()
        self.assertFalse(any(table in query["sql"].lower() for query in queries
            for table in ("sales_order_lines", "netshop_rows")))
        self.assertEqual(result["schemaVersion"], owning.SCHEMA)
        self.assertFalse(result["authorityVerified"])
        self.assertTrue(result["sealedSelectedSourcesFullyReplayed"])
        self.assertEqual(result["reportBinding"]["reportId"], self.report.id)
        shop = result["material"]["shopDayRows"][0]
        self.assertEqual(shop["sourceDayStatus"]["netshopSku"],
            "partial_metric_coverage")
        self.assertEqual(shop["netshopSku"]["paymentCents"],
            {"value": 0, "presentRows": 1, "missingRows": 1})
        sku = next(row for row in result["material"]["skuDayRows"]
            if row["skuId"] == "SKU1")
        self.assertEqual(sku["netshopSku"]["paymentCents"],
            shop["netshopSku"]["paymentCents"])
        self.assertEqual(shop["erpSales"]["netSalesCents"]["value"], 120000)
        self.assertEqual(shop["netshopSpuNative"]["paymentCents"]["value"], 70)
        self.assertEqual(shop["promotion"]["spendCents"]["value"], 6000)
        self.report.refresh_from_db(); self.report.workflow.refresh_from_db()
        self.assertEqual((self.report.snapshot_json, self.report.workflow.input_json),
            (before_report, before_input))
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(
            run_id=self.parent.id).count(), before_chunks)

    def test_wrong_actor_pair_cross_shop_and_window_are_closed(self):
        for kwargs in ({"actor": self.viewer},
                {"actor": self.user("daily-other@example.invalid", "admin", None)},
                {"pair": "0"*64}, {"window": "previous"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AiError):
                self.prepared(**kwargs)
        self.sources.append({"key": "other-sku", "domain": "netshop",
            "query": {**{key: value for key, value in self.query.items()
                if key != "channel"}, "shop": "另一店", "dataset": "sku"}})
        self._recollect("cross-shop-extra")
        wrong = deepcopy(self.keys)
        wrong["netshopSku"]["current"] = "other-sku"
        with self.assertRaises(AiError):
            self.prepared(keys=wrong)

    def test_missing_comparison_source_keeps_null_and_no_erp_pair(self):
        result = self.prepared(pair=None, window="yearAgo")
        self.assertEqual(result["material"]["skuDayRows"], [])
        self.assertTrue(all(row["sourceDayStatus"]["erpSales"] == "missing_source"
            and row["erpSales"]["netSalesCents"]["value"] is None
            and row["promotion"]["spendCents"]["value"] is None
            for row in result["material"]["shopDayRows"]))

    def test_duplicate_native_source_hash_fails_even_if_sealed_page_ids_differ(self):
        original = NetshopRow.objects.get(source_row_key="cross-daily-sku-1")
        self._native("duplicate-content", "sku_daily", "SKU1", payment=10,
            metrics={"transactionAmountCents": 10, "transactionQuantity": 1,
                "visitors": 2},
            row_hash=original.source_row_hash)
        self._recollect("duplicate-native-hash")
        with self.assertRaises(AiError) as failure:
            self.prepared()
        reasons, cause = [], failure.exception
        while cause is not None:
            reasons.append(str(cause))
            cause = cause.__cause__
        self.assertTrue(any("源内容摘要重复" in reason for reason in reasons), reasons)

    def test_final_account_revocation_and_capacity_refuse_before_return(self):
        with self.assertRaises(AiError):
            self.prepared(erp_scratch_bytes=4096)
        def revoke(event):
            if event == {"stage": "cross_source_daily", "phase": "complete"}:
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with self.assertRaises(AiError):
            self.prepared(checkpoint=revoke)
