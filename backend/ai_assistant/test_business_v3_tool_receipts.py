"""Isolated PG proof that only real successful audit-bound v3 chunks qualify."""
from unittest.mock import patch

from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis import finance_collection_state as verifier
from business_analysis.contracts import PageReconciler, canonical, comparison_periods
from business_analysis.test_finance_collection_state import owned_page, sources as finance_fixture
from sales.auth import Principal

from . import business_evidence_v3 as plan, business_finance_collection_v3 as finance
from . import business_v3_tool_receipts as receipts, models as m, transport
from .policy import AiError, digest, uid


class BusinessV3ToolReceiptTests(TestCase):
    def setUp(self):
        if connection.vendor != "postgresql": self.skipTest("v3 receipt guard requires PostgreSQL")
        self.principal = Principal("v3-receipt@example.test", "Synthetic", "admin", None)
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
        now = timezone.now()
        AppUser.objects.create(email=self.principal.email, display_name="Synthetic", role=role,
            status="active", scope=None, version=1, created_at=now, updated_at=now)
        _, query, publication, rows = finance_fixture()
        self.query, self.page = query, owned_page(query, publication, rows, offset=0, total=len(rows))
        body = {"schemaVersion": "business-evidence-v3", "clientRequestId": uid("client"),
            "sources": [{"key": "daily", "domain": "sales", "query": {"platform": "京东", "shop": "测试店",
                "channel": "京东", "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"}},
                {"key": "finance", "domain": "finance", "query": query}],
            "analysisRequest": {"schemaVersion": "business-analysis-request-v1", "question": "财务证据",
                "requestedDimensions": ["shop"], "requestedWindows": ["current"]}}
        self.run_id = plan.create(body, self.principal)["item"]["id"]

    def audit(self, *, request_id="receipt-step", status="succeeded", digest_value=None, tool=None):
        return m.AiToolAuditLogs.objects.create(id=uid("audit"), request_id=request_id,
            invocation_id=uid("invocation"), actor_email=self.principal.email, actor_role="admin",
            surface="business_collection", tool_name=tool or finance.TOOL, arguments_json="{}",
            status=status, duration_ms=1, response_digest=digest_value or digest(canonical(self.page)))

    def test_signed_tool_audit_binds_exact_chunk_once(self):
        def execute(name, arguments, principal, **kwargs):
            self.audit(request_id=kwargs["request_id"])
            return {"ok": True, "toolName": name, "data": self.page}
        catalog = [{"name": finance.TOOL, "risk": "read_only", "allowedRoles": ["admin"],
            "scopePolicy": "unscoped_only", "execution": {"mode": "direct", "allowedSurfaces": ["business_collection"]}}]
        with patch.object(transport, "catalog", return_value=catalog), \
                patch.object(transport, "execute_tool", side_effect=execute):
            result = finance.advance_finance_source(self.run_id, "finance", 1, self.principal, "receipt-step")
        self.assertTrue(result["finished"])
        proof = receipts.require_complete(self.run_id, "finance", self.principal)
        self.assertEqual((proof["pageCount"], proof["receiptCount"]), (1, 1))
        self.assertFalse(proof["sourceAuthorityVerified"])
        row = m.AiBusinessSourceToolReceipt.objects.get(run_id=self.run_id)
        self.assertEqual(row.response_digest, row.chunk.payload_digest)
        self.assertEqual(row.response_digest, row.audit.response_digest)
        self.assertEqual(row.payload_bytes, len(row.chunk.payload_json.encode("utf-8")))
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessSourceToolReceipt.objects.create(chunk=row.chunk, audit=row.audit,
                run=row.run, source=row.source, sequence=1, request_id=row.request_id,
                invocation_id=row.invocation_id, actor_email=row.actor_email, tool_name=row.tool_name,
                surface=row.surface, response_digest=row.response_digest, payload_bytes=row.payload_bytes,
                source_ref=row.source_ref, source_revision=row.source_revision)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessSourceToolReceipt.objects.filter(pk=row.pk).update(actor_email="other@example.test")

    def test_old_direct_fact_without_receipt_is_preserved_but_untrusted(self):
        parent = m.AiBusinessEvidenceRun.objects.get(pk=self.run_id)
        source = m.AiBusinessEvidenceSource.objects.get(run=parent, source_key="finance")
        state = verifier.consume(None, self.page, trusted_query=self.query)
        raw = canonical(self.page)
        with transaction.atomic():
            m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run=parent, source_key=source.source_key,
                sequence=1, payload_json=raw, payload_digest=digest(raw))
            m.AiBusinessEvidenceSource.objects.filter(pk=source.pk).update(version=2, checkpoint_run_version=2,
                page_count=1, stored_bytes=len(raw.encode()), row_count=state["rowsRead"], finished=True,
                checkpoint_json=canonical(state), updated_at=timezone.now())
            m.AiBusinessEvidenceRun.objects.filter(pk=parent.pk).update(version=2, stored_bytes=len(raw.encode()))
        self.assertTrue(finance.inspect(self.run_id, "finance", self.principal)["finished"])
        with self.assertRaises(AiError): receipts.require_complete(self.run_id, "finance", self.principal)
        audit = self.audit()
        chunk = m.AiBusinessEvidenceChunk.objects.get(run=parent)
        with self.assertRaises(DatabaseError), transaction.atomic():
            receipts.bind_page(parent=parent, source=source, chunk=chunk, principal=self.principal,
                request_id=audit.request_id, tool_name=finance.TOOL, page=self.page,
                encoded=raw, audit={"id": audit.id, "invocation_id": audit.invocation_id})

    def test_missing_duplicate_failed_or_wrong_digest_audit_blocks_append(self):
        raw = canonical(self.page)
        with self.assertRaises(AiError):
            receipts.audit_for_page(self.principal, request_id="missing", tool_name=finance.TOOL, encoded=raw)
        self.audit(request_id="failed", status="failed")
        with self.assertRaises(AiError):
            receipts.audit_for_page(self.principal, request_id="failed", tool_name=finance.TOOL, encoded=raw)
        self.audit(request_id="wrong", digest_value="0"*64)
        with self.assertRaises(AiError):
            receipts.audit_for_page(self.principal, request_id="wrong", tool_name=finance.TOOL, encoded=raw)
        self.audit(request_id="duplicate"); self.audit(request_id="duplicate")
        with self.assertRaises(AiError):
            receipts.audit_for_page(self.principal, request_id="duplicate", tool_name=finance.TOOL, encoded=raw)
        self.assertFalse(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).exists())

    def test_database_rejects_forged_actor_surface_digest_source_and_sequence(self):
        # A bare physical chunk is intentionally not a valid completed v3
        # directory; roll the entire adversarial fixture back before Django's
        # deferred global directory check runs at TestCase teardown.
        with transaction.atomic():
            parent = m.AiBusinessEvidenceRun.objects.get(pk=self.run_id)
            source = m.AiBusinessEvidenceSource.objects.get(run=parent, source_key="finance")
            other_source = m.AiBusinessEvidenceSource.objects.get(run=parent, source_key="daily")
            raw = canonical(self.page)
            audit = self.audit(request_id="db-guard-step")
            chunk = m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run=parent,
                source_key=source.source_key, sequence=1, payload_json=raw, payload_digest=digest(raw))
            correct = {"chunk": chunk, "audit": audit, "run": parent, "source": source,
                "sequence": 1, "request_id": audit.request_id, "invocation_id": audit.invocation_id,
                "actor_email": self.principal.email, "tool_name": finance.TOOL,
                "surface": "business_collection", "response_digest": chunk.payload_digest,
                "payload_bytes": len(raw.encode("utf-8")), "source_ref": self.page["sourceRef"],
                "source_revision": self.page["sourceRevision"]}
            for changed in ({"actor_email": "forged@example.test"}, {"surface": "chat"},
                            {"response_digest": "0" * 64}, {"source": other_source},
                            {"sequence": 2}, {"tool_name": "get_business_source_page"},
                            {"source_revision": "stale"}):
                with self.subTest(changed=changed), self.assertRaises(DatabaseError), transaction.atomic():
                    m.AiBusinessSourceToolReceipt.objects.create(**{**correct, **changed})
            null_digest_audit = m.AiToolAuditLogs.objects.create(id=uid("audit"),
                request_id="null-digest", invocation_id=uid("invocation"),
                actor_email=self.principal.email, actor_role="admin", surface="business_collection",
                tool_name=finance.TOOL, arguments_json="{}", status="succeeded",
                duration_ms=1, response_digest=None)
            with self.assertRaises(DatabaseError), transaction.atomic():
                m.AiBusinessSourceToolReceipt.objects.create(**{**correct,
                    "audit": null_digest_audit, "request_id": null_digest_audit.request_id,
                    "invocation_id": null_digest_audit.invocation_id})
            self.assertFalse(m.AiBusinessSourceToolReceipt.objects.filter(run=parent).exists())
            source.refresh_from_db()
            self.assertEqual((source.page_count, source.finished), (0, False))
            transaction.set_rollback(True)
        self.assertFalse(m.AiBusinessEvidenceChunk.objects.filter(run_id=self.run_id).exists())

    def test_genuine_audit_and_page_cannot_complete_another_shops_source(self):
        # 0031's physical chunk guard admits the same platform/window. The
        # receipt must still fail authority qualification for a different shop.
        foreign_query = {"platform": "京东", "shop": "另一店", "channel": "京东",
            "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"}
        rows = [{"rowId": "1", "platform": "京东", "shopName": "另一店", "channel": "京东",
            "date": "2026-08-20", "metrics": {"salesCents": 100}}]
        page = {"schemaVersion": "business-analysis-v1", "sourceRef": "b" * 64,
            "sourceRevision": "1:2", "source": "erp_sales", "sourceDataset": None,
            "monetaryUnit": "CNY_CENT", "filters": {**foreign_query,
                "periods": comparison_periods(foreign_query["startDate"], foreign_query["endDate"]),
                "limit": 100}, "items": rows,
            "control": {"rowCount": 1, "typedTotals": {"salesCents": 100}},
            "pageEvidence": {"rowCount": 1, "sha256": digest(rows)},
            "pagination": {"limit": 100, "hasMore": False, "nextCursor": None},
            "metricSemantics": None}
        verifier = PageReconciler(); verifier.consume(page); verifier.result()
        checkpoint = {"pageCount": 1, "verifier": verifier.__dict__, "metadata": {
            "sourceRevision": page["sourceRevision"], "coverage": None,
            "excludedOverlappingPeriodRows": None, "identityCheck": None,
            "availableDates": None, "metricSemantics": None, "freshness": None,
            "firstCollectedAt": "2026-09-24T00:00:00+08:00",
            "lastCollectedAt": "2026-09-24T00:00:00+08:00"}}
        raw = canonical(page)
        parent = m.AiBusinessEvidenceRun.objects.get(pk=self.run_id)
        source = m.AiBusinessEvidenceSource.objects.get(run=parent, source_key="daily")
        audit = m.AiToolAuditLogs.objects.create(id=uid("audit"), request_id="foreign-page",
            invocation_id=uid("invocation"), actor_email=self.principal.email, actor_role="admin",
            surface="business_collection", tool_name="get_business_source_page", arguments_json="{}",
            status="succeeded", duration_ms=1, response_digest=digest(raw))
        with transaction.atomic():
            chunk = m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run=parent,
                source_key=source.source_key, sequence=1, payload_json=raw, payload_digest=digest(raw))
            m.AiBusinessSourceToolReceipt.objects.create(chunk=chunk, audit=audit, run=parent,
                source=source, sequence=1, request_id=audit.request_id,
                invocation_id=audit.invocation_id, actor_email=self.principal.email,
                tool_name=audit.tool_name, surface="business_collection",
                response_digest=digest(raw), payload_bytes=len(raw.encode("utf-8")),
                source_ref=page["sourceRef"], source_revision=page["sourceRevision"])
            m.AiBusinessEvidenceSource.objects.filter(pk=source.pk).update(version=2,
                checkpoint_run_version=2, page_count=1, stored_bytes=len(raw.encode("utf-8")),
                row_count=1, finished=True, checkpoint_json=canonical(checkpoint),
                updated_at=timezone.now())
            m.AiBusinessEvidenceRun.objects.filter(pk=parent.pk).update(version=2,
                stored_bytes=len(raw.encode("utf-8")))
        self.assertEqual(m.AiBusinessSourceToolReceipt.objects.filter(source=source).count(), 1)
        with self.assertRaises(AiError):
            receipts.require_complete(self.run_id, "daily", self.principal)
