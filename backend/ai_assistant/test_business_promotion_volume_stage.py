"""Actual renderer-7 immutable staged chunks; DB ready remains closed."""
from unittest.mock import patch

from django import test as djtest
from access_control.models import AppUser

from . import business_files as files, business_promotion_volume_stage as stage
from . import business_volume_files, models as m
from . import test_business_promotion_approved_content as fixtures
from .policy import AiError


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionVolumeStageTests(djtest.TransactionTestCase):
    user = fixtures.PromotionApprovedContentTests.user
    call = fixtures.PromotionApprovedContentTests.call
    collect_body = fixtures.PromotionApprovedContentTests.collect_body
    bundle = fixtures.PromotionApprovedContentTests.bundle
    input_for = fixtures.PromotionApprovedContentTests.input_for
    insert = fixtures.PromotionApprovedContentTests.insert
    seed = fixtures.PromotionApprovedContentTests.seed
    setUp = fixtures.PromotionApprovedContentTests.setUp
    request_body = fixtures.PromotionApprovedContentTests.request_body
    current_catalog = fixtures.PromotionApprovedContentTests.current_catalog
    create_fixed_report = fixtures.PromotionApprovedContentTests.create_fixed_report
    base = fixtures.PromotionApprovedContentTests.base
    read = fixtures.PromotionApprovedContentTests.read
    append = fixtures.PromotionApprovedContentTests.append
    package = fixtures.PromotionApprovedContentTests.package
    promotion = fixtures.PromotionApprovedContentTests.promotion
    complete = fixtures.PromotionApprovedContentTests.complete
    running_job = fixtures.PromotionApprovedContentTests.running_job
    five_completed = fixtures.PromotionApprovedContentTests.five_completed
    approved = fixtures.PromotionApprovedContentTests.approved

    def create_approved_report(self):
        report = self.five_completed(promotion_reference=True)
        self.approved(report)
        return report

    def test_full_stage_resume_rebuild_cancel_keep_attempts_separate(self):
        report = self.create_approved_report()
        with patch.object(stage.approved_content.runtime.transport, "catalog", side_effect=self.current_catalog):
            created = stage.create(report.id, self.admin)
            self.assertFalse(created["replayed"])
            run_id = created["item"]["id"]
            self.assertEqual(stage.create(report.id, self.admin)["item"]["id"], run_id)
            first = files.tick()
            self.assertEqual(first["status"], "staged_unpublished")
            row = m.AiBusinessFileRun.objects.get(pk=run_id)
            self.assertEqual((row.renderer_version, row.status, row.attempt), (7, "paused", 1))
            self.assertEqual(row.error_code, "renderer_unpublished")
            self.assertGreater(row.stored_bytes, 0)
            old = list(m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=1).values_list(
                "id", "content_digest"))
            self.assertTrue(old)
            stage._verify_staged(row, self.admin, lambda *args, **kwargs: None)
            stage.control(run_id, "resume", row.version, self.admin)
            resumed = files.tick()
            self.assertEqual((resumed["status"], resumed["attempt"]), ("staged_unpublished", 1))
            row.refresh_from_db()
            self.assertEqual(old, list(m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=1).values_list(
                "id", "content_digest")))
            stage.control(run_id, "rebuild", row.version, self.admin)
            second = files.tick()
            self.assertEqual((second["status"], second["attempt"]), ("staged_unpublished", 2))
            row.refresh_from_db()
            self.assertTrue(m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=2).exists())
            self.assertEqual(old, list(m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=1).values_list(
                "id", "content_digest")))
            self.assertEqual(files.mapping(row)["manifest"], None)
            stage.control(run_id, "cancel", row.version, self.admin)
            row.refresh_from_db()
            self.assertEqual(row.status, "cancelled")
            self.assertEqual(files.tick()["status"], "idle")

    def test_interrupted_attempt_never_mixes_saved_chunks_with_resume(self):
        report = self.create_approved_report()
        with patch.object(stage.approved_content.runtime.transport, "catalog", side_effect=self.current_catalog):
            run_id = stage.create(report.id, self.admin)["item"]["id"]
            original = business_volume_files._save_file
            count = 0
            def interrupted(*args, **kwargs):
                nonlocal count
                count += 1
                if count == 2: raise RuntimeError("synthetic interruption")
                return original(*args, **kwargs)
            with patch.object(business_volume_files, "_save_file", side_effect=interrupted):
                failure = files.tick()
            self.assertEqual(failure["status"], "paused")
            row = m.AiBusinessFileRun.objects.get(pk=run_id)
            self.assertEqual((row.attempt, row.manifest_json), (1, "{}"))
            prior = m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=1).count()
            self.assertGreater(prior, 0)
            stage.control(run_id, "resume", row.version, self.admin)
            recovered = files.tick()
            self.assertEqual((recovered["status"], recovered["attempt"]), ("staged_unpublished", 2))
            row.refresh_from_db()
            self.assertEqual(m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=1).count(), prior)
            self.assertTrue(m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=2).exists())
            stage._verify_staged(row, self.admin, lambda *args, **kwargs: None)

    def test_current_approval_and_owner_required_for_internal_queue(self):
        report = self.five_completed()
        with patch.object(stage.approved_content.runtime.transport, "catalog", side_effect=self.current_catalog):
            with self.assertRaises(AiError): stage.create(report.id, self.admin)
            self.approved(report)
            with self.assertRaises(AiError): stage.create(report.id, self.viewer)
            created = stage.create(report.id, self.admin)
            run_id = created["item"]["id"]
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            result = files.tick()
            self.assertEqual(result["status"], "paused")
            self.assertEqual(m.AiBusinessVolumeChunk.objects.filter(run_id=run_id).count(), 0)
