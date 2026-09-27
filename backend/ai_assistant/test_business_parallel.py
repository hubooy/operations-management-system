from threading import Barrier, Lock
from unittest.mock import patch
from django.db import connection
from django.test import TransactionTestCase, override_settings
from . import test_business_reports as fixtures, models as m, workflows as w, business_parallel as parallel
from .policy import AiError


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class ParallelBusinessTests(TransactionTestCase):
    user = fixtures.BusinessReportTests.user
    call = fixtures.BusinessReportTests.call
    execute = fixtures.BusinessReportTests.execute
    collect = fixtures.BusinessReportTests.collect
    create = fixtures.BusinessReportTests.create
    setUp = fixtures.BusinessReportTests.setUp

    def start(self):
        item = self.create()
        w.workflow_tick()
        flow = m.AiWorkflowRuns.objects.get(pk=item["workflowId"])
        jobs = list(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id))
        self.assertEqual(len(jobs), 3)
        self.assertEqual({job.workflow_node_key for job in jobs}, {"commerce", "promotion", "market_b2b"})
        return flow, jobs

    def test_three_actual_provider_calls_overlap_and_dispatcher_lock_rejects_overlap(self):
        flow, jobs = self.start()
        barrier, mutex = Barrier(3, timeout=10), Lock()
        active, peak, calls = 0, 0, 0
        def turn(*args, **kwargs):
            nonlocal active, peak, calls
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(parallel.agent_queue_tick()["status"], "dispatcher_busy")
            with mutex:
                active += 1
                calls += 1
                peak = max(peak, active)
            barrier.wait()
            with mutex:
                active -= 1
            return {"text": "{}", "calls": [], "frame": {"role": "assistant", "content": "{}"}}
        with patch("ai_assistant.transport.catalog", return_value=self.tools), patch("ai_assistant.provider.turn", side_effect=turn):
            first = parallel.agent_queue_tick()
            self.assertEqual(first["selected"], 3)
            second = parallel.agent_queue_tick()
        self.assertEqual((peak, calls), (3, 3))
        self.assertEqual({r["status"] for r in first["results"]}, {"completed"})
        self.assertEqual(second["status"], "idle")
        self.assertEqual(m.AiAgentProviderDispatches.objects.count(), 3)
        self.assertEqual(m.AiAgentProviderResults.objects.count(), 3)
        w.workflow_tick()
        self.assertTrue(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id, workflow_node_key="independent_review").exists())
        self.assertFalse(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id, workflow_node_key="report").exists())

    def test_local_retry_keeps_completed_siblings_and_reuses_only_failed_job(self):
        flow, jobs = self.start()
        failed = jobs[0]
        with patch("ai_assistant.transport.catalog", side_effect=AiError("offline", "service_unavailable", 503)):
            w.agent_tick(job_id=failed.id)
        with patch("ai_assistant.transport.catalog", return_value=self.tools), patch("ai_assistant.provider.turn", return_value={"text": "{}", "calls": [], "frame": {"role": "assistant", "content": "{}"}}) as model:
            w.workflow_tick()
            flow.refresh_from_db()
            self.assertEqual(flow.status, "running", list(m.AiAgentJobs.objects.values("status", "error_code", "retryable")))
            parallel.agent_queue_tick()
            w.workflow_tick()
            flow.refresh_from_db()
            self.assertEqual(flow.status, "paused")
            completed = dict(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id, status="completed").values_list("id", "output_json"))
            self.assertEqual(len(completed), 2)
            self.assertEqual(model.call_count, 2)
            w.control(flow.id, {"expectedVersion": flow.version}, self.admin, "resume", True)
            parallel.agent_queue_tick()
            parallel.agent_queue_tick()
            w.workflow_tick()
            self.assertEqual(model.call_count, 3)
            self.assertEqual(dict(m.AiAgentJobs.objects.filter(pk__in=completed).values_list("id", "output_json")), completed)
            self.assertEqual(m.AiAgentJobs.objects.filter(pk=failed.id, status="completed").count(), 1)
            self.assertTrue(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id, workflow_node_key="independent_review").exists())

    def test_cancellation_fences_all_three_inflight_results(self):
        flow, _ = self.start()
        barrier, mutex = Barrier(3, timeout=10), Lock()
        ordinal = 0
        def turn(*args, **kwargs):
            nonlocal ordinal
            with mutex:
                ordinal += 1
                mine = ordinal
            barrier.wait()
            if mine == 1:
                flow.refresh_from_db()
                w.control(flow.id, {"expectedVersion": flow.version}, self.admin, "cancel", True)
            barrier.wait()
            return {"text": "{}", "calls": [], "frame": {"role": "assistant", "content": "{}"}}
        with patch("ai_assistant.transport.catalog", return_value=self.tools), patch("ai_assistant.provider.turn", side_effect=turn) as model:
            result = parallel.agent_queue_tick()
            self.assertEqual({r["status"] for r in result["results"]}, {"lease_lost"})
            self.assertEqual(parallel.agent_queue_tick()["status"], "idle")
            self.assertEqual(model.call_count, 3)
        self.assertEqual(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id, status="cancelled").count(), 3)
        self.assertEqual(m.AiAgentProviderResults.objects.count(), 0)

    def test_one_unknown_result_preserves_two_successes_without_replay(self):
        flow, jobs = self.start()
        failed_task = jobs[0].task
        def turn(model, frames, *args):
            if frames[0]["content"].startswith(failed_task):
                raise AiError("unknown", "provider_timeout", 503)
            return {"text": "{}", "calls": [], "frame": {"role": "assistant", "content": "{}"}}
        with patch("ai_assistant.transport.catalog", return_value=self.tools), patch("ai_assistant.provider.turn", side_effect=turn) as model:
            parallel.agent_queue_tick()
            w.workflow_tick()
            flow.refresh_from_db()
            self.assertEqual(flow.status, "failed")
            self.assertFalse(flow.retryable)
            self.assertEqual(parallel.agent_queue_tick()["status"], "idle")
            self.assertEqual(model.call_count, 3)
        self.assertEqual(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id, status="completed").count(), 2)
        self.assertEqual(m.AiAgentProviderDispatches.objects.filter(state="unknown").count(), 1)
        self.assertFalse(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id, workflow_node_key="independent_review").exists())
        with self.assertRaises(AiError):
            w.control(flow.id, {"expectedVersion": flow.version}, self.admin, "resume", True)

    def test_dispatch_lock_is_released_on_selector_failure(self):
        with patch("ai_assistant.workflows.agent_candidates", side_effect=RuntimeError("fixture")), self.assertRaises(RuntimeError):
            parallel.agent_queue_tick()
        self.assertEqual(parallel.agent_queue_tick()["status"], "idle")
