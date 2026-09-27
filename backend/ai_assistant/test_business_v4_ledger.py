"""Synthetic isolated PostgreSQL v4 physical fence checks; no collector calls."""
from copy import deepcopy
import json

from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis import evidence_v4
from business_analysis.contracts import AnalysisContractError, comparison_periods
from sales.auth import Principal

from . import business_daily_collection_v3 as daily_reader, models as m
from .policy import canonical, digest, uid


def plan_fixture(client):
    dates = {"startDate": "2026-08-20", "endDate": "2026-09-18"}
    sources = [{"key": "sales", "domain": "sales", "query": {"platform": "京东",
        "shop": "测试店", "channel": "京东", **dates, "window": "current"}},
        {"key": "finance", "domain": "finance", "query": {"months": ["2026-08", "2026-09"],
            "scope": {"scope_key": "shop:测试店", "scope_type": "shop",
                "scope_name": "测试店", "group_name": "京东组"}, "analysisPeriod": dates}}]
    measurements = [{"sourceKey": "sales", "measuredRowCount": 1,
        "maxRowUtf8Bytes": 1000, "pageEnvelopeUtf8Bytes": 2048,
        "sourceRevisionHint": "1:2"},
        {"sourceKey": "finance", "measuredRowCount": 0,
         "maxRowUtf8Bytes": 0, "pageEnvelopeUtf8Bytes": 1000,
         "sourceRevisionHint": "0:" + "a" * 64}]
    return evidence_v4.build_plan(client_request_id=client, sources=sources,
        measurements=measurements, analysis_request={"schemaVersion": "business-analysis-request-v1",
            "question": "大证据物理账", "requestedDimensions": ["shop"],
            "requestedWindows": ["current"]})


