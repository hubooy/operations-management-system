from copy import deepcopy
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.http import QueryDict
from urllib.parse import urlencode
from netshop.models import NetshopRow
from netshop.analysis import read_page, validate_request
from sales.tests.factories import signed_headers, TEST_SECRET
from business_analysis.test_budget import fixture
from business_analysis.test_report_files import ReportData
from business_analysis.report_files import NS, column_name
from . import business_budget, business_evidence as evidence, business_reports as reports, business_export, models as m, workflows
from .test_business_evidence import BusinessEvidenceTests, versioned_netshop_facts
from . import tests as fixtures
from .policy import AiError, canonical


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessBudgetTests(TestCase):
    user = BusinessEvidenceTests.user
    call = BusinessEvidenceTests.call

    def setUp(self):
        BusinessEvidenceTests.setUp(self)
        with versioned_netshop_facts():
            for i in range(2):
                NetshopRow.objects.create(source_row_key=f"budget-{i}", source_row_hash=f"{i+1:064x}", first_import_batch_id="fixture", last_import_batch_id="fixture", source_row_number=i+1,
                    source="jd_promotion", dataset="ad", platform="京东", shop_name="合成店", business_date="2026-08-01", sku_id=f"S{i}", spu_id="P1",
                    spend_cents=3000, net_transaction_amount_cents=15000, clicks=300, impressions=3000, net_orders=30,
                    metrics_json={"spendCents": 3000, "netTransactionAmountCents": 15000, "clicks": 300, "impressions": 3000, "netOrders": 30}, raw_json={})
        query = {"platform": "京东", "shop": "合成店", "dataset": "promotion", "startDate": "2026-08-01", "endDate": "2026-08-01"}
        body = {"clientRequestId": "budget-evidence", "sources": [{"key": "ads", "domain": "netshop", "query": query}]}
        self.evidence_id = evidence.create(body, self.admin)["item"]["id"]
        def source(name, args, principal, **kwargs):
            data = {"dataCutoffDate": "2026-08-01"} if name == "get_data_freshness" else read_page(*validate_request(QueryDict(urlencode(args))))
            return {"ok": True, "auditStatus": "recorded", "toolName": name, "data": data}
        with patch("ai_assistant.transport.catalog", return_value=self.catalog), patch("ai_assistant.transport.execute_tool", side_effect=source):
            evidence.collect(self.evidence_id, {"sourceKey": "ads", "expectedVersion": 1}, self.admin, "budget-source")
        evidence.finish(self.evidence_id, {"expectedVersion": 2, "action": "seal"}, self.admin)
        rows = evidence.analysis_table(self.evidence_id, {"sourceKey": "ads", "dimension": "sku"}, self.admin)["rows"]
        self.plan, _ = fixture()
        for target, row in zip(self.plan["targets"], rows): target.update(rowId=row["id"], rowIndex=row["rowIndex"])
        self.request = {"clientRequestId": "budget-report", "evidenceRunId": self.evidence_id, "question": "分析预算并说明假设", "dryRun": False, "budgetPlan": self.plan}
        self.tools = [fixtures.CATALOG[0], *[{**fixtures.CATALOG[0], "name": name} for name in sorted(reports.TOOLS | {reports.BUDGET_TOOL})]]
        self.diagnosis = {"summary": "情景需要业务验证", "findings": [{"id": "gap-1", "kind": "gap", "title": "缺少因果实验", "explanation": "归因效率不是增量效果", "references": []}]}

    def create(self, dry=False):
        with patch("ai_assistant.transport.catalog", return_value=self.tools):
            item = reports.create({**self.request, "dryRun": dry}, self.admin)["item"]
        return m.AiReportRun.objects.select_related("workflow").get(pk=item["id"])

    def run_workflow(self, *, all_pages=True):
        report = self.create()
        rounds = {}
        def turn(model, frames, system, entries):
            self.assertEqual({entry["name"] for entry in entries}, reports.TOOLS | {reports.BUDGET_TOOL})
            job = m.AiAgentJobs.objects.get(status="running")
            count = rounds.get(job.id, 0); rounds[job.id] = count+1
            if count == 0:
                name, args = "get_business_analysis_evidence", {"runId": self.evidence_id}
            elif job.workflow_node_key in {"promotion", "independent_review", "report"} and count <= (2 if all_pages else 1):
                name, args = reports.BUDGET_TOOL, {"runId": self.evidence_id, "reportId": report.id, "offset": count-1, "limit": 1}
            else:
                value = {"approved": True, "conflicts": [], "limitations": ["仅合成演练"]} if job.workflow_node_key == "independent_review" else {"sections": [{"title": title, "body": "预算假设不代表实际收益。"} for title in reports.SECTIONS], "diagnosis": self.diagnosis} if job.workflow_node_key == "report" else self.diagnosis
                answer = canonical(value)
                return {"text": answer, "calls": [], "frame": {"role": "assistant", "content": answer}}
            return {"text": "", "calls": [{"id": f"call-{count}", "name": name, "arguments": args}], "frame": {"role": "assistant", "content": None,
                "tool_calls": [{"id": f"call-{count}", "type": "function", "function": {"name": name, "arguments": canonical(args)}}]}}
        def tool(name, args, principal, **kwargs):
            data = business_budget.read(args["reportId"], {k: v for k, v in args.items() if k != "reportId"}, principal) if name == reports.BUDGET_TOOL else evidence.mapping(evidence.get_run(self.evidence_id, principal))
            return {"ok": True, "auditStatus": "recorded", "toolName": name, "data": data}
        with patch("ai_assistant.transport.catalog", return_value=self.tools), patch("ai_assistant.provider.turn", side_effect=turn), patch("ai_assistant.transport.execute_tool", side_effect=tool):
            for _ in range(90):
                workflows.workflow_tick(); workflows.agent_tick()
                report.workflow.refresh_from_db()
                if report.workflow.status in {"waiting_review", "failed"}: break
        self.assertEqual(report.workflow.status, "waiting_review", list(m.AiAgentJobs.objects.values("status", "error_code")))
        return report

    def test_actual_evidence_budget_pages_identity_and_no_live_source(self):
        report = self.create(True)
        with patch("ai_assistant.transport.execute_tool") as source, patch("ai_assistant.provider.turn") as model:
            result = business_budget.for_report(report, self.admin)
            self.assertEqual(result["scenarios"][0]["summary"]["projectedAttributedGmvCents"], 45000)
            page = business_budget.read(report.id, {"runId": self.evidence_id, "limit": 1}, self.admin)
            self.assertTrue(page["pagination"]["hasMore"])
            self.assertEqual(page["rows"][0]["budgetCents"], 6000)
            source.assert_not_called(); model.assert_not_called()
        for principal in (self.viewer, self.user("budget-other@example.invalid", "admin", None)):
            with self.assertRaises(AiError): business_budget.for_report(report, principal)
        with self.assertRaises(AiError): business_budget.read(report.id, {"runId": "other"}, self.admin)
        with patch("ai_assistant.transport.catalog", return_value=self.tools):
            self.assertEqual(reports.create({**self.request, "dryRun": True}, self.admin)["item"]["id"], report.id)
            changed = deepcopy(self.request); changed["budgetPlan"]["reserveCents"] = 2000
            with self.assertRaises(AiError): reports.create({**changed, "dryRun": True}, self.admin)

    def test_budget_agent_receipts_are_required_for_all_objects_and_export_same_numbers(self):
        report = self.run_workflow()
        result = reports.validate_review(report, self.admin)
        self.assertEqual(result["budget"]["allocation"]["allocatedCents"], 9000)
        node = m.AiWorkflowNodeRuns.objects.get(run_id=report.workflow_id, node_key="human_review")
        workflows.review(report.workflow_id, node.node_key, {"expectedVersion": node.version, "decision": "approve"}, self.admin)
        workflows.workflow_tick(); report.workflow.refresh_from_db()
        xlsx, html = io.BytesIO(), io.BytesIO()
        business_export.build(report, self.admin, xlsx, html)
        tables = {t["key"]: t for t in ReportData(html.getvalue().decode()).value["tables"]}
        self.assertEqual(tables["budget-targets"]["proof"]["rowCount"], 2)
        keys = [c["key"] for c in tables["budget-summary"]["columns"]]
        self.assertEqual(tables["budget-summary"]["rows"][0][keys.index("/projectedAttributedGmvCents")], 45000)
        # Read the actual ZIP cells independently; comparing only renderer
        # metadata would miss a column shift, wrong unit or missing value.
        with zipfile.ZipFile(io.BytesIO(xlsx.getvalue())) as archive:
            self.assertIsNone(archive.testzip())
            manifest = json.loads(archive.read("teruisi-manifest.json"))
            for sheet_number, table in enumerate(tables.values(), 1):
                if not table["key"].startswith("budget-"): continue
                self.assertEqual(manifest["tables"][sheet_number-1], table["proof"])
                root = ET.fromstring(archive.read(f"xl/worksheets/sheet{sheet_number}.xml"))
                cells = {c.get("r"): c for c in root.findall('.//{'+NS+'}c')}
                for row_number, row in enumerate(table["rows"], 4):
                    for column, expected in enumerate(row, 1):
                        address = column_name(column)+str(row_number)
                        c = cells[address]
                        node = c.find('.//{'+NS+'}t') if c.get("t") == "inlineStr" else c.find('{'+NS+'}v')
                        actual = None if node is None else node.text or ""
                        if c.get("t") == "b": actual = actual == "1"
                        elif c.get("t") == "n": actual = float(actual)
                        self.assertEqual(actual, expected, (table["key"], address))

    def test_incomplete_budget_review_cannot_be_formally_approved(self):
        report = self.run_workflow(all_pages=False)
        with self.assertRaisesMessage(AiError, "尚未完整读取固定预算情景"):
            reports.validate_review(report, self.admin)

    def test_offline_renderer_keeps_original_tables_and_legacy_version(self):
        report = self.run_workflow()
        original = []
        for version in (1, 2, 3):
            xlsx, html = io.BytesIO(), io.BytesIO()
            proof = business_export.build(report, self.admin, xlsx, html, draft=True, renderer_version=version)
            document = html.getvalue().decode()
            original.append(ReportData(document).value)
            self.assertEqual('id="budget-data"' in document, version >= 2)
            if version == 2:
                self.assertIn("unreviewed_local_scenario", document)
                self.assertIn("Excel 当前保留原报告参数快照", document)
            if version == 3:
                self.assertIn("Excel 附有独立可编辑试算页", document)
                self.assertEqual(len(proof['budgetCalculator']['sheets']), 3)
                self.assertEqual(proof['budgetCalculator']['reportId'], report.id)
                self.assertNotIn("Excel 当前保留原报告参数快照", document)
        self.assertEqual(original[0], original[1])
        self.assertEqual(original[1], original[2])
        from . import business_files
        file_id = business_files.create(report.id, {'draft': True}, self.admin)['item']['id']
        with patch('ai_assistant.provider.turn') as model, patch('ai_assistant.transport.execute_tool') as source:
            self.assertEqual(business_files.tick()['status'], 'ready')
            model.assert_not_called(); source.assert_not_called()
        item = business_files.mapping(business_files.get(file_id, self.admin))
        self.assertEqual(item['manifest']['rendererVersion'], 5)
        self.assertEqual(item['manifest']['budgetCalculator'], proof['budgetCalculator'])

    def test_stale_ref_overlap_nonpromotion_and_missing_dimension_rejected(self):
        for change in ("id", "dimension", "row", "source"):
            plan = deepcopy(self.plan)
            if change == "id": plan["targets"][0]["rowId"] = "0"*64
            if change == "dimension": plan["targets"][1]["dimension"] = "shop"
            if change == "row": plan["targets"][0]["rowIndex"] = 100
            if change == "source": plan["targets"][0]["sourceKey"] = "missing"
            with self.assertRaises(AiError): business_budget.resolve(self.evidence_id, plan, self.admin)

    def test_budget_endpoint_is_reader_only_and_principal_bound(self):
        report = self.create(True)
        url = f"/api/ai/reports/{report.id}/budget?runId={self.evidence_id}"
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}), override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET, DJANGO_PROCESS_ROLE="ai_reader"), patch("ai_assistant.views.authority"):
            response = self.client.get(url, headers=signed_headers(url, email=self.admin.email))
            self.assertEqual(response.status_code, 200, response.content)
            self.assertIn(self.client.get(url, headers=signed_headers(url, email=self.viewer.email, role="viewer")).status_code, (403, 404))

    def test_parameter_preview_is_read_only_and_new_report_preserves_previous_version(self):
        report = self.create(True)
        original = report.snapshot_json
        changed = deepcopy(self.plan); changed["reserveCents"] = 2000
        with override_settings(DJANGO_PROCESS_ROLE="ai_reader"), patch("ai_assistant.views.authority"), patch("ai_assistant.provider.turn") as model:
            response = self.call(f"/api/ai/reports/{report.id}/budget-preview", {"budgetPlan": changed}, principal=self.admin)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["budget"]["allocation"]["allocatedCents"], 8000)
            model.assert_not_called()
        report.refresh_from_db(); self.assertEqual(report.snapshot_json, original)
        self.assertEqual(m.AiReportRun.objects.count(), 1)
        with patch("ai_assistant.transport.catalog", return_value=self.tools):
            new = reports.create({**self.request, "clientRequestId": "budget-revision", "budgetPlan": changed, "previousReportId": report.id, "dryRun": True}, self.admin)
        snapshot = json.loads(m.AiReportRun.objects.get(pk=new["item"]["id"]).snapshot_json)
        self.assertEqual(snapshot["previousReportId"], report.id)
        self.assertNotEqual(snapshot["budgetPlanDigest"], json.loads(original)["budgetPlanDigest"])
