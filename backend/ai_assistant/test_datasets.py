from copy import deepcopy
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from access_control.models import AccessRole, AppUser
from sales.auth import Principal
from . import datasets, models as m, views
from .control_models import AiDataRevision, AiWriteAuthority
from .policy import AiError, digest
from . import tests as support

CATALOG, ADMIN = support.CATALOG, support.ADMIN


class DatasetSourceErrorTests(SimpleTestCase):
    def test_source_errors_preserve_http_contract(self):
        for code, status in [("invalid_arguments", 400), ("forbidden", 403),
                             ("payload_too_large", 413), ("tool_result_too_large", 413),
                             ("service_unavailable", 503), ("unknown", 503)]:
            with self.subTest(code=code), self.assertRaises(AiError) as caught:
                datasets._result({"ok": False, "toolName": "get_system_dataset_records",
                                  "error": {"code": code}}, "get_system_dataset_records")
            self.assertEqual(caught.exception.status, status)


def catalog_fixture():
    entries = []
    for name in {v[0] for v in datasets.DATASETS.values()} | {"get_data_freshness"}:
        entry = deepcopy(CATALOG[0])
        entry.update(name=name, title=name, allowedRoles=["viewer", "analyst", "operator", "admin"])
        entry["inputSchema"]["properties"] = {"limit": {"type": "integer", "maximum": 20}}
        if name.endswith("page_data"):
            entry["inputSchema"]["properties"]["view"] = {"type": "string"}
            entry["inputSchema"]["required"] = ["view"]
        if name == "get_netshop_performance":
            entry["inputSchema"]["properties"]["dataset"] = {"type": "string"}
        if name in {"get_inventory_page_data", "get_finance_page_data"}:
            entry["scopePolicy"] = "unscoped_only"
        entries.append(entry)
    return sorted(entries, key=lambda v: v["name"])


def source_result(name, args, principal, **kwargs):
    return {"ok": True, "toolName": name, "data": (
        {"sales": {"through": "2026-09-07"}, "inventory": {"asOf": "2026-09-08"}}
        if name == "get_data_freshness" else
        {"items": [{"sku": "测试规格", "amountCents": -100}], "total": 500,
         "returned": 1, "truncated": True, "dataCutoffDate": "2026-09-06"})}


