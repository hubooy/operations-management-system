"""Exercise the real chat/provider loop with isolated HTTP and source fixtures."""
import json
from copy import deepcopy
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from erp_reference.models import ErpProductMaster
from system_datasets.reader import query as record_query
from . import chat, datasets, provider, artifacts, models as m
from .policy import AiError, canonical
from .test_datasets import catalog_fixture
from . import tests as support


def catalog():
    result = catalog_fixture()
    for name in ["describe_system_datasets", "query_system_dataset", "get_system_dataset_records"]:
        entry = deepcopy(result[0])
        entry.update(name=name, title=name)
        entry["inputSchema"] = {"type": "object", "properties": {
            "dataset": {"type": "string"}, "queryJson": {"type": "string", "maxLength": 16000}
        }, "additionalProperties": False}
        entry["execution"]["maxCallsPerRequest"] = 4
        result.append(entry)
    return result


def wire(protocol, name=None, args=None, answer=""):
    if protocol == "anthropic":
        return {"id": "fixture-provider", "content": ([{"type": "tool_use", "id": "fixture-call", "name": name, "input": args}] if name else [{"type": "text", "text": answer}])}
    return {"id": "fixture-provider", "choices": [{"message": {
        "role": "assistant", "content": None if name else answer,
        **({"tool_calls": [{"id": "fixture-call", "type": "function", "function": {"name": name, "arguments": canonical(args)}}]} if name else {})
    }}]}


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class DatasetChatTests(TestCase):
    user = support.AiDomainTests.user
    call = support.AiDomainTests.call

    def setUp(self):
        support.AiDomainTests.setUp(self)
        self.admin = self.user("dataset-admin@example.invalid", "admin", None)
        for i in range(2):
            ErpProductMaster.objects.create(product_code=f"fixture-{i}", product_name="隔离测试货品", brand="Fixture", source_row_number=i+1,
                last_import_batch_id="fixture", created_at=timezone.now().isoformat(), updated_at=timezone.now().isoformat())

    def test_chat_discovers_queries_and_returns_table_for_both_protocols(self):
        entries = catalog()
        def execute(name, args, principal, **kwargs):
            if name == "describe_system_datasets":
                data = datasets.describe(principal, args.get("dataset"), domain=args.get("domain"))
            elif name == "query_system_dataset":
                data = datasets.query(args["dataset"], {"query": json.loads(args["queryJson"])}, principal, kwargs["request_id"])
            elif name == "get_system_dataset_records":
                data = record_query(args["dataset"], json.loads(args["queryJson"]), principal)
            elif name == "get_data_freshness":
                data = {"sales": {"through": "2026-09-07"}}
            else:
                raise AssertionError(name)
            return {"ok": True, "toolName": name, "data": data}

        for protocol in ["openai_compatible", "anthropic"]:
            with self.subTest(protocol=protocol):
                self.model.protocol = protocol
                self.model.save(update_fields=["protocol"])
                responses = [wire(protocol, "describe_system_datasets", {"domain": "erp_reference"}),
                    wire(protocol, "describe_system_datasets", {"dataset": "rows_erp_product_master"}),
                    wire(protocol, "query_system_dataset", {"dataset": "rows_erp_product_master", "queryJson": canonical({"columns": ["product_code", "product_name"], "pageSize": 1})}),
                    wire(protocol, answer="查询来源为 ERP 货品数据集。本页 1 条，仍有后续记录；ERP 截止日期未知。")]
                with patch.object(chat.transport, "catalog", return_value=entries), patch.object(chat.transport, "execute_tool", side_effect=execute) as source, patch.object(provider, "decrypt", return_value="isolated-fixture-key"), patch.object(provider, "bounded_json", side_effect=responses) as http:
                    body = {"clientRequestId": "dataset-chat-"+protocol, "message": "查询系统 ERP 货品记录"}
                    response = self.call("/api/ai/chat", body, self.admin)
                    self.assertEqual(response.status_code, 200, response.content)
                    result = response.json()
                    self.assertEqual(http.call_count, 4)
                    last_body = http.call_args.args[1]
                    self.assertIn("fixture-0", canonical(last_body["messages"]))
                    self.assertIn("nextCursor", canonical(last_body["messages"]))
                    self.assertIn("describe_system_datasets", canonical(last_body["tools"]))
                    first_body = http.call_args_list[0].args[1]
                    system = first_body.get("system") or first_body["messages"][0]["content"]
                    self.assertIn("querySchema", system)
                    self.assertIn("不将单页求和作为总计", system)
                    self.assertEqual(result["outcome"], "answered")
                    self.assertEqual(result["artifacts"][0]["rows"], [["fixture-0", "隔离测试货品"]])
                    self.assertTrue(result["artifacts"][0]["truncated"])
                    for call in source.call_args_list:
                        self.assertEqual(call.args[2], self.admin)
                        self.assertEqual(call.kwargs["surface"], "ai_chat")
                    replay = self.call("/api/ai/chat", body, self.admin)
                    self.assertEqual(replay.status_code, 200)
                    self.assertEqual(http.call_count, 4)

    def test_dataset_audit_failure_does_not_become_an_answer_or_artifact(self):
        with patch.object(chat.transport, "catalog", return_value=catalog()), patch.object(chat.transport, "execute_tool", return_value={"ok": False, "auditStatus": "unavailable"}), patch.object(provider, "decrypt", return_value="isolated-fixture-key"), patch.object(provider, "bounded_json", return_value=wire("openai_compatible", "query_system_dataset", {"dataset": "rows_erp_product_master", "queryJson": "{}"})) as http:
            with self.assertRaises(AiError):
                chat.answer({"clientRequestId": "dataset-audit-offline", "message": "查询货品"}, self.admin, "fixture-audit")
            self.assertEqual(http.call_count, 1)
        self.assertFalse(m.AiArtifacts.objects.exists())
        self.assertFalse(m.AiConversationMessages.objects.filter(role="assistant").exists())

    def test_model_cannot_call_records_missing_from_its_authorized_catalog(self):
        entries = [entry for entry in catalog() if entry["name"] != "get_system_dataset_records"]
        with patch.object(chat.transport, "catalog", return_value=entries), patch.object(chat.transport, "execute_tool") as source, patch.object(provider, "decrypt", return_value="isolated-fixture-key"), patch.object(provider, "bounded_json", return_value=wire("openai_compatible", "get_system_dataset_records", {"dataset": "rows_erp_product_master", "queryJson": "{}"})):
            with self.assertRaises(AiError):
                chat.answer({"clientRequestId": "dataset-not-allowed", "message": "查询货品"}, self.owner, "fixture-denied")
            source.assert_not_called()
        self.assertFalse(m.AiArtifacts.objects.exists())

    def test_provider_accepts_registered_query_size_and_keeps_other_tools_bounded(self):
        for protocol in ["openai_compatible", "anthropic"]:
            self.model.protocol = protocol
            args = {"dataset": "rows_erp_product_master", "queryJson": "x"*9000}
            with patch.object(provider, "decrypt", return_value="isolated-fixture-key"), patch.object(provider, "bounded_json", return_value=wire(protocol, "query_system_dataset", args)):
                self.assertEqual(provider.turn(self.model, [], "fixture", catalog())["calls"][0]["arguments"], args)
            for name, query_json in [("unknown_tool", "x"*9000), ("query_system_dataset", "x"*34000)]:
                with patch.object(provider, "decrypt", return_value="isolated-fixture-key"), patch.object(provider, "bounded_json", return_value=wire(protocol, name, {"queryJson": query_json})), self.assertRaises(AiError):
                    provider.turn(self.model, [], "fixture", catalog())

    def test_record_tables_preserve_content_exclusions_and_paging_marker(self):
        result = artifacts.candidate("query_system_dataset", {"data": {"rows": [{"product_code": "fixture", "content": "private", "api_key": "never"}], "hasMore": True, "total": None}})
        self.assertEqual(result["columns"], ["product_code"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["rowCount"], 1)
        self.assertIsNone(artifacts.candidate("query_system_dataset", {"data": "malformed"}))
