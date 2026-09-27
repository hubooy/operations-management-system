"""Signed public renderer-7 creation, publication, and bounded chunk delivery."""
import base64
from datetime import timedelta
import hashlib
import json
from unittest.mock import patch

from django import test as djtest

from access_control.models import AppUser
from sales.tests.factories import TEST_SECRET, signed_headers

from . import business_evidence, business_files as files
from . import business_promotion_volume_stage as stage
from . import business_volume_files, models as m
from . import test_business_promotion_file_ready as fixtures
from .test_business_screening_http import process_role
from .policy import AiError, mutation, uid


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionPublicFileTests(djtest.TransactionTestCase):
    """Reuse the real sealed and approved five-job fixture, not a model stub."""

    user = fixtures.PromotionReadyTests.user
    call = fixtures.PromotionReadyTests.call
    collect_body = fixtures.PromotionReadyTests.collect_body
    bundle = fixtures.PromotionReadyTests.bundle
    input_for = fixtures.PromotionReadyTests.input_for
    insert = fixtures.PromotionReadyTests.insert
    seed = fixtures.PromotionReadyTests.seed
    setUp = fixtures.PromotionReadyTests.setUp
    request_body = fixtures.PromotionReadyTests.request_body
    current_catalog = fixtures.PromotionReadyTests.current_catalog
    create_fixed_report = fixtures.PromotionReadyTests.create_fixed_report
    base = fixtures.PromotionReadyTests.base
    read = fixtures.PromotionReadyTests.read
    append = fixtures.PromotionReadyTests.append
    package = fixtures.PromotionReadyTests.package
    promotion = fixtures.PromotionReadyTests.promotion
    complete = fixtures.PromotionReadyTests.complete
    running_job = fixtures.PromotionReadyTests.running_job
    five_completed = fixtures.PromotionReadyTests.five_completed
    approved = fixtures.PromotionReadyTests.approved
    complete_flow = fixtures.PromotionReadyTests.complete_flow
    create_approved_report = fixtures.PromotionReadyTests.create_approved_report

    def _signed(self, method, url, *, body=None, request_id="promotion-file-public-1"):
        raw = (json.dumps(body, ensure_ascii=False, separators=(",", ":"))
            if body is not None else "")
        headers = signed_headers(url, email=self.admin.email, method=method,
            body=raw, request_id=request_id)
        with process_role("ai_writer" if method == "POST" else "ai_reader"), djtest.override_settings(
                DJANGO_INTERNAL_SECRET=TEST_SECRET), patch.dict(
                "os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}):
            response = (self.client.post(url, data=raw, content_type="application/json", headers=headers)
                if method == "POST" else self.client.get(url, headers=headers))
        return response

    def test_signed_create_tick_publish_and_download_exact_chunks(self):
        report = self.create_approved_report()
        self.complete_flow(report)
        body = {"deliveryMode": "volumes", "draft": False,
            "expectedPrincipalKey": business_evidence.principal_key(self.admin)}
        url = f"/api/ai/reports/{report.id}/files"
        with patch.object(stage.approved_content.runtime.transport, "catalog",
                side_effect=self.current_catalog), patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            created = self._signed("POST", url, body=body)
            self.assertEqual(created.status_code, 200, created.content)
            item = created.json()["item"]
            self.assertEqual((item["rendererVersion"], item["status"], item["draft"]),
                (7, "queued", False))
            replay = self._signed("POST", url, body=body)
            self.assertEqual(replay.status_code, 200, replay.content)
            self.assertEqual(replay.json()["item"]["id"], item["id"])
            self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
            self.assertEqual(m.AiBusinessFileRun.objects.filter(report=report).count(), 1)
            done = files.tick()
            self.assertEqual(done["status"], "ready", done)
            model.assert_not_called()
            remote.assert_not_called()

        run_id = item["id"]
        row = m.AiBusinessFileRun.objects.get(pk=run_id)
        self.assertEqual((row.status, row.renderer_version), ("ready", 7))
        compact = json.loads(row.manifest_json)
        progress = json.loads(row.progress_json)
        self.assertEqual(progress["stage"], "ready")
        self.assertEqual(progress["manifestFileSha256"], compact["manifestFile"]["sha256"])
        self.assertEqual(len(progress["publicationFenceDigest"]), 64)

        # The public signed reader must return precisely the immutable bytes
        # that the compact published descriptor names, including the manifest.
        for descriptor in [*compact["files"], compact["manifestFile"]]:
            raw = bytearray()
            for sequence in range(1, descriptor["chunkCount"]+1):
                chunk_url = (f"/api/ai/business-files/{run_id}/volumes/"
                    f"{descriptor['volumeIndex']}/chunks/{descriptor['format']}?sequence={sequence}")
                response = self._signed("GET", chunk_url,
                    request_id=f"promotion-file-chunk-{descriptor['volumeIndex']}-{descriptor['format']}-{sequence}")
                self.assertEqual(response.status_code, 200, response.content)
                part = response.json()
                self.assertEqual(part["bindingDigest"], row.binding_digest)
                self.assertEqual(part["fileSha256"], descriptor["sha256"])
                raw.extend(base64.b64decode(part["base64"], validate=True))
            self.assertEqual(len(raw), descriptor["bytes"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), descriptor["sha256"])

        with self.assertRaises(AiError):
            business_volume_files.chunk(run_id, "0", "html", {"sequence": "1"}, self.admin)
        first = compact["files"][0]
        chunk_url = (f"/api/ai/business-files/{run_id}/volumes/"
            f"{first['volumeIndex']}/chunks/{first['format']}?sequence=1")
        with patch.object(stage.promotion_export, "prepare",
                side_effect=AssertionError("full promotion scan during chunk")), patch.object(
                stage.approved_content, "build",
                side_effect=AssertionError("full content rebuild during chunk")):
            bounded = self._signed("GET", chunk_url,
                request_id="promotion-file-public-bounded-read")
        self.assertEqual(bounded.status_code, 200, bounded.content)
        with patch.object(stage, "_publication_fence", return_value="0"*64):
            changed = self._signed("GET", chunk_url,
                request_id="promotion-file-public-fence-changed")
        self.assertEqual(changed.status_code, 409, changed.content)

        class UndoProbe(Exception):
            pass

        def rejects_changed_graph(change):
            with self.assertRaises(UndoProbe):
                with mutation(self.admin):
                    change()
                    with self.assertRaises(AiError) as denied:
                        business_volume_files.chunk(run_id, str(first["volumeIndex"]),
                            first["format"], {"sequence": "1"}, self.admin)
                    self.assertEqual(denied.exception.status, 409)
                    raise UndoProbe()

        rejects_changed_graph(lambda: m.AiWorkflowNodeRuns.objects.create(
            id=uid("unexpected-file-node"), run_id=report.workflow_id,
            node_key="unexpected", position=6, node_type="agent",
            instruction="unexpected", depends_on_json="[]"))
        rejects_changed_graph(lambda: m.AiWorkflowNodeRuns.objects.filter(
            run_id=report.workflow_id, node_key="commerce").update(
            instruction="changed after publication"))
        rejects_changed_graph(lambda: m.AiAgentJobs.objects.create(
            id=uid("unexpected-file-job"), owner_email=report.owner_email,
            scope_json=report.scope_json, client_request_id=uid("unexpected-client"),
            request_digest="0"*64, task="unexpected", workflow_run_id=report.workflow_id,
            workflow_node_key="unexpected"))
        human = m.AiWorkflowNodeRuns.objects.get(run_id=report.workflow_id,
            node_key="human_review")
        rejects_changed_graph(lambda: m.AiWorkflowRuns.objects.filter(
            pk=report.workflow_id).update(completed_at=None))
        rejects_changed_graph(lambda: m.AiWorkflowRuns.objects.filter(
            pk=report.workflow_id).update(
            completed_at=human.completed_at-timedelta(seconds=1)))
        rejects_changed_graph(lambda: m.AiAgentJobs.objects.filter(
            workflow_run_id=report.workflow_id, workflow_node_key="commerce").update(
            phase="executing"))
        rejects_changed_graph(lambda: m.AiAgentJobs.objects.filter(
            workflow_run_id=report.workflow_id, workflow_node_key="commerce").update(
            state_json='{"tampered":true}'))
        self.assertEqual(business_volume_files.chunk(run_id,
            str(first["volumeIndex"]), first["format"], {"sequence": "1"},
            self.admin)["fileSha256"], first["sha256"])

        AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        revoked = self._signed("GET", chunk_url,
            request_id="promotion-file-public-owner-revoked")
        self.assertEqual(revoked.status_code, 403, revoked.content)

    def test_exact_public_creation_rejects_draft_and_wrong_principal(self):
        report = self.create_approved_report()
        self.complete_flow(report)
        url = f"/api/ai/reports/{report.id}/files"
        with patch.object(stage.approved_content.runtime.transport, "catalog",
                side_effect=self.current_catalog):
            for number, body in enumerate((
                    {"deliveryMode": "volumes", "draft": True,
                     "expectedPrincipalKey": business_evidence.principal_key(self.admin)},
                    {"deliveryMode": "volumes", "draft": "false",
                     "expectedPrincipalKey": business_evidence.principal_key(self.admin)},
                    {"deliveryMode": "volumes", "draft": False,
                     "expectedPrincipalKey": "wrong"},
                    {"draft": False},
            ), 1):
                response = self._signed("POST", url, body=body,
                    request_id=f"promotion-file-invalid-{number}")
                self.assertGreaterEqual(response.status_code, 400, response.content)
                self.assertLess(response.status_code, 500, response.content)
            self.assertFalse(m.AiBusinessFileRun.objects.filter(report=report).exists())
