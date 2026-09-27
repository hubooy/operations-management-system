"""Planned full shop catalog -> actual source readers -> five agents -> 2 volumes.

Business rows are synthetic; only network/provider boundaries are simulated.
No production data, paid model, or external notification is used.
"""
import base64
from copy import deepcopy
import hashlib
import json
from threading import Barrier, Lock
from unittest.mock import patch
from django.db import connection
from django.test import TransactionTestCase, override_settings
from business_analysis import volume_delivery
from . import business_planning, business_evidence as evidence, business_reports as reports
from . import business_parallel, business_files, business_volume_files, models as m, workflows, tests as fixtures
from .test_business_export_catalog_acceptance import BusinessExportCatalogAcceptanceTests
from .policy import canonical, digest


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessWorkbenchCatalogAcceptanceTests(TransactionTestCase):
    user = fixtures.AiDomainTests.user
    skip_draft_report = True

    def planned_evidence_body(self, common):
        self.question = "完整核验合成店铺推广、销售、市场与B端三周期，披露空主数据。"
        self.planned = business_planning.preview({"schemaVersion": "business-plan-request-v2", "question": self.question,
            "startDate": common["startDate"], "endDate": common["endDate"], "windows": ["current", "previous", "yearAgo"],
            "shops": [{"platform": "京东", "shop": self.shop, "salesChannels": [self.channel],
                "datasets": ["promotion", "sku", "spu", "b2b", "master"]}],
            "markets": [{"platform": "京东", "category": "饮水机", "scope": "POP", "rankingDimension": "SKU", "priceBandFilter": "全部"}]}, self.admin)
        self.assertTrue(self.planned["canCollect"], self.planned["limitations"])
        self.assertEqual(self.planned["capacity"]["sourceCount"], 19)
        return {**self.planned["evidenceRequest"], "clientRequestId": "planned-full-shop",
            "expectedPrincipalKey": self.planned["principalKey"]}

    def setUp(self):
        # TransactionTestCase flushes migration seed rows between tests; own
        # the source revision fixture instead of depending on test order.
        from market.models import MarketDataRevision
        from netshop.models import NetshopDataRevision
        MarketDataRevision.objects.get_or_create(domain="market", defaults={"revision": 0, "source_digest": "0"*64})
        NetshopDataRevision.objects.get_or_create(domain="netshop", defaults={"revision": 0, "source_digest": "0"*64})
        BusinessExportCatalogAcceptanceTests.setUp(self)

    def test_planned_19_sources_complete_five_agent_review_and_persistent_156_tables(self):
        row = evidence.get_run(self.run_id, self.admin)
        self.assertEqual(digest(row.plan_json), self.planned["planDigest"])
        self.assertEqual(json.loads(row.plan_json)["catalogDigest"], self.planned["catalogDigest"])
        self.assertTrue(evidence.mapping(row)["workbenchAnalysisEnabled"])
        self.assertFalse(m.AiReportRun.objects.exists())
        def key(domain, window, dataset=None):
            return next(source["key"] for source in self.sources if source["domain"] == domain
                and source["query"]["window"] == window and (dataset is None or source["query"].get("dataset") == dataset))
        args = {"sourceKey": key("sales", "current"), "baselineKey": key("sales", "previous"), "dimension": "shop"}
        table = evidence.analysis_table(self.run_id, args, self.admin)
        actual = table["rows"][0]
        diagnosis = {"summary": "全来源合成流程核验；主数据为空，历史主数据不作假设。", "findings": [{
            "id": "comparison", "kind": "observation", "title": "ERP净销售环比核验",
            "explanation": "只描述已核验金额，不将推广归因金额混成ERP销售，也不推断因果。",
            "references": [{**args, "rowIndex": 0, "rowId": actual["id"], "metric": "netSalesCents", "field": "value"}]}]}
        tools = []
        for name in ("get_business_evidence_directory_v2", "get_business_analysis_table_v2"):
            tool = deepcopy(fixtures.CATALOG[0])
            tool["name"] = name
            tool["execution"].update(allowedSurfaces=["business_agent_v2"], maxCallsPerRequest=8)
            tools.append(tool)
        with patch("ai_assistant.transport.catalog", return_value=tools):
            item = reports.create({"clientRequestId": "full-shop-report", "evidenceRunId": self.run_id,
                "question": self.question, "expectedPrincipalKey": self.planned["principalKey"], "dryRun": False}, self.admin)["item"]
        flow = m.AiWorkflowRuns.objects.get(pk=item["workflowId"])
        barrier, mutex = Barrier(3, timeout=15), Lock()
        active, peak = 0, 0
        offsets, analysed, roles = {}, set(), set()
        def turn(model, frames, system, entries):
            nonlocal active, peak
            self.assertFalse(connection.in_atomic_block)
            jobs = list(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id, status="running"))
            job = next(job for job in jobs if frames[0]["content"].startswith(job.task+"\n<task_input>"))
            calls = m.AiAgentProviderResults.objects.filter(dispatch__job_id=job.id).count()
            with mutex:
                roles.add(job.workflow_node_key)
            if calls == 0 and job.workflow_node_key in {"commerce", "promotion", "market_b2b"}:
                with mutex:
                    active += 1
                    peak = max(peak, active)
                barrier.wait()
                with mutex:
                    active -= 1
            with mutex:
                offset = offsets.get(job.id, 0)
                done = job.id in analysed
            if offset is not None:
                name, arguments = "get_business_evidence_directory_v2", {"runId": self.run_id, "offset": offset}
                page = evidence.directory(self.run_id, {"offset": str(offset), "limit": "20"}, self.admin)
                with mutex:
                    offsets[job.id] = page["nextOffset"]
            elif not done:
                name, arguments = "get_business_analysis_table_v2", {"runId": self.run_id, **args}
                with mutex:
                    analysed.add(job.id)
            else:
                value = {"approved": True, "conflicts": [], "limitations": ["仅合成数据，非正式经营结论"]} if job.workflow_node_key == "independent_review" else (
                    {"sections": [{"title": title, "body": "合成19来源三周期流程验收；真实经营口径和决策效果尚待验收。"} for title in reports.SECTIONS], "diagnosis": diagnosis}
                    if job.workflow_node_key == "report" else diagnosis)
                text = canonical(value)
                return {"text": text, "calls": [], "frame": {"role": "assistant", "content": text}}
            call = {"id": f"call-{calls}", "name": name, "arguments": arguments}
            return {"text": "", "calls": [call], "frame": {"role": "assistant", "content": None,
                "tool_calls": [{"id": call["id"], "type": "function", "function": {"name": name, "arguments": canonical(arguments)}}]}}
        def execute(name, arguments, principal, **kwargs):
            self.assertEqual(kwargs["surface"], "business_agent_v2")
            data = evidence.directory(self.run_id, {"offset": str(arguments["offset"]), "limit": "20"}, principal) if name == "get_business_evidence_directory_v2" else evidence.analysis_table(
                self.run_id, {k: str(v) for k, v in arguments.items() if k != "runId"}, principal)
            return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": data}
        with patch("ai_assistant.transport.catalog", return_value=tools), patch("ai_assistant.provider.turn", side_effect=turn), patch("ai_assistant.transport.execute_tool", side_effect=execute):
            for _ in range(60):
                workflows.workflow_tick()
                business_parallel.agent_queue_tick()
                flow.refresh_from_db()
                if flow.status in {"waiting_review", "failed", "paused"}:
                    break
        self.assertEqual(flow.status, "waiting_review", list(m.AiAgentJobs.objects.values("workflow_node_key", "status", "error_code")))
        self.assertEqual(peak, 3)
        self.assertEqual(len(roles), 5)
        self.assertEqual(len(analysed), 5)
        self.assertTrue(all(value is None for value in offsets.values()))
        report = m.AiReportRun.objects.select_related("workflow").get(pk=item["id"])
        reports.validate_review(report, self.admin)
        node = m.AiWorkflowNodeRuns.objects.get(run=flow, node_key="human_review")
        workflows.review(flow.id, node.node_key, {"expectedVersion": node.version, "decision": "approve"}, self.admin)
        workflows.workflow_tick()
        report.refresh_from_db()
        self.assertEqual(report.workflow.status, "completed")
        file_id = business_files.create(report.id, {"deliveryMode": "volumes", "expectedPrincipalKey": self.planned["principalKey"]}, self.admin)["item"]["id"]
        with patch("ai_assistant.provider.turn") as provider, patch("ai_assistant.transport.execute_tool") as transport:
            result = business_files.tick()
            self.assertEqual(result["status"], "ready", result)
            provider.assert_not_called()
            transport.assert_not_called()
        saved = business_files.get(file_id, self.admin)
        compact = json.loads(saved.manifest_json)
        self.assertEqual(compact["volumeCount"], 2)
        self.assertEqual(len(compact["files"]), 4)
        downloaded = {}
        for descriptor in [*compact["files"], compact["manifestFile"]]:
            raw = b"".join(base64.b64decode(business_volume_files.chunk(file_id, str(descriptor["volumeIndex"]), descriptor["format"],
                {"sequence": str(i)}, self.admin)["base64"]) for i in range(1, descriptor["chunkCount"]+1))
            self.assertEqual(hashlib.sha256(raw).hexdigest(), descriptor["sha256"])
            downloaded[descriptor["volumeIndex"], descriptor["format"]] = raw
        reference = reports.bound_reference(json.loads(report.snapshot_json), self.admin)
        complete = volume_delivery.verify_full(compact, downloaded[0, "json"], binding_digest=saved.binding_digest,
            attempt=saved.attempt, draft=False, report_id=report.id, evidence_digest=reference["sealedDigest"])
        self.assertEqual(complete["sourceTableCount"], 156)
        self.assertEqual({t["key"] for t in complete["tables"] if t["key"].startswith("raw-")}, {"raw-"+source["key"] for source in self.sources})
        self.assertFalse(m.AiAgentProviderDispatches.objects.filter(state="unknown").exists())
