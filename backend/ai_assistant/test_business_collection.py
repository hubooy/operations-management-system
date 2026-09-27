from unittest.mock import patch
from datetime import timedelta
from django.test import TestCase, override_settings
from django.utils import timezone
from . import test_business_evidence as fixtures, business_evidence as evidence, business_collection as collection, models as m
from .control_models import AiMutationAudit
from .policy import AiError, mutation
from sales.analysis import read_page
from sales.tests.factories import make_line


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class AutomaticCollectionTests(TestCase):
    user = fixtures.BusinessEvidenceTests.user
    call = fixtures.BusinessEvidenceTests.call
    setUp = fixtures.BusinessEvidenceTests.setUp

    def start(self):
        body = {**self.body, "collectionMode": "bulk", "autoCollect": True}
        self.catalog.append({**self.catalog[0], "name": "get_business_source_page"})
        return evidence.create(body, self.admin)["item"]["id"]

    def execute(self, name, args, principal, **kwargs):
        self.assertEqual(kwargs["surface"], "business_collection")
        if name == "get_data_freshness":
            data = {"dataCutoffDate": "2026-08-01"}
        else:
            self.assertEqual(args["domain"], "sales")
            data = read_page(principal, {"operation": "analysis_records", **{k: v for k, v in args.items() if k != "domain"}})
        return {"toolName": name, "ok": True, "data": data}

    def tick(self, execute=None):
        with patch("ai_assistant.transport.catalog", return_value=self.catalog), patch("ai_assistant.transport.execute_tool", side_effect=execute or self.execute):
            return collection.tick()

    def test_background_collects_multiple_pages_and_seals_without_browser(self):
        for i in range(13, 126):
            make_line(i, f"auto-{i}", channel=self.query["channel"], online_spec_code="M1").save()
        run_id = self.start()
        result = self.tick()
        row = evidence.get_run(run_id, self.admin)
        self.assertEqual(row.status, "sealed")
        self.assertEqual(evidence.mapping(row)["sources"]["sales"]["rowCount"], 125)
        self.assertGreater(m.AiBusinessEvidenceChunk.objects.filter(run_id=run_id).count(), 1)
        self.assertEqual(result["steps"][-1]["status"], "sealed")
        self.assertTrue(AiMutationAudit.objects.filter(action="business_collection_page_committed").exists())
        self.assertEqual(self.tick()["status"], "idle")
        self.assertFalse(m.AiAgentProviderDispatches.objects.exists())

    def test_pause_during_read_rejects_late_page_then_resume_continues(self):
        run_id = self.start()
        def interrupt(name, args, principal, **kwargs):
            value = self.execute(name, args, principal, **kwargs)
            if name != "get_data_freshness":
                row = evidence.get_run(run_id, self.admin)
                collection.control(run_id, {"expectedVersion": row.version, "action": "pause"}, self.admin)
            return value
        self.assertEqual(self.tick(interrupt)["status"], "superseded")
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.count(), 0)
        self.assertEqual(self.tick()["status"], "idle")
        row = evidence.get_run(run_id, self.admin)
        collection.control(run_id, {"expectedVersion": row.version, "action": "resume"}, self.admin)
        self.tick()
        self.assertEqual(evidence.get_run(run_id, self.admin).status, "sealed")

    def test_failure_backoff_is_bounded_and_preserves_checkpoint(self):
        run_id = self.start()
        for index in range(3):
            row = evidence.get_run(run_id, self.admin)
            future = max(timezone.now(), row.next_collect_at) + timedelta(seconds=1)
            with patch("ai_assistant.business_collection.timezone.now", return_value=future):
                self.tick(lambda *a, **k: (_ for _ in ()).throw(AiError("offline", "service_unavailable", 503)))
            saved = evidence.get_run(run_id, self.admin)
            self.assertEqual(saved.collection_failures, index+1)
            self.assertEqual(m.AiBusinessEvidenceChunk.objects.count(), 0)
            self.assertEqual(self.tick()["status"], "idle")
        self.assertEqual(saved.collection_status, "paused")

    def test_page_audit_failure_rolls_back_facts_and_auto_seal(self):
        run_id = self.start()
        audit = collection.audit
        def reject(row, principal, action, request_id):
            if action == "page_committed":
                raise AiError("audit offline", "service_unavailable", 503)
            return audit(row, principal, action, request_id)
        with patch("ai_assistant.business_collection.audit", side_effect=reject):
            self.tick()
        row = evidence.get_run(run_id, self.admin)
        self.assertEqual(row.status, "collecting")
        self.assertEqual(row.state_json, "{}")
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.count(), 0)
        self.assertEqual(row.collection_failures, 1)

    def test_expired_read_claim_can_resume_but_permission_loss_pauses(self):
        run_id = self.start()
        with mutation(self.admin):
            row = evidence.get_run(run_id, self.admin)
            row.collection_status = "reading"
            row.next_collect_at = timezone.now() - timedelta(seconds=1)
            row.version += 1
            row.save()
        self.tick()
        self.assertEqual(evidence.get_run(run_id, self.admin).status, "sealed")
        self.body["clientRequestId"] = "lost-access"
        second = self.start()
        from access_control.models import AppUser
        AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with patch("ai_assistant.transport.execute_tool") as remote:
            result = collection.tick()
            remote.assert_not_called()
        self.assertEqual(result["status"], "paused")
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=second).collection_status, "paused")

    def test_control_api_is_owner_writer_only_and_versioned(self):
        run_id = self.start()
        path = f"/api/ai/business-evidence/{run_id}/control"
        response = self.call(path, {"action": "pause", "expectedVersion": 1}, principal=self.admin)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["item"]["collection"]["status"], "paused")
        self.assertEqual(self.call(path, {"action": "resume", "expectedVersion": 1}, principal=self.admin).status_code, 409)
        self.assertEqual(self.call(path, {"action": "resume", "expectedVersion": 2}, principal=self.viewer).status_code, 403)
        with override_settings(DJANGO_PROCESS_ROLE="ai_reader"), patch("ai_assistant.views.authority"):
            self.assertEqual(self.call(path, {"action": "resume", "expectedVersion": 2}, principal=self.admin).status_code, 403)
        self.assertTrue(AiMutationAudit.objects.filter(action="POST " + path).exists())

    def test_owner_demotion_pauses_without_reading_sources(self):
        run_id = self.start()
        from access_control.models import AppUser
        AppUser.objects.filter(email=self.admin.email).update(role_id="operator")
        with patch("ai_assistant.transport.execute_tool") as remote:
            self.assertEqual(collection.tick()["status"], "paused")
            remote.assert_not_called()
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=run_id).collection_status, "paused")
        self.assertEqual(collection.tick()["status"], "idle")
