import json
from unittest.mock import patch

from django.test import TestCase

from finance.models import FinanceLine, FinanceMonth, FinanceTarget, FinanceWriteAuthority
from finance.annual_progress import annual_progress
from finance.import_service import import_finance_payload
from finance.target_service import upsert_target
from sales.tests.factories import TEST_SECRET, signed_headers
from .factories import prepared_payload


@patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
class AnnualProgressTests(TestCase):
    def setUp(self):
        FinanceWriteAuthority.objects.filter(id=1).update(status="postgres")
        import_finance_payload(prepared_payload("2026-01", "2026-03"), "fixture@example.invalid")

    def target(self, **overrides):
        return upsert_target({"periodType": "year", "periodKey": "2026", "platform": "京东", "shopName": "同名店",
                              "salesTargetCents": 360_000, "profitTargetCents": 90_000,
                              "grossMarginBps": 4_500, "promotionFeeRatioBps": 600, **overrides})[0]

    def test_annual_uses_only_whole_shop_year_target_and_monthly_facts(self):
        self.target()
        self.target(periodType="month", periodKey="2026-03", salesTargetCents=1)
        self.target(category="风扇", salesTargetCents=2)
        payload = annual_progress("2026", 1, 100)
        jd = next(row for row in payload["items"] if row["platform"] == "京东")
        tm = next(row for row in payload["items"] if row["platform"] == "天猫")
        self.assertEqual(jd["netSalesCents"], 180_000)
        self.assertEqual(jd["profitCents"], 45_000)
        self.assertEqual(jd["salesProgress"], 0.5)
        self.assertEqual(jd["profitProgress"], 0.5)
        self.assertEqual(jd["grossMarginBps"], 4_000)
        self.assertEqual(jd["grossMarginGapBps"], -500)
        self.assertEqual(jd["promotionFeeRatioBps"], 500)
        self.assertEqual(jd["promotionFeeGapBps"], -100)
        self.assertEqual(tm["netSalesCents"], 120_000)
        self.assertIsNone(tm["salesProgress"])
        self.assertEqual(payload["cutoffMonth"], "2026-03")
        self.assertEqual(jd["missingMonths"], ["2026-02"])
        self.assertEqual(jd["missingGrossMarginMonths"], ["2026-02"])

    def test_same_name_shops_remain_separate_across_pages(self):
        self.target()
        first, second = annual_progress("2026", 1, 1), annual_progress("2026", 2, 1)
        self.assertEqual(first["pagination"]["total"], 2)
        self.assertTrue(first["pagination"]["truncated"])
        self.assertNotEqual(first["items"][0]["key"], second["items"][0]["key"])
        self.assertFalse(second["pagination"]["truncated"])

    def test_target_without_financial_data_is_not_zero_completion(self):
        self.target(periodKey="2025")
        row = annual_progress("2025", 1, 100)["items"][0]
        self.assertIsNone(row["netSalesCents"])
        self.assertIsNone(row["salesProgress"])

    def test_missing_shop_metric_discloses_gap_and_negative_profit_is_preserved(self):
        self.target()
        FinanceLine.objects.filter(month="2026-03", group_name="京东", metric_key="net_sales").delete()
        FinanceLine.objects.filter(month="2026-01", group_name="京东", metric_key="profit").update(amount_cents=-10_000)
        row = next(row for row in annual_progress("2026", 1, 100)["items"] if row["platform"] == "京东")
        self.assertEqual(row["netSalesCents"], 60_000)
        self.assertEqual(row["profitCents"], -10_000)
        self.assertLess(row["profitProgress"], 0)
        self.assertEqual(row["missingMonths"], ["2026-02", "2026-03"])

    def test_incomplete_import_and_zero_targets_do_not_generate_progress(self):
        self.target(salesTargetCents=0, profitTargetCents=0)
        FinanceMonth.objects.filter(month="2026-03").update(status="processing")
        payload = annual_progress("2026", 1, 100)
        self.assertEqual(payload["cutoffMonth"], "2026-01")
        self.assertTrue(all(row["salesProgress"] is None for row in payload["items"]))

    def test_reimport_replaces_actuals_without_double_counting_or_changing_targets(self):
        self.target()
        snapshot = list(FinanceTarget.objects.values())
        imported = prepared_payload("2026-01", "2026-03")
        from .factories import changed_raw_file
        imported = changed_raw_file(imported)
        for row in imported["months"][1]["lines"]:
            if row["groupName"] == "京东" and row["metricKey"] == "net_sales":
                row["amountCents"] = 140_000
        import_finance_payload(imported, "fixture@example.invalid")
        jd = next(row for row in annual_progress("2026", 1, 100)["items"] if row["platform"] == "京东")
        self.assertEqual(jd["netSalesCents"], 200_000)
        self.assertEqual(list(FinanceTarget.objects.values()), snapshot)

    def test_annual_endpoint_validates_year_and_enforces_scope(self):
        url = "/api/finance/targets?view=annual&year=2026"
        response = self.client.get(url, headers=signed_headers(url, role="viewer"))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("X-Finance-Data-Revision", response)
        for query in ("view=annual", "view=annual&year=no", "view=annual&year=2026&year=2025"):
            url = "/api/finance/targets?" + query
            self.assertEqual(self.client.get(url, headers=signed_headers(url)).status_code, 400)
        url = "/api/finance/targets?view=annual&year=2026"
        self.assertEqual(self.client.get(url).status_code, 401)

    def test_year_target_list_excludes_month_category_and_other_year(self):
        self.target()
        self.target(periodType="month", periodKey="2026-03")
        self.target(category="风扇")
        self.target(periodKey="2025")
        url = "/api/finance/targets?view=items&year=2026"
        result = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()["pagination"]["total"], 1)
