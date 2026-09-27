"""Real v2 Agent queue and sealed facts through the durable renderer-4/6 ledger."""
import base64
import hashlib
import io
import json
from unittest.mock import patch
from django.db import DatabaseError, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from sales.tests.factories import signed_headers, TEST_SECRET
from . import business_evidence, business_files as files, business_volume_files as volumes, models as m
from . import test_business_v2_parallel_acceptance as fixtures
from .policy import AiError, mutation


class OutputBudgetTests(SimpleTestCase):
    def test_shared_high_water_charges_seeks_but_not_overwrites(self):
        budget = volumes.OutputBudget(12)
        one, two = budget.stream(io.BytesIO()), budget.stream(io.BytesIO())
        one.write(b"1234")
        one.seek(0)
        one.write(b"ab")
        self.assertEqual(budget.used, 4)
        two.seek(6)
        two.write(b"xy")
        self.assertEqual(budget.used, 12)
        with self.assertRaises(AiError):
            one.seek(9)
            one.write(b"z")
        self.assertEqual(one.raw.getvalue(), b"ab34")


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessVolumeFileTests(TransactionTestCase):
    user = fixtures.BusinessV2ParallelAcceptanceTests.user

    def setUp(self):
        fixtures.BusinessV2ParallelAcceptanceTests.setUp(self)
        fixtures.BusinessV2ParallelAcceptanceTests.test_five_agents_prove_own_directory_then_real_human_review_and_content(self)
        self.report = m.AiReportRun.objects.select_related("workflow").get()

    def start(self):
        return files.create(self.report.id, {"deliveryMode": "volumes",
            "expectedPrincipalKey": business_evidence.principal_key(self.admin)}, self.admin)["item"]["id"]

    def test_actual_complete_delivery_download_and_permissions(self):
        run_id = self.start()
        self.assertEqual(self.start(), run_id)
        created = files.get(run_id, self.admin)
        self.assertEqual(created.binding_digest, files.binding(self.report, self.admin, created.draft, renderer_version=6))
        with patch("ai_assistant.transport.execute_tool") as remote, patch("ai_assistant.provider.turn") as provider:
            result = files.tick()
            self.assertEqual(result["status"], "ready", result)
            remote.assert_not_called()
            provider.assert_not_called()
        row = files.get(run_id, self.admin)
        compact = json.loads(row.manifest_json)
        self.assertEqual(compact["schemaVersion"], "business-file-delivery-v2")
        self.assertEqual(m.AiBusinessFileChunk.objects.count(), 0)
        total = 0
        for item in [*compact["files"], compact["manifestFile"]]:
            raw = b"".join(base64.b64decode(volumes.chunk(run_id, str(item["volumeIndex"]), item["format"],
                {"sequence": str(i)}, self.admin)["base64"]) for i in range(1, item["chunkCount"]+1))
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item["sha256"])
            self.assertEqual(len(raw), item["bytes"])
            total += len(raw)
            if item["format"] == "json":
                self.assertEqual(json.loads(raw)["reportId"], self.report.id)
        self.assertEqual(row.stored_bytes, total)
        with self.assertRaises(AiError):
            files.chunk(run_id, "html", {"sequence": "1"}, self.admin)
        with self.assertRaises(AiError):
            volumes.chunk(run_id, "0", "html", {"sequence": "1"}, self.admin)
        other = self.user("other-volume@example.invalid", "admin", None)
        with self.assertRaises(AiError):
            volumes.chunk(run_id, "0", "json", {"sequence": "1"}, other)
        url = f"/api/ai/business-files/{run_id}/volumes/0/chunks/json?sequence=1"
        with override_settings(DJANGO_PROCESS_ROLE="ai_reader", DJANGO_INTERNAL_SECRET=TEST_SECRET), patch("ai_assistant.views.authority"), patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}):
            response = self.client.get(url, headers=signed_headers(url, email=self.admin.email))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["format"], "json")

    def test_complete_staged_attempt_recovers_without_rebuild(self):
        self._staged_recovery(self.start())

    def test_legacy_v4_staged_recovery_keeps_bytes_and_version(self):
        with mutation(self.admin):
            old = m.AiBusinessFileRun.objects.create(id="legacy-staged-v4", report=self.report,
                owner_email=self.admin.email, renderer_version=4,
                binding_digest=files.binding(self.report, self.admin, False, renderer_version=4))
        self._staged_recovery(old.id)
        row = files.get(old.id, self.admin)
        self.assertEqual(row.renderer_version, 4)
        saved = list(m.AiBusinessVolumeChunk.objects.filter(run=row).values_list("id", "content_digest"))
        compact = json.loads(row.manifest_json)
        for item in [*compact["files"], compact["manifestFile"]]:
            raw = b"".join(base64.b64decode(volumes.chunk(row.id, str(item["volumeIndex"]), item["format"],
                {"sequence":str(i)}, self.admin)["base64"]) for i in range(1,item["chunkCount"]+1))
            self.assertEqual(hashlib.sha256(raw).hexdigest(),item["sha256"])
        self.assertNotEqual(self.start(), old.id)
        self.assertEqual(saved,list(m.AiBusinessVolumeChunk.objects.filter(run=row).values_list("id", "content_digest")))

    def _staged_recovery(self, run_id):
        original = files.audit
        def fail(row, principal, action):
            if action == "volumes_ready":
                raise RuntimeError("synthetic audit failure")
            return original(row, principal, action)
        with patch("ai_assistant.business_files.audit", side_effect=fail):
            self.assertEqual(files.tick()["status"], "paused")
        row = files.get(run_id, self.admin)
        self.assertNotEqual(row.manifest_json, "{}")
        count = m.AiBusinessVolumeChunk.objects.count()
        files.control(run_id, {"expectedVersion": row.version, "action": "resume"}, self.admin)
        with patch("ai_assistant.business_export.prepare_volumes", side_effect=AssertionError("no rebuild")):
            result = files.tick()
        self.assertEqual(result["status"], "ready", result)
        self.assertEqual(files.get(run_id, self.admin).attempt, 1)
        self.assertEqual(m.AiBusinessVolumeChunk.objects.count(), count)

    def test_partial_attempt_rebuilds_whole_batch_and_retains_old_bytes(self):
        run_id = self.start()
        original = volumes._save_file
        count = 0
        def stop(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("synthetic interruption after first file")
            return original(*args, **kwargs)
        with patch("ai_assistant.business_volume_files._save_file", side_effect=stop):
            self.assertEqual(files.tick()["status"], "paused")
        row = files.get(run_id, self.admin)
        self.assertEqual(row.manifest_json, "{}")
        old = list(m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=1).values_list("id", "content_digest"))
        self.assertTrue(old)
        files.control(run_id, {"expectedVersion": row.version, "action": "resume"}, self.admin)
        result = files.tick()
        self.assertEqual(result["status"], "ready", result)
        row.refresh_from_db()
        self.assertEqual(row.attempt, 2)
        self.assertEqual(list(m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=1).values_list("id", "content_digest")), old)
        self.assertEqual(row.stored_bytes, sum(len(p.content) for p in m.AiBusinessVolumeChunk.objects.filter(run=row)))

    def test_cancel_during_build_and_audit_failure_leave_no_late_chunks(self):
        run_id = self.start()
        original = volumes._save_file
        def cancel(row, principal, state, descriptor, path, checkpoint):
            current = files.get(run_id, principal)
            files.control(run_id, {"expectedVersion": current.version, "action": "cancel"}, principal)
            return original(row, principal, state, descriptor, path, checkpoint)
        with patch("ai_assistant.business_volume_files._save_file", side_effect=cancel):
            self.assertEqual(files.tick()["status"], "superseded")
        self.assertEqual(files.get(run_id, self.admin).status, "cancelled")
        self.assertEqual(m.AiBusinessVolumeChunk.objects.count(), 0)

    def test_chunk_audit_failure_rolls_back_bytes_and_unready_is_not_downloadable(self):
        run_id = self.start()
        original = files.audit
        def fail(row, principal, action):
            if action == "volume_chunks_saved":
                raise RuntimeError("synthetic chunk audit failure")
            return original(row, principal, action)
        with patch("ai_assistant.business_files.audit", side_effect=fail):
            self.assertEqual(files.tick()["status"], "paused")
        self.assertEqual(m.AiBusinessVolumeChunk.objects.count(), 0)
        self.assertEqual(files.get(run_id, self.admin).stored_bytes, 0)
        with self.assertRaises(AiError):
            volumes.chunk(run_id, "1", "xlsx", {"sequence": "1"}, self.admin)
        with self.assertRaises(DatabaseError), transaction.atomic(), mutation(self.admin):
            row = m.AiBusinessFileRun.objects.get(pk=run_id)
            row.status, row.version = "ready", row.version+1
            row.save()

    def test_account_switch_and_quota_are_fail_closed(self):
        with self.assertRaises(AiError):
            files.create(self.report.id, {"deliveryMode": "volumes", "expectedPrincipalKey": "wrong"}, self.admin)
        run_id = self.start()
        with patch("ai_assistant.business_files.RUN_BYTES", 1):
            self.assertEqual(files.tick()["status"], "paused")
        self.assertEqual(m.AiBusinessVolumeChunk.objects.count(), 0)
        self.assertEqual(files.get(run_id, self.admin).stored_bytes, 0)

    def test_permission_revoked_after_render_rejects_first_persistent_chunk(self):
        from access_control.models import AppUser
        run_id = self.start()
        original = volumes._save_file
        def revoke(*args, **kwargs):
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return original(*args, **kwargs)
        with patch("ai_assistant.business_volume_files._save_file", side_effect=revoke):
            result = files.tick()
        self.assertEqual(result["status"], "paused", result)
        self.assertEqual(result["errorCode"], "access_denied")
        self.assertEqual(m.AiBusinessVolumeChunk.objects.count(), 0)
        self.assertEqual(m.AiBusinessFileRun.objects.get(pk=run_id).stored_bytes, 0)
