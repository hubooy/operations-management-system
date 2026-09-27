"""The existing collector contract also holds for the explicit v2 directory."""
import json
from urllib.parse import urlencode
from unittest.mock import patch

from django.test import override_settings
from django.http import QueryDict
from sales.analysis_continuation import read_page as continuation_page
from sales.models import SalesDataRevision

from . import business_collection as collection, business_evidence as evidence, models as m
from . import test_business_collection as legacy
from .policy import AiError
from sales.tests.factories import make_line


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class V2AutomaticCollectionTests(legacy.AutomaticCollectionTests):
    def setUp(self):
        super().setUp()
        for domain in ("sales", "erp"):
            SalesDataRevision.objects.get_or_create(domain=domain, defaults={"revision": 1, "source_digest": "a"*64})

    # Inherit the seven independent v1 scenarios: complete paging, late response
    # isolation, bounded retry, audit rollback, expired claims and role/owner gates.
    # Only admission changes; transport and the scheduler are the real implementations.
    def start(self):
        body = {**self.body, "schemaVersion": "business-evidence-v2", "collectionMode": "bulk", "autoCollect": True}
        self.catalog.append({**self.catalog[0], "name": "get_business_source_page"})
        self.catalog.append({**self.catalog[0], "name": "get_business_sales_continuation_page"})
        return evidence.create(body, self.admin)["item"]["id"]

    def execute(self, name, args, principal, **kwargs):
        if name != "get_business_sales_continuation_page":
            return super().execute(name, args, principal, **kwargs)
        self.assertEqual(kwargs["surface"], "business_collection")
        self.assertNotIn("domain", args)
        return {"toolName": name, "ok": True, "auditStatus": "recorded",
            "data": continuation_page(principal, QueryDict(urlencode(args)))}

    def advance_once(self, execute=None):
        with patch("ai_assistant.transport.catalog", return_value=self.catalog), patch(
            "ai_assistant.transport.execute_tool", side_effect=execute or self.execute
        ):
            return collection._advance()

    def test_large_directory_is_resumed_in_bounded_ticks_without_plan_sources(self):
        self.body["sources"] = [{"key": f"source-{index:02d}", "domain": "sales", "query": {
            **self.query, "shop": f"空店{index}", "channel": f"空渠道{index}"
        }} for index in range(17)]
        run_id = self.start()
        row = evidence.get_run(run_id, self.admin)
        self.assertNotIn("sources", json.loads(row.plan_json))
        self.assertEqual(m.AiBusinessEvidenceSource.objects.filter(run=row).count(), 17)
        first = self.tick()
        self.assertGreater(len(first["steps"]), 0)
        self.assertLessEqual(len(first["steps"]), 16)
        self.assertEqual(evidence.get_run(run_id, self.admin).status, "collecting")
        for _ in range(17):
            if evidence.get_run(run_id, self.admin).status == "sealed":
                break
            self.tick()
        row.refresh_from_db()
        self.assertEqual(row.status, "sealed")
        sources = list(m.AiBusinessEvidenceSource.objects.filter(run=row))
        self.assertTrue(all(source.finished and source.row_count == 0 and source.page_count == 1 for source in sources))
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run=row).count(), 17)
        self.assertEqual(row.stored_bytes, sum(source.stored_bytes for source in sources))
        self.assertFalse(m.AiAgentProviderDispatches.objects.exists())

    def test_pause_and_new_invocation_restore_exact_source_checkpoint(self):
        for index in range(13, 126):
            make_line(index, f"v2-resume-{index}", channel=self.query["channel"], online_spec_code="M1").save()
        run_id = self.start()
        self.advance_once()
        source = m.AiBusinessEvidenceSource.objects.get(run_id=run_id)
        self.assertEqual((source.page_count, source.row_count, source.finished), (1, 100, False))
        checkpoint = source.checkpoint_json
        first_chunk = m.AiBusinessEvidenceChunk.objects.get(run_id=run_id, sequence=1)
        first_bytes = (first_chunk.payload_json, first_chunk.payload_digest)
        row = evidence.get_run(run_id, self.admin)
        collection.control(run_id, {"expectedVersion": row.version, "action": "pause"}, self.admin)
        self.assertEqual(self.tick()["status"], "idle")
        source.refresh_from_db()
        self.assertEqual(source.checkpoint_json, checkpoint)
        row = evidence.get_run(run_id, self.admin)
        collection.control(run_id, {"expectedVersion": row.version, "action": "resume"}, self.admin)
        self.tick()
        source.refresh_from_db()
        row.refresh_from_db()
        first_chunk.refresh_from_db()
        self.assertEqual((first_chunk.payload_json, first_chunk.payload_digest), first_bytes)
        self.assertEqual(row.status, "sealed")
        self.assertEqual((source.page_count, source.row_count, source.finished), (2, 125, True))
        self.assertLessEqual(source.checkpoint_run_version, row.version)
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run=row).count(), 2)
        self.assertEqual(row.stored_bytes, source.stored_bytes)
        self.assertEqual(json.loads(first_chunk.payload_json)["pageEvidence"]["rowCount"], 100)

    def test_cancel_during_read_discards_page_and_preserves_initial_directory(self):
        run_id = self.start()

        def cancel(name, args, principal, **kwargs):
            response = self.execute(name, args, principal, **kwargs)
            if name != "get_data_freshness":
                row = evidence.get_run(run_id, self.admin)
                evidence.finish(run_id, {"expectedVersion": row.version, "action": "cancel"}, self.admin)
            return response

        self.assertEqual(self.tick(cancel)["status"], "superseded")
        row = evidence.get_run(run_id, self.admin)
        source = m.AiBusinessEvidenceSource.objects.get(run=row)
        self.assertEqual(row.status, "cancelled")
        self.assertEqual((source.version, source.page_count, source.stored_bytes, source.checkpoint_json), (1, 0, 0, "{}"))
        self.assertFalse(m.AiBusinessEvidenceChunk.objects.filter(run=row).exists())
        self.assertEqual(self.tick()["status"], "idle")

    def test_directory_verification_error_pauses_instead_of_leaving_a_read_claim(self):
        run_id = self.start()
        with patch("ai_assistant.business_evidence_store.catalog", side_effect=AiError("目录摘要不匹配", "conflict", 409)), patch(
            "ai_assistant.transport.execute_tool"
        ) as remote:
            result = collection.tick()
        remote.assert_not_called()
        self.assertEqual(result["status"], "paused")
        row = evidence.get_run(run_id, self.admin)
        self.assertEqual((row.collection_status, row.collection_error_code), ("paused", "conflict"))
        self.assertFalse(m.AiBusinessEvidenceChunk.objects.filter(run=row).exists())

        self.body["clientRequestId"] = "next-after-corrupt-directory"
        next_id = self.start()
        self.tick()
        self.assertEqual(evidence.get_run(next_id, self.admin).status, "sealed")
        row.refresh_from_db()
        self.assertEqual(row.collection_status, "paused")

    def test_permission_revoked_during_read_discards_the_page(self):
        from access_control.models import AppUser

        run_id = self.start()

        def revoke(name, args, principal, **kwargs):
            response = self.execute(name, args, principal, **kwargs)
            if name != "get_data_freshness":
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return response

        self.assertEqual(self.tick(revoke)["status"], "paused")
        row = m.AiBusinessEvidenceRun.objects.get(pk=run_id)
        source = m.AiBusinessEvidenceSource.objects.get(run=row)
        self.assertEqual(row.collection_status, "paused")
        self.assertEqual((source.version, source.page_count, source.stored_bytes), (1, 0, 0))
        self.assertFalse(m.AiBusinessEvidenceChunk.objects.filter(run=row).exists())
