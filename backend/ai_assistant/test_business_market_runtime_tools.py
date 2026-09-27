"""Signed internal GET with actual sealed market reports and owning services."""
from dataclasses import replace
from unittest.mock import patch
from urllib.parse import urlencode
from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from access_control.models import AppUser
from sales.tests.factories import signed_headers, TEST_SECRET
from . import business_market_dynamics as owning
from . import test_business_market_dynamics as fixtures
from .policy import AiError, canonical, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketRuntimeRouteTests(djtest.TransactionTestCase):
    user = fixtures.BusinessMarketDynamicsTests.user
    call = fixtures.BusinessMarketDynamicsTests.call
    bundle = fixtures.BusinessMarketDynamicsTests.bundle
    input_for = fixtures.BusinessMarketDynamicsTests.input_for
    insert = fixtures.BusinessMarketDynamicsTests.insert
    seed = fixtures.BusinessMarketDynamicsTests.seed
    collect_body = fixtures.BusinessMarketDynamicsTests.collect_body
    market_row = fixtures.BusinessMarketDynamicsTests.market_row
    setUp = fixtures.BusinessMarketDynamicsTests.setUp

    def route(self, params=None, *, actor=None, role="ai_reader", method="GET", signed=True,
              extra_query="", report_id=None, suffix="market-dynamics", exact_query=False):
        actor = actor or self.admin
        query = params if exact_query else {"sourceKey": "market", "view": "price_band", "bands": canonical(fixtures.BANDS), **(params or {})}
        url = f"/api/ai/reports/{report_id or self.report.id}/{suffix}?" + urlencode(query or {}) + extra_query
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
        value = response.json()
        self.assertEqual(value["responseDigest"], digest({k:v for k,v in value.items() if k != "responseDigest"}))
        return value

    def test_signed_price_and_rank_pages_equal_complete_owning_envelope(self):
        actual = self.checked(self.route({"offset": "0", "limit": "20"}))
        expected = owning.page(self.report.id, {"sourceKey": "market", "view": "price_band", "bands": fixtures.BANDS, "offset": 0, "limit": 20}, self.admin)
        self.assertEqual(actual, expected)
        self.assertFalse(actual["authority"]["wholeMarketCoverageVerified"])
        params = {"sourceKey": "market", "view": "rank_entry_exit", "baselineKey": "market-prior", "offset": "0", "limit": "20"}
        actual = self.checked(self.route(params, exact_query=True))
        expected = owning.page(self.report.id, {**params, "offset": 0, "limit": 20}, self.admin)
        self.assertEqual(actual, expected)
        self.assertIsNotNone(actual["table"]["pagination"]["nextOffset"])

    def test_exact_row_and_changed_bands_or_digest_reject(self):
        page = self.checked(self.route()); row = page["table"]["rows"][0]
        params = {"rowIndex": str(row["rowIndex"]), "rowId": row["rowId"]}
        found = self.checked(self.route(params))
        self.assertEqual(found["row"], row); self.assertEqual(found["bindingDigest"], page["bindingDigest"])
        for change in ({"rowId": "0"*64}, {"bands": canonical([{ "key": "other", "lowerCents": 0, "upperExclusiveCents": None}])}):
            self.assertEqual(self.route({**params, **change}).status_code, 409)

    def test_query_duplicates_unknown_mixed_modes_and_number_forms_reject_before_scan(self):
        invalid = ({"offset": "01"}, {"offset": "-1"}, {"offset": "1.0"}, {"offset": "1e2"}, {"offset": "99999999"},
            {"limit": "10"}, {"limit": "020"}, {"unknown": "x"}, {"rowIndex": "0"}, {"rowId": "a"*64},
            {"rowIndex": "0", "rowId": "a"*64, "limit": "20"}, {"rowIndex": "0", "rowId": "a"*64, "offset": "0"},
            {"baselineKey": "market-prior"}, {"view": "rank_entry_exit", "baselineKey": "market-prior"})
        for params in invalid:
            with self.subTest(params=params), patch.object(owning, "table") as scan:
                self.assertEqual(self.route(params).status_code, 400); scan.assert_not_called()
        with patch.object(owning, "table") as scan:
            self.assertEqual(self.route(extra_query="&sourceKey=market").status_code, 400); scan.assert_not_called()
        for params in ({"sourceKey": "market", "view": "price_band"}, {"sourceKey": "market", "view": "rank_entry_exit"}):
            self.assertEqual(self.route(params, exact_query=True).status_code, 400)

    def test_bands_json_duplicate_keys_nonfinite_nested_and_byte_limit_reject(self):
        for raw in ("null", "{}", "[]", '[{"key":"x","key":"y","lowerCents":0,"upperExclusiveCents":null}]',
            '[{"key":"x","lowerCents":NaN,"upperExclusiveCents":null}]', '[{"key":"x","lowerCents":true,"upperExclusiveCents":null}]',
            '[{"key":"x","lowerCents":0,"upperExclusiveCents":null,"extra":1}]', "["*1100 + "]"*1100):
            with self.subTest(raw=raw[:60]), patch.object(owning, "table") as scan:
                self.assertEqual(self.route({"bands": raw}).status_code, 400); scan.assert_not_called()
        self.assertEqual(self.route({"bands": '"' + "中"*2731 + '"'}).status_code, 413)

    def test_signed_reader_role_scope_methods_and_report_boundaries(self):
        other = self.user("market-runtime-other@example.invalid", "admin", None)
        for actor, status in ((self.viewer, 403), (other, 404), (replace(self.admin, scope={"platforms": ["京东"], "channels": [], "warehouses": []}), 403)):
            self.assertEqual(self.route(actor=actor).status_code, status)
        self.assertEqual(self.route(signed=False).status_code, 401)
        self.assertEqual(self.route(role="ai_writer").status_code, 403)
        self.assertEqual(self.route(method="POST").status_code, 405)
        self.assertEqual(self.route(suffix="market-dynamics/extra").status_code, 404)
        self.assertEqual(self.route(report_id="missing-report").status_code, 404)
        self.assertEqual(self.route({"sourceKey": "sales"}).status_code, 422)

    def test_capacity_and_final_revocation_return_errors_without_authority(self):
        with patch.object(owning, "MAX_RESPONSE_BYTES", 100): response = self.route()
        self.assertEqual(response.status_code, 413); self.assertNotIn("authority", response.json())
        original = owning._response
        def revoke(*args, **kwargs):
            value = original(*args, **kwargs)
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return value
        with patch.object(owning, "_response", side_effect=revoke): response = self.route()
        self.assertEqual(response.status_code, 403); self.assertNotIn("authority", response.json())

    def test_read_has_no_business_or_external_calls_and_old_screening_gate_stays_closed(self):
        with patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            self.checked(self.route())
        model.assert_not_called(); remote.assert_not_called()
        for query in queries:
            self.assertNotIn("market_ranking_entries", query["sql"].lower())
            self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")))
        # This integrated report did not become a screening report when its new
        # market endpoint was added. Preserve the existing reader's rejection.
        from . import business_screening_tools
        params = {"runId": self.parent.id, "screeningId": "unbound",
                  "mode": "native", "dimension": "shop", "sourceKey": "sales"}
        with self.assertRaises(AiError) as caught:
            business_screening_tools.read(self.report.id, "analysis", params, self.admin)
        self.assertEqual(self.route(params, exact_query=True, suffix="screening/analysis").status_code, caught.exception.status)
