"""Isolated PostgreSQL tests for an internal, inert v3 source directory."""
from copy import deepcopy
from unittest.mock import patch

from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis import evidence_v3
from sales.auth import Principal

from . import business_evidence, business_evidence_v3, models as m
from .policy import AiError, canonical, digest, uid


def sources():
    return [{"key": "sales-current", "domain": "sales", "query": {
        "platform": "京东", "shop": "测试店", "channel": "京东", "startDate": "2026-08-20",
        "endDate": "2026-09-18", "window": "current"}},
        {"key": "finance-context", "domain": "finance", "query": {
            "months": ["2026-08", "2026-09"], "scope": {"scope_key": "shop:测试店",
                "scope_type": "shop", "scope_name": "测试店", "group_name": "京东组"},
            "analysisPeriod": {"startDate": "2026-08-20", "endDate": "2026-09-18"}}}]


def request():
    return {"schemaVersion": "business-analysis-request-v1", "question": "店铺经营与月度财报背景",
            "requestedDimensions": ["shop"], "requestedWindows": ["current"]}


class BusinessEvidenceV3PlanTests(TestCase):
    def setUp(self):
        if connection.vendor != "postgresql": self.skipTest("v3 database gate requires PostgreSQL")
        self.principal = Principal("v3-plan@example.test", "Synthetic", "admin", None)
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
        now = timezone.now()
        AppUser.objects.create(email=self.principal.email, display_name="Synthetic", role=role,
            status="active", scope=None, version=1, created_at=now, updated_at=now)
        self.body = {"schemaVersion": evidence_v3.HEADER_SCHEMA, "clientRequestId": "v3-plan-request",
                     "sources": sources(), "analysisRequest": request()}

    def create(self):
        return business_evidence_v3.create(self.body, self.principal)

    def test_initial_real_parent_directory_and_complete_read_pages(self):
        result = self.create()
        self.assertFalse(result["replayed"])
        run_id = result["item"]["id"]
        row = m.AiBusinessEvidenceRun.objects.get(pk=run_id)
        self.assertEqual((row.status, row.collection_status, row.version, row.stored_bytes),
                         ("collecting", "manual", 1, 0))
        self.assertEqual(row.state_json, "{}")
        self.assertFalse(m.AiBusinessEvidenceChunk.objects.filter(run_id=run_id).exists())
        self.assertEqual(m.AiBusinessEvidenceSource.objects.filter(run_id=run_id).count(), 2)
        detail = business_evidence_v3.detail(run_id, self.principal)
        self.assertEqual(detail["item"], result["item"])
        self.assertFalse(detail["plan"]["reportGenerationSupported"])
        pages, offset = [], 0
        while True:
            page = business_evidence_v3.directory(run_id, offset=offset, limit=1, principal=self.principal)
            pages.append(page)
            offset = page["nextOffset"]
            if offset is None: break
        self.assertEqual(evidence_v3.validate_directory_pages(pages, self.body["sources"],
            run_id=run_id, evidence_version=1, analysis_request=self.body["analysisRequest"])["sourceCount"], 2)
        self.assertEqual({item["domain"] for page in pages for item in page["items"]}, {"finance", "sales"})
        self.assertTrue(all(not item["reportGenerationSupported"] for item in [detail["item"]]))

    def test_request_idempotence_and_changed_scope_collision(self):
        first = self.create()
        again = self.create()
        self.assertTrue(again["replayed"])
        self.assertEqual(first["item"], again["item"])
        self.assertEqual(m.AiBusinessEvidenceRun.objects.count(), 1)
        self.body["sources"][1]["query"]["scope"]["group_name"] = "另一组"
        with self.assertRaises(AiError) as caught: self.create()
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(m.AiBusinessEvidenceRun.objects.count(), 1)

    def test_current_actor_and_month_fail_before_any_plan_write(self):
        original = deepcopy(self.body)
        for change in (lambda: AppUser.objects.filter(email=self.principal.email).update(status="disabled"),
                       lambda: AppUser.objects.filter(email=self.principal.email).update(scope={"warehouses": [], "channels": [], "platforms": []}),
                       lambda: self.body["sources"][1]["query"].update(months=["2026-08"])):
            with self.subTest(change=change), self.assertRaises(AiError), transaction.atomic():
                change()
                self.create()
                transaction.set_rollback(True)
            self.body = deepcopy(original)
        self.assertFalse(m.AiBusinessEvidenceRun.objects.exists())
        with self.assertRaises(AiError):
            business_evidence_v3.create(self.body, Principal("local-admin@teruisi.local", "Local", "admin", None))
        self.assertFalse(m.AiBusinessEvidenceRun.objects.exists())

    def test_directory_rejects_initial_sql_tamper_and_late_revocation(self):
        built = evidence_v3.build_catalog(self.body["sources"], analysis_request=self.body["analysisRequest"])
        forged = deepcopy(built["header"]); forged["catalogDigest"] = "f" * 64
        with transaction.atomic():
            parent = m.AiBusinessEvidenceRun.objects.create(id=uid("evidence"), owner_email=self.principal.email,
                client_request_id=uid("client"), request_digest=business_evidence_v3._identity({"header": forged}),
                plan_json=canonical(forged), collection_status="manual")
            for entry in built["entries"]:
                m.AiBusinessEvidenceSource.objects.create(id=uid("source"), run=parent,
                    source_key=entry["key"], ordinal=entry["ordinal"], domain=entry["domain"],
                    query_json=canonical(entry["query"]), query_digest=entry["queryDigest"])
        with self.assertRaises(AiError) as caught:
            business_evidence_v3.directory(parent.id, principal=self.principal)
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceSource.objects.filter(run_id=parent.id).update(query_json="{}")
        normal = self.create()["item"]["id"]
        original = business_evidence_v3._unchanged
        calls = []
        def revoked(*args):
            calls.append(1)
            if len(calls) == 2:
                AppUser.objects.filter(email=self.principal.email).update(version=2)
            return original(*args)
        with patch.object(business_evidence_v3, "_unchanged", side_effect=revoked):
            with self.assertRaises(AiError):
                business_evidence_v3.directory(normal, principal=self.principal)
        self.assertEqual(len(calls), 2)

    def test_v2_create_and_directory_remain_unchanged(self):
        old_body = {"schemaVersion": "business-evidence-v2", "clientRequestId": "old-v2-request",
                    "sources": [sources()[0]], "analysisRequest": request()}
        result = business_evidence.create(old_body, self.principal)
        self.assertEqual(result["item"]["status"], "collecting")
        page = business_evidence.directory(result["item"]["id"], {}, self.principal)
        self.assertEqual(page["schemaVersion"], "business-evidence-directory-page-v2")
        self.assertEqual(page["items"][0]["domain"], "sales")
        self.assertFalse(m.AiBusinessEvidenceChunk.objects.filter(run_id=result["item"]["id"]).exists())
