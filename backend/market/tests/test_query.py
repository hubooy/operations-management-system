from __future__ import annotations

from datetime import date

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from market.errors import MarketApiError
from market.models import (
    MarketNetshopProjection,
    MarketNetshopProjectionControl,
    MarketRankingEntry,
)
from market.query import _projection_metrics, _sales_metrics
from sales.auth import Principal


PRINCIPAL = Principal(
    email="market-query-test@example.test",
    display_name="Market query test",
    role="analyst",
    scope=None,
)


class MarketSalesMetricsTests(SimpleTestCase):
    @staticmethod
    def rows(count: int) -> list[MarketRankingEntry]:
        return [
            MarketRankingEntry(id=index + 1, sku_code=f"SKU-{index:04d}")
            for index in range(count)
        ]

    def test_chunks_products_and_long_date_ranges_with_one_revision(self) -> None:
        calls: list[dict[str, object]] = []

        def loader(principal: Principal, request: dict[str, object]):
            self.assertIs(principal, PRINCIPAL)
            self.assertLessEqual(len(request["productCodes"]), 1_000)
            start = request["startDate"]
            end = request["endDate"]
            self.assertIsInstance(start, str)
            self.assertIsInstance(end, str)
            self.assertLessEqual((date.fromisoformat(end) - date.fromisoformat(start)).days, 730)
            calls.append(request)
            return (
                {
                    "rows": [
                        {
                            "productCode": product_code,
                            "owned": product_code == "SKU-0000",
                            "ownSalesCents": 1,
                        }
                        for product_code in request["productCodes"]
                    ]
                },
                "11:7",
            )

        metrics, revision = _sales_metrics(
            PRINCIPAL,
            self.rows(1_001),
            {"startDate": "2020-01-01", "endDate": "2024-12-31"},
            loader,
        )

        self.assertEqual(len(calls), 6)
        self.assertEqual(revision, "11:7")
        self.assertEqual(metrics["SKU-0000"], {"owned": True, "ownSalesCents": 3})
        self.assertEqual(metrics["SKU-1000"], {"owned": False, "ownSalesCents": 3})

    def test_fails_closed_when_chunk_revisions_drift(self) -> None:
        calls = 0

        def loader(_principal: Principal, request: dict[str, object]):
            nonlocal calls
            calls += 1
            return (
                {
                    "rows": [
                        {
                            "productCode": product_code,
                            "owned": False,
                            "ownSalesCents": 0,
                        }
                        for product_code in request["productCodes"]
                    ]
                },
                f"{calls}:7",
            )

        with self.assertRaises(MarketApiError) as raised:
            _sales_metrics(
                PRINCIPAL,
                self.rows(1_001),
                {"startDate": None, "endDate": None},
                loader,
            )
        self.assertEqual(raised.exception.status, 503)


class MarketProjectionMetricsTests(TestCase):
    def test_uses_date_bounded_projection_series_and_preserves_inclusive_ranges(self) -> None:
        MarketNetshopProjectionControl.objects.update_or_create(
            id=1,
            defaults={"active_revision": "projection-test", "active_total": 6},
        )
        MarketNetshopProjection.objects.bulk_create(
            [
                MarketNetshopProjection(
                    projection_revision="projection-test",
                    projection_key="identity-sku-1",
                    kind="identity",
                    sku_id="SKU-1",
                    product_code="P-1",
                ),
                MarketNetshopProjection(
                    projection_revision="projection-test",
                    projection_key="metric-before",
                    kind="metric",
                    source="jd_sku_daily",
                    dataset="sku_daily",
                    business_date="2026-07-31",
                    sku_id="SKU-1",
                    transaction_amount_cents=90_000,
                ),
                MarketNetshopProjection(
                    projection_revision="projection-test",
                    projection_key="metric-first-a",
                    kind="metric",
                    source="jd_sku_daily",
                    dataset="sku_daily",
                    business_date="2026-08-01",
                    sku_id="SKU-1",
                    transaction_amount_cents=1_000,
                ),
                MarketNetshopProjection(
                    projection_revision="projection-test",
                    projection_key="metric-first-b",
                    kind="metric",
                    source="jd_sku_daily",
                    dataset="sku_daily",
                    business_date="2026-08-01",
                    sku_id="SKU-1",
                    transaction_amount_cents=500,
                ),
                MarketNetshopProjection(
                    projection_revision="projection-test",
                    projection_key="metric-middle",
                    kind="metric",
                    source="jd_sku_daily",
                    dataset="sku_daily",
                    business_date="2026-08-15",
                    sku_id="SKU-1",
                    transaction_amount_cents=2_000,
                ),
                MarketNetshopProjection(
                    projection_revision="projection-test",
                    projection_key="metric-after",
                    kind="metric",
                    source="jd_sku_daily",
                    dataset="sku_daily",
                    business_date="2026-09-01",
                    sku_id="SKU-1",
                    transaction_amount_cents=80_000,
                ),
            ]
        )
        rows = [
            MarketRankingEntry(
                id=101,
                period_start="2026-08-01",
                period_end="2026-08-31",
                ranking_dimension="SKU",
                sku_code="SKU-1",
                gmv_cents=100,
            ),
            MarketRankingEntry(
                id=102,
                period_start="2026-08-15",
                period_end="2026-08-15",
                ranking_dimension="SKU",
                sku_code="SKU-1",
                gmv_cents=200,
            ),
        ]

        with CaptureQueriesContext(connection) as queries:
            effective, owned = _projection_metrics(rows)

        self.assertEqual(effective, {101: 3_500, 102: 2_000})
        self.assertEqual(owned, {"SKU-1", "P-1"})
        metric_sql = next(
            item["sql"]
            for item in queries.captured_queries
            if "market_netshop_projection" in item["sql"] and "jd_sku_daily" in item["sql"]
        )
        self.assertIn("business_date", metric_sql)
        self.assertIn("2026-08-01", metric_sql)
        self.assertIn("2026-08-31", metric_sql)
