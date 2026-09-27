import json
from copy import deepcopy
from unittest.mock import patch
from django.apps import apps
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from sales.auth import Principal
from ai_assistant.policy import AiError
from ai_assistant import datasets
from ai_assistant.test_datasets import catalog_fixture
from ai_assistant.models import AiConversations, AiConversationMessages
from erp_reference.models import ErpProductMaster
from .catalog import SPECS, MANIFEST, DOMAINS, describe
from .reader import query, _redact, _value

ADMIN = Principal("local-admin@teruisi.local", "Admin", "admin", None)
SECRET = "isolated-all-datasets-secret-abcdefghijklmnopqrstuvwxyz"


class CoverageTests(SimpleTestCase):
    def test_numeric_filters_reject_nonfinite_or_lossy_coercion(self):
        self.assertEqual(_value(1.25, {"type": "FloatField"}), 1.25)
        self.assertEqual(str(_value("123.4500", {"type": "DecimalField"})), "123.4500")
        for value in [True, "NaN", "Infinity", "1e9999", "not-a-number"]:
            with self.assertRaises(AiError):
                _value(value, {"type": "DecimalField"})

    def test_every_domain_model_and_every_field_is_accounted_for(self):
        expected = {f"{m._meta.app_label}.{m.__name__}": m for m in apps.get_models() if m._meta.app_label in DOMAINS}
        covered = {f'{s["domain"]}.{s["model"]}' for s in SPECS.values()}
        aliases = {item["model"] for item in MANIFEST["excludedModels"]}
        self.assertEqual(covered | aliases, set(expected))
        self.assertEqual(len(SPECS), 219)
        self.assertEqual(len(SPECS), len(MANIFEST["datasets"]))
        for key in covered:
            model = expected[key]
            spec = next(s for s in SPECS.values() if f'{s["domain"]}.{s["model"]}' == key)
            self.assertEqual(set(spec["fields"]) | set(spec["excludedFields"]), {f.attname for f in model._meta.concrete_fields}, key)
            self.assertEqual(set(spec["fields"]) & set(spec["excludedFields"]), set(), key)
            for name, field in spec["fields"].items():
                self.assertEqual(next(f.column for f in model._meta.concrete_fields if f.attname == name), field["column"])
            self.assertTrue(spec["keys"])
            self.assertLessEqual(len(spec["id"]), 63)

    def test_catalog_pages_all_datasets_without_truncating_discovery(self):
        catalog = catalog_fixture()
        entry = deepcopy(catalog[0]); entry.update(name="get_system_dataset_records", allowedRoles=["admin"], scopePolicy="unscoped_only")
        catalog.append(entry)
        with patch.object(datasets.transport, "catalog", return_value=catalog):
            items = []
            for page in range(1, 14):
                result = datasets.describe(ADMIN, page=page)
                items.extend(result["items"])
                if not result["hasMore"]: break
            self.assertEqual(len(items), 243)
            self.assertEqual(len({v["id"] for v in items}), 243)
            info = datasets.describe(ADMIN, "rows_sales_order_lines")
            self.assertIn("allocated_amount_cents", info["fields"])
            self.assertEqual(info["fields"]["allocated_amount_cents"]["unit"], "CNY_cent")
            for role in ["viewer", "analyst", "operator"]:
                actor = Principal("limited@example.invalid", "Limited", role, None)
                self.assertNotIn("rows_sales_order_lines", [v["id"] for v in datasets.describe(actor)["items"]])

    def test_credentials_binary_and_raw_customer_fields_excluded(self):
        for spec in SPECS.values():
            for key in spec["fields"]:
                self.assertNotRegex(key, r"password|secret|token|encrypted|api_key|aes_key|object_key")
                self.assertNotEqual(spec["fields"][key]["type"], "BinaryField")
        customer = SPECS["rows_customer_service_conversations"]
        self.assertIn("messages", customer["excludedFields"])
        self.assertIn("customer_id", customer["excludedFields"])
        self.assertEqual(_redact({"API_KEY": "never", "nested": {"token": "never", "metric": 2}}), {"API_KEY": "[redacted]", "nested": {"token": "[redacted]", "metric": 2}})


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class RecordQueryTests(TestCase):
    def setUp(self):
        self.secret = patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": SECRET})
        self.secret.start(); self.addCleanup(self.secret.stop)

    def test_new_business_fields_preserve_units_null_empty_and_permissions(self):
        from finance.models import FinanceTarget
        from workflow.models import NewProductProject
        FinanceTarget.objects.create(id="annual-fixture", period_type="year", period_key="2026",
            gross_margin_bps=3250, created_at="2026-09-16", updated_at="2026-09-16")
        targets = query("rows_finance_targets_scoped", {"columns": ["id", "gross_margin_bps"]}, ADMIN)
        self.assertEqual(targets["rows"], [{"id": "annual-fixture", "gross_margin_bps": 3250}])
        self.assertEqual(describe(SPECS["rows_finance_targets_scoped"], True)["fields"]["gross_margin_bps"]["unit"], "basis_point")
        for name, plan in [("legacy", None), ("cleared", ""), ("planned", "测试店铺规划")]:
            NewProductProject.objects.create(product_name=name, proposed_date="2026-09-16", shop_plan=plan,
                created_by=ADMIN.email, updated_by=ADMIN.email)
        projects=query("rows_workflow_new_product_projects", {"columns": ["product_name", "shop_plan"]}, ADMIN)
        self.assertEqual({row["product_name"]:row["shop_plan"] for row in projects["rows"]},
                         {"legacy":None,"cleared":"","planned":"测试店铺规划"})
        for dataset, field in [("rows_finance_targets_scoped","gross_margin_bps"),
                               ("rows_workflow_new_product_projects","shop_plan")]:
            for role in ["viewer", "analyst", "operator"]:
                with self.subTest(dataset=dataset,role=role), self.assertRaises(AiError):
                    query(dataset, {"columns":[field]}, Principal("limited@example.invalid","Limited",role,None))
            with self.assertRaises(AiError):
                query(dataset, {"columns":[field]}, Principal(ADMIN.email,"Admin","admin",{"shops":["limited"]}))
            with override_settings(DJANGO_PROCESS_ROLE="finance_writer"), self.assertRaises(AiError):
                query(dataset, {"columns":[field]}, ADMIN)

    def test_every_dataset_reads_all_declared_columns_on_migrated_schema(self):
        for spec in SPECS.values():
            columns = list(spec["fields"])
            for start in range(0, len(columns), 50):
                with self.subTest(dataset=spec["id"], offset=start):
                    result = query(spec["id"], {"columns": columns[start:start+50], "pageSize": 1}, ADMIN)
                    self.assertEqual(result["dataset"], spec["id"])

    def products(self, count=23):
        for i in range(count):
            ErpProductMaster.objects.create(product_code=f"fixture-{i:03}", product_name="测试货品", brand="Fixture", source_row_number=i+1, last_import_batch_id="fixture", created_at=timezone.now().isoformat(), updated_at=timezone.now().isoformat())

    def test_keyset_reads_complete_dataset_without_duplicates(self):
        self.products()
        args = {"columns": ["product_code", "product_name"], "pageSize": 5}
        found = []
        while True:
            result = query("rows_erp_product_master", args, ADMIN)
            found.extend(row["product_code"] for row in result["rows"])
            if not result["nextCursor"]: break
            self.assertNotIn("fixture", result["nextCursor"])
            args["cursor"] = result["nextCursor"]
        self.assertEqual(len(found), 23)
        self.assertEqual(len(set(found)), 23)
        self.assertEqual(found, sorted(found))

    def test_public_wrapper_executes_audited_record_tool_and_preserves_cursor(self):
        self.products(2)
        catalog = catalog_fixture()
        entry = deepcopy(catalog[0]); entry.update(name="get_system_dataset_records", allowedRoles=["admin"], scopePolicy="unscoped_only")
        catalog.append(entry)
        def execute(name, args, principal, **kwargs):
            return {"ok": True, "toolName": name, "data": {} if name == "get_data_freshness" else query(args["dataset"], json.loads(args["queryJson"]), principal)}
        with patch.object(datasets.transport, "catalog", return_value=catalog), patch.object(datasets.transport, "execute_tool", side_effect=execute) as source:
            result = datasets.query("rows_erp_product_master", {"query": {"pageSize": 1}}, ADMIN, "fixture-request")
        self.assertEqual([call.args[0] for call in source.call_args_list], ["get_data_freshness", "get_system_dataset_records"])
        self.assertEqual(result["requestId"], "fixture-request")
        self.assertEqual(result["data"]["returned"], 1)
        self.assertTrue(result["data"]["nextCursor"])

    def test_cursor_tamper_cross_actor_cross_filter_and_cross_dataset_rejected(self):
        self.products(2)
        result = query("rows_erp_product_master", {"pageSize": 1}, ADMIN)
        token = result["nextCursor"]
        for args, actor, dataset in [
            ({"cursor": token[:-4]+"xxxx"}, ADMIN, "rows_erp_product_master"),
            ({"cursor": token}, Principal("other@example.invalid", "Other", "admin", None), "rows_erp_product_master"),
            ({"cursor": token, "filters": [{"field": "brand", "op": "eq", "value": "Other"}]}, ADMIN, "rows_erp_product_master"),
            ({"cursor": token}, ADMIN, "rows_sales_order_lines"),
        ]:
            with self.subTest(dataset=dataset), self.assertRaises(AiError): query(dataset, args, actor)

    def test_owner_scopes_and_long_content_can_be_read_in_windows(self):
        text = "甲乙丙丁" * 900
        for owner, ident in [(ADMIN.email, "own"), ("other@example.invalid", "other")]:
            AiConversations.objects.create(id=ident, title=ident, created_by=owner)
            AiConversationMessages.objects.create(id=ident, conversation_id=ident, role="user", content=text)
        result = query("rows_ai_conversation_messages", {"columns": ["id", "content"], "textLimit": 1000}, ADMIN)
        self.assertEqual(result["returned"], 1)
        self.assertEqual(result["rows"][0]["id"], "own")
        self.assertEqual(result["cellWindows"]["0.content"]["nextOffset"], 1000)
        remaining = query("rows_ai_conversation_messages", {"columns": ["content"], "textOffset": 1000, "textLimit": 8000}, ADMIN)
        self.assertEqual(result["rows"][0]["content"]+remaining["rows"][0]["content"], text)

    def test_wrong_role_domain_unknown_fields_and_unbounded_values_rejected(self):
        with override_settings(DJANGO_PROCESS_ROLE="sales_writer"), self.assertRaises(AiError):
            query("rows_sales_order_lines", {}, ADMIN)
        with override_settings(DJANGO_PROCESS_ROLE="reader"), self.assertRaises(AiError):
            query("rows_erp_product_master", {}, ADMIN)
        for args in [{"sql": "select 1"}, {"columns": ["__dict__"]}, {"columns": ["api_key_encrypted"]}, {"pageSize": 101}, {"filters": [{"field": "product_code__contains", "op": "eq", "value": "x"}]}, {"filters": [{"field": "product_code", "op": "in", "value": ["x"]*51}]}]:
            with self.assertRaises(AiError): query("rows_erp_product_master", args, ADMIN)
