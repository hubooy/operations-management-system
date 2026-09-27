"""Real signed HTTP boundaries; no paid model or production data."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from django.db import connection
from django.utils import timezone

from . import test_business_screening_creation as creation_tests
from . import test_business_screening_content as content_tests
from . import business_screening_creation as creation, business_screening_content as content
from . import business_screening_readiness as readiness, workflows, models as m, views, transport
from .business_sealed import Reader
from .control_models import AiWriteReceipt, AiWriteAuthority
from .policy import AiError, mutation


def forbid_locked_facts(test):
    original=Reader.pages
    def pages(reader,*args,**kwargs):
        test.assertFalse(connection.in_atomic_block)
        for page in original(reader,*args,**kwargs):
            test.assertFalse(connection.in_atomic_block)
            yield page
    return patch.object(Reader,"pages",pages)


def process_role(role):
    # Activate only the disposable database's actual authority, not a mock.
    from uuid import UUID
    epoch=UUID(int=1)
    with mutation():
        AiWriteAuthority.objects.filter(pk=1,status="d1").update(status="postgres",authority_epoch=epoch,cutover_id="screening-http-fixture",
            migration_verify_run_id="isolated-http-fixture",activated_at=timezone.now())
    return override_settings(DJANGO_PROCESS_ROLE=role,AI_WRITE_AUTHORITY_EPOCH=str(epoch),
        AI_WRITE_CUTOVER_ID="screening-http-fixture")


@override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningCreationHttpTests(TransactionTestCase):
    user=creation_tests.ScreeningCreationTests.user
    call=creation_tests.ScreeningCreationTests.call
    collect_body=creation_tests.ScreeningCreationTests.collect_body
    bundle=creation_tests.ScreeningCreationTests.bundle
    input_for=creation_tests.ScreeningCreationTests.input_for
    insert=creation_tests.ScreeningCreationTests.insert
    seed=creation_tests.ScreeningCreationTests.seed
    setUp=creation_tests.ScreeningCreationTests.setUp
    creation_body=creation_tests.ScreeningCreationTests.creation_body
    catalog_response=creation_tests.ScreeningCreationTests.catalog_response
    counts=creation_tests.ScreeningCreationTests.counts

    def test_signed_creation_and_replays_keep_full_preparation_outside_http_transaction(self):
        body=self.creation_body(mapped=True,budget=True)
        with process_role("ai_writer"),forbid_locked_facts(self),patch.object(
                transport,"catalog",side_effect=self.catalog_response):
            response=self.call("/api/ai/business-reports",body,principal=self.admin,request_id="screen-create-http")
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(AiWriteReceipt.objects.get(pk="screen-create-http").status,"completed")
        before=self.counts()
        with patch.object(transport,"catalog",side_effect=AssertionError("replay network")),patch.object(
                Reader,"pages",side_effect=AssertionError("replay facts")):
            replay=self.call("/api/ai/business-reports",body,principal=self.admin,request_id="screen-create-http")
            lookup=self.call("/api/ai/business-reports",body,principal=self.admin,request_id="screen-create-http-lookup")
        self.assertEqual(replay.status_code,200,replay.content)
        self.assertEqual(replay.headers.get("X-Teruisi-Write-Replay"),"1")
        self.assertEqual(lookup.status_code,200,lookup.content)
        self.assertTrue(lookup.json()["replayed"])
        self.assertEqual(lookup.json()["item"],response.json()["item"])
        self.assertEqual(self.counts(),before)
        row=m.AiReportRun.objects.select_related("workflow").get(pk=response.json()["item"]["id"])
        with patch.object(Reader,"pages",side_effect=AssertionError("progress scans")):
            self.assertEqual(readiness.preparation_status(row,self.admin),
                {"status":"queued_scan","scanPublished":False,"agentsStarted":False})
            lease=readiness.claim(row.workflow,self.admin)
            self.assertIsNotNone(lease)
            self.assertEqual(readiness.preparation_status(row,self.admin)["status"],"scanning")
            with mutation(self.admin):
                m.AiWorkflowRuns.objects.filter(pk=row.workflow_id).update(lease_expires_at=timezone.now()-timedelta(seconds=1))
            self.assertEqual(readiness.preparation_status(row,self.admin)["status"],"queued_scan")

    def test_failed_http_finish_rolls_back_report_budget_nodes_and_completed_receipt(self):
        body=self.creation_body(mapped=True,budget=True);before=self.counts()
        original=views.finish
        def fail(*args,**kwargs):
            self.assertTrue(connection.in_atomic_block)
            original(*args,**kwargs)
            raise AiError("synthetic commit failure","conflict",409)
        with patch.object(transport,"catalog",side_effect=self.catalog_response),patch.object(views,"finish",side_effect=fail):
            response=self.call("/api/ai/business-reports",body,principal=self.admin,request_id="screen-create-rollback")
        self.assertEqual(response.status_code,409,response.content)
        self.assertEqual(self.counts(),before)
        self.assertEqual(AiWriteReceipt.objects.get(pk="screen-create-rollback").status,"processing")
        with patch.object(creation,"create",side_effect=AssertionError("unknown request rerun")):
            retry=self.call("/api/ai/business-reports",body,principal=self.admin,request_id="screen-create-rollback")
        self.assertEqual(retry.json()["code"],"request_pending")


@override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningReviewHttpTests(TransactionTestCase):
    user=content_tests.ScreeningPreparedReviewTests.user
    call=content_tests.ScreeningPreparedReviewTests.call
    collect_body=content_tests.ScreeningPreparedReviewTests.collect_body
    bundle=content_tests.ScreeningPreparedReviewTests.bundle
    input_for=content_tests.ScreeningPreparedReviewTests.input_for
    insert=content_tests.ScreeningPreparedReviewTests.insert
    seed=content_tests.ScreeningPreparedReviewTests.seed
    setUp=content_tests.ScreeningPreparedReviewTests.setUp
    screening_bundle=content_tests.ScreeningPreparedReviewTests.screening_bundle
    insert_screening=content_tests.ScreeningPreparedReviewTests.insert_screening
    create_complete=content_tests.ScreeningPreparedReviewTests.create_complete
    waiting=content_tests.ScreeningPreparedReviewTests.waiting

    def test_signed_review_prepares_outside_transaction_and_replays_without_content(self):
        report,human=self.waiting(budget=True)
        with process_role("ai_reader"),forbid_locked_facts(self):
            detail=self.call(f"/api/ai/reports/{report.id}",principal=self.admin,method="GET")
        self.assertEqual(detail.status_code,200,detail.content)
        self.assertNotIn("contentError",detail.json())
        self.assertEqual(detail.json()["screeningPreparation"]["status"],"waiting_review")
        # Optional synthetic DTO artifact for the independent browser rehearsal.
        import os
        if os.environ.get("TERUISI_SCREENING_SYNTHETIC_UI_FIXTURE") == "1":
            from pathlib import Path
            artifact=Path(__file__).resolve().parents[2]/".runtime"/"screening-ui-synthetic-detail.json"
            artifact.write_bytes(detail.content)
        path=f"/api/ai/workflow-runs/{report.workflow_id}/nodes/human_review/review"
        body={"expectedVersion":human.version,"decision":"approve"}
        with process_role("ai_writer"),forbid_locked_facts(self):
            response=self.call(path,body,principal=self.admin,request_id="screen-review-http")
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()["item"]["status"],"queued")
        with patch.object(content,"prepare_review",side_effect=AssertionError("replay full review")):
            replay=self.call(path,body,principal=self.admin,request_id="screen-review-http")
        self.assertEqual(replay.status_code,200,replay.content)
        self.assertEqual(replay.json(),response.json())
        self.assertEqual(replay.headers.get("X-Teruisi-Write-Replay"),"1")

    def test_failed_finish_keeps_human_waiting_and_request_uncertain(self):
        report,human=self.waiting()
        original=views.finish
        def fail(*args,**kwargs):
            original(*args,**kwargs)
            raise AiError("synthetic review rollback","conflict",409)
        path=f"/api/ai/workflow-runs/{report.workflow_id}/nodes/human_review/review"
        with patch.object(views,"finish",side_effect=fail):
            response=self.call(path,{"expectedVersion":human.version,"decision":"approve"},
                principal=self.admin,request_id="screen-review-rollback")
        self.assertEqual(response.status_code,409,response.content)
        human.refresh_from_db()
        self.assertEqual(human.status,"waiting_review")
        self.assertEqual(m.AiWorkflowRuns.objects.get(pk=report.workflow_id).status,"waiting_review")
        self.assertEqual(AiWriteReceipt.objects.get(pk="screen-review-rollback").status,"processing")
