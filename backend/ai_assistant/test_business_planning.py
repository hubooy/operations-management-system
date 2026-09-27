"""Workbench admission and discovery never dispatch business/model work."""
import json
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from unittest.mock import patch
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from business_analysis.test_planning import fixture
from . import business_planning, business_evidence as evidence, models as m, tests as fixtures
from .control_models import AiMutationAudit, AiWriteReceipt
from .policy import AiError, canonical, digest


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessPlanningTests(TestCase):
    user = fixtures.AiDomainTests.user
    call = fixtures.AiDomainTests.call

    def setUp(self):
        fixtures.AiDomainTests.setUp(self)
        self.admin = self.user("planner@example.invalid", "admin", None)
        self.other_admin = self.user("other-planner@example.invalid", "admin", None)
        self.request = fixture()

    def plan(self):
        return business_planning.preview(self.request, self.admin)

    def create(self, client="planned", actor=None):
        return evidence.create({"clientRequestId": client, **self.plan()["evidenceRequest"]}, actor or self.admin)["item"]

    def test_preview_reader_is_read_only_and_auth_bound(self):
        before = (m.AiBusinessEvidenceRun.objects.count(), m.AiWorkflowRuns.objects.count(), AiMutationAudit.objects.count(), AiWriteReceipt.objects.count())
        with patch("ai_assistant.transport.execute_tool") as tools, patch("ai_assistant.provider.turn") as model, CaptureQueriesContext(connection) as queries:
            with override_settings(DJANGO_PROCESS_ROLE="ai_reader"), patch("ai_assistant.views.authority"):
                response = self.call("/api/ai/business-plan/preview", self.request, self.admin)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response["Cache-Control"], "no-store")
            tools.assert_not_called()
            model.assert_not_called()
        value = response.json()
        self.assertTrue(value["canCollect"])
        self.assertEqual(value["principalKey"], digest(["business-workbench", self.admin.email]))
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertFalse(any(any(table in q["sql"].lower() for table in ("netshop_rows", "sales_lines", "market_ranking_entries")) for q in queries))
        self.assertEqual(before, (m.AiBusinessEvidenceRun.objects.count(), m.AiWorkflowRuns.objects.count(), AiMutationAudit.objects.count(), AiWriteReceipt.objects.count()))
        for principal in (self.owner, self.viewer):
            self.assertEqual(self.call("/api/ai/business-plan/preview", self.request, principal).status_code, 403)
        with override_settings(DJANGO_PROCESS_ROLE="ai_writer"), patch("ai_assistant.views.authority"):
            self.assertEqual(self.call("/api/ai/business-plan/preview", self.request, self.admin).status_code, 403)
        self.assertEqual(self.call("/api/ai/business-plan/preview", principal=self.admin, method="GET").status_code, 405)

    def test_submission_principal_binding_rejects_switched_account_and_scope(self):
        from access_control.models import AppUser
        key = evidence.principal_key(self.admin)
        body = {"clientRequestId": "bound", "expectedPrincipalKey": key, **self.plan()["evidenceRequest"]}
        before = (m.AiBusinessEvidenceRun.objects.count(), m.AiWorkflowRuns.objects.count(),
            m.AiReportRun.objects.count(), AiWriteReceipt.objects.count(), AiMutationAudit.objects.count())
        denied = self.call("/api/ai/business-evidence", body, self.other_admin)
        self.assertEqual(denied.status_code, 403, denied.content)
        report = {"clientRequestId": "bound-report", "expectedPrincipalKey": key,
            "evidenceRunId": "missing-evidence", "question": "分析", "dryRun": True}
        denied = self.call("/api/ai/business-reports", report, self.other_admin)
        self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(before, (m.AiBusinessEvidenceRun.objects.count(), m.AiWorkflowRuns.objects.count(),
            m.AiReportRun.objects.count(), AiWriteReceipt.objects.count(), AiMutationAudit.objects.count()))
        AppUser.objects.filter(email=self.admin.email).update(scope={"warehouses": [], "channels": [], "platforms": ["京东"]})
        denied = self.call("/api/ai/business-evidence", body, self.admin)
        self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(before, (m.AiBusinessEvidenceRun.objects.count(), m.AiWorkflowRuns.objects.count(),
            m.AiReportRun.objects.count(), AiWriteReceipt.objects.count(), AiMutationAudit.objects.count()))
        AppUser.objects.filter(email=self.admin.email).update(scope=None)
        result = evidence.create(body, self.admin)
        self.assertNotIn("expectedPrincipalKey", result["item"]["plan"])
        self.assertTrue(evidence.create(body, self.admin)["replayed"])

    def test_plan_and_creation_bytes_match_and_metadata_is_immutable(self):
        planned = self.plan()
        body = {"clientRequestId": "same", **planned["evidenceRequest"]}
        result = evidence.create(body, self.admin)
        row = m.AiBusinessEvidenceRun.objects.get(pk=result["item"]["id"])
        self.assertEqual(digest(row.plan_json), planned["planDigest"])
        self.assertEqual(len(row.plan_json.encode()), planned["capacity"]["planBytes"])
        workflow = {"evidenceRunId": row.id, "question": self.request["question"], "sources": body["sources"]}
        self.assertEqual(len(canonical(workflow).encode()), planned["capacity"]["workflowBytes"])
        self.assertTrue(evidence.create(body, self.admin)["replayed"])
        changed = deepcopy(body)
        changed["analysisRequest"]["question"] = "改变问题"
        with self.assertRaises(AiError):
            evidence.create(changed, self.admin)
        self.assertEqual(m.AiBusinessEvidenceRun.objects.count(), 1)
        self.assertEqual(result["item"]["analysisRequestMeaning"], "requested_only_not_source_availability_or_dimension_coverage")
        for update in ({"question": ""}, {"requestedDimensions": ["sku", "sku"]}, {"requestedWindows": ["bad"]}, {"extra": True}):
            invalid = deepcopy(body)
            invalid["clientRequestId"] = "bad"
            invalid["analysisRequest"].update(update)
            with self.assertRaises(AiError):
                evidence.create(invalid, self.admin)

    def test_legacy_plan_keeps_exact_bytes_and_replay(self):
        source = self.plan()["sources"][0]
        body = {"clientRequestId": "legacy", "sources": [source]}
        item = evidence.create(body, self.admin)["item"]
        row = m.AiBusinessEvidenceRun.objects.get(pk=item["id"])
        self.assertEqual(row.plan_json, canonical({"schemaVersion": "business-evidence-v1", "sources": [source]}))
        self.assertTrue(evidence.create(body, self.admin)["replayed"])
        self.assertEqual(evidence.listing({}, self.admin)["items"][0]["question"], "")

    def test_legacy_over_workflow_limit_still_creates_and_replays(self):
        sources = [{"key": "legacy-"+str(i), "domain": "sales", "query": {
            "platform": "平"*100, "shop": "店"*99+str(i), "channel": "渠"*100,
            "startDate": "2026-08-01", "endDate": "2026-08-01"}} for i in range(9)]
        body = {"clientRequestId": "legacy-wide", "sources": sources}
        original = canonical({"schemaVersion": "business-evidence-v1", "sources": sources})
        self.assertGreater(len(canonical({"evidenceRunId": "evidence-"+"0"*36, "question": "", "sources": sources}).encode()), 8000)
        self.assertLessEqual(len(original.encode()), 16000)
        first = evidence.create(body, self.admin)
        self.assertTrue(evidence.create(body, self.admin)["replayed"])
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=first["item"]["id"]).plan_json, original)

    def test_new_request_requires_exact_scheduled_windows_and_one_original_period(self):
        planned = self.plan()["evidenceRequest"]
        def reject(changed):
            before = m.AiBusinessEvidenceRun.objects.count()
            with self.assertRaises(AiError):
                evidence.create({"clientRequestId": "tampered", **changed}, self.admin)
            self.assertEqual(m.AiBusinessEvidenceRun.objects.count(), before)

        missing = deepcopy(planned)
        missing["sources"] = [s for s in missing["sources"] if s["query"]["window"] != "yearAgo"]
        reject(missing)
        extra = deepcopy(planned)
        extra["analysisRequest"]["requestedWindows"] = ["current"]
        reject(extra)
        mixed = deepcopy(planned)
        mixed["sources"][0]["query"]["endDate"] = "2024-02-28"
        reject(mixed)
        historical_master = deepcopy(planned)
        master = next(s for s in historical_master["sources"] if s["query"].get("dataset") == "master")
        master["query"]["window"] = "yearAgo"
        reject(historical_master)
        item = evidence.create({"clientRequestId": "current-master", **planned}, self.admin)["item"]
        masters = [s for s in item["plan"]["sources"] if s["query"].get("dataset") == "master"]
        self.assertEqual([s["query"]["window"] for s in masters], ["current"])
        self.assertEqual(item["plan"]["analysisRequest"]["requestedWindows"], ["current", "previous", "yearAgo"])

        # Legacy admission continues to allow independently selected windows.
        legacy = {"clientRequestId": "legacy-independent", "sources": missing["sources"]}
        self.assertEqual(evidence.create(legacy, self.admin)["item"]["plan"]["sources"], missing["sources"])

    def test_capacity_rejected_before_any_collection_or_workflow(self):
        self.request["shops"][0].update(datasets=["promotion", "sku", "spu", "b2b", "master"], salesChannels=["京东渠道"])
        plan = self.plan()
        self.assertFalse(plan["canCollect"])
        with self.assertRaises(AiError):
            evidence.create({"clientRequestId": "too-many", **plan["evidenceRequest"]}, self.admin)
        self.request = fixture()
        self.request["question"] = "中"*1000
        self.request["shops"] = [{"platform": "京东", "shop": "店"*99+str(i), "datasets": ["promotion"], "salesChannels": []} for i in range(4)]
        plan = self.plan()
        self.assertEqual(plan["capacity"]["sourceCount"], 12)
        self.assertGreater(plan["capacity"]["workflowBytes"], 8000)
        self.assertFalse(plan["canCollect"])
        with self.assertRaises(AiError):
            evidence.create({"clientRequestId": "too-wide", **plan["evidenceRequest"]}, self.admin)
        self.assertFalse(m.AiBusinessEvidenceRun.objects.exists())
        self.assertFalse(m.AiWorkflowRuns.objects.exists())

    def test_list_is_compact_paginated_owner_bound_and_exact_recovery(self):
        # Windows clock resolution may give both records the same timestamp;
        # that correctly falls back to UUID ordering, not insertion order.
        stamp = datetime(2026, 8, 1, tzinfo=timezone.utc)
        field = m.AiBusinessEvidenceRun._meta.get_field("created_at")
        with patch.object(field, "_get_default", side_effect=[stamp, stamp+timedelta(seconds=1), stamp+timedelta(seconds=2)]):
            first = self.create("first")
            self.create("second")
            self.create("first", self.other_admin)
        page = evidence.listing({"pageSize": "1"}, self.admin)
        self.assertEqual(page["pagination"], {"page": 1, "pageSize": 1, "total": 2, "hasMore": True})
        self.assertEqual(page["items"][0]["clientRequestId"], "second")
        self.assertNotIn("plan", page["items"][0])
        self.assertNotIn("sources", page["items"][0])
        self.assertEqual(page["items"][0]["rowCount"], 0)
        self.assertEqual(page["items"][0]["completedSources"], 0)
        self.assertEqual(evidence.listing({"page": "2", "pageSize": "1"}, self.admin)["items"][0]["id"], first["id"])
        exact = evidence.listing({"clientRequestId": "first"}, self.admin)
        self.assertEqual([r["id"] for r in exact["items"]], [first["id"]])
        for params in ({"page": "0"}, {"pageSize": "21"}, {"page": "01"}, {"page": "-1"}, {"page": "1.5"}, {"page": "9"*5000}, {"unknown": "x"}):
            with self.assertRaises(AiError):
                evidence.listing(params, self.admin)
        with self.assertRaises(AiError):
            evidence.listing({}, self.viewer)
        with override_settings(DJANGO_PROCESS_ROLE="ai_reader"), patch("ai_assistant.views.authority"):
            response = self.call("/api/ai/business-evidence", principal=self.admin, method="GET")
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["pagination"]["total"], 2)

    def test_detail_reports_filter_exact_json_owner_and_bound_projection(self):
        item = self.create()
        def report(key, owner, evidence_id):
            flow = m.AiWorkflowRuns.objects.create(id="flow-"+key, owner_email=owner.email, client_request_id=key,
                request_digest=digest(key), scope_json="null", name="合成", graph_json='{"nodes":[]}', graph_digest=digest(key), dry_run=1)
            m.AiReportRun.objects.create(id="report-"+key, owner_email=owner.email, client_request_id=key,
                request_digest=digest(key), workflow=flow, snapshot_json=json.dumps({"schemaVersion": "business-report-v1", "evidenceRunId": evidence_id}))
        for i in range(12):
            report(str(i), self.admin, item["id"])
        report("other-owner", self.other_admin, item["id"])
        report("substring", self.admin, item["id"]+"-other")
        with CaptureQueriesContext(connection) as queries:
            detail = evidence.detail(item["id"], self.admin)
        self.assertEqual(len(detail["reports"]), 10)
        self.assertEqual(detail["reportsPagination"], {"limit": 10, "hasMore": True})
        self.assertFalse({"report-other-owner", "report-substring"} & {r["id"] for r in detail["reports"]})
        report_query = next(q["sql"] for q in queries if "ai_report_runs" in q["sql"])
        self.assertIn("LIMIT 11", report_query)
        self.assertNotIn("snapshot_json", report_query.split(" FROM ")[0])
        with self.assertRaises(AiError):
            evidence.detail(item["id"], self.other_admin)
