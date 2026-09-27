"""Prospective profile SQL over actual sealed fixtures; no Agent dispatch."""
from copy import deepcopy
from importlib import import_module
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from django.apps import apps
from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase, override_settings

from business_analysis import screening_storage as storage_contract
from business_analysis.contracts import digest as object_digest
from . import business_budget_store as budget_store, business_diagnostic_screening as screening
from . import business_screening_runtime as runtime, business_screening_runtime_contract as contract
from . import business_screening_store as store
from . import models as m, test_business_diagnostic_screening as fixtures
from . import test_business_screening_storage as storage_fixtures
from .policy import AiError, canonical, digest, mutation


def migration():
    return import_module("ai_assistant.migrations.0024_business_screening_runtime")


class SqlContractTests(unittest.TestCase):
    def test_frozen_predecessors_unchanged_and_all_functions_have_safe_privileges(self):
        sql = migration()
        integrated = import_module("ai_assistant.migrations.0022_business_integrated_reports")
        storage = import_module("ai_assistant.migrations.0023_business_screening_storage")
        self.assertEqual(sql.PAIR_CHECK, integrated.REPORT_GUARD[
            integrated.REPORT_GUARD.index("  plan_text:="):integrated.REPORT_GUARD.index("  SELECT * INTO flow")])
        for name in ("NEW_BUDGET_GUARD", "INTEGRATED_REPORT_GUARD", "SCREEN_INITIAL", "REPORT_GUARD", "WORKFLOW_GUARD"):
            value = getattr(sql, name)
            self.assertIn("VOLATILE SET search_path=pg_catalog,public", value)
            self.assertNotIn("SECURITY DEFINER", value)
        self.assertNotIn("FOR UPDATE", sql.REPORT_GUARD)
        self.assertNotIn("FOR UPDATE", sql.WORKFLOW_GUARD)
        self.assertEqual(sql.SCREEN_INITIAL.count("FOR UPDATE"), 1)
        self.assertEqual(storage.INITIAL.count("business-agent-screening-reference-v1"), 0)

    def test_intent_constants_match_frozen_runtime_contract(self):
        intent = contract.intent("screening-fixed", "a"*64)
        self.assertEqual(set(intent), set(contract.INTENT_FIELDS))
        for key, value in intent.items():
            if key not in {"id", "selectionPlanDigest"}:
                self.assertIn("'"+value+"'", migration().INTENT_CHECK)
        self.assertIn("NEW.id IS DISTINCT FROM snapshot->'screeningIntent'->>'id'", migration().SCREEN_INITIAL)


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class ScreeningRuntimeGuardTests(TransactionTestCase):
    # Module aliases avoid discovering imported TestCase classes a second time.
    user = fixtures.DiagnosticScreeningTests.user
    call = fixtures.DiagnosticScreeningTests.call
    collect_body = fixtures.DiagnosticScreeningTests.collect_body
    bundle = fixtures.DiagnosticScreeningTests.bundle
    input_for = fixtures.DiagnosticScreeningTests.input_for
    insert = fixtures.DiagnosticScreeningTests.insert
    seed = fixtures.DiagnosticScreeningTests.seed
    setUp = fixtures.DiagnosticScreeningTests.setUp

    def screening_bundle(self, *, mapped=False, budget=False):
        self.serial += 1
        report_id = "screen-runtime-report-"+str(self.serial)
        prepared_budget = budget_store.prepare(self.parent, self.budget_plan, self.admin, report_id) if budget else None
        prepared = runtime.prepare(self.parent, self.admin, report_id, "固定筛查合成测试",
            "screen-runtime-result-"+str(self.serial),
            choices=[{"salesKey":"sales", "masterKey":"master"}] if mapped else None,
            budget=prepared_budget)
        return prepared.snapshot, prepared.reference, prepared_budget

    def insert_screening(self, bundle, **kwargs):
        graph = contract.graph(bundle[2] is not None)
        changes = {"graph_json":canonical(graph), "graph_digest":digest(graph)}
        changes.update(kwargs.pop("flow_changes", {}))
        return self.insert(bundle, flow_changes=changes, **kwargs)

    def deny(self, bundle, **kwargs):
        before = tuple(cls.objects.count() for cls in (m.AiReportRun, m.AiWorkflowRuns, m.AiBusinessBudgetPlan))
        with self.assertRaises(DatabaseError), mutation(self.admin):
            self.insert_screening(bundle, **kwargs)
        self.assertEqual(before, tuple(cls.objects.count() for cls in (m.AiReportRun, m.AiWorkflowRuns, m.AiBusinessBudgetPlan)))

    def test_actual_prepare_all_four_mapping_budget_combinations_and_immutable_roots(self):
        for mapped in (False, True):
            for budget in (False, True):
                with self.subTest(mapped=mapped, budget=budget):
                    bundle = self.screening_bundle(mapped=mapped, budget=budget)
                    with mutation(self.admin): report = self.insert_screening(bundle)
                    self.assertEqual(report.snapshot_json, canonical(bundle[0]))
                    self.assertEqual(report.workflow.input_json, canonical(bundle[1]))
                    actual = runtime.bound(report, self.admin)
                    self.assertEqual(actual[1], bundle[0])
                    self.assertEqual(actual[2], bundle[1])
                    self.assertEqual(bool(report.budget_plan_id), budget)
                    for cls, key, values in (
                        (m.AiReportRun, report.id, {"snapshot_json":canonical({**bundle[0],"question":"changed"})}),
                        (m.AiWorkflowRuns, report.workflow_id, {"input_json":canonical({**bundle[1],"question":"changed"})}),
                    ):
                        with self.assertRaises(DatabaseError), transaction.atomic():
                            cls.objects.filter(pk=key).update(**values)

    def test_intent_exact_fields_types_constants_and_duplicate_keys(self):
        base = self.screening_bundle()
        for key in contract.INTENT_FIELDS:
            for change in (None, True, 1.0, {}, "bad id" if key == "id" else "future"):
                bundle = deepcopy(base)
                bundle[0]["screeningIntent"][key] = change
                bundle[1]["screeningIntent"][key] = change
                with self.subTest(key=key, value=change): self.deny(bundle)
            bundle = deepcopy(base)
            del bundle[0]["screeningIntent"][key]
            del bundle[1]["screeningIntent"][key]
            self.deny(bundle)
        bundle = deepcopy(base)
        bundle[0]["screeningIntent"]["extra"] = 1
        bundle[1]["screeningIntent"]["extra"] = 1
        self.deny(bundle)
        needle = '"packagePolicy":"'+contract.PACKAGE_POLICY+'"'
        for field in ("snapshot_raw", "input_raw"):
            original = canonical(base[0 if field == "snapshot_raw" else 1])
            self.deny(base, **{field:original.replace(needle, needle+","+needle, 1)})

    def test_snapshot_and_reference_binding_mismatches_nulls_and_numeric_lexemes(self):
        base = self.screening_bundle(mapped=True)
        for key, value in (("executionProfile","future"), ("sourceCount",float(base[0]["sourceCount"])),
                ("evidenceVersion",True), ("evidenceVersion",float(self.parent.version)), ("catalogDigest","0"*64),
                ("evidencePlanDigest","0"*64), ("sealedDigest","0"*64), ("evidenceRunId","missing"),
                ("mappingPlan",None), ("mappingPlanDigest",None), ("budgetPlan",{}), ("extra",1)):
            bundle = deepcopy(base); bundle[0][key] = value
            with self.subTest(field=key, value=value): self.deny(bundle)
        for key in ("screeningIntent", "reportId", "scope", "skills", "mappingPlan", "mappingPlanDigest"):
            bundle = deepcopy(base); raw = deepcopy(bundle[0]); del raw[key]
            with self.subTest(missing=key): self.deny(bundle, snapshot_raw=canonical(raw))
        for key, value in (("reportId","other"), ("sourceCount",3.0), ("question","other"),
                ("screeningIntent",None), ("mappingRef",None), ("budgetRef",{}), ("extra",1)):
            bundle = deepcopy(base); bundle[1][key] = value
            with self.subTest(input=key): self.deny(bundle)
        bundle = deepcopy(base); bundle[1]["screeningIntent"]["id"] = "other-screening"
        self.deny(bundle)
        for value in (True, 1.0):
            bundle = deepcopy(base); bundle[1]["mappingRef"]["pairCount"] = value
            self.deny(bundle)
        raw = canonical(base[1])
        self.deny(base, input_raw=raw.replace('"pairCount":1','"pairCount":1e0',1))
        self.deny(base, input_raw=raw.replace('"pairCount":1','"pairCount":1,"pairCount":1',1))

    def test_mapping_uses_real_directory_and_budget_both_directions_are_required(self):
        base = self.screening_bundle(mapped=True)
        for sales, master in (("ads","master"),("sales","ads"),("sales","missing")):
            bundle = deepcopy(base)
            plan = bundle[0]["mappingPlan"]
            plan["pairs"] = [{"salesKey":sales,"masterKey":master,
                "pairKey":object_digest(["exact-product-partition-v1",sales,master])}]
            bundle[0]["mappingPlanDigest"] = digest(plan)
            bundle[1]["mappingRef"]["planDigest"] = digest(plan)
            self.deny(bundle)
        for changes in ({"owner_email":"other@example.invalid"},{"scope_json":"{}"}):
            self.deny(self.screening_bundle(), flow_changes=changes)
            self.deny(self.screening_bundle(), report_changes=changes)
        bundle = self.screening_bundle(budget=True)
        bundle[0].pop("budgetRef"); bundle[1].pop("budgetRef")
        self.deny(bundle)
        bundle = self.screening_bundle(budget=True)
        self.deny((bundle[0], bundle[1], None))
        bundle = self.screening_bundle(budget=True)
        for value in (None, {}, {**bundle[0]["budgetRef"],"planDigest":"0"*64}):
            changed = deepcopy(bundle)
            changed[0]["budgetRef"] = changed[1]["budgetRef"] = value
            self.deny(changed)

    def test_legacy_profiles_keep_original_bytes_and_reject_intent(self):
        for profile in ("business-agent-integrated-reference-v1", "business-agent-budget-reference-v1", "business-agent-reference-v2"):
            bundle = self.bundle(budget=profile=="business-agent-budget-reference-v1")
            bundle[0]["executionProfile"] = profile
            if profile != "business-agent-integrated-reference-v1":
                bundle[0].pop("mappingPlan"); bundle[0].pop("mappingPlanDigest"); bundle[1].pop("mappingRef")
            if profile == "business-agent-reference-v2": bundle[1].pop("reportId")
            with mutation(self.admin): report = self.insert(bundle)
            self.assertEqual(report.snapshot_json, canonical(bundle[0]))
            self.assertEqual(report.workflow.input_json, canonical(bundle[1]))
            for target in (0,1):
                bad = self.bundle(budget=profile=="business-agent-budget-reference-v1")
                bad[0]["executionProfile"] = profile
                if profile != "business-agent-integrated-reference-v1":
                    bad[0].pop("mappingPlan"); bad[0].pop("mappingPlanDigest"); bad[1].pop("mappingRef")
                bad[target]["screeningIntent"] = contract.intent("bad-old-intent", "a"*64)
                self.deny(bad)

    def test_orphan_workflow_deferred_binding_cannot_commit_and_has_no_preparation_side_effect(self):
        bundle = self.screening_bundle()
        graph = contract.graph()
        before = m.AiWorkflowRuns.objects.count()
        with self.assertRaisesRegex(DatabaseError, "screening_workflow_orphan"), mutation(self.admin):
            m.AiWorkflowRuns.objects.create(id="orphan-screening",owner_email=self.admin.email,
                scope_json="null",client_request_id="orphan-screening",request_digest="a"*64,
                name="inert",graph_json=canonical(graph),graph_digest=digest(graph),input_json=canonical(bundle[1]),dry_run=1)
        self.assertEqual(m.AiWorkflowRuns.objects.count(), before)
        self.assertEqual(m.AiBusinessScreeningRun.objects.count(), 0)

    def test_publication_requires_exact_preallocated_id_and_selection_after_real_scan(self):
        bundle = self.screening_bundle(mapped=True)
        with mutation(self.admin): report = self.insert_screening(bundle)
        verified = screening.prepare_for_report(report.id, self.admin)
        pages = storage_contract.materialize(json.loads(verified._result_json))
        intended = bundle[0]["screeningIntent"]["id"]
        def publish(raw, run_id):
            fields = storage_fixtures.row_values(raw, "ignored"); fields["id"] = run_id
            run = m.AiBusinessScreeningRun.objects.create(**fields)
            for page in raw["pages"]:
                m.AiBusinessScreeningPage.objects.create(**storage_fixtures.page_values(page, run.id))
            return run
        for changed, run_id in ((pages, "different-result"),
                (storage_fixtures.variant(pages,"wrong-selection"), intended)):
            with self.assertRaisesRegex(DatabaseError,"screening_publication_intent"), mutation(self.admin):
                publish(changed,run_id)
            self.assertEqual(m.AiBusinessScreeningRun.objects.count(),0)
            self.assertEqual(m.AiBusinessScreeningPage.objects.count(),0)
        with mutation(self.admin): saved = publish(pages,intended)
        self.assertEqual(saved.id, intended)
        self.assertEqual(saved.selection_plan_digest,bundle[0]["screeningIntent"]["selectionPlanDigest"])

    def test_reverse_restores_exact_previous_functions_but_new_report_blocks_without_publication(self):
        sql = migration(); editor = SimpleNamespace(connection=connection)
        with transaction.atomic():
            sql.uninstall(apps, editor)
            for name, definition in (("ai_business_budget_report_guard",sql.prior.NEW_BUDGET_GUARD),
                    ("ai_business_integrated_report_guard",sql.prior.REPORT_GUARD),("ai_screen_initial_guard",sql.storage.INITIAL)):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT prosrc FROM pg_proc WHERE proname=%s",[name])
                    self.assertEqual(cursor.fetchone()[0],definition.split("$$")[1])
            sql.install(apps,editor)
        bundle = self.screening_bundle()
        with mutation(self.admin): self.insert_screening(bundle)
        self.assertEqual(m.AiBusinessScreeningRun.objects.count(),0)
        with self.assertRaisesRegex(RuntimeError,"screening"): sql.uninstall(apps,editor)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_trigger WHERE tgname='ai_business_screening_workflow_binding' AND tgenabled='O'")
            self.assertEqual(cursor.fetchone()[0],1)

    def test_owning_publication_replay_and_read_recheck_preallocated_identity(self):
        bundle = self.screening_bundle(mapped=True, budget=True)
        with mutation(self.admin): report = self.insert_screening(bundle)
        verified = screening.prepare_for_report(report.id,self.admin)
        first = store.publish(verified,self.admin)
        self.assertEqual(first["reference"]["id"],bundle[0]["screeningIntent"]["id"])
        self.assertFalse(first["replayed"])
        self.assertTrue(store.publish(verified,self.admin)["replayed"])
        self.assertEqual(store.describe(first["reference"]["id"],self.admin)["reference"],first["reference"])
        # Simulate a corrupted restoration/ORM result without relaxing the SQL
        # insertion guard. Owning reads and replay must independently fail.
        from django.db.models.query import QuerySet
        original = QuerySet.first
        def wrong_identity(query):
            row = original(query)
            if type(row) is m.AiBusinessScreeningRun:
                row.id = "wrong-restored-identity"
            return row
        with patch.object(QuerySet,"first",wrong_identity):
            with self.assertRaises(AiError): store.describe(first["reference"]["id"],self.admin)
            with self.assertRaises(AiError): store.publish(verified,self.admin)
