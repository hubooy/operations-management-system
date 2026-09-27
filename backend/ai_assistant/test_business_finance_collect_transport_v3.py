"""Actual AI ledger tests for signed-tool-only finance append decisions."""
from unittest.mock import patch

from django.db import connection, transaction
from django.test import TestCase
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis import finance_collection_state as verifier
from business_analysis.contracts import canonical, digest as content_digest
from business_analysis.test_finance_collection_state import owned_page, sources as finance_fixture
from sales.auth import Principal

from . import business_evidence_v3 as plan, business_finance_collection_v3 as collector
from . import business_v3_tool_receipts as receipts, models as m, transport
from .policy import AiError, digest, uid


def entry():
    return {"name": collector.TOOL, "risk": "read_only", "allowedRoles": ["admin"],
            "scopePolicy": "unscoped_only", "execution": {"mode": "direct", "allowedSurfaces": ["business_collection"]}}


class FinanceCollectorTransportTests(TestCase):
    def setUp(self):
        if connection.vendor != "postgresql": self.skipTest("finance v3 append needs PostgreSQL")
        self.principal = Principal("finance-collector@example.test", "Synthetic", "admin", None)
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
        now = timezone.now()
        AppUser.objects.create(email=self.principal.email, display_name="Synthetic", role=role,
            status="active", scope=None, version=1, created_at=now, updated_at=now)
        from business_analysis.test_finance_source import row as finance_row
        _, query, publication, rows = finance_fixture([finance_row(1), finance_row(2, subject_name="第二行")])
        self.query, self.publication, self.rows = query, publication, rows
        sources = [{"key": "daily", "domain": "sales", "query": {"platform": "京东", "shop": "测试店",
            "channel": "京东", "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"}},
            {"key": "finance", "domain": "finance", "query": query}]
        result = plan.create({"schemaVersion": "business-evidence-v3", "clientRequestId": uid("client"),
            "sources": sources, "analysisRequest": {"schemaVersion": "business-analysis-request-v1",
                "question": "店铺与财务", "requestedDimensions": ["shop"], "requestedWindows": ["current"]}}, self.principal)
        self.run_id = result["item"]["id"]

    def page(self, first=True):
        return owned_page(self.query, self.publication, self.rows[:1] if first else self.rows[1:],
            offset=0 if first else 1, total=2)

    def tool(self, page, *, side_effect=None, entries=None):
        def execute(name, arguments, principal, **kwargs):
            if side_effect: side_effect()
            self.assertEqual(name, collector.TOOL)
            self.assertEqual(arguments["query"], self.query)
            self.audit_page(kwargs["request_id"], name, page)
            return {"ok": True, "toolName": collector.TOOL, "data": page}
        return patch.object(transport, "catalog", return_value=entries if entries is not None else [entry()]), \
            patch.object(transport, "execute_tool", side_effect=execute)

    def audit_page(self, request_id, tool_name, page):
        m.AiToolAuditLogs.objects.create(id=uid("audit"), request_id=request_id,
            invocation_id=uid("invocation"), actor_email=self.principal.email, actor_role="admin",
            surface="business_collection", tool_name=tool_name, arguments_json="{}", status="succeeded",
            duration_ms=1, response_digest=digest(canonical(page)))

    def persist_fixture_directly(self, page):
        """Test-only race writer: a completed prior page commits during fetch."""
        checkpoint = verifier.consume(None, page, trusted_query=self.query)
        raw = canonical(page)
        parent = m.AiBusinessEvidenceRun.objects.get(pk=self.run_id)
        source = m.AiBusinessEvidenceSource.objects.get(run=parent, source_key="finance")
        with transaction.atomic():
            m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run=parent,
                source_key="finance", sequence=1, payload_json=raw, payload_digest=digest(raw))
            m.AiBusinessEvidenceSource.objects.filter(pk=source.pk).update(version=2, checkpoint_run_version=2,
                page_count=1, stored_bytes=len(raw.encode()), row_count=checkpoint["rowsRead"],
                finished=False, checkpoint_json=canonical(checkpoint), updated_at=timezone.now())
            m.AiBusinessEvidenceRun.objects.filter(pk=parent.pk).update(version=2, stored_bytes=len(raw.encode()))

    def test_two_signed_tool_pages_append_and_finish_only_finance_source(self):
        first = self.page()
        second = self.page(first=False)
        seen = []
        def execute(name, arguments, principal, **kwargs):
            self.assertEqual(name, collector.TOOL)
            self.assertEqual(kwargs["surface"], "business_collection")
            self.assertEqual(kwargs["policy_digest"], digest([entry()]))
            seen.append(arguments)
            page = first if len(seen) == 1 else second
            self.audit_page(kwargs["request_id"], name, page)
            return {"ok": True, "toolName": name, "data": page}
        with patch.object(transport, "catalog", return_value=[entry()]), \
                patch.object(transport, "execute_tool", side_effect=execute):
            one = collector.advance_finance_source(self.run_id, "finance", 1, self.principal, "finance-step-1")
            self.assertEqual(one["pageCount"], 1)
            self.assertFalse(one["finished"])
            two = collector.advance_finance_source(self.run_id, "finance", 2, self.principal, "finance-step-2")
        self.assertTrue(two["finished"])
        self.assertEqual(two["rowCount"], 2)
        self.assertEqual(seen[1], verifier.next_arguments(verifier.consume(None, first, trusted_query=self.query),
            trusted_query=self.query))
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), 2)
        self.assertEqual(m.AiBusinessSourceToolReceipt.objects.filter(run_id=self.run_id).count(), 2)
        self.assertTrue(receipts.require_complete(self.run_id, "finance", self.principal)["auditBound"])
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=self.run_id).status, "collecting")
        self.assertEqual(m.AiBusinessEvidenceSource.objects.get(run_id=self.run_id, source_key="daily").page_count, 0)
        self.assertFalse(two["persistentEvidenceVerified"])

    def test_forged_page_role_source_and_missing_policy_do_not_write(self):
        forged = self.page()
        forged["rows"][0]["amount_cents"] = 999
        forged["pageEvidence"]["sha256"] = content_digest(forged["rows"])
        forged["pageDigest"] = content_digest({key: item for key, item in forged.items() if key != "pageDigest"})
        catalog, execute = self.tool(forged)
        with catalog, execute, self.assertRaises(AiError):
            collector.advance_finance_source(self.run_id, "finance", 1, self.principal, "forged-page")
        self.assertFalse(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).exists())
        with self.assertRaises(TypeError):
            collector.advance_finance_source(self.run_id, "finance", 1, self.principal, "forged-caller", page=forged)
        with self.assertRaises(AiError):
            collector.advance_finance_source(self.run_id, "daily", 1, self.principal, "wrong-source")
        with self.assertRaises(AiError):
            collector.advance_finance_source(self.run_id, "finance", 1,
                Principal(self.principal.email, "Scoped", "admin", {"warehouses": [], "channels": [], "platforms": []}), "scoped")
        with patch.object(transport, "catalog", return_value=[]), patch.object(transport, "execute_tool") as unused:
            with self.assertRaises(AiError):
                collector.advance_finance_source(self.run_id, "finance", 1, self.principal, "missing-tool")
            unused.assert_not_called()

    def test_stale_revision_and_cas_race_keep_one_source_chain(self):
        first = self.page()
        self.persist_fixture_directly(first)
        stale = self.page(first=False); stale["sourceRevision"] = "9:" + "a"*64
        stale["pageDigest"] = content_digest({key: item for key, item in stale.items() if key != "pageDigest"})
        catalog, execute = self.tool(stale)
        with catalog, execute, self.assertRaises(AiError):
            collector.advance_finance_source(self.run_id, "finance", 2, self.principal, "stale-revision")
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), 1)

    def test_parent_cas_race_after_fetch_rolls_back_second_append(self):
        first = self.page()
        catalog, execute = self.tool(first, side_effect=lambda: self.persist_fixture_directly(first))
        with catalog, execute, self.assertRaises(AiError) as caught:
            collector.advance_finance_source(self.run_id, "finance", 1, self.principal, "racing-step")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), 1)
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=self.run_id).version, 2)

    def test_live_page_cap_refuses_before_full_ledger_replay_or_transport(self):
        with patch.object(collector, "MAX_LIVE_PAGES", 0), \
                patch.object(collector, "inspect") as replay, patch.object(transport, "catalog") as catalog:
            with self.assertRaises(AiError) as caught:
                collector.advance_finance_source(self.run_id, "finance", 1, self.principal, "bounded-step")
            self.assertEqual(caught.exception.status, 413)
            replay.assert_not_called()
            catalog.assert_not_called()
