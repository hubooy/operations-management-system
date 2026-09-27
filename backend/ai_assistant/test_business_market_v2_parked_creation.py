"""Isolated PostgreSQL parked market-v2 report root and dispatch denial."""
import json
from copy import deepcopy
from importlib import import_module
from unittest.mock import patch

from django import test as djtest
from django.db import DatabaseError, connection, transaction

from . import business_market_v2_parked_creation as service
from . import business_promotion_market_runtime_v2_contract as runtime
from . import models as m, transport
from . import test_business_promotion_market_admission as fixtures
from .policy import AiError, canonical, digest, uid


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2ParkedCreationTests(djtest.TransactionTestCase):
    user = fixtures.PromotionMarketAdmissionTests.user
    request_body = fixtures.PromotionMarketAdmissionTests.request_body
    current_catalog = fixtures.PromotionMarketAdmissionTests.current_catalog
    create_fixed_report = fixtures.PromotionMarketAdmissionTests.create_fixed_report
    planned_evidence_body = fixtures.PromotionMarketAdmissionTests.planned_evidence_body
    setUp = fixtures.PromotionMarketAdmissionTests.setUp
    selector = fixtures.PromotionMarketAdmissionTests.selector

    def body(self, **changes):
        return {"schemaVersion": service.REQUEST_SCHEMA,
            "clientRequestId": "market-v2-parked-creation",
            "sourceReportId": self.report.id,
            "marketSelector": self.selector(), **changes}

    def test_parked_report_is_exact_root_without_jobs_models_or_files(self):
        before = (m.AiReportRun.objects.count(),
            m.AiWorkflowRuns.objects.count(), m.AiAgentJobs.objects.count(),
            m.AiAgentProviderDispatches.objects.count(),
            m.AiAgentToolDispatches.objects.count(),
            m.AiBusinessFileRun.objects.count())
        source_snapshot, source_input = (self.report.snapshot_json,
            self.report.workflow.input_json)
        with patch.object(transport, "execute_tool") as remote:
            first = service.create(self.body(), self.admin)
            replay = service.create(self.body(), self.admin)
        remote.assert_not_called()
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["item"], replay["item"])
        self.assertEqual(first["item"]["workflowStatus"], "paused")
        self.assertEqual(first["item"]["pauseReason"],
            "market_material_not_admitted")
        row = m.AiReportRun.objects.select_related("workflow").get(
            pk=first["item"]["id"])
        snapshot, input_value = json.loads(row.snapshot_json), json.loads(
            row.workflow.input_json)
        self.assertEqual(snapshot["sourceRoot"]["sourceReportId"], self.report.id)
        self.assertEqual(snapshot["sourceRoot"]["evidenceRunId"],
            json.loads(source_snapshot)["evidenceRunId"])
        self.assertFalse(snapshot["marketMaterialReady"])
        self.assertFalse(snapshot["registered"])
        self.assertNotIn("manifestDigest", row.snapshot_json)
        self.assertNotIn("ndjsonSha256", row.snapshot_json)
        self.assertEqual(input_value["marketSelector"], snapshot["marketSelector"])
        self.assertEqual(row.workflow.graph_digest, digest(row.workflow.graph_json))
        self.assertEqual(row.workflow.allowed_tools_json, "[]")
        self.assertEqual(snapshot["proposedTools"], list(runtime.TOOL_ORDER))
        self.assertEqual(snapshot["roleReadPolicyDigest"],
            service.TOOL_POLICY_DIGEST)
        self.assertEqual(row.workflow.tool_policy_digest,
            service.EMPTY_TOOL_POLICY_DIGEST)
        self.assertFalse(m.AiWorkflowNodeRuns.objects.filter(run=row.workflow).exists())
        self.assertEqual((m.AiReportRun.objects.count(),
            m.AiWorkflowRuns.objects.count(), m.AiAgentJobs.objects.count(),
            m.AiAgentProviderDispatches.objects.count(),
            m.AiAgentToolDispatches.objects.count(),
            m.AiBusinessFileRun.objects.count()),
            (before[0]+1, before[1]+1, *before[2:]))
        self.report.refresh_from_db()
        self.report.workflow.refresh_from_db()
        self.assertEqual((self.report.snapshot_json,
            self.report.workflow.input_json), (source_snapshot, source_input))

    def test_profile_cannot_transition_queue_dispatch_or_change_snapshot(self):
        item = service.create(self.body(), self.admin)["item"]
        row = m.AiReportRun.objects.select_related("workflow").get(pk=item["id"])
        for status in ("queued", "running"):
            with self.subTest(status=status), self.assertRaises(DatabaseError), \
                    transaction.atomic():
                m.AiWorkflowRuns.objects.filter(pk=row.workflow_id).update(status=status)
        changed = json.loads(row.snapshot_json)
        changed["marketMaterialReady"] = True
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiReportRun.objects.filter(pk=row.pk).update(
                snapshot_json=canonical(changed))
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiAgentJobs.objects.create(id=uid("agent"),
                owner_email=self.admin.email, client_request_id=uid("client"),
                request_digest="0"*64, scope_json="null", task="forbidden",
                workflow_run_id=row.workflow_id,
                workflow_node_key="market_b2b")
        self.assertEqual(m.AiWorkflowRuns.objects.get(pk=row.workflow_id).status,
            "paused")
        self.assertFalse(m.AiAgentJobs.objects.filter(
            workflow_run_id=row.workflow_id).exists())

    def test_reverse_requires_no_parked_report(self):
        item = service.create(self.body(), self.admin)["item"]
        migration = import_module(
            "ai_assistant.migrations.0044_business_market_v2_profile")
        with connection.schema_editor() as editor:
            with self.assertRaisesRegex(RuntimeError, "不能逆迁移"):
                migration.uninstall(None, editor)
        self.assertEqual(m.AiReportRun.objects.get(pk=item["id"]).id, item["id"])

    def test_database_rejects_null_market_query_values_after_preparation(self):
        original_create = m.AiReportRun.objects.create
        baseline_key = self.selector()["rankBaselineKey"]
        source = m.AiBusinessEvidenceSource.objects.get(
            run_id=self.run_id, source_key=baseline_key)
        for field in ("window", "startDate"):
            query = json.loads(source.query_json)
            query[field] = None
            forged_query = canonical(query)

            def insert_after_source_drift(**kwargs):
                with connection.cursor() as cursor:
                    for trigger in ("ai_write_fence", "ai_immutable_identity",
                                    "ai_business_source_state"):
                        cursor.execute("ALTER TABLE public.ai_business_evidence_sources "
                            "DISABLE TRIGGER " + trigger)
                    cursor.execute("UPDATE public.ai_business_evidence_sources "
                        "SET query_json=%s,query_digest=%s WHERE id=%s",
                        [forged_query, digest(forged_query), source.id])
                return original_create(**kwargs)

            with self.subTest(field=field), patch.object(m.AiReportRun.objects,
                    "create", side_effect=insert_after_source_drift), \
                    self.assertRaisesRegex(DatabaseError,
                        "ai_market_v2_source_query_value_invalid"):
                service.create(self.body(clientRequestId="null-" + field),
                    self.admin)
        self.assertEqual(m.AiReportRun.objects.filter(
            client_request_id__startswith="null-").count(), 0)

    def test_wrong_actor_source_observation_and_duplicate_request_reject(self):
        outside = self.user("market-parked-outside@example.invalid", "admin", None)
        with self.assertRaises(AiError):
            service.create(self.body(), outside)
        with self.assertRaises(AiError):
            service.create(self.body(marketSelector=self.selector(
                rankBaselineKey="sales")), self.admin)
        with self.assertRaises(AiError):
            service.create(self.body(marketSelector=self.selector(
                currentObservationDate="2026-08-02")), self.admin)
        service.create(self.body(), self.admin)
        with self.assertRaises(AiError):
            service.create(self.body(sourceReportId="another-report"), self.admin)

    def test_database_rejects_forged_initial_root_selector_and_derived_material(self):
        original = service._shape
        cases = (
            ("source", lambda snapshot, input_value:
                snapshot["sourceRoot"].update(sourceSnapshotDigest="0"*64)),
            ("selector", lambda snapshot, input_value:
                snapshot["marketSelector"].update(rankBaselineKey="sales")),
            ("material", lambda snapshot, input_value:
                snapshot.update(marketManifestDigest="0"*64)),
        )
        for name, corrupt in cases:
            def forged(*args, **kwargs):
                snapshot, input_value = original(*args, **kwargs)
                corrupt(snapshot, input_value)
                for key in ("sourceRoot", "marketSelector"):
                    input_value[key] = deepcopy(snapshot[key])
                if "marketManifestDigest" in snapshot:
                    input_value["marketManifestDigest"] = snapshot["marketManifestDigest"]
                return snapshot, input_value
            expected_error = AiError if name == "source" else DatabaseError
            with self.subTest(name=name), patch.object(service, "_shape",
                    side_effect=forged), self.assertRaises(expected_error):
                service.create(self.body(clientRequestId="forged-"+name), self.admin)
        self.assertEqual(m.AiReportRun.objects.filter(
            client_request_id__startswith="forged-").count(), 0)

    def test_database_rejects_initial_wrong_graph_and_tool_catalog(self):
        original = m.AiWorkflowRuns.objects.create
        for name, replacement in (("graph", {"graph_json": "{}"}),
                                  ("tools", {"allowed_tools_json":
                                      '["get_business_promotion_market_v2"]'})):
            def forged(**kwargs):
                return original(**{**kwargs, **replacement})
            with self.subTest(name=name), patch.object(m.AiWorkflowRuns.objects,
                    "create", side_effect=forged), self.assertRaises(DatabaseError):
                service.create(self.body(clientRequestId="forged-flow-"+name),
                    self.admin)
        self.assertFalse(m.AiReportRun.objects.filter(
            client_request_id__startswith="forged-flow-").exists())
