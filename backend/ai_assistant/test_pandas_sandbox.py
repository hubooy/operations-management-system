"""Synthetic inputs only. Never execute generated Python in the test host."""
from copy import deepcopy
import os
import json
from unittest import skipUnless
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase, override_settings
from sales.auth import Principal
from pandas_runner.protocol import encode, signature, validate_job, validate_result
from . import pandas_sandbox as sandbox, datasets, chat, models as m
from .policy import AiError, canonical
from .test_datasets import catalog_fixture
from . import tests as support
from .test_dataset_chat import catalog as chat_catalog, wire
from . import provider

ADMIN = Principal("local-admin@teruisi.local", "Fixture", "admin", None)
IMAGE = "sha256:" + "a" * 64
INPUT = {"name": "sales", "dataset": "rows_erp_product_master", "query": {"columns": ["product_code"], "pageSize": 1}}
PAYLOAD = {"operation": "pandas-analysis", "surface": "ai_chat", "inputsJson": canonical([INPUT]), "code": "result = frames['sales']"}


def page(rows=None, more=False, cursor=None, **extra):
    return {"source": {"domain": "erp_reference", "storage": "Django/PostgreSQL"},
        "dataCutoffDate": None, "queriedAt": "2026-09-11T00:00:00Z", "freshness": {},
        "data": {"rows": rows if rows is not None else [{"product_code": "合成货品"}],
            "hasMore": more, "nextCursor": cursor, "truncated": more, "truncatedFields": [], "cellWindows": {}, **extra}}


