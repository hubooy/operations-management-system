"""Actual child ownership before scheduler propagation; no model calls."""
from unittest.mock import patch

from django import test as djtest

from . import business_screening_pipeline as service, business_screening_permission as permission
from . import business_parallel, models as m, provider
from . import test_business_screening_execution as fixtures
from .policy import AiError, canonical, mutation


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class ScreeningPipelineBindingTests(djtest.TransactionTestCase):
    user = fixtures.ScreeningExecutionTests.user
    call = fixtures.ScreeningExecutionTests.call
    collect_body = fixtures.ScreeningExecutionTests.collect_body
    bundle = fixtures.ScreeningExecutionTests.bundle
    input_for = fixtures.ScreeningExecutionTests.input_for
    insert = fixtures.ScreeningExecutionTests.insert
    seed = fixtures.ScreeningExecutionTests.seed
    screening_bundle = fixtures.ScreeningExecutionTests.screening_bundle
    insert_screening = fixtures.ScreeningExecutionTests.insert_screening
    ready = fixtures.ScreeningExecutionTests.ready
    running = fixtures.ScreeningExecutionTests.running
    setUp = fixtures.ScreeningExecutionTests.setUp

    def advance(self, report, permit):
        with mutation(self.admin):
            flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
            return service.step(flow, permit, self.admin)

    def assert_no_propagation(self, report, permit):
        before_jobs = m.AiAgentJobs.objects.count()
        before_providers = m.AiAgentProviderDispatches.objects.count()
        before_nodes = list(m.AiWorkflowNodeRuns.objects.filter(run_id=report.workflow_id)
                            .order_by("position").values("id", "status", "version", "input_json", "output_json", "agent_job_id"))
        with patch.object(provider, "turn", side_effect=AssertionError("no model")):
            with self.assertRaises(AiError): self.advance(report, permit)
            # The shared parallel entry also cannot bypass the screening gate.
            with self.assertRaises(AiError):
                with mutation(self.admin):
                    flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
                    business_parallel.workflow_step(flow, self.admin, [], screening_permission=permit)
        self.assertEqual(m.AiAgentJobs.objects.count(), before_jobs)
        self.assertEqual(m.AiAgentProviderDispatches.objects.count(), before_providers)
        self.assertEqual(list(m.AiWorkflowNodeRuns.objects.filter(run_id=report.workflow_id)
                             .order_by("position").values("id", "status", "version", "input_json", "output_json", "agent_job_id")), before_nodes)

    def test_other_reports_completed_child_is_rejected_before_any_copy_or_creation(self):
        report, _ = self.running()
        _, foreign = self.running()
        permit = permission.get(report, self.admin)
        with mutation(self.admin):
            foreign.status = "completed"
            foreign.output_json = canonical({"answer":"other report synthetic output"})
            foreign.save()
            # Node identity is mutable in the existing DB schema; unlike a
            # protected job identity, this is a real persisted negative case.
            m.AiWorkflowNodeRuns.objects.filter(run_id=report.workflow_id, node_key="commerce").update(agent_job=foreign)
        self.assert_no_propagation(report, permit)

    def test_same_report_swapped_roles_are_rejected_even_with_unique_child_ids(self):
        report, commerce = self.running()
        promotion = m.AiAgentJobs.objects.get(workflow_run_id=report.workflow_id, workflow_node_key="promotion")
        permit = permission.get(report, self.admin)
        with mutation(self.admin):
            m.AiWorkflowNodeRuns.objects.filter(run_id=report.workflow_id, node_key="commerce").update(agent_job=promotion)
            m.AiWorkflowNodeRuns.objects.filter(run_id=report.workflow_id, node_key="promotion").update(agent_job=commerce)
        self.assert_no_propagation(report, permit)

    def test_valid_running_children_can_finish_then_completed_output_cannot_change(self):
        report, _ = self.running()
        permit = permission.get(report, self.admin)
        self.advance(report, permit)
        self.assertEqual(m.AiAgentJobs.objects.filter(workflow_run_id=report.workflow_id).count(), 3)
        with mutation(self.admin):
            for child in m.AiAgentJobs.objects.filter(workflow_run_id=report.workflow_id):
                child.status = "completed"
                child.output_json = canonical({"answer":"synthetic " + child.workflow_node_key})
                child.save()
        # Running nodes have no output yet. Only the real scheduler copies the
        # completed child's result, then creates the fourth dependent role.
        with patch.object(provider, "turn", side_effect=AssertionError("no model")):
            self.advance(report, permit)
        self.assertEqual(m.AiAgentJobs.objects.filter(workflow_run_id=report.workflow_id).count(), 4)
        self.assertEqual(m.AiWorkflowNodeRuns.objects.filter(run_id=report.workflow_id, status="completed").count(), 3)
        self.advance(report, permit)
        with mutation(self.admin):
            m.AiWorkflowNodeRuns.objects.filter(run_id=report.workflow_id, node_key="commerce").update(
                output_json=canonical({"answer":"late node-only change"}))
        self.assert_no_propagation(report, permit)
