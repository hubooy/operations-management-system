"""PostgreSQL candidate segments over one mixed v4 promotion/finance run."""
import json
from unittest.mock import patch

from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from access_control.models import AppUser
from business_analysis import evidence_v4
from business_analysis.contracts import AnalysisContractError
from finance import business_evidence_page as finance_owner
from finance.import_service import import_finance_payload
from finance.models import FinanceDataRevision, FinanceLine, FinanceWriteAuthority
from finance.tests.factories import prepared_payload
from netshop.models import NetshopRow

from . import (business_v4_finance_collection as finance_collector,
    business_v4_netshop_promotion as promotion_collector,
    business_v4_validation as validation, models as m, transport)
from .test_business_v4_netshop_promotion import (BusinessV4NetshopPromotionTests,
    tool as promotion_tool)
from .policy import AiError, canonical, digest, uid


def finance_tool():
    return {"name": finance_collector.TOOL, "risk": "read_only",
        "allowedRoles": ["admin"], "scopePolicy": "unscoped_only",
        "execution": {"mode": "direct", "allowedSurfaces": ["business_collection"]}}


class BusinessV4ValidationTests(TestCase):
    promotion_owner = BusinessV4NetshopPromotionTests.owner

    def setUp(self):
        BusinessV4NetshopPromotionTests.setUp(self)
        FinanceWriteAuthority.objects.filter(id=1).update(status="postgres")
        import_finance_payload(prepared_payload("2026-08"), self.principal.email)
        import_finance_payload(prepared_payload("2026-09"), self.principal.email)
        self.finance_query = {"months": ["2026-08", "2026-09"],
            "scope": {"scope_key": "business", "scope_type": "business",
                "scope_name": "志高事业部", "group_name": ""},
            "analysisPeriod": {"startDate": self.query["startDate"],
                "endDate": self.query["endDate"]}}
        revision = FinanceDataRevision.objects.get(domain="finance")
        self.rebuild_plan(f"{revision.revision}:{revision.source_digest}")

    def rebuild_plan(self, finance_revision):
        plan = evidence_v4.build_plan(client_request_id=uid("v4-client"),
            sources=[{"key": "promotion-current", "domain": "netshop",
                "query": self.query},
                {"key": "finance-context", "domain": "finance",
                    "query": self.finance_query}],
            measurements=[{"sourceKey": "promotion-current", "measuredRowCount": 101,
                "maxRowUtf8Bytes": 2000, "pageEnvelopeUtf8Bytes": 2048,
                "sourceRevisionHint": "7:" + "a" * 12},
                {"sourceKey": "finance-context", "measuredRowCount": 120,
                    "maxRowUtf8Bytes": 1200, "pageEnvelopeUtf8Bytes": 2048,
                    "sourceRevisionHint": finance_revision}],
            analysis_request={"schemaVersion": "business-analysis-request-v1",
                "question": "京东推广与财报自然月同一证据任务",
                "requestedDimensions": ["shop", "keyword"],
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

    def finance_owner(self, name, arguments, principal, **kwargs):
        page = finance_owner.read_page(principal, arguments["query"],
            offset=arguments["offset"], after_id=arguments["afterId"],
            expected_source_ref=arguments.get("expectedSourceRef"),
            expected_revision=arguments.get("expectedRevision"))
        m.AiToolAuditLogs.objects.create(id=uid("audit"),
            request_id=kwargs["request_id"], invocation_id=uid("invocation"),
            actor_email=self.principal.email, actor_role="admin",
            surface="business_collection", tool_name=name,
            arguments_json=canonical({"argumentsDigest": digest(arguments)}),
            status="succeeded", duration_ms=1,
            response_digest=digest(canonical(page)))
        return {"ok": True, "toolName": name, "data": page}

    def owner(self, name, arguments, principal, **kwargs):
        if name == finance_collector.TOOL:
            return self.finance_owner(name, arguments, principal, **kwargs)
        return self.promotion_owner(name, arguments, principal, **kwargs)

    def collect(self, source_key, version, number):
        tools = [promotion_tool(promotion_collector.FIRST_TOOL),
            promotion_tool(promotion_collector.CONTINUATION_TOOL), finance_tool()]
        with patch.object(transport, "catalog", return_value=tools), \
                patch.object(transport, "execute_tool", side_effect=self.owner):
            service = (finance_collector if source_key == "finance-context"
                else promotion_collector)
            return service.advance(self.parent.id, source_key, version,
                self.principal, f"mixed-{source_key}-{number}")

    def complete_mixed(self):
        # Promotion first page, finance pages, then promotion continuation:
        # each parent version and the shared counters must reflect both domains.
        version = 1
        promotion = self.collect("promotion-current", version, 1)
        version = promotion["runVersion"]
        number = 1
        while True:
            finance = self.collect("finance-context", version, number)
            version = finance["runVersion"]
            if finance["finished"]: break
            number += 1
        number = 2
        while not promotion["finished"]:
            promotion = self.collect("promotion-current", version, number)
            version = promotion["runVersion"]
            number += 1
        return version

    def test_mixed_parent_candidate_segments_preserve_counters_without_seal(self):
        self.assertRaises(AiError, validation.start_attempt, self.parent.id, 1,
            self.principal)  # both sources incomplete
        version = self.complete_mixed()
        parent = m.AiBusinessV4Run.objects.get(pk=self.parent.pk)
        sources = list(m.AiBusinessV4Source.objects.filter(run=parent))
        self.assertEqual((parent.page_count, parent.row_count, parent.stored_bytes),
            (sum(item.page_count for item in sources),
             sum(item.row_count for item in sources),
             sum(item.stored_bytes for item in sources)))
        attempt = validation.start_attempt(parent.id, version, self.principal)
        self.assertEqual(validation.start_attempt(parent.id, version,
            self.principal)["attemptId"], attempt["attemptId"])
        finance = validation.advance_segment(attempt["attemptId"],
            "finance-context", 1, self.principal)
        promotion = validation.advance_segment(attempt["attemptId"],
            "promotion-current", 1, self.principal)
        self.assertTrue(finance["sourceCompleteCandidate"])
        self.assertTrue(promotion["sourceCompleteCandidate"])
        self.assertFalse(finance["financeRevisionWriteFenceVerified"])
        self.assertFalse(promotion["sourceAuthorityVerified"])
        self.assertEqual(m.AiBusinessV4ValidationSegment.objects.filter(
            attempt_id=attempt["attemptId"]).count(), 2)
        parent.refresh_from_db()
        self.assertEqual((parent.status, parent.collection_status),
            ("collecting", "manual"))
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessV4Run.objects.filter(pk=parent.pk).update(status="sealed",
                version=parent.version + 1)

    def test_wrong_hmac_revocation_and_stale_index_cannot_resume_or_seal(self):
        version = self.complete_mixed()
        attempt = validation.start_attempt(self.parent.id, version, self.principal)
        validation.advance_segment(attempt["attemptId"], "promotion-current", 1,
            self.principal)
        segment = m.AiBusinessV4ValidationSegment.objects.get(
            attempt_id=attempt["attemptId"], source=self.sources["promotion-current"])
        with patch.object(validation, "_mac", return_value="0" * 64), \
                self.assertRaises(AiError):
            validation._verified_segment(segment, *validation._key())
        with self.assertRaises(AiError):
            validation.advance_segment(attempt["attemptId"], "promotion-current", 1,
                self.principal)
        with transaction.atomic():
            AppUser.objects.filter(email=self.principal.email).update(version=2)
            with self.assertRaises(AiError):
                validation.advance_segment(attempt["attemptId"], "finance-context", 1,
                    self.principal)
            transaction.set_rollback(True)
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).status,
            "collecting")

    def test_first_page_metadata_must_match_completed_source_checkpoint(self):
        self.complete_mixed()
        source = m.AiBusinessV4Source.objects.get(
            pk=self.sources["promotion-current"].pk)
        chunk = m.AiBusinessV4Chunk.objects.get(source=source, sequence=1)
        receipt = m.AiBusinessV4ToolReceipt.objects.select_related("audit").get(
            source=source, sequence=1)
        saved = json.loads(source.checkpoint_json)
        saved["metadata"]["metricSemantics"]["reportedGmvCents"] = "forged"
        source.checkpoint_json = canonical(saved)
        with self.assertRaises(AnalysisContractError):
            validation._scan_page(validation._initial_progress(source), source,
                self.query, chunk, receipt, 1)

    def test_requested_but_physically_missing_or_duplicate_baseline_cannot_form_run(self):
        sources = [{"key": "promotion-current", "domain": "netshop",
            "query": self.query}, {"key": "finance-context", "domain": "finance",
            "query": self.finance_query}]
        measures = [{"sourceKey": "promotion-current", "measuredRowCount": 101,
            "maxRowUtf8Bytes": 2000, "pageEnvelopeUtf8Bytes": 2048,
            "sourceRevisionHint": "7:" + "a" * 12},
            {"sourceKey": "finance-context", "measuredRowCount": 120,
             "maxRowUtf8Bytes": 1200, "pageEnvelopeUtf8Bytes": 2048,
             "sourceRevisionHint": "7:" + "a" * 64}]
        request = {"schemaVersion": "business-analysis-request-v1",
            "question": "请求同比但来源缺失", "requestedDimensions": ["shop"],
            "requestedWindows": ["current", "yearAgo"]}
        with self.assertRaises(AnalysisContractError):
            evidence_v4.build_plan(client_request_id=uid("v4-client"),
                sources=sources, measurements=measures, analysis_request=request)
        duplicate = {"key": "promotion-duplicate", "domain": "netshop",
            "query": dict(self.query)}
        with self.assertRaises(AnalysisContractError):
            evidence_v4.build_plan(client_request_id=uid("v4-client"),
                sources=[*sources, duplicate],
                measurements=[*measures, {**measures[0],
                    "sourceKey": "promotion-duplicate"}],
                analysis_request={**request, "requestedWindows": ["current"]})

    def test_16_page_segment_and_real_resume_from_persisted_last_state(self):
        base = NetshopRow.objects.filter(source_row_key="v4-promo-0").first()
        values = {field.attname: getattr(base, field.attname)
            for field in base._meta.concrete_fields if field.attname != "id"}
        NetshopRow.objects.bulk_create([NetshopRow(**{**values,
            "source_row_key": f"v4-large-{index}",
            "source_row_hash": f"{100000 + index:064x}",
            "source_row_number": 2000 + index,
            "sku_id": str(2000 + index)}) for index in range(1500)])
        version = 1
        number = 1
        while True:
            page = self.collect("promotion-current", version, number)
            version = page["runVersion"]
            if page["finished"]: break
            number += 1
            self.assertLess(number, 30)
        self.assertGreater(page["pageCount"], validation.SEGMENT_PAGES)
        finance = self.collect("finance-context", version, 1)
        self.assertTrue(finance["finished"])
        attempt = validation.start_attempt(self.parent.id, finance["runVersion"],
            self.principal)
        first = validation.advance_segment(attempt["attemptId"],
            "promotion-current", 1, self.principal)
        self.assertEqual(first["endSequence"], 16)
        self.assertFalse(first["sourceCompleteCandidate"])
        # A new call reloads the immutable, HMAC-bound state from PostgreSQL.
        second = validation.advance_segment(attempt["attemptId"],
            "promotion-current", 2, self.principal)
        self.assertEqual(second["startSequence"], 17)
        self.assertTrue(second["sourceCompleteCandidate"])
        with self.assertRaises(AiError):
            validation.advance_segment(attempt["attemptId"],
                "promotion-current", 3, self.principal)
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).status,
            "collecting")
