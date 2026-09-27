import base64
from copy import deepcopy
import hashlib
import json
from unittest.mock import patch
from django.db import connection, DatabaseError, transaction
from django.test import TestCase, override_settings
from sales.tests.factories import signed_headers, TEST_SECRET

from . import business_files as files, models as m, test_business_reports as report_tests
from .policy import AiError, mutation


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessFileTests(TestCase):
    user = report_tests.BusinessReportTests.user
    call = report_tests.BusinessReportTests.call
    execute = report_tests.BusinessReportTests.execute
    collect = report_tests.BusinessReportTests.collect
    create = report_tests.BusinessReportTests.create

    def setUp(self):
        report_tests.BusinessReportTests.setUp(self)
        # Produce the actual synthetic five-Agent workflow, persisted evidence
        # receipts and human approval; do not fabricate completed DB states.
        report_tests.BusinessReportTests.test_specialists_and_reviewer_use_real_queue_and_same_evidence(self)
        self.report = m.AiReportRun.objects.select_related("workflow").get()

    def start(self):
        return files.create(self.report.id, {}, self.admin)["item"]["id"]

    def test_actual_pair_persists_and_downloads_with_no_new_source_or_model_calls(self):
        response = self.call(f"/api/ai/reports/{self.report.id}/files", {}, principal=self.admin)
        self.assertEqual(response.status_code, 200, response.content)
        run_id = response.json()["item"]["id"]
        self.assertEqual(self.start(), run_id)
        with self.assertRaises(AiError):
            files.chunk(run_id, "xlsx", {"sequence": "1"}, self.admin)
        with patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as source:
            result = files.tick()
            self.assertEqual(result["status"], "ready", result)
            model.assert_not_called()
            source.assert_not_called()
        row = files.get(run_id, self.admin)
        manifest = files.mapping(row)["manifest"]
        for format in ("html", "xlsx"):
            parts = [files.chunk(run_id, format, {"sequence": str(i)}, self.admin) for i in range(1, manifest["files"][format]["chunkCount"]+1)]
            raw = b"".join(base64.b64decode(p["base64"]) for p in parts)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest["files"][format]["sha256"])
            self.assertTrue(raw.startswith(b"PK" if format == "xlsx" else b"<!doctype html>"))
        self.assertEqual(files.tick()["status"], "idle")
        self.assertEqual(len(files.listing(self.report.id, self.admin)["items"]), 1)
        self.assertIsNone(files.listing(self.report.id, self.admin)["items"][0]["manifest"])
        with override_settings(DJANGO_PROCESS_ROLE="ai_reader"), patch("ai_assistant.views.authority"):
            url = f"/api/ai/business-files/{run_id}/chunks/xlsx?sequence=1"
            with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}), override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET):
                result = self.client.get(url, headers=signed_headers(url, email=self.admin.email))
            self.assertEqual(result.status_code, 200, result.content)
            denied = self.call(f"/api/ai/business-files/{run_id}/control", {"expectedVersion": row.version, "action": "cancel"}, principal=self.admin)
            self.assertEqual(denied.status_code, 403)

    def test_cancel_during_build_rejects_late_bytes(self):
        run_id = self.start()
        def cancelled(report, principal, xlsx, html, **kwargs):
            current = files.get(run_id, principal)
            files.control(run_id, {"expectedVersion": current.version, "action": "cancel"}, principal)
            xlsx.write(b"synthetic")
            html.write(b"synthetic")
            return {"tables": []}
        with patch("ai_assistant.business_export.build", side_effect=cancelled):
            self.assertEqual(files.tick()["status"], "superseded")
        self.assertEqual(files.get(run_id, self.admin).status, "cancelled")
        self.assertEqual(m.AiBusinessFileChunk.objects.count(), 0)

    def test_old_and_new_renderer_tasks_remain_distinct_and_immutable(self):
        with mutation(self.admin):
            old = m.AiBusinessFileRun.objects.create(id="legacy-files", report=self.report, owner_email=self.admin.email,
                binding_digest=files.binding(self.report, self.admin, False, renderer_version=1), renderer_version=1)
        new = files.get(self.start(), self.admin)
        self.assertEqual(new.renderer_version, 5)
        self.assertNotEqual(new.id, old.id)
        self.assertEqual(self.start(), new.id)
        from importlib import import_module
        from django.apps import apps
        reverse = import_module("ai_assistant.migrations.0025_business_file_opc").uninstall
        with self.assertRaisesMessage(RuntimeError, "禁止逆迁移"):
            reverse(apps, None)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessFileRun.objects.filter(pk=old.pk).update(renderer_version=2)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessFileRun.objects.create(id="unsupported-files", report=self.report, owner_email=self.admin.email,
                binding_digest=old.binding_digest, renderer_version=7)
        seen = []
        original = files.business_export.build
        def observe(*args, **kwargs):
            self.assertEqual(kwargs["renderer_version"], m.AiBusinessFileRun.objects.get(status="building").renderer_version)
            seen.append(kwargs["renderer_version"])
            return original(*args, **kwargs)
        with patch("ai_assistant.business_export.build", side_effect=observe):
            self.assertEqual(files.tick()["status"], "ready")
            self.assertEqual(files.tick()["status"], "ready")
        # Creation timestamps may tie; queue order is not renderer-version order.
        self.assertCountEqual(seen, [1, 5])
        with mutation(self.admin):
            m.AiBusinessFileRun.objects.create(id="offline-files", report=self.report, owner_email=self.admin.email,
                binding_digest=old.binding_digest, renderer_version=2)
        with patch("ai_assistant.business_export.build", side_effect=observe):
            self.assertEqual(files.tick()["status"], "ready")
        self.assertCountEqual(seen, [1, 2, 5])
        with mutation(self.admin):
            m.AiBusinessFileRun.objects.create(id="old-native-files", report=self.report, owner_email=self.admin.email,
                binding_digest=files.binding(self.report, self.admin, False, renderer_version=3), renderer_version=3)
        with patch("ai_assistant.business_export.build", side_effect=observe):
            self.assertEqual(files.tick()["status"], "ready")
        self.assertCountEqual(seen, [1, 2, 3, 5])
        for item in files.listing(self.report.id, self.admin)["items"]:
            row = files.get(item["id"], self.admin)
            manifest = json.loads(row.manifest_json)
            for kind in ("html", "xlsx"):
                raw = b"".join(base64.b64decode(files.chunk(row.id, kind, {"sequence":str(i)}, self.admin)["base64"])
                    for i in range(1, manifest["files"][kind]["chunkCount"]+1))
                self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest["files"][kind]["sha256"])


    def test_ready_audit_failure_resumes_staged_files_without_rebuilding(self):
        self._staged_recovery(self.start())

    def test_legacy_v3_staged_recovery_keeps_bytes_and_version(self):
        with mutation(self.admin):
            old = m.AiBusinessFileRun.objects.create(id="legacy-staged-v3", report=self.report,
                owner_email=self.admin.email, renderer_version=3,
                binding_digest=files.binding(self.report, self.admin, False, renderer_version=3))
        self._staged_recovery(old.id)
        row = files.get(old.id, self.admin)
        self.assertEqual(row.renderer_version, 3)
        saved = list(m.AiBusinessFileChunk.objects.filter(run=row).values_list("id", "content_digest"))
        self.assertNotEqual(self.start(), old.id)
        self.assertEqual(saved, list(m.AiBusinessFileChunk.objects.filter(run=row).values_list("id", "content_digest")))

    def _staged_recovery(self, run_id):
        original = files.audit
        def fail_ready(row, principal, action):
            if action == "ready":
                raise RuntimeError("synthetic audit unavailable")
            return original(row, principal, action)
        with patch("ai_assistant.business_files.audit", side_effect=fail_ready):
            self.assertEqual(files.tick()["status"], "paused")
        row = files.get(run_id, self.admin)
        self.assertNotEqual(row.manifest_json, "{}")
        count = m.AiBusinessFileChunk.objects.count()
        files.control(row.id, {"expectedVersion": row.version, "action": "resume"}, self.admin)
        with patch("ai_assistant.business_export.build") as build:
            self.assertEqual(files.tick()["status"], "ready")
            build.assert_not_called()
        self.assertEqual(m.AiBusinessFileChunk.objects.count(), count)
        self.assertEqual(files.get(run_id, self.admin).attempt, 1)

    def test_chunk_audit_failure_rolls_back_batch_and_storage_count(self):
        run_id = self.start()
        original = files.audit
        def fail_chunks(row, principal, action):
            if action == "chunks_saved":
                raise RuntimeError("synthetic chunk audit unavailable")
            return original(row, principal, action)
        with patch("ai_assistant.business_files.audit", side_effect=fail_chunks):
            self.assertEqual(files.tick()["status"], "paused")
        self.assertEqual(m.AiBusinessFileChunk.objects.count(), 0)
        self.assertEqual(files.get(run_id, self.admin).stored_bytes, 0)

    def test_large_payload_transport_owner_binding_and_corruption(self):
        run_id = self.start()
        payload = b"synthetic-only-" * 800000  # > old 9 MiB relay; transport fixture, not an XLSX validation.
        def render(report, principal, xlsx, html, **kwargs):
            xlsx.write(payload)
            html.write(payload)
            return {"tables": []}
        with patch("ai_assistant.business_export.build", side_effect=render):
            self.assertEqual(files.tick()["status"], "ready")
        row = files.get(run_id, self.admin)
        expected = json.loads(row.manifest_json)["files"]["xlsx"]
        self.assertGreater(expected["bytes"], 9*1024*1024)
        self.assertGreater(expected["chunkCount"], 16)
        for principal in (self.viewer, self.owner, self.user("other@example.test", "admin", None)):
            with self.assertRaises(AiError):
                files.chunk(run_id, "xlsx", {"sequence": "1"}, principal)
        with self.assertRaises(DatabaseError), transaction.atomic(), mutation(self.admin):
            m.AiBusinessFileChunk.objects.filter(run_id=run_id).update(content=b"changed")
        record = m.AiBusinessFileChunk.objects.filter(run_id=run_id, format="xlsx", sequence=1).first()
        record.content = b"corrupted read projection"
        with patch("ai_assistant.business_files.m.AiBusinessFileChunk.objects.filter") as query:
            query.return_value.first.return_value = record
            with self.assertRaises(AiError):
                files.chunk(run_id, "xlsx", {"sequence": "1"}, self.admin)

    def test_permission_loss_quota_and_incomplete_publication_fail_closed(self):
        run_id = self.start()
        with self.assertRaises(DatabaseError), transaction.atomic(), mutation(self.admin):
            m.AiBusinessFileRun.objects.filter(pk=run_id).update(status="ready", version=2)
        with patch("ai_assistant.business_files.OWNER_BYTES", 1):
            self.assertEqual(files.tick()["status"], "paused")
        row = files.get(run_id, self.admin)
        self.assertEqual(row.stored_bytes, 0)
        files.control(run_id, {"expectedVersion": row.version, "action": "resume"}, self.admin)
        with patch("ai_assistant.business_files.workflows.background", side_effect=AiError("disabled", "access_denied", 403)), patch("ai_assistant.business_export.build") as build:
            result = files.tick()
            self.assertEqual(result["status"], "paused")
            self.assertEqual(result["errorCode"], "access_denied")
            build.assert_not_called()

    def test_overlapping_scheduler_is_excluded_by_another_database_session(self):
        self.start()
        other = connection.copy(alias="business-file-lock-test")
        try:
            with other.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(%s,%s)", [192839, 7303])
                self.assertEqual(files.tick(), {"status": "file_builder_busy"})
                cursor.execute("SELECT pg_advisory_unlock(%s,%s)", [192839, 7303])
        finally:
            other.close()
