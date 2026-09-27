"""Actual frame encoding and model budgets; no database or paid provider."""
from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from business_analysis.evidence_v2 import workflow_reference
from . import business_budget_preflight as preflight, business_reports, provider, workflows
from . import test_business_budget_receipts as fixtures
from .business_budget_receipts import TOOLS, BUDGET_TOOL, DIRECTORY_TOOL, TABLE_TOOL
from .policy import AiError, canonical


class BusinessBudgetPreflightTests(TestCase):
    def setUp(self):
        self.evidence,self.sources,self.prepared,self.directories,self.budgets=fixtures.trusted_fixture(target_count=2,source_count=19)
        self.reference={**workflow_reference(self.sources,run_id=self.evidence.id,evidence_version=3,sealed_digest="c"*64,question="预算"),
            "reportId":"report-fixed","budgetRef":self.prepared.reference}
        self.flow=SimpleNamespace(id="flow-fixed",model_id="model",dry_run=False,input_json=canonical(self.reference))
        self.graph=workflows.validate_graph(business_reports.graph_budget())
        self.entries=[{"name":name,"execution":{"maxCallsPerRequest":8}} for name in TOOLS]
        self.model=SimpleNamespace(protocol="openai_compatible",max_total_tool_calls=12,max_tool_rounds=6,max_tokens=2048,
            generation_options_json=canonical({"contextWindowTokens":128000}))
        self.trusted=patch.object(preflight,"_trusted",return_value=(self.reference,self.sources,self.directories,self.budgets)).start()
        patch.object(workflows,"resolve_model",return_value=self.model).start()
        patch.object(workflows,"execution_guidance",return_value="").start()
        patch.object(provider,"system_prompt",side_effect=lambda model,system:system).start()
        self.addCleanup(patch.stopall)

    def run_preflight(self):
        return preflight.preflight(self.flow,None,self.graph,self.entries,prepared=self.prepared)

    def test_both_protocols_measure_all_nodes_and_required_budget_pages(self):
        for protocol in ("openai_compatible","anthropic"):
            self.model.protocol=protocol
            with patch.object(provider,"turn") as paid:
                result=self.run_preflight()
            paid.assert_not_called()
            self.assertEqual(len(result["nodes"]),6)
            bykey={n["nodeKey"]:n for n in result["nodes"]}
            self.assertEqual(bykey["human_review"]["transcriptBytes"],0)
            self.assertGreater(bykey["human_review"]["inputBytes"],16000)
            self.assertEqual(bykey["commerce"]["budgetPages"],0)
            self.assertEqual(bykey["report"]["budgetPages"],1)
            self.assertTrue(all(n["inputBytes"]<=24*1024 and n["transcriptBytes"]<=192*1024 for n in result["nodes"]))

    def test_original_larger_report_cap_would_block_human_review_upfront(self):
        with patch.dict(business_reports.BUDGET_OUTPUT_LIMITS,{"report":15000}),self.assertRaises(AiError) as caught:
            self.run_preflight()
        self.assertEqual(caught.exception.code,"payload_too_large")

    def test_single_tool_total_calls_rounds_and_catalog_limits(self):
        for field,value in (("max_total_tool_calls",2),("max_tool_rounds",3)):
            old=getattr(self.model,field);setattr(self.model,field,value)
            with self.assertRaises(AiError) as caught:self.run_preflight()
            self.assertEqual(caught.exception.code,"tool_limit_exceeded");setattr(self.model,field,old)
        for tool in self.entries:
            old=tool["execution"]["maxCallsPerRequest"];tool["execution"]["maxCallsPerRequest"]=0
            with self.assertRaises(AiError):self.run_preflight()
            tool["execution"]["maxCallsPerRequest"]=old
        self.entries.pop()
        with self.assertRaises(AiError):self.run_preflight()

    def test_small_context_and_dropped_frames_are_never_accepted(self):
        self.model.generation_options_json=canonical({"contextWindowTokens":8192})
        with self.assertRaises(AiError) as caught:self.run_preflight()
        self.assertEqual(caught.exception.code,"ai_context_budget_exceeded")
        with patch.object(preflight,"fit_context",return_value=([],{"droppedMessages":1,"estimatedInputTokens":1})),self.assertRaises(AiError):
            self.run_preflight()

    def test_hundred_targets_are_not_a_model_context_promise(self):
        row,sources,prepared,directories,budgets=fixtures.trusted_fixture(target_count=100,source_count=19)
        self.prepared=prepared
        self.trusted.return_value=(self.reference,sources,directories,budgets)
        self.model.max_tool_rounds=20;self.model.max_total_tool_calls=40
        self.model.generation_options_json=canonical({"contextWindowTokens":2000000})
        # With large actual page content the full transcript must be measured.
        for page in budgets.values():page["limitations"].append("<\\\""*5000)
        with patch.object(provider,"turn") as paid,self.assertRaises(AiError) as caught:self.run_preflight()
        self.assertEqual(caught.exception.code,"transcript_limit_exceeded");paid.assert_not_called()

    def test_dry_run_still_measures_full_node_inputs_without_model(self):
        self.flow.dry_run=True
        with patch.object(workflows,"resolve_model") as model,patch.object(preflight,"fit_context") as fit:
            result=self.run_preflight()
        model.assert_not_called();fit.assert_not_called();self.assertEqual(len(result["nodes"]),6)
        with patch.dict(business_reports.BUDGET_OUTPUT_LIMITS,{"report":15000}),self.assertRaises(AiError):self.run_preflight()

    def test_analysis_reserve_bounds_utf8_nested_protocol_encoding(self):
        for char in ('"','\\','中','\n','<'):
            unit=len(canonical(char).encode())-2
            data={"x":char*((38000-len(canonical({"x":""}).encode()))//unit)}
            for protocol in ("openai_compatible","anthropic"):
                frames=[]
                preflight._append_tool(frames,SimpleNamespace(protocol=protocol),TABLE_TOOL,
                    {"runId":"r"*160,"sourceKey":"s"*160,"dimension":"shop"},data,1)
                self.assertLess(len(canonical(frames).encode()),preflight.ANALYSIS_TRANSCRIPT_RESERVE)

    def test_dependency_probes_bound_quotes_backslashes_and_html_safe_expansion(self):
        result=self.run_preflight();limit=next(n for n in result["nodes"] if n["nodeKey"]=="report")["inputBytes"]
        for char in ('"','\\','中','<','\n'):
            dependencies={key:{"answer":char*(business_reports.BUDGET_OUTPUT_LIMITS[key]//len(char.encode()))}
                for key in ("commerce","promotion","market_b2b","independent_review")}
            size=len(canonical({"workflowInput":self.reference,"dependencies":dependencies}).encode())
            self.assertLessEqual(size,limit)
