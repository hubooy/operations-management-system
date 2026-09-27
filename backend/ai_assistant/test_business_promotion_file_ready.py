"""Renderer-7 internal publication over persisted verified volumes."""
import json
from importlib import import_module
import unittest
from unittest.mock import patch

from django import test as djtest
from django.db import DatabaseError, transaction
from access_control.models import AppUser

from . import business_files as files, business_promotion_volume_stage as stage
from . import business_promotion_review as review, business_volume_files, models as m
from . import test_business_promotion_approved_content as approval_fixture
from . import test_business_promotion_volume_stage as fixtures
from .policy import AiError, mutation


def migration():
    return import_module("ai_assistant.migrations.0029_business_promotion_file_ready")


class PromotionReadySqlContractTests(unittest.TestCase):
    def test_old_file_guards_and_explicit_review_requirements(self):
        sql = migration()
        self.assertEqual(sql.OLD_SQL[0], sql.NEW_SQL[0])
        self.assertEqual(sql.OLD_SQL[1], sql.NEW_SQL[1])
        self.assertIn("parent_renderer NOT IN (4,6,7)", sql.MANIFEST_GUARD)
        self.assertIn("OLD.error_code<>'renderer_unpublished'", sql.RUN_GUARD)
        self.assertIn("NEW.attempt<>OLD.attempt", sql.RUN_GUARD)
        self.assertIn("NEW.manifest_json IS DISTINCT FROM OLD.manifest_json", sql.RUN_GUARD)
        self.assertIn("ai_business_promotion_ready_requirements(parent.id)", sql.COMPLETE_GUARD)
        self.assertIn("human.status<>'completed'", sql.READY_REQUIREMENTS)
        self.assertIn("total<>5", sql.READY_REQUIREMENTS)
        self.assertIn("flow.cancel_requested<>0", sql.READY_REQUIREMENTS)
        self.assertIn("flow.status<>'completed'", sql.READY_REQUIREMENTS)
        self.assertIn("flow.output_json::jsonb IS DISTINCT FROM expected_output", sql.READY_REQUIREMENTS)
        self.assertNotIn("SECURITY DEFINER", sql.READY_REQUIREMENTS)


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionReadyTests(djtest.TransactionTestCase):
    user = fixtures.PromotionVolumeStageTests.user
    call = fixtures.PromotionVolumeStageTests.call
    collect_body = fixtures.PromotionVolumeStageTests.collect_body
    bundle = fixtures.PromotionVolumeStageTests.bundle
    input_for = fixtures.PromotionVolumeStageTests.input_for
    insert = fixtures.PromotionVolumeStageTests.insert
    seed = fixtures.PromotionVolumeStageTests.seed
    setUp = fixtures.PromotionVolumeStageTests.setUp
    request_body = fixtures.PromotionVolumeStageTests.request_body
    current_catalog = fixtures.PromotionVolumeStageTests.current_catalog
    create_fixed_report = fixtures.PromotionVolumeStageTests.create_fixed_report
    base = fixtures.PromotionVolumeStageTests.base
    read = fixtures.PromotionVolumeStageTests.read
    append = fixtures.PromotionVolumeStageTests.append
    package = fixtures.PromotionVolumeStageTests.package
    promotion = fixtures.PromotionVolumeStageTests.promotion
    complete = fixtures.PromotionVolumeStageTests.complete
    running_job = fixtures.PromotionVolumeStageTests.running_job
    five_completed = fixtures.PromotionVolumeStageTests.five_completed
    approved = fixtures.PromotionVolumeStageTests.approved
    complete_flow = approval_fixture.PromotionApprovedContentTests.complete_flow
    create_approved_report = fixtures.PromotionVolumeStageTests.create_approved_report

    def staged(self):
        report = self.create_approved_report()
        run_id = stage.create(report.id, self.admin)["item"]["id"]
        result = files.tick()
        self.assertEqual(result["status"], "staged_unpublished")
        return m.AiBusinessFileRun.objects.select_related("report__workflow").get(pk=run_id)

    def test_complete_stage_can_publish_only_through_internal_verifier(self):
        with patch.object(stage.approved_content.runtime.transport, "catalog", side_effect=self.current_catalog):
            row = self.staged()
            with self.assertRaises(AiError): stage.publish(row.id, row.version, self.admin)
            self.complete_flow(row.report)
            row.refresh_from_db()
            result = stage.publish(row.id, row.version, self.admin)
            self.assertEqual(result["item"]["status"], "ready")
            self.assertEqual(result["item"]["rendererVersion"], 7)
            self.assertEqual(result["item"]["manifest"]["rendererVersion"], 7)
            row.refresh_from_db()
            self.assertEqual(row.status, "ready")
            progress = json.loads(row.progress_json)
            self.assertEqual(progress["stage"], "ready")
            self.assertEqual(len(progress["publicationFenceDigest"]), 64)
            self.assertEqual(progress["manifestFileSha256"],
                result["item"]["manifest"]["manifestFile"]["sha256"])
            downloaded = business_volume_files.chunk(
                row.id, "1", "html", {"sequence": "1"}, self.admin)
            self.assertEqual(downloaded["runId"], row.id)
            self.assertEqual(downloaded["format"], "html")
            self.assertEqual(downloaded["bindingDigest"], row.binding_digest)
            with self.assertRaises(AiError): stage.publish(row.id, row.version, self.admin)

    def test_tampered_compact_or_revoked_owner_cannot_publish(self):
        with patch.object(stage.approved_content.runtime.transport, "catalog", side_effect=self.current_catalog):
            row = self.staged()
            self.complete_flow(row.report)
            with self.assertRaises(DatabaseError), mutation(self.admin):
                m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=row.attempt).update(content=b"tampered")
            value = json.loads(row.manifest_json)
            value["bindingDigest"] = "0"*64
            with mutation(self.admin):
                row.manifest_json = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                row.version += 1
                row.save(update_fields=["manifest_json", "version"])
            row.refresh_from_db()
            with self.assertRaises(AiError): stage.publish(row.id, row.version, self.admin)
            self.assertEqual(m.AiBusinessFileRun.objects.get(pk=row.id).status, "paused")

        # A separate report proves live owner revocation without an altered
        # compact manifest. Its file stays paused and inaccessible.
        with patch.object(stage.approved_content.runtime.transport, "catalog", side_effect=self.current_catalog):
            another = self.staged()
            self.complete_flow(another.report)
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            with self.assertRaises(AiError): stage.publish(another.id, another.version, self.admin)
            self.assertEqual(m.AiBusinessFileRun.objects.get(pk=another.id).status, "paused")

    def test_direct_sql_still_requires_staged_attempt_and_human_review(self):
        with patch.object(stage.approved_content.runtime.transport, "catalog", side_effect=self.current_catalog):
            row = self.staged()
            with self.assertRaisesRegex(DatabaseError, "ai_promotion_ready"), mutation(self.admin):
                m.AiBusinessFileRun.objects.filter(pk=row.id).update(status="ready",
                    error_code="", progress_json='{"stage":"ready"}', version=row.version+1)
            self.complete_flow(row.report)
            with self.assertRaises(DatabaseError), mutation(self.admin):
                m.AiBusinessFileRun.objects.filter(pk=row.id).update(status="ready",
                    error_code="", progress_json='{"stage":"ready"}',
                    version=row.version+1, attempt=row.attempt+1)
            self.assertEqual(m.AiBusinessFileRun.objects.get(pk=row.id).status, "paused")
            with mutation(self.admin):
                human = m.AiWorkflowNodeRuns.objects.get(run_id=row.report.workflow_id,
                    node_key="human_review")
                human.status = "waiting_review"
                human.version += 1
                human.save(update_fields=["status", "version"])
            with self.assertRaises(AiError): stage.publish(row.id, row.version, self.admin)
            with self.assertRaisesRegex(DatabaseError, "ai_promotion_ready"), mutation(self.admin):
                m.AiBusinessFileRun.objects.filter(pk=row.id).update(status="ready",
                    error_code="", progress_json='{"stage":"ready"}', version=row.version+1)
            self.assertEqual(m.AiBusinessFileRun.objects.get(pk=row.id).status, "paused")

    def test_provider_or_tool_ledger_race_before_cas_blocks_ready(self):
        with patch.object(stage.approved_content.runtime.transport, "catalog", side_effect=self.current_catalog):
            row = self.staged()
            self.complete_flow(row.report)
            with patch.object(review, "_ledger", side_effect=["a"*64, "b"*64]):
                with self.assertRaises(AiError): stage.publish(row.id, row.version, self.admin)
            self.assertEqual(m.AiBusinessFileRun.objects.get(pk=row.id).status, "paused")
