from __future__ import annotations

from unittest.mock import patch
from urllib.parse import urlencode

from django.db import connection
from django.test import TestCase
from django.core.cache import cache
from django.test.utils import CaptureQueriesContext
from django.utils.encoding import iri_to_uri

from .factories import TEST_SECRET, install_fixture, make_line, signed_headers
from sales.models import SalesOrderLine


class SalesApiContractTests(TestCase):
    def setUp(self) -> None:
        cache.clear()
        install_fixture()

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_brand_filter_uses_exact_erp_identity_and_preserves_refunds(self):
        from sales.models import ErpProductMaster, SalesDataRevision
        ErpProductMaster.objects.filter(product_code="P1").update(brand="志高")
        # A name match without the ERP brand must never count toward the brand.
        SalesOrderLine.objects.filter(product_code="P2").update(product_name="志高字样测试商品")
        base = "/api/sales/category-analysis?startDate=2026-08-01&endDate=2026-08-02&pageSize=20&"
        url = base + urlencode({"brand": "志高"})
        result = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()["summary"]["netSalesCents"], 8000)
        self.assertEqual(result.json()["summary"]["netQuantity"], 1)
        self.assertEqual(result.json()["filtersApplied"]["brands"], ["志高"])
        different = base + urlencode({"brand": "志"})
        self.assertEqual(self.client.get(different, headers=signed_headers(different)).json()["summary"]["netSalesCents"], 0)
        ErpProductMaster.objects.filter(product_code="P1").update(brand="其他")
        SalesDataRevision.objects.filter(domain="erp").update(revision=4)
        self.assertEqual(self.client.get(url, headers=signed_headers(url)).json()["summary"]["netSalesCents"], 0)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_summary_multicode_query_accepts_1000_characters_and_rejects_overflow(self) -> None:
        query = ",".join(f"SKU-{index:05d}" for index in range(100)) + "Z"
        self.assertEqual(len(query), 1000)
        for value, expected_status in ((query, 200), (query + "Z", 400)):
            url = "/api/sales/summary?range=custom&startDate=2026-08-01&endDate=2026-08-02&" + urlencode({"productQuery": value})
            response = self.client.get(url, headers=signed_headers(url))
            self.assertEqual(response.status_code, expected_status, response.content)
            if expected_status == 400:
                self.assertIn("1000", response.json()["error"])

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_summary_preserves_metrics_cutoff_and_revision_headers(self) -> None:
        url = "/api/sales/summary?range=custom&startDate=2026-08-01&endDate=2026-08-02"
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["current"]["grossSalesCents"], 16_000)
        self.assertEqual(payload["current"]["refundAmountCents"], 2_000)
        self.assertEqual(payload["current"]["netSalesCents"], 14_000)
        self.assertEqual(payload["current"]["netQuantity"], 2)
        self.assertAlmostEqual(payload["current"]["grossMarginRate"], 0.3)
        self.assertEqual(payload["dataCutoffDate"], "2026-08-02")
        self.assertEqual(payload["previousStartDate"], "2026-07-01")
        self.assertEqual(payload["previousEndDate"], "2026-07-02")
        self.assertEqual(payload["latestBatch"]["id"], "batch-1")
        self.assertEqual(response["X-Sales-Data-Revision"], "7:3")
        self.assertEqual(response["X-Sales-Source-Revision"], "7:3")
        self.assertEqual(response["Cache-Control"], "no-store")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_summary_cache_is_revision_keyed(self) -> None:
        url = "/api/sales/summary?range=custom&startDate=2026-08-01&endDate=2026-08-02&view=dashboard"
        first = self.client.get(url, headers=signed_headers(url))
        second = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(first["X-Sales-Overview-Cache"], "miss")
        self.assertEqual(second["X-Sales-Overview-Cache"], "hit")
        from sales.models import SalesDataRevision

        SalesDataRevision.objects.filter(domain="sales").update(revision=8)
        third = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(third["X-Sales-Overview-Cache"], "miss")
        self.assertEqual(third["X-Sales-Data-Revision"], "8:3")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_dashboard_projection_is_bounded(self) -> None:
        url = "/api/sales/summary?range=custom&startDate=2026-08-01&endDate=2026-08-02&view=dashboard"
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["projection"], "dashboard")
        self.assertEqual(payload["previousStartDate"], "2026-07-01")
        self.assertEqual(payload["previousEndDate"], "2026-07-02")
        self.assertNotIn("filterOptions", payload)
        self.assertNotIn("yearAgoDaily", payload)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_full_and_category_contracts_accept_the_366_day_boundary(self) -> None:
        summary_url = "/api/sales/summary?range=custom&startDate=2025-08-02&endDate=2026-08-02"
        summary = self.client.get(summary_url, headers=signed_headers(summary_url))
        self.assertEqual(summary.status_code, 200, summary.content)
        self.assertEqual(summary.json()["current"]["netSalesCents"], 14_000)
        self.assertEqual(summary.json()["comparisonDayCount"], 366)

        category_url = "/api/sales/category-analysis?startDate=2025-08-02&endDate=2026-08-02&pageSize=100"
        category = self.client.get(category_url, headers=signed_headers(category_url))
        self.assertEqual(category.status_code, 200, category.content)
        self.assertEqual(category.json()["summary"]["netSalesCents"], 14_000)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_category_analysis_uses_erp_category_and_keeps_refunds(self) -> None:
        url = "/api/sales/category-analysis?startDate=2026-08-01&endDate=2026-08-02&pageSize=100"
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["summary"]["netSalesCents"], 14_000)
        self.assertEqual(payload["summary"]["categoryCount"], 3)
        water = next(item for item in payload["details"]["items"] if item["category"] == "饮水设备")
        self.assertEqual(water["netSalesCents"], 8_000)
        self.assertEqual(water["netQuantity"], 1)
        self.assertEqual(water["currentWeekNetSalesCents"], 8_000)
        self.assertEqual(water["previousWeekNetSalesCents"], 0)
        self.assertEqual(payload["uncategorized"]["netSalesCents"], 1_000)
        self.assertFalse(any(item["category"] == "旧类目" for item in payload["details"]["items"]))

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_category_queries_use_materialized_projection_without_runtime_trim_or_substr(self) -> None:
        url = "/api/sales/category-analysis?startDate=2026-08-01&endDate=2026-08-02&pageSize=100"
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 200, response.content)
        sql = "\n".join(query["sql"] for query in captured.captured_queries).lower()
        self.assertNotIn("trim(", sql)
        self.assertNotIn("substr(", sql)
        self.assertNotIn("erp_product_master", sql)
        self.assertLessEqual(len(captured), 10)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_category_detail_uses_platform_plus_shop_identity(self) -> None:
        url = iri_to_uri("/api/sales/category-analysis/detail?startDate=2026-08-01&endDate=2026-08-02&category=饮水设备")
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["totals"], {"netSalesCents": 8_000, "platformCount": 1, "shopCount": 1})
        self.assertEqual(payload["platforms"][0]["platform"], "京东")
        self.assertEqual(payload["platforms"][0]["shops"][0]["shop"], "京东一店")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_summary_preserves_raw_shop_whitespace_while_category_contract_trims_it(self) -> None:
        make_line(
            6, "L6", platform=" 京东 ", shop_name=" 京东一店 ", allocated_amount_cents=1_000,
            cost_amount_cents=700, gross_profit_cents=300, ship_time="2026-08-02 14:00:00",
        ).save(force_insert=True)
        summary_url = iri_to_uri("/api/sales/summary?range=custom&startDate=2026-08-01&endDate=2026-08-02&outlet=京东%1F京东一店")
        summary = self.client.get(summary_url, headers=signed_headers(summary_url)).json()
        self.assertEqual(summary["current"]["netSalesCents"], 13_000)
        detail_url = iri_to_uri("/api/sales/category-analysis/detail?startDate=2026-08-01&endDate=2026-08-02&category=饮水设备")
        detail = self.client.get(detail_url, headers=signed_headers(detail_url)).json()
        self.assertEqual(detail["totals"]["netSalesCents"], 9_000)
        self.assertEqual(detail["totals"]["shopCount"], 1)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_scope_is_intersected_and_empty_scope_denied(self) -> None:
        url = iri_to_uri("/api/sales/category-analysis?startDate=2026-08-01&endDate=2026-08-02&platform=天猫")
        scope = {"warehouses": [], "channels": [], "platforms": ["京东"]}
        response = self.client.get(url, headers=signed_headers(url, scope=scope))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["netSalesCents"], 0)
        empty_scope = {"warehouses": [], "channels": [], "platforms": []}
        response = self.client.get(url, headers=signed_headers(url, scope=empty_scope))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "access_denied")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_product_name_resolution_does_not_leak_codes_outside_principal_scope(self) -> None:
        make_line(
            6, "L6", platform="京东", product_code="P-JD", product_name="同名商品",
            category="范围品类", allocated_amount_cents=2_000, cost_amount_cents=1_000,
            gross_profit_cents=1_000, ship_time="2026-08-02 14:00:00",
        ).save(force_insert=True)
        make_line(
            7, "L7", platform="天猫", shop_name="天猫一店", product_code="P-TM", product_name="同名商品",
            category="范围品类", allocated_amount_cents=9_000, cost_amount_cents=4_000,
            gross_profit_cents=5_000, ship_time="2026-08-02 15:00:00",
        ).save(force_insert=True)
        url = iri_to_uri(
            "/api/sales/category-analysis?startDate=2026-08-01&endDate=2026-08-02"
            "&productQuery=同名商品&pageSize=100"
        )
        scope = {"warehouses": [], "channels": [], "platforms": ["京东"]}
        response = self.client.get(url, headers=signed_headers(url, scope=scope))
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["filtersApplied"]["productCodes"], ["P-JD"])
        self.assertEqual(payload["summary"]["netSalesCents"], 2_000)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_summary_rejects_restricted_principal(self) -> None:
        url = "/api/sales/summary?range=custom&startDate=2026-08-01&endDate=2026-08-02"
        scope = {"warehouses": ["主仓"], "channels": [], "platforms": []}
        response = self.client.get(url, headers=signed_headers(url, scope=scope))
        self.assertEqual(response.status_code, 403)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_repeated_scalar_parameters_use_the_public_edge_first_value_contract(self) -> None:
        summary_url = (
            "/api/sales/summary?range=custom&range=today"
            "&startDate=2026-08-01&endDate=2026-08-02&view=dashboard"
        )
        summary = self.client.get(summary_url, headers=signed_headers(summary_url))
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["range"], "custom")
        self.assertEqual(summary.json()["current"]["netSalesCents"], 14_000)

        detail_url = iri_to_uri(
            "/api/sales/category-analysis/detail?startDate=2026-08-01&endDate=2026-08-02"
            "&category=饮水设备&category=制冰设备"
        )
        detail = self.client.get(detail_url, headers=signed_headers(detail_url))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["category"], "饮水设备")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_structured_outlet_overrides_legacy_shop_filter(self) -> None:
        url = iri_to_uri(
            "/api/sales/summary?range=custom&startDate=2026-08-01&endDate=2026-08-02"
            "&shop=京东二店&outlet=京东%1F京东一店&view=dashboard"
        )
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["current"]["netSalesCents"], 13_000)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_category_detail_uses_utf8_binary_tie_break_order(self) -> None:
        for identifier, platform in enumerate(["中", "é", "a", "A"], start=6):
            make_line(
                identifier, f"ORDER-{identifier}", platform=platform, shop_name=f"{platform}店",
                product_code=f"ORDER-P{identifier}", product_name=f"排序商品{identifier}",
                category="并列品类", allocated_amount_cents=1_000, cost_amount_cents=600,
                gross_profit_cents=400, ship_time="2026-08-03 10:00:00",
            ).save(force_insert=True)
        url = iri_to_uri(
            "/api/sales/category-analysis/detail?startDate=2026-08-03&endDate=2026-08-03"
            "&category=并列品类"
        )
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            [item["platform"] for item in response.json()["platforms"]],
            ["A", "a", "é", "中"],
        )

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_signature_tampering_fails_closed(self) -> None:
        url = "/api/sales/summary?range=custom&startDate=2026-08-01&endDate=2026-08-02"
        headers = signed_headers(url)
        headers["X-Teruisi-Signature"] = "v1=" + "0" * 64
        response = self.client.get(url, headers=headers)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "authentication_required")

    @patch.dict(
        "os.environ",
        {
            "TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET,
            "TERUISI_DJANGO_SIGNATURE_MAX_AGE_SECONDS": "not-a-number",
        },
    )
    def test_invalid_signature_age_configuration_fails_as_service_unavailable(self) -> None:
        url = "/api/sales/summary?range=custom&startDate=2026-08-01&endDate=2026-08-02"
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "service_unavailable")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    @patch("sales.views.revision_token", side_effect=["1:1", "2:1", "2:1", "3:1"])
    def test_continuously_changing_revision_returns_retryable_503(self, _revision) -> None:
        url = "/api/sales/summary?range=custom&startDate=2026-08-01&endDate=2026-08-02&view=dashboard"
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "sales_overview_revision_changed")
        self.assertEqual(response["Retry-After"], "1")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    @patch("sales.views.revision_token", side_effect=["7:3", "7:3", "7:3", "99:99"])
    def test_payload_header_uses_the_revision_verified_with_payload(self, revision) -> None:
        url = "/api/sales/summary?range=custom&startDate=2026-08-01&endDate=2026-08-02&view=dashboard"
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Sales-Data-Revision"], "7:3")
        self.assertEqual(revision.call_count, 3)
