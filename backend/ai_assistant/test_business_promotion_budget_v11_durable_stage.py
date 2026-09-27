"""Isolated PostgreSQL real-role checks for 0066 staged-only candidate.

The class installs the candidate only when 0066 is not yet migrated. The main
task runs these tests serially against its disposable test database.
"""
from contextlib import contextmanager
from importlib import import_module
import json
import secrets
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.apps import apps
from django import test as djtest
from django.db import DatabaseError, connection
from django.utils import timezone

from business_analysis import volume_delivery
from . import business_files as files
from . import business_promotion_budget_v11_durable_stage as stage
from . import business_promotion_budget_v11_stage_sql as sql
from . import business_volume_files, models as m
from . import test_business_promotion_budget_v10_stage as fixture
from .control_models import AiWriteAuthority
from .database_contract import provision
from .policy import AiError, mutation


@contextmanager
def writer_session():
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION AUTHORIZATION teruisi_ai_writer")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test",
    AI_PROMOTION_BUDGET_V11_STAGE_CANDIDATE_ENABLED=True)
class BudgetV11DurableStageTests(djtest.TransactionTestCase):
    user = fixture.PromotionBudgetV10StageTests.user
    call = fixture.PromotionBudgetV10StageTests.call
    collect_body = fixture.PromotionBudgetV10StageTests.collect_body
    bundle = fixture.PromotionBudgetV10StageTests.bundle
    input_for = fixture.PromotionBudgetV10StageTests.input_for
    insert = fixture.PromotionBudgetV10StageTests.insert
    seed = fixture.PromotionBudgetV10StageTests.seed
    setUp = fixture.PromotionBudgetV10StageTests.setUp
    request_body = fixture.PromotionBudgetV10StageTests.request_body
    current_catalog = fixture.PromotionBudgetV10StageTests.current_catalog
    create_fixed_report = fixture.PromotionBudgetV10StageTests.create_fixed_report
    base = fixture.PromotionBudgetV10StageTests.base
    read = fixture.PromotionBudgetV10StageTests.read
    append = fixture.PromotionBudgetV10StageTests.append
    package = fixture.PromotionBudgetV10StageTests.package
    promotion = fixture.PromotionBudgetV10StageTests.promotion
    complete = fixture.PromotionBudgetV10StageTests.complete
    running_job = fixture.PromotionBudgetV10StageTests.running_job
    five_completed = fixture.PromotionBudgetV10StageTests.five_completed
    approved = fixture.PromotionBudgetV10StageTests.approved
    _complete_budget_report = fixture.PromotionBudgetV10StageTests._complete_budget_report
    databases = {"default"}
    _installed_candidate = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        connection.ensure_connection()
        provision(connection.connection, secrets.token_hex(32), secrets.token_hex(32))
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regprocedure(%s)", [sql.STAGE_SIGNATURE])
            installed = cursor.fetchone()[0] is not None
        if not installed:
            sql.install(apps, SimpleNamespace(connection=connection))
            cls._installed_candidate = True

    @classmethod
    def tearDownClass(cls):
        try:
            if cls._installed_candidate:
                sql.uninstall(apps, SimpleNamespace(connection=connection))
        finally:
            super().tearDownClass()

    @contextmanager
    def _writer(self):
        authority = AiWriteAuthority.objects.get(id=1)
        if authority.status != "postgres":
            epoch = uuid4()
            AiWriteAuthority.objects.filter(id=1, status=authority.status).update(
                status="postgres", authority_epoch=epoch,
                cutover_id="v11-stage-isolated",
                migration_verify_run_id="v11-stage-isolated",
                activated_at=timezone.now())
            authority.refresh_from_db()
        # A PostgreSQL authority is terminal. A later writer session in this
        # same test must reuse its exact adopted identity, never rewrite it.
        self.assertEqual(authority.status, "postgres")
        with self.settings(DJANGO_PROCESS_ROLE="ai_writer",
                AI_WRITE_AUTHORITY_EPOCH=str(authority.authority_epoch),
                AI_WRITE_CUTOVER_ID=authority.cutover_id), writer_session():
            yield

    def _stage(self, report):
        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog), patch(
                "ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            with self._writer():
                created = stage.create(report.id, self.admin)
                self.assertFalse(created["replayed"])
                result = files.tick()
                self.assertEqual(result["status"], "staged_unpublished", result)
                model.assert_not_called()
                remote.assert_not_called()
        row = m.AiBusinessFileRun.objects.get(pk=created["item"]["id"])
        self.assertEqual((row.renderer_version, row.status, row.error_code),
            (11, "paused", "renderer_unpublished"))
        self.assertEqual(json.loads(row.progress_json)["stage"],
            "staged_unpublished")
        return row

    def test_writer_stages_fixed_budget_but_ready_and_download_stay_closed(self):
        report = self._complete_budget_report()
        row = self._stage(report)
        compact = volume_delivery.validate(json.loads(row.manifest_json),
            binding_digest=row.binding_digest, attempt=row.attempt,
            draft=False, renderer_version=11)
        self.assertEqual(m.AiBusinessVolumeChunk.objects.filter(run=row,
            attempt=row.attempt).count(), sum(item["chunkCount"] for item in
            [*compact["files"], compact["manifestFile"]]))
        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog):
            with self._writer():
                stage._verify_staged(row, self.admin, lambda *args, **kwargs: None)
        with self.assertRaises(AiError):
            files.control(row.id, {"action": "resume",
                "expectedVersion": row.version}, self.admin)
        with self.assertRaises(AiError):
            business_volume_files.chunk(row.id, "1", "html",
                {"sequence": "1"}, self.admin)
        with self._writer():
            with self.assertRaisesRegex(DatabaseError,
                    "ai_budget_v11_ready_unpublished"), mutation(self.admin):
                m.AiBusinessFileRun.objects.filter(pk=row.pk).update(
                    status="ready", error_code="", version=row.version + 1,
                    progress_json='{"stage":"ready"}')
        row.refresh_from_db()
        self.assertEqual(row.status, "paused")

    def test_real_roles_and_missing_chunks_cannot_forge_stage(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE'),"
                "has_function_privilege(%s,%s,'EXECUTE')",
                ["teruisi_ai_writer", sql.STAGE_SIGNATURE,
                 "teruisi_ai_reader", sql.STAGE_SIGNATURE])
            self.assertEqual(cursor.fetchone(), (True, False))
        report = self.five_completed(promotion_reference=True)
        self.approved(report)
        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog), self._writer():
            created = stage.create(report.id, self.admin)
            run_id = created["item"]["id"]
            row = m.AiBusinessFileRun.objects.get(pk=run_id)
            with mutation(self.admin):
                row.status, row.attempt, row.version = "building", 1, 2
                row.save()
            with self.assertRaises(DatabaseError), mutation(self.admin):
                m.AiBusinessFileRun.objects.filter(pk=run_id).update(
                    status="paused", error_code="renderer_unpublished",
                    version=3, progress_json='{"stage":"staged_unpublished"}')
        self.assertFalse(m.AiBusinessFileRun.objects.filter(pk=run_id,
            status="paused", error_code="renderer_unpublished").exists())

    def test_reverse_rejects_any_v11_row(self):
        report = self.five_completed(promotion_reference=True)
        self.approved(report)
        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog), self._writer():
            created = stage.create(report.id, self.admin)
        with self.assertRaisesRegex(RuntimeError, "renderer 11"):
            sql.uninstall(apps, SimpleNamespace(connection=connection))
        self.assertTrue(m.AiBusinessFileRun.objects.filter(
            pk=created["item"]["id"]).exists())
