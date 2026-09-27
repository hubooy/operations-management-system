"""Renderer-9 PostgreSQL storage boundary; no publisher is registered here."""
from hashlib import sha256
from importlib import import_module
import json
from types import SimpleNamespace
import unittest

from django.apps import apps
from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase, override_settings

from . import business_files as files, models as m
from . import test_business_promotion_volume_stage as fixtures, workflows
from .policy import mutation


def migration():
    return import_module(
        "ai_assistant.migrations.0046_business_promotion_trial_file_guard")


class PromotionTrialSqlContractTests(unittest.TestCase):
    def test_exact_predecessors_reserved_eight_and_distinct_nine_lane(self):
        sql = migration()
        self.assertEqual(len(sql.NEW_SQL), 5)
        self.assertIs(sql.CHUNK_GUARD, sql.OLD_SQL[0])
        self.assertEqual(sql.CHUNK_GUARD, sql.OLD_SQL[0])
        self.assertIn("parent_renderer NOT IN (4,6,7,9)", sql.MANIFEST_GUARD)
        self.assertIn("NEW.renderer_version IN (4,5,6,7,9)", sql.RUN_GUARD)
        self.assertIn("ai_business_promotion_ready_requirements(parent.id)",
                      sql.COMPLETE_GUARD)
        self.assertIn("ai_business_promotion_trial_ready_requirements(parent.id)",
                      sql.COMPLETE_GUARD)
        self.assertIn("parent.renderer_version<>9", sql.READY_REQUIREMENTS)
        self.assertIn("ai_promotion_trial_parent_changed_no_progress", sql.RUN_GUARD)
        self.assertNotIn("SECURITY DEFINER", sql.PARENT_REQUIREMENTS)
        self.assertNotIn("SECURITY DEFINER", sql.READY_REQUIREMENTS)
        self.assertNotIn("renderer_version IN (4,5,6,7,8,9)", sql.RUN_GUARD)
        for old, new in zip(sql.OLD_SQL, sql.NEW_SQL):
            self.assertEqual(old.split("FUNCTION ", 1)[1].split("(", 1)[0],
                             new.split("FUNCTION ", 1)[1].split("(", 1)[0])


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionTrialGuardTests(TransactionTestCase):
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
    complete_flow = fixtures.fixtures.PromotionApprovedContentTests.complete_flow

    def file(self, report, *, tag, version=9, draft=False):
        with mutation(self.admin):
            return m.AiBusinessFileRun.objects.create(
                id="promotion-trial-" + tag, report=report,
                owner_email=self.admin.email.lower(), renderer_version=version,
                draft=draft, binding_digest="a" * 64)

    def approved_report(self):
        report = self.five_completed(promotion_reference=True)
        self.approved(report)
        return report

    def test_eight_unapproved_wrong_profile_draft_and_binding_fail(self):
        report = self.approved_report()
        with self.assertRaises(DatabaseError), mutation(self.admin):
            self.file(report, tag="reserved", version=8)
        with self.assertRaisesRegex(DatabaseError, "ai_promotion_trial_draft_denied"), mutation(self.admin):
            self.file(report, tag="draft", draft=True)
        with self.assertRaises(DatabaseError), mutation(self.admin):
            m.AiBusinessFileRun.objects.create(
                id="promotion-trial-binding", report=report,
                owner_email=self.admin.email.lower(), renderer_version=9,
                binding_digest="wrong")
        row = self.file(report, tag="approved")
        self.assertEqual((row.renderer_version, row.draft, row.status), (9, False, "queued"))
        with self.assertRaisesRegex(DatabaseError, "ai_promotion_trial_binding_immutable"), mutation(self.admin):
            m.AiBusinessFileRun.objects.filter(pk=row.pk).update(renderer_version=7,
                version=row.version + 1)

    def test_approval_and_profile_are_required_before_insert(self):
        unapproved = self.five_completed(promotion_reference=True)
        with self.assertRaises(DatabaseError), mutation(self.admin):
            self.file(unapproved, tag="unapproved")
        self.approved(unapproved)
        self.file(unapproved, tag="approved-after-review")
        other = self.report  # the original sealed report, not the promotion profile
        self.assertNotEqual(json.loads(other.snapshot_json).get("executionProfile"),
                            "business-agent-screening-promotion-reference-v1")
        with self.assertRaisesRegex(DatabaseError, "ai_promotion_trial_parent_unapproved"), mutation(self.admin):
            self.file(other, tag="wrong-profile")

    def _staged(self, report):
        row = self.file(report, tag="staged")
        with mutation(self.admin):
            row.status, row.attempt, row.version = "building", 1, 2
            row.save(update_fields=["status", "attempt", "version"])
            descriptors = []
            total = 0
            for index, kind, raw in ((1, "html", b"h"), (1, "xlsx", b"x"),
                                     (0, "json", b"j")):
                m.AiBusinessVolumeChunk.objects.create(
                    id=f"promotion-trial-{kind}", run=row, attempt=1,
                    volume_index=index, format=kind, sequence=1, content=raw,
                    content_digest=sha256(raw).hexdigest())
                descriptors.append({"volumeIndex": index, "format": kind,
                    "bytes": len(raw), "sha256": sha256(raw).hexdigest(),
                    "chunkCount": 1})
                total += len(raw)
            row.manifest_json = json.dumps({"schemaVersion": "business-file-delivery-v2",
                "rendererVersion": 9, "bindingDigest": row.binding_digest,
                "attempt": 1, "draft": False, "volumeCount": 1,
                "files": descriptors[:2], "manifestFile": descriptors[2]},
                separators=(",", ":"), sort_keys=True)
            row.stored_bytes, row.version = total, 3
            row.save(update_fields=["stored_bytes", "manifest_json", "version"])
        return row

    def test_nine_ready_needs_frozen_stage_and_completed_approval(self):
        report = self.approved_report()
        row = self._staged(report)
        with self.assertRaisesRegex(DatabaseError, "ai_promotion_ready_requires_staged_attempt"), mutation(self.admin):
            m.AiBusinessFileRun.objects.filter(pk=row.pk).update(
                status="ready", version=4, progress_json='{"stage":"ready"}')
        with mutation(self.admin):
            row.status, row.error_code = "paused", "renderer_unpublished"
            row.progress_json, row.version = '{"stage":"staged_unpublished"}', 4
            row.save(update_fields=["status", "error_code", "progress_json", "version"])
        with self.assertRaisesRegex(DatabaseError, "ai_promotion_ready_parent_invalid"), mutation(self.admin):
            m.AiBusinessFileRun.objects.filter(pk=row.pk).update(
                status="ready", error_code="", progress_json='{"stage":"ready"}',
                version=5)
        self.complete_flow(report)
        with mutation(self.admin):
            m.AiBusinessFileRun.objects.filter(pk=row.pk).update(
                status="ready", error_code="", progress_json='{"stage":"ready"}',
                version=5)
        self.assertEqual(m.AiBusinessFileRun.objects.get(pk=row.pk).status, "ready")

    def test_old_seven_remains_staged_and_any_nine_row_blocks_reverse(self):
        report = self.approved_report()
        old = self.file(report, tag="old-seven", version=7)
        self.assertEqual(old.renderer_version, 7)
        row = self.file(report, tag="reverse")
        with mutation(self.admin):
            row.status, row.version = "cancelled", 2
            row.save(update_fields=["status", "version"])
        with self.assertRaisesRegex(RuntimeError, "renderer 9"):
            migration().uninstall(apps, SimpleNamespace(connection=connection))

    def test_cancelled_parent_allows_only_safe_file_convergence_and_next_queue(self):
        report = self.approved_report()
        row = self.file(report, tag="parent-cancelled")
        flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
        workflows.control(flow.id, {"expectedVersion": flow.version},
            self.admin, "cancel", workflow=True)
        with self.assertRaisesRegex(DatabaseError, "ai_promotion_trial_parent_changed_no_progress"), mutation(self.admin):
            m.AiBusinessFileRun.objects.filter(pk=row.pk).update(
                status="building", attempt=1, version=2)
        with self.assertRaisesRegex(DatabaseError, "ai_promotion_trial_parent_changed_no_progress"), mutation(self.admin):
            m.AiBusinessFileRun.objects.filter(pk=row.pk).update(
                status="cancelled", stored_bytes=1, version=2)
        self.assertEqual(files.control(row.id, {"expectedVersion": row.version,
            "action": "pause"}, self.admin)["item"]["status"], "paused")
        row.refresh_from_db()
        with self.assertRaisesRegex(DatabaseError, "ai_promotion_trial_parent_changed_no_progress"), mutation(self.admin):
            m.AiBusinessFileRun.objects.filter(pk=row.pk).update(
                status="queued", version=row.version + 1)
        self.assertEqual(files.control(row.id, {"expectedVersion": row.version,
            "action": "cancel"}, self.admin)["item"]["status"], "cancelled")
        self.assertEqual(files.tick()["status"], "idle")
        successor = self.approved_report()
        next_row = self.file(successor, tag="next-queue")
        self.assertEqual(list(m.AiBusinessFileRun.objects.filter(
            status="queued").values_list("id", flat=True)), [next_row.id])

    def test_empty_reverse_restores_exact_five_predecessor_bodies(self):
        sql = migration()
        editor = SimpleNamespace(connection=connection)
        with transaction.atomic():
            sql.uninstall(apps, editor)
            for original in sql.OLD_SQL:
                name = original.split("FUNCTION ", 1)[1].split("(", 1)[0]
                with connection.cursor() as cursor:
                    cursor.execute("SELECT prosrc FROM pg_proc WHERE proname=%s", [name])
                    self.assertEqual(cursor.fetchone()[0], original.split("$$")[1])
            sql.install(apps, editor)
