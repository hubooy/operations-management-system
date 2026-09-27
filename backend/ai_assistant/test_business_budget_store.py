"""Fixed budget references with real sealed readers and immutable bindings."""
from copy import deepcopy
from dataclasses import replace
import json
from unittest.mock import patch
from urllib.parse import urlencode

from django.db import connection
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from netshop.analysis import read_page, validate_request
from netshop.models import NetshopRow
from business_analysis import budget_reference as contract
from business_analysis.evidence_v2 import workflow_reference
from business_analysis.test_budget import fixture
from . import business_budget, business_budget_store as store, business_evidence as evidence, business_reports, models as m, workflows
from . import tests as fixtures
from .test_business_evidence import versioned_netshop_facts
from .policy import AiError, canonical, digest, mutation, uid


def seed_fixed_report(principal, *, report_id="budget-store-report"):
    """Upgrade/recovery fixture. Caller initializes admin/model and test authority.

Uses actual netshop reader pages and real collect/seal/resolve. The generic dry
workflow is an inert restoration fixture, not an admitted budget execution.
Returns (report, PreparedBudget), and performs no paid or remote business call.
"""
    shop = "预算合成"+digest(report_id)[:12]
    with versioned_netshop_facts():
        for i in range(2):
            NetshopRow.objects.create(source_row_key=f"{report_id}-{i}", source_row_hash=digest([report_id,i]),
                first_import_batch_id="fixture", last_import_batch_id="fixture", source_row_number=i+1,
                source="jd_promotion", dataset="ad", platform="京东", shop_name=shop, business_date="2026-08-01",
                sku_id=f"S{i}", spu_id="P1", spend_cents=3000, net_transaction_amount_cents=15000, clicks=300, impressions=3000, net_orders=30,
                metrics_json={"spendCents":3000,"netTransactionAmountCents":15000,"clicks":300,"impressions":3000,"netOrders":30}, raw_json={})
    source = {"key":"ads","domain":"netshop","query":{"platform":"京东","shop":shop,"dataset":"promotion",
        "startDate":"2026-08-01","endDate":"2026-08-01","window":"current"}}
    request = {"schemaVersion":"business-analysis-request-v1","question":"合成固定预算验证",
        "requestedDimensions":["sku"],"requestedWindows":["current"]}
    run_id = evidence.create({"schemaVersion":"business-evidence-v2","clientRequestId":"evidence-"+digest(report_id),
        "sources":[source],"collectionMode":"bulk","analysisRequest":request}, principal)["item"]["id"]
    tools = [fixtures.CATALOG[0], {**fixtures.CATALOG[0],"name":"get_business_source_page"}]
    def execute(name, args, actor, **kwargs):
        values = {k:v for k,v in args.items() if k != "domain"}
        data = {"dataCutoffDate":"2026-08-01"} if name == "get_data_freshness" else read_page(*validate_request(QueryDict(urlencode(values))))
        return {"ok":True,"toolName":name,"auditStatus":"recorded","data":data}
    with patch("ai_assistant.transport.catalog",return_value=tools), patch("ai_assistant.transport.execute_tool",side_effect=execute):
        evidence.collect(run_id,{"sourceKey":"ads","expectedVersion":1},principal,"budget-store-seed")
    evidence.finish(run_id,{"expectedVersion":2,"action":"seal"},principal)
    row = evidence.get_run(run_id,principal)
    targets = evidence.analysis_table(run_id,{"sourceKey":"ads","dimension":"sku"},principal)["rows"]
    plan, _ = fixture()
    for target, actual in zip(plan["targets"],targets): target.update(rowId=actual["id"],rowIndex=actual["rowIndex"])
    prepared = store.prepare(row,plan,principal,report_id)
    binding = prepared.binding
    reference = workflow_reference([source],run_id=row.id,evidence_version=row.version,sealed_digest=binding["sealedDigest"],
        question=request["question"],analysis_request=request)
    with mutation(principal):
        saved = store.insert(prepared,principal)
        flow = workflows.create({"clientRequestId":"flow-"+digest(report_id),"name":"合成固定预算恢复",
            "graph":business_reports.graph_v2(),"input":{**reference,"reportId":report_id,"budgetRef":prepared.reference},"dryRun":True},principal,True)
        snapshot = {"schemaVersion":"business-report-v1","executionProfile":contract.PROFILE,"evidenceProtocol":"reference-v2",
            "reportId":report_id,"budgetRef":prepared.reference,**{k:v for k,v in reference.items() if k != "inputMode"}}
        report = m.AiReportRun.objects.create(id=report_id,owner_email=principal.email.lower(),scope_json=canonical(principal.scope),
            client_request_id="report-"+digest(report_id),request_digest=digest([plan,binding]),workflow_id=flow["item"]["id"],
            snapshot_json=canonical(snapshot),budget_plan=saved)
    return report,prepared


