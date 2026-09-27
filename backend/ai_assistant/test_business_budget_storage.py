"""Actual PG constraints, deferred ownership and serialized storage quotas.

No Agent profile is enabled. Oversized JSON fixtures test storage byte bounds,
not semantic acceptance by budget.normalize or model-context admission.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from importlib import import_module
import json
from threading import Event
import time
from unittest.mock import patch
from urllib.parse import urlencode

from django.apps import apps
from django.db import DatabaseError, connection, connections, transaction
from django.db.models.deletion import ProtectedError
from django.http import QueryDict
from django.test import TransactionTestCase, override_settings
from netshop.analysis import read_page, validate_request
from netshop.models import NetshopDataRevision

from business_analysis import budget_reference as contract
from . import business_evidence as evidence, models as m
from . import test_business_budget_v2 as fixtures
from .database_contract import MODELS, READ_TABLES, WRITER_PRIVILEGES
from .policy import canonical, digest
from .table_manifest import AI_TABLES


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessBudgetStorageTests(TransactionTestCase):
    user = fixtures.BusinessBudgetV2Tests.user
    call = fixtures.BusinessBudgetV2Tests.call

    def setUp(self):
        NetshopDataRevision.objects.get_or_create(domain="netshop", defaults={"revision": 0, "source_digest": "a"*64})
        fixtures.BusinessBudgetV2Tests.setUp(self)
        self.parent = m.AiBusinessEvidenceRun.objects.get(pk=self.v2_id)
        self.serial = 0

    def values(self, number, *, parent=None, owner=None, plan_json=None):
        parent = parent or self.parent
        owner = owner or parent.owner_email
        header, seal = json.loads(parent.plan_json), json.loads(parent.state_json)
        binding = contract.make_binding(self.v2_plan, report_id="stored-report-"+str(number), owner_email=owner, scope=None,
            evidence_run_id=parent.id, evidence_version=parent.version, evidence_plan_digest=digest(parent.plan_json),
            catalog_digest=header["catalogDigest"], sealed_digest=seal["sealedDigest"], analysis_request=header.get("analysisRequest"))
        raw = canonical(self.v2_plan) if plan_json is None else plan_json
        binding["planDigest"] = digest(raw)
        return {"id": "stored-plan-"+str(number), "owner_email": owner, "scope_json": "null", "evidence_id": parent.id,
                "evidence_version": parent.version, "plan_json": raw, "plan_digest": digest(raw),
                "binding_json": canonical(binding), "binding_digest": digest(binding)}

    def report_values(self, values):
        binding = json.loads(values["binding_json"])
        snapshot = {"schemaVersion": "business-report-v1", "executionProfile": contract.PROFILE,
            "evidenceProtocol": "reference-v2", "reportId": binding["reportId"],
            "budgetRef": {"schemaVersion": contract.REFERENCE_SCHEMA, "id": values["id"],
                          "planDigest": values["plan_digest"], "bindingDigest": values["binding_digest"]},
            **{key: binding[key] for key in ("evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest")},
            "sourceCount": 1, "question": "仅合成存储测试，不调用模型"}
        return {"id": binding["reportId"], "owner_email": values["owner_email"], "scope_json": values["scope_json"],
                "client_request_id": binding["reportId"], "request_digest": digest(snapshot),
                "snapshot_json": canonical(snapshot), "budget_plan_id": values["id"]}

    def insert(self, values, *, report_changes=None, snapshot_changes=None):
        budget = m.AiBusinessBudgetPlan.objects.create(**values)
        report = self.report_values(values)
        if snapshot_changes:
            snapshot = json.loads(report["snapshot_json"]); snapshot.update(snapshot_changes)
            report["snapshot_json"] = canonical(snapshot)
        if report_changes:
            report.update(report_changes)
        flow = m.AiWorkflowRuns.objects.create(id="workflow-"+values["id"], owner_email=values["owner_email"],
            client_request_id="workflow-"+values["id"], request_digest="a"*64, scope_json=values["scope_json"],
            name="合成存储", graph_json='{"nodes":[]}', graph_digest="a"*64, input_json="{}", dry_run=1)
        report["workflow_id"] = flow.id
        return budget, m.AiReportRun.objects.create(**report)

    def seed(self, number, **kwargs):
        with transaction.atomic():
            return self.insert(self.values(number, **kwargs))

    def rebind(self, values, **changes):
        result = deepcopy(values); binding = json.loads(result["binding_json"]); binding.update(changes)
        result.update(binding_json=canonical(binding), binding_digest=digest(binding))
        return result

    def plan_size(self, size):
        plan = deepcopy(self.v2_plan)
        plan["targets"][0]["ownerRole"] = ""
        raw = canonical(plan)
        self.assertGreaterEqual(size, len(raw.encode()))
        # Chinese and escaped text exercise byte storage, not Python len().
        padding = size-len(raw.encode())
        plan["targets"][0]["ownerRole"] = "中"*(padding//3)+"x"*(padding%3)
        result = canonical(plan)
        self.assertEqual(len(result.encode()), size)
        return result

    def second_parent(self, index):
        principal = self.user(f"quota-{index}@example.invalid", "admin", None)
        sources = json.loads(self.parent.plan_json)
        original = list(m.AiBusinessEvidenceSource.objects.filter(run=self.parent).order_by("ordinal"))
        request = {"schemaVersion": "business-evidence-v2", "clientRequestId": "quota-"+str(index), "collectionMode": "bulk",
                   "sources": [{"key": s.source_key, "domain": s.domain, "query": json.loads(s.query_json)} for s in original]}
        if "analysisRequest" in sources:
            request["analysisRequest"] = sources["analysisRequest"]
        run = evidence.create(request, principal)["item"]
        tools = [self.catalog[0], {**self.catalog[0], "name": "get_business_source_page"}]
        def execute(name, args, principal, **kwargs):
            query = {k: v for k, v in args.items() if k != "domain"}
            data = {"dataCutoffDate": "2026-08-01"} if name == "get_data_freshness" else read_page(*validate_request(QueryDict(urlencode(query))))
            return {"ok": True, "toolName": name, "auditStatus": "recorded", "data": data}
        with patch("ai_assistant.transport.catalog", return_value=tools), patch("ai_assistant.transport.execute_tool", side_effect=execute):
            evidence.collect(run["id"], {"sourceKey": "ads", "expectedVersion": 1}, principal, "quota-fixture")
        evidence.finish(run["id"], {"expectedVersion": 2, "action": "seal"}, principal)
        return m.AiBusinessEvidenceRun.objects.get(pk=run["id"])

    def test_exact_reference_commits_and_records_are_insert_only(self):
        budget, report = self.seed(1)
        self.assertEqual(report.budget_plan_id, budget.id)
        self.assertIn("ai_business_budget_plans", AI_TABLES)
        self.assertIn("ai_business_budget_plans", READ_TABLES)
        self.assertIs(MODELS["ai_business_budget_plans"], m.AiBusinessBudgetPlan)
        self.assertEqual(WRITER_PRIVILEGES["ai_business_budget_plans"], ("SELECT", "INSERT"))
        for operation in (lambda: m.AiBusinessBudgetPlan.objects.filter(pk=budget.id).update(plan_json=budget.plan_json),
                          lambda: m.AiBusinessBudgetPlan.objects.filter(pk=budget.id).delete(),
                          lambda: m.AiReportRun.objects.filter(pk=report.id).update(budget_plan_id=None)):
            with self.assertRaises((DatabaseError, ProtectedError)), transaction.atomic():
                operation()

    def test_orphan_fails_at_commit_and_late_failure_rolls_back_everything(self):
        with self.assertRaisesRegex(DatabaseError, "orphan"), transaction.atomic():
            m.AiBusinessBudgetPlan.objects.create(**self.values(1))
        self.assertEqual(m.AiBusinessBudgetPlan.objects.count(), 0)
        with self.assertRaisesRegex(RuntimeError, "audit"), transaction.atomic():
            self.insert(self.values(2)); raise RuntimeError("synthetic audit failure")
        self.assertEqual(m.AiBusinessBudgetPlan.objects.count(), 0)
        self.assertFalse(m.AiReportRun.objects.exists())
        self.assertFalse(m.AiWorkflowRuns.objects.filter(id="workflow-stored-plan-2").exists())

    def test_binding_types_owner_evidence_digest_and_profile_fail_closed(self):
        for index, changes in enumerate(({"ownerEmail": "other@example.invalid"}, {"scopeDigest": "a"*64},
                {"evidenceVersion": True}, {"evidenceVersion": 3.0}, {"catalogDigest": "a"*64},
                {"sealedDigest": "a"*64}, {"evidencePlanDigest": "a"*64}, {"analysisRequestDigest": "a"*64},
                {"calculatorVersion": "future"}, {"extra": 1})):
            with self.subTest(changes=changes), self.assertRaises(DatabaseError), transaction.atomic():
                self.insert(self.rebind(self.values(index), **changes))
        for index, changes in enumerate(({"owner_email": self.admin.email.upper()}, {"scope_json": "{}"},
                                        {"evidence_version": self.parent.version+1}, {"plan_digest": "c"*64})):
            values = self.values(index); values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(DatabaseError), transaction.atomic():
                self.insert(values)
        for index, changes in enumerate(({"executionProfile": "business-agent-reference-v2"}, {"budgetRef": None},
                {"reportId": "another"}, {"budgetPlan": {}}, {"evidenceVersion": True}, {"evidenceVersion": 3.0},
                {"evidenceRunId": "another"}, {"sealedDigest": "a"*64})):
            with self.subTest(changes=changes), self.assertRaises(DatabaseError), transaction.atomic():
                self.insert(self.values(index), snapshot_changes=changes)
        self.assertEqual(m.AiBusinessBudgetPlan.objects.count(), 0)

    def test_missing_targets_and_byte_bounds_with_resigned_digests(self):
        for index, raw in enumerate(("{}", '{"targets":null}', '{"targets":[]}', '{"targets":{}}', canonical({"targets": [{}]*101}))):
            with self.subTest(raw=raw[:30]), self.assertRaises(DatabaseError), transaction.atomic():
                self.insert(self.values(index, plan_json=raw))
        self.seed("exact-bytes", plan_json=self.plan_size(48000))
        with self.assertRaises(DatabaseError), transaction.atomic():
            self.insert(self.values("over-bytes", plan_json=self.plan_size(48001)))
        value = self.values("binding-limit")
        # Whitespace keeps the JSON semantic binding valid, while raw SHA and
        # actual byte bounds still have to match exactly.
        value["binding_json"] += " "*(4096-len(value["binding_json"].encode()))
        value["binding_digest"] = digest(value["binding_json"])
        with transaction.atomic():
            self.insert(value)
        value = self.values("binding-over")
        value["binding_json"] += " "*(4097-len(value["binding_json"].encode()))
        value["binding_digest"] = digest(value["binding_json"])
        with self.assertRaises(DatabaseError), transaction.atomic():
            self.insert(value)

    def test_owner_count_includes_same_transaction_and_historical_reports(self):
        with transaction.atomic():
            for i in range(200):
                self.insert(self.values(i))
        with self.assertRaisesRegex(DatabaseError, "quota"), transaction.atomic():
            self.insert(self.values(201))
        self.assertEqual(m.AiBusinessBudgetPlan.objects.count(), 200)

    def fill_bytes(self, prefix, limit, parent):
        remaining, index = limit, 0
        with transaction.atomic():
            while remaining:
                key = f"{prefix}-{index}"
                base = self.values(key, parent=parent)
                binding_bytes = len(base["binding_json"].encode())
                if remaining <= 48000+binding_bytes:
                    plan_bytes = remaining-binding_bytes
                else:
                    following = self.values(f"{prefix}-{index+1}", parent=parent)
                    minimum_next = len(following["binding_json"].encode())+len(following["plan_json"].encode())
                    # Reserve a valid last record instead of leaving a tiny
                    # remainder below the minimum JSON structure size.
                    plan_bytes = min(48000, remaining-binding_bytes-minimum_next)
                value = self.values(key, parent=parent, plan_json=self.plan_size(plan_bytes))
                actual = len(value["plan_json"].encode())+len(value["binding_json"].encode())
                self.insert(value)
                remaining -= actual
                index += 1
        self.assertLessEqual(index, 200)
        return index

    def test_owner_exact_byte_quota_includes_binding_and_next_record_refused(self):
        limit = 8*1024*1024
        self.fill_bytes("owner-bytes", limit, self.parent)
        with connection.cursor() as cursor:
            cursor.execute("SELECT sum(octet_length(plan_json)+octet_length(binding_json)) FROM ai_business_budget_plans")
            self.assertEqual(cursor.fetchone()[0], limit)
        with self.assertRaisesRegex(DatabaseError, "quota"), transaction.atomic():
            self.insert(self.values("owner-byte-over"))

    def test_global_exact_byte_quota_crosses_owners_without_patching_limits(self):
        parents = [self.parent]+[self.second_parent(i) for i in range(1, 9)]
        for index, parent in enumerate(parents[:8]):
            self.fill_bytes(f"global-bytes-{index}", 8*1024*1024, parent)
        with connection.cursor() as cursor:
            cursor.execute("SELECT sum(octet_length(plan_json)+octet_length(binding_json)) FROM ai_business_budget_plans")
            self.assertEqual(cursor.fetchone()[0], 64*1024*1024)
        # This new owner has zero usage: rejection must be the global bound.
        self.assertFalse(m.AiBusinessBudgetPlan.objects.filter(owner_email=parents[-1].owner_email).exists())
        with self.assertRaisesRegex(DatabaseError, "quota"), transaction.atomic():
            self.insert(self.values("global-byte-over", parent=parents[-1]))

    def test_global_row_quota_crosses_owners(self):
        parents = [self.parent]+[self.second_parent(i) for i in range(1, 11)]
        with transaction.atomic():
            for i in range(2000):
                self.insert(self.values(i, parent=parents[i//200]))
        with self.assertRaisesRegex(DatabaseError, "quota"), transaction.atomic():
            self.insert(self.values(2000, parent=parents[-1]))

    def test_lock_wait_uses_committed_quota_not_outer_insert_snapshot(self):
        with transaction.atomic():
            for i in range(199):
                self.insert(self.values(i))
        started, pid = Event(), []
        loser = self.values("loser")
        def concurrent_insert():
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid.append(cursor.fetchone()[0])
                started.set()
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL statement_timeout=\'5s\'")
                        cursor.execute("SET LOCAL lock_timeout=\'5s\'")
                    self.insert(loser)
                return "unexpected-success"
            except DatabaseError as error:
                return str(error)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                self.insert(self.values("winner"))
                future = pool.submit(concurrent_insert)
                self.assertTrue(started.wait(5))
                deadline, observed = time.monotonic()+5, False
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", [pid[0]])
                        row = cursor.fetchone()
                    if row == ("Lock",):
                        observed = True; break
                    time.sleep(0.01)
                self.assertTrue(observed, "second connection never blocked on the real revision lock")
            self.assertIn("quota", future.result(timeout=10))
        self.assertEqual(m.AiBusinessBudgetPlan.objects.count(), 200)

    def test_repeatable_read_cannot_use_an_old_quota_snapshot(self):
        with self.assertRaisesRegex(DatabaseError, "isolation"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            self.insert(self.values(1))

    def test_reverse_gate_refuses_before_dropping_anything(self):
        self.seed(1)
        migration = import_module("ai_assistant.migrations.0021_business_budget_plans")
        with self.assertRaisesRegex(RuntimeError, "预算"), connection.schema_editor() as editor:
            migration.uninstall(apps, editor)
        self.assertEqual(m.AiBusinessBudgetPlan.objects.count(), 1)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_trigger WHERE tgname='ai_business_budget_complete'")
            self.assertEqual(cursor.fetchone()[0], 2)
