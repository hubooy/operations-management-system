"""Explicit content scope tests; synthetic database tests are run by the owner."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from django import test as djtest

from . import business_integrated as contract, business_integrated_tools as tools
from . import business_integrated_receipts as receipts, business_reports as reports
from . import business_budget_store as budgets, models as m
from . import test_business_integrated_receipts as receipt_fixtures
from . import test_business_integrated_reports as report_fixtures
from .business_integrated_content_reuse import ContentReuse
from .policy import AiError, canonical, digest


class IntegratedContentReusePureTests(unittest.TestCase):
    def setUp(self):
        self.report,self.prepared,self.evidence,self.sources,_,_=receipt_fixtures.trusted_fixture(target_count=2)
        self.report.budget_plan_id=self.prepared.budget.id
        self.evidence.state_json=canonical({"sealedDigest":"c"*64})
        self.principal=SimpleNamespace(email=self.report.owner_email,role="admin",scope=None)
        self.allowed=True
        def bound(report, principal, **kwargs):
            if not self.allowed or principal.role != "admin" or principal.email != self.report.owner_email:
                raise AiError("synthetic revoked", "access_denied",403)
            return report,json.loads(report.snapshot_json),self.prepared.reference,self.evidence,self.sources
        self.bound=patch.object(contract,"bound",side_effect=bound).start()
        self.fixed=patch.object(budgets,"binding_for_report",side_effect=lambda *a: budgets.BudgetBinding(
            self.prepared.budget.id,self.prepared.budget.plan_json,self.prepared.budget.binding_json)).start()
        self.loaded=patch.object(budgets,"load",return_value=self.prepared.budget).start()
        self.calculated=patch.object(tools,"_analysis_from",side_effect=lambda p,e,s,a,u:{"arguments":a,"rows":[]}).start()
        self.addCleanup(patch.stopall)
        self.args={"reportId":self.report.id,"runId":self.evidence.id,"mode":"mapped",
            "pairKey":self.prepared.plan["pairs"][0]["pairKey"],"dimension":"sku","offset":0}

    def analysis(self, reuse, **changes):
        return tools.analysis_from(self.prepared,self.evidence,self.sources,{**self.args,**changes},self.principal,_reuse=reuse)

    def test_only_completed_results_reused_and_prepared_rebuilt(self):
        with ContentReuse(self.report,self.principal) as reuse:
            one=tools.prepare_for_report(self.report,self.principal,resolve_budget=True,_reuse=reuse)[1]
            two=tools.prepare_for_report(self.report,self.principal,resolve_budget=True,_reuse=reuse)[1]
            self.assertIsNot(one.budget,two.budget)
            self.assertEqual(one,two)
            self.assertEqual(self.loaded.call_count,1)
            self.assertEqual(self.fixed.call_count,2)
            first=self.analysis(reuse); first["rows"].append("caller mutation")
            self.assertEqual(self.analysis(reuse)["rows"],[])
            self.analysis(reuse,offset=20)
            self.assertEqual(self.calculated.call_count,2)
        self.assertEqual(reuse._memo.stats()["entries"],0)
        with ContentReuse(self.report,self.principal) as fresh:
            self.analysis(fresh); fresh.budget(self.report,self.principal)
        self.assertEqual((self.calculated.call_count,self.loaded.call_count),(3,2))

    def test_bound_changes_reject_hits_and_exit_discards_return(self):
        with self.assertRaises(AiError):
            with ContentReuse(self.report,self.principal) as reuse:
                self.analysis(reuse)
                self.allowed=False
                self.analysis(reuse)
        self.assertEqual(self.calculated.call_count,1)
        self.allowed=True
        with self.assertRaises(AiError):
            with ContentReuse(self.report,self.principal) as reuse:
                self.analysis(reuse)
                self.allowed=False
        self.assertEqual(reuse._memo.stats()["entries"],0)

    def test_no_cross_owner_report_scope_or_prepared_alias(self):
        with ContentReuse(self.report,self.principal) as reuse:
            self.analysis(reuse)
            other=deepcopy(self.report); other.id="other-report"
            with self.assertRaises(AiError): reuse.budget(other,self.principal)
            other_principal=SimpleNamespace(email="other@example.invalid",role="admin",scope=None)
            with self.assertRaises(AiError): reuse.budget(self.report,other_principal)
            altered=deepcopy(self.sources); altered[0]["query"]["shop"]="other"
            with self.assertRaises(AiError):
                tools.analysis_from(self.prepared,self.evidence,altered,self.args,self.principal,_reuse=reuse)
        self.loaded.assert_not_called()
        self.assertEqual(self.calculated.call_count,1)

    def test_unmodified_none_path_does_not_construct_scope(self):
        with patch("ai_assistant.business_integrated_content_reuse.CompletedReuse",side_effect=AssertionError("scope")):
            tools.prepare_for_report(self.report,self.principal,resolve_budget=True)
            tools.analysis_from(self.prepared,self.evidence,self.sources,self.args,self.principal)
        self.assertEqual((self.loaded.call_count,self.calculated.call_count),(1,1))


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class IntegratedContentReusePostgresTests(djtest.TransactionTestCase):
    # Alias the module, not its TestCase, to avoid discovering its expensive E2E
    # cases twice. Only provider/transport boundaries in drive() are mocked.
    user=report_fixtures.BusinessIntegratedReportTests.user
    call=report_fixtures.BusinessIntegratedReportTests.call
    collect_body=report_fixtures.BusinessIntegratedReportTests.collect_body
    setUp=report_fixtures.BusinessIntegratedReportTests.setUp
    create=report_fixtures.BusinessIntegratedReportTests.create
    diagnosis=report_fixtures.BusinessIntegratedReportTests.diagnosis
    drive=report_fixtures.BusinessIntegratedReportTests.drive

    def test_real_five_ledgers_same_content_bytes_and_per_request_counts(self):
        report,_=self.create(budget=True)
        flow,roles,peak=self.drive(report,budget=True)
        self.assertEqual(flow.status,"waiting_review")
        self.assertEqual((roles,peak),(contract.NODES,3))
        actual_analysis,actual_load,actual_proof=tools._analysis_from,budgets.load,receipts.validate_complete

        def measured(function):
            proofs={}
            def proof(job,*args,**kwargs):
                result=actual_proof(job,*args,**kwargs)
                proofs[job.workflow_node_key]=canonical(result)
                return result
            with patch.object(tools,"_analysis_from",wraps=actual_analysis) as analysis, \
                    patch.object(budgets,"load",wraps=actual_load) as load, \
                    patch.object(receipts,"validate_complete",side_effect=proof), \
                    patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as network:
                value=function(report,self.admin)
                model.assert_not_called(); network.assert_not_called()
            mapped=sum(call.args[3]["mode"]=="mapped" for call in analysis.call_args_list)
            native=sum(call.args[3]["mode"]=="native" for call in analysis.call_args_list)
            return canonical(value),proofs,(mapped,native,load.call_count)

        before=measured(reports._content)
        after=measured(reports.content)
        self.assertEqual(before[2],(3,2,6))
        self.assertEqual(after[2],(1,1,1))
        self.assertEqual(before[:2],after[:2])
        self.assertEqual(set(after[1]),contract.NODES)
        fresh=measured(reports.content)
        self.assertEqual(fresh,after)

        # Every job still needs its own stored result even when the expected
        # page is already memoized by an earlier sibling. Corrupt only the
        # read projection, with a recomputed receipt digest; immutable rows
        # are never edited or their triggers bypassed.
        jobs=list(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id))
        manager=m.AiAgentToolResults.objects
        original_filter=manager.filter
        for job in jobs:
            dispatch=m.AiAgentToolDispatches.objects.get(job=job,tool_name=contract.TABLE_TOOL)
            for mode in ("missing","tampered"):
                def filtered(*args,**kwargs):
                    query=original_filter(*args,**kwargs)
                    if kwargs.get("tool_dispatch_id") != dispatch.id: return query
                    projected=MagicMock()
                    def first():
                        if mode=="missing": return None
                        row=query.annotate(result_bytes=receipts.Func(receipts.F("result_json"),function="OCTET_LENGTH",
                            output_field=receipts.BigIntegerField()),result_text=receipts.Substr("result_json",1,receipts.MAX_RESULT_BYTES+1)).values(
                                "tool_dispatch_id","result_text","result_bytes","result_digest").first()
                        value=json.loads(row["result_text"])
                        value["data"]["pageDigest"]="0"*64
                        raw=canonical(value)
                        return {**row,"result_text":raw,"result_bytes":len(raw.encode()),"result_digest":digest(raw)}
                    projected.annotate.return_value.values.return_value.first.side_effect=first
                    return projected
                with self.subTest(job=job.workflow_node_key,mode=mode),patch.object(manager,"filter",side_effect=filtered):
                    with self.assertRaises(AiError): reports.content(report,self.admin)

        # Revoke after _content computed a complete result: __exit__ must
        # still reject it. This models a last-moment live authorization loss.
        from access_control.models import AppUser
        original_content=reports._content
        def revoke_after(*args,**kwargs):
            value=original_content(*args,**kwargs)
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return value
        with patch.object(reports,"_content",side_effect=revoke_after):
            with self.assertRaises(AiError): reports.content(report,self.admin)
