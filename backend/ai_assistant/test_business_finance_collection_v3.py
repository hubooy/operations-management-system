"""Finance-only v3 physical transition and immutable replay probes."""
import hashlib
from importlib import import_module

from django.apps import apps
from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis import finance_collection_state as verifier
from business_analysis.contracts import canonical
from business_analysis.test_finance_collection_state import owned_page, sources as finance_fixture
from sales.auth import Principal

from . import business_evidence_v3 as plan, business_finance_collection_v3 as reader, models as m
from .policy import digest, uid


class BusinessFinanceCollectionV3Tests(TestCase):
    def setUp(self):
        if connection.vendor != "postgresql": self.skipTest("finance source transitions require PostgreSQL")
        self.principal = Principal("v3-finance-facts@example.test", "Synthetic", "admin", None)
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
        now = timezone.now()
        AppUser.objects.create(email=self.principal.email, role=role, status="active", scope=None,
            display_name="Synthetic", version=1, created_at=now, updated_at=now)
        _, query, publication, rows = finance_fixture()
        self.query, self.publication, self.rows = query, publication, rows
        daily = {"key": "sales-current", "domain": "sales", "query": {"platform": "京东",
            "shop": "测试店", "channel": "京东", "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"}}
        finance = {"key": "finance-context", "domain": "finance", "query": query}
        self.body = {"schemaVersion": "business-evidence-v3", "clientRequestId": uid("client"),
            "sources": [daily, finance], "analysisRequest": {"schemaVersion": "business-analysis-request-v1",
                "question": "店铺与财报", "requestedDimensions": ["shop"], "requestedWindows": ["current"]}}

    def plan(self):
        result = plan.create(self.body, self.principal)
        row = m.AiBusinessEvidenceRun.objects.get(pk=result["item"]["id"])
        finance = m.AiBusinessEvidenceSource.objects.get(run=row, source_key="finance-context")
        daily = m.AiBusinessEvidenceSource.objects.get(run=row, source_key="sales-current")
        return row, finance, daily

    def page(self):
        return owned_page(self.query, self.publication, self.rows, offset=0, total=len(self.rows))

    def append_physical_fixture(self, row, source):
        page = self.page()
        checkpoint = verifier.consume(None, page, trusted_query=self.query)
        raw = canonical(page)
        with transaction.atomic():
            m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run=row, source_key=source.source_key,
                sequence=1, payload_json=raw, payload_digest=digest(raw))
            m.AiBusinessEvidenceSource.objects.filter(pk=source.pk).update(
                version=2, checkpoint_run_version=2, page_count=1, stored_bytes=len(raw.encode("utf-8")),
                row_count=checkpoint["rowsRead"], finished=checkpoint["finished"], checkpoint_json=canonical(checkpoint),
                updated_at=timezone.now())
            m.AiBusinessEvidenceRun.objects.filter(pk=row.pk).update(version=2, stored_bytes=len(raw.encode("utf-8")))
        return checkpoint, raw

    def test_finance_source_can_finish_while_parent_and_daily_remain_unsealed(self):
        row, source, daily = self.plan()
        initial = reader.inspect(row.id, source.source_key, self.principal)
        self.assertEqual(initial["nextArguments"], {"query": self.query, "offset": 0, "afterId": 0})
        self.assertFalse(initial["persistentEvidenceVerified"])
        checkpoint, raw = self.append_physical_fixture(row, source)
        actual = reader.inspect(row.id, source.source_key, self.principal)
        self.assertTrue(actual["finished"])
        self.assertEqual(actual["pageCount"], 1)
        self.assertEqual(actual["rowCount"], len(self.rows))
        self.assertEqual(actual["storedBytes"], len(raw.encode("utf-8")))
        stored = m.AiBusinessEvidenceChunk.objects.get(run=row, source_key=source.source_key)
        self.assertEqual(stored.payload_digest, hashlib.sha256(raw.encode("utf-8")).hexdigest())
        self.assertEqual(actual["sourceCheckpointDigest"], digest(canonical(checkpoint)))
        self.assertIsNone(actual["nextArguments"])
        self.assertFalse(actual["persistentEvidenceVerified"])
        self.assertFalse(actual["reportGenerationSupported"])
        row.refresh_from_db(); daily.refresh_from_db()
        self.assertEqual((row.status, row.collection_status), ("collecting", "manual"))
        self.assertEqual(daily.page_count, 0)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceRun.objects.filter(pk=row.pk).update(version=3, status="sealed")

    def test_wrong_domain_chunk_missing_checkpoint_and_parent_cas_fail(self):
        row, source, daily = self.plan()
        page = self.page(); raw = canonical(page)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run=row, source_key=daily.source_key,
                sequence=1, payload_json=raw, payload_digest=digest(raw))
        for mode in ("source_without_chunk", "chunk_without_source", "parent_without_source", "wrong_page_size"):
            with self.subTest(mode=mode), self.assertRaises(DatabaseError), transaction.atomic():
                if mode in ("chunk_without_source", "wrong_page_size"):
                    m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run=row, source_key=source.source_key,
                        sequence=1, payload_json=raw if mode != "wrong_page_size" else raw + " " * 38000,
                        payload_digest=digest(raw if mode != "wrong_page_size" else raw + " " * 38000))
                if mode == "source_without_chunk":
                    m.AiBusinessEvidenceSource.objects.filter(pk=source.pk).update(
                        version=2, checkpoint_run_version=2, page_count=1, stored_bytes=len(raw.encode()), row_count=1,
                        checkpoint_json='{"schemaVersion":"fake"}')
                if mode == "parent_without_source":
                    m.AiBusinessEvidenceRun.objects.filter(pk=row.pk).update(version=2, stored_bytes=len(raw.encode()))
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ai_business_v3_directory_complete IMMEDIATE")

    def test_immutable_chunk_and_reverse_with_facts_are_denied(self):
        row, source, _ = self.plan()
        self.append_physical_fixture(row, source)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceChunk.objects.filter(run=row).update(payload_json="{}")
        migration = import_module("ai_assistant.migrations.0030_business_finance_source_pages")
        with connection.schema_editor() as editor:
            with self.assertRaises(RuntimeError): migration.uninstall(apps, editor)
