"""Isolated PostgreSQL coverage for internal mapped analysis, no public route."""
from copy import deepcopy
from collections import Counter
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from netshop.models import NetshopRow
from sales.models import SalesOrderLine
from sales.tests.factories import make_line
from business_analysis import mapped_results, mapping_plan
from business_analysis.contracts import AnalysisContractError
from . import business_budget, business_evidence as evidence, business_identity as identity
from . import business_mapped_analysis as service, test_business_identity as identity_fixtures
from .test_business_evidence import versioned_netshop_facts
from .business_sealed import Reader
from .policy import AiError, canonical, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessMappedAnalysisTests(djtest.TestCase):
    user = identity_fixtures.BusinessIdentityTests.user
    call = identity_fixtures.BusinessIdentityTests.call

    def setUp(self):
        identity_fixtures.BusinessIdentityTests.setUp(self)

    def seed(self, *, baseline=False, end_date=None, client="mapped-second"):
        body = deepcopy(self.evidence_body)
        body["clientRequestId"] = client
        if end_date:
            for source in body["sources"]: source["query"]["endDate"] = end_date
        if baseline:
            body["sources"].append({"key": "previous", "domain": "sales",
                "query": {**body["sources"][0]["query"], "window": "previous"}})
        run_id = evidence.create(body, self.admin)["item"]["id"]
        version = 1
        with patch("ai_assistant.transport.catalog", return_value=self.source_tools), patch("ai_assistant.transport.execute_tool", side_effect=self.source_execute):
            for source in body["sources"]:
                while True:
                    result = evidence.collect(run_id, {"sourceKey": source["key"], "expectedVersion": version}, self.admin, "mapped-seed")["item"]
                    version = result["version"]
                    if result["sources"][source["key"]]["complete"]: break
        evidence.finish(run_id, {"expectedVersion": version, "action": "seal"}, self.admin)
        return run_id

    def master(self, number, code, sku, spu):
        with versioned_netshop_facts():
            NetshopRow.objects.create(source_row_key=f"mapped-master-{number}", source_row_hash=f"{number:064x}",
                first_import_batch_id="master", last_import_batch_id="master", source_row_number=number,
                source="jd_product_master", dataset="product_master", platform="京东", shop_name="京东一店",
                sku_id=sku, spu_id=spu, snapshot_date="2026-08-01", raw_json={"商家编码": code},
                created_at="2026-08-01", updated_at="2026-08-01")

    def prior(self, identifier=100, **changes):
        make_line(identifier, f"mapped-prior-{identifier}", channel=self.query["channel"], online_spec_code="M1",
            ship_time="2026-07-31 10:00:00", line_ship_time="2026-07-31 10:00:00", **changes).save()

    def page(self, run_id=None, **params):
        return service.page(run_id or self.run_id, {"salesKey": "sales", "masterKey": "master", "dimension": "sku", **params}, self.admin)

    def fixed_plan(self, run_id, baseline=False):
        sources = Reader(evidence.get_run(run_id, self.admin), self.admin).sources
        choices = [{"salesKey": "sales", "masterKey": "master"}]
        if baseline: choices.append({"salesKey": "previous", "masterKey": "master"})
        plan = mapping_plan.normalize(sources, choices)
        return plan, {pair["salesKey"]: pair["pairKey"] for pair in plan["pairs"]}

    def test_all_metrics_refunds_ambiguity_and_sku_across_spu_do_not_fan_out(self):
        self.master(2, "M2", "SKU1", "SPU2")
        self.master(3, "M3", "SKU3", "SPU3")
        self.master(4, "M3", "SKU4", "SPU4")
        SalesOrderLine.objects.filter(pk=2).update(online_spec_code="M2")
        SalesOrderLine.objects.filter(pk=3).update(online_spec_code="M3")
        SalesOrderLine.objects.filter(pk=4).update(allocated_amount_cents=-2000, cost_amount_cents=-700,
            gross_profit_cents=-1500, quantity=-1, fee_allocation_cents=100)
        run_id = self.seed()
        native = evidence.analysis_table(run_id, {"sourceKey": "sales", "dimension": "shop"}, self.admin)
        result = self.page(run_id)["table"]
        rows = result["rows"]
        self.assertEqual(Counter(row["entity"]["mappingStatus"] for row in rows), {"matched": 1, "ambiguous": 1, "unmatched": 1})
        self.assertEqual(sum(row["currentRowCount"] for row in rows), 12)
        matched = next(row for row in rows if row["entity"]["mappingStatus"] == "matched")
        self.assertEqual(matched["entity"]["skuId"], "SKU1")
        self.assertEqual(matched["currentRowCount"], 10)
        for key in mapped_results.METRICS:
            self.assertEqual(sum(row["metrics"][key]["value"] for row in rows), native["rows"][0]["metrics"][key]["value"], key)
        total = {key: sum(row["metrics"][key]["value"] for row in rows) for key in mapped_results.METRICS}
        self.assertEqual((total["positiveSalesCents"], total["refundCents"], total["netSalesCents"]), (110000, 2000, 108000))
        self.assertEqual(total["grossProfitCents"], total["netSalesCents"]-total["costCents"])
        self.assertNotEqual(total["grossProfitCents"], total["reportedGrossProfitCents"])
        self.assertEqual((total["positiveQuantity"], total["returnQuantity"], total["netQuantity"]), (11, 1, 10))
        self.assertEqual(total["feeCents"], 100)
        self.assertTrue(all(row["dimensionMissing"] for row in rows if row["entity"]["mappingStatus"] != "matched"))
        spu = self.page(run_id, dimension="spu")["table"]
        self.assertEqual(sum(row["currentRowCount"] for row in spu["rows"]), 12)
        self.assertEqual({row["entity"]["spuId"] for row in spu["rows"] if row["entity"]["mappingStatus"] == "matched"}, {"SPU1", "SPU2"})

    def test_trusted_description_does_not_scan_each_pair_fact_stream_consumed_once(self):
        self.prior(); run_id = self.seed(baseline=True)
        calls = []
        original = Reader.pages
        def tracked(reader, key, checkpoint=None):
            calls.append(key)
            yield from original(reader, key, checkpoint)
        with patch.object(Reader, "pages", tracked), patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            identity.describe(run_id, "sales", "master", self.admin)
            self.assertEqual(calls, [])
            result = self.page(run_id, baselineSalesKey="previous", baselineMasterKey="master")
        self.assertEqual(Counter(calls), {"sales": 1, "previous": 1, "master": 2})
        self.assertTrue(result["table"]["dateCoverageComparable"])
        model.assert_not_called(); remote.assert_not_called()
        self.assertFalse(any(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for query in queries))
        self.assertFalse(any(any(name in query["sql"].lower() for name in ("netshop_rows", "sales_order_lines")) for query in queries))

    def test_missing_dates_and_empty_baseline_never_create_growth_or_zero_fact_rows(self):
        self.prior()
        run_id = self.seed(baseline=True, end_date="2026-08-02")
        result = self.page(run_id, baselineSalesKey="previous", baselineMasterKey="master")["table"]
        self.assertFalse(result["dateCoverageComparable"])
        self.assertTrue(all(value["changeRate"] is None and value["difference"] is None for row in result["rows"] for value in row["comparisons"].values()))
        SalesOrderLine.objects.filter(pk=100).delete()
        empty_id = self.seed(baseline=True, client="mapped-empty-before")
        empty = self.page(empty_id, baselineSalesKey="previous", baselineMasterKey="master")["table"]
        self.assertFalse(empty["dateCoverageComparable"])
        self.assertTrue(all(row["baselineMetrics"] is None and row["baselineRowCount"] is None for row in empty["rows"]))
        self.assertTrue(all(value["baseline"] is None and value["changeRate"] is None for row in empty["rows"] for value in row["comparisons"].values()))

    def test_fixed_plan_context_binding_and_lifecycle(self):
        plan, keys = self.fixed_plan(self.run_id)
        with service.table(self.run_id, plan, keys["sales"], "sku", self.admin) as table:
            header = table.header(); rows = list(table.scan())
            self.assertEqual(rows, table.page()["rows"])
            self.assertEqual(header["binding"]["evidenceRunId"], self.run_id)
            self.assertEqual(header["binding"]["master"]["sourceKey"], "master")
            self.assertLessEqual(table.stats()["combinedScratchHighWaterBytes"], 256*1024*1024)
            self.assertEqual(table.stats()["maxConcurrentMappingContexts"], 1)
            self.assertEqual(rows[0]["id"], digest([header["bindingDigest"], rows[0]["entity"]]))
        with self.assertRaises(AnalysisContractError): table.page()
        self.assertEqual(plan, self.fixed_plan(self.run_id)[0])
        with self.assertRaises(AiError):
            with service.table(self.run_id, plan, "f"*64, "sku", self.admin): pass
        altered = deepcopy(plan); altered["algorithmVersion"] = "unsupported"
        with self.assertRaises(AiError):
            with service.table(self.run_id, altered, keys["sales"], "sku", self.admin): pass

    def test_invalid_pair_dimension_baseline_and_pagination_fail_closed(self):
        for change in ({"dimension": "keyword"}, {"dimension": "daily"}, {"salesKey": "master"}, {"masterKey": "missing"},
                {"baselineSalesKey": "sales"}, {"baselineSalesKey": "sales", "baselineMasterKey": "master"},
                {"offset": "01"}, {"offset": True}, {"offset": "250001"}, {"limit": "1"}, {"unknown": "x"}):
            with self.subTest(change=change), self.assertRaises(AiError): self.page(**change)
        for actor in (self.viewer, self.user("mapped-other@example.invalid", "admin", None)):
            with self.assertRaises(AiError): service.page(self.run_id, {"salesKey":"sales","masterKey":"master","dimension":"sku"}, actor)

    def test_late_corruption_and_permission_changes_cannot_publish(self):
        original = Reader.pages
        def corrupt(reader, key, checkpoint=None):
            for page in original(reader, key, checkpoint):
                yield page
            if key == "sales": raise AiError("synthetic incomplete final verification", "conflict", 409)
        with patch.object(Reader, "pages", corrupt), self.assertRaises(AiError): self.page()
        from access_control.models import AppUser
        # Revoke inside the trusted table context, after complete computation.
        with self.assertRaises(AiError):
            with service.analyzed(self.run_id, "sales", "master", "sku", self.admin) as (table, _):
                self.assertTrue(table.page()["rows"])
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with self.assertRaises(AnalysisContractError): table.page()

    def test_old_native_and_budget_contracts_are_not_rewritten(self):
        query = {"sourceKey": "sales", "dimension": "sku"}
        before = canonical(evidence.analysis_table(self.run_id, query, self.admin))
        legacy = canonical(evidence.reconcile_products(self.run_id, {"sales": "sales", "master": "master"}, self.admin))
        result = self.page()
        self.assertEqual(before, canonical(evidence.analysis_table(self.run_id, query, self.admin)))
        self.assertEqual(legacy, canonical(evidence.reconcile_products(self.run_id, {"sales": "sales", "master": "master"}, self.admin)))
        self.assertTrue(evidence.analysis_table(self.run_id, query, self.admin)["rows"][0]["dimensionMissing"])
        row = next(row for row in result["table"]["rows"] if not row["dimensionMissing"])
        budget = {"totalBudgetCents": 10000, "reserveCents": 0, "horizonDays": 1, "observationDays": 1,
            "reviewAfterSpendBps": 5000, "minimumClicks": 1, "minimumOrderLines": 1,
            "targets": [{"sourceKey":"sales", "dimension":"sku", "rowIndex":row["rowIndex"], "rowId":row["id"],
                "weight":1,"minBudgetCents":0,"maxBudgetCents":10000,"ownerRole":"运营","minimumRoasBps":10000}],
            "scenarios":[{"name":"显式假设","cpcFactorBps":10000,"orderRateFactorBps":10000,"orderValueFactorBps":10000,"contributionMarginBps":None}]}
        with self.assertRaisesRegex(AiError, "本期推广来源"): business_budget.resolve(self.run_id, budget, self.admin)
