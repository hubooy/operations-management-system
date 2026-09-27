"""Isolated PostgreSQL lease lifecycle tests; never dispatch a real model.

The owning scan, publication, role package and capacity measurement are real.
Only transport catalog delivery and deliberate interruption points are patched.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from threading import Barrier, Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from django import test as djtest
from django.db import connection, connections, close_old_connections
from django.utils import timezone

from . import business_screening_readiness as service
from . import business_screening_permission as permission
from . import business_screening_admission as admission
from . import business_diagnostic_screening as screening, business_screening_store as store
from . import models as m, workflows, provider, transport
from . import test_business_screening_admission as fixtures
from .business_sealed import Reader
from .policy import AiError, canonical, mutation, uid


class ReadinessClockTests(unittest.TestCase):
    def test_deadline_is_exact_and_database_checks_are_throttled_but_forceable(self):
        clock = [10.0]
        with patch.object(service, "time", SimpleNamespace(monotonic=lambda: clock[0])), \
                patch.object(service, "_leased") as leased:
            check = service.Check(object(), object())
            check(); clock[0] += .5; check()
            self.assertEqual(leased.call_count, 1)
            check(force=True)
            self.assertEqual(leased.call_count, 2)
            clock[0] = 189.999; check()
            clock[0] = 190
            with self.assertRaises(AiError) as caught: check(force=True)
            self.assertEqual(caught.exception.code, "screening_prepare_deadline")
            self.assertEqual(leased.call_count, 3)


class _Fixtures:
    user = fixtures.ScreeningAdmissionTests.user
    call = fixtures.ScreeningAdmissionTests.call
    collect_body = fixtures.ScreeningAdmissionTests.collect_body
    bundle = fixtures.ScreeningAdmissionTests.bundle
    input_for = fixtures.ScreeningAdmissionTests.input_for
    insert = fixtures.ScreeningAdmissionTests.insert
    seed = fixtures.ScreeningAdmissionTests.seed
    screening_bundle = fixtures.ScreeningAdmissionTests.screening_bundle
    insert_screening = fixtures.ScreeningAdmissionTests.insert_screening
    ready = fixtures.ScreeningAdmissionTests.ready

    def setUp(self):
        fixtures.ScreeningAdmissionTests.setUp(self)
        permission.clear()
        self.addCleanup(permission.clear)

    def flow(self, report):
        return m.AiWorkflowRuns.objects.get(pk=report.workflow_id)

    def published(self, report):
        key = json.loads(report.snapshot_json)["screeningIntent"]["id"]
        return m.AiBusinessScreeningRun.objects.filter(pk=key).exists()

    def cancel_flow(self, report):
        row = self.flow(report)
        workflows.control(row.id, {"expectedVersion": row.version}, self.admin, "cancel", workflow=True)

    def expire(self, report):
        with mutation(self.admin):
            m.AiWorkflowRuns.objects.filter(pk=report.workflow_id).update(
                lease_expires_at=timezone.now()-timedelta(seconds=1),
                next_run_at=timezone.now()-timedelta(seconds=1))

    def no_dispatch(self):
        self.assertEqual(m.AiAgentJobs.objects.count(), 0)
        self.assertEqual(m.AiAgentProviderDispatches.objects.count(), 0)
        self.assertEqual(m.AiAgentToolDispatches.objects.count(), 0)


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class ReadinessLifecycleTests(_Fixtures, djtest.TransactionTestCase):
    def test_two_ticks_real_publication_permission_and_cached_reuse_ignore_forged_event(self):
        report = self.ready(mapped=True, publish=False)
        materialize = store.contract.materialize
        def outside_lock(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            return materialize(*args, **kwargs)
        with patch.object(store.contract, "materialize", side_effect=outside_lock), \
                patch.object(permission, "get", side_effect=AssertionError("first tick cannot admit")), \
                patch.object(provider, "turn") as paid, patch.object(transport, "execute_tool") as tool:
            result = service.advance(self.flow(report), self.admin)
        self.assertEqual(result["status"], "screening_prepared")
        self.assertTrue(self.published(report)); self.no_dispatch()
        paid.assert_not_called(); tool.assert_not_called()
        row = self.flow(report)
        self.assertEqual(row.attempt_count, 1)
        self.assertEqual(row.lease_token, "")
        # An append-only, self-asserted ready event is deliberately not authority.
        with mutation(self.admin):
            m.AiWorkflowEvents.objects.create(id=uid("forged-ready"), run=row, run_version=row.version,
                owner_email=row.owner_email, actor_email=self.admin.email,
                event_type="screening_capacity_verified", details_json=canonical({
                    "schemaVersion": service.EVENT_SCHEMA, "proof": {"capacityVerified": True, "fits": True}}))
        continued = []
        def on_ready(actual, prepared):
            self.assertTrue(connection.in_atomic_block)
            proof = permission.check(prepared, self.admin)
            self.assertTrue(proof["capacityVerified"])
            self.assertEqual(proof["reportId"], report.id)
            self.assertFalse(proof["runtimeAdmissionGranted"])
            self.assertEqual(actual.lease_token, "")
            continued.append(actual.id)
            return {"status": "checked_continuation"}
        measure = admission.preflight.measure
        with patch.object(transport, "catalog", return_value=fixtures.catalog()) as catalog, \
                patch.object(admission.preflight, "measure", wraps=measure) as measured, \
                patch.object(screening, "prepare_for_report", side_effect=AssertionError("published scan must not repeat")), \
                patch.object(Reader, "pages", side_effect=AssertionError("no-budget admission must not scan facts")), \
                patch.object(provider, "turn") as paid, patch.object(transport, "execute_tool") as tool:
            for _ in range(2):
                self.assertEqual(service.advance(self.flow(report), self.admin, on_ready=on_ready)["status"], "checked_continuation")
        self.assertEqual(measured.call_count, 1)
        self.assertEqual(catalog.call_count, 2)
        self.assertEqual(continued, [report.workflow_id]*2)
        self.assertEqual(self.flow(report).attempt_count, 1)
        paid.assert_not_called(); tool.assert_not_called(); self.no_dispatch()

    def test_cancel_at_scan_publication_and_after_admission_never_continues(self):
        for phase in ("scan", "publish", "admission"):
            with self.subTest(phase=phase):
                report = self.ready(publish=phase == "admission")
                continued = Mock(side_effect=AssertionError("cancelled continuation"))
                prepare, materialize, get = screening.prepare_for_report, store.contract.materialize, permission.get
                fired = []
                def scan(*args, **kwargs):
                    original = kwargs["checkpoint"]
                    def callback(event):
                        if not fired:
                            fired.append(True); self.cancel_flow(report)
                        original(event, force=True)
                    return prepare(*args, **{**kwargs, "checkpoint": callback})
                def before_publication(*args, **kwargs):
                    result = materialize(*args, **kwargs)
                    self.assertFalse(connection.in_atomic_block)
                    self.cancel_flow(report)
                    return result
                def after_admission(*args, **kwargs):
                    prepared = get(*args, **kwargs)
                    self.cancel_flow(report)
                    return prepared
                target, attribute, replacement = ((screening, "prepare_for_report", scan) if phase == "scan" else
                    (store.contract, "materialize", before_publication) if phase == "publish" else
                    (permission, "get", after_admission))
                with patch.object(target, attribute, side_effect=replacement), \
                        patch.object(transport, "catalog", return_value=fixtures.catalog()):
                    result = service.advance(self.flow(report), self.admin, on_ready=continued)
                self.assertEqual(result["status"], "lease_lost")
                self.assertEqual(self.flow(report).status, "cancelled")
                self.assertEqual(self.published(report), phase == "admission")
                continued.assert_not_called(); self.no_dispatch()

    def test_scan_revocation_fails_without_publication_or_continuation(self):
        from access_control.models import AppUser
        report = self.ready(publish=False)
        original = screening.prepare_for_report
        continued = Mock()
        def revoke(*args, **kwargs):
            check = kwargs["checkpoint"]
            def callback(event):
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")
                check(event, force=True)
            return original(*args, **{**kwargs, "checkpoint": callback})
        with patch.object(screening, "prepare_for_report", side_effect=revoke):
            result = service.advance(self.flow(report), self.admin, on_ready=continued)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errorCode"], "access_denied")
        self.assertFalse(self.published(report))
        continued.assert_not_called(); self.no_dispatch()

    def test_admission_crossing_deadline_inside_finish_lock_cannot_continue(self):
        report = self.ready()
        clock = [0.0]
        original = permission.check
        continued = Mock()
        def late_check(*args, **kwargs):
            result = original(*args, **kwargs)
            if connection.in_atomic_block:
                clock[0] = service.DEADLINE_SECONDS
            return result
        with patch.object(service, "time", SimpleNamespace(monotonic=lambda: clock[0])), \
                patch.object(permission, "check", side_effect=late_check), \
                patch.object(transport, "catalog", return_value=fixtures.catalog()):
            result = service.advance(self.flow(report), self.admin, on_ready=continued)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errorCode"], "screening_prepare_deadline")
        self.assertEqual(self.flow(report).lease_token, "")
        self.assertTrue(self.published(report))
        continued.assert_not_called(); self.no_dispatch()

    def test_expired_epoch_takeover_cannot_be_failed_by_late_old_scan(self):
        report = self.ready(publish=False)
        old_prepare = screening.prepare_for_report
        successor = []
        def takeover(*args, **kwargs):
            check = kwargs["checkpoint"]
            def callback(event):
                if not successor:
                    self.expire(report)
                    successor.append(service.claim(self.flow(report), self.admin))
                check(event, force=True)
            return old_prepare(*args, **{**kwargs, "checkpoint": callback})
        with patch.object(screening, "prepare_for_report", side_effect=takeover):
            result = service.advance(self.flow(report), self.admin)
        self.assertEqual(result["status"], "lease_lost")
        row = self.flow(report)
        self.assertEqual((row.lease_token, row.lease_epoch, row.version),
                         (successor[0].token, successor[0].epoch, successor[0].version))
        self.assertEqual(row.attempt_count, 2)
        self.assertEqual(row.status, "queued")
        self.assertEqual(row.error_code, "")
        self.assertFalse(self.published(report)); self.no_dispatch()

    def test_three_unpublished_claims_bound_recovery_but_ready_does_not_spend_scan_attempts(self):
        report = self.ready(publish=False)
        for index in range(service.MAX_SCAN_ATTEMPTS):
            lease = service.claim(self.flow(report), self.admin)
            self.assertEqual(lease.epoch, index+1)
            self.assertEqual(self.flow(report).attempt_count, index+1)
            self.expire(report)
        self.assertIsNone(service.claim(self.flow(report), self.admin))
        row = self.flow(report)
        self.assertEqual(row.status, "failed")
        self.assertEqual(row.error_code, "screening_prepare_attempts_exceeded")
        self.assertEqual(row.attempt_count, 3)
        ready_report = self.ready()
        with mutation(self.admin):
            m.AiWorkflowRuns.objects.filter(pk=ready_report.workflow_id).update(attempt_count=3)
        self.assertIsNotNone(service.claim(self.flow(ready_report), self.admin))
        self.assertEqual(self.flow(ready_report).attempt_count, 3)
        self.no_dispatch()


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class ReadinessConcurrencyTests(_Fixtures, djtest.TransactionTestCase):
    def test_two_connections_same_candidate_version_have_exactly_one_claim(self):
        report = self.ready(publish=False)
        barrier = Barrier(2)
        def claim():
            close_old_connections()
            db = connections["default"]
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET statement_timeout='5s'")
                    cursor.execute("SET lock_timeout='5s'")
                candidate = self.flow(report)
                barrier.wait(timeout=5)
                return service.claim(candidate, self.admin)
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim) for _ in range(2)]
            claimed = [future.result(timeout=20) for future in futures]
        winners = [lease for lease in claimed if lease is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(self.flow(report).attempt_count, 1)
        self.assertEqual(self.flow(report).lease_epoch, 1)
        self.assertEqual(m.AiWorkflowEvents.objects.filter(run_id=report.workflow_id,
            event_type="screening_prepare_claimed").count(), 1)
        self.assertFalse(service.available(m.AiWorkflowRuns.objects.filter(pk=report.workflow_id)).exists())
        self.no_dispatch()

    def test_scanning_does_not_hold_global_mutation_lock(self):
        report = self.ready(publish=False)
        entered, other_committed = Event(), Event()
        original = screening.prepare_for_report
        def paused_scan(*args, **kwargs):
            check = kwargs["checkpoint"]
            def checkpoint(event):
                if not entered.is_set():
                    self.assertFalse(connections["default"].in_atomic_block)
                    entered.set()
                    if not other_committed.wait(8):
                        raise AssertionError("another mutation could not finish while scan was paused")
                check(event)
            return original(*args, **{**kwargs, "checkpoint": checkpoint})
        def worker():
            close_old_connections()
            try:
                return service.advance(self.flow(report), self.admin)
            finally:
                connections["default"].close()
        with patch.object(screening, "prepare_for_report", side_effect=paused_scan), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker)
            try:
                self.assertTrue(entered.wait(8), "scan did not reach checkpoint")
                with connection.cursor() as cursor: cursor.execute("SET lock_timeout='3s'")
                with mutation(self.admin):
                    workflows.event(self.flow(self.report), self.admin, "independent_mutation_during_scan")
                other_committed.set()
                self.assertEqual(future.result(timeout=30)["status"], "screening_prepared")
            finally:
                other_committed.set()
                with connection.cursor() as cursor: cursor.execute("SET lock_timeout=DEFAULT")
        self.assertTrue(self.published(report)); self.no_dispatch()
