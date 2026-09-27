"""Synthetic two-shop/two-window acceptance with real queues and PostgreSQL.

Only provider and transport boundaries are simulated; no paid model or external
data request is made. The three specialist provider calls genuinely overlap.
"""
from copy import deepcopy
import json
from threading import Barrier, Lock
from unittest.mock import patch
from django.db import connection
from django.test import TransactionTestCase, override_settings
from sales.analysis import read_page
from sales.tests.factories import make_line
from . import business_evidence as evidence, business_reports as reports, business_parallel as parallel
from . import models as m, workflows, tests as fixtures
from .policy import canonical


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessV2ParallelAcceptanceTests(TransactionTestCase):
    user = fixtures.AiDomainTests.user

    def setUp(self):
        fixtures.AiDomainTests.setUp(self)
        self.admin = self.user("v2-parallel@example.invalid", "admin", None)
        self.tools = []
        for name in ("get_business_evidence_directory_v2", "get_business_analysis_table_v2"):
            entry = deepcopy(fixtures.CATALOG[0])
            entry["name"] = name
            entry["execution"].update(allowedSurfaces=["business_agent_v2"], maxCallsPerRequest=8)
            self.tools.append(entry)
        sources = []
        for index, (key, shop, current, previous) in enumerate((
            ("a", "京东合成一店", 12345, 10000), ("b", "京东合成二店", 20000, 22000)
        )):
            channel = "京东-"+shop
            for offset, (window, amount, date) in enumerate((("current", current, "2026-08-01"), ("previous", previous, "2026-07-31"))):
                make_line(index*2+offset+1, f"parallel-{key}-{window}", shop_name=shop, channel=channel,
                    allocated_amount_cents=amount, online_spec_code="SYNTHETIC-"+key,
                    ship_time=date+" 10:00:00", line_ship_time=date+" 10:00:00").save()
                sources.append({"key": key+"-"+window, "domain": "sales", "query": {
                    "platform": "京东", "shop": shop, "channel": channel,
                    "startDate": "2026-08-01", "endDate": "2026-08-01", "window": window}})
        self.run_id = evidence.create({"schemaVersion": "business-evidence-v2", "clientRequestId": "parallel-evidence",
            "sources": sources, "collectionMode": "bulk"}, self.admin)["item"]["id"]
        collection_tools = [deepcopy(fixtures.CATALOG[0]), {**deepcopy(fixtures.CATALOG[0]), "name": "get_business_source_page"}]

        def collect(name, args, principal, **kwargs):
            self.assertEqual(kwargs["surface"], "business_collection")
            data = {"dataCutoffDate": "2026-08-01"} if name == "get_data_freshness" else read_page(principal,
                {"operation": "analysis_records", **{k: v for k, v in args.items() if k != "domain"}})
            return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": data}

        with patch("ai_assistant.transport.catalog", return_value=collection_tools), patch("ai_assistant.transport.execute_tool", side_effect=collect):
            for source in sources:
                row = evidence.get_run(self.run_id, self.admin)
                evidence.collect(self.run_id, {"sourceKey": source["key"], "expectedVersion": row.version}, self.admin, "synthetic-read")
        row = evidence.get_run(self.run_id, self.admin)
        evidence.finish(row.id, {"expectedVersion": row.version, "action": "seal"}, self.admin)
        self.table_args = {"sourceKey": "a-current", "baselineKey": "a-previous", "dimension": "shop"}
        table = evidence.analysis_table(self.run_id, self.table_args, self.admin)
        actual = table["rows"][0]
        self.assertEqual(actual["metrics"]["netSalesCents"]["value"], 12345)
        self.assertEqual(actual["baselineMetrics"]["netSalesCents"]["value"], 10000)
        self.assertEqual(actual["comparisons"]["netSalesCents"]["difference"], 2345)
        second = evidence.analysis_table(self.run_id, {"sourceKey": "b-current", "baselineKey": "b-previous", "dimension": "shop"}, self.admin)
        self.assertEqual(second["rows"][0]["metrics"]["netSalesCents"]["value"], 20000)
        self.assertEqual(second["rows"][0]["comparisons"]["netSalesCents"]["difference"], -2000)
        self.diagnosis = {"summary": "合成两店两周期核验，缺少推广与市场数据。", "findings": [{
            "id": "synthetic-observation", "kind": "observation", "title": "一店净销售核验",
            "explanation": "只引用当前店铺 ERP 净销售，不推断推广归因或市场份额。",
            "references": [{"sourceKey": "a-current", "baselineKey": "a-previous", "dimension": "shop", "rowIndex": 0,
                "rowId": actual["id"], "metric": "netSalesCents", "field": "value"}]}]}

    def test_five_agents_prove_own_directory_then_real_human_review_and_content(self):
        def catalog(principal, surface):
            self.assertEqual(surface, "business_agent_v2")
            self.assertEqual(principal.email, self.admin.email)
            return self.tools

        with patch("ai_assistant.transport.catalog", side_effect=catalog):
            created = reports.create({"clientRequestId": "v2-parallel-report", "evidenceRunId": self.run_id,
                "question": "比较两店本期与环比，并披露其他数据缺口。", "dryRun": False}, self.admin)["item"]
        flow = m.AiWorkflowRuns.objects.get(pk=created["workflowId"])
        self.assertNotIn("sources", json.loads(flow.input_json))
        self.assertEqual(json.loads(flow.input_json)["sourceCount"], 4)
        barrier, mutex = Barrier(3, timeout=15), Lock()
        active, peak, roles = 0, 0, set()

        def turn(model, frames, system, entries):
            nonlocal active, peak
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual({entry["name"] for entry in entries}, {entry["name"] for entry in self.tools})
            candidates = list(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id, status="running"))
            job = next(job for job in candidates if frames[0]["content"].startswith(job.task+"\n<task_input>"))
            count = m.AiAgentProviderResults.objects.filter(dispatch__job_id=job.id).count()
            with mutex:
                roles.add(job.workflow_node_key)
            if count == 0:
                if job.workflow_node_key in {"commerce", "promotion", "market_b2b"}:
                    with mutex:
                        active += 1
                        peak = max(peak, active)
                    barrier.wait()
                    with mutex:
                        active -= 1
                name, args = "get_business_evidence_directory_v2", {"runId": self.run_id, "offset": 0}
            elif count == 1:
                name, args = "get_business_analysis_table_v2", {"runId": self.run_id, **self.table_args}
            else:
                value = ({"approved": True, "conflicts": [], "limitations": ["仅合成 ERP 数据，推广和市场未验收"]}
                    if job.workflow_node_key == "independent_review" else
                    {"sections": [{"title": title, "body": "合成演练。真实口径和经营效果仍待验收。"} for title in reports.SECTIONS],
                        "diagnosis": self.diagnosis} if job.workflow_node_key == "report" else self.diagnosis)
                text = canonical(value)
                return {"text": text, "calls": [], "frame": {"role": "assistant", "content": text}}
            call = {"id": f"call-{count}", "name": name, "arguments": args}
            return {"text": "", "calls": [call], "frame": {"role": "assistant", "content": None,
                "tool_calls": [{"id": call["id"], "type": "function", "function": {"name": name, "arguments": canonical(args)}}]}}

        def execute(name, args, principal, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(kwargs["surface"], "business_agent_v2")
            self.assertEqual(args["runId"], self.run_id)
            data = evidence.directory(self.run_id, {"offset": str(args["offset"]), "limit": "20"}, principal) if name == "get_business_evidence_directory_v2" else evidence.analysis_table(self.run_id,
                {k: str(v) for k, v in args.items() if k != "runId"}, principal)
            return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": data}

        with patch("ai_assistant.transport.catalog", side_effect=catalog), patch("ai_assistant.provider.turn", side_effect=turn), \
                patch("ai_assistant.transport.execute_tool", side_effect=execute):
            for _ in range(40):
                workflows.workflow_tick()
                parallel.agent_queue_tick()
                flow.refresh_from_db()
                if flow.status in {"waiting_review", "failed", "paused"}:
                    break
        self.assertEqual(flow.status, "waiting_review", list(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id).values("workflow_node_key", "status", "error_code")))
        self.assertEqual(peak, 3)
        self.assertEqual(roles, {"commerce", "promotion", "market_b2b", "independent_review", "report"})
        self.assertEqual(m.AiAgentProviderResults.objects.count(), 15)
        self.assertEqual(m.AiAgentToolResults.objects.count(), 10)
        for job in m.AiAgentJobs.objects.filter(workflow_run_id=flow.id):
            self.assertEqual(m.AiAgentToolResults.objects.filter(tool_dispatch__job=job,
                tool_dispatch__tool_name="get_business_evidence_directory_v2").count(), 1)
        report = m.AiReportRun.objects.select_related("workflow").get(pk=created["id"])
        self.assertTrue(reports.validate_review(report, self.admin)["diagnosis"]["factsVerified"])
        node = m.AiWorkflowNodeRuns.objects.get(run=flow, node_key="human_review")
        workflows.review(flow.id, node.node_key, {"expectedVersion": node.version, "decision": "approve"}, self.admin)
        workflows.workflow_tick()
        flow.refresh_from_db()
        self.assertEqual(flow.status, "completed")
        report.refresh_from_db()
        self.assertEqual(len(reports.content(report, self.admin)["sections"]), 5)
        self.assertFalse(m.AiAgentProviderDispatches.objects.filter(state="unknown").exists())
