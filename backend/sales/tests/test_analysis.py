from unittest.mock import patch
from django.test import TestCase
from sales.auth import Principal
from sales.analysis import read_page, validate
from sales.query import SalesAccessError, SalesRequestError
from sales.models import SalesOrderLine, SalesDataRevision
from sales.tests.factories import make_line
from business_analysis.contracts import PageReconciler


class SalesAnalysisTests(TestCase):
    principal = Principal("admin@example.test", "Admin", "admin", None)

    def setUp(self):
        SalesDataRevision.objects.update_or_create(domain="sales", defaults={"revision": 1})
        self.query = {"operation": "analysis_records", "platform": "京东", "shop": "京东一店", "channel": "京东-京东一店",
            "startDate": "2026-08-01", "endDate": "2026-08-02", "limit": 1}
        for i, amount, cost, quantity in [(1, 10000, 7000, 2), (2, -2000, -1400, -1)]:
            make_line(i, f"line-{i}", channel=self.query["channel"], allocated_amount_cents=amount, cost_amount_cents=cost,
                quantity=quantity, online_spec_code="merchant-A").save()
        make_line(3, "other-channel", channel="其他渠道", allocated_amount_cents=99999).save()
        make_line(4, "excluded", channel=self.query["channel"], warehouse="刷刷仓", allocated_amount_cents=99999).save()

    def test_sales_refund_cost_and_complete_pages(self):
        first = read_page(self.principal, self.query)
        self.assertEqual(first["control"]["rowCount"], 2)
        totals = first["control"]["typedTotals"]
        self.assertEqual(totals["netSalesCents"], 8000)
        self.assertEqual(totals["positiveSalesCents"] - totals["refundCents"], 8000)
        self.assertEqual(totals["grossProfitCents"], 2400)
        self.assertEqual(totals["costCents"], 5600)
        self.assertEqual(totals["netQuantity"], 1)
        self.assertNotIn("order_no", str(first))
        self.assertEqual(first["coverage"]["missingDates"], ["2026-08-02"])
        verifier = PageReconciler()
        verifier.consume(first)
        cursor = first["pagination"]["nextCursor"]
        verifier.consume(read_page(self.principal, {**self.query, "cursor": cursor}), request_cursor=cursor)
        self.assertTrue(verifier.result()["reconciled"])

    def test_cursor_scope_revision_and_permissions_fail_closed(self):
        cursor = read_page(self.principal, self.query)["pagination"]["nextCursor"]
        for changed in ({"channel": "其他渠道"}, {"window": "yearAgo"}, {"limit": 2}):
            with self.assertRaises(SalesRequestError):
                read_page(self.principal, {**self.query, "cursor": cursor, **changed})
        with patch("sales.analysis.revision_token", side_effect=["1:0", "2:0"]), self.assertRaises(SalesRequestError):
            read_page(self.principal, self.query)
        with self.assertRaises(SalesAccessError):
            read_page(Principal("a", "a", "viewer", None), self.query)
        with self.assertRaises(SalesRequestError):
            validate({**self.query, "sql": "SELECT"})

    def test_comparison_windows_and_quantity_exclusions(self):
        make_line(5, "year-ago", channel=self.query["channel"], ship_time="2025-08-01 00:00:00", allocated_amount_cents=100).save()
        self.assertEqual(read_page(self.principal, {**self.query, "window": "yearAgo"})["control"]["typedTotals"]["netSalesCents"], 100)
        make_line(6, "adjustment", channel=self.query["channel"], product_code="ERP_PRICE_ADJUSTMENT", quantity=99, allocated_amount_cents=500).save()
        total = read_page(self.principal, self.query)["control"]["typedTotals"]
        self.assertEqual(total["netSalesCents"], 8500)
        self.assertEqual(total["netQuantity"], 1)

    def test_no_rows_exposes_channel_check_without_widening_query(self):
        page = read_page(self.principal, {**self.query, "channel": "错误渠道"})
        self.assertEqual(page["control"]["rowCount"], 0)
        self.assertFalse(page["identityCheck"]["requestedChannelSeen"])
        self.assertIn(self.query["channel"], page["identityCheck"]["knownChannels"])
        self.assertEqual(page["items"], [])