class BusinessV4LedgerTests(TestCase):
    def setUp(self):
        if connection.vendor != "postgresql": self.skipTest("v4 physical guard needs PostgreSQL")
        self.principal = Principal("v4-ledger@example.test", "Synthetic", "admin", None)
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
        now = timezone.now()
        AppUser.objects.create(email=self.principal.email, display_name="Synthetic", role=role,
            status="active", scope=None, version=1, created_at=now, updated_at=now)
        self.parent, self.sources = self.make_plan("v4-plan-one")

    def make_plan(self, client):
        plan = plan_fixture(client)
        raw = canonical(plan)
        with transaction.atomic():
            parent = m.AiBusinessV4Run.objects.create(id=uid("v4-run"),
                owner_email=self.principal.email, client_request_id=client,
                plan_json=raw, plan_digest=digest(raw),
                run_identity_digest=plan["runIdentityDigest"])
            sources = {}
            for entry in plan["sourcePlans"]:
                source = m.AiBusinessV4Source.objects.create(id=uid("v4-source"), run=parent,
                    source_key=entry["sourceKey"], ordinal=entry["ordinal"],
                    domain=entry["domain"], temporal_role=entry["temporalRole"],
                    query_json=canonical(entry["query"]), query_digest=entry["queryDigest"],
                    source_identity_digest=entry["sourceIdentityDigest"],
                    source_revision_hint=entry["sourceRevisionHint"])
                sources[entry["sourceKey"]] = source
        return parent, sources

    def daily_page(self):
        query = self.sources["sales"].query_json
        import json
        q = json.loads(query)
        items = [{"rowId": "1", "platform": "京东", "shopName": "测试店",
            "channel": "京东", "date": "2026-08-20", "metrics": {"salesCents": 100}}]
        return {"schemaVersion": "business-analysis-v1", "sourceRef": "b" * 64,
            "sourceRevision": "1:2", "source": "erp_sales", "sourceDataset": None,
            "monetaryUnit": "CNY_CENT", "filters": {**q,
                "periods": comparison_periods(q["startDate"], q["endDate"]), "limit": 100},
            "items": items, "control": {"rowCount": 1, "typedTotals": {"salesCents": 100}},
            "pageEvidence": {"rowCount": 1, "sha256": digest(items)},
            "pagination": {"limit": 100, "hasMore": False, "nextCursor": None},
            "metricSemantics": None}

    def append_daily(self, *, receipt=True, page=None, checkpoint_extra=None):
        # Django TestCase nests savepoints in a class transaction; a prior
        # test's named IMMEDIATE mode can survive a rolled-back savepoint.
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ai_v4_chunk_complete, ai_v4_parent_complete DEFERRED")
        page = self.daily_page() if page is None else page
        raw = canonical(page); size = len(raw.encode("utf-8"))
        source = self.sources["sales"]
        audit = m.AiToolAuditLogs.objects.create(id=uid("audit"), request_id=uid("request"),
            invocation_id=uid("invocation"), actor_email=self.principal.email,
            actor_role="admin", surface="business_collection", tool_name="get_business_source_page",
            arguments_json="{}", status="succeeded", duration_ms=1, response_digest=digest(raw))
        chunk = m.AiBusinessV4Chunk.objects.create(id=uid("v4-chunk"), run=self.parent,
            source=source, sequence=1, payload_json=raw, payload_digest=digest(raw),
            source_ref=page["sourceRef"], source_revision=page["sourceRevision"], row_count=1)
        if receipt:
            m.AiBusinessV4ToolReceipt.objects.create(chunk=chunk, audit=audit, run=self.parent,
                source=source, sequence=1, actor_email=self.principal.email,
                request_id=audit.request_id, invocation_id=audit.invocation_id,
                tool_name=audit.tool_name, response_digest=chunk.payload_digest,
                payload_bytes=size)
        checkpoint = {"schemaVersion": "business-v4-checkpoint-v1",
            "sourceRef": page["sourceRef"], "sourceRevision": page["sourceRevision"],
            "lastChunkDigest": chunk.payload_digest, "pageCount": 1, "rowCount": 1,
            "storedBytes": size, "finished": True}
        if checkpoint_extra is not None:
            checkpoint["extra"] = checkpoint_extra
        m.AiBusinessV4Source.objects.filter(pk=source.pk).update(version=2, page_count=1,
            row_count=1, stored_bytes=size, finished=True, source_ref=page["sourceRef"],
            source_revision=page["sourceRevision"], checkpoint_json=canonical(checkpoint),
            updated_at=timezone.now())
        m.AiBusinessV4Run.objects.filter(pk=self.parent.pk).update(version=2,
            page_count=1, row_count=1, stored_bytes=size)
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ai_v4_chunk_complete, ai_v4_parent_complete IMMEDIATE")
        return chunk

    def test_checkpoint_utf8_capacity_rejected_before_json_replay(self):
        with self.assertRaisesMessage(DatabaseError, "ai_business_v4_checkpoint_capacity_invalid"):
            with transaction.atomic():
                self.append_daily(checkpoint_extra="中" * 11_000)
        self.assertFalse(m.AiBusinessV4Chunk.objects.filter(run=self.parent).exists())

    def test_exact_append_receipt_and_old_tables_are_separate(self):
        with transaction.atomic():
            chunk = self.append_daily()
        self.assertEqual(m.AiBusinessV4Chunk.objects.filter(run=self.parent).count(), 1)
        self.assertEqual(m.AiBusinessV4ToolReceipt.objects.filter(run=self.parent).count(), 1)
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).status, "collecting")
        self.assertFalse(m.AiBusinessEvidenceChunk.objects.exists())
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessV4Chunk.objects.filter(pk=chunk.pk).update(payload_digest="0" * 64)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessV4ToolReceipt.objects.filter(chunk=chunk).delete()

    def test_missing_receipt_out_of_order_foreign_chunk_and_caps_rejected(self):
        with self.assertRaises(DatabaseError), transaction.atomic():
            self.append_daily(receipt=False)
        self.assertFalse(m.AiBusinessV4Chunk.objects.filter(run=self.parent).exists())
        raw = canonical(self.daily_page())
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessV4Chunk.objects.create(id=uid("chunk"), run=self.parent,
                source=self.sources["sales"], sequence=2, payload_json=raw,
                payload_digest=digest(raw), source_ref="b" * 64,
                source_revision="1:2", row_count=1)
        second, foreign = self.make_plan("v4-plan-two")
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessV4Chunk.objects.create(id=uid("chunk"), run=self.parent,
                source=foreign["sales"], sequence=1, payload_json=raw,
                payload_digest=digest(raw), source_ref="b" * 64,
                source_revision="1:2", row_count=1)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessV4Run.objects.filter(pk=self.parent.pk).update(
                version=2, page_count=65537, stored_bytes=8 * 1024**3 + 1)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessV4Run.objects.filter(pk=self.parent.pk).update(
                version=2, page_count=1, stored_bytes=1, row_count=0)
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ai_v4_parent_complete IMMEDIATE")
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessV4Source.objects.filter(pk=self.sources["sales"].pk).update(
                version=2, page_count=16385, stored_bytes=2 * 1024**3 + 1)
        self.assertEqual(second.status, "collecting")

    def test_initial_finance_and_daily_required_fields_reject_null_or_empty(self):
        def denied(change):
            client = uid("v4-forged")
            planned = plan_fixture(client)
            entry = next(item for item in planned["sourcePlans"] if item["sourceKey"] == change[0])
            query = deepcopy(entry["query"])
            change[1](query)
            entry["query"] = query
            entry["queryDigest"] = digest(query)
            entry["sourceIdentityDigest"] = digest({"schemaVersion": evidence_v4.SOURCE_IDENTITY_SCHEMA,
                "key": entry["sourceKey"], "domain": entry["domain"],
                "queryDigest": entry["queryDigest"], "temporalRole": entry["temporalRole"]})
            with self.assertRaises(DatabaseError), transaction.atomic():
                raw = canonical(planned)
                run = m.AiBusinessV4Run.objects.create(id=uid("v4-run"),
                    owner_email=self.principal.email, client_request_id=client,
                    plan_json=raw, plan_digest=digest(raw),
                    run_identity_digest=planned["runIdentityDigest"])
                for item in planned["sourcePlans"]:
                    m.AiBusinessV4Source.objects.create(id=uid("v4-source"), run=run,
                        source_key=item["sourceKey"], ordinal=item["ordinal"],
                        domain=item["domain"], temporal_role=item["temporalRole"],
                        query_json=canonical(item["query"]), query_digest=item["queryDigest"],
                        source_identity_digest=item["sourceIdentityDigest"],
                        source_revision_hint=item["sourceRevisionHint"])
        for change in (("finance", lambda q: q["scope"].pop("scope_type")),
                       ("finance", lambda q: q["scope"].update(scope_key=None)),
                       ("finance", lambda q: q["scope"].update(scope_name="")),
                       ("finance", lambda q: q["scope"].update(group_name=None)),
                       ("sales", lambda q: q.update(shop=None)),
                       ("sales", lambda q: q.update(channel=""))):
            with self.subTest(change=change): denied(change)
        def market_missing_scope(q):
            q.clear(); q.update({"platform": "京东", "category": "测试类", "scope": "",
                "rankingDimension": "SKU", "priceBandFilter": "all",
                "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"})
        with self.assertRaises(DatabaseError), transaction.atomic():
            client = uid("v4-market")
            planned = plan_fixture(client)
            entry = next(item for item in planned["sourcePlans"] if item["sourceKey"] == "sales")
            entry["domain"] = "market"
            market_missing_scope(entry["query"])
            entry["queryDigest"] = digest(entry["query"])
            entry["sourceIdentityDigest"] = digest({"schemaVersion": evidence_v4.SOURCE_IDENTITY_SCHEMA,
                "key": entry["sourceKey"], "domain": "market", "queryDigest": entry["queryDigest"],
                "temporalRole": "daily_fact"})
            raw = canonical(planned)
            run = m.AiBusinessV4Run.objects.create(id=uid("v4-run"),
                owner_email=self.principal.email, client_request_id=client,
                plan_json=raw, plan_digest=digest(raw),
                run_identity_digest=planned["runIdentityDigest"])
            for item in planned["sourcePlans"]:
                m.AiBusinessV4Source.objects.create(id=uid("v4-source"), run=run,
                    source_key=item["sourceKey"], ordinal=item["ordinal"],
                    domain=item["domain"], temporal_role=item["temporalRole"],
                    query_json=canonical(item["query"]), query_digest=item["queryDigest"],
                    source_identity_digest=item["sourceIdentityDigest"],
                    source_revision_hint=item["sourceRevisionHint"])
        self.assertEqual(m.AiBusinessV4Run.objects.count(), 1)

    def test_physical_previous_window_date_is_not_owning_authority(self):
        bad = self.daily_page()
        bad["filters"]["periods"]["previous"]["startDate"] = "2026-01-01"
        with transaction.atomic():
            self.append_daily(page=bad)
        query = json.loads(self.sources["sales"].query_json)
        metadata = {"sourceRevision": bad["sourceRevision"], "coverage": None,
            "excludedOverlappingPeriodRows": None, "identityCheck": None,
            "availableDates": None, "metricSemantics": None, "freshness": None}
        with self.assertRaises(AnalysisContractError):
            daily_reader._Identity()._identity({"domain": "sales", "query": query},
                bad, metadata, True)
        self.assertFalse(json.loads(self.parent.plan_json)["sourceAuthorityVerified"])