class PandasExportTests(SimpleTestCase):
    def test_guangdong_total_pages_contract_does_not_require_invented_truncated_flag(self):
        item = {"name": "stock", "dataset": "inventory_guangdong", "query": {"limit": 1}}
        def response(number):
            return {**page(), "data": {"items": [{"productCode": f"货品{number}"}], "pagination": {
                "page": number, "pageSize": 1, "total": 2, "totalPages": 2}}}
        with patch.object(datasets, "query", side_effect=[response(1), response(2)]):
            frames, _ = sandbox.export_frames([item], ADMIN, "gd", "ai_chat")
        self.assertEqual(len(frames["stock"]), 2)
        broken = response(1)
        broken["data"]["pagination"]["totalPages"] = 1
        with patch.object(datasets, "query", return_value=broken), self.assertRaises(AiError):
            sandbox.export_frames([item], ADMIN, "gd", "ai_chat")

    def test_native_inventory_pages_export_complete_scalar_columns(self):
        item = {"name": "stock", "dataset": "inventory_age", "query": {"limit": 1},
                "columns": ["productCode", "availableQuantity"]}
        def response(number, more):
            return {**page(), "data": {"items": [{"productCode": f"货品{number}", "availableQuantity": number,
                "details": {"ignored": True}}], "pagination": {"page": number, "pageSize": 1,
                "total": 2, "returned": 1, "truncated": more}}}
        with patch.object(datasets, "query", side_effect=[response(1, True), response(2, False)]) as source:
            frames, sources = sandbox.export_frames([item], ADMIN, "native", "dingtalk_chat")
        self.assertEqual(frames["stock"], [{"productCode": "货品1", "availableQuantity": 1},
                                          {"productCode": "货品2", "availableQuantity": 2}])
        self.assertEqual(source.call_args.args[1], {"query": {"limit": 1, "page": 2}})
        self.assertEqual(source.call_args.kwargs["surface"], "dingtalk_chat")
        self.assertEqual(sources[0]["pages"], 2)
        self.assertEqual(sources[0]["columns"], item["columns"])
        validate_job({"frames": frames, "code": "result = frames['stock']"})

    def test_native_export_rejects_last_page_bad_counts_and_incomplete_rows(self):
        item = {"name": "stock", "dataset": "inventory_age", "query": {"limit": 1}}
        valid = {"items": [{"x": 1}], "pagination": {"page": 1, "pageSize": 1, "total": 1, "truncated": False}}
        cases = [
            {**valid, "pagination": {**valid["pagination"], "page": 2}},
            {**valid, "pagination": {**valid["pagination"], "total": 2}},
            {**valid, "pagination": {**valid["pagination"], "truncated": "false"}},
            {**valid, "pagination": {**valid["pagination"], "returned": 0}},
            {**valid, "items": [{"x": 1, "truncatedFields": ["x"]}]},
        ]
        for data in cases:
            with self.subTest(data=data), patch.object(datasets, "query", return_value={**page(), "data": data}), self.assertRaises(AiError):
                sandbox.export_frames([item], ADMIN, "native", "ai_chat")
        with patch.object(datasets, "query") as source, self.assertRaises(AiError):
            sandbox.export_frames([{**item, "query": {"page": 2}}], ADMIN, "native", "ai_chat")
        source.assert_not_called()

    def test_projection_does_not_invent_fields_or_accept_duplicate_columns(self):
        for columns in [["missing"], ["product_code", "product_code"], [], "product_code"]:
            with self.subTest(columns=columns), patch.object(datasets, "query", return_value=page()), self.assertRaises(AiError):
                sandbox.export_frames([{**INPUT, "columns": columns}], ADMIN, "native", "ai_chat")

    def test_native_page_total_changes_reject_whole_export(self):
        item = {"name": "stock", "dataset": "inventory_age", "query": {"limit": 1}}
        def response(number, total):
            return {**page(), "data": {"items": [{"x": number}], "pagination": {
                "page": number, "pageSize": 1, "total": total, "truncated": number < total}}}
        with patch.object(datasets, "query", side_effect=[response(1, 2), response(2, 3)]), self.assertRaises(AiError):
            sandbox.export_frames([item], ADMIN, "native", "ai_chat")

    def test_broker_reply_signature_image_cleanup_and_shape_are_verified(self):
        from unittest.mock import Mock
        key = b"k" * 32
        valid = {"result": {"columns": ["amount"], "rows": [{"amount": 800}]}, "image": IMAGE, "cleanupVerified": True}
        for payload, bad_signature, rejected in [(valid, False, False), (valid, True, True),
                ({**valid, "cleanupVerified": False}, False, True), ({**valid, "image": "sha256:" + "b" * 64}, False, True),
                ({**valid, "result": {"rows": [], "columns": [], "forged": True}}, False, True)]:
            connection, response = Mock(), Mock()
            connection.sock = None
            response.status = 200
            raw = encode(payload)
            response.read.return_value = raw
            connection.getresponse.return_value = response
            def header(name):
                if name == "Content-Type":
                    return "application/json"
                headers = connection.request.call_args.kwargs["headers"]
                return "invalid" if bad_signature else signature(key, headers["X-Timestamp"], headers["X-Nonce"], raw, "response")
            response.getheader.side_effect = lambda name, default=None: header(name)
            with patch.object(sandbox.http.client, "HTTPConnection", return_value=connection) as http:
                if rejected:
                    with self.assertRaises(AiError):
                        sandbox.call_runner({"code": "result = frames['sales']", "frames": {"sales": []}}, (key, IMAGE))
                else:
                    result = sandbox.call_runner({"code": "result = frames['sales']", "frames": {"sales": []}}, (key, IMAGE))
                    self.assertEqual(result["rows"], [{"amount": 800}])
                self.assertEqual(http.call_args.args, ("127.0.0.1", 8121))
                connection.close.assert_called_once()

    def test_continuous_pages_keep_scope_query_and_disclose_non_atomic_source(self):
        with patch.object(datasets, "query", side_effect=[page(more=True, cursor="p2"), page([{"product_code": "合成货品2"}])]) as source:
            frames, sources = sandbox.export_frames([INPUT], ADMIN, "fixture", "ai_chat")
        self.assertEqual(len(frames["sales"]), 2)
        self.assertEqual(source.call_args.args[1]["query"], {**INPUT["query"], "cursor": "p2"})
        self.assertEqual(source.call_args.args[2], ADMIN)
        self.assertTrue(sources[0]["complete"])
        self.assertEqual(sources[0]["consistency"], "live_per_page")
        self.assertIsNone(sources[0]["dataCutoffDate"])

    def test_partial_text_cursor_cycles_and_inconsistent_pages_fail(self):
        cases = [page(truncatedFields=["0.product_code"]), page(cellWindows={"0.x": {"nextOffset": 2000}}),
                 page(more=True), page(more=True, cursor="p2", rows=[]), page(cursor="orphan"),
                 page(truncated=True), page(hasMore="false")]
        for value in cases:
            with self.subTest(value=value), patch.object(datasets, "query", return_value=value), self.assertRaises(AiError):
                sandbox.export_frames([INPUT], ADMIN, "fixture", "ai_chat")
        with patch.object(datasets, "query", return_value=page(more=True, cursor="cycle")), self.assertRaises(AiError):
            sandbox.export_frames([INPUT], ADMIN, "fixture", "ai_chat")

    def test_bounds_duplicate_aliases_and_no_model_provided_files_or_credentials(self):
        for items in [[{**INPUT, "path": "/etc/passwd"}], [{**INPUT, "query": {"cursor": "late-page"}}],
                      [{**INPUT, "query": {"textOffset": 1}}], [{**INPUT, "name": "../../host"}],
                      [{**INPUT, "collection": "rows[0]"}], [INPUT] * 4]:
            with self.subTest(items=items), self.assertRaises(AiError):
                sandbox.export_frames(items, ADMIN, "fixture", "ai_chat")
        with patch.object(datasets, "query", return_value=page()), self.assertRaises(AiError):
            sandbox.export_frames([INPUT, INPUT], ADMIN, "fixture", "ai_chat")
        with patch.object(datasets, "query", return_value=page([{"x": 1}] * 2001)), self.assertRaises(AiError):
            sandbox.export_frames([INPUT], ADMIN, "fixture", "ai_chat")

    def test_native_collection_requires_complete_evidence(self):
        item = {"name": "native", "dataset": "sales_category", "query": {}, "collection": "trend.items"}
        for data in [{"trend": {"items": [{"x": 1}]}}, {"truncated": True, "trend": {"items": [], "truncated": False}},
                     {"trend": {"items": [{"x": 1}], "truncated": False, "total": 4}}]:
            with patch.object(datasets, "query", return_value={**page(), "data": data}), self.assertRaises(AiError):
                sandbox.export_frames([item], ADMIN, "fixture", "ai_chat")
        with patch.object(datasets, "query", return_value={**page(), "data": {"trend": {"items": [{"x": 1}], "truncated": False, "total": 1}}}):
            frames, _ = sandbox.export_frames([item], ADMIN, "fixture", "ai_chat")
            self.assertEqual(frames["native"], [{"x": 1}])

    def test_unavailable_or_denied_never_exports_or_calls_container(self):
        with patch.object(sandbox, "config", side_effect=sandbox.unavailable()), patch.object(datasets, "query") as source, self.assertRaises(AiError):
            sandbox.run(PAYLOAD, ADMIN, "fixture")
        source.assert_not_called()
        viewer = Principal(ADMIN.email, "Fixture", "viewer", None)
        with patch.object(sandbox, "current_principal", return_value=viewer), patch.object(sandbox, "config") as config, self.assertRaises(AiError):
            sandbox.run(PAYLOAD, viewer, "fixture")
        config.assert_not_called()

    def test_permissions_change_before_and_after_container_suppresses_output(self):
        narrower = Principal(ADMIN.email, "Fixture", "admin", {"warehouses": ["A"], "channels": [], "platforms": []})
        for actors, runs in [([ADMIN, narrower], 0), ([ADMIN, ADMIN, narrower], 1)]:
            with patch.object(sandbox, "current_principal", side_effect=actors), patch.object(sandbox, "config", return_value=(b"a"*32, IMAGE)), \
                    patch.object(sandbox, "export_frames", return_value=({"sales": [{"x": 1}]}, [])), \
                    patch.object(sandbox, "call_runner", return_value={"columns": ["x"], "rows": [{"x": 1}]}) as runner, self.assertRaises(AiError):
                sandbox.run(PAYLOAD, ADMIN, "fixture")
            self.assertEqual(runner.call_count, runs)

    def test_single_slot_and_result_metadata(self):
        sandbox._slots.acquire()
        try:
            with patch.object(sandbox, "config", return_value=(b"a"*32, IMAGE)), self.assertRaises(AiError) as error:
                sandbox.run(PAYLOAD, ADMIN, "fixture")
            self.assertEqual(error.exception.status, 429)
        finally:
            sandbox._slots.release()
        with patch.object(sandbox, "config", return_value=(b"a"*32, IMAGE)), patch.object(datasets, "query", return_value=page()), \
                patch.object(sandbox, "call_runner", return_value={"columns": ["x"], "rows": [{"x": -100}]}):
            result = sandbox.run(PAYLOAD, ADMIN, "fixture")
        self.assertEqual(result["items"], [{"x": -100}])
        self.assertTrue(result["cleanupVerified"])
        self.assertNotIn("code", result)
        self.assertFalse(result["truncated"])

    def test_existing_dataset_permission_and_field_filters_not_bypassed(self):
        entries = catalog_fixture()
        with patch.object(datasets.transport, "catalog", return_value=entries), patch.object(datasets.transport, "execute_tool") as tool, self.assertRaises(AiError):
            sandbox.export_frames([INPUT], Principal("analyst@example.invalid", "Analyst", "analyst", None), "fixture", "ai_chat")
        tool.assert_not_called()


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PandasDispatchTests(TestCase):
    user = support.AiDomainTests.user
    call = support.AiDomainTests.call

    def setUp(self):
        support.AiDomainTests.setUp(self)

    def test_signed_consumer_receipt_replays_without_second_export_and_is_owner_bound(self):
        with patch.object(sandbox, "config", return_value=(b"a"*32, IMAGE)), \
                patch.object(sandbox, "export_frames", return_value=({"sales": [{"x": 1}]}, [])) as export, \
                patch.object(sandbox, "call_runner", return_value={"columns": ["x"], "rows": [{"x": 1}]}) as runner:
            first = self.call("/api/ai/consumer", PAYLOAD, self.owner, request_id="pandas-receipt")
            second = self.call("/api/ai/consumer", PAYLOAD, self.owner, request_id="pandas-receipt")
            crossed = self.call("/api/ai/consumer", PAYLOAD, self.other, request_id="pandas-receipt")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(first.json(), second.json())
        self.assertGreaterEqual(crossed.status_code, 400)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(export.call_count, 1)

    def test_failed_dispatch_is_not_replayed_and_audit_contains_only_digest(self):
        with patch.object(sandbox, "config", return_value=(b"a"*32, IMAGE)), \
                patch.object(sandbox, "export_frames", return_value=({"sales": []}, [])), \
                patch.object(sandbox, "call_runner", side_effect=sandbox.unavailable()) as runner:
            first = self.call("/api/ai/consumer", PAYLOAD, self.owner, request_id="pandas-failed")
            second = self.call("/api/ai/consumer", PAYLOAD, self.owner, request_id="pandas-failed")
        self.assertEqual(first.status_code, 503)
        self.assertGreaterEqual(second.status_code, 400)
        self.assertEqual(runner.call_count, 1)
        chat.audit(self.owner, "audit-fixture", "run_pandas_analysis", "started", arguments={"code": "sensitive-literal", "inputsJson": "query-literal"})
        summary = m.AiToolAuditLogs.objects.get(request_id="audit-fixture").arguments_json
        self.assertNotIn("sensitive-literal", summary)
        self.assertNotIn("query-literal", summary)
        self.assertIn("argumentsDigest", summary)

    def test_pandas_tool_flows_through_both_model_protocols_and_private_table_artifact(self):
        entries = chat_catalog()
        tool = deepcopy(entries[0])
        tool.update(name="run_pandas_analysis", title="pandas", inputSchema={"type": "object", "properties": {
            "code": {"type": "string"}, "inputsJson": {"type": "string"}}, "required": ["code", "inputsJson"], "additionalProperties": False})
        tool["execution"].update(environment="isolated_container", maxCallsPerRequest=1, timeoutMs=30000)
        entries.append(tool)
        for protocol in ["openai_compatible", "anthropic"]:
            self.model.protocol = protocol
            self.model.save(update_fields=["protocol"])
            args = {"code": PAYLOAD["code"], "inputsJson": PAYLOAD["inputsJson"]}
            responses = [wire(protocol, "run_pandas_analysis", args), wire(protocol, answer="合成测试计算结果为 8 元，来源日期未知。")]
            with patch.object(chat.transport, "catalog", return_value=entries), \
                    patch.object(chat.transport, "execute_tool", return_value={"ok": True, "toolName": "run_pandas_analysis", "data": {"items": [{"销售额（元）": 8, "毛利率 (%)": 25}], "returned": 1, "truncated": False}}), \
                    patch.object(provider, "decrypt", return_value="isolated-fixture-key"), patch.object(provider, "bounded_json", side_effect=responses) as http:
                response = self.call("/api/ai/chat", {"clientRequestId": "pandas-chat-" + protocol, "message": "使用 pandas 计算合成样例"}, self.owner)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(http.call_count, 2)
            artifact = response.json()["artifacts"][0]
            self.assertEqual(artifact["rows"], [[8, 25]])
            self.assertEqual(artifact["columns"], ["销售额（元）", "毛利率 (%)"])
            downloaded = self.call(artifact["downloadUrl"], None, self.owner, method="GET")
            self.assertEqual(downloaded.status_code, 200, downloaded.content)
            self.assertIn("销售额（元）", downloaded.content.decode("utf-8"))
            denied = self.call(artifact["downloadUrl"], None, self.other, method="GET")
            self.assertEqual(denied.status_code, 404)


