"""Real AI ledger/owning netshop facts; only the cross-process transport is replaced."""
import copy
import json
import time
from unittest.mock import Mock, patch
from urllib.parse import urlencode
from contextlib import nullcontext

from django.core import signing
from django.http import QueryDict
from django.test import TestCase, override_settings
from access_control.models import AppUser
from netshop import analysis, analysis_continuation
from netshop.errors import NetshopApiError
from netshop.models import NetshopRow, NetshopDataRevision
from netshop.tests import test_analysis as netshop_fixtures
from . import business_evidence as evidence, business_evidence_store as store, business_collection as collection
from . import business_collection_continuation as continuation, models as m, test_business_evidence as fixtures
from .policy import AiError, canonical, uid


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessCollectionContinuationTests(TestCase):
    user = fixtures.BusinessEvidenceTests.user
    call = fixtures.BusinessEvidenceTests.call

    def setUp(self):
        fixtures.BusinessEvidenceTests.setUp(self)
        netshop_fixtures.AnalysisRecordsTests.setUp(self)
        template = NetshopRow.objects.get(source_row_key="analysis-1")
        rows = []
        for index in range(103):
            row = copy.copy(template)
            row.pk = None
            row.source_row_key = f"continuation-{index}"
            row.source_row_hash = f"{index+100:064x}"
            rows.append(row)
        NetshopRow.objects.bulk_create(rows)
        self.query = {key: value for key, value in self.params.items() if key != "limit"}
        self.catalog = [{**self.catalog[0], "name": name} for name in (
            "get_data_freshness", "get_business_source_page", continuation.TOOL)]
        self.calls = []
        self.expire_first_cursor = True
        self.run_id = evidence.create({"schemaVersion": "business-evidence-v2", "clientRequestId": uid("continuation"),
            "sources": [{"key": "shop", "domain": "netshop", "query": self.query}],
            "collectionMode": "bulk", "autoCollect": True}, self.admin)["item"]["id"]

    def execute(self, name, arguments, principal, **kwargs):
        self.calls.append((name, copy.deepcopy(arguments)))
        self.assertEqual(kwargs["surface"], "business_collection")
        if name == "get_data_freshness":
            data = {"dataCutoffDate": "2026-09-03"}
        elif name == continuation.TOOL:
            self.assertNotIn("domain", arguments)
            data = analysis_continuation.read_page(principal, QueryDict(urlencode(arguments)))
        else:
            self.assertEqual(name, "get_business_source_page")
            params = {key: value for key, value in arguments.items() if key != "domain"}
            expired_clock = patch.object(signing.TimestampSigner, "timestamp", return_value=signing.b62_encode(int(time.time())-7200)) if self.expire_first_cursor else nullcontext()
            with expired_clock:
                data = analysis.read_page(*analysis.validate_request(QueryDict(urlencode(params))))
        return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": data}

    def collect(self, execute=None, commit=None):
        row = evidence.get_run(self.run_id, self.admin)
        with patch("ai_assistant.transport.catalog", return_value=self.catalog), patch(
                "ai_assistant.transport.execute_tool", side_effect=execute or self.execute):
            return evidence.collect(row.id, {"sourceKey": "shop", "expectedVersion": row.version},
                self.admin, "continuation-test", commit=commit)

    def prepared_input(self):
        row = evidence.get_run(self.run_id, self.admin)
        return row, store.catalog(row)[0], store.source_record(row, "shop"), self.admin

    def test_expired_page_after_pause_resume_seals_and_preserves_original_bytes(self):
        self.collect()
        first = m.AiBusinessEvidenceChunk.objects.get(run_id=self.run_id, sequence=1)
        original = (first.payload_json, first.payload_digest)
        old_cursor = json.loads(first.payload_json)["pagination"]["nextCursor"]
        with self.assertRaises(signing.SignatureExpired): signing.loads(old_cursor, salt=analysis.CURSOR_SALT, max_age=3600)
        row = evidence.get_run(self.run_id, self.admin)
        collection.control(row.id, {"expectedVersion": row.version, "action": "pause"}, self.admin)
        row.refresh_from_db()
        collection.control(row.id, {"expectedVersion": row.version, "action": "resume"}, self.admin)
        with patch("ai_assistant.transport.catalog", return_value=self.catalog), patch("ai_assistant.transport.execute_tool", side_effect=self.execute):
            collection.tick()
        row.refresh_from_db(); first.refresh_from_db()
        self.assertEqual(row.status, "sealed")
        self.assertEqual((first.payload_json, first.payload_digest), original)
        source = store.source_record(row, "shop")
        self.assertEqual((source.page_count, source.row_count, source.finished), (2, 105, True))
        self.assertEqual([name for name, _ in self.calls], ["get_data_freshness", "get_business_source_page", continuation.TOOL])
        self.assertEqual(self.calls[-1][1]["cursor"], old_cursor)
        self.assertEqual(evidence.source_detail(row.id, "shop", self.admin)["reconciliation"]["rowCount"], 105)
        self.assertFalse(m.AiAgentProviderDispatches.objects.exists())

    def test_missing_continuation_tool_rejects_without_old_path_fallback(self):
        self.collect()
        self.catalog = [item for item in self.catalog if item["name"] != continuation.TOOL]
        with self.assertRaises(AiError) as caught: self.collect()
        self.assertEqual(caught.exception.code, "access_denied")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), 1)

    def test_current_cursor_uses_same_continuation_entry_without_renewal(self):
        self.expire_first_cursor = False
        self.collect()
        first = m.AiBusinessEvidenceChunk.objects.get(run_id=self.run_id, sequence=1)
        before = (first.payload_json, first.payload_digest)
        cursor = json.loads(first.payload_json)["pagination"]["nextCursor"]
        signing.loads(cursor, salt=analysis.CURSOR_SALT, max_age=3600)
        with patch.object(analysis_continuation.analysis_cursor, "read_expired_page", side_effect=AssertionError("fresh cursor must not renew")):
            self.collect()
        first.refresh_from_db()
        self.assertEqual((first.payload_json, first.payload_digest), before)
        self.assertEqual(self.calls[-1][0], continuation.TOOL)
        self.assertEqual(self.calls[-1][1]["cursor"], cursor)
        source = store.source_record(evidence.get_run(self.run_id, self.admin), "shop")
        self.assertEqual((source.page_count, source.row_count, source.finished), (2, 105, True))

    def test_response_loss_repeats_read_but_commits_one_page(self):
        self.collect()
        old = store.source_record(evidence.get_run(self.run_id, self.admin), "shop").checkpoint_json
        def lost(*args, **kwargs):
            self.execute(*args, **kwargs)
            raise AiError("response lost", "service_unavailable", 503)
        with self.assertRaises(AiError): self.collect(lost)
        self.assertEqual(store.source_record(evidence.get_run(self.run_id, self.admin), "shop").checkpoint_json, old)
        self.collect()
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), 2)
        self.assertEqual(self.calls[-1][1], self.calls[-2][1])

    def test_actor_permission_aba_during_remote_read_is_rejected(self):
        self.collect()
        def changed(*args, **kwargs):
            response = self.execute(*args, **kwargs)
            user = AppUser.objects.get(email=self.admin.email)
            AppUser.objects.filter(pk=user.pk).update(status="disabled", version=user.version+1)
            AppUser.objects.filter(pk=user.pk).update(status="active", version=user.version+2)
            return response
        with self.assertRaises(AiError) as caught: self.collect(changed)
        self.assertEqual(caught.exception.code, "access_denied")
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), 1)

    def test_cancelled_parent_rejects_late_response(self):
        self.collect()
        def cancel(*args, **kwargs):
            response = self.execute(*args, **kwargs)
            row = evidence.get_run(self.run_id, self.admin)
            evidence.finish(row.id, {"expectedVersion": row.version, "action": "cancel"}, self.admin)
            return response
        with self.assertRaises(AiError): self.collect(cancel)
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), 1)
        self.assertEqual(evidence.get_run(self.run_id, self.admin).status, "cancelled")

    def test_audit_failure_rolls_back_next_page_and_checkpoint(self):
        self.collect()
        old = store.source_record(evidence.get_run(self.run_id, self.admin), "shop").checkpoint_json
        def fail(*args): raise AiError("audit failed")
        with self.assertRaises(AiError): self.collect(commit=fail)
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), 1)
        self.assertEqual(store.source_record(evidence.get_run(self.run_id, self.admin), "shop").checkpoint_json, old)

    def test_concurrent_page_commit_wins_once_and_late_cas_is_rejected(self):
        self.collect()
        def competing(*args, **kwargs):
            response = self.execute(*args, **kwargs)
            self.collect()
            return response
        with self.assertRaises(AiError) as caught: self.collect(competing)
        self.assertEqual(caught.exception.code, "version_conflict")
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), 2)
        source = store.source_record(evidence.get_run(self.run_id, self.admin), "shop")
        self.assertEqual((source.page_count, source.row_count), (2, 105))

    def test_final_capacity_gate_preserves_existing_checkpoint(self):
        self.collect()
        old = store.source_record(evidence.get_run(self.run_id, self.admin), "shop").checkpoint_json
        with patch.object(evidence, "MAX_BYTES", 1), self.assertRaises(AiError) as caught:
            self.collect()
        self.assertEqual(caught.exception.code, "payload_too_large")
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), 1)
        self.assertEqual(store.source_record(evidence.get_run(self.run_id, self.admin), "shop").checkpoint_json, old)

    def test_source_revision_change_and_returned_revision_mismatch_reject(self):
        self.collect()
        def wrong(*args, **kwargs):
            response = self.execute(*args, **kwargs)
            response["data"]["sourceRevision"] = "changed"
            return response
        with self.assertRaises(AiError): self.collect(wrong)
        NetshopDataRevision.objects.filter(domain="netshop").update(revision=8)
        with self.assertRaises(NetshopApiError): self.collect()
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), 1)

    def test_corrupt_checkpoint_last_id_and_cursor_do_not_reach_remote(self):
        self.collect()
        for field, value in (("last_id", True), ("last_id", 999999), ("expected_cursor", "wrong"), ("source_ref", "f"*64)):
            row, source, record, actor = self.prepared_input()
            entry = json.loads(record.checkpoint_json); entry["verifier"][field] = value
            record.checkpoint_json = canonical(entry)
            with self.subTest(field=field), self.assertRaises(AiError): continuation.prepare(row, source, record, actor)

    def test_original_payload_byte_corruption_and_extra_tail_are_rejected(self):
        self.collect()
        row, source, record, actor = self.prepared_input()
        manager = m.AiBusinessEvidenceChunk.objects
        actual = manager.filter(run_id=row.id, source_key="shop")
        raw = actual.get(sequence=1).payload_json
        for changed in (raw+" ", "["+raw[1:]):
            proxy = Mock(wraps=actual)
            proxy.order_by.side_effect = actual.order_by
            filtered, values = Mock(), Mock()
            values.first.return_value = changed
            filtered.values_list.return_value = values
            proxy.filter = Mock(return_value=filtered)
            with patch.object(manager, "filter", return_value=proxy), self.assertRaises(AiError):
                continuation.prepare(row, source, record, actor)
        proxy = Mock(wraps=actual)
        ordered, annotated = Mock(), Mock()
        annotated.values.return_value = [{"id": "extra", "sequence": 2, "payload_digest": "a"*64, "payload_bytes": 1}]
        ordered.annotate.return_value = annotated
        proxy.order_by = Mock(return_value=ordered)
        with patch.object(manager, "filter", return_value=proxy), self.assertRaises(AiError):
            continuation.prepare(row, source, record, actor)
