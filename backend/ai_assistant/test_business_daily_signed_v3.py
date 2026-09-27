"""Internal signed tool decision and CAS probes for v3 daily sources."""
from unittest.mock import patch

from django.db import connection, transaction
from django.test import TestCase
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis.contracts import PageReconciler, canonical, comparison_periods, digest as row_digest
from sales.auth import Principal

from . import business_daily_collection_v3 as daily, business_evidence_v3 as plan
from . import business_v3_tool_receipts as receipts, models as m, transport
from .policy import AiError, digest, uid


def tool(name):
    return {"name": name, "risk": "read_only", "allowedRoles": ["admin"],
            "scopePolicy": "unscoped_only", "execution": {"mode": "direct", "allowedSurfaces": ["business_collection"]}}


class BusinessDailySignedV3Tests(TestCase):
    def setUp(self):
        if connection.vendor != "postgresql": self.skipTest("signed v3 daily append needs PostgreSQL")
        self.principal = Principal("v3-daily-signed@example.test", "Synthetic", "admin", None)
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
        now = timezone.now()
        AppUser.objects.create(email=self.principal.email, display_name="Synthetic", role=role,
            status="active", scope=None, version=1, created_at=now, updated_at=now)
        self.query = {"platform": "京东", "shop": "测试店", "channel": "京东",
            "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"}
        body = {"schemaVersion": "business-evidence-v3", "clientRequestId": uid("client"),
            "sources": [{"key": "sales-current", "domain": "sales", "query": self.query},
                {"key": "finance-context", "domain": "finance", "query": {
                    "months": ["2026-08", "2026-09"], "scope": {"scope_key": "shop:测试店",
                        "scope_type": "shop", "scope_name": "测试店", "group_name": "京东组"},
                    "analysisPeriod": {"startDate": "2026-08-20", "endDate": "2026-09-18"}}}],
            "analysisRequest": {"schemaVersion": "business-analysis-request-v1", "question": "日来源续读",
                "requestedDimensions": ["shop"], "requestedWindows": ["current"]}}
        self.run_id = plan.create(body, self.principal)["item"]["id"]

    def page(self, row_id, *, more, first):
        records = [{"rowId": str(row_id), "platform": "京东", "shopName": "测试店", "channel": "京东",
            "date": "2026-08-20", "metrics": {"salesCents": 100}}]
        return {"schemaVersion": "business-analysis-v1", "sourceRef": "b"*64, "sourceRevision": "1:2",
            "source": "erp_sales", "sourceDataset": None, "monetaryUnit": "CNY_CENT",
            "filters": {**self.query, "periods": comparison_periods(self.query["startDate"], self.query["endDate"]), "limit": 100},
            "items": records, "control": {"rowCount": 2, "typedTotals": {"salesCents": 200}} if first else None,
            "pageEvidence": {"rowCount": 1, "sha256": row_digest(records)},
            "pagination": {"limit": 100, "hasMore": more, "nextCursor": "signed-cursor" if more else None},
            "metricSemantics": None}

    def catalog(self):
        return [tool(daily.INITIAL_TOOL), tool("get_business_sales_continuation_page")]

    def audit_page(self, request_id, tool_name, page):
        m.AiToolAuditLogs.objects.create(id=uid("audit"), request_id=request_id,
            invocation_id=uid("invocation"), actor_email=self.principal.email, actor_role="admin",
            surface="business_collection", tool_name=tool_name, arguments_json="{}", status="succeeded",
            duration_ms=1, response_digest=digest(canonical(page)))

    def manually_append_first(self, first):
        verifier = PageReconciler(); verifier.consume(first)
        metadata = {"sourceRevision": first["sourceRevision"], "coverage": None,
            "excludedOverlappingPeriodRows": None, "identityCheck": None, "availableDates": None,
            "metricSemantics": None, "freshness": None, "firstCollectedAt": "2026-09-24T00:00:00+08:00",
            "lastCollectedAt": "2026-09-24T00:00:00+08:00"}
        checkpoint = {"pageCount": 1, "verifier": verifier.__dict__, "metadata": metadata}
        raw = canonical(first)
        parent = m.AiBusinessEvidenceRun.objects.get(pk=self.run_id)
        source = m.AiBusinessEvidenceSource.objects.get(run=parent, source_key="sales-current")
        with transaction.atomic():
            m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run=parent, source_key=source.source_key,
                sequence=1, payload_json=raw, payload_digest=digest(raw))
            m.AiBusinessEvidenceSource.objects.filter(pk=source.pk).update(version=2, checkpoint_run_version=2,
                page_count=1, stored_bytes=len(raw.encode()), row_count=1, finished=False,
                checkpoint_json=canonical(checkpoint), updated_at=timezone.now())
            m.AiBusinessEvidenceRun.objects.filter(pk=parent.pk).update(version=2, stored_bytes=len(raw.encode()))

    def test_first_signed_page_then_verified_continuation_completes_only_daily(self):
        first, second = self.page(1, more=True, first=True), self.page(2, more=False, first=False)
        seen = []
        def execute(name, args, principal, **kwargs):
            seen.append((name, args))
            page = first if len(seen) == 1 else second
            self.audit_page(kwargs["request_id"], name, page)
            return {"ok": True, "toolName": name, "data": page}
        with patch.object(transport, "catalog", return_value=self.catalog()), \
                patch.object(transport, "execute_tool", side_effect=execute):
            one = daily.advance_daily_source(self.run_id, "sales-current", 1, self.principal, "daily-first")
            self.assertEqual(one["pageCount"], 1)
            self.assertFalse(one["finished"])
            two = daily.advance_daily_source(self.run_id, "sales-current", 2, self.principal, "daily-second")
        self.assertTrue(two["finished"])
        self.assertEqual(two["rowCount"], 2)
        self.assertEqual([name for name, _ in seen], [daily.INITIAL_TOOL, "get_business_sales_continuation_page"])
        self.assertEqual(seen[1][1]["expectedSourceRef"], first["sourceRef"])
        self.assertEqual(seen[1][1]["expectedRevision"], first["sourceRevision"])
        self.assertEqual(seen[1][1]["expectedLastId"], 1)
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), 2)
        self.assertTrue(receipts.require_complete(self.run_id, "sales-current", self.principal)["auditBound"])
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=self.run_id).status, "collecting")
        self.assertEqual(m.AiBusinessEvidenceSource.objects.get(run_id=self.run_id, source_key="finance-context").page_count, 0)

    def test_stale_revision_invalid_page_and_wrong_actor_never_append(self):
        first = self.page(1, more=True, first=True)
        self.manually_append_first(first)
        stale = self.page(2, more=False, first=False); stale["sourceRevision"] = "2:3"
        with patch.object(transport, "catalog", return_value=self.catalog()), \
                patch.object(transport, "execute_tool", return_value={"ok": True,
                    "toolName": "get_business_sales_continuation_page", "data": stale}):
            with self.assertRaises(AiError):
                daily.advance_daily_source(self.run_id, "sales-current", 2, self.principal, "stale-source")
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), 1)
        with self.assertRaises(AiError):
            daily.advance_daily_source(self.run_id, "finance-context", 2, self.principal, "wrong-domain")
        with self.assertRaises(AiError):
            daily.advance_daily_source(self.run_id, "sales-current", 2,
                Principal(self.principal.email, "Scoped", "admin", {"warehouses": [], "channels": [], "platforms": []}), "scoped")

    def test_old_cursor_is_forwarded_only_to_domain_continuation_tool(self):
        first = self.page(1, more=True, first=True)
        first["pagination"]["nextCursor"] = "expired-original-signed-cursor"
        self.manually_append_first(first)
        second = self.page(2, more=False, first=False)
        observed = []
        def continued(name, args, principal, **kwargs):
            observed.append((name, args))
            self.audit_page(kwargs["request_id"], name, second)
            return {"ok": True, "toolName": name, "data": second}
        with patch.object(transport, "catalog", return_value=self.catalog()), \
                patch.object(transport, "execute_tool", side_effect=continued):
            result = daily.advance_daily_source(self.run_id, "sales-current", 2,
                self.principal, "expired-through-owner")
        self.assertTrue(result["finished"])
        self.assertEqual(observed[0][0], "get_business_sales_continuation_page")
        self.assertEqual(observed[0][1]["cursor"], "expired-original-signed-cursor")

    def test_invalid_cursor_error_and_cas_race_do_not_write(self):
        first = self.page(1, more=True, first=True)
        self.manually_append_first(first)
        before = m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count()
        with patch.object(transport, "catalog", return_value=self.catalog()), \
                patch.object(transport, "execute_tool", return_value={"ok": False,
                    "toolName": "get_business_sales_continuation_page", "error": {"code": "invalid_cursor"}}):
            with self.assertRaises(AiError):
                daily.advance_daily_source(self.run_id, "sales-current", 2, self.principal, "bad-cursor")
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), before)
        second = self.page(2, more=False, first=False)
        def race(*args, **kwargs):
            AppUser.objects.filter(email=self.principal.email).update(version=2)
            return {"ok": True, "toolName": "get_business_sales_continuation_page", "data": second}
        with patch.object(transport, "catalog", return_value=self.catalog()), \
                patch.object(transport, "execute_tool", side_effect=race):
            with self.assertRaises(AiError):
                daily.advance_daily_source(self.run_id, "sales-current", 2, self.principal, "actor-race")
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), before)

    def test_early_page_cap_refuses_before_replay_or_signed_fetch(self):
        with patch.object(daily, "MAX_LIVE_PAGES", 0), patch.object(daily, "inspect") as replay, \
                patch.object(transport, "catalog") as tool_catalog:
            with self.assertRaises(AiError) as caught:
                daily.advance_daily_source(self.run_id, "sales-current", 1, self.principal, "bounded")
            self.assertEqual(caught.exception.status, 413)
            replay.assert_not_called(); tool_catalog.assert_not_called()

    def test_parent_cas_race_during_signed_fetch_keeps_single_chunk(self):
        first = self.page(1, more=True, first=True)
        def race(*args, **kwargs):
            self.manually_append_first(first)
            self.audit_page(kwargs["request_id"], daily.INITIAL_TOOL, first)
            return {"ok": True, "toolName": daily.INITIAL_TOOL, "data": first}
        with patch.object(transport, "catalog", return_value=self.catalog()), \
                patch.object(transport, "execute_tool", side_effect=race):
            with self.assertRaises(AiError) as caught:
                daily.advance_daily_source(self.run_id, "sales-current", 1, self.principal, "cas-race")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).count(), 1)
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=self.run_id).version, 2)
