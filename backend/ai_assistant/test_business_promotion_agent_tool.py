"""Fourth tool over real new-profile report, published screening and job."""
from datetime import timedelta
import json
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from . import business_diagnostic_screening as screening
from . import business_promotion_agent_tool as service
from . import business_promotion_creation as creation
from . import business_screening_store as store
from . import models as m
from . import test_business_promotion_creation as fixtures
from .policy import AiError, canonical, digest, mutation, uid


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionAgentToolTests(djtest.TransactionTestCase):
    user = fixtures.PromotionCreationTests.user
    call = fixtures.PromotionCreationTests.call
    collect_body = fixtures.PromotionCreationTests.collect_body
    bundle = fixtures.PromotionCreationTests.bundle
    input_for = fixtures.PromotionCreationTests.input_for
    insert = fixtures.PromotionCreationTests.insert
    seed = fixtures.PromotionCreationTests.seed
    setUp = fixtures.PromotionCreationTests.setUp
    request_body = fixtures.PromotionCreationTests.request_body
    current_catalog = fixtures.PromotionCreationTests.current_catalog

    def create_report(self, *, publish=True):
        body = self.request_body()
        with patch.object(creation.transport, "catalog", side_effect=self.current_catalog):
            created = creation.create(body, self.admin)
        report = m.AiReportRun.objects.select_related("workflow").get(pk=created["item"]["id"])
        if publish:
            verified = screening.prepare_for_report(report.id, self.admin)
            store.publish(verified, self.admin)
        return report

    def actual_job(self, report, role="promotion"):
        flow = report.workflow
        node = m.AiWorkflowNodeRuns.objects.get(run=flow, node_key=role)
        payload = canonical({"workflowInput":json.loads(flow.input_json), "dependencies":{}})
        with mutation(self.admin):
            job = m.AiAgentJobs.objects.create(id=uid("promotion-tool-job"),
                owner_email=flow.owner_email, scope_json=flow.scope_json,
                client_request_id=uid("promotion-tool-client"), request_digest=digest(payload),
                task=node.instruction, input_json=payload, workflow_run_id=flow.id,
                workflow_node_key=role, model_id=flow.model_id, model_version=flow.model_version,
                allowed_tools_json=flow.allowed_tools_json, tool_policy_digest=flow.tool_policy_digest,
                status="running", lease_token=uid("promotion-tool-lease"), lease_epoch=1,
                lease_expires_at=timezone.now()+timedelta(minutes=4))
            node.agent_job = job
            node.status = "running"
            node.input_json = payload
            node.save()
        return job

    def read(self, job, params):
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.read(job.id, params, self.admin)

    def test_published_new_profile_reads_exact_page_and_row_without_receipt(self):
        report = self.create_report()
        job = self.actual_job(report)
        args = {"reportId":report.id, "sourceKey":"ads", "view":"keyword_sku"}
        before = (m.AiAgentToolDispatches.objects.count(), m.AiAgentToolResults.objects.count())
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            page = self.read(job, args)
            row = page["table"]["rows"][0]
            found = self.read(job, {**args, "rowIndex":row["rowIndex"], "rowId":row["id"]})
        self.assertEqual(found["row"], row)
        self.assertEqual(page["binding"]["reportBinding"]["reportId"], report.id)
        self.assertTrue(page["authority"]["completeSourceTraversalForSelectedSources"])
        self.assertNotIn("readReceipt", page)
        self.assertEqual(before, (m.AiAgentToolDispatches.objects.count(), m.AiAgentToolResults.objects.count()))
        model.assert_not_called(); remote.assert_not_called()
        for query in queries:
            self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")))

    def test_unpublished_wrong_role_selector_and_invalid_mode_fail_closed(self):
        report = self.create_report(publish=False)
        job = self.actual_job(report)
        args = {"reportId":report.id, "sourceKey":"ads", "view":"keyword_sku"}
        with self.assertRaises(AiError): self.read(job, args)
        verified = screening.prepare_for_report(report.id, self.admin)
        store.publish(verified, self.admin)
        other = self.actual_job(report, role="commerce")
        with self.assertRaises(AiError): self.read(other, args)
        for changed in ({**args, "sourceKey":"sales"}, {**args, "baselineKey":"ads"},
                {**args, "view":"unit"}, {**args, "offset":True},
                {**args, "limit":10}, {**args, "rowIndex":0},
                {**args, "rowIndex":0, "rowId":"0"*64, "offset":0},
                {**args, "unexpected":True}):
            with self.subTest(changed=changed), self.assertRaises(AiError): self.read(job, changed)

    def test_late_revocation_cannot_return_owning_page(self):
        from access_control.models import AppUser
        report = self.create_report()
        job = self.actual_job(report)
        original = service.owning.page
        def revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return result
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog), patch.object(
                service.owning, "page", side_effect=revoke), self.assertRaises(AiError):
            service.read(job.id, {"reportId":report.id, "sourceKey":"ads", "view":"keyword_sku"}, self.admin)
