"""Signed internal-only market observation GET over actual sealed pages."""
from dataclasses import replace
from unittest.mock import patch
from urllib.parse import urlencode

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from sales.tests.factories import TEST_SECRET, signed_headers
from . import business_market_observation as owning
from . import business_market_observation_runtime_tools as runtime
from . import test_business_market_observation as fixtures
from .policy import digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketObservationRuntimeRouteTests(djtest.TransactionTestCase):
    user = fixtures.BusinessMarketObservationTests.user
    call = fixtures.BusinessMarketObservationTests.call
    bundle = fixtures.BusinessMarketObservationTests.bundle
    input_for = fixtures.BusinessMarketObservationTests.input_for
    insert = fixtures.BusinessMarketObservationTests.insert
    seed = fixtures.BusinessMarketObservationTests.seed
    collect_body = fixtures.BusinessMarketObservationTests.collect_body
    market_row = fixtures.BusinessMarketObservationTests.market_row
    setUp = fixtures.BusinessMarketObservationTests.setUp
    params = fixtures.BusinessMarketObservationTests.params

    def route(self, params=None, *, actor=None, role="ai_reader", method="GET",
              signed=True, suffix="market-observation", report_id=None,
              extra_query=""):
        actor = actor or self.admin
        query = {key: str(value) for key, value in (params or self.params()).items()}
        url = (f"/api/ai/reports/{report_id or self.report.id}/{suffix}?"
            + urlencode(query) + extra_query)
        raw = b"{}" if method == "POST" else b""
        headers = (signed_headers(url, email=actor.email, role=actor.role,
            scope=actor.scope, method=method, body=raw) if signed else {})
        with (patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}),
                djtest.override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET,
                    DJANGO_PROCESS_ROLE=role), patch("ai_assistant.views.authority")):
            return self.client.generic(method, url, data=raw,
                content_type="application/json", headers=headers)

    def checked(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertLessEqual(len(response.content), 38000)
        value = response.json()
        self.assertEqual(value["responseDigest"], digest({key: child for key, child
            in value.items() if key != "responseDigest"}))
        return value

    def test_signed_page_and_row_equal_owning_and_keep_top_null(self):
        page = self.checked(self.route())
        self.assertEqual(page, owning.page(self.report.id, self.params(), self.admin))
        self.assertFalse(page["authority"]["ownProductIdentityVerified"])
        row = next(item for item in page["table"]["rows"]
            if item["skuId"] == "market-77")
        selected = self.params(rowIndex=row["rowIndex"], rowId=row["rowId"])
        exact = self.checked(self.route(selected))
        self.assertEqual(exact, owning.read_row(self.report.id, "market", "market-prior",
            "2026-08-03", "2026-07-31", row["rowIndex"], row["rowId"], self.admin))
        self.assertIsNone(exact["row"]["baseline"]["metrics"])

    def test_bad_query_method_role_and_cross_report_reject_before_scan(self):
        for changed in (self.params(offset="01"), self.params(offset="-1"),
                self.params(limit="10"), self.params(rowIndex="1"),
                self.params(rowId="a"*64), self.params(rowIndex="1", rowId="a"*64, offset="0"),
                self.params(currentObservationDate="2026-02-30"), self.params(extra="x")):
            with self.subTest(changed=changed), patch.object(runtime.owning, "table") as scan:
                self.assertEqual(self.route(changed).status_code, 400)
                scan.assert_not_called()
        with patch.object(runtime.owning, "table") as scan:
            self.assertEqual(self.route(extra_query="&currentSourceKey=market").status_code, 400)
            scan.assert_not_called()
        self.assertEqual(self.route(method="POST").status_code, 405)
        self.assertEqual(self.route(role="ai_writer").status_code, 403)
        self.assertEqual(self.route(signed=False).status_code, 401)
        self.assertEqual(self.route(suffix="market-observation/extra").status_code, 404)
        self.assertEqual(self.route(report_id="missing-report").status_code, 404)
        self.assertEqual(self.route(report_id=self.old_report.id).status_code, 409)
        self.assertEqual(self.route(self.params(baselineSourceKey="market-other")).status_code, 409)
        for actor, expected in ((self.viewer, 403),
                (self.user("market-observation-other@example.invalid", "admin", None), 404),
                (replace(self.admin, scope={"warehouses": [], "channels": [],
                    "platforms": ["京东"]}), 403)):
            self.assertEqual(self.route(actor=actor).status_code, expected)

    def test_signed_get_uses_only_sealed_pages_and_final_permission(self):
        with (patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote,
                CaptureQueriesContext(connection) as queries):
            self.checked(self.route())
        model.assert_not_called(); remote.assert_not_called()
        self.assertFalse(any("market_ranking_entries" in item["sql"].lower()
            or item["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
            for item in queries))
