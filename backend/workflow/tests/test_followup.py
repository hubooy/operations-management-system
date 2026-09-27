from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import date, datetime, time
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from sales.models import ErpProductMaster, SalesImportBatch, SalesOrderLine
from sales.tests.factories import TEST_SECRET, make_line, signed_headers
from workflow.followup import claim_weekly_delivery, weekly_followup
from workflow.management.commands.new_product_weekly_report import _assert_send_receipt, _due_now, _exact_identity, _run_dws
from workflow.models import NewProductLine, NewProductLineCode, NewProductWeeklyReportConfig, WorkflowWriteAuthority
from workflow.weekly_report_image import render_weekly_report_html


JPEG_2X2_BASE64 = (
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/"
    "2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCAACAAIDASIAAhEBAxEB/"
    "8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2Jy"
    "ggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLD"
    "xMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3"
    "AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6"
    "goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD8rZ55"
    "LmaSaaRpZZGLvI7EszE5JJPUmiiis6fwL0OvF/7xU/xP8z//2Q=="
)


def uploaded_image() -> dict[str, object]:
    content = base64.b64decode(JPEG_2X2_BASE64)
    return {
        "fileName": "本地产品图.jpg", "mimeType": "image/jpeg", "sizeBytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(), "dataBase64": JPEG_2X2_BASE64,
    }


def body_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def master(code: str, name: str, row: int, brand: str = "志高") -> None:
    ErpProductMaster.objects.create(
        product_code=code,
        product_name=name,
        brand=brand,
        specification="",
        barcode="",
        category="商用设备",
        supplier="",
        product_status="正常",
        source_row_number=row,
        last_import_batch_id="erp-products-1",
        created_at="2026-08-01 00:00:00",
        updated_at="2026-09-01 00:00:00",
    )


@patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
class NewProductWeeklyFollowupTests(TestCase):
    def setUp(self) -> None:
        WorkflowWriteAuthority.objects.filter(id=1).update(status="postgres")
        SalesImportBatch.objects.create(
            id="sales-weekly-1", source="test", file_name="sales.xlsx", file_size_bytes=100,
            file_hash="b" * 64, sheet_name="销售", status="completed", row_count=3,
            inserted_count=3, duplicate_count=0, warning_count=0, warnings_json="[]",
            totals_json="{}", created_at="2026-09-01 10:00:00", completed_at="2026-09-14 10:00:00",
        )
        master("YS-001", "油水分离器 100 型", 1)
        master("YS-002", "商用油水分离器 120 型", 2)
        master("OTHER-1", "商用净水机", 3)

    def request_json(self, method: str, url: str, payload: dict[str, object], request_id: str):
        body = body_bytes(payload)
        return getattr(self.client, method.lower())(
            url,
            data=body,
            content_type="application/json; charset=utf-8",
            headers=signed_headers(url, method=method, body=body, request_id=request_id),
        )

    def create_line(self):
        return self.request_json(
            "POST",
            "/api/workflow/new-product-lines",
            {
                "name": "油水分离器",
                "matchTerms": ["商用油水分离器"],
                "productImageUrl": "https://example.test/oil-water-separator.png",
                "monitoringStartDate": "2026-09-01",
                "weeklyUnitTarget": 10,
                "weeklySalesTargetCents": 100_000,
                "active": True,
                "codes": [{"productCode": "YS-001"}],
            },
            "followup-create-line",
        )

    def test_manual_product_line_and_learning_use_jackyun_master_identity(self) -> None:
        created = self.create_line()
        self.assertEqual(created.status_code, 201, created.content)
        item = created.json()["item"]
        self.assertEqual(item["name"], "油水分离器")
        self.assertEqual(item["matchTerms"], ["商用油水分离器"])
        self.assertEqual(item["productImageUrl"], "https://example.test/oil-water-separator.png")
        self.assertNotIn("trackingWeeks", item)
        self.assertEqual(item["codes"][0]["productName"], "油水分离器 100 型")

        learned = self.request_json("POST", "/api/workflow/new-product-lines/learn", {}, "followup-learn")
        self.assertEqual(learned.status_code, 200, learned.content)
        result = learned.json()["result"]
        self.assertEqual([row["productCode"] for row in result["added"]], ["YS-002"])
        self.assertEqual(NewProductLineCode.objects.count(), 2)

        repeated = self.request_json("POST", "/api/workflow/new-product-lines/learn", {}, "followup-learn-repeat")
        self.assertEqual(repeated.json()["result"]["added"], [])

    def test_draft_learning_is_read_only_and_uses_unsaved_keywords(self):
        from urllib.parse import urlencode
        line = self.create_line().json()["item"]
        before_lines = list(NewProductLine.objects.values())
        before_codes = list(NewProductLineCode.objects.values())
        url = "/api/workflow/new-product-lines/learn?" + urlencode({"lineId": line["id"], "name": "编辑中的产品线", "term": "净水机"})
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row["productCode"] for row in response.json()["candidates"]], ["OTHER-1"])
        self.assertEqual(list(NewProductLine.objects.values()), before_lines)
        self.assertEqual(list(NewProductLineCode.objects.values()), before_codes)
        viewer = self.client.get(url, headers=signed_headers(url, role="viewer"))
        self.assertEqual(viewer.status_code, 403)

    def test_draft_learning_conflicts_and_existing_ownership_are_not_auto_added(self):
        from workflow.followup import preview_product_line_codes
        line = self.create_line().json()["item"]
        result = preview_product_line_codes("", "第二产品线", ["油水分离器"])
        self.assertEqual(result["candidates"], [])
        self.assertEqual({row["productCode"] for row in result["ambiguous"]}, {"YS-001", "YS-002"})
        self.assertEqual(NewProductLineCode.objects.count(), 1)
        own = preview_product_line_codes(line["id"], "油水分离器", [])
        self.assertEqual({row["productCode"] for row in own["candidates"]}, {"YS-001", "YS-002"})

    def test_production_reader_routes_learning_get_and_rejects_post(self):
        from django.test import RequestFactory
        from django.urls import URLResolver
        from django.urls.resolvers import RegexPattern
        from workflow.urls import read_patterns
        from urllib.parse import urlencode

        line = self.create_line().json()["item"]
        path = "/api/workflow/new-product-lines/learn"
        url = path + "?" + urlencode({"lineId": line["id"], "name": "编辑产品线", "term": "净水机"})
        match = URLResolver(RegexPattern(r"^api/workflow/"), read_patterns).resolve(path.lstrip("/"))
        self.assertIsNotNone(match)
        before = list(NewProductLineCode.objects.values())
        response = match.func(RequestFactory().get(url, headers=signed_headers(url)))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads(response.content)["candidates"][0]["productCode"], "OTHER-1")
        rejected = match.func(RequestFactory().post(path, data="{}", content_type="application/json"))
        self.assertEqual(rejected.status_code, 405)
        self.assertEqual(list(NewProductLineCode.objects.values()), before)

    def test_draft_learning_rejects_truncation_and_bad_terms(self):
        from workflow.followup import preview_product_line_codes
        from workflow.errors import WorkflowApiError
        with patch("workflow.followup.MAX_CATALOG_SCAN", 1), self.assertRaises(WorkflowApiError):
            preview_product_line_codes("", "油水分离器", [])
        with self.assertRaises(WorkflowApiError):
            preview_product_line_codes("", "a", [])
        with patch("workflow.followup.MAX_CODES_PER_LINE", 1), self.assertRaises(WorkflowApiError):
            preview_product_line_codes("", "油水分离器", [])

    def test_import_trigger_defers_until_expected_erp_projection_batch_is_visible(self) -> None:
        self.create_line()
        deferred = self.request_json(
            "POST",
            "/api/workflow/new-product-lines/learn",
            {"expectedSourceBatchId": "erp-products-2"},
            "followup-learn-deferred",
        )
        self.assertEqual(deferred.status_code, 200, deferred.content)
        self.assertTrue(deferred.json()["result"]["deferred"])
        self.assertEqual(NewProductLineCode.objects.count(), 1)

        current = self.request_json(
            "POST",
            "/api/workflow/new-product-lines/learn",
            {"expectedSourceBatchId": "erp-products-1"},
            "followup-learn-current",
        )
        self.assertFalse(current.json()["result"]["deferred"])
        self.assertEqual([row["productCode"] for row in current.json()["result"]["added"]], ["YS-002"])

    def test_unknown_manual_code_is_rejected(self) -> None:
        response = self.request_json(
            "POST",
            "/api/workflow/new-product-lines",
            {
                "name": "不存在产品线", "matchTerms": [], "monitoringStartDate": "2026-09-01",
                "productImageUrl": "", "weeklyUnitTarget": None, "weeklySalesTargetCents": None,
                "active": True, "codes": [{"productCode": "MISSING"}],
            },
            "followup-missing-code",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("尚未出现在吉客云货品主数据", response.json()["error"])

    def test_product_image_rejects_local_or_insecure_targets(self) -> None:
        for index, product_image_url in enumerate([
            "http://example.test/product.png",
            "https://127.0.0.1/product.png",
            "https://localhost/product.png",
            "https://example.test:8443/product.png",
        ]):
            response = self.request_json(
                "POST",
                "/api/workflow/new-product-lines",
                {
                    "name": "图片安全校验", "matchTerms": [], "monitoringStartDate": "2026-09-01",
                    "productImageUrl": product_image_url, "weeklyUnitTarget": None,
                    "weeklySalesTargetCents": None, "active": True, "codes": [],
                },
                f"followup-image-url-{index}",
            )
            self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(NewProductLine.objects.count(), 0)

    def test_local_product_image_upload_is_saved_served_embedded_and_removable(self) -> None:
        response = self.request_json(
            "POST",
            "/api/workflow/new-product-lines",
            {
                "name": "本地图片产品线", "matchTerms": [], "monitoringStartDate": "2026-09-01",
                "productImage": uploaded_image(), "weeklyUnitTarget": None,
                "weeklySalesTargetCents": None, "active": True, "codes": [],
            },
            "followup-upload-image",
        )
        self.assertEqual(response.status_code, 201, response.content)
        item = response.json()["item"]
        self.assertEqual(item["productImageFileName"], "本地产品图.jpg")
        self.assertRegex(item["productImageUrl"], rf"^/api/workflow/new-product-lines/{item['id']}/image\?v=1$")

        image_path = f"/api/workflow/new-product-lines/{item['id']}/image"
        image_response = self.client.get(image_path, headers=signed_headers(image_path, request_id="followup-read-image"))
        self.assertEqual(image_response.status_code, 200, image_response.content)
        self.assertEqual(image_response.json()["image"]["dataBase64"], JPEG_2X2_BASE64)

        report = weekly_followup(week_start=date.fromisoformat("2026-09-07"), embed_uploaded_images=True)
        uploaded = next(row for row in report["items"] if row["id"] == item["id"])
        self.assertTrue(uploaded["productImageUrl"].startswith("data:image/jpeg;base64,"))

        removed = self.request_json(
            "PATCH",
            f"/api/workflow/new-product-lines/{item['id']}",
            {"productImage": None, "expectedVersion": item["version"]},
            "followup-remove-image",
        )
        self.assertEqual(removed.status_code, 200, removed.content)
        self.assertEqual(removed.json()["item"]["productImageUrl"], "")
        self.assertEqual(removed.json()["item"]["productImageFileName"], "")

    def test_local_product_image_upload_rejects_tampered_content(self) -> None:
        image = uploaded_image()
        image["sha256"] = "0" * 64
        response = self.request_json(
            "POST",
            "/api/workflow/new-product-lines",
            {
                "name": "损坏图片产品线", "matchTerms": [], "monitoringStartDate": "2026-09-01",
                "productImage": image, "weeklyUnitTarget": None,
                "weeklySalesTargetCents": None, "active": True, "codes": [],
            },
            "followup-upload-image-tampered",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(NewProductLine.objects.count(), 0)

    def test_weekly_metrics_group_all_codes_by_user_named_line(self) -> None:
        self.create_line()
        self.request_json("POST", "/api/workflow/new-product-lines/learn", {}, "followup-learn-metrics")
        SalesOrderLine.objects.bulk_create([
            make_line(1, "weekly-1", product_code="YS-001", product_name="油水分离器 100 型", quantity=3, allocated_amount_cents=30_000, cost_amount_cents=18_000, gross_profit_cents=12_000, ship_time="2026-09-08 10:00:00"),
            make_line(2, "weekly-2", product_code="YS-002", product_name="商用油水分离器 120 型", quantity=2, allocated_amount_cents=24_000, cost_amount_cents=14_000, gross_profit_cents=10_000, ship_time="2026-09-09 10:00:00"),
            make_line(3, "weekly-prev", product_code="YS-001", product_name="油水分离器 100 型", quantity=1, allocated_amount_cents=10_000, cost_amount_cents=6_000, gross_profit_cents=4_000, ship_time="2026-09-02 10:00:00"),
        ])
        url = "/api/workflow/new-product-weekly-followup?weekStart=2026-09-07"
        response = self.client.get(url, headers=signed_headers(url, request_id="followup-report"))
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertIn("UTC", payload["timezone"])
        self.assertEqual(payload["weekStart"], "2026-09-07")
        self.assertEqual(payload["items"][0]["name"], "油水分离器")
        self.assertEqual(payload["items"][0]["codeCount"], 2)
        self.assertEqual(payload["items"][0]["current"]["netQuantity"], 5)
        self.assertEqual(payload["items"][0]["current"]["netSalesCents"], 54_000)
        self.assertEqual(payload["items"][0]["previous"]["netSalesCents"], 10_000)
        self.assertEqual(payload["summary"]["netSalesCents"], 54_000)
        self.assertEqual(payload["timelineStart"], "2026-08-03")
        self.assertEqual([week["weekStart"] for week in payload["weeks"]], [
            "2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31", "2026-09-07",
        ])
        self.assertEqual(payload["items"][0]["brand"], "志高")
        self.assertEqual(payload["items"][0]["productImageUrl"], "https://example.test/oil-water-separator.png")
        self.assertEqual(payload["items"][0]["weeklyNetQuantities"], [0, 0, 0, 0, 1, 5])
        self.assertEqual(payload["messageText"], "新品销售周报")
        document, width, height = render_weekly_report_html(payload)
        self.assertIn("品牌", document)
        self.assertIn("产品图", document)
        self.assertIn("https://example.test/oil-water-separator.png", document)
        self.assertIn("object-fit:contain", document)
        self.assertNotIn("object-fit:cover", document)
        self.assertIn("第32周", document)
        self.assertIn("油水分离器", document)
        self.assertGreaterEqual(width, 1280)
        self.assertGreater(height, 200)

    def test_weekly_matrix_rejects_a_week_before_the_fixed_timeline(self) -> None:
        url = "/api/workflow/new-product-weekly-followup?weekStart=2026-07-27"
        response = self.client.get(url, headers=signed_headers(url, request_id="followup-before-anchor"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("2026-08-03", response.json()["error"])

    def test_weekly_matrix_uses_zhigao_when_master_brand_is_blank(self) -> None:
        ErpProductMaster.objects.filter(product_code="YS-001").update(brand="")
        created = self.create_line()
        self.assertEqual(created.status_code, 201, created.content)
        url = "/api/workflow/new-product-weekly-followup?weekStart=2026-09-07"
        response = self.client.get(url, headers=signed_headers(url, request_id="followup-brand-fallback"))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["items"][0]["brand"], "志高")

    def test_each_product_line_excludes_sales_before_its_own_monitoring_start(self) -> None:
        response = self.request_json(
            "POST",
            "/api/workflow/new-product-lines",
            {
                "name": "净水机",
                "matchTerms": [],
                "productImageUrl": "",
                "monitoringStartDate": "2026-09-08",
                "weeklyUnitTarget": None,
                "weeklySalesTargetCents": None,
                "active": True,
                "codes": [{"productCode": "OTHER-1"}],
            },
            "followup-create-water-line",
        )
        self.assertEqual(response.status_code, 201, response.content)
        SalesOrderLine.objects.bulk_create([
            make_line(11, "water-before", product_code="OTHER-1", product_name="商用净水机", quantity=9, allocated_amount_cents=90_000, ship_time="2026-09-02 10:00:00"),
            make_line(12, "water-after", product_code="OTHER-1", product_name="商用净水机", quantity=2, allocated_amount_cents=20_000, ship_time="2026-09-09 10:00:00"),
        ])
        url = "/api/workflow/new-product-weekly-followup?weekStart=2026-09-07"
        report = self.client.get(url, headers=signed_headers(url, request_id="followup-line-start")).json()
        item = next(row for row in report["items"] if row["name"] == "净水机")
        self.assertEqual(item["current"]["netQuantity"], 2)
        self.assertEqual(item["cumulative"]["netQuantity"], 2)

    def test_product_line_continues_indefinitely_and_paused_line_is_excluded_from_report(self) -> None:
        created = self.create_line()
        self.assertEqual(created.status_code, 201, created.content)
        line = created.json()["item"]
        far_future = weekly_followup(week_start=date.fromisoformat("2035-09-03"))
        self.assertEqual([item["id"] for item in far_future["items"]], [line["id"]])
        self.assertNotEqual(far_future["items"][0]["status"], "tracking_ended")

        paused = self.request_json(
            "PATCH",
            f"/api/workflow/new-product-lines/{line['id']}",
            {"active": False, "expectedVersion": line["version"]},
            "followup-pause-line",
        )
        self.assertEqual(paused.status_code, 200, paused.content)
        self.assertFalse(paused.json()["item"]["active"])
        report = weekly_followup(week_start=date.fromisoformat("2026-09-07"))
        self.assertEqual(report["items"], [])
        self.assertEqual(report["summary"]["lineCount"], 0)

    def test_report_config_uses_local_clock_and_requires_approved_exact_targets(self) -> None:
        missing = self.request_json(
            "PATCH", "/api/workflow/new-product-weekly-report-config",
            {
                "enabled": True,
                "targetGroupName": "相似测试群",
                "robotName": "志高助手",
                "expectedVersion": 1,
            },
            "followup-config-missing",
        )
        self.assertEqual(missing.status_code, 400)
        configured = self.request_json(
            "PATCH", "/api/workflow/new-product-weekly-report-config",
            {
                "enabled": True,
                "targetGroupName": "测试群聊",
                "robotName": "志高助手",
                "sendWeekday": 0,
                "sendLocalTime": "09:30",
                "expectedVersion": 1,
            },
            "followup-config-ok",
        )
        self.assertEqual(configured.status_code, 200, configured.content)
        self.assertTrue(configured.json()["config"]["enabled"])

    def test_delivery_claim_is_idempotent_and_bot_resolution_requires_one_exact_match(self) -> None:
        self.create_line()
        report = weekly_followup(week_start=date.fromisoformat("2026-09-07"))
        config = NewProductWeeklyReportConfig.objects.get(id=1)
        config.target_group_name = "新品销售跟进群"
        config.robot_name = "志高助手"
        first, claimed = claim_weekly_delivery(report, config, actor="test@example.com")
        repeated, repeated_claimed = claim_weekly_delivery(report, config, actor="test@example.com")
        self.assertTrue(claimed)
        self.assertFalse(repeated_claimed)
        self.assertEqual(first.id, repeated.id)

        identity, _record = _exact_identity(
            [{"items": [{"robotName": "志高助手", "robotCode": "robot-1"}, {"robotName": "其他", "robotCode": "robot-2"}]}],
            expected_name="志高助手",
            name_fields=("robotName",),
            id_fields=("robotCode",),
            label="钉钉机器人",
        )
        self.assertEqual(identity, "robot-1")
        _assert_send_receipt({"ok": True, "result": {"success": True}})

    def test_windows_dws_invocation_uses_node_without_a_command_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            npm_root = Path(directory)
            (npm_root / "node_modules" / "dingtalk-workspace-cli" / "bin").mkdir(parents=True)
            launcher = npm_root / "dws.cmd"
            node = npm_root / "node.exe"
            cli = npm_root / "node_modules" / "dingtalk-workspace-cli" / "bin" / "dws.js"
            launcher.write_text("", encoding="utf-8")
            node.write_bytes(b"")
            cli.write_text("", encoding="utf-8")
            completed = subprocess.CompletedProcess([], 0, stdout=b'{"ok":true}', stderr=b"")
            with patch.object(os, "name", "nt"), patch(
                "workflow.management.commands.new_product_weekly_report.shutil.which",
                side_effect=lambda name: str(launcher) if name == "dws.cmd" else None,
            ), patch(
                "workflow.management.commands.new_product_weekly_report.subprocess.run",
                return_value=completed,
            ) as run:
                self.assertEqual(_run_dws(["chat", "search", "--query", "测试群聊"]), {"ok": True})
            command = run.call_args.args[0]
            self.assertEqual(command[:2], [str(node), str(cli)])
            self.assertNotIn("cmd.exe", command)
            self.assertEqual(command[-2:], ["--format", "json"])

    def test_management_command_reconciles_without_sending_by_default(self) -> None:
        self.create_line()
        stdout = StringIO()
        call_command("new_product_weekly_report", stdout=stdout)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["learning"]["added"][0]["productCode"], "YS-002")

    def test_management_command_idempotently_enables_weekly_report(self) -> None:
        first_stdout = StringIO()
        call_command("configure_new_product_weekly_report", "--enable", stdout=first_stdout)
        first = json.loads(first_stdout.getvalue())
        self.assertEqual(first["status"], "enabled")
        self.assertTrue(first["config"]["enabled"])

        repeated_stdout = StringIO()
        call_command("configure_new_product_weekly_report", "--enable", stdout=repeated_stdout)
        repeated = json.loads(repeated_stdout.getvalue())
        self.assertEqual(repeated["status"], "already_enabled")
        self.assertEqual(repeated["config"]["version"], first["config"]["version"])

    @patch("workflow.management.commands.new_product_weekly_report._run_dws")
    def test_management_command_dry_run_verifies_exact_group_robot_and_membership(self, run_dws) -> None:
        self.create_line()
        NewProductWeeklyReportConfig.objects.filter(id=1).update(enabled=True)
        run_dws.side_effect = [
            {"items": [{"title": "测试群聊", "openConversationId": "group-1"}], "hasMore": False},
            {"items": [{"robotName": "志高助手", "robotCode": "robot-1"}], "hasMore": False},
            {"items": [{"robotName": "志高助手", "robotCode": "robot-1"}]},
            {"ok": True, "dryRun": True},
        ]
        stdout = StringIO()
        call_command("new_product_weekly_report", "--dry-run", stdout=stdout)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "dry_run_ok")
        self.assertEqual(run_dws.call_count, 4)
        self.assertIn("--dry-run", run_dws.call_args_list[-1].args[0])

    @patch("workflow.management.commands.new_product_weekly_report.render_weekly_report_png")
    @patch("workflow.management.commands.new_product_weekly_report._run_dws")
    def test_management_command_uploads_png_and_sends_preview_link_by_bot(self, run_dws, render_png) -> None:
        self.create_line()
        NewProductWeeklyReportConfig.objects.filter(id=1).update(enabled=True)
        render_png.return_value = {"sha256": "a" * 64, "sizeBytes": 4_096, "width": 1280, "height": 300}
        run_dws.side_effect = [
            {"items": [{"title": "测试群聊", "openConversationId": "group-1"}], "hasMore": False},
            {"items": [{"robotName": "志高助手", "robotCode": "robot-1"}], "hasMore": False},
            {"items": [{"robotName": "志高助手", "robotCode": "robot-1"}]},
            {"ok": True, "result": {"dentryUuid": "file-1"}},
            {"ok": True, "result": {"docUrl": "https://alidocs.dingtalk.com/i/nodes/file-1"}},
            {"ok": True, "result": {"success": True}},
        ]
        stdout = StringIO()
        call_command("new_product_weekly_report", "--send", "--force", stdout=stdout)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "delivered")
        self.assertEqual(payload["deliveryMode"], "png_drive_preview_by_bot")
        self.assertEqual(run_dws.call_count, 6)
        self.assertEqual(run_dws.call_args_list[3].args[0][0:2], ["drive", "upload"])
        sent = "\n".join(run_dws.call_args_list[5].args[0])
        self.assertIn("https://alidocs.dingtalk.com/i/nodes/file-1", sent)
        self.assertIn("新品销售周报", sent)
        self.assertNotIn("本周净销量", sent)
        self.assertIn("--yes", run_dws.call_args_list[5].args[0])

    def test_local_schedule_uses_machine_calendar(self) -> None:
        config = NewProductWeeklyReportConfig.objects.get(id=1)
        config.send_weekday = 2
        config.send_local_time = time(9, 30)
        self.assertTrue(_due_now(config, datetime.fromisoformat("2026-09-02T09:35:00+08:00")))
        self.assertFalse(_due_now(config, datetime.fromisoformat("2026-09-02T09:40:00+08:00")))
