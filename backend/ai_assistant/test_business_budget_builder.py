"""Read-only first-budget service contracts and real sealed-source integration."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest import TestCase as PureTestCase
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from business_analysis.test_budget import fixture
from sales.tests.factories import signed_headers, TEST_SECRET
from . import business_budget_builder as builder, business_evidence, test_business_budget_store as store_fixtures
from . import tests as fixtures
from .policy import AiError, canonical


class BusinessBudgetBuilderPureTests(PureTestCase):
    def setUp(self):
        self.binding = {"evidenceRunId": "evidence-test", "evidenceVersion": 3,
            "evidencePlanDigest": "1"*64, "catalogDigest": "2"*64, "sealedDigest": "3"*64}
        self.reader = SimpleNamespace(sources=[{"key": "ads", "domain": "netshop", "query": {"dataset": "promotion"}}],
            pages=lambda key: iter(()), info=lambda key: {"expected": {}})
        self.table = {"total": 2, "sourceMetadata": {"coverage": {"status": "dates_present"}},
            "rows": [{"rowIndex": i, "id": str(i), "entity": {"skuId": str(i)}} for i in range(2)]}
        self.actor = object()
        self.sealed = patch.object(builder, "_sealed", return_value=(object(), self.reader, self.binding)).start()
        self.finish = patch.object(builder, "_finish").start()
        self.build = patch.object(builder, "build_table", side_effect=lambda *a, **k: deepcopy(self.table)).start()
        self.addCleanup(patch.stopall)

    def targets(self, **kwargs):
        return builder.targets("evidence-test", {"sourceKey": "ads", "dimension": "sku", **kwargs}, self.actor)

    def test_exact_binding_original_rows_and_fixed_pagination(self):
        actual = self.targets()
        self.assertEqual(actual["rows"], self.table["rows"])
        self.assertEqual(actual["evidenceBinding"], self.binding)
        self.assertEqual(actual["pagination"], {"offset": 0, "limit": 20, "total": 2, "hasMore": False, "nextOffset": None})
        self.assertEqual(self.build.call_args.kwargs, {"offset": 0, "limit": 20})
        self.finish.assert_called_once()

    def test_strict_query_and_sources_reject_without_building(self):
        for params in ({"offset": value} for value in (True, 0, 1.0, "01", "-1", "١", "9"*4301, "250000")):
            with self.subTest(params=params), self.assertRaises(AiError): self.targets(**params)
        for params in ({"limit": "1"}, {"limit": 20}, {"extra": "x"}, {"dimension": "daily"}, {"dimension": []}, {"sourceKey": "missing"}):
            with self.subTest(params=params), self.assertRaises(AiError): self.targets(**params)
        for source in ({"domain": "sales", "query": {}}, {"domain": "netshop", "query": {"dataset": "sku"}},
                       {"domain": "netshop", "query": {"dataset": "promotion", "window": "previous"}}):
            self.reader.sources = [{"key": "ads", **source}]
            with self.assertRaises(AiError): self.targets()
        self.build.assert_not_called()

    def test_utf8_complete_prefix_and_oversize_single_row_or_header(self):
        self.table["rows"] = [{"rowIndex": i, "text": "中"*6000} for i in range(3)]
        self.table["total"] = 3
        actual = self.targets()
        self.assertEqual(len(actual["rows"]), 2)
        self.assertEqual(actual["pagination"]["nextOffset"], 2)
        self.assertLessEqual(len(canonical(actual).encode()), 38000)
        self.assertEqual(actual["rows"], self.table["rows"][:2])
        self.table["rows"][0]["text"] = "中"*13000
        with self.assertRaises(AiError) as caught: self.targets()
        self.assertEqual(caught.exception.status, 413)
        self.table["rows"] = []; self.table["sourceMetadata"] = {"text": "中"*13000}
        with self.assertRaises(AiError): self.targets()

    def test_empty_end_out_of_range_and_late_failure(self):
        self.table.update(rows=[], total=0)
        self.assertIsNone(self.targets()["pagination"]["nextOffset"])
        with self.assertRaises(AiError): self.targets(offset="1")
        self.finish.side_effect = AiError("permission revoked", "access_denied", 403)
        with self.assertRaises(AiError): self.targets()

    def test_preview_exact_binding_result_and_limits(self):
        plan, _ = fixture()
        calculated = {**{k: self.binding[k] for k in ("evidenceRunId", "evidenceVersion", "evidencePlanDigest")}, "plan": plan}
        body = {"evidenceBinding": self.binding, "budgetPlan": plan}
        with patch.object(builder.business_budget, "resolve", return_value=calculated) as resolve:
            actual = builder.preview("evidence-test", body, self.actor)
            self.assertTrue(actual["previewOnly"])
            self.assertEqual(actual["evidenceBinding"], self.binding)
            self.assertEqual(actual["budget"], calculated)
            self.assertEqual(resolve.call_count, 1)
            for key in self.binding:
                bad = deepcopy(body); bad["evidenceBinding"][key] = 4 if key == "evidenceVersion" else "0"*64
                with self.subTest(key=key), self.assertRaises(AiError): builder.preview("evidence-test", bad, self.actor)
            bad = deepcopy(body); bad["evidenceBinding"]["evidenceVersion"] = True
            with self.assertRaises(AiError): builder.preview("evidence-test", bad, self.actor)
            self.assertEqual(resolve.call_count, 1)
            calculated["evidenceVersion"] = 4
            with self.assertRaises(AiError): builder.preview("evidence-test", body, self.actor)
            calculated["evidenceVersion"] = 3; calculated["wide"] = "中"*(700000)
            with self.assertRaises(AiError) as caught: builder.preview("evidence-test", body, self.actor)
            self.assertEqual(caught.exception.status, 413)


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessBudgetBuilderTests(TestCase):
    user = fixtures.AiDomainTests.user
    call = fixtures.AiDomainTests.call

    def setUp(self):
        fixtures.AiDomainTests.setUp(self)
        self.admin = self.user("budget-builder@example.invalid", "admin", None)
        self.report, self.prepared = store_fixtures.seed_fixed_report(self.admin)
        self.run_id = self.prepared.binding["evidenceRunId"]

    def targets(self, actor=None):
        return builder.targets(self.run_id, {"sourceKey": "ads", "dimension": "sku"}, actor or self.admin)

    def test_real_targets_preview_equal_existing_arithmetic_and_are_read_only(self):
        with patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            chosen = self.targets()
            result = builder.preview(self.run_id, {"evidenceBinding": chosen["evidenceBinding"], "budgetPlan": self.prepared.plan}, self.admin)
        self.assertEqual(chosen["rows"], business_evidence.analysis_table(self.run_id, {"sourceKey": "ads", "dimension": "sku"}, self.admin)["rows"])
        self.assertEqual(result["budget"], self.prepared.result)
        self.assertLessEqual(len(canonical(chosen).encode()), 38000)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        model.assert_not_called(); remote.assert_not_called()

    def test_owner_role_stale_binding_and_changed_row_are_rejected(self):
        chosen = self.targets()
        other = self.user("budget-builder-other@example.invalid", "admin", None)
        for actor in (other, self.viewer):
            with self.assertRaises(AiError): self.targets(actor)
            with self.assertRaises(AiError): builder.preview(self.run_id, {"evidenceBinding": chosen["evidenceBinding"], "budgetPlan": self.prepared.plan}, actor)
        plan = self.prepared.plan; plan["targets"][0]["rowId"] = "0"*64
        with self.assertRaises(AiError): builder.preview(self.run_id, {"evidenceBinding": chosen["evidenceBinding"], "budgetPlan": plan}, self.admin)
        bad = deepcopy(chosen["evidenceBinding"]); bad["sealedDigest"] = "0"*64
        with patch.object(builder.business_budget, "resolve") as resolve, self.assertRaises(AiError):
            builder.preview(self.run_id, {"evidenceBinding": bad, "budgetPlan": self.prepared.plan}, self.admin)
        resolve.assert_not_called()

    def test_legacy_unsealed_and_changed_pages_fail_closed(self):
        row = business_evidence.get_run(self.run_id, self.admin)
        with patch.object(builder.business_evidence, "get_run", return_value=row):
            for key, value in (("status", "collecting"), ("plan_json", canonical({"schemaVersion": "business-evidence-v1"}))):
                old = getattr(row, key); setattr(row, key, value)
                with self.assertRaises(AiError): self.targets()
                setattr(row, key, old)
        with patch("ai_assistant.business_sealed.Reader.pages", side_effect=AiError("corrupt page", "conflict", 409)):
            with self.assertRaises(AiError): self.targets()

    def route_get(self, suffix, query="", *, actor=None, role="ai_reader"):
        actor = actor or self.admin
        url = f"/api/ai/business-evidence/{self.run_id}/{suffix}" + query
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}), override_settings(
                DJANGO_INTERNAL_SECRET=TEST_SECRET, DJANGO_PROCESS_ROLE=role), patch("ai_assistant.views.authority"):
            return self.client.get(url, headers=signed_headers(url, email=actor.email,
                role=actor.role, scope=actor.scope))

    def route_post(self, suffix, payload, *, actor=None, role="ai_reader"):
        # AiDomainTests.call installs the same fixture secret for signing and
        # verification. GET's helper separately binds the full query string.
        with override_settings(DJANGO_PROCESS_ROLE=role), patch("ai_assistant.views.authority"):
            return self.call(f"/api/ai/business-evidence/{self.run_id}/{suffix}", payload, actor or self.admin)

    def test_reader_routes_return_bound_results_without_writes_or_dispatch(self):
        query = "?sourceKey=ads&dimension=sku&offset=0&limit=20"
        with patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            chosen = self.route_get("budget-targets", query)
            self.assertEqual(chosen.status_code, 200, chosen.content)
            self.assertEqual(chosen["Cache-Control"], "no-store")
            self.assertLessEqual(len(chosen.content), 38000)
            value = chosen.json()
            self.assertEqual(value["schemaVersion"], "business-budget-targets-v1")
            preview = self.route_post("budget-preview", {"evidenceBinding": value["evidenceBinding"], "budgetPlan": self.prepared.plan})
            self.assertEqual(preview.status_code, 200, preview.content)
            self.assertEqual(preview["Cache-Control"], "no-store")
            self.assertTrue(preview.json()["previewOnly"])
            self.assertEqual(preview.json()["evidenceBinding"], value["evidenceBinding"])
            self.assertEqual(preview.json()["budget"], self.prepared.result)
        model.assert_not_called(); remote.assert_not_called()
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertFalse(any(any(table in q["sql"].lower() for table in ("netshop_rows", "sales_lines", "market_ranking_entries")) for q in queries))

    def test_routes_reject_writer_wrong_methods_and_ambiguous_queries(self):
        binding = self.targets()["evidenceBinding"]
        body = {"evidenceBinding": binding, "budgetPlan": self.prepared.plan}
        query = "?sourceKey=ads&dimension=sku"
        self.assertEqual(self.route_get("budget-targets", query, role="ai_writer").status_code, 403)
        self.assertEqual(self.route_post("budget-preview", body, role="ai_writer").status_code, 403)
        self.assertEqual(self.route_post("budget-targets", {}).status_code, 405)
        self.assertEqual(self.route_get("budget-preview").status_code, 405)
        for suffix in ("&unknown=1", "&sourceKey=ads", "&offset=0&offset=1", "&offset=01", "&limit=10", "&offset=250000"):
            response = self.route_get("budget-targets", query+suffix)
            self.assertEqual(response.status_code, 400, response.content)
        response = self.route_post("budget-preview", {**body, "unknown": 1})
        self.assertEqual(response.status_code, 400, response.content)

    def test_routes_reject_cross_owner_role_and_changed_scope(self):
        from access_control.models import AppUser
        binding = self.targets()["evidenceBinding"]
        body = {"evidenceBinding": binding, "budgetPlan": self.prepared.plan}
        query = "?sourceKey=ads&dimension=sku"
        other = self.user("budget-builder-route-other@example.invalid", "admin", None)
        for actor in (other, self.viewer):
            self.assertIn(self.route_get("budget-targets", query, actor=actor).status_code, (403, 404))
            self.assertIn(self.route_post("budget-preview", body, actor=actor).status_code, (403, 404))
        scope = {"warehouses": [], "channels": [], "platforms": ["京东"]}
        AppUser.objects.filter(email=self.admin.email).update(scope=scope)
        # Test both a stale signed identity and a freshly signed narrowed scope.
        for actor in (self.admin, replace(self.admin, scope=scope)):
            self.assertIn(self.route_get("budget-targets", query, actor=actor).status_code, (403, 404))
            self.assertIn(self.route_post("budget-preview", body, actor=actor).status_code, (403, 404))
