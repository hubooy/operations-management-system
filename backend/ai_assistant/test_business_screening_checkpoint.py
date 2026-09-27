"""Cooperative interruption is internal, lossless and never partial authority."""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from django import test as djtest
from business_analysis import partitioned, identity_partitioned, mapped_results, results, diagnostic_screening
from business_analysis.contracts import AnalysisContractError, canonical
from business_analysis.test_results import fixture as native_fixture
from business_analysis.test_partitioned import paged, proof_for
from business_analysis.test_identity_partitioned import pages as identity_pages
from business_analysis.test_mapped_results import fixture as mapped_fixture
from business_analysis import test_diagnostic_screening as pure_fixture
from . import business_diagnostic_screening as service, business_mapped_analysis, business_identity
from . import test_business_screening_runtime_guard as fixture
from .policy import AiError, mutation


@contextmanager
def scratch_paths():
    paths=[]
    def create(*args,**kwargs):
        value=TemporaryDirectory(*args,**kwargs)
        paths.append(Path(value.name))
        return value
    with ExitStack() as stack:
        for module in (partitioned,identity_partitioned,mapped_results):
            stack.enter_context(patch.object(module,"TemporaryDirectory",create))
        yield paths


class ScreeningCheckpointPureTests(unittest.TestCase):
    def test_native_default_and_callback_have_identical_complete_bytes(self):
        page,_=native_fixture(rows=[(str(i),i,1,10) for i in range(251)])
        proof=proof_for(page)
        def render(**kwargs):
            with results.stream_table(paged(page),"sku",proof,**kwargs) as (header,rows):
                return canonical({**header,"rows":list(rows)})
        expected=render(); events=[]
        self.assertEqual(render(checkpoint=lambda event:events.append(event)),expected)
        self.assertEqual(render(checkpoint=lambda event:False),expected)
        self.assertEqual(render(checkpoint=None),expected)
        self.assertTrue({"native_aggregate","native_verify","native_scan","native_rows"}<={e["stage"] for e in events})

    def test_native_all_python_stages_preserve_exception_and_delete_scratch(self):
        page,_=native_fixture(rows=[(str(i),i,1,10) for i in range(251)])
        proof=proof_for(page)
        for stage in ("native_aggregate","native_verify","native_scan","native_rows"):
            error=ValueError("cancel "+stage)
            def checkpoint(event):
                if event["stage"]==stage: raise error
            with self.subTest(stage=stage),scratch_paths() as paths:
                with self.assertRaises(ValueError) as caught:
                    with results.stream_table(paged(page),"sku",proof,checkpoint=checkpoint) as (_,rows):list(rows)
                self.assertIs(caught.exception,error)
                self.assertTrue(paths)
                self.assertTrue(all(not p.exists() for p in paths))

    def test_identity_all_python_stages_preserve_exception_and_delete_scratch(self):
        for stage in ("identity_source_page","identity_verify","identity_rows","identity_scan"):
            error=TypeError("cancel "+stage)
            def checkpoint(event):
                if event["stage"]==stage: raise error
            with self.subTest(stage=stage),scratch_paths() as paths:
                with self.assertRaises(TypeError) as caught:
                    with identity_partitioned.reconcile_products(identity_pages("sales",101),identity_pages("master",101),checkpoint=checkpoint) as table:
                        list(table.scan())
                self.assertIs(caught.exception,error)
                self.assertTrue(paths)
                self.assertTrue(all(not p.exists() for p in paths))

    def test_mapped_default_bytes_and_cancellation_close_both_sqlite_layers(self):
        source,_=mapped_fixture()
        def render(**kwargs):
            with mapped_results.mapped_table(source,"sku",**kwargs) as table:
                return canonical({"header":table.header(),"rows":list(table.scan())})
        self.assertEqual(render(),render(checkpoint=lambda event:False))
        for stage in ("mapped_aggregate","mapped_scan"):
            error=ValueError("cancel "+stage)
            def checkpoint(event):
                if event["stage"]==stage: raise error
            with self.subTest(stage=stage),scratch_paths() as paths:
                with self.assertRaises(ValueError) as caught:render(checkpoint=checkpoint)
                self.assertIs(caught.exception,error)
                self.assertTrue(all(not p.exists() for p in paths))

    def test_sqlite_progress_restores_original_exception_in_all_three_contexts(self):
        query="WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000) SELECT sum(x) FROM n"
        source,_=mapped_fixture()
        for name in ("native","identity","mapped"):
            armed=False; events=[]; error=TimeoutError("synthetic deadline "+name)
            def checkpoint(event):
                events.append(event)
                if armed and event.get("phase")=="sqlite": raise error
            @contextmanager
            def opened():
                if name=="native":
                    with partitioned.PartitionedGroups(checkpoint=checkpoint) as table:yield table.db
                elif name=="identity":
                    with identity_partitioned.reconcile_products(identity_pages("sales",1),identity_pages("master",1),checkpoint=checkpoint) as table:yield table._db
                else:
                    with mapped_results.mapped_table(source,"sku",checkpoint=checkpoint) as table:yield table._db
            with self.subTest(layer=name),scratch_paths() as paths:
                with self.assertRaises(TimeoutError) as caught:
                    with opened() as db:
                        armed=True
                        db.execute(query).fetchone()
                self.assertIs(caught.exception,error)
                self.assertTrue(any(e.get("phase")=="sqlite" for e in events))
                self.assertTrue(all(not p.exists() for p in paths))

    def test_consumer_cannot_suppress_sqlite_interrupted_and_exit_successfully(self):
        query="WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000) SELECT sum(x) FROM n"
        source,_=mapped_fixture()
        for name in ("native","identity","mapped"):
            armed=False; error=ValueError("original suppressed sqlite cancellation")
            def checkpoint(event):
                if armed and event.get("phase")=="sqlite":raise error
            opener = (partitioned.PartitionedGroups(checkpoint=checkpoint) if name=="native" else
                identity_partitioned.reconcile_products(identity_pages("sales",1),identity_pages("master",1),checkpoint=checkpoint) if name=="identity" else
                mapped_results.mapped_table(source,"sku",checkpoint=checkpoint))
            with self.subTest(layer=name),scratch_paths() as paths,self.assertRaises(ValueError) as caught:
                with opener as table:
                    armed=True
                    try:(table.db if name=="native" else table._db).execute(query).fetchone()
                    except sqlite3.DatabaseError:pass
            self.assertIs(caught.exception,error)
            self.assertTrue(all(not p.exists() for p in paths))

    def test_full_scanner_callbacks_preserve_bytes_and_stop_before_partial_success(self):
        descriptor=pure_fixture.descriptor(rules=["erp_refund_present"])
        closed=[]
        @contextmanager
        def opened(value):
            try:yield pure_fixture.header(value,201),(pure_fixture.row(value,i) for i in range(201))
            finally:closed.append(True)
        expected=diagnostic_screening.prepare(pure_fixture.binding(),[descriptor],opened)
        events=[]
        actual=diagnostic_screening.prepare(pure_fixture.binding(),[descriptor],opened,checkpoint=lambda e:events.append(e))
        self.assertEqual(canonical(actual),canonical(expected))
        self.assertEqual([e["rowOffset"] for e in events if e["stage"]=="screen_candidates"],[0,100,200])
        for stage,phase in (("screen_descriptor","before"),("screen_descriptor","after"),("screen_table","before"),
                ("screen_candidates",None),("screen_partition",None),("screen_table","after")):
            error=ValueError("exact caller error")
            def checkpoint(event):
                if event["stage"]==stage and (phase is None or event.get("phase")==phase):raise error
            with self.subTest(stage=stage,phase=phase),self.assertRaises(ValueError) as caught:
                diagnostic_screening.prepare(pure_fixture.binding(),[descriptor],opened,checkpoint=checkpoint)
            self.assertIs(caught.exception,error)
        self.assertTrue(closed)

    def test_suppressed_callback_and_truthy_return_cannot_certify_partial_input(self):
        descriptor=pure_fixture.descriptor(rules=["erp_refund_present"])
        error=ValueError("suppressed cancel")
        @contextmanager
        def hostile(value):
            try:yield pure_fixture.header(value,1),iter([pure_fixture.row(value,0)])
            except ValueError:pass
        def checkpoint(event):
            if event["stage"]=="screen_candidates":raise error
        with self.assertRaises(ValueError) as caught:
            diagnostic_screening.prepare(pure_fixture.binding(),[descriptor],hostile,checkpoint=checkpoint)
        self.assertIs(caught.exception,error)
        page,proof=native_fixture();page["items"][-1]["metrics"]["spendCents"]=999
        with self.assertRaises(AnalysisContractError):
            with results.stream_table([page],"sku",proof,checkpoint=lambda event:True) as (_,rows):list(rows)

    def test_partial_stream_cancel_and_retained_exception_do_not_keep_windows_handles(self):
        page,proof=native_fixture()
        marker=ValueError("cancel after first row"); iterator=None
        with scratch_paths() as paths:
            with self.assertRaises(ValueError) as caught:
                with results.stream_table([page],"sku",proof,checkpoint=lambda event:None) as (_,iterator):
                    next(iterator)
                    raise marker
            self.assertIs(caught.exception,marker)
            self.assertTrue(all(not p.exists() for p in paths))
        self.assertEqual(list(iterator),[])


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningCheckpointOwningTests(djtest.TransactionTestCase):
    user=fixture.ScreeningRuntimeGuardTests.user
    call=fixture.ScreeningRuntimeGuardTests.call
    collect_body=fixture.ScreeningRuntimeGuardTests.collect_body
    bundle=fixture.ScreeningRuntimeGuardTests.bundle
    input_for=fixture.ScreeningRuntimeGuardTests.input_for
    insert=fixture.ScreeningRuntimeGuardTests.insert
    seed=fixture.ScreeningRuntimeGuardTests.seed
    setUp=fixture.ScreeningRuntimeGuardTests.setUp
    screening_bundle=fixture.ScreeningRuntimeGuardTests.screening_bundle
    insert_screening=fixture.ScreeningRuntimeGuardTests.insert_screening

    def make_report(self,mapped=True):
        bundle=self.screening_bundle(mapped=mapped)
        with mutation(self.admin):return self.insert_screening(bundle)

    def test_real_owning_native_and_mapped_default_bytes_and_reader_hook(self):
        report=self.make_report()
        expected=service.prepare_for_report(report.id,self.admin)
        events=[]
        actual=service.prepare_for_report(report.id,self.admin,checkpoint=lambda event:events.append(event))
        self.assertEqual(actual._binding_json,expected._binding_json)
        self.assertEqual(actual._result_json,expected._result_json)
        stages={event["stage"] for event in events}
        self.assertTrue({"preparing","screen_descriptor","native_aggregate","identity_source_page","mapped_aggregate","screen_complete"}<=stages)
        keys={event["sourceKey"] for event in events if event["stage"]=="preparing"}
        self.assertEqual(keys,{"ads","sales","master"})

    def test_real_reader_callback_exception_is_not_translated_and_all_temps_removed(self):
        report=self.make_report()
        for stage in ("preparing","native_aggregate","identity_source_page","mapped_aggregate","screen_candidates","screen_complete"):
            error=ValueError("exact owning cancel "+stage)
            def checkpoint(event):
                if event["stage"]==stage:raise error
            with self.subTest(stage=stage),scratch_paths() as paths:
                with self.assertRaises(ValueError) as caught:service.prepare_for_report(report.id,self.admin,checkpoint=checkpoint)
                self.assertIs(caught.exception,error)
                self.assertTrue(all(not path.exists() for path in paths))

    def test_final_callback_cannot_bypass_actual_permission_or_mapped_context_fence(self):
        from access_control.models import AppUser
        report=self.make_report()
        status=AppUser.objects.get(email=self.admin.email).status
        for stage in ("screen_complete","mapped_aggregate"):
            AppUser.objects.filter(email=self.admin.email).update(status=status)
            def checkpoint(event):
                if event["stage"]==stage:AppUser.objects.filter(email=self.admin.email).update(status="disabled")
                return True
            with self.subTest(stage=stage),scratch_paths() as paths:
                with self.assertRaises(AiError) as error:service.prepare_for_report(report.id,self.admin,checkpoint=checkpoint)
                self.assertEqual(error.exception.status,403)
                self.assertTrue(all(not path.exists() for path in paths))