@override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class BusinessBudgetStoreTests(TestCase):
    user = fixtures.AiDomainTests.user
    call = fixtures.AiDomainTests.call

    def setUp(self):
        fixtures.AiDomainTests.setUp(self)
        self.admin = self.user("budget-fixed@example.invalid","admin",None)
        self.report,self.prepared = seed_fixed_report(self.admin)
        self.evidence = evidence.get_run(self.prepared.binding["evidenceRunId"],self.admin)

    def test_load_prepare_and_pages_use_real_seal_without_source_model_or_writes(self):
        with patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            loaded = store.load(self.report,self.admin)
            prepared = store.prepare(self.evidence,self.prepared.plan,self.admin,self.report.id)
            first = store.page(self.report,self.admin,limit=1)
            tail = store.page(self.report,self.admin,offset=1)
        model.assert_not_called(); remote.assert_not_called()
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT","UPDATE","DELETE")) for q in queries))
        self.assertEqual(loaded.binding,self.prepared.binding)
        self.assertEqual(prepared.result,self.prepared.result)
        self.assertEqual(loaded.result["allocation"]["allocatedCents"],9000)
        self.assertEqual(first["pagination"]["nextOffset"],1)
        self.assertIsNone(tail["pagination"]["nextOffset"])
        self.assertEqual(business_budget.for_report(self.report,self.admin),loaded.result)
        self.assertEqual(loaded.reference,self.prepared.reference)
        changed = loaded.plan; changed["targets"].clear()
        self.assertEqual(len(loaded.plan["targets"]),2)

    def test_insert_requires_full_mutation_and_rejects_prepared_alias_changes(self):
        prepared = store.prepare(self.evidence,self.prepared.plan,self.admin,"second-report")
        with self.assertRaises(AiError): store.insert(prepared,self.admin)
        bad = replace(prepared,result_json=canonical({**prepared.result,"allocation":{}}))
        with mutation(self.admin), self.assertRaises(AiError): store.insert(bad,self.admin)
        self.assertEqual(m.AiBusinessBudgetPlan.objects.count(),1)

    def test_report_transaction_failure_keeps_no_parameter_or_workflow(self):
        prepared = store.prepare(self.evidence,self.prepared.plan,self.admin,"failed-report")
        count = m.AiWorkflowRuns.objects.count()
        with self.assertRaisesMessage(AiError,"synthetic audit failure"):
            with mutation(self.admin):
                store.insert(prepared,self.admin)
                raise AiError("synthetic audit failure")
        self.assertEqual(m.AiBusinessBudgetPlan.objects.count(),1)
        self.assertEqual(m.AiWorkflowRuns.objects.count(),count)

    def test_permissions_stale_evidence_and_changed_targets_fail(self):
        other = self.user("budget-fixed-other@example.invalid","admin",None)
        for actor in (self.viewer,other):
            with self.assertRaises(AiError): store.load(self.report,actor)
            with self.assertRaises(AiError): store.prepare(self.evidence,self.prepared.plan,actor,"other-report")
        stale = deepcopy(self.evidence); stale.version += 1
        with self.assertRaises(AiError): store.prepare(stale,self.prepared.plan,self.admin,"stale-report")
        for field,value in (("rowId","0"*64),("rowIndex",True),("sourceKey","missing"),("dimension","shop")):
            plan = self.prepared.plan; plan["targets"][0][field] = value
            with self.assertRaises(AiError): store.prepare(self.evidence,plan,self.admin,"bad-target")

    def test_quota_counts_all_parameter_and_binding_bytes_and_history(self):
        prepared = store.prepare(self.evidence,self.prepared.plan,self.admin,"quota-report")
        used = len(prepared.plan_json.encode())+len(prepared.binding_json.encode())
        existing = len(self.prepared.plan_json.encode())+len(self.prepared.binding_json.encode())
        for constant,value in (("OWNER_ROWS",1),("GLOBAL_ROWS",1),("OWNER_BYTES",existing+used-1),("GLOBAL_BYTES",existing+used-1)):
            with patch.object(contract,constant,value),mutation(self.admin),self.assertRaises(AiError) as caught:
                store.insert(prepared,self.admin)
            self.assertEqual(caught.exception.status,429)
        self.assertEqual(m.AiBusinessBudgetPlan.objects.count(),1)

    def test_new_profile_requires_internal_prepared_and_legacy_profile_with_ref_refused(self):
        with patch("ai_assistant.provider.turn") as model,patch("ai_assistant.transport.catalog") as catalog:
            with self.assertRaises(AiError):
                workflows.create({"clientRequestId":"closed-profile","name":"关闭","graph":business_reports.graph_v2(),"dryRun":True},
                    self.admin,True,execution_profile=contract.PROFILE)
            self.assertTrue(business_reports.is_v2_snapshot(json.loads(self.report.snapshot_json)))
            with self.assertRaises(AiError): business_reports.is_v2_snapshot({"executionProfile":business_reports.V2_PROFILE,
                "evidenceProtocol":"reference-v2","budgetRef":self.prepared.reference})
        model.assert_not_called(); catalog.assert_not_called()
        altered = deepcopy(self.report); snapshot = json.loads(altered.snapshot_json)
        snapshot["executionProfile"] = business_reports.V2_PROFILE; altered.snapshot_json = canonical(snapshot)
        with patch.object(m.AiReportRun.objects,"filter") as reports:
            reports.return_value.select_related.return_value.first.return_value = altered
            with self.assertRaises(AiError): business_budget.for_report(altered,self.admin)
        snapshot["executionProfile"] = contract.PROFILE; snapshot.pop("budgetRef")
        altered.snapshot_json = canonical(snapshot); altered.budget_plan_id = None
        with patch.object(m.AiReportRun.objects,"filter") as reports:
            reports.return_value.select_related.return_value.first.return_value = altered
            with self.assertRaises(AiError): business_budget.for_report(altered,self.admin)

    def test_missing_or_tampered_parameter_never_returns_no_budget(self):
        row = m.AiBusinessBudgetPlan.objects.get(pk=self.prepared.id)
        variants = [None]
        for field,value in (("plan_digest","0"*64),("binding_digest","0"*64),("evidence_version",row.evidence_version+1)):
            changed = deepcopy(row); setattr(changed,field,value); variants.append(changed)
        changed = deepcopy(row); binding = json.loads(changed.binding_json); binding["catalogDigest"] = "0"*64
        changed.binding_json = canonical(binding); changed.binding_digest = digest(binding); variants.append(changed)
        for changed in variants:
            with patch.object(m.AiBusinessBudgetPlan.objects,"filter") as plans:
                plans.return_value.first.return_value = changed
                with self.assertRaises(AiError): business_budget.for_report(self.report,self.admin)

    def test_preview_recalculates_without_changing_fixed_parameters(self):
        before = m.AiBusinessBudgetPlan.objects.get(pk=self.prepared.id)
        plan = self.prepared.plan; plan["totalBudgetCents"] = 15000
        result = business_budget.preview(self.report.id,{"budgetPlan":plan},self.admin)
        self.assertTrue(result["previewOnly"])
        self.assertEqual(result["originalPlanDigest"],self.prepared.binding["planDigest"])
        self.assertNotEqual(result["budget"]["planDigest"],self.prepared.binding["planDigest"])
        before.refresh_from_db(); self.assertEqual(before.plan_json,self.prepared.plan_json)
