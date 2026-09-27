"""Local cache mechanics and real owning admission reuse; no PG run here."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from django import test as djtest

from . import business_screening_permission as service, business_screening_admission as admission
from . import business_screening_tools as tools, transport
from . import test_business_screening_admission as fixtures
from .business_sealed import Reader
from .policy import AiError


class PermissionPureTests(unittest.TestCase):
    """Only cache orchestration is mocked; permits are the actual private type."""
    def setUp(self):
        service.clear()
        self.addCleanup(service.clear)
        self.actor=SimpleNamespace(email="owner@example.invalid",scope=None)
        self.report=SimpleNamespace(id="report",snapshot_json="{}",workflow=SimpleNamespace(status="queued"))
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.real_bound=service._bound
        self.stack.enter_context(patch.object(service,"connection",SimpleNamespace(in_atomic_block=False)))
        self.bound=self.stack.enter_context(patch.object(service,"_bound",side_effect=lambda report,principal:report))
        self.revalidate=self.stack.enter_context(patch.object(admission,"revalidate",side_effect=lambda prepared,principal:admission._proof(prepared)))
        self.catalog=self.stack.enter_context(patch.object(transport,"catalog",return_value=fixtures.catalog()))
        self.catalog_check=self.stack.enter_context(patch.object(admission,"_catalog",side_effect=lambda entries,flow:entries))
        self.prepare=self.stack.enter_context(patch.object(admission,"prepare",side_effect=self.make))

    def make(self,report,principal):
        return admission.PreparedAdmission(admission._TOKEN,"{}",{}, {"reportId":report.id,"ownerEmail":principal.email})

    def test_hit_returns_same_internal_object_and_rechecks_catalog_without_prepare(self):
        value=service.get(self.report,self.actor)
        self.assertIs(service.get(self.report,self.actor),value)
        self.assertEqual(self.prepare.call_count,1)
        self.catalog.assert_called_once();self.catalog_check.assert_called_once()
        self.assertEqual(self.revalidate.call_count,2)
        self.assertEqual(service._stats()["entries"],1)
        for supplied in ({"reportId":"report"},{"event_type":"screening_ready"},"{}"):
            with self.assertRaises(AiError):service.check(supplied,self.actor)

    def test_owner_keys_never_reuse_and_mutated_private_payload_drops_entry(self):
        first=service.get(self.report,self.actor)
        other=SimpleNamespace(email="other@example.invalid",scope=None)
        self.assertIsNot(service.get(self.report,other),first)
        self.assertEqual(self.prepare.call_count,2)
        object.__setattr__(first,"_proof_json","{}")
        with self.assertRaises(AiError):service.get(self.report,self.actor)
        self.assertEqual(service._stats()["entries"],1)

    def test_stale_guidance_model_catalog_or_failed_prepare_never_auto_replace(self):
        for stale in ("guidance changed","model changed","catalog changed"):
            service.clear();self.prepare.reset_mock();service.get(self.report,self.actor)
            with patch.object(admission,"revalidate",side_effect=AiError(stale)),self.assertRaises(AiError):
                service.get(self.report,self.actor)
            self.assertEqual(self.prepare.call_count,1);self.assertEqual(service._stats()["entries"],0)
        with patch.object(admission,"prepare",side_effect=AiError("failed")),self.assertRaises(AiError):service.get(self.report,self.actor)
        self.assertEqual(service._stats(),{"entries":0,"canonicalBytes":0,"pending":0})
        service.get(self.report,self.actor)
        with patch.object(transport,"catalog",side_effect=AiError("catalog changed")),self.assertRaises(AiError):service.get(self.report,self.actor)
        self.assertEqual(service._stats()["entries"],0)

    def test_outer_transaction_refused_before_authority_or_network(self):
        with patch.object(service,"connection",SimpleNamespace(in_atomic_block=True)),self.assertRaises(AiError):service.get(self.report,self.actor)
        self.bound.assert_not_called();self.catalog.assert_not_called();self.prepare.assert_not_called()

    def test_lru_entry_and_canonical_byte_budgets_evict_oldest_and_clear_reprepares(self):
        for i in range(65):service.get(SimpleNamespace(id=f"report-{i}",snapshot_json="{}",workflow=self.report.workflow),self.actor)
        self.assertEqual(service._stats()["entries"],64)
        with service._lock:self.assertNotIn((self.actor.email,"report-0"),service._cache)
        service.clear()
        charge=service._charge((self.actor.email,self.report.id),self.make(self.report,self.actor))
        with patch.object(service,"MAX_BYTES",charge*2+80):
            for i in range(4):service.get(SimpleNamespace(id=f"report-{i}",snapshot_json="{}",workflow=self.report.workflow),self.actor)
            self.assertLessEqual(service._stats()["canonicalBytes"],service.MAX_BYTES)
            self.assertLessEqual(service._stats()["entries"],2)
        before=self.prepare.call_count;service.clear();service.get(self.report,self.actor)
        self.assertEqual(self.prepare.call_count,before+1)

    def test_same_key_single_flight_parallel_success_and_reentry_denial(self):
        entered,release=threading.Event(),threading.Event()
        def load(report,principal):
            entered.set();self.assertTrue(release.wait(3))
            return self.make(report,principal)
        with patch.object(admission,"prepare",side_effect=load) as prepare,ThreadPoolExecutor(max_workers=2) as pool:
            owner=pool.submit(service.get,self.report,self.actor);self.assertTrue(entered.wait(3))
            waiter=pool.submit(service.get,self.report,self.actor);release.set()
            self.assertIs(owner.result(3),waiter.result(3));self.assertEqual(prepare.call_count,1)
        service.clear()
        with patch.object(admission,"prepare",side_effect=lambda r,p:service.get(r,p)),self.assertRaises(AiError):service.get(self.report,self.actor)
        self.assertEqual(service._stats()["pending"],0)

    def test_parallel_distinct_key_limit_and_oversize_never_grow_cache(self):
        entered,release=threading.Event(),threading.Event()
        def load(report,principal):
            entered.set();self.assertTrue(release.wait(3));return self.make(report,principal)
        with (patch.object(service,"MAX_ENTRIES",1),patch.object(admission,"prepare",side_effect=load),
                ThreadPoolExecutor(max_workers=1) as pool):
            owner=pool.submit(service.get,self.report,self.actor);self.assertTrue(entered.wait(3))
            different=SimpleNamespace(id="second",snapshot_json="{}",workflow=self.report.workflow)
            with self.assertRaises(AiError) as error:service.get(different,self.actor)
            self.assertEqual(error.exception.status,503)
            self.assertEqual(service._stats()["pending"],1)
            release.set();owner.result(3)
        service.clear()
        with patch.object(service,"MAX_BYTES",1),self.assertRaises(AiError) as error:service.get(self.report,self.actor)
        self.assertEqual(error.exception.status,413)
        self.assertEqual(service._stats(),{"entries":0,"canonicalBytes":0,"pending":0})

    def test_timeout_clear_and_failed_owner_wake_waiters_without_late_cache(self):
        for outcome in ("timeout","clear","failed"):
            service.clear();entered,release=threading.Event(),threading.Event()
            def load(report,principal):
                entered.set();self.assertTrue(release.wait(3))
                if outcome=="failed":raise AiError("owner failed")
                return self.make(report,principal)
            with patch.object(admission,"prepare",side_effect=load),ThreadPoolExecutor(max_workers=2) as pool:
                owner=pool.submit(service.get,self.report,self.actor);self.assertTrue(entered.wait(3))
                if outcome=="timeout":
                    with patch.object(service,"WAIT_SECONDS",0.01),self.assertRaises(AiError) as error:service.get(self.report,self.actor)
                    self.assertEqual(error.exception.status,503);release.set();owner.result(3)
                else:
                    with service._lock:flight=next(iter(service._pending.values()))
                    waiting=threading.Event();original=flight.event.wait
                    def wait(seconds):
                        waiting.set();return original(seconds)
                    with patch.object(flight.event,"wait",side_effect=wait):
                        waiter=pool.submit(service.get,self.report,self.actor);self.assertTrue(waiting.wait(3))
                        if outcome=="clear":service.clear()
                        release.set()
                        with self.assertRaises(AiError):owner.result(3)
                        with self.assertRaises(AiError):waiter.result(3)
                    self.assertEqual(service._stats()["entries"],0)
            self.assertEqual(service._stats()["pending"],0)

    def test_only_queued_running_states_and_no_public_event_restoration(self):
        for status in ("paused","cancelled","failed","waiting_review","completed"):
            actual=SimpleNamespace(workflow=SimpleNamespace(status=status))
            with patch.object(service.runtime,"bound",return_value=(actual,None,None,None,None,None)),self.assertRaises(AiError):
                self.real_bound(self.report,self.actor)
        for status in ("queued","running"):
            actual=SimpleNamespace(workflow=SimpleNamespace(status=status,cancel_requested=1))
            with patch.object(service.runtime,"bound",return_value=(actual,None,None,None,None,None)),self.assertRaises(AiError):
                self.real_bound(self.report,self.actor)


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningPermissionTests(djtest.TransactionTestCase):
    user=fixtures.ScreeningAdmissionTests.user
    call=fixtures.ScreeningAdmissionTests.call
    collect_body=fixtures.ScreeningAdmissionTests.collect_body
    bundle=fixtures.ScreeningAdmissionTests.bundle
    input_for=fixtures.ScreeningAdmissionTests.input_for
    insert=fixtures.ScreeningAdmissionTests.insert
    seed=fixtures.ScreeningAdmissionTests.seed
    screening_bundle=fixtures.ScreeningAdmissionTests.screening_bundle
    insert_screening=fixtures.ScreeningAdmissionTests.insert_screening
    ready=fixtures.ScreeningAdmissionTests.ready

    def setUp(self):
        fixtures.ScreeningAdmissionTests.setUp(self)
        service.clear();self.addCleanup(service.clear)

    def test_actual_budgeted_cache_hit_no_fact_resolution_or_measurement(self):
        report=self.ready(mapped=True,budget=True)
        with patch.object(transport,"catalog",return_value=fixtures.catalog()) as catalog:
            prepared=service.get(report,self.admin)
            with (patch.object(admission,"prepare",side_effect=AssertionError("no prepare hit")),
                    patch.object(tools,"prepare_for_report",side_effect=AssertionError("no pages prepare hit")),
                    patch.object(admission.preflight,"measure",side_effect=AssertionError("no measure hit")),
                    patch.object(Reader,"pages",side_effect=AssertionError("no facts hit"))):
                self.assertIs(service.get(report,self.admin),prepared)
                self.assertEqual(service.check(prepared,self.admin),prepared.proof)
            self.assertEqual(catalog.call_count,2)

    def test_clear_restarts_real_preparation_and_other_owner_is_denied(self):
        report=self.ready()
        with patch.object(transport,"catalog",return_value=fixtures.catalog()):
            original=service.get(report,self.admin)
            service.clear();restarted=service.get(report,self.admin)
            self.assertIsNot(original,restarted);self.assertEqual(original.proof,restarted.proof)
            other=self.user("permit-other@example.invalid","admin",None)
            with self.assertRaises(AiError):service.get(report,other)
            self.assertEqual(service._stats()["entries"],1)
