"""Renderer-10 storage lane remains staged/unpublished under PostgreSQL."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django import test as djtest
from django.db import DatabaseError, connection, transaction

from business_analysis import volume_delivery
from . import business_files as files
from . import business_promotion_budget_v10_stage as stage
from . import models as m
from . import test_business_promotion_budget_v10_volumes as fixture
from .health import _verify_promotion_trial_file_guard
from .policy import AiError, mutation


def migration():
    from importlib import import_module
    return import_module(
        "ai_assistant.migrations.0054_business_promotion_budget_file_staging")

@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionBudgetV10StageTests(djtest.TransactionTestCase):
    user = fixture.PromotionBudgetV10VolumeTests.user
    call = fixture.PromotionBudgetV10VolumeTests.call
    collect_body = fixture.PromotionBudgetV10VolumeTests.collect_body
    bundle = fixture.PromotionBudgetV10VolumeTests.bundle
    input_for = fixture.PromotionBudgetV10VolumeTests.input_for
    insert = fixture.PromotionBudgetV10VolumeTests.insert
    seed = fixture.PromotionBudgetV10VolumeTests.seed
    setUp = fixture.PromotionBudgetV10VolumeTests.setUp
    request_body = fixture.PromotionBudgetV10VolumeTests.request_body
    current_catalog = fixture.PromotionBudgetV10VolumeTests.current_catalog
    create_fixed_report = fixture.PromotionBudgetV10VolumeTests.create_fixed_report
    base = fixture.PromotionBudgetV10VolumeTests.base
    read = fixture.PromotionBudgetV10VolumeTests.read
    append = fixture.PromotionBudgetV10VolumeTests.append
    package = fixture.PromotionBudgetV10VolumeTests.package
    promotion = fixture.PromotionBudgetV10VolumeTests.promotion
    complete = fixture.PromotionBudgetV10VolumeTests.complete
    running_job = fixture.PromotionBudgetV10VolumeTests.running_job
    five_completed = fixture.PromotionBudgetV10VolumeTests.five_completed
    approved = fixture.PromotionBudgetV10VolumeTests.approved
    _complete_budget_report = fixture.PromotionBudgetV10VolumeTests._complete_budget_report

    def test_v10_catalog_rejects_budget_parent_acl_drift(self):
        with transaction.atomic(), connection.cursor() as cursor:
            # This isolated fixture creates the writer role after migrations;
            # the protected production provisioner grants these helpers first.
            cursor.execute("SELECT to_regrole('teruisi_ai_writer')")
            if cursor.fetchone()[0] is None:
                cursor.execute("CREATE ROLE teruisi_ai_writer NOLOGIN NOINHERIT")
            for signature in (
                "public.ai_business_promotion_trial_parent_requirements(text,text,text)",
                "public.ai_business_promotion_trial_ready_requirements(text)",
                "public.ai_business_promotion_budget_parent_requirements(text,text,text)",
            ):
                cursor.execute("GRANT EXECUTE ON FUNCTION " + signature +
                    " TO teruisi_ai_writer")
            _verify_promotion_trial_file_guard(cursor,
                budget_stage_enabled=True)
            cursor.execute("REVOKE EXECUTE ON FUNCTION "
                "public.ai_business_promotion_budget_parent_requirements("
                "text,text,text) FROM teruisi_ai_writer")
            with self.assertRaisesRegex(ValueError, "function ACL drift"):
                _verify_promotion_trial_file_guard(cursor,
                    budget_stage_enabled=True)
            transaction.set_rollback(True)

    def _stage(self, report):
        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog), patch(
                "ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            created = stage.create(report.id, self.admin)
            self.assertFalse(created["replayed"])
            run_id = created["item"]["id"]
            result = files.tick()
            self.assertEqual(result["status"], "staged_unpublished", result)
            model.assert_not_called()
            remote.assert_not_called()
        row = m.AiBusinessFileRun.objects.get(pk=run_id)
        self.assertEqual((row.renderer_version, row.status,
            row.error_code), (10, "paused", "renderer_unpublished"))
        self.assertEqual(json.loads(row.progress_json)["stage"], "staged_unpublished")
        self.assertGreater(row.stored_bytes, 0)
        compact = volume_delivery.validate(json.loads(row.manifest_json),
            binding_digest=row.binding_digest, attempt=row.attempt,
            draft=False, renderer_version=10)
        self.assertEqual(m.AiBusinessVolumeChunk.objects.filter(run=row,
            attempt=row.attempt).count(), sum(item["chunkCount"] for item in
            [*compact["files"], compact["manifestFile"]]))
        return row

    def test_no_budget_stages_complete_bytes_but_no_route_or_ready(self):
        report = self.five_completed(promotion_reference=True)
        self.approved(report)
        row = self._stage(report)
        with self.assertRaises(AiError):
            files.control(row.id, {"action": "resume",
                "expectedVersion": row.version}, self.admin)
        with self.assertRaises(AiError):
            files.chunk(row.id, "html", {"sequence": "1"}, self.admin)
        with self.assertRaisesRegex(DatabaseError,
                "ai_promotion_budget_renderer_unpublished"), mutation(self.admin):
            m.AiBusinessFileRun.objects.filter(pk=row.pk).update(
                status="ready", error_code="", version=row.version + 1,
                progress_json='{"stage":"ready"}')
        row.refresh_from_db()
        self.assertEqual(row.status, "paused")
        with self.assertRaisesRegex(RuntimeError, "renderer 10"):
            migration().uninstall(apps, SimpleNamespace(connection=connection))

    def test_actual_fixed_budget_stages_only_and_keeps_native_proof(self):
        report = self._complete_budget_report()
        row = self._stage(report)
        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog):
            stage._verify_staged(row, self.admin, lambda *args, **kwargs: None)
        compact = json.loads(row.manifest_json)
        self.assertEqual(compact["rendererVersion"], 10)
        self.assertNotEqual(compact["manifestFile"]["sha256"], "0" * 64)

    def test_unapproved_cannot_create_and_old_nine_semantics_remain(self):
        report = self.five_completed()
        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog):
            with self.assertRaises(AiError):
                stage.create(report.id, self.admin)
        self.assertFalse(m.AiBusinessFileRun.objects.filter(
            renderer_version=10).exists())
        self.assertEqual(m.AiBusinessFileRun.objects.filter(
            renderer_version=9).count(), 0)

    def test_empty_reverse_restores_frozen_nine_functions_then_reinstalls(self):
        sql = migration()
        editor = SimpleNamespace(connection=connection)
        with transaction.atomic():
            sql.uninstall(apps, editor)
            for original in sql.OLD_SQL:
                name = original.split("FUNCTION ", 1)[1].split("(", 1)[0]
                with connection.cursor() as cursor:
                    cursor.execute("SELECT prosrc FROM pg_proc WHERE proname=%s",
                        [name.removeprefix("public.")])
                    self.assertEqual(cursor.fetchone()[0],
                        original.split("$$", 2)[1])
            sql.install(apps, editor)
