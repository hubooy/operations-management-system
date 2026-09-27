"""Full scheduler and owning data, synthetic provider/transport boundaries.

This verifies orchestration and evidence, not real-model judgment quality.
"""
from datetime import timedelta
import base64
import hashlib
import json
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from business_analysis import screening_package

from . import test_business_screening_admission as fixtures, test_business_screening_diagnosis as answers
from . import business_screening_creation as creation, business_screening_runtime_contract as contract
from . import business_screening_permission as permission, business_screening_tools as tools
from . import business_reports, business_parallel, workflows, models as m, provider, transport
from .policy import canonical, mutation, uid


@override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningPipelineTests(TransactionTestCase):
    user=fixtures.ScreeningAdmissionTests.user
    call=fixtures.ScreeningAdmissionTests.call
    collect_body=fixtures.ScreeningAdmissionTests.collect_body
    bundle=fixtures.ScreeningAdmissionTests.bundle
    input_for=fixtures.ScreeningAdmissionTests.input_for
    insert=fixtures.ScreeningAdmissionTests.insert
    seed=fixtures.ScreeningAdmissionTests.seed

    def setUp(self):
        fixtures.ScreeningAdmissionTests.setUp(self)
        permission.clear();self.addCleanup(permission.clear)

    def test_real_five_job_scheduler_candidate_numbers_and_human_review(self):
        answers.candidate_evidence(self)
        with mutation(self.admin):
            m.AiModels.objects.filter(pk=self.model.pk).update(max_tool_rounds=20,max_total_tool_calls=40)
        with patch.object(transport,"catalog",return_value=fixtures.catalog()):
            created=creation.create({"clientRequestId":uid("pipeline"),"evidenceRunId":self.parent.id,
                "question":"合成全流程诊断与规划","dryRun":False,"analysisMode":"screening-v1"},self.admin)
        report=m.AiReportRun.objects.select_related("workflow").get(pk=created["item"]["id"])
        flow_id=report.workflow_id
        with mutation(self.admin):
            m.AiWorkflowRuns.objects.exclude(pk=flow_id).update(next_run_at=timezone.now()+timedelta(days=1))
        graph=workflows.validate_graph(contract.graph(False))
        counts={role:0 for role in contract.ROLES}

        def model_reply(model,frames,system,entries):
            role=next(n["key"] for n in graph["nodes"] if n["type"]=="agent"
                and frames[0]["content"].startswith(n["instruction"]+"\n<task_input>"))
            job=m.AiAgentJobs.objects.get(workflow_run_id=flow_id,workflow_node_key=role)
            self.assertEqual(frames[0]["content"],job.task+"\n<task_input>"+job.input_json.replace("<","\\u003c")+"</task_input>")
            counts[role]+=1
            saved=[json.loads(row.result_json)["data"] for row in m.AiAgentToolResults.objects.filter(
                tool_dispatch__job=job,tool_dispatch__tool_name=contract.PACKAGE_TOOL).order_by("tool_dispatch__tool_call_ordinal")]
            offset=saved[-1]["pagination"]["nextOffset"] if saved else 0
            if offset is not None:
                ref=json.loads(job.input_json)["workflowInput"]
                args={"runId":ref["evidenceRunId"],"reportId":ref["reportId"],
                    "screeningId":ref["screeningIntent"]["id"],"role":role,"offset":offset}
                call={"id":f"{role}-{counts[role]}","name":contract.PACKAGE_TOOL,"arguments":args}
                frame={"role":"assistant","content":None,"tool_calls":[{"id":call["id"],"type":"function",
                    "function":{"name":call["name"],"arguments":canonical(args)}}]}
                return {"text":"","calls":[call],"frame":frame,"usage":{}}
            decoded=screening_package.decode_pages(saved)
            choices=[item for group in decoded["candidates"] for item in group["items"]]
            chosen=next((c for c in choices if any(value is not None for value in c["current"].values())),None)
            ref=None
            if chosen is not None:
                metric=next(key for key,value in chosen["current"].items() if value is not None)
                ref={"candidateId":chosen["candidateId"],"metric":metric,"field":"value"}
            output=answers.answer(role,ref)
            if role=="report" and ref:
                finding=output["diagnosis"]["findings"][0]
                finding["kind"]="action"
                finding["action"]={"object":"固定候选对应推广对象","change":"人工复核后小范围调整",
                    "prerequisites":"先核对统计口径与退款","successMetric":"观察净成交与推广费用",
                    "observationDays":7,"rollback":"指标未改善则恢复原设置","priority":"high",
                    "ownerRole":"运营负责人","budgetImpact":"待固定预算情景测算"}
            text=canonical(output)
            return {"text":text,"calls":[],"frame":{"role":"assistant","content":text},"usage":{}}

        def owning_tool(name,args,principal,**kwargs):
            self.assertEqual(kwargs["surface"],contract.SURFACE)
            self.assertEqual(name,contract.PACKAGE_TOOL)
            params={key:str(value) for key,value in args.items() if key!="reportId"}
            return {"ok":True,"toolName":name,"auditStatus":"recorded",
                "data":tools.read(args["reportId"],"package",params,principal)}

        with (patch.object(transport,"catalog",return_value=fixtures.catalog()),
                patch.object(provider,"turn",side_effect=model_reply),patch.object(transport,"execute_tool",side_effect=owning_tool)):
            self.assertEqual(workflows.workflow_tick()["status"],"screening_prepared")
            self.assertEqual(m.AiAgentJobs.objects.filter(workflow_run_id=flow_id).count(),0)
            workflows.workflow_tick()
            self.assertEqual(m.AiAgentJobs.objects.filter(workflow_run_id=flow_id).count(),3)
            simultaneous=business_parallel.agent_queue_tick()
            self.assertEqual(simultaneous["selected"],3)
            for _ in range(80):
                queued=list(workflows.agent_candidates().filter(workflow_run_id=flow_id).values_list("id",flat=True))
                for job_id in queued:
                    result=workflows.agent_tick(job_id=job_id)
                    self.assertNotIn(result["status"],{"failed","lease_lost"},result)
                workflows.workflow_tick()
                flow=m.AiWorkflowRuns.objects.get(pk=flow_id)
                self.assertNotIn(flow.status,{"failed","paused"},flow.error_code)
                if flow.status=="waiting_review":break
            else:self.fail("synthetic five-role workflow did not reach human review")
            report.refresh_from_db()
            value=business_reports.content(report,self.admin)
            self.assertEqual(set(value["screening"]["readProofs"]),set(contract.ROLES))
            self.assertEqual(len({p["jobId"] for p in value["screening"]["readProofs"].values()}),5)
            self.assertTrue(all(count>=2 for count in counts.values()))
            finding=value["diagnosis"]["findings"][0]
            self.assertEqual(finding["kind"],"action")
            self.assertTrue(finding["facts"][0]["verification"]["candidateNumberVerified"])
            self.assertFalse(finding["facts"][0]["verification"]["agentReadVerified"])
            human=m.AiWorkflowNodeRuns.objects.get(run_id=flow_id,node_key="human_review")
            workflows.review(flow_id,"human_review",{"expectedVersion":human.version,"decision":"approve"},self.admin)
            workflows.workflow_tick()
        self.assertEqual(m.AiWorkflowRuns.objects.get(pk=flow_id).status,"completed")
        self.assertEqual(m.AiAgentJobs.objects.filter(workflow_run_id=flow_id,status="completed").count(),5)
        self.assertFalse(m.AiAgentProviderDispatches.objects.filter(job__workflow_run_id=flow_id).exclude(state="succeeded").exists())
        # The exact completed five-job workflow also reaches formal paired
        # delivery; no separate pre-written report fixture substitutes for it.
        from . import business_files, business_volume_files, business_evidence
        created=business_files.create(report.id,{"deliveryMode":"volumes","draft":False,
            "expectedPrincipalKey":business_evidence.principal_key(self.admin)},self.admin)
        built=business_files.tick()
        self.assertEqual(built["status"],"ready",built)
        stored=business_files.get(created["item"]["id"],self.admin)
        self.assertFalse(stored.draft)
        manifest=json.loads(stored.manifest_json)
        self.assertEqual({item["format"] for item in manifest["files"]},{"html","xlsx"})
        for item in [*manifest["files"],manifest["manifestFile"]]:
            raw=b"".join(base64.b64decode(business_volume_files.chunk(stored.id,str(item["volumeIndex"]),item["format"],
                {"sequence":str(sequence)},self.admin)["base64"]) for sequence in range(1,item["chunkCount"]+1))
            self.assertEqual((len(raw),hashlib.sha256(raw).hexdigest()),(item["bytes"],item["sha256"]))
            import os
            if os.environ.get("TERUISI_SCREENING_SYNTHETIC_FILE_FIXTURE") == "1":
                from pathlib import Path
                folder=Path(__file__).resolve().parents[2]/".runtime"/("screening-formal-files" if stored.renderer_version == 4 else f"screening-formal-files-v{stored.renderer_version}")
                folder.mkdir(exist_ok=True)
                (folder/f"volume-{item['volumeIndex']}.{item['format']}").write_bytes(raw)
