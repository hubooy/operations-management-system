"""Approved renderer-9 trial: signed opt-in, complete persisted volumes and guards."""
import base64
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

from django import test as djtest

from access_control.models import AppUser

from . import business_evidence, business_files as files
from . import business_promotion_trial_volume_stage as stage
from . import business_volume_files, business_promotion_trial_volume_download as download, models as m
from . import test_business_promotion_public_files as fixture
from .policy import AiError


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionTrialDeliveryTests(djtest.TransactionTestCase):
    user = fixture.PromotionPublicFileTests.user
    call = fixture.PromotionPublicFileTests.call
    collect_body = fixture.PromotionPublicFileTests.collect_body
    bundle = fixture.PromotionPublicFileTests.bundle
    input_for = fixture.PromotionPublicFileTests.input_for
    insert = fixture.PromotionPublicFileTests.insert
    seed = fixture.PromotionPublicFileTests.seed
    setUp = fixture.PromotionPublicFileTests.setUp
    request_body = fixture.PromotionPublicFileTests.request_body
    current_catalog = fixture.PromotionPublicFileTests.current_catalog
    create_fixed_report = fixture.PromotionPublicFileTests.create_fixed_report
    base = fixture.PromotionPublicFileTests.base
    read = fixture.PromotionPublicFileTests.read
    append = fixture.PromotionPublicFileTests.append
    package = fixture.PromotionPublicFileTests.package
    promotion = fixture.PromotionPublicFileTests.promotion
    complete = fixture.PromotionPublicFileTests.complete
    running_job = fixture.PromotionPublicFileTests.running_job
    five_completed = fixture.PromotionPublicFileTests.five_completed
    approved = fixture.PromotionPublicFileTests.approved
    complete_flow = fixture.PromotionPublicFileTests.complete_flow
    create_approved_report = fixture.PromotionPublicFileTests.create_approved_report
    _signed = fixture.PromotionPublicFileTests._signed

    def _body(self):
        return {"deliveryMode": "promotionTrialVolumes", "draft": False,
            "expectedPrincipalKey": business_evidence.principal_key(self.admin)}

    def _ready(self):
        report = self.create_approved_report()
        self.complete_flow(report)
        with patch.object(stage.approved_content.runtime.transport, "catalog",
                side_effect=self.current_catalog), patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            response = self._signed("POST", f"/api/ai/reports/{report.id}/files",
                body=self._body(), request_id="promotion-trial-create")
            self.assertEqual(response.status_code, 200, response.content)
            row_id = response.json()["item"]["id"]
            self.assertEqual(response.json()["item"]["rendererVersion"], 9)
            finished = files.tick()
            self.assertEqual(finished["status"], "ready", finished)
            model.assert_not_called()
            remote.assert_not_called()
        return report, m.AiBusinessFileRun.objects.get(pk=row_id)

    def test_signed_trial_ready_all_html_xlsx_json_chunks_reassemble(self):
        report, row = self._ready()
        compact = json.loads(row.manifest_json)
        self.assertEqual((row.renderer_version, row.draft, row.status), (9, False, "ready"))
        self.assertEqual(json.loads(row.progress_json)["manifestFileSha256"],
            compact["manifestFile"]["sha256"])
        self.assertEqual({descriptor["format"] for descriptor in [*compact["files"],
            compact["manifestFile"]]}, {"html", "xlsx", "json"})
        for descriptor in [*compact["files"], compact["manifestFile"]]:
            raw = bytearray()
            for sequence in range(1, descriptor["chunkCount"] + 1):
                url = (f"/api/ai/business-files/{row.id}/volumes/"
                    f"{descriptor['volumeIndex']}/chunks/{descriptor['format']}?sequence={sequence}")
                response = self._signed("GET", url,
                    request_id=f"trial-{descriptor['volumeIndex']}-{descriptor['format']}-{sequence}")
                self.assertEqual(response.status_code, 200, response.content)
                part = response.json()
                self.assertEqual((part["attempt"], part["bindingDigest"],
                    part["fileSha256"]), (row.attempt, row.binding_digest, descriptor["sha256"]))
                raw.extend(base64.b64decode(part["base64"], validate=True))
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()),
                (descriptor["bytes"], descriptor["sha256"]))
        self.assertEqual(m.AiBusinessFileRun.objects.filter(report=report,
            renderer_version=7).count(), 0)

    def test_missing_forged_chunk_owner_and_source_change_fail_closed(self):
        report, row = self._ready()
        descriptor = json.loads(row.manifest_json)["files"][0]
        args = (row.id, str(descriptor["volumeIndex"]), descriptor["format"],
            {"sequence": "1"}, self.admin)
        self.assertEqual(business_volume_files.chunk(*args)["fileSha256"],
            descriptor["sha256"])
        with patch.object(m.AiBusinessVolumeChunk.objects, "filter") as query:
            query.return_value.first.return_value = None
            with self.assertRaises(AiError): business_volume_files.chunk(*args)
            query.return_value.first.return_value = SimpleNamespace(
                content=b"forged", content_digest="0"*64)
            with self.assertRaises(AiError): business_volume_files.chunk(*args)
        with patch.object(stage, "_publication_fence", return_value="0"*64):
            with self.assertRaises(AiError): business_volume_files.chunk(*args)
        source = business_evidence.get_run(
            json.loads(report.snapshot_json)["evidenceRunId"], self.admin)
        changed_source = SimpleNamespace(status="sealed", version=source.version + 1,
            plan_json=source.plan_json, state_json=source.state_json)
        with patch.object(download.business_evidence, "get_run",
                return_value=changed_source):
            with self.assertRaises(AiError): business_volume_files.chunk(*args)
        with self.assertRaises(AiError): business_volume_files.chunk(
            row.id, "0", "html", {"sequence": "1"}, self.admin)
        with self.assertRaises(AiError): business_volume_files.chunk(
            row.id, str(descriptor["volumeIndex"]), descriptor["format"],
            {"sequence": str(descriptor["chunkCount"] + 1)}, self.admin)
        with self.assertRaises(AiError): business_volume_files.chunk(
            row.id, str(descriptor["volumeIndex"]), descriptor["format"],
            {"sequence": "1"}, self.viewer)
        AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with self.assertRaises(AiError): business_volume_files.chunk(*args)

    def test_explicit_trial_opt_in_and_approval_required(self):
        report = self.five_completed(promotion_reference=True)
        with patch.object(stage.approved_content.runtime.transport, "catalog",
                side_effect=self.current_catalog):
            with self.assertRaises(AiError): stage.create(report.id, self.admin)
        self.approved(report)
        self.complete_flow(report)
        url = f"/api/ai/reports/{report.id}/files"
        wrong = [
            {**self._body(), "draft": True},
            {**self._body(), "expectedPrincipalKey": "wrong"},
            {**self._body(), "deliveryMode": "unknown"},
        ]
        for index, body in enumerate(wrong):
            response = self._signed("POST", url, body=body,
                request_id=f"trial-invalid-{index}")
            self.assertGreaterEqual(response.status_code, 400, response.content)
        self.assertFalse(m.AiBusinessFileRun.objects.filter(report=report).exists())

    def test_interrupted_trial_attempt_is_not_published_or_mixed_on_resume(self):
        report = self.create_approved_report()
        self.complete_flow(report)
        with patch.object(stage.approved_content.runtime.transport, "catalog",
                side_effect=self.current_catalog):
            row_id = stage.create(report.id, self.admin)["item"]["id"]
            original = business_volume_files._save_file
            calls = 0

            def interrupted(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2: raise RuntimeError("synthetic interruption")
                return original(*args, **kwargs)

            with patch.object(business_volume_files, "_save_file",
                    side_effect=interrupted):
                failed = files.tick()
            self.assertEqual(failed["status"], "paused")
            row = m.AiBusinessFileRun.objects.get(pk=row_id)
            self.assertEqual((row.status, row.attempt, row.manifest_json),
                ("paused", 1, "{}"))
            old_count = m.AiBusinessVolumeChunk.objects.filter(
                run=row, attempt=1).count()
            self.assertGreater(old_count, 0)
            stage.control(row.id, "resume", row.version, self.admin)
            self.assertEqual(files.tick()["status"], "ready")
            row.refresh_from_db()
            self.assertEqual((row.status, row.attempt), ("ready", 2))
            self.assertEqual(m.AiBusinessVolumeChunk.objects.filter(
                run=row, attempt=1).count(), old_count)
            self.assertTrue(m.AiBusinessVolumeChunk.objects.filter(
                run=row, attempt=2).exists())

    def test_changed_current_table_declaration_cannot_publish_staged_bytes(self):
        report = self.create_approved_report()
        self.complete_flow(report)
        with patch.object(stage.approved_content.runtime.transport, "catalog",
                side_effect=self.current_catalog):
            row_id = stage.create(report.id, self.admin)["item"]["id"]
            with patch.object(stage, "publish", side_effect=AiError(
                    "hold staging", "conflict", 409)):
                self.assertEqual(files.tick()["status"], "paused")
            row = m.AiBusinessFileRun.objects.get(pk=row_id)
            self.assertEqual((row.status, row.error_code),
                ("paused", "renderer_unpublished"))
            original = stage.formal_pair._tables

            changes = {
                "title": lambda table: replace(table, title="已更改的摘要标题"),
                "note": lambda table: replace(table, note="已更改的口径说明"),
                "columnLabel": lambda table: replace(table, columns=(
                    replace(table.columns[0], label="已更改的列名"),
                    *table.columns[1:])),
            }
            for name, change in changes.items():
                def changed(*args, **kwargs):
                    tables = original(*args, **kwargs)
                    return (change(tables[0]), *tables[1:])
                with self.subTest(name=name), patch.object(stage.formal_pair,
                        "_tables", side_effect=changed):
                    with self.assertRaises(AiError):
                        stage.publish(row.id, row.version, self.admin)
            row.refresh_from_db()
            self.assertEqual((row.status, row.error_code),
                ("paused", "renderer_unpublished"))
