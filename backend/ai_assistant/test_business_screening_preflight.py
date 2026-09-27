"""Actual pure scanner/packages/provider frames; no database or model calls."""
from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from business_analysis import screening_package as package, screening_storage as storage, budget, budget_reference
from business_analysis import test_screening_package as fixtures, test_budget as budget_fixtures
from . import business_screening_runtime_contract as contract, business_screening_preflight as preflight, provider
from .policy import AiError, canonical, digest


def fixture(*, rich=True, rows=1, with_budget=False, already_proposed=False):
    _, kwargs, value = fixtures.fixture(rows=rows,rich=rich)
    binding = value["authority"]["binding"]
    workflow = {"inputMode":"reference-v2", "question":"核对完整来源和经营异常", **{k:binding[k] for k in preflight.SEAL_KEYS}}
    if binding["mappingPlanDigest"]:
        workflow["mappingRef"] = {"schemaVersion":"business-mapping-reference-v1", "planDigest":binding["mappingPlanDigest"],
            "pairCount":len({d["mapping"]["pairKey"] for d in kwargs["selection_plan"]["descriptors"] if d["mapping"]})}
    budget_pages = []
    if with_budget:
        plan, baselines = budget_fixtures.fixture()
        fixed = budget_reference.make_binding(plan,report_id=binding["reportId"],owner_email=binding["ownerEmail"],scope=binding["scope"],
            evidence_run_id=binding["evidenceRunId"],evidence_version=binding["evidenceVersion"],evidence_plan_digest=binding["evidencePlanDigest"],
            catalog_digest=binding["catalogDigest"],sealed_digest=binding["sealedDigest"],analysis_request=kwargs["selection_plan"]["analysisRequest"])
        budget_ref = budget_reference.make_reference("budget-synthetic",fixed)
        binding["budgetRef"] = workflow["budgetRef"] = budget_ref
        result = {**budget.calculate(plan,baselines), **{k:binding[k] for k in ("evidenceRunId","evidenceVersion","evidencePlanDigest")}}
        offset = 0
        while offset is not None:
            page = budget_reference.page(result,fixed,budget_ref=budget_ref,report_id=binding["reportId"],offset=offset,limit=1)
            budget_pages.append(page); offset = page["pagination"]["nextOffset"]
    if already_proposed:
        binding["executionProfile"] = contract.PROFILE
        workflow.update(reportId=binding["reportId"],screeningIntent=contract.intent("screening-synthetic",value["planDigest"]))
    binding["workflowInputDigest"] = digest(workflow)
    value["bindingDigest"] = digest(binding)
    value["resultDigest"] = digest({k:v for k,v in value.items() if k != "resultDigest"})
    bundle = storage.materialize(value)
    packages = package.build(bundle,**kwargs)
    role_pages = {role:list(result.pages()) for role,result in packages.items()}
    manifest = storage.validate(bundle)
    reference = {"schemaVersion":contract.REFERENCE_SCHEMA,"workflowInput":workflow,
        "screeningReference":{"schemaVersion":"business-screening-storage-reference-v1","id":"screening-synthetic",
            "reportId":binding["reportId"],"bindingDigest":value["bindingDigest"],"selectionPlanDigest":value["planDigest"],
            "resultDigest":value["resultDigest"],"contentRootDigest":manifest["contentRootDigest"],"manifestDigest":storage.raw_digest(bundle["manifestJson"])},
        "packageDigests":{role:result.package_digest for role,result in packages.items()}}
    return role_pages,reference,budget_pages


def entries():
    return [{"name":name,"description":"固定筛查完整数据读取", "parameters":{"type":"object","properties":{"runId":{"type":"string"},
        "reportId":{"type":"string"},"screeningId":{"type":"string"},"role":{"type":"string","enum":list(package.ROLES)},
        "offset":{"type":"integer"}},"required":["runId","reportId","screeningId"],"additionalProperties":False},
        "execution":{"maxCallsPerRequest":8,"maxResultCharacters":40000}} for name in sorted(contract.TOOLS)]