@skipUnless(os.getenv("TERUISI_PANDAS_INTEGRATION_TEST") == "1", "requires explicit isolated PostgreSQL and signed real broker")
@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PandasRealDatabaseBrokerTests(TestCase):
    user = support.AiDomainTests.user
    call = support.AiDomainTests.call

    def setUp(self):
        from django.db import connection
        self.assertEqual(connection.vendor, "postgresql")
        self.assertTrue(55440 <= int(connection.settings_dict["PORT"]) <= 55999)
        if os.getenv("TERUISI_PANDAS_INTEGRATION_PORT"):
            port = int(os.environ["TERUISI_PANDAS_INTEGRATION_PORT"])
            self.assertTrue(18121 <= port <= 18129)
            self.enterContext(patch.object(sandbox, "PORT", port))
        support.AiDomainTests.setUp(self)

    def test_real_three_app_join_returns_chinese_amount_and_audited_private_artifact(self):
        from erp_reference.models import ErpProductMaster
        from inventory.models import InventoryStockLine
        from sales.models import SalesOrderLine, sales_projection_values
        from django.utils import timezone
        from system_datasets.reader import query
        from . import artifacts
        admin = self.user("cross-app@example.invalid", "admin", None)
        stamp = timezone.now().isoformat()
        ErpProductMaster.objects.create(product_code="合成-A", product_name="合成风扇", brand="合成品牌",
            source_row_number=1, last_import_batch_id="cross-erp", created_at=stamp, updated_at=stamp)
        InventoryStockLine.objects.create(batch_id="cross-stock", row_key="cross-stock-A", source_row_number=1,
            snapshot_date="2026-09-10", warehouse="广东仓", product_code="合成-A", available_quantity=8)
        for number, amount, quantity in [(1, 10000, 2), (2, -2500, -1)]:
            raw = dict(source_line_key=f"cross-{number}", ship_time="2026-09-10T10:00:00+08:00",
                product_code="合成-A", product_name="合成风扇", warehouse="广东仓", category="合成设备",
                channel="自营", platform="京东", shop_name="合成店", order_no=f"cross-{number}")
            values = {f.name: "" for f in SalesOrderLine._meta.fields if f.get_internal_type() == "TextField" and not f.has_default()}
            values.update(raw)
            values.update(sales_projection_values(raw))
            values.update(source_row_hash=str(number)*64, first_import_batch_id="cross-sales", last_import_batch_id="cross-sales",
                source_row_number=number, quantity=quantity, allocated_amount_cents=amount,
                list_unit_price_cents=5000, cost_amount_cents=0, allocated_unit_price_cents=5000,
                fee_allocation_cents=0, gross_profit_cents=amount, gross_margin_bps=10000,
                untaxed_gross_profit_cents=amount, untaxed_gross_margin_bps=10000, created_at=stamp, updated_at=stamp)
            SalesOrderLine.objects.create(**values)
        inputs = [
            {"name": "sales", "dataset": "rows_sales_order_lines", "query": {"columns": ["product_code", "allocated_amount_cents", "quantity"],
                "filters": [{"field": "last_import_batch_id", "op": "eq", "value": "cross-sales"}], "pageSize": 1}},
            {"name": "erp", "dataset": "rows_erp_product_master", "query": {"columns": ["product_code", "brand"], "pageSize": 1}},
            {"name": "stock", "dataset": "rows_inventory_stock_lines", "query": {"columns": ["product_code", "available_quantity"],
                "filters": [{"field": "batch_id", "op": "eq", "value": "cross-stock"}], "pageSize": 1}},
        ]
        code = """sales = frames['sales'].groupby('product_code', as_index=False).agg(allocated_amount_cents=('allocated_amount_cents', 'sum'), quantity=('quantity', 'sum'))
result = sales.merge(frames['erp'], on='product_code', validate='one_to_one').merge(frames['stock'], on='product_code', validate='one_to_one')
result['销售额（元）'] = result['allocated_amount_cents'] / 100
result = result[['product_code', 'brand', '销售额（元）', 'quantity', 'available_quantity']]
"""
        entries = catalog_fixture()
        entry = deepcopy(entries[0])
        entry.update(name="get_system_dataset_records", allowedRoles=["admin"], scopePolicy="unscoped_only")
        entries.append(entry)
        def edge(name, args, principal, **kwargs):
            data = {} if name == "get_data_freshness" else query(args["dataset"], json.loads(args["queryJson"]), principal)
            chat.audit(principal, kwargs["request_id"], name, "success", arguments=args)
            return {"ok": True, "toolName": name, "data": data}
        with patch.object(datasets.transport, "catalog", return_value=entries), patch.object(datasets.transport, "execute_tool", side_effect=edge):
            response = self.call("/api/ai/consumer", {**PAYLOAD, "inputsJson": canonical(inputs), "code": code}, admin, request_id="cross-app-real")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["items"], [{"product_code": "合成-A", "brand": "合成品牌", "销售额（元）": 75.0,
                                       "quantity": 1, "available_quantity": 8}])
        self.assertEqual([s["source"]["domain"] for s in data["sources"]], ["sales", "erp_reference", "inventory"])
        self.assertEqual([s["pages"] for s in data["sources"]], [2, 1, 1])
        self.assertTrue(data["cleanupVerified"])
        artifact = artifacts.candidate("run_pandas_analysis", data)
        self.assertIn("销售额（元）", artifact["columns"])
        self.assertEqual(m.AiToolAuditLogs.objects.filter(tool_name="get_system_dataset_records").count(), 4)

    def test_real_export_compute_receipt_replay_scope_and_audit(self):
        from erp_reference.models import ErpProductMaster
        from django.utils import timezone
        from system_datasets.reader import query
        from .control_models import AiWriteReceipt
        admin = self.user("pandas-admin@example.invalid", "admin", None)
        for number in range(3):
            ErpProductMaster.objects.create(product_code=f"合成-{number}", product_name="合成商品", brand="合成品牌",
                source_row_number=number+1, last_import_batch_id="synthetic-pandas", created_at=timezone.now().isoformat(), updated_at=timezone.now().isoformat())
        entries = catalog_fixture()
        entry = deepcopy(entries[0])
        entry.update(name="get_system_dataset_records", allowedRoles=["admin"], scopePolicy="unscoped_only")
        entries.append(entry)

        def edge(name, args, principal, **kwargs):
            # Replace only edge transport with the real owning-domain SQL reader.
            data = {} if name == "get_data_freshness" else query(args["dataset"], json.loads(args["queryJson"]), principal)
            chat.audit(principal, kwargs["request_id"], name, "success", arguments=args)
            return {"ok": True, "toolName": name, "data": data}

        payload = {**PAYLOAD, "code": "result = pd.DataFrame([{'count': len(frames['sales'])}])"}
        with patch.object(datasets.transport, "catalog", return_value=entries), patch.object(datasets.transport, "execute_tool", side_effect=edge), \
                patch.object(sandbox, "call_runner", wraps=sandbox.call_runner) as runner:
            first = self.call("/api/ai/consumer", payload, admin, request_id="real-pandas-fixture")
            second = self.call("/api/ai/consumer", payload, admin, request_id="real-pandas-fixture")
            denied = self.call("/api/ai/consumer", payload, self.owner, request_id="real-pandas-denied")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.json(), first.json())
        self.assertGreaterEqual(denied.status_code, 400)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(first.json()["items"], [{"count": 3}])
        self.assertEqual(first.json()["sources"][0]["pages"], 3)
        receipt = AiWriteReceipt.objects.get(request_id="real-pandas-fixture")
        self.assertEqual(receipt.response_status, 200)
        self.assertEqual(receipt.response_payload["items"], [{"count": 3}])
        self.assertEqual(m.AiToolAuditLogs.objects.filter(tool_name="get_system_dataset_records").count(), 3)
