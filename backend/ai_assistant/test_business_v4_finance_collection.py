"""Isolated PostgreSQL v4 signed monthly finance collection and replay."""
import json
from unittest.mock import patch

from django.db import connection, transaction
from django.test import TestCase
from django.utils import timezone

from access_control.models import AppUser
from business_analysis import evidence_v4, finance_collection_state_v4 as verifier
from finance import business_evidence_page as owning
from finance.errors import FinanceApiError
from finance.models import FinanceDataRevision, FinanceLine, FinanceMonth
from finance.tests.test_business_evidence_page import FinanceEvidencePageTests
from sales.auth import Principal

from . import (business_v4_finance_collection as collector,
    business_v4_finance_replay as replay, models as m, transport)
from .policy import AiError, canonical, digest, uid


def tool():
    return {"name": collector.TOOL, "risk": "read_only", "allowedRoles": ["admin"],
        "scopePolicy": "unscoped_only", "execution": {"mode": "direct",
            "allowedSurfaces": ["business_collection"]}}


class BusinessV4FinanceCollectionTests(TestCase):
    extend_rows = FinanceEvidencePageTests.extend_rows

    def setUp(self):
        FinanceEvidencePageTests.setUp(self)
        self.plan()

    def plan(self):
        daily = {"key": "sales-current", "domain": "sales", "query": {
            "platform": "京东", "shop": "测试店", "channel": "京东",
            "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"}}
        finance = {"key": "finance-context", "domain": "finance", "query": self.query}
        plan = evidence_v4.build_plan(client_request_id=uid("v4-client"),
            sources=[daily, finance],
            measurements=[{"sourceKey": "sales-current", "measuredRowCount": 0,
                "maxRowUtf8Bytes": 0, "pageEnvelopeUtf8Bytes": 2048,
                "sourceRevisionHint": "0:" + "a" * 12},
                {"sourceKey": "finance-context", "measuredRowCount": 100,
                 "maxRowUtf8Bytes": 1200, "pageEnvelopeUtf8Bytes": 2048,
                 "sourceRevisionHint": "7:" + "a" * 64}],
            analysis_request={"schemaVersion": "business-analysis-request-v1",
                "question": "财报按自然月独立取证", "requestedDimensions": ["shop"],
                "requestedWindows": ["current"]})
        with transaction.atomic():
            raw = canonical(plan)
            self.parent = m.AiBusinessV4Run.objects.create(id=uid("v4-run"),
                owner_email=self.principal.email,
                client_request_id=plan["clientRequestId"], plan_json=raw,
                plan_digest=digest(raw), run_identity_digest=plan["runIdentityDigest"])
            self.sources = {}
            for entry in plan["sourcePlans"]:
                self.sources[entry["sourceKey"]] = m.AiBusinessV4Source.objects.create(
                    id=uid("v4-source"), run=self.parent, source_key=entry["sourceKey"],
                    ordinal=entry["ordinal"], domain=entry["domain"],
                    temporal_role=entry["temporalRole"],
                    query_json=canonical(entry["query"]),
                    query_digest=entry["queryDigest"],
                    source_identity_digest=entry["sourceIdentityDigest"],
                    source_revision_hint=entry["sourceRevisionHint"])
        self.calls = []
        self.audit_override = None
        self.page_query_override = None

    def owner(self, name, arguments, principal, **kwargs):
        self.calls.append((name, arguments))
        query = self.page_query_override or arguments["query"]
        page = owning.read_page(principal, query, offset=arguments["offset"],
            after_id=arguments["afterId"],
            expected_source_ref=arguments.get("expectedSourceRef"),
            expected_revision=arguments.get("expectedRevision"))
        m.AiToolAuditLogs.objects.create(id=uid("audit"),
            request_id=kwargs["request_id"], invocation_id=uid("invocation"),
            actor_email=self.principal.email, actor_role="admin",
            surface="business_collection", tool_name=name,
            arguments_json=canonical({"argumentsDigest":
                self.audit_override or digest(arguments)}),
            status="succeeded", duration_ms=1,
            response_digest=digest(canonical(page)))
        return {"ok": True, "toolName": name, "data": page}

    def advance(self, version, request_id):
        with patch.object(transport, "catalog", return_value=[tool()]), \
                patch.object(transport, "execute_tool", side_effect=self.owner):
            return collector.advance(self.parent.id, "finance-context", version,
                self.principal, request_id)

    def complete(self):
        version = 1
        for number in range(1, 20):
            result = self.advance(version, f"finance-{number}")
            if result["finished"]:
                return result
            version = result["runVersion"]
        self.fail("finance fixture did not finish within bounded pages")

    def test_real_two_page_monthly_scope_resume_and_full_replay(self):
        self.extend_rows(90)
        FinanceLine.objects.filter(month="2026-08", scope_key="business",
            metric_key="net_sales").update(amount_cents=0)
        FinanceLine.objects.filter(month="2026-09", scope_key="business",
            metric_key="net_sales").update(amount_cents=None)
        result = self.complete()
        self.assertEqual(result["pageCount"], 2)
        self.assertEqual([name for name, _ in self.calls], [collector.TOOL] * 2)
        first = json.loads(m.AiBusinessV4Chunk.objects.get(run=self.parent,
            source=self.sources["finance-context"], sequence=1).payload_json)
        self.assertEqual(self.calls[1][1]["offset"], first["pagination"]["nextOffset"])
        self.assertEqual(self.calls[1][1]["afterId"], first["pagination"]["nextLastId"])
        self.assertEqual(self.calls[1][1]["expectedSourceRef"], first["sourceRef"])
        proof = replay.inspect(self.parent.id, "finance-context", self.principal)
        self.assertEqual(proof["pageCount"], 2)
        self.assertEqual(proof["monthlyContext"]["rowCount"], result["rowCount"])
        self.assertEqual(proof["monthlyContext"]["publication"]["missingMonths"], [])
        self.assertEqual([item["month"] for item in proof["monthlyContext"]["coverage"]],
            ["2026-08", "2026-09"])
        self.assertEqual(proof["monthlyContext"]["coverage"][0]["metrics"]["net_sales"],
            {"status": "present", "value": 0, "unit": "CNY_cent"})
        self.assertEqual(proof["monthlyContext"]["coverage"][1]["metrics"]["net_sales"]["status"],
            "missing_value")
        self.assertTrue(proof["requestOffsetAuditVerified"])
        for key in ("upstreamSignatureVerified", "sealed",
                    "persistentEvidenceVerified", "reportGenerationSupported",
                    "dailyProrationAllowed", "inferSkuProfit", "sumWithErpB2bAdsAllowed"):
            self.assertFalse(proof[key])
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).status,
            "collecting")

    def test_whole_checkpoint_utf8_cap_rejects_before_any_chunk_write(self):
        self.assertEqual(collector.MAX_CHECKPOINT_BYTES, 32_768)
        page = owning.read_page(self.principal, self.query)
        state = verifier.consume(None, page, trusted_query=self.query)
        raw = canonical(page)
        wrapped = {"schemaVersion": collector.CHECKPOINT_SCHEMA,
            "sourceRef": state["sourceRef"],
            "sourceRevision": state["sourceRevision"],
            "lastChunkDigest": digest(raw), "pageCount": state["pageCount"],
            "rowCount": state["rowsRead"], "storedBytes": state["storedBytes"],
            "finished": state["finished"], "financeState": state}
        exact_size = len(canonical(wrapped).encode("utf-8"))
        with patch.object(collector, "MAX_CHECKPOINT_BYTES", exact_size - 1), \
                self.assertRaises(AiError):
            self.advance(1, "checkpoint-one-byte-short")
        self.assertFalse(m.AiBusinessV4Chunk.objects.filter(run=self.parent).exists())
        self.assertFalse(m.AiBusinessV4ToolReceipt.objects.filter(run=self.parent).exists())
        source = m.AiBusinessV4Source.objects.get(pk=self.sources["finance-context"].pk)
        self.assertEqual((source.page_count, source.checkpoint_json), (0, "{}"))

    def test_scope_month_revision_actor_and_wrong_tool_audit_never_append(self):
        self.extend_rows(90)
        self.page_query_override = {**self.query, "scope": {**self.query["scope"],
            "scope_name": "其他范围"}}
        with self.assertRaises(AiError): self.advance(1, "wrong-scope")
        self.page_query_override = {**self.query, "months": ["2026-08", "2026-09", "2026-10"]}
        with self.assertRaises(AiError): self.advance(1, "wrong-month")
        self.page_query_override = None
        self.audit_override = "0" * 64
        with self.assertRaises(AiError): self.advance(1, "wrong-audit")
        self.audit_override = None
        self.assertFalse(m.AiBusinessV4Chunk.objects.filter(run=self.parent).exists())
        first = self.advance(1, "finance-first")
        with self.assertRaises(AiError): self.advance(1, "stale-parent-cas")
        with transaction.atomic():
            AppUser.objects.filter(email=self.principal.email).update(status="inactive")
            with self.assertRaises(AiError): self.advance(first["runVersion"], "revoked")
            transaction.set_rollback(True)
        revision = FinanceDataRevision.objects.get(domain="finance")
        FinanceDataRevision.objects.filter(domain="finance").update(
            revision=revision.revision + 1,
            source_digest=digest([revision.source_digest, "synthetic-revision-switch"]))
        with self.assertRaises(FinanceApiError):
            self.advance(first["runVersion"], "revision-switched")
        self.assertEqual(m.AiBusinessV4Chunk.objects.filter(run=self.parent).count(), 1)

    def test_missing_month_null_zero_and_missing_receipt_not_reported_as_zero(self):
        self.query = {**self.query, "months": ["2026-08", "2026-09", "2026-10"]}
        self.plan()
        self.complete()
        proof = replay.inspect(self.parent.id, "finance-context", self.principal)
        october = proof["monthlyContext"]["coverage"][2]
        self.assertEqual(october["month"], "2026-10")
        self.assertFalse(october["published"])
        self.assertEqual(october["metrics"]["net_sales"]["status"], "missing_month")
        original = m.AiBusinessV4ToolReceipt.objects.filter
        def omit(*args, **kwargs):
            return original(*args, **kwargs).exclude(sequence=1)
        with patch.object(m.AiBusinessV4ToolReceipt.objects, "filter",
                side_effect=omit), self.assertRaises(AiError):
            replay.inspect(self.parent.id, "finance-context", self.principal)

    def test_late_revocation_and_batch_change_reject_replay_or_continuation(self):
        self.extend_rows(90)
        first = self.advance(1, "finance-first")
        FinanceMonth.objects.filter(month="2026-09").update(status="processing")
        with self.assertRaises(FinanceApiError):
            self.advance(first["runVersion"], "batch-switched")
        self.assertEqual(m.AiBusinessV4Chunk.objects.filter(run=self.parent).count(), 1)
        FinanceMonth.objects.filter(month="2026-09").update(status="completed")
        result = self.advance(first["runVersion"], "finance-second")
        self.assertTrue(result["finished"])
        def revoke(event):
            if event == {"stage": "v4_finance_replay", "phase": "complete"}:
                AppUser.objects.filter(email=self.principal.email).update(status="inactive")
        with self.assertRaises(AiError):
            replay.inspect(self.parent.id, "finance-context",
                self.principal, checkpoint=revoke)
