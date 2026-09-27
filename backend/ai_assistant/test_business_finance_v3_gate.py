"""PostgreSQL negative gates for inert v3 finance source directories."""
from copy import deepcopy
from importlib import import_module

from django.apps import apps
from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis import evidence_v2, evidence_v3
from . import models as m
from .policy import canonical, digest, uid


def daily():
    return {"key": "sales-current", "domain": "sales", "query": {
        "platform": "京东", "shop": "测试店", "channel": "京东", "startDate": "2026-08-20",
        "endDate": "2026-09-18", "window": "current"}}


def finance():
    return {"key": "finance-context", "domain": "finance", "query": {
        "months": ["2026-08", "2026-09"],
        "scope": {"scope_key": "shop:测试店", "scope_type": "shop", "scope_name": "测试店", "group_name": "京东组"},
        "analysisPeriod": {"startDate": "2026-08-20", "endDate": "2026-09-18"}}}


class FinanceV3GateTests(TestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("v3 SQL gates require PostgreSQL")
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
        now = timezone.now()
        self.email = "finance-v3-owner@example.test"
        AppUser.objects.create(email=self.email, display_name="Finance V3", role=role, status="active",
                               scope=None, version=1, created_at=now, updated_at=now)
        self.request = {"schemaVersion": "business-analysis-request-v1", "question": "日经营及自然月财报",
                        "requestedDimensions": ["shop"], "requestedWindows": ["current"]}

    def catalog(self):
        return evidence_v3.build_catalog([daily(), finance()], analysis_request=self.request)

    def parent(self, built=None, **changes):
        built = built or self.catalog()
        values = {"id": uid("evidence"), "owner_email": self.email, "client_request_id": uid("client"),
                  "request_digest": built["planDigest"], "plan_json": canonical(built["header"])}
        values.update(changes)
        return m.AiBusinessEvidenceRun.objects.create(**values)

    def source(self, parent, entry, **changes):
        values = {"id": uid("source"), "run": parent, "source_key": entry["key"],
                  "ordinal": entry["ordinal"], "domain": entry["domain"],
                  "query_json": canonical(entry["query"]), "query_digest": entry["queryDigest"]}
        values.update(changes)
        return m.AiBusinessEvidenceSource.objects.create(**values)

    def drain(self):
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ai_business_v3_directory_complete IMMEDIATE")
            cursor.execute("SET CONSTRAINTS ai_business_v3_directory_complete DEFERRED")

    def complete(self):
        built = self.catalog(); parent = self.parent(built)
        sources = [self.source(parent, entry) for entry in built["entries"]]
        self.drain()
        return parent, sources

    def test_only_exact_initial_plan_and_two_sources_can_be_staged(self):
        parent, sources = self.complete()
        self.assertEqual(parent.plan_json, canonical(self.catalog()["header"]))
        self.assertEqual({item.domain for item in sources}, {"sales", "finance"})
        self.assertEqual(parent.status, "collecting")
        self.assertEqual(parent.stored_bytes, 0)
        self.assertFalse(m.AiBusinessEvidenceChunk.objects.filter(run=parent).exists())
        self.assertTrue(all(item.page_count == 0 and item.checkpoint_json == "{}" for item in sources))

    def test_wrong_actor_scope_and_authority_claim_are_rejected(self):
        for mode in ("absent", "disabled", "operator", "scoped", "parent_scope", "claimed_report"):
            with self.subTest(mode=mode), self.assertRaises(DatabaseError), transaction.atomic():
                if mode == "absent":
                    self.parent(owner_email="missing@example.test")
                elif mode == "parent_scope":
                    self.parent(scope_json='{"shops":["测试店"]}')
                elif mode == "claimed_report":
                    built = self.catalog(); built["header"]["reportGenerationSupported"] = True
                    self.parent(built)
                else:
                    changes = {"disabled": {"status": "disabled"}, "operator": {"role_id": "operator"},
                               "scoped": {"scope": {"shops": ["测试店"]}}}[mode]
                    if mode == "operator":
                        AccessRole.objects.get_or_create(code="operator", defaults={"rank": 30, "label": "Operator"})
                    AppUser.objects.filter(email=self.email).update(**changes)
                    self.parent()

    def test_wrong_finance_month_scope_domain_and_cross_source_dates_rejected(self):
        for mode in ("missing_month", "gap", "scope", "domain", "period", "daily_dates", "wrong_digest"):
            with self.subTest(mode=mode), self.assertRaises(DatabaseError), transaction.atomic():
                built = self.catalog(); parent = self.parent(built)
                for entry in built["entries"]:
                    item = deepcopy(entry)
                    if item["domain"] == "finance":
                        if mode == "missing_month": item["query"]["months"] = ["2026-08"]
                        if mode == "gap": item["query"]["months"] = ["2026-07", "2026-09"]
                        if mode == "scope": item["query"]["scope"]["scope_type"] = "channel"
                        if mode == "domain": item["domain"] = "market"
                        if mode == "period": item["query"]["analysisPeriod"]["startDate"] = "2026-08-21"
                    elif mode == "daily_dates": item["query"]["startDate"] = "2026-08-21"
                    if mode != "wrong_digest": item["queryDigest"] = digest(item["query"])
                    else: item["queryDigest"] = "0" * 64
                    self.source(parent, item)
                self.drain()

    def test_v3_chunks_checkpoint_and_terminal_state_are_closed(self):
        parent, sources = self.complete()
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run=parent, source_key=sources[0].source_key,
                sequence=1, payload_json="{}", payload_digest=digest("{}"))
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceSource.objects.filter(pk=sources[0].id).update(version=2, checkpoint_run_version=2,
                                                                                 checkpoint_json='{"pageCount":1}')
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceRun.objects.filter(pk=parent.id).update(version=2, status="sealed")
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=parent.id).version, 1)
        self.assertFalse(m.AiBusinessEvidenceChunk.objects.filter(run=parent).exists())

    def test_v2_and_legacy_rows_keep_their_old_paths(self):
        legacy = m.AiBusinessEvidenceRun.objects.create(id=uid("legacy"), owner_email=self.email,
            client_request_id=uid("legacy-client"), request_digest=digest("legacy"),
            plan_json='{"schemaVersion":"business-evidence-v1","sources":[]}')
        raw = legacy.plan_json
        built = evidence_v2.build_catalog([daily()])
        v2 = self.parent(built)
        source = self.source(v2, built["entries"][0]); self.drain()
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=legacy.id).plan_json, raw)
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=v2.id).plan_json, canonical(built["header"]))
        self.assertEqual(source.domain, "sales")
        with self.assertRaises(DatabaseError), transaction.atomic():
            self.source(v2, {**built["entries"][0], "key": "finance-foreign", "ordinal": 2,
                "domain": "finance", "query": finance()["query"], "queryDigest": digest(finance()["query"])})

    def test_malformed_old_plan_keeps_existing_database_behavior(self):
        old = m.AiBusinessEvidenceRun.objects.create(id=uid("legacy"), owner_email=self.email,
            client_request_id=uid("legacy-client"), request_digest=digest("malformed-old"),
            plan_json="legacy-unparsed-plan")
        m.AiBusinessEvidenceRun.objects.filter(pk=old.id).update(version=2)
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=old.id).plan_json, "legacy-unparsed-plan")
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceRun.objects.create(id=uid("v3"), owner_email=self.email,
                client_request_id=uid("v3-client"), request_digest=digest("malformed-v3"),
                plan_json='{"schemaVersion":"business-evidence-v3",')

    def test_reverse_rejects_even_uncollected_v3_rows(self):
        migration = import_module("ai_assistant.migrations.0028_business_finance_v3_gate")
        parent, _ = self.complete()
        with connection.schema_editor() as editor:
            with self.assertRaises(RuntimeError): migration.uninstall(apps, editor)
        self.assertTrue(m.AiBusinessEvidenceRun.objects.filter(pk=parent.id).exists())

    def test_empty_reverse_and_reinstall_preserve_v2_insert(self):
        migration = import_module("ai_assistant.migrations.0028_business_finance_v3_gate")
        def grants():
            with connection.cursor() as cursor:
                cursor.execute("""SELECT relname,coalesce(relacl::text,'') FROM pg_class WHERE relname IN
                    ('ai_business_evidence_runs','ai_business_evidence_sources','ai_business_evidence_chunks',
                     'finance_lines','access_control_users') ORDER BY relname""")
                return cursor.fetchall()
        before_grants = grants()
        with connection.schema_editor() as editor:
            migration.uninstall(apps, editor)
            migration.install(apps, editor)
        self.assertEqual(grants(), before_grants)
        built = evidence_v2.build_catalog([daily()])
        parent = self.parent(built)
        self.source(parent, built["entries"][0]); self.drain()
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=parent.id).plan_json, canonical(built["header"]))