class DatasetContractTests(SimpleTestCase):
    def setUp(self):
        self.catalog = catalog_fixture()
        self.catalog_patch = patch.object(datasets.transport, "catalog", return_value=self.catalog)
        self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)
        self.edge_patch = patch.object(datasets.transport, "execute_tool", side_effect=source_result)
        self.execute = self.edge_patch.start()
        self.addCleanup(self.edge_patch.stop)

    def test_explicit_catalog_schema_and_fixed_selectors(self):
        result = datasets.describe(ADMIN)
        self.assertEqual(result["count"], 24)
        self.assertNotIn("query_system_dataset", str(result))
        item = datasets.describe(ADMIN, "inventory_age")
        self.assertNotIn("view", item["querySchema"]["properties"])
        self.assertEqual(item["querySchema"]["required"], [])
        self.assertEqual(item["fixedFilters"], {"view": "age"})
        self.execute.assert_not_called()

    def test_role_scope_and_disabled_capabilities_filtered(self):
        scoped = Principal("scoped@example.invalid", "Scoped", "viewer",
                           {"warehouses": [], "channels": ["京东-测试店"], "platforms": ["京东"]})
        names = {item["id"] for item in datasets.describe(scoped)["items"]}
        self.assertNotIn("finance_analysis", names)
        self.assertNotIn("inventory_age", names)
        self.assertNotIn("workflow_tasks", names)
        self.assertIn("workflow_operations", names)
        self.catalog[0]["allowedRoles"] = ["admin"]
        self.assertNotIn(self.catalog[0]["name"], {datasets.DATASETS[key][0] for key in
            {item["id"] for item in datasets.describe(scoped)["items"]}})

    def test_freshness_precedes_query_identity_policy_and_native_totals_preserved(self):
        result = datasets.query("inventory_age", {"query": {"limit": 1}}, ADMIN, "request-1")
        calls = self.execute.call_args_list
        self.assertEqual([v.args[0] for v in calls], ["get_data_freshness", "get_inventory_page_data"])
        self.assertEqual(calls[1].args[1], {"limit": 1, "view": "age"})
        self.assertEqual(calls[1].args[2], ADMIN)
        self.assertEqual(calls[1].kwargs["policy_digest"], digest(self.catalog))
        self.assertEqual(calls[1].kwargs["request_id"], "request-1")
        self.assertEqual(result["data"]["total"], 500)
        self.assertTrue(result["data"]["truncated"])
        self.assertEqual(result["data"]["items"][0]["amountCents"], -100)
        self.assertEqual(result["dataCutoffDate"], "2026-09-06")

    def test_unknown_recursive_and_forbidden_inputs_never_execute(self):
        cases = [("query_system_dataset", {}), ("missing", {}),
                 ("inventory_age", {"query": {"view": "guangdong"}}),
                 ("sales_summary", {"query": {"sql": "select * from sales"}}),
                 ("sales_summary", {"query": {"role": "admin"}}),
                 ("sales_summary", {"query": []}),
                 ("sales_summary", {"query": {"limit": "中" * 8000}}),
                 ("sales_summary", {"query": {"__proto__": {}}})]
        for key, body in cases:
            with self.subTest(key=key, body=str(body)[:90]), self.assertRaises(AiError):
                datasets.query(key, body, ADMIN, "bad")
        self.execute.assert_not_called()

    def test_audit_failure_freshness_failure_and_policy_race_fail_closed(self):
        for result in [
            {"ok": False, "toolName": "get_data_freshness", "auditStatus": "unavailable"},
            {"ok": False, "toolName": "get_data_freshness", "error": {"code": "forbidden"}},
            {"ok": True, "toolName": "wrong-tool", "data": {}},
        ]:
            self.execute.side_effect = None
            self.execute.return_value = result
            self.execute.reset_mock()
            with self.assertRaises(AiError):
                datasets.query("sales_summary", {}, ADMIN, "failed")
            self.assertEqual(self.execute.call_count, 1)
        self.execute.side_effect = AiError("policy changed", "access_denied", 403)
        with self.assertRaises(AiError):
            datasets.query("sales_summary", {}, ADMIN, "race")

    def test_unknown_cutoff_never_borrows_sales_date(self):
        self.execute.side_effect = lambda name, *args, **kwargs: {
            "ok": True, "toolName": name, "data": {"sales": {"through": "2026-09-07"}}}
        self.assertIsNone(datasets.query("market_overview", {}, ADMIN, "cutoff")["dataCutoffDate"])

    def test_oversized_result_is_rejected_not_truncated(self):
        self.execute.side_effect = lambda name, *args, **kwargs: {
            "ok": True, "toolName": name, "data": {"value": "中" * 40000}}
        with self.assertRaises(AiError) as error:
            datasets.query("sales_summary", {}, ADMIN, "large")
        self.assertEqual(error.exception.status, 413)

    def test_tool_json_only_and_repeated_queries_use_fresh_catalog(self):
        for raw in ["[]", "null", "select * from x", '{"limit":NaN}', '{"limit":Infinity}']:
            with self.subTest(raw=raw), self.assertRaises(AiError):
                datasets.consumer({"operation": "datasets-query", "dataset": "sales_summary", "queryJson": raw}, ADMIN, "bad")
        for request_id in ["repeat", "repeat"]:
            result = datasets.consumer({"operation": "datasets-query", "dataset": "sales_summary", "queryJson": "{}"}, ADMIN, request_id)
            self.assertEqual(result["requestId"], request_id)
        self.assertEqual(self.execute.call_count, 4)


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class DatasetEndpointTests(TestCase):
    call = support.AiDomainTests.call

    def setUp(self):
        AiDataRevision.objects.get_or_create(domain="ai-assistant")
        AiWriteAuthority.objects.update_or_create(id=1, defaults={
            "status": "postgres", "authority_epoch": "ab3213bd-2e10-4da1-b7a9-b0127eb76aaf", "cutover_id": "dataset-fixture",
            "migration_verify_run_id": "dataset-fixture-verify", "activated_at": timezone.now()})
        self.owner = ADMIN

    @patch.object(datasets.transport, "catalog", side_effect=lambda *_: catalog_fixture())
    @patch.object(datasets.transport, "execute_tool", side_effect=source_result)
    def test_public_and_consumer_endpoints_reader_only_without_fact_writes(self, execute, catalog):
        with override_settings(DJANGO_PROCESS_ROLE="ai_reader", AI_WRITE_AUTHORITY_EPOCH="ab3213bd-2e10-4da1-b7a9-b0127eb76aaf", AI_WRITE_CUTOVER_ID="dataset-fixture"):
            response = self.call("/api/ai/datasets", method="GET")
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["count"], 24)
            self.assertEqual(response["Cache-Control"], "no-store")
            response = self.call("/api/ai/datasets/inventory_age", method="GET")
            self.assertEqual(response.status_code, 200, response.content)
            response = self.call("/api/ai/datasets/inventory_age/query", {"query": {"limit": 1}})
            self.assertEqual(response.status_code, 200, response.content)
            response = self.call("/api/ai/consumer", {"operation": "datasets-query", "dataset": "inventory_age", "queryJson": "{}"})
            self.assertEqual(response.status_code, 200, response.content)
        with override_settings(DJANGO_PROCESS_ROLE="ai_writer"):
            response = self.call("/api/ai/datasets/sales_summary/query", {})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(m.AiAnalysisRuns.objects.count(), 0)

    @patch.object(datasets.transport, "catalog", side_effect=lambda *_: catalog_fixture())
    def test_unsigned_unknown_extra_and_method_rejected(self, catalog):
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": "A-valid-isolated-signing-secret-13579-abcdefghijklmnopqrstuvwxyz"}):
            self.assertIn(self.client.get("/api/ai/datasets").status_code, [401, 403])
        self.assertEqual(self.call("/api/ai/datasets/unknown", method="GET").status_code, 404)
        self.assertEqual(self.call("/api/ai/datasets", {}).status_code, 405)
        self.assertEqual(self.call("/api/ai/datasets/sales_summary/query", {"role": "admin"}).status_code, 400)

    def test_dataset_concurrency_budget_fails_closed_and_recovers(self):
        self.assertTrue(views._dataset_slots.acquire(blocking=False))
        self.assertTrue(views._dataset_slots.acquire(blocking=False))
        try:
            self.assertEqual(self.call("/api/ai/datasets", method="GET").status_code, 429)
            self.assertEqual(self.call("/api/ai/consumer", {"operation": "datasets-query", "dataset": "sales_summary", "queryJson": "{}"}).status_code, 429)
        finally:
            views._dataset_slots.release()
            views._dataset_slots.release()
        with patch.object(datasets.transport, "catalog", return_value=catalog_fixture()):
            self.assertEqual(self.call("/api/ai/datasets", method="GET").status_code, 200)

    @patch.object(datasets.transport, "catalog", side_effect=lambda *_: catalog_fixture())
    @patch.object(datasets.transport, "execute_tool", side_effect=source_result)
    def test_viewer_query_scope_and_account_revocation(self, execute, catalog):
        AccessRole.objects.get_or_create(code="viewer", defaults={"label": "viewer", "description": "fixture", "rank": 0, "permissions": []})
        viewer = Principal("viewer@example.invalid", "Viewer", "viewer", {
            "warehouses": [], "channels": ["京东-测试店"], "platforms": ["京东"]})
        AppUser.objects.create(email=viewer.email, display_name="Viewer", role_id="viewer", scope=viewer.scope,
                               created_at=timezone.now(), updated_at=timezone.now())
        response = self.call("/api/ai/datasets/sales_summary/query", {}, principal=viewer)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(execute.call_args.args[2], viewer)
        self.assertEqual(self.call("/api/ai/datasets/finance_analysis/query", {}, principal=viewer).status_code, 404)
        AppUser.objects.filter(email=viewer.email).update(status="disabled")
        before = execute.call_count
        self.assertEqual(self.call("/api/ai/datasets/sales_summary/query", {}, principal=viewer).status_code, 403)
        self.assertEqual(execute.call_count, before)
