"""Signed internal reader route; real sealed sources, no model registration."""
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import patch
from urllib.parse import urlencode
from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from access_control.models import AppUser
from sales.tests.factories import signed_headers, TEST_SECRET
from . import business_promotion_keyword_sku as owning
from . import test_business_promotion_keyword_sku as fixtures
from .policy import digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionRuntimeRouteTests(djtest.TransactionTestCase):
    user = fixtures.BusinessPromotionKeywordSkuTests.user
    call = fixtures.BusinessPromotionKeywordSkuTests.call
    bundle = fixtures.BusinessPromotionKeywordSkuTests.bundle
    input_for = fixtures.BusinessPromotionKeywordSkuTests.input_for
    insert = fixtures.BusinessPromotionKeywordSkuTests.insert
    seed = fixtures.BusinessPromotionKeywordSkuTests.seed
    collect_body = fixtures.BusinessPromotionKeywordSkuTests.collect_body
    ad = fixtures.BusinessPromotionKeywordSkuTests.ad
    setUp = fixtures.BusinessPromotionKeywordSkuTests.setUp

    def route(self, params=None, *, actor=None, role="ai_reader", method="GET", signed=True,
              extra_query="", path_suffix="", report_id=None):
        actor = actor or self.admin
        query = {"sourceKey": "ads", "view": "keyword_sku", **(params or {})}
        url = f"/api/ai/reports/{report_id or self.report.id}/promotion-keyword-sku{path_suffix}?"+urlencode(query)+extra_query
        body = b"{}" if method == "POST" else b""
        headers = signed_headers(url, email=actor.email, role=actor.role, scope=actor.scope, method=method, body=body) if signed else {}
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}), djtest.override_settings(
                DJANGO_INTERNAL_SECRET=TEST_SECRET, DJANGO_PROCESS_ROLE=role), patch("ai_assistant.views.authority"):
            return self.client.generic(method, url, data=body, content_type="application/json", headers=headers)

    def checked(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("X-AI-Revision", response)
        self.assertLessEqual(len(response.content), 38000)
        result = response.json()
        self.assertEqual(result["schemaVersion"], owning.SCHEMA)
        self.assertEqual(result["responseDigest"], digest({k:v for k,v in result.items() if k != "responseDigest"}))
        return result

    def test_signed_full_owning_envelope_and_baseline_match_direct_service(self):
        for view in ("keyword_sku", "keyword_sku_context"):
            params = {"sourceKey": "ads", "view": view, "baselineKey": "ads-previous", "offset": 0, "limit": 20}
            expected = owning.page(self.report.id, params, self.admin)
            actual = self.checked(self.route({**params, "offset": "0", "limit": "20"}))
            self.assertEqual(actual, expected)
            self.assertTrue(actual["authority"]["reportBindingVerified"])
            self.assertFalse(actual["table"]["authorityVerified"])
            self.assertEqual(sum(row["metrics"]["spendCents"]["value"] for row in actual["table"]["rows"]), 1050)

    def test_exact_row_reference_roundtrip_and_wrong_binding_rejected(self):
        page = self.checked(self.route())
        row = page["table"]["rows"][0]
        params = {"rowIndex": str(row["rowIndex"]), "rowId": row["id"]}
        result = self.checked(self.route(params))
        self.assertEqual(result["row"], row)
        self.assertEqual(result["bindingDigest"], page["bindingDigest"])
        for change in ({"rowId": "0"*64}, {"view": "keyword_sku_context"}, {"baselineKey": "ads-previous"}):
            with self.subTest(change=change):
                self.assertEqual(self.route({**params, **change}).status_code, 409)

    def test_strict_queries_reject_duplicates_mixed_modes_and_noncanonical_numbers(self):
        invalid = ({"offset": "01"}, {"offset": "-1"}, {"offset": "1.0"}, {"offset": "1e2"},
            {"offset": "99999999"}, {"limit": "10"}, {"limit": "020"}, {"unknown": "x"},
            {"rowIndex": "0"}, {"rowId": "a"*64}, {"rowIndex": "0", "rowId": "a"*64, "limit": "20"},
            {"rowIndex": "0", "rowId": "a"*64, "offset": "0"}, {"rowIndex": "true", "rowId": "a"*64})
        for params in invalid:
            with self.subTest(params=params), patch.object(owning, "table") as scan:
                self.assertEqual(self.route(params).status_code, 400)
                scan.assert_not_called()
        with patch.object(owning, "table") as scan:
            self.assertEqual(self.route(extra_query="&sourceKey=ads").status_code, 400)
            scan.assert_not_called()

    def test_auth_reader_methods_and_precise_path_boundaries(self):
        others = self.user("promotion-runtime-other@example.invalid", "admin", None)
        for actor, status in ((self.viewer, 403), (others, 404),
                (replace(self.admin, scope={"warehouses": [], "channels": [], "platforms": ["京东"]}), 403)):
            # A different administrator must not learn that the owner's report exists.
            with self.subTest(actor=actor.email): self.assertEqual(self.route(actor=actor).status_code, status)
        self.assertEqual(self.route(signed=False).status_code, 401)
        self.assertEqual(self.route(role="ai_writer").status_code, 403)
        self.assertEqual(self.route(method="POST").status_code, 405)
        self.assertEqual(self.route(path_suffix="/extra").status_code, 404)
        self.assertEqual(self.route(report_id="not-a-report").status_code, 404)

    def test_wrong_source_view_and_baseline_never_grant_authority(self):
        for params, status in (({"sourceKey": "missing"}, 404), ({"sourceKey": "sales"}, 422),
                ({"sourceKey": "master"}, 422), ({"view": "sku"}, 400), ({"baselineKey": "ads"}, 409),
                ({"baselineKey": "ads-other"}, 409)):
            with self.subTest(params=params):
                response = self.route(params)
                self.assertEqual(response.status_code, status, response.content)
                self.assertNotIn("authority", response.json())

    def test_capacity_rejection_and_late_revocation_return_no_success_envelope(self):
        with patch.object(owning, "MAX_RESPONSE_BYTES", 200):
            response = self.route()
        self.assertEqual(response.status_code, 413)
        self.assertNotIn("authority", response.json())
        pure = owning.promotion_keyword_sku.table
        @contextmanager
        def revoke(*args, **kwargs):
            with pure(*args, **kwargs) as table:
                yield table
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with patch.object(owning.promotion_keyword_sku, "table", revoke): response = self.route()
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("authority", response.json())

    def test_read_does_not_query_business_tables_write_or_dispatch_models(self):
        with patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as transport, CaptureQueriesContext(connection) as queries:
            self.checked(self.route())
        model.assert_not_called(); transport.assert_not_called()
        for query in queries:
            self.assertNotIn("netshop_rows", query["sql"].lower())
            self.assertNotIn("sales_order_lines", query["sql"].lower())
            self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")))
