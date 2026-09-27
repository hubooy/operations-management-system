"""Frozen SQL and real sealed-report checks for the 0026 profile boundary."""
from copy import deepcopy
from importlib import import_module
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from django.apps import apps
from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase, override_settings

from . import business_promotion_creation_contract as creation
from . import business_promotion_runtime_contract as contract
from . import models as m, test_business_diagnostic_screening as fixtures
from . import test_business_promotion_creation_contract as creation_fixtures
from .policy import canonical, digest, mutation


def migration():
    return import_module("ai_assistant.migrations.0026_business_promotion_profile")


class PromotionProfileSqlContractTests(unittest.TestCase):
    def test_exact_predecessors_old_profiles_and_file_versions_remain_frozen(self):
        sql = migration()
        previous = import_module("ai_assistant.migrations.0024_business_screening_runtime")
        file_sql = import_module("ai_assistant.migrations.0025_business_file_opc")
        self.assertEqual(sql.previous, previous)
        for old in ("ai_business_screening_report_guard", "ai_business_screening_workflow_guard",
                    "ai_screen_initial_guard", "ai_business_budget_report_guard", "ai_business_integrated_report_guard"):
            self.assertTrue(any(old in value for value in (sql.SCREENING_GUARD, sql.WORKFLOW_GUARD,
                sql.SCREEN_INITIAL, sql.BUDGET_GUARD, sql.INTEGRATED_GUARD)))
        self.assertIn(previous.REPORT_GUARD[previous.REPORT_GUARD.index("  IF (snapshot ? 'mappingPlan')"):],
                      sql.SCREENING_GUARD)
        self.assertEqual(len(file_sql.NEW_SQL), 5)
        self.assertNotIn("renderer_version=7", "\n".join((sql.PROMOTION_GUARD, sql.SCREEN_INITIAL)))
        self.assertIn("promotionCatalogDigest", sql.PROMOTION_GUARD)
        self.assertIn("(intent->>'id')", sql.PROMOTION_GUARD)
        self.assertNotIn("'||intent->>'id'||", sql.PROMOTION_GUARD)
        self.assertEqual(m.AiWorkflowRuns._meta.get_field("dry_run").get_internal_type(), "BigIntegerField")
        self.assertIn("flow.dry_run IS DISTINCT FROM 0", sql.PROMOTION_GUARD)
        self.assertNotIn("flow.dry_run IS DISTINCT FROM false", sql.PROMOTION_GUARD)
        self.assertTrue(all(value.startswith("CREATE OR REPLACE FUNCTION ") for value in
            (previous.NEW_BUDGET_GUARD, previous.INTEGRATED_REPORT_GUARD, previous.SCREEN_INITIAL)))
        self.assertTrue(all(value.startswith("CREATE FUNCTION ") for value in
            (previous.REPORT_GUARD, previous.WORKFLOW_GUARD)))
        self.assertIn("ai_business_evidence_sources WHERE run_id=parent.id", sql.PROMOTION_GUARD)
        self.assertIn("ai_promotion_workflow_orphan", sql.WORKFLOW_GUARD)
        for value in (sql.PROMOTION_GUARD, sql.SCREEN_INITIAL, sql.WORKFLOW_GUARD):
            self.assertIn("VOLATILE SET search_path=pg_catalog,public", value)
            self.assertNotIn("SECURITY DEFINER", value)

    def test_pinned_graph_and_tool_names_match_current_candidate_contract(self):
        sql = migration()
        self.assertIn(digest(contract.graph(False)), sql.PROMOTION_GUARD)
        self.assertIn(digest(contract.graph(True)), sql.PROMOTION_GUARD)
        for name in contract.TOOL_ORDER:
            self.assertIn(name, sql.PROMOTION_GUARD)
        self.assertIn("(snapshot ? 'budgetRef' AND flow.graph_digest IS DISTINCT FROM", sql.PROMOTION_GUARD)
        self.assertIn("NOT (snapshot ? 'budgetRef') AND flow.graph_digest IS DISTINCT FROM", sql.PROMOTION_GUARD)
        self.assertNotIn("IS DISTINCT FROM CASE WHEN", sql.PROMOTION_GUARD)
        self.assertIn(creation.PROMOTION_REF_SCHEMA, sql.PROMOTION_GUARD)
        self.assertIn(contract.ALGORITHM_VERSION, sql.PROMOTION_GUARD)


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionProfileMigrationTests(TransactionTestCase):
    user = fixtures.DiagnosticScreeningTests.user
    call = fixtures.DiagnosticScreeningTests.call
    collect_body = fixtures.DiagnosticScreeningTests.collect_body
    bundle = fixtures.DiagnosticScreeningTests.bundle
    input_for = fixtures.DiagnosticScreeningTests.input_for
    insert = fixtures.DiagnosticScreeningTests.insert
    seed = fixtures.DiagnosticScreeningTests.seed
    setUp = fixtures.DiagnosticScreeningTests.setUp

    def prepared(self, tag, *, mapped=False, budget=False, baseline=False):
        request = {"reportId": "promotion-guard-"+tag, "screeningId": "promotion-screen-"+tag,
            "question": "固定词货经营诊断", "sourceKey": "ads"}
        if baseline:
            request["baselineKey"] = "ads-previous"
        if mapped:
            request["mappingPairs"] = [{"salesKey": "sales", "masterKey": "master"}]
        if budget:
            request["budgetPlan"] = deepcopy(self.budget_plan)
        with patch.object(creation.transport, "catalog", return_value=creation_fixtures.catalog()):
            return creation.prepare_candidate(self.parent.id, request, self.admin)

    def insert_shape(self, candidate, *, snapshot=None, workflow=None, snapshot_raw=None,
                     input_raw=None, flow_changes=None):
        shape = candidate.shape
        chosen = snapshot or shape["snapshot"]
        flow_input = workflow or shape["workflowInput"]
        changed = {"graph_json": canonical(shape["graph"]), "graph_digest": digest(shape["graph"]),
            "allowed_tools_json": canonical(shape["allowedTools"]),
            "tool_policy_digest": shape["toolCatalogDigest"], "dry_run": False}
        changed.update(flow_changes or {})
        return self.insert((chosen, flow_input, candidate._base.budget),
            snapshot_raw=snapshot_raw, input_raw=input_raw, flow_changes=changed)

    def deny(self, candidate, **kwargs):
        before = tuple(model.objects.count() for model in
            (m.AiWorkflowRuns, m.AiReportRun, m.AiBusinessBudgetPlan, m.AiBusinessScreeningRun))
        with self.assertRaises(DatabaseError), mutation(self.admin):
            self.insert_shape(candidate, **kwargs)
        self.assertEqual(before, tuple(model.objects.count() for model in
            (m.AiWorkflowRuns, m.AiReportRun, m.AiBusinessBudgetPlan, m.AiBusinessScreeningRun)))

    def test_actual_sealed_report_and_old_rows_unmodified(self):
        old = (self.report.snapshot_json, self.report.workflow.input_json)
        for mapped, budget in ((False, False), (True, True)):
            candidate = self.prepared(f"valid-{int(mapped)}-{int(budget)}", mapped=mapped, budget=budget)
            with mutation(self.admin):
                saved = self.insert_shape(candidate)
            self.assertEqual(saved.snapshot_json, canonical(candidate.shape["snapshot"]))
            self.assertEqual(saved.workflow.input_json, canonical(candidate.shape["workflowInput"]))
            self.assertEqual(bool(saved.budget_plan_id), budget)
        self.report.refresh_from_db(); self.report.workflow.refresh_from_db()
        self.assertEqual(old, (self.report.snapshot_json, self.report.workflow.input_json))

    def test_selector_actor_source_context_or_workflow_mismatch_is_atomic(self):
        candidate = self.prepared("bad")
        for change in (
            lambda s: s["promotionSelector"].update(sourceKey="master"),
            lambda s: s["promotionSelector"].update(views=["keyword_sku"]),
            lambda s: s.update(contextDigest="0"*64),
            lambda s: s.update(promotionCatalogDigest="0"*64),
            lambda s: s.update(promotionAlgorithmVersion="wrong"),
            lambda s: s["screeningIntent"].update(id="other-screen"),
        ):
            snapshot = deepcopy(candidate.shape["snapshot"]); change(snapshot)
            with self.subTest(snapshot=snapshot["promotionSelector"]): self.deny(candidate, snapshot=snapshot)
        for changes in ({"owner_email": "different@example.invalid"},
                        {"scope_json": "{}"}, {"dry_run": True},
                        {"graph_digest": "0"*64}, {"allowed_tools_json": "[]"}):
            with self.subTest(flow=changes): self.deny(candidate, flow_changes=changes)
        altered = deepcopy(candidate.shape["workflowInput"])
        altered["promotionRef"]["promotionSelector"]["sourceKey"] = "master"
        self.deny(candidate, workflow=altered)
        snapshot = candidate.shape["snapshot"]
        raw = canonical(snapshot).replace('"sourceKey":"ads"', '"sourceKey":"ads","sourceKey":"ads"', 1)
        self.deny(candidate, snapshot_raw=raw)

    def test_orphan_workflow_and_reverse_guard(self):
        candidate = self.prepared("orphan")
        value = candidate.shape
        with self.assertRaises(DatabaseError), mutation(self.admin):
            m.AiWorkflowRuns.objects.create(id="promotion-orphan", owner_email=self.admin.email,
                scope_json="null", client_request_id="promotion-orphan", request_digest="a"*64,
                name="孤儿", graph_json=canonical(value["graph"]), graph_digest=digest(value["graph"]),
                input_json=canonical(value["workflowInput"]), dry_run=False)
        sql = migration(); editor = SimpleNamespace(connection=connection)
        with transaction.atomic():
            sql.uninstall(apps, editor)
            for name, source in (("ai_business_screening_report_guard", sql.previous.REPORT_GUARD),
                    ("ai_business_screening_workflow_guard", sql.previous.WORKFLOW_GUARD),
                    ("ai_screen_initial_guard", sql.previous.SCREEN_INITIAL)):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT prosrc FROM pg_proc WHERE proname=%s", [name])
                    self.assertEqual(cursor.fetchone()[0], source.split("$$")[1])
            sql.install(apps, editor)
        with mutation(self.admin): self.insert_shape(candidate)
        with self.assertRaisesRegex(RuntimeError, "词货"): sql.uninstall(apps, editor)

    def test_old_profile_rejects_promotion_fields_and_file_guard_unchanged(self):
        old = self.bundle()
        old[0]["promotionSelector"] = {"sourceKey": "ads", "views": ["keyword_sku", "keyword_sku_context"]}
        with self.assertRaises(DatabaseError), mutation(self.admin): self.insert(old)
        previous_files = import_module("ai_assistant.migrations.0025_business_file_opc")
        for expected in previous_files.NEW_SQL:
            name = expected.split("FUNCTION ", 1)[1].split("(", 1)[0]
            with connection.cursor() as cursor:
                cursor.execute("SELECT prosrc FROM pg_proc WHERE proname=%s", [name])
                self.assertEqual(cursor.fetchone()[0], expected.split("$$")[1])
