"""Paused promotion CAS candidate and unregistered five-role continuation."""
import json
from unittest.mock import patch

from django import test as djtest
from django.db import transaction

from . import business_promotion_pipeline as pipeline
from . import business_promotion_readiness as readiness
from . import business_promotion_runtime_permission as permission
from . import business_promotion_preflight as preflight
from . import models as m
from . import test_business_promotion_preflight as fixtures
from .policy import AiError, canonical, mutation


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionResumePipelineTests(djtest.TransactionTestCase):
    user = fixtures.PromotionPreflightPersistedTests.user
    call = fixtures.PromotionPreflightPersistedTests.call
    collect_body = fixtures.PromotionPreflightPersistedTests.collect_body
    bundle = fixtures.PromotionPreflightPersistedTests.bundle
    input_for = fixtures.PromotionPreflightPersistedTests.input_for
    insert = fixtures.PromotionPreflightPersistedTests.insert
    seed = fixtures.PromotionPreflightPersistedTests.seed
    setUp = fixtures.PromotionPreflightPersistedTests.setUp
    request_body = fixtures.PromotionPreflightPersistedTests.request_body
    current_catalog = fixtures.PromotionPreflightPersistedTests.current_catalog
    create_published = fixtures.PromotionPreflightPersistedTests.create_published

    def capacity(self, report):
        with patch.object(preflight.transport, "catalog", side_effect=self.current_catalog):
            return permission.prepare(report.id, self.admin, "commerce")

    def test_parked_exact_version_has_reviewable_cas_candidate_but_no_resume(self):
        report = self.create_published()
        parked = readiness.advance(report.workflow, self.admin)
        self.assertEqual(parked["status"], "screening_published_awaiting_admission")
        with patch.object(preflight.transport, "catalog", side_effect=self.current_catalog):
            candidate = readiness.prepare_resume_candidate(report.id, self.admin)
        with transaction.atomic():
            plan = readiness.check_resume_candidate(candidate, self.admin)
        self.assertEqual(plan["fromStatus"], "paused")
        self.assertEqual(plan["proposedStatus"], "queued")
        self.assertFalse(plan["casApplied"])
        self.assertFalse(plan["runtimeAdmissionGranted"])
        self.assertFalse(plan["pipelineRegistered"])
        with self.assertRaises(AiError) as error:
            readiness.resume_cas(candidate, self.admin)
        self.assertEqual(error.exception.code, "promotion_pipeline_not_routed")
        flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
        self.assertEqual(flow.status, "paused")
        self.assertEqual(flow.error_code, readiness.PARKED_CODE)
        self.assertFalse(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id).exists())
        with mutation(self.admin):
            m.AiWorkflowRuns.objects.filter(pk=flow.id).update(version=flow.version + 1)
        with transaction.atomic(), self.assertRaises(AiError):
            readiness.check_resume_candidate(candidate, self.admin)

    def test_private_pipeline_can_create_five_bound_children_only_under_route_token(self):
        report = self.create_published()
        with self.assertRaises(AiError): pipeline.step(report.workflow, None, self.admin)
        with transaction.atomic(), self.assertRaises(AiError):
            pipeline._step_after_route(report.workflow, None, self.admin, route_token=None)
        for expected_count in (3, 4, 5):
            prepared = self.capacity(report)
            with transaction.atomic():
                row = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
                pipeline._step_after_route(row, prepared, self.admin, route_token=pipeline._ROUTE_TOKEN)
            jobs = list(m.AiAgentJobs.objects.filter(workflow_run_id=report.workflow_id))
            self.assertEqual(len(jobs), expected_count)
            if expected_count < 5:
                with mutation(self.admin):
                    for job in jobs:
                        if job.status == "queued":
                            job.status = "completed"
                            job.output_json = canonical({"answer": "已核对"})
                            job.save()
        self.assertEqual({job.workflow_node_key for job in jobs},
            {"commerce", "promotion", "market_b2b", "independent_review", "report"})
