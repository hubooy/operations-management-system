"""Isolated PostgreSQL one-page signed JD promotion v4 collection checks."""
import time
import json
from unittest.mock import patch
from urllib.parse import urlencode

from django.core import signing
from django.db import DatabaseError, connection, transaction
from django.http import QueryDict
from django.test import TestCase
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis import evidence_v4
from netshop import analysis as owning
from netshop import analysis_continuation as continuation
from netshop.errors import NetshopApiError
from netshop.models import NetshopDataRevision, NetshopRow
from sales.auth import Principal
from business_analysis.contracts import PageReconciler

from . import business_v4_netshop_promotion as collector, chat, models as m, transport
from .policy import AiError, canonical, digest, uid


def tool(name):
    return {"name": name, "risk": "read_only", "allowedRoles": ["admin"],
        "scopePolicy": "unscoped_only", "execution": {"mode": "direct",
            "allowedSurfaces": ["business_collection"]}}


class BusinessV4NetshopPromotionTests(TestCase):
    def setUp(self):
        if connection.vendor != "postgresql": self.skipTest("v4 collector requires PostgreSQL")
        self.principal = Principal("v4-jd-promotion@example.test", "Synthetic", "admin", None)
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
        now = timezone.now()
        AppUser.objects.create(email=self.principal.email, display_name="Synthetic", role=role,
            status="active", scope=None, version=1, created_at=now, updated_at=now)
        NetshopDataRevision.objects.update_or_create(domain="netshop",
            defaults={"revision": 6, "source_digest": "b" * 64})
        with transaction.atomic():
            NetshopRow.objects.bulk_create([NetshopRow(source_row_key=f"v4-promo-{index}",
                source_row_hash=f"{index+1:064x}", first_import_batch_id="synthetic",
                last_import_batch_id="synthetic", source_row_number=index+1,
                source="jd_promotion", dataset="ad", platform="京东", shop_name="测试店",
                business_date="2026-08-20", sku_id=str(index+1), spu_id="P1",
                product_code="M1", spend_cents=100, net_transaction_amount_cents=1000,
                impressions=20, clicks=2, net_orders=1,
                metrics_json={"spendCents": 100, "netTransactionAmountCents": 1000,
                              "impressions": 20, "clicks": 2, "netOrders": 1},
                raw_json={"推广计划": "合成计划", "搜索词": "开水器"},
                created_at="2026-09-24", updated_at="2026-09-24")
                for index in range(101)])
            NetshopDataRevision.objects.filter(domain="netshop").update(
                revision=7, source_digest="a" * 64)
        self.query = {"platform": "京东", "shop": "测试店", "dataset": "promotion",
            "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"}
        finance = {"months": ["2026-08", "2026-09"],
            "scope": {"scope_key": "shop:测试店", "scope_type": "shop",
                "scope_name": "测试店", "group_name": "京东组"},
            "analysisPeriod": {"startDate": self.query["startDate"], "endDate": self.query["endDate"]}}
        plan = evidence_v4.build_plan(client_request_id=uid("v4-client"),
            sources=[{"key": "promotion-current", "domain": "netshop", "query": self.query},
                {"key": "finance-context", "domain": "finance", "query": finance}],
            measurements=[{"sourceKey": "promotion-current", "measuredRowCount": 101,
                "maxRowUtf8Bytes": 2000, "pageEnvelopeUtf8Bytes": 2048,
                "sourceRevisionHint": "7:" + "a" * 12},
                {"sourceKey": "finance-context", "measuredRowCount": 0,
                 "maxRowUtf8Bytes": 0, "pageEnvelopeUtf8Bytes": 1000,
                 "sourceRevisionHint": "0:" + "a" * 64}],
            analysis_request={"schemaVersion": "business-analysis-request-v1",
                "question": "京东推广完整两页", "requestedDimensions": ["shop", "keyword"],
                "requestedWindows": ["current"]})
        with transaction.atomic():
            raw = canonical(plan)
            self.parent = m.AiBusinessV4Run.objects.create(id=uid("v4-run"),
                owner_email=self.principal.email, client_request_id=plan["clientRequestId"],
                plan_json=raw, plan_digest=digest(raw),
                run_identity_digest=plan["runIdentityDigest"])
            self.sources = {}
            for entry in plan["sourcePlans"]:
                self.sources[entry["sourceKey"]] = m.AiBusinessV4Source.objects.create(
                    id=uid("v4-source"), run=self.parent, source_key=entry["sourceKey"],
                    ordinal=entry["ordinal"], domain=entry["domain"],
                    temporal_role=entry["temporalRole"], query_json=canonical(entry["query"]),
                    query_digest=entry["queryDigest"],
                    source_identity_digest=entry["sourceIdentityDigest"],
                    source_revision_hint=entry["sourceRevisionHint"])
        self.calls = []
        self.audit_arguments_digest_override = None
        self.page_window_override = None
        self.page_shop_override = None

    def owner(self, name, arguments, principal, **kwargs):
        self.calls.append((name, arguments))
        if name == collector.FIRST_TOOL:
            requested = {key: value for key, value in arguments.items()
                if key != "domain"}
            if self.page_window_override:
                requested["window"] = self.page_window_override
            if self.page_shop_override:
                requested["shop"] = self.page_shop_override
            params = QueryDict(urlencode(requested))
            spec, limit, cursor = owning.validate_request(params)
            with patch.object(signing.TimestampSigner, "timestamp",
                    return_value=signing.b62_encode(int(time.time()) - 7200)):
                page = owning.read_page(spec, limit, cursor)
        else:
            page = continuation.read_page(principal, QueryDict(urlencode(arguments)))
        m.AiToolAuditLogs.objects.create(id=uid("audit"), request_id=kwargs["request_id"],
            invocation_id=uid("invocation"), actor_email=self.principal.email,
            actor_role="admin", surface="business_collection", tool_name=name,
            arguments_json=canonical({"argumentsDigest":
                self.audit_arguments_digest_override or digest(arguments)}),
            status="succeeded", duration_ms=1,
            response_digest=digest(canonical(page)))
        return {"ok": True, "toolName": name, "data": page}

    def advance(self, version, request_id, source_key="promotion-current"):
        with patch.object(transport, "catalog", return_value=[tool(collector.FIRST_TOOL),
                    tool(collector.CONTINUATION_TOOL)]), \
                patch.object(transport, "execute_tool", side_effect=self.owner):
            return collector.advance(self.parent.id, source_key, version,
                self.principal, request_id)

    def three_window_run(self):
        for window, day in (("previous", "2026-07-21"),
                            ("yearAgo", "2025-08-20")):
            NetshopRow.objects.bulk_create([NetshopRow(
                source_row_key=f"v4-{window}-{index}",
                source_row_hash=f"{1000 + (1 if window == 'previous' else 1000) + index:064x}",
                first_import_batch_id="synthetic", last_import_batch_id="synthetic",
                source_row_number=index + 1, source="jd_promotion", dataset="ad",
                platform="京东", shop_name="测试店", business_date=day,
                sku_id=str(index + 1), spu_id="P1", product_code="M1",
                spend_cents=100, net_transaction_amount_cents=1000,
                impressions=20, clicks=2, net_orders=1,
                metrics_json={"spendCents": 100, "netTransactionAmountCents": 1000,
                    "impressions": 20, "clicks": 2, "netOrders": 1},
                raw_json={"推广计划": "合成计划", "搜索词": "开水器"},
                created_at="2026-09-24", updated_at="2026-09-24")
                for index in range(101)])
        original = json.loads(self.parent.plan_json)
        finance = next(item["query"] for item in original["sourcePlans"]
            if item["domain"] == "finance")
        windows = ("current", "previous", "yearAgo")
        plan = evidence_v4.build_plan(client_request_id=uid("v4-client"),
            sources=[{"key": f"promotion-{window}", "domain": "netshop",
                "query": {**self.query, "window": window}} for window in windows]
                + [{"key": "finance-context", "domain": "finance", "query": finance}],
            measurements=[{"sourceKey": f"promotion-{window}",
                "measuredRowCount": 101, "maxRowUtf8Bytes": 2000,
                "pageEnvelopeUtf8Bytes": 2048,
                "sourceRevisionHint": "7:" + "a" * 12} for window in windows]
                + [{"sourceKey": "finance-context", "measuredRowCount": 0,
                    "maxRowUtf8Bytes": 0, "pageEnvelopeUtf8Bytes": 1000,
                    "sourceRevisionHint": "0:" + "a" * 64}],
            analysis_request={"schemaVersion": "business-analysis-request-v1",
                "question": "京东推广同一任务的环比同比", "requestedDimensions": ["shop", "keyword"],
                "requestedWindows": list(windows)})
        with transaction.atomic():
            raw = canonical(plan)
            self.parent = m.AiBusinessV4Run.objects.create(id=uid("v4-run"),
                owner_email=self.principal.email, client_request_id=plan["clientRequestId"],
                plan_json=raw, plan_digest=digest(raw),
                run_identity_digest=plan["runIdentityDigest"])
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

    def test_signed_first_then_genuine_expired_continuation_and_restart(self):
        one = self.advance(1, "v4-first")
        self.assertEqual(one["pageCount"], 1)
        self.assertFalse(one["finished"])
        self.assertTrue(0 < one["rowCount"] < 100)  # owner byte budget ended this page early
        self.assertEqual(m.AiBusinessV4Chunk.objects.filter(run=self.parent).count(), 1)
        self.assertEqual(m.AiBusinessV4ToolReceipt.objects.filter(run=self.parent).count(), 1)
        # The second call reconstructs only the persisted checkpoint/last
        # immutable chunk. The real owning continuation renews a genuinely
        # expired, signed one-hour cursor under unchanged revision.
        two = self.advance(2, "v4-second")
        self.assertTrue(two["finished"])
        self.assertEqual((two["pageCount"], two["rowCount"]), (2, 101))
        self.assertEqual([name for name, _ in self.calls],
            [collector.FIRST_TOOL, collector.CONTINUATION_TOOL])
        self.assertEqual(self.calls[1][1]["expectedSourceRef"], one["sourceRef"])
        self.assertEqual(self.calls[1][1]["expectedRevision"], one["sourceRevision"])
        first_chunk = m.AiBusinessV4Chunk.objects.get(run=self.parent, source=self.sources["promotion-current"], sequence=1)
        self.assertEqual(self.calls[1][1]["expectedLastId"],
            int(json.loads(first_chunk.payload_json)["items"][-1]["rowId"]))
        second_chunk = m.AiBusinessV4Chunk.objects.get(run=self.parent, source=self.sources["promotion-current"], sequence=2)
        first_ids = [int(item["rowId"]) for item in json.loads(first_chunk.payload_json)["items"]]
        second_ids = [int(item["rowId"]) for item in json.loads(second_chunk.payload_json)["items"]]
        self.assertLess(first_ids[-1], second_ids[0])
        self.assertEqual(len(set(first_ids + second_ids)), 101)
        self.assertEqual(m.AiBusinessV4Chunk.objects.filter(run=self.parent).count(), 2)
        self.assertEqual(m.AiBusinessV4ToolReceipt.objects.filter(run=self.parent).count(), 2)
        parent = m.AiBusinessV4Run.objects.get(pk=self.parent.pk)
        source = m.AiBusinessV4Source.objects.get(pk=self.sources["promotion-current"].pk)
        self.assertEqual((parent.page_count, parent.row_count, parent.stored_bytes),
                         (source.page_count, source.row_count, source.stored_bytes))
        self.assertEqual(parent.status, "collecting")
        self.assertFalse(two["sourceAuthorityVerified"])

    def test_three_fixed_windows_collect_independent_signed_page_chains(self):
        self.three_window_run()
        seen_refs = set()
        version = 1
        for window, date in (("previous", "2026-07-21"),
                             ("current", "2026-08-20"),
                             ("yearAgo", "2025-08-20")):
            key = f"promotion-{window}"
            first = self.advance(version, f"v4-{window}-first", key)
            self.assertFalse(first["finished"])
            self.assertTrue(0 < first["rowCount"] < 100)
            version = first["runVersion"]
            second = self.advance(version, f"v4-{window}-second", key)
            self.assertEqual((second["pageCount"], second["rowCount"]), (2, 101))
            self.assertTrue(second["finished"])
            self.assertNotIn(second["sourceRef"], seen_refs)
            seen_refs.add(second["sourceRef"])
            version = second["runVersion"]
            chunks = list(m.AiBusinessV4Chunk.objects.filter(run=self.parent,
                source=self.sources[key]).order_by("sequence"))
            self.assertEqual(len(chunks), 2)
            self.assertTrue(all(item["date"] == date for chunk in chunks
                for item in json.loads(chunk.payload_json)["items"]))
            self.assertEqual(self.calls[-1][1]["window"], window)
            self.assertEqual(self.calls[-1][1]["expectedSourceRef"], second["sourceRef"])
        parent = m.AiBusinessV4Run.objects.get(pk=self.parent.pk)
        self.assertEqual((parent.page_count, parent.row_count), (6, 303))
        self.assertEqual(len(seen_refs), 3)
        self.assertEqual(parent.status, "collecting")

    def test_baseline_wrong_window_cross_shop_duplicate_and_stale_cas_rejected(self):
        self.three_window_run()
        self.page_window_override = "current"
        with self.assertRaises(AiError):
            self.advance(1, "wrong-previous-period", "promotion-previous")
        self.page_window_override = None
        self.page_shop_override = "其他店"
        with self.assertRaises(AiError):
            self.advance(1, "wrong-year-shop", "promotion-yearAgo")
        self.page_shop_override = None
        self.assertFalse(m.AiBusinessV4Chunk.objects.filter(run=self.parent).exists())
        first = self.advance(1, "valid-previous-first", "promotion-previous")
        with self.assertRaises(AiError):
            self.advance(1, "stale-other-window", "promotion-yearAgo")
        plan = json.loads(self.parent.plan_json)
        same = next(item for item in plan["sourcePlans"]
            if item["sourceKey"] == "promotion-previous")
        plan["sourcePlans"].append({**same, "sourceKey": "duplicate-previous"})
        with self.assertRaises(AiError):
            collector.selection(plan, {**self.query, "window": "previous"},
                "promotion-previous")
        self.assertEqual(first["runVersion"], 2)
        self.assertEqual(m.AiBusinessV4Chunk.objects.filter(run=self.parent).count(), 1)

    def test_duplicate_version_wrong_domain_actor_revision_and_capacity_never_append(self):
        one = self.advance(1, "v4-first")
        before = m.AiBusinessV4Chunk.objects.filter(run=self.parent).count()
        with self.assertRaises(AiError): self.advance(1, "v4-stale")
        with self.assertRaises(AiError):
            collector.advance(self.parent.id, "finance-context", 2,
                self.principal, "v4-foreign")
        with patch.object(evidence_v4, "MAX_SOURCE_PAGES", 1):
            with self.assertRaises(AiError): self.advance(2, "v4-cap")
        with transaction.atomic():
            AppUser.objects.filter(email=self.principal.email).update(status="inactive")
            with self.assertRaises(AiError): self.advance(2, "v4-revoked")
            transaction.set_rollback(True)
        NetshopDataRevision.objects.filter(domain="netshop").update(revision=8)
        with self.assertRaises(NetshopApiError): self.advance(2, "v4-revision")
        self.assertEqual(m.AiBusinessV4Chunk.objects.filter(run=self.parent).count(), before)
        self.assertEqual(m.AiBusinessV4Source.objects.get(pk=self.sources["promotion-current"].pk).source_ref,
                         one["sourceRef"])

    def test_long_cursor_audit_is_digest_only_and_wrong_digest_never_appends(self):
        raw_arguments = {"cursor": "signed-" + "x" * 1600,
                         "expectedLastId": 92, "expectedSourceRef": "a" * 64}
        chat.audit(self.principal, "digest-only", collector.CONTINUATION_TOOL,
            "succeeded", arguments=raw_arguments, result={"returned": 1},
            surface="business_collection")
        audited = m.AiToolAuditLogs.objects.get(request_id="digest-only")
        self.assertEqual(audited.arguments_json,
            canonical({"argumentsDigest": digest(raw_arguments)}))
        self.assertNotIn(raw_arguments["cursor"], audited.arguments_json)
        chat.audit(self.principal, "legacy-summary", collector.CONTINUATION_TOOL,
            "succeeded", arguments=raw_arguments, result={"returned": 1},
            surface="ai_agent")
        legacy = m.AiToolAuditLogs.objects.get(request_id="legacy-summary")
        self.assertIn(raw_arguments["cursor"][:240], legacy.arguments_json)
        self.assertNotIn(raw_arguments["cursor"][:241], legacy.arguments_json)
        self.assertNotIn(raw_arguments["cursor"], legacy.arguments_json)
        self.audit_arguments_digest_override = "0" * 64
        with self.assertRaises(AiError): self.advance(1, "wrong-args-digest")
        self.assertFalse(m.AiBusinessV4Chunk.objects.filter(run=self.parent).exists())

    def test_synthetic_1000_page_resume_uses_one_indexed_last_chunk_lookup(self):
        source = self.sources["promotion-current"]
        source.page_count, source.version, source.row_count = 1000, 1001, 1000
        source.stored_bytes = 100_000
        source.source_ref, source.source_revision = "b" * 64, "7:" + "a" * 12
        rows = [{"rowId": "1000", "platform": "京东", "shopName": "测试店",
                 "metrics": {"spendCents": 100}}]
        page = {"schemaVersion": "business-analysis-v1", "sourceRef": source.source_ref,
            "sourceRevision": source.source_revision, "items": rows,
            "pagination": {"limit": 100, "hasMore": True, "nextCursor": "signed-cursor"},
            "pageEvidence": {"rowCount": 1, "sha256": digest(rows)}}
        raw = canonical(page)
        verifier = PageReconciler().__dict__
        verifier.update(source_ref=source.source_ref, expected_cursor="signed-cursor",
            last_id=1000, rows=1000, finished=False)
        checkpoint = {"schemaVersion": collector.CHECKPOINT_SCHEMA,
            "sourceRef": source.source_ref, "sourceRevision": source.source_revision,
            "lastChunkDigest": digest(raw), "pageCount": 1000, "rowCount": 1000,
            "storedBytes": 100_000, "finished": False, "verifier": verifier,
            "metadata": {"sourceRevision": source.source_revision}}
        source.checkpoint_json = canonical(checkpoint)
        last = {"payload_json": raw, "payload_digest": digest(raw),
            "source_ref": source.source_ref, "source_revision": source.source_revision,
            "row_count": 1}
        with patch.object(m.AiBusinessV4Chunk.objects, "filter") as chunks:
            chunks.return_value.values.return_value.first.return_value = last
            saved, state, _, actual_digest = collector._checkpoint(source)
            chunks.assert_called_once_with(run_id=source.run_id, source_id=source.id,
                sequence=1000)
        self.assertEqual(saved["pageCount"], 1000)
        self.assertEqual(state.last_id, 1000)
        self.assertEqual(actual_digest, digest(raw))

    def test_two_page_final_parent_counter_mismatch_still_fails_deferred_guard(self):
        original_save = m.AiBusinessV4Run.save
        def corrupt_second_parent(instance, *args, **kwargs):
            if instance.page_count == 2:
                instance.row_count += 1  # physically plausible delta, wrong final sum
            return original_save(instance, *args, **kwargs)
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ai_v4_parent_complete DEFERRED")
            self.advance(1, "v4-mismatch-first")
            with patch.object(m.AiBusinessV4Run, "save", corrupt_second_parent):
                self.advance(2, "v4-mismatch-second")
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ai_v4_parent_complete IMMEDIATE")
        self.assertFalse(m.AiBusinessV4Chunk.objects.filter(run=self.parent).exists())
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).page_count, 0)
