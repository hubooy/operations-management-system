"""Isolated PostgreSQL physical daily v3 and mixed directory replay checks."""
from importlib import import_module
from unittest.mock import patch

from django.apps import apps
from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis import finance_source
from business_analysis.contracts import PageReconciler, canonical, comparison_periods, digest as page_digest
from business_analysis.test_finance_collection_state import owned_page
from business_analysis.test_finance_source import fixture as finance_fixture, row as finance_row
from sales.auth import Principal

from . import business_daily_collection_v3 as daily_reader, business_evidence_v3 as plan
from . import business_finance_collection_v3 as finance_reader, business_v3_catalog as catalog, models as m, transport
from .policy import AiError, digest, uid


def sources():
    return [{"key": "sales-current", "domain": "sales", "query": {"platform": "京东", "shop": "测试店",
        "channel": "京东", "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"}},
        {"key": "finance-context", "domain": "finance", "query": {"months": ["2026-08", "2026-09"],
            "scope": {"scope_key": "shop:测试店", "scope_type": "shop", "scope_name": "测试店", "group_name": "京东组"},
            "analysisPeriod": {"startDate": "2026-08-20", "endDate": "2026-09-18"}}}]


class BusinessDailyCollectionV3Tests(TestCase):
    def setUp(self):
        if connection.vendor != "postgresql": self.skipTest("v3 daily SQL guards require PostgreSQL")
        self.principal = Principal("v3-daily-owner@example.test", "Synthetic", "admin", None)
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
        now = timezone.now()
        AppUser.objects.create(email=self.principal.email, display_name="Synthetic", role=role,
            status="active", scope=None, version=1, created_at=now, updated_at=now)
        request = {"schemaVersion": "business-evidence-v3", "clientRequestId": uid("client"),
            "sources": sources(), "analysisRequest": {"schemaVersion": "business-analysis-request-v1",
                "question": "日经营与财务", "requestedDimensions": ["shop"], "requestedWindows": ["current"]}}
        created = plan.create(request, self.principal)
        self.run_id = created["item"]["id"]

    def page_and_checkpoint(self):
        query = sources()[0]["query"]
        periods = comparison_periods(query["startDate"], query["endDate"])
        rows = [{"rowId": "1", "platform": "京东", "shopName": "测试店", "channel": "京东",
                 "date": "2026-08-20", "metrics": {"salesCents": 100}}]
        revision = "1:" + "a" * 64
        page = {"schemaVersion": "business-analysis-v1", "sourceRef": "b" * 64,
            "sourceRevision": revision, "source": "erp_sales", "sourceDataset": None,
            "monetaryUnit": "CNY_CENT",
            "filters": {**query, "periods": periods, "limit": 100},
            "items": rows, "control": {"rowCount": 1, "typedTotals": {"salesCents": 100}},
            "pageEvidence": {"rowCount": 1, "sha256": page_digest(rows)},
            "pagination": {"limit": 100, "hasMore": False, "nextCursor": None},
            "metricSemantics": None}
        verifier = PageReconciler(); verifier.consume(page); verifier.result()
        metadata = {"sourceRevision": revision, "coverage": None, "excludedOverlappingPeriodRows": None,
            "identityCheck": None, "availableDates": None, "metricSemantics": None,
            "freshness": None, "firstCollectedAt": "2026-09-24T00:00:00+08:00",
            "lastCollectedAt": "2026-09-24T00:00:00+08:00"}
        checkpoint = {"pageCount": 1, "verifier": verifier.__dict__, "metadata": metadata}
        return page, checkpoint

    def append_physical(self, *, page=None, checkpoint=None):
        default_page, default_checkpoint = self.page_and_checkpoint()
        page = default_page if page is None else page
        checkpoint = default_checkpoint if checkpoint is None else checkpoint
        raw = canonical(page)
        parent = m.AiBusinessEvidenceRun.objects.get(pk=self.run_id)
        source = m.AiBusinessEvidenceSource.objects.get(run=parent, source_key="sales-current")
        with transaction.atomic():
            m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run=parent, source_key=source.source_key,
                sequence=1, payload_json=raw, payload_digest=digest(raw))
            m.AiBusinessEvidenceSource.objects.filter(pk=source.pk).update(version=2, checkpoint_run_version=2,
                page_count=1, stored_bytes=len(raw.encode("utf-8")), row_count=1, finished=True,
                checkpoint_json=canonical(checkpoint), updated_at=timezone.now())
            m.AiBusinessEvidenceRun.objects.filter(pk=parent.pk).update(version=2, stored_bytes=len(raw.encode("utf-8")))
        return raw

    def test_daily_source_finishes_with_actual_replay_parent_and_finance_unsealed(self):
        row, built, records, _ = catalog.load(self.run_id, self.principal)
        self.assertEqual(len(records), len(built["entries"]))
        self.assertEqual(row.status, "collecting")
        self.assertEqual(daily_reader.inspect(self.run_id, "sales-current", self.principal)["pageCount"], 0)
        raw = self.append_physical()
        result = daily_reader.inspect(self.run_id, "sales-current", self.principal)
        self.assertTrue(result["finished"])
        self.assertEqual(result["rowCount"], 1)
        self.assertEqual(result["storedBytes"], len(raw.encode("utf-8")))
        self.assertTrue(result["reconciliation"]["reconciled"])
        self.assertFalse(result["persistentEvidenceVerified"])
        row.refresh_from_db()
        self.assertEqual((row.status, row.collection_status), ("collecting", "manual"))
        self.assertEqual(m.AiBusinessEvidenceSource.objects.get(run=row, source_key="finance-context").page_count, 0)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceRun.objects.filter(pk=row.pk).update(version=3, status="sealed")

    def test_wrong_chunk_checkpoint_and_parent_cas_are_rejected(self):
        page, checkpoint = self.page_and_checkpoint()
        raw = canonical(page)
        row = m.AiBusinessEvidenceRun.objects.get(pk=self.run_id)
        source = m.AiBusinessEvidenceSource.objects.get(run=row, source_key="sales-current")
        for mode in ("missing_chunk", "wrong_schema", "wrong_source", "missing_parent", "bad_checkpoint"):
            with self.subTest(mode=mode), self.assertRaises(DatabaseError), transaction.atomic():
                if mode != "missing_chunk":
                    bad_page = {**page, "schemaVersion": "wrong"} if mode == "wrong_schema" else page
                    body = canonical(bad_page)
                    m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run=row,
                        source_key="finance-context" if mode == "wrong_source" else "sales-current",
                        sequence=1, payload_json=body, payload_digest=digest(body))
                if mode != "wrong_source":
                    m.AiBusinessEvidenceSource.objects.filter(pk=source.pk).update(version=2,
                        checkpoint_run_version=2, page_count=1, stored_bytes=len(raw.encode()), row_count=1,
                        finished=True, checkpoint_json=canonical({**checkpoint, "pageCount": 2} if mode == "bad_checkpoint" else checkpoint),
                        updated_at=timezone.now())
                if mode not in ("missing_parent", "wrong_source"):
                    m.AiBusinessEvidenceRun.objects.filter(pk=row.pk).update(version=2, stored_bytes=len(raw.encode()))
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ai_business_v3_directory_complete IMMEDIATE")

    def test_read_only_replay_rejects_self_consistent_forged_metrics(self):
        page, checkpoint = self.page_and_checkpoint()
        page["items"][0]["metrics"]["salesCents"] = 999
        page["pageEvidence"] = {"rowCount": 1, "sha256": page_digest(page["items"])}
        # A physical SQL guard cannot prove source provenance; full replay
        # compares the control total and rejects an internally consistent page.
        self.append_physical(page=page, checkpoint=checkpoint)
        with self.assertRaises(AiError):
            daily_reader.inspect(self.run_id, "sales-current", self.principal)

    def test_reverse_with_daily_facts_denied_and_late_actor_revocation_blocks_read(self):
        self.append_physical()
        migration = import_module("ai_assistant.migrations.0031_business_daily_v3_source_pages")
        with connection.schema_editor() as editor:
            with self.assertRaises(RuntimeError): migration.uninstall(apps, editor)
        original = catalog.unchanged
        def revoke(*args):
            AppUser.objects.filter(email=self.principal.email).update(version=2)
            return original(*args)
        with patch.object(catalog, "unchanged", side_effect=revoke):
            with self.assertRaises(AiError): daily_reader.inspect(self.run_id, "sales-current", self.principal)

    def test_daily_first_then_signed_finance_advance_and_both_source_replays(self):
        self.append_physical()
        pending = finance_reader.inspect(self.run_id, "finance-context", self.principal)
        self.assertEqual(pending["pageCount"], 0)
        raw = finance_row(1)
        raw.update(scope_key="shop:测试店", scope_type="shop", scope_name="测试店", group_name="京东组")
        args = finance_fixture([raw])
        args["query"] = sources()[1]["query"].copy()
        args["query"].pop("analysisPeriod")
        args["query"]["months"] = ["2026-08", "2026-09"]
        source = finance_source.build(**args, analysis_period=sources()[1]["query"]["analysisPeriod"])
        manifest = source.manifest
        finance_query = sources()[1]["query"]
        publication = {"months": manifest["months"], "batches": manifest["batches"],
                       "missingMonths": ["2026-09"]}
        finance_page = owned_page(finance_query, publication, source.page()["rows"], offset=0, total=1)
        tool_entry = {"name": finance_reader.TOOL, "risk": "read_only", "allowedRoles": ["admin"],
                      "scopePolicy": "unscoped_only", "execution": {"mode": "direct", "allowedSurfaces": ["business_collection"]}}
        m.AiToolAuditLogs.objects.create(id=uid("audit"), request_id="daily-first-finance",
            invocation_id=uid("invocation"), actor_email=self.principal.email, actor_role="admin",
            surface="business_collection", tool_name=finance_reader.TOOL, arguments_json="{}",
            status="succeeded", duration_ms=1, response_digest=digest(canonical(finance_page)))
        with patch.object(transport, "catalog", return_value=[tool_entry]), \
                patch.object(transport, "execute_tool", return_value={"ok": True,
                    "toolName": finance_reader.TOOL, "data": finance_page}):
            completed = finance_reader.advance_finance_source(self.run_id, "finance-context", 2,
                self.principal, "daily-first-finance")
        self.assertTrue(completed["finished"])
        self.assertTrue(daily_reader.inspect(self.run_id, "sales-current", self.principal)["finished"])
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=self.run_id).status, "collecting")