def model():
    return SimpleNamespace(protocol="openai_compatible",max_total_tool_calls=40,max_tool_rounds=20,max_tokens=2048,
        generation_options_json=canonical({"contextWindowTokens":128000}))


class ScreeningPreflightTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.small = fixture()
        cls.budgeted = fixture(with_budget=True)
        cls.typical55 = fixture(rich=False,rows=2)

    def run_preview(self, data=None, **overrides):
        pages, reference, budgets = data or self.small
        return preflight.measure(pages,reference,model=overrides.pop("model",model()),entries=overrides.pop("entries",entries()),
            budget_pages=budgets,**overrides)

    def test_fixed_graph_intent_and_unregistered_protocol(self):
        value = contract.graph(True)
        nodes = {n["key"]:n for n in value["nodes"]}
        self.assertEqual(list(nodes),[*package.ROLES,"human_review"])
        self.assertEqual(nodes["independent_review"]["dependsOn"],list(package.ROLES[:3]))
        self.assertEqual(nodes["report"]["dependsOn"],list(package.ROLES[:4]))
        self.assertEqual(nodes["human_review"]["dependsOn"],["report"])
        self.assertEqual(set(contract.intent("screening-id","a"*64)),contract.INTENT_FIELDS)
        for role in package.ROLES:
            text = nodes[role]["instruction"]
            self.assertIn(contract.PACKAGE_TOOL,text);self.assertIn("candidateId",text)
            self.assertIn("omittedRows",text);self.assertIn(str(contract.OUTPUT_LIMITS[role])+"个UTF-8字节",text)
            self.assertNotIn("get_business_integrated",text)
            if role != "independent_review":
                self.assertIn(canonical(contract.ACTION_SHAPE),text)
                self.assertIn(canonical(contract.FINDING_SHAPE),text)
        with self.assertRaises(ValueError):contract.graph(1)
        value["nodes"].clear();self.assertEqual(len(contract.graph()["nodes"]),6)

    def test_real_frames_both_protocols_complete_small_fit_without_dispatch(self):
        original = provider.tool_frames
        calls = []
        def frames(protocol, requests, results):
            calls.append((protocol.protocol,requests,results))
            return original(protocol,requests,results)
        with patch.object(provider,"tool_frames",frames),patch.object(provider,"turn") as paid:
            result = self.run_preview()
        paid.assert_not_called();self.assertTrue(result["fits"],result["nodes"])
        self.assertTrue(result["previewOnly"]);self.assertFalse(result["runtimeAdmissionGranted"]);self.assertFalse(result["modelDispatched"])
        self.assertEqual({p for p,_,_ in calls},{"openai_compatible","anthropic"})
        self.assertTrue(all(len(c[0]["id"])==160 for _,c,_ in calls))
        for _,requests,_ in calls:
            self.assertEqual(requests[0]["arguments"]["screeningId"],self.small[1]["screeningReference"]["id"])
            if requests[0]["name"]==contract.PACKAGE_TOOL:self.assertIn(requests[0]["arguments"]["role"],package.ROLES)
        self.assertFalse(any(c[0]["name"]==contract.TABLE_TOOL for _,c,_ in calls))
        for node in result["nodes"]:
            self.assertLessEqual(node["inputBytes"],24*1024)
            self.assertEqual(node["analysisPagesRequired"],0);self.assertFalse(node["optionalAnalysisReserved"])
            if node["nodeKey"] != "human_review":
                self.assertEqual(node["packagePages"],len(self.small[0][node["nodeKey"]]))
                self.assertEqual(node["remainingToolCalls"],40-node["requiredToolCalls"])
                self.assertEqual(node["remainingTranscriptBytes"],192*1024-node["transcriptBytes"])

    def test_old_input_is_not_mutated_and_exact_new_intent_remains_unchanged(self):
        original = deepcopy(self.small)
        result = self.run_preview()
        self.assertEqual(self.small,original);self.assertFalse(result["actualInputAlreadyProposed"])
        self.assertEqual(result["sourceInputDigest"],digest(self.small[1]["workflowInput"]))
        self.assertNotEqual(result["sourceInputDigest"],result["proposedInputDigest"])
        proposed = fixture(already_proposed=True)
        result = self.run_preview(proposed)
        self.assertTrue(result["actualInputAlreadyProposed"])
        self.assertEqual(result["sourceInputDigest"],result["proposedInputDigest"])
        result["proposedWorkflowInput"]["screeningIntent"]["id"]="other"
        self.assertEqual(self.run_preview(proposed)["proposedWorkflowInput"],proposed[1]["workflowInput"])

    def test_complete_55_partitions_are_measured_not_assumed_to_fit(self):
        pages = self.typical55[0]
        decoded = package.decode_pages(pages["report"])
        self.assertEqual(len([r for r in decoded["coverage"] if r["kind"]=="partition"]),55)
        before = digest(pages)
        result = self.run_preview(self.typical55)
        self.assertEqual(digest(pages),before)
        self.assertEqual(len(result["nodes"]),6)
        self.assertTrue(all(n["packagePages"]==len(pages[n["nodeKey"]]) for n in result["nodes"] if n["nodeKey"] in package.ROLES))
        self.assertEqual(result["fits"],all(n["fits"] for n in result["nodes"]))

    def test_large_full_candidates_fail_without_clipping(self):
        wide = fixture(rich=False,rows=32)
        cfg=model();cfg.generation_options_json=canonical({"contextWindowTokens":2000000})
        before=digest(wide[0]); result=self.run_preview(wide,model=cfg)
        self.assertFalse(result["fits"])
        self.assertTrue(any("transcript_limit_exceeded" in n["failures"] for n in result["nodes"]))
        self.assertEqual(before,digest(wide[0]));self.assertFalse(result["runtimeAdmissionGranted"])

    def test_budget_complete_pages_bound_and_only_required_roles_counted(self):
        result = self.run_preview(self.budgeted)
        for node in result["nodes"]:
            self.assertEqual(node["budgetPages"],len(self.budgeted[2]) if node["nodeKey"] in contract.BUDGET_NODES else 0)
        for change in ("missing","truncated","unexpected","cross_report","bad_hash","wrong_ref","float_offset","reordered"):
            pages,ref,budgets=deepcopy(self.budgeted)
            if change=="missing":budgets=[]
            elif change=="truncated":budgets=budgets[:-1]
            elif change=="unexpected":pages,ref,_=deepcopy(self.small)
            elif change=="cross_report":budgets[0]["reportId"]="other"
            elif change=="bad_hash":budgets[0]["rows"][0]["rowIndex"]=99
            elif change=="wrong_ref":budgets[0]["budgetRef"]["planDigest"]="a"*64
            elif change=="float_offset":
                budgets[0]["pagination"]["offset"]=0.0
                budgets[0]["pageDigest"]=digest({k:v for k,v in budgets[0].items() if k!="pageDigest"})
            else:budgets.reverse()
            with self.subTest(change=change),self.assertRaises(AiError):self.run_preview((pages,ref,budgets))

    def test_five_role_missing_tail_duplicate_swapped_and_rehashed_forgery_reject(self):
        for change in ("role","tail","duplicate","swapped","digest","reference","input","manifest","schema"):
            pages,ref,budgets=deepcopy(self.small)
            if change=="role":pages.pop("commerce")
            elif change=="tail":pages["report"].pop()
            elif change=="duplicate":pages["commerce"].append(deepcopy(pages["commerce"][-1]))
            elif change=="swapped":pages["commerce"]=pages["promotion"]
            elif change=="digest":ref["packageDigests"]["commerce"]="0"*64
            elif change=="reference":ref["screeningReference"]["reportId"]="other"
            elif change=="input":ref["workflowInput"]["question"]="changed"
            elif change=="manifest":ref["screeningReference"]["manifestDigest"]="0"*64
            else:ref["schemaVersion"]="unknown"
            with self.subTest(change=change),self.assertRaises(AiError):self.run_preview((pages,ref,budgets))

    def test_call_round_tool_entry_and_token_limits(self):
        for field in ("max_total_tool_calls","max_tool_rounds"):
            cfg=model();setattr(cfg,field,1)
            result=self.run_preview(self.budgeted,model=cfg)
            self.assertFalse(result["fits"])
            self.assertTrue(any("tool_limit_exceeded" in n["failures"] for n in result["nodes"]))
        for replacement in (True,0,9,8.0):
            tools=entries();tools[0]["execution"]["maxCallsPerRequest"]=replacement
            with self.subTest(replacement=replacement),self.assertRaises(AiError):self.run_preview(entries=tools)
        tools=entries();tools[0]["name"]="get_business_integrated_directory_v1"
        with self.assertRaises(AiError):self.run_preview(entries=tools)
        cfg=model();cfg.generation_options_json=canonical({"contextWindowTokens":8192})
        result=self.run_preview(model=cfg)
        self.assertFalse(result["fits"]);self.assertTrue(any("ai_context_budget_exceeded" in n["failures"] for n in result["nodes"]))

    def test_fit_context_cannot_drop_or_mutate_frames(self):
        for fitted,info in (([],{"droppedMessages":1}),([],{"droppedMessages":0})):
            with patch.object(preflight,"fit_context",return_value=(fitted,info)):
                result=self.run_preview()
            self.assertFalse(result["fits"])
            self.assertTrue(all("context_would_drop_messages" in n["failures"] for n in result["nodes"] if n["nodeKey"] in package.ROLES))

    def test_worst_dependency_double_escape_and_human_cap(self):
        measured=self.run_preview()
        maximum=next(n["inputBytes"] for n in measured["nodes"] if n["nodeKey"]=="report")
        for character in ('"','\\','<','中','\n'):
            data={"workflowInput":measured["proposedWorkflowInput"],"dependencies":{role:{"answer":character*(contract.OUTPUT_LIMITS[role]//len(character.encode()))}
                for role in package.ROLES[:4]}}
            self.assertLessEqual(len(canonical(data).encode()),maximum)
        with patch.dict(contract.OUTPUT_LIMITS,{"report":15000}):result=self.run_preview()
        human=next(n for n in result["nodes"] if n["nodeKey"]=="human_review")
        self.assertIn("node_input_limit_exceeded",human["failures"])
        self.assertEqual(human["transcriptBytes"],0)

    def test_caller_capacity_containers_and_invalid_models_are_bounded(self):
        for cfg in (None,SimpleNamespace(protocol="unknown")):
            with self.assertRaises(AiError):self.run_preview(model=cfg)
        cfg=model();cfg.max_tool_rounds=True
        with self.assertRaises(AiError):self.run_preview(model=cfg)
        with self.assertRaises(AiError):self.run_preview(guidance="中"*11000)
        pages,ref,budgets=deepcopy(self.small);pages["report"][0]["oversize"]="x"*38000
        with self.assertRaises(AiError):self.run_preview((pages,ref,budgets))
        for invalid in (None,[None,None,None],[{},[],{}]):
            with self.subTest(catalog=invalid),self.assertRaises(AiError):self.run_preview(entries=invalid)
        pages,ref,budgets=deepcopy(self.budgeted)
        budgets[0]["allocation"]={}
        budgets[0]["pageDigest"]=digest({k:v for k,v in budgets[0].items() if k!="pageDigest"})
        with self.assertRaises(AiError):self.run_preview((pages,ref,budgets))
