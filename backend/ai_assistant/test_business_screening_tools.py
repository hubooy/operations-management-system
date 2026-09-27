"""Internal protocol tests over real sealed and published synthetic evidence.

Pure envelope tests are independent of PostgreSQL. Integration tests are run by
the isolated root harness; they never dispatch a model or register a profile.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from business_analysis import budget_reference, screening_package
from . import business_screening_tools as tools, business_screening_runtime as runtime
from . import business_diagnostic_screening as screening, business_screening_store as store
from . import business_screening_packages as packages, business_budget_store
from . import test_business_screening_runtime_guard as fixtures
from .business_sealed import Reader
from .policy import AiError, canonical, digest, mutation


class ScreeningToolEnvelopeTests(unittest.TestCase):
    def test_role_sequence_exact_types_and_independent_raw_hashes(self):
        ready = object.__new__(packages.PreparedPackages)
        entries = tuple((role,screening_package.Package({"role":role},{},[]),"") for role in screening_package.ROLES)
        entries = tuple((role,page,page.package_digest) for role,page,_ in entries)
        object.__setattr__(ready,"_packages",entries)
        expected = tools._package_digests(ready)
        for changed in (list(entries),entries[:-1],tuple(reversed(entries)),(entries[1],*entries[1:]),
                ((entries[0][0],entries[0][1],True),*entries[1:]),((entries[0][0],{},entries[0][2]),*entries[1:])):
            object.__setattr__(ready,"_packages",changed)
            with self.assertRaises(AiError):tools._call(tools._package_digests,ready)
        object.__setattr__(ready,"_packages",entries)
        object.__setattr__(entries[0][1],"_raw","{}")
        with self.assertRaises(AiError):tools._call(tools._package_digests,ready)
        replacement = screening_package.Package({"role":screening_package.ROLES[0]},{},[["forged"]])
        object.__setattr__(ready,"_packages",((entries[0][0],replacement,replacement.package_digest),*entries[1:]))
        self.assertNotEqual(tools._package_digests(ready),expected)

    def test_integer_and_query_offsets_are_not_coerced(self):
        for value in (True,False,0,1.0,None,"01","-1","1e0"," 1","250001",[]):
            with self.subTest(value=value),self.assertRaises(AiError): tools._offset(value,250000)
        self.assertEqual(tools._offset("250000",250000),250000)
        for value in (True,False,1.0,None,"0",-1,250001):
            with self.assertRaises(AiError): tools._integer_offset(value,250000)
        self.assertEqual(tools._integer_offset(0,250000),0)

    def test_complete_utf8_prefix_and_empty_table_never_drop_columns(self):
        prepared = SimpleNamespace(reference={"reportId":"synthetic","screeningIntent":{"id":"ready"}})
        rows = [{"rowIndex":n,"id":str(n),"text":"中\\\""*1700,"metrics":{"amount":n}} for n in range(23)]
        result,offset = [],0
        while offset is not None:
            page = tools._table_page(prepared,"native",{"sourceKey":"source","dimension":"sku"},
                {"rows":rows[offset:offset+20],"total":len(rows),"pageDigest":"old","columns":["all"]},offset)
            self.assertLessEqual(len(canonical(page).encode()),38000)
            self.assertEqual(page["pageDigest"],digest({k:v for k,v in page.items() if k!="pageDigest"}))
            self.assertEqual(page["table"]["pageDigest"],digest({k:v for k,v in page["table"].items() if k!="pageDigest"}))
            self.assertEqual(page["table"]["columns"],["all"])
            result.extend(page["table"]["rows"])
            offset = page["table"]["pagination"]["nextOffset"]
        self.assertEqual(result,rows)
        self.assertEqual(tools._table_page(prepared,"native",{}, {"rows":[],"total":0},0)["table"]["rows"],[])
        with patch.object(tools,"MAX_RESPONSE_BYTES",100),self.assertRaises(AiError) as error:
            tools._table_page(prepared,"native",{}, {"rows":rows[:1],"total":1},0)
        self.assertEqual(error.exception.status,413)

    def test_no_json_preparation_and_invalid_get_shape_fails_before_loading(self):
        for value in ({},"{}",SimpleNamespace()):
            with self.assertRaises(AiError): tools._checked(value,None)
        with self.assertRaises(AiError): tools.Prepared(None,None,None,None,None,None,None,None,None)
        with patch.object(tools,"prepare_for_report",side_effect=AssertionError("must reject before prepare")):
            for operation,params in (("directory",{}),("package",{}),("package",{"runId":"r","screeningId":"s","role":"commerce","jobId":"j"}),
                    ("budget",{"runId":"r","screeningId":"s","role":"commerce"}),
                    ("analysis",{"runId":"r","screeningId":"s","mode":"native","dimension":"sku","offset":"01"})):
                with self.subTest(operation=operation,params=params),self.assertRaises(AiError):tools.read("r",operation,params,None)


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningToolsTests(djtest.TransactionTestCase):
    user = fixtures.ScreeningRuntimeGuardTests.user
    call = fixtures.ScreeningRuntimeGuardTests.call
    collect_body = fixtures.ScreeningRuntimeGuardTests.collect_body
    bundle = fixtures.ScreeningRuntimeGuardTests.bundle
    input_for = fixtures.ScreeningRuntimeGuardTests.input_for
    insert = fixtures.ScreeningRuntimeGuardTests.insert
    seed = fixtures.ScreeningRuntimeGuardTests.seed
    setUp = fixtures.ScreeningRuntimeGuardTests.setUp
    screening_bundle = fixtures.ScreeningRuntimeGuardTests.screening_bundle
    insert_screening = fixtures.ScreeningRuntimeGuardTests.insert_screening

    def seed_ready(self, *, mapped=False, budget=False, publish=True):
        bundle = self.screening_bundle(mapped=mapped,budget=budget)
        with mutation(self.admin): report = self.insert_screening(bundle)
        if publish: store.publish(screening.prepare_for_report(report.id,self.admin),self.admin)
        return report

    def args(self, prepared, **changes):
        return {"runId":prepared.reference["evidenceRunId"],"reportId":prepared.report_id,
            "screeningId":prepared.reference["screeningIntent"]["id"],"mode":"native","dimension":"sku","sourceKey":"ads",**changes}

    def read(self, report, operation, **params):
        snapshot = json.loads(report.snapshot_json)
        return tools.read(report.id,operation,{"runId":self.parent.id,"screeningId":snapshot["screeningIntent"]["id"],**params},self.admin)

    def verified(self, page):
        self.assertLessEqual(len(canonical(page).encode()),38000)
        self.assertEqual(page["pageDigest"],digest({k:v for k,v in page.items() if k!="pageDigest"}))
        return page

    def test_real_four_mapping_budget_combinations_and_preflight_budget_envelope(self):
        for mapped in (False,True):
            for budget in (False,True):
                with self.subTest(mapped=mapped,budget=budget):
                    report = self.seed_ready(mapped=mapped,budget=budget)
                    prepared = tools.prepare_for_report(report,self.admin,resolve_budget=budget)
                    page = self.verified(tools.package_from(prepared,"commerce",self.admin))
                    self.assertEqual(page,packages.page(prepared.packages,"commerce",self.admin))
                    native = self.verified(tools.analysis_from(prepared,self.args(prepared),self.admin))
                    self.assertEqual(sum(row["metrics"]["spendCents"]["value"] for row in native["table"]["rows"]),6000)
                    self.assertEqual(native["reference"],json.loads(report.workflow.input_json))
                    self.assertEqual(native["schemaVersion"],"business-screening-analysis-v1")
                    if mapped:
                        args = self.args(prepared,mode="mapped",pairKey=prepared.snapshot["mappingPlan"]["pairs"][0]["pairKey"])
                        args.pop("sourceKey")
                        table = self.verified(tools.analysis_from(prepared,args,self.admin))["table"]
                        self.assertEqual(sum(row["metrics"]["netSalesCents"]["value"] for row in table["rows"]),120000)
                    else:
                        args = self.args(prepared,mode="mapped",pairKey="a"*64);args.pop("sourceKey")
                        with self.assertRaises(AiError):tools.analysis_from(prepared,args,self.admin)
                    if budget:
                        actual = self.verified(tools.budget_from(prepared,self.admin))
                        page = budget_reference.page(prepared.budget.result,prepared.budget.binding,
                            budget_ref=prepared.budget.reference,report_id=report.id,offset=0,limit=20)
                        expected = {"schemaVersion":tools.contract.BUDGET_PAGE_SCHEMA,"reference":prepared.reference,"budget":page}
                        expected["pageDigest"] = digest(expected)
                        self.assertEqual(actual,expected)
                    else:
                        with self.assertRaises(AiError):tools.budget_from(prepared,self.admin)

    def test_ready_is_required_and_other_report_intent_cannot_be_reused(self):
        report = self.seed_ready(publish=False)
        with self.assertRaises(AiError) as error:tools.prepare_for_report(report,self.admin)
        self.assertEqual(error.exception.status,409)
        with self.assertRaises(AiError):tools.prepare_for_report(self.report,self.admin)
        ready = self.seed_ready()
        prepared = tools.prepare_for_report(ready,self.admin)
        for changes in ({"runId":"another"},{"reportId":report.id},{"screeningId":json.loads(report.snapshot_json)["screeningIntent"]["id"]}):
            with self.assertRaises(AiError):tools.analysis_from(prepared,self.args(prepared,**changes),self.admin)
        for key in ("runId","screeningId"):
            with self.assertRaises(AiError):self.read(ready,"package",role="commerce",**{key:"another"})

    def test_package_preparation_and_reused_enumeration_never_scan_facts_or_resolve_budget(self):
        report = self.seed_ready(mapped=True,budget=True)
        with patch.object(Reader,"pages",side_effect=AssertionError("package cannot scan facts")),patch.object(
                business_budget_store,"load",side_effect=AssertionError("package cannot calculate budget")),patch(
                "ai_assistant.provider.turn") as model,patch("ai_assistant.transport.execute_tool") as remote,CaptureQueriesContext(connection) as queries:
            prepared = tools.prepare_for_report(report,self.admin)
            with patch.object(packages,"prepare",side_effect=AssertionError("reuse must not reconstruct packages")),patch.object(
                    store,"_all",side_effect=AssertionError("reuse must not reread all stored pages")):
                for role in screening_package.ROLES:
                    pages,budgets = tools.expected_pages(prepared,role,self.admin)
                    self.assertFalse(budgets)
                    decoded = screening_package.decode_pages(list(pages.values()))
                    self.assertEqual(decoded["role"],role)
        model.assert_not_called();remote.assert_not_called()
        sql = [q["sql"].strip().lower() for q in queries]
        self.assertFalse(any("netshop_rows" in q or "sales_order_lines" in q for q in sql))
        self.assertFalse(any(q.startswith(("insert ","update ","delete ")) for q in sql))

    def test_exact_modes_roles_offsets_and_current_owner_scope(self):
        report = self.seed_ready(mapped=True)
        prepared = tools.prepare_for_report(report,self.admin)
        for role in (True,None,"admin","Commerce",["commerce"]):
            with self.assertRaises(AiError):tools.package_from(prepared,role,self.admin)
        for changes in ({"mode":"invalid"},{"dimension":[]},{"dimension":"invalid"},{"sourceKey":"missing"},
                {"baselineKey":"missing"},{"pairKey":"a"*64},{"baselinePairKey":"a"*64},
                {"offset":True},{"offset":1.0},{"offset":"0"},{"extra":{}},{"jobId":"another-job"}):
            with self.subTest(changes=changes),self.assertRaises(AiError):tools.analysis_from(prepared,self.args(prepared,**changes),self.admin)
        from sales.auth import Principal
        for actor in (self.viewer,self.user("screen-tools-other@example.invalid","admin",None),Principal(self.admin.email,"scoped","admin",{"shops":["other"]})):
            with self.assertRaises(AiError):tools.prepare_for_report(report,actor)
            with self.assertRaises(AiError):tools.package_from(prepared,"commerce",actor)
        for value in ("01","-1","10000",0,True):
            with self.assertRaises(AiError):self.read(report,"package",role="commerce",offset=value)
        page = self.read(report,"package",role="commerce")
        self.assertEqual(page,tools.package_from(prepared,"commerce",self.admin))

    def test_native_and_mapped_full_stream_exhaustion_and_late_page_failure(self):
        report = self.seed_ready(mapped=True)
        prepared = tools.prepare_for_report(report,self.admin)
        pair = prepared.snapshot["mappingPlan"]["pairs"][0]["pairKey"]
        native = self.args(prepared)
        mapped = self.args(prepared,mode="mapped",pairKey=pair);mapped.pop("sourceKey")
        original = Reader.pages
        calls,completed = [],[]
        def tracked(reader,key,*args,**kwargs):
            calls.append(key)
            yield from original(reader,key,*args,**kwargs)
            completed.append(key)
        for args,expected in ((native,{"ads"}),(mapped,{"sales","master"})):
            calls.clear();completed.clear()
            with patch.object(Reader,"pages",tracked):tools.analysis_from(prepared,args,self.admin)
            self.assertEqual(set(calls),expected)
            self.assertEqual(calls,completed)
        def failed(reader,key,*args,**kwargs):
            yield from original(reader,key,*args,**kwargs)
            raise AiError("late source verification failure","conflict",409)
        for args in (native,mapped):
            with patch.object(Reader,"pages",failed),self.assertRaisesRegex(AiError,"late source"):tools.analysis_from(prepared,args,self.admin)

    def test_real_table_and_budget_complete_prefixes_preserve_original_rows(self):
        report = self.seed_ready(mapped=True,budget=True)
        prepared = tools.prepare_for_report(report,self.admin,resolve_budget=True)
        native = self.args(prepared)
        mapped = self.args(prepared,mode="mapped",pairKey=prepared.snapshot["mappingPlan"]["pairs"][0]["pairKey"]);mapped.pop("sourceKey")
        for args in (native,mapped):
            full = tools.analysis_from(prepared,args,self.admin)
            self.assertGreater(len(full["table"]["rows"]),1)
            prefix = tools._table_page(prepared,args["mode"],full["selector"],{**full["table"],"rows":full["table"]["rows"][:1]},0)
            cap = len(canonical(prefix).encode())
            with patch.object(tools,"MAX_RESPONSE_BYTES",cap):first = self.verified(tools.analysis_from(prepared,args,self.admin))
            self.assertEqual(first,prefix)
            following = tools.analysis_from(prepared,{**args,"offset":1},self.admin)
            self.assertEqual(first["table"]["rows"]+following["table"]["rows"],full["table"]["rows"])
            with patch.object(tools,"MAX_RESPONSE_BYTES",cap-1),self.assertRaises(AiError) as error:tools.analysis_from(prepared,args,self.admin)
            self.assertEqual(error.exception.status,413)
        full = tools.budget_from(prepared,self.admin)
        fixed = prepared.budget
        raw = budget_reference.page(fixed.result,fixed.binding,budget_ref=fixed.reference,report_id=prepared.report_id,offset=0,limit=1)
        prefix = {"schemaVersion":tools.contract.BUDGET_PAGE_SCHEMA,"reference":prepared.reference,"budget":raw}
        prefix["pageDigest"] = digest(prefix)
        cap = len(canonical(prefix).encode())
        with patch.object(tools,"MAX_RESPONSE_BYTES",cap):first = tools.budget_from(prepared,self.admin)
        self.assertEqual(first,prefix)
        following = tools.budget_from(prepared,self.admin,offset=1)
        self.assertEqual(first["budget"]["rows"]+following["budget"]["rows"],full["budget"]["rows"])
        with patch.object(tools,"MAX_RESPONSE_BYTES",cap-1),self.assertRaises(AiError):tools.budget_from(prepared,self.admin)

    def test_late_real_revocation_discards_all_three_tool_results(self):
        from access_control.models import AppUser
        report = self.seed_ready(mapped=True,budget=True)
        prepared = tools.prepare_for_report(report,self.admin,resolve_budget=True)
        original_status = AppUser.objects.get(email=self.admin.email).status
        mapped = self.args(prepared,mode="mapped",pairKey=prepared.snapshot["mappingPlan"]["pairs"][0]["pairKey"])
        mapped.pop("sourceKey")
        for owner,name,invoke in ((packages,"page",lambda:tools.package_from(prepared,"commerce",self.admin)),
                (tools,"_table_page",lambda:tools.analysis_from(prepared,self.args(prepared),self.admin)),
                (tools,"_table_page",lambda:tools.analysis_from(prepared,mapped,self.admin)),
                (budget_reference,"page",lambda:tools.budget_from(prepared,self.admin))):
            original = getattr(owner,name)
            def revoke(*args,**kwargs):
                value = original(*args,**kwargs)
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")
                return value
            AppUser.objects.filter(email=self.admin.email).update(status=original_status)
            with patch.object(owner,name,revoke),self.assertRaises(AiError) as error:invoke()
            self.assertEqual(error.exception.status,403)

    def test_returned_copies_frozen_state_and_private_data_tampering(self):
        report = self.seed_ready(budget=True)
        prepared = tools.prepare_for_report(report,self.admin,resolve_budget=True)
        expected = tools.budget_from(prepared,self.admin)
        prepared.snapshot["question"]="changed";prepared.reference["question"]="changed"
        prepared.sources.clear();prepared.actual.snapshot_json="{}";prepared.evidence.plan_json="{}"
        prepared.budget.result["plan"].clear()
        self.assertEqual(tools.budget_from(prepared,self.admin),expected)
        with self.assertRaises(FrozenInstanceError):prepared._reference_json="{}"
        object.__setattr__(prepared._budget,"result_json","{}")
        with self.assertRaises(AiError):tools.budget_from(prepared,self.admin)
        prepared = tools.prepare_for_report(report,self.admin)
        other = self.seed_ready()
        object.__setattr__(prepared,"_packages",tools.prepare_for_report(other,self.admin).packages)
        with self.assertRaises(AiError):tools.package_from(prepared,"commerce",self.admin)

    def test_expected_pages_budget_requires_explicit_resolution_and_matches_direct_read(self):
        report = self.seed_ready(budget=True)
        prepared = tools.prepare_for_report(report,self.admin,resolve_budget=True)
        pages,budgets = tools.expected_pages(prepared,"promotion",self.admin)
        self.assertEqual(pages[0],self.read(report,"package",role="promotion"))
        self.assertEqual(budgets[0],self.read(report,"budget"))
        self.assertEqual(tools.analysis_from(prepared,self.args(prepared),self.admin),self.read(report,"analysis",mode="native",dimension="sku",sourceKey="ads"))
        not_resolved = tools.prepare_for_report(report,self.admin)
        with self.assertRaises(AiError):tools.budget_from(not_resolved,self.admin)

    def test_same_report_resigned_inner_package_cannot_replace_outer_fixed_digests(self):
        report = self.seed_ready()
        prepared = tools.prepare_for_report(report,self.admin)
        inner = prepared.packages
        original = inner._packages
        role,page,_ = original[0]
        raw = json.loads(page._raw)
        raw["records"][0][1]["query"]["shop"] = "同报告伪造来源"
        replacement = screening_package.Package(raw["header"],raw["directory"],raw["records"])
        object.__setattr__(inner,"_packages",((role,replacement,replacement.package_digest),*original[1:]))
        # The storage header is unchanged and the inner self-reported digest is
        # internally consistent; only the independent outer anchor detects it.
        self.assertNotEqual(replacement.package_digest,page.package_digest)
        with patch.object(packages,"prepare",side_effect=AssertionError("no rebuild")),patch.object(
                store,"_all",side_effect=AssertionError("no persisted page reload")):
            for invoke in (lambda:tools.package_from(prepared,role,self.admin),
                    lambda:tools.expected_pages(prepared,role,self.admin),
                    lambda:tools.analysis_from(prepared,self.args(prepared),self.admin)):
                with self.assertRaises(AiError):invoke()
