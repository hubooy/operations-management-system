"""Real runtime rejection of forged known analysis receipts; synthetic I/O.

The fixture report uses the real creator and sealed sources. The corruption is
injected at the transport result boundary before the immutable ledger insert;
no database trigger or append-only rule is disabled to mutate saved evidence.
"""
import json
from unittest.mock import patch

from django import test as djtest
from . import test_business_integrated_reports as fixtures
from . import business_integrated as integrated, business_integrated_tools as tool_service
from . import models as m, workflows
from .policy import canonical, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class BusinessIntegratedTamperTests(djtest.TransactionTestCase):
    user=fixtures.BusinessIntegratedReportTests.user
    call=fixtures.BusinessIntegratedReportTests.call
    collect_body=fixtures.BusinessIntegratedReportTests.collect_body
    create=fixtures.BusinessIntegratedReportTests.create

    def setUp(self):
        fixtures.BusinessIntegratedReportTests.setUp(self)

    def corrupt_analysis(self,mode):
        report,_=self.create()
        with patch("ai_assistant.transport.catalog",return_value=self.tools):
            workflows.workflow_tick()
        job=m.AiAgentJobs.objects.get(workflow_run_id=report.workflow_id,workflow_node_key="commerce")
        pairs={p["salesKey"]:p["pairKey"] for p in json.loads(report.snapshot_json)["mappingPlan"]["pairs"]}
        scope={"runId":self.run_id,"reportId":report.id,"offset":0}
        arguments={**scope,"mode":mode,"dimension":"sku",**(
            {"pairKey":pairs["sales"],"baselinePairKey":pairs["previous"]} if mode=="mapped" else {"sourceKey":"ads"})}
        schedule=[(integrated.DIRECTORY_TOOL,scope),(integrated.TABLE_TOOL,arguments)]
        replies=[]
        for index,(name,args) in enumerate(schedule):
            call={"id":f"tamper-call-{index}","name":name,"arguments":args}
            replies.append({"text":"","calls":[call],"frame":{"role":"assistant","content":None,
                "tool_calls":[{"id":call["id"],"type":"function","function":{"name":name,"arguments":canonical(args)}}]}})
        forged=[]
        original_values=[]
        def execute(name,args,principal,**kwargs):
            self.assertEqual(kwargs["surface"],integrated.SURFACE)
            operation="directory" if name==integrated.DIRECTORY_TOOL else "analysis"
            data=tool_service.read(report.id,operation,{k:str(v) for k,v in args.items() if k!="reportId"},principal)
            if operation=="analysis":
                metric="netSalesCents" if mode=="mapped" else "spendCents"
                row=next(r for r in data["table"]["rows"] if (r["metrics"].get(metric) or {}).get("value") is not None)
                original_values.append(row["metrics"][metric]["value"])
                row["metrics"][metric]["value"]+=12345
                # A forged caller recomputes every self-reported page digest.
                if "pageDigest" in data["table"]:
                    data["table"]["pageDigest"]=digest({k:v for k,v in data["table"].items() if k!="pageDigest"})
                data["pageDigest"]=digest({k:v for k,v in data.items() if k!="pageDigest"})
            result={"toolName":name,"ok":True,"auditStatus":"recorded","data":data}
            if operation=="analysis":forged.append(result)
            return result
        with patch("ai_assistant.transport.catalog",return_value=self.tools), \
                patch("ai_assistant.provider.turn",side_effect=replies) as provider, \
                patch("ai_assistant.transport.execute_tool",side_effect=execute) as transport:
            # Two actual persisted provider requests and two tool checkpoints.
            for _ in range(4):
                tick=workflows.agent_tick(job_id=job.id)
                self.assertEqual(tick["status"],"checkpoint",tick)
            self.assertEqual((provider.call_count,transport.call_count),(2,2))
            self.assertEqual(len(original_values),1)
            saved=m.AiAgentToolResults.objects.get(tool_dispatch__job_id=job.id,tool_dispatch__tool_name=integrated.TABLE_TOOL)
            self.assertEqual(json.loads(saved.result_json),forged[0])
            self.assertEqual(saved.result_digest,digest(saved.result_json))
            saved_bytes=(saved.result_json,saved.result_digest)
            providers_before=list(m.AiAgentProviderResults.objects.filter(dispatch__job_id=job.id).order_by("dispatch_id").values_list(
                "dispatch_id","response_json","response_digest"))
            self.assertEqual(len(providers_before),2)
            self.assertTrue(all(digest(raw)==sha for _,raw,sha in providers_before))
            provider.reset_mock();transport.reset_mock()
            failed=workflows.agent_tick(job_id=job.id)
            self.assertEqual((failed["status"],failed["errorCode"]),("failed","integrated_read_incomplete"))
            provider.assert_not_called();transport.assert_not_called()
            # The failed task cannot silently retry the known dispatch.
            self.assertEqual(workflows.agent_tick(job_id=job.id)["status"],"idle")
            provider.assert_not_called();transport.assert_not_called()
        saved.refresh_from_db();job.refresh_from_db()
        self.assertEqual((saved.result_json,saved.result_digest),saved_bytes)
        self.assertEqual(job.status,"failed")
        self.assertEqual(m.AiAgentProviderDispatches.objects.filter(job_id=job.id,state="succeeded").count(),2)
        self.assertEqual(m.AiAgentToolDispatches.objects.filter(job_id=job.id,state="succeeded").count(),2)
        self.assertFalse(m.AiAgentToolDispatches.objects.filter(job_id=job.id,state="unknown").exists())
        self.assertEqual(providers_before,list(m.AiAgentProviderResults.objects.filter(dispatch__job_id=job.id).order_by("dispatch_id").values_list(
            "dispatch_id","response_json","response_digest")))

    def test_rehashed_mapped_analysis_is_rejected_before_next_provider_without_losing_receipt(self):
        self.corrupt_analysis("mapped")

    def test_rehashed_native_analysis_is_rejected_before_next_provider_without_losing_receipt(self):
        self.corrupt_analysis("native")
