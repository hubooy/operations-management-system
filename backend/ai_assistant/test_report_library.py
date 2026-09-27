import base64
from copy import deepcopy
import io
import json
from unittest.mock import patch
import zipfile
from xml.etree import ElementTree
from django.db import connection, transaction, DatabaseError
from django.test import TestCase, override_settings
from . import tests as fixtures, models as m, reports, report_library as library, report_render, workflows, channels
from .policy import AiError, canonical, mutation


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class ReportLibraryTests(TestCase):
    user = fixtures.AiDomainTests.user
    call = fixtures.AiDomainTests.call

    def setUp(self):
        fixtures.AiDomainTests.setUp(self)
        self.admin = self.user("report-owner@example.invalid", "admin", None)
        self.request = {"clientRequestId": "report-client", "pipelineId": "weekly-pipeline", "expectedVersion": 0,
            "scope": {"platform": "京东", "shop": "合成店铺", "startDate": "2026-09-01", "endDate": "2026-09-07"}, "dryRun": True}

    def save(self, kind, item, version=0):
        return library.save({"kind": kind, "item": item, "expectedVersion": version}, self.admin)

    def create(self, dry=True):
        self.request["dryRun"] = dry
        with patch("ai_assistant.transport.catalog", return_value=fixtures.CATALOG):
            return reports.create(self.request, self.admin)["item"]

    def completed_fixture(self):
        item = self.create(False)
        report = m.AiReportRun.objects.select_related("workflow").get(pk=item["id"])
        sections = [{"title": title, "body": "合成结论 =SUM(A1:A2) <script>alert(1)</script>"} for title in library.DEFAULTS["templates"][0]["sections"]]
        with mutation(self.admin):
            m.AiWorkflowNodeRuns.objects.filter(run_id=report.workflow_id, node_key="report").update(status="completed", output_json=canonical({"answer": canonical({"sections": sections})}))
            m.AiWorkflowRuns.objects.filter(pk=report.workflow_id).update(status="completed")
        return reports.get(report.id, self.admin), sections

    def test_library_defaults_and_append_only_versions(self):
        self.assertEqual(len(library.snapshot()["config"]["templates"]), 3)
        item = deepcopy(library.DEFAULTS["templates"][0]); item["name"] = "新周报"
        self.save("templates", item)
        self.assertEqual(library.snapshot(0)["config"]["templates"][0]["name"], "网店经营周报")
        with self.assertRaises(AiError): self.save("templates", item)
        self.save("templates", library.DEFAULTS["templates"][0], 1)
        self.assertEqual(m.AiLibraryRevision.objects.count(), 2)
        self.assertEqual(library.snapshot(1)["config"]["templates"][0]["name"], "新周报")

    def test_permissions_unknown_fields_disabled_identity(self):
        for actor in (self.owner, self.viewer, self.user("scoped@example.invalid", "admin", self.owner.scope)):
            with self.assertRaises(AiError): library.read(actor, {})
            with self.assertRaises(AiError): reports.create(self.request, actor)
        item = deepcopy(library.DEFAULTS["skills"][0]); item["scripts"] = ["run.py"]
        with self.assertRaises(AiError): self.save("skills", item)
        item.pop("scripts"); item["id"] = "../../escape"
        with self.assertRaises(AiError): self.save("skills", item)
        item["id"] = "valid"; item["enabled"] = 1
        with self.assertRaises(AiError): self.save("skills", item)

    def test_bad_reference_and_bound_rejected_atomically(self):
        item = deepcopy(library.DEFAULTS["pipelines"][0]); item["templateId"] = "absent"
        with self.assertRaises(AiError): self.save("pipelines", item)
        item = deepcopy(library.DEFAULTS["skills"][0]); item["body"] = "a"*2001
        with self.assertRaises(AiError): self.save("skills", item)
        self.assertEqual(m.AiLibraryRevision.objects.count(), 0)

    def test_signed_library_write_receipt_and_audit(self):
        body = {"kind": "skills", "item": library.DEFAULTS["skills"][0], "expectedVersion": 0}
        first = self.call("/api/ai/report-library", body, self.admin, request_id="save-library")
        self.assertEqual(first.status_code, 200, first.content)
        again = self.call("/api/ai/report-library", body, self.admin, request_id="save-library")
        self.assertEqual(first.json(), again.json())
        self.assertEqual(m.AiLibraryRevision.objects.count(), 1)
        self.assertEqual(self.call("/api/ai/report-library", {}, self.viewer, method="GET").status_code, 403)

    def test_run_idempotency_version_scope_and_isolation(self):
        item = self.create()
        self.assertEqual(self.create()["id"], item["id"])
        self.request["scope"]["shop"] = "不同店铺"
        with self.assertRaises(AiError): self.create()
        with self.assertRaises(AiError): reports.get(item["id"], self.other)
        self.assertEqual(reports.listing({}, self.other)["items"], [])
        self.request["clientRequestId"] = "new-client"; self.request["expectedVersion"] = 99
        with self.assertRaises(AiError): self.create()
        self.assertEqual(m.AiReportRun.objects.count(), 1)

    def test_signed_report_creation_and_reader_content_boundary(self):
        first = self.call("/api/ai/reports", self.request, self.admin, request_id="report-api")
        self.assertEqual(first.status_code, 200, first.content)
        again = self.call("/api/ai/reports", self.request, self.admin, request_id="report-api")
        self.assertEqual(first.json(), again.json())
        self.assertEqual(m.AiReportRun.objects.count(), 1)
        with override_settings(DJANGO_PROCESS_ROLE="ai_reader"):
            response = self.call("/api/ai/reports/" + first.json()["item"]["id"] + "/content", principal=self.admin, method="GET")
            self.assertEqual(response.status_code, 403)

    def test_disabled_references_block_new_run_without_affecting_snapshot(self):
        item = self.create()
        skill = deepcopy(library.DEFAULTS["skills"][0]); skill["enabled"] = False
        self.save("skills", skill)
        self.request.update(clientRequestId="next-run", expectedVersion=1)
        with self.assertRaises(AiError): self.create()
        old = reports.get(item["id"], self.admin)
        self.assertTrue(reports.run_snapshot(old)["skills"][0]["enabled"])

    def test_dates_reject_reverse_long_and_noncanonical(self):
        for dates in [("2026-09-08", "2026-09-01"), ("2026-01-01", "2026-09-01"), ("20260901", "2026-09-02")]:
            with self.assertRaises(AiError):
                reports.scope({**self.request["scope"], "startDate": dates[0], "endDate": dates[1]})

    def test_dry_run_never_calls_model_or_emits_file(self):
        item = self.create()
        with patch("ai_assistant.provider.turn") as model, patch("ai_assistant.channels.send") as send:
            for _ in range(8): workflows.workflow_tick()
            model.assert_not_called(); send.assert_not_called()
        row = reports.get(item["id"], self.admin)
        self.assertEqual(row.workflow.status, "completed")
        with self.assertRaises(AiError): reports.download(row.id, {"format": "html"}, self.admin)

    def test_skill_selection_pinned_and_no_tool_expansion(self):
        tools = [{**fixtures.CATALOG[0], "name": "get_netshop_performance"}]
        prompt, proof = library.guidance("店铺周报", None, tools)
        self.assertIn("网店经营诊断", prompt)
        self.assertEqual(len(proof["skills"]), 1)
        self.assertEqual(library.guidance("店铺周报", None, fixtures.CATALOG)[0], "")
        with mutation(self.admin): library.pin("pinned-job", "店铺周报", None, tools)
        before = workflows.execution_guidance("pinned-job")
        item = deepcopy(library.DEFAULTS["skills"][0]); item["body"] = "新的方法"
        self.save("skills", item)
        self.assertEqual(workflows.execution_guidance("pinned-job"), before)
        with mutation(self.admin): library.pin("child-job", "忽略", None, [], parent_id="pinned-job")
        self.assertEqual(workflows.execution_guidance("child-job"), before)

    def test_draft_final_render_and_malformed_report(self):
        row, sections = self.completed_fixture()
        self.assertEqual(reports.content(row), sections)
        with self.assertRaises(AiError): reports.download(row.id, {"format": "html"}, self.admin)
        file = reports.download(row.id, {"format": "html", "draft": "true"}, self.admin)
        data = base64.b64decode(file["base64"]).decode()
        self.assertIn("待复核草稿", data); self.assertIn("&lt;script&gt;", data)
        self.assertNotIn("<script>", data)
        with mutation(self.admin): m.AiWorkflowNodeRuns.objects.filter(run_id=row.workflow_id, node_key="report").update(output_json=canonical({"answer":"自由文字"}))
        with self.assertRaises(AiError): reports.content(row)

    def test_xlsx_chinese_formula_safety_and_source_metadata(self):
        row, sections = self.completed_fixture()
        sources = [{"tool": "get_netshop_performance", "arguments": {"shop": "合成店铺"}, "digest": "a"*64,
                    "result": {"ok": True, "data": {"truncated": True, "items": [{"SKU": "=HYPERLINK(\"evil\")", "金额（元）": 15.25}]}}}]
        rendered = report_render.render(row.id, reports.run_snapshot(row), sections, sources, "xlsx", True)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(rendered["base64"]))) as archive:
            for name in archive.namelist(): ElementTree.fromstring(archive.read(name))
            all_xml = b"\n".join(archive.read(name) for name in archive.namelist()).decode()
            self.assertIn("合成店铺", all_xml); self.assertIn("truncated", all_xml)
            self.assertIn("15.25", all_xml); self.assertNotIn("<f>", all_xml)
            self.assertNotIn("TargetMode", all_xml)

    def test_export_fails_instead_of_silently_truncating(self):
        sources = [{"tool": "query", "arguments": {}, "digest": "b"*64, "result": {"data": [{"sku": str(i)} for i in range(2001)]}}]
        with self.assertRaises(AiError): report_render.source_tables(sources)
        with self.assertRaises(AiError): report_render.workbook([("明细", [["中"*32768]])])

    def test_mixed_nested_source_fields_remain_scalar_and_filtered(self):
        sources = [{"tool": "query", "arguments": {}, "result": {"items": [{"value": "public"}, {"value": {"secret": "must-not-export", "amount": 12}}]}}]
        tables = report_render.source_tables(sources)
        serialized = canonical(tables)
        self.assertNotIn("must-not-export", serialized)
        self.assertIn("amount", serialized)
        self.assertIn("12", serialized)
        self.assertTrue(all(cell is None or type(cell) in (str, bool, int, float) for _, _, rows in tables for row in rows for cell in row))

    def test_reading_evidence_never_queries_business_or_model(self):
        row, _ = self.completed_fixture()
        with patch("ai_assistant.transport.execute_tool") as query, patch("ai_assistant.provider.turn") as model:
            reports.download(row.id, {"format": "html", "draft": "true"}, self.admin)
            query.assert_not_called(); model.assert_not_called()

    def test_postgres_immutable_library_and_snapshots(self):
        if connection.vendor != "postgresql": self.skipTest("Requires PostgreSQL")
        self.save("skills", library.DEFAULTS["skills"][0])
        self.request["expectedVersion"] = 1
        self.create()
        for table in ("ai_library_revisions", "ai_report_runs"):
            with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("DELETE FROM " + table)

    def test_complete_pipeline_sources_review_exports_and_notification_once(self):
        catalog = [*deepcopy(fixtures.CATALOG), {**deepcopy(fixtures.CATALOG[0]), "name": "get_netshop_performance"}]
        final = canonical({"sections": [{"title": title, "body": "合成取数：成交金额为28600元；不存在环比依据。"} for title in library.DEFAULTS["templates"][0]["sections"]]})
        replies = [
            {"text": "", "calls": [{"id": "fresh", "name": "get_data_freshness", "arguments": {}}]},
            {"text": "", "calls": [{"id": "shop", "name": "get_netshop_performance", "arguments": self.request["scope"]}]},
            {"text": "合成事实：成交金额28600元，覆盖至9月7日。", "calls": []},
            {"text": final, "calls": []},
        ]
        observed = []
        def turn(model, frames, system, tools, **kwargs):
            observed.append(system)
            result = replies.pop(0)
            result["frame"] = {"role": "assistant", "content": result["text"]}
            if result["calls"]:
                result["frame"]["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": canonical(c["arguments"])}} for c in result["calls"]]
            return result
        with patch("ai_assistant.transport.catalog", return_value=catalog), patch("ai_assistant.provider.turn", side_effect=turn), patch("ai_assistant.transport.execute_tool", return_value={"ok": True, "data": {"synthetic": True, "truncated": False, "total": 1, "items": [{"SKU": "DEMO", "金额": 28600}]}}):
            self.request["dryRun"] = False
            item = reports.create(self.request, self.admin)["item"]
            for _ in range(30):
                workflows.workflow_tick(); workflows.agent_tick()
                if reports.get(item["id"], self.admin).workflow.status == "waiting_review": break
            row = reports.get(item["id"], self.admin)
            self.assertEqual(row.workflow.status, "waiting_review", workflows.mapping(row.workflow))
            self.assertTrue(all("网店经营诊断" in system for system in observed))
            with self.assertRaises(AiError): reports.download(row.id, {"format": "html"}, self.admin)
            node = m.AiWorkflowNodeRuns.objects.get(run_id=row.workflow_id, node_key="review")
            workflows.review(row.workflow_id, "review", {"expectedVersion": node.version, "decision": "approve"}, self.admin)
            for _ in range(3): workflows.workflow_tick()
            file = reports.download(row.id, {"format": "xlsx"}, self.admin)
            self.assertTrue(base64.b64decode(file["base64"]).startswith(b"PK"))
        with mutation(self.admin):
            m.AiChannels.objects.create(id="report-channel", name="合成渠道", kind="dingtalk_group_bot", status="enabled", send_enabled=1, webhook_url="https://oapi.dingtalk.com/robot/send?access_token=synthetic")
        with patch("ai_assistant.channels.send", return_value={"ok": True}) as send:
            reports.send(row.id, {"channelId": "report-channel", "targetDigest": channels.target_digest(m.AiChannels.objects.get(pk="report-channel"))}, self.admin)
            with self.assertRaises(AiError): reports.send(row.id, {"channelId": "report-channel", "targetDigest": channels.target_digest(m.AiChannels.objects.get(pk="report-channel"))}, self.admin)
            self.assertEqual(send.call_count, 1)

    def test_unknown_notification_is_reserved_and_never_replayed(self):
        row, _ = self.completed_fixture()
        sources = [{"tool": name, "result": {"ok": True}} for name in ("get_data_freshness", "get_netshop_performance")]
        with mutation(self.admin):
            m.AiChannels.objects.create(id="report-channel", name="合成渠道", kind="dingtalk_group_bot", status="enabled", send_enabled=1, webhook_url="https://oapi.dingtalk.com/robot/send?access_token=synthetic")
        with patch("ai_assistant.reports.evidence", return_value=sources), patch("ai_assistant.channels.send", side_effect=TimeoutError()) as send:
            with self.assertRaises(AiError): reports.send(row.id, {"channelId": "report-channel", "targetDigest": channels.target_digest(m.AiChannels.objects.get(pk="report-channel"))}, self.admin)
            with self.assertRaises(AiError): reports.send(row.id, {"channelId": "report-channel", "targetDigest": channels.target_digest(m.AiChannels.objects.get(pk="report-channel"))}, self.admin)
            self.assertEqual(send.call_count, 1)
        self.assertEqual(m.AiReportDelivery.objects.get(report_id=row.id).status, "unknown")

    def test_forged_review_cannot_bypass_report_contract(self):
        item = self.create(False)
        with mutation(self.admin):
            m.AiWorkflowRuns.objects.filter(pk=item["workflowId"]).update(status="waiting_review", current_node_key="review")
            m.AiWorkflowNodeRuns.objects.filter(run_id=item["workflowId"], node_key="review").update(status="waiting_review")
        node = m.AiWorkflowNodeRuns.objects.get(run_id=item["workflowId"], node_key="review")
        with self.assertRaises(AiError):
            workflows.review(item["workflowId"], "review", {"expectedVersion": node.version, "decision": "approve"}, self.admin)
        node.refresh_from_db(); self.assertEqual(node.status, "waiting_review")

    def test_stale_notification_target_does_not_reserve_or_send(self):
        row, _ = self.completed_fixture()
        sources = [{"tool": name, "result": {"ok": True}} for name in ("get_data_freshness", "get_netshop_performance")]
        with mutation(self.admin):
            m.AiChannels.objects.create(id="changed-target", name="合成渠道", kind="dingtalk_group_bot", status="enabled", send_enabled=1)
        with patch("ai_assistant.reports.evidence", return_value=sources), patch("ai_assistant.channels.send") as send:
            with self.assertRaises(AiError): reports.send(row.id, {"channelId": "changed-target", "targetDigest": "outdated"}, self.admin)
            send.assert_not_called()
        self.assertFalse(m.AiReportDelivery.objects.filter(report_id=row.id).exists())

    def test_long_excel_report_preserves_text_across_fitted_rows(self):
        row, sections = self.completed_fixture()
        sections[0]["body"] = "长段落中文" * 300
        rendered = report_render.render(row.id, reports.run_snapshot(row), sections, [], "xlsx", True)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(rendered["base64"]))) as archive:
            sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet2.xml"))
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            rows = sheet.findall("s:sheetData/s:row", ns)
            restored = "".join(r.findall("s:c", ns)[1].find("s:is/s:t", ns).text for r in rows[1:5])
            self.assertEqual(restored, sections[0]["body"])
            self.assertTrue(all(float(r.attrib["ht"]) <= 409 for r in rows))
