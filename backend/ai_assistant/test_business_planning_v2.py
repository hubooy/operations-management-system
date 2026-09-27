"""Real-API integration tests; run only in the coordinator's isolated PG suite."""
from copy import deepcopy
import json
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from business_analysis import evidence_v2
from business_analysis.contracts import canonical as contract_canonical
from business_analysis.test_planning_v2 import complete_request, sources_request
from . import business_evidence as evidence, business_planning, models as m, tests as fixtures
from .control_models import AiMutationAudit, AiWriteReceipt
from .policy import AiError, canonical, digest


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessPlanningV2Tests(TestCase):
    user = fixtures.AiDomainTests.user
    call = fixtures.AiDomainTests.call

    def setUp(self):
        fixtures.AiDomainTests.setUp(self)
        self.admin = self.user("planner-v2@example.invalid", "admin", None)
        self.other_admin = self.user("planner-v2-other@example.invalid", "admin", None)

    def test_preview_is_reader_only_principal_bound_and_never_reads_facts(self):
        counts = (m.AiBusinessEvidenceRun.objects.count(), m.AiWorkflowRuns.objects.count(), AiWriteReceipt.objects.count(), AiMutationAudit.objects.count())
        # The actual market adapter may reject the synthetic label. Permission
        # and no-I/O checks do not depend on that precise support combination.
        with patch("ai_assistant.transport.execute_tool") as tools, patch("ai_assistant.provider.turn") as model, CaptureQueriesContext(connection) as queries:
            with override_settings(DJANGO_PROCESS_ROLE="ai_reader"), patch("ai_assistant.views.authority"):
                response = self.call("/api/ai/business-plan/preview", complete_request(), self.admin)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.json()["schemaVersion"], "business-plan-preview-v2")
        self.assertEqual(response.json()["principalKey"], evidence.principal_key(self.admin))
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertFalse(any(any(table in q["sql"].lower() for table in ("netshop_rows", "sales_lines", "market_ranking_entries")) for q in queries))
        tools.assert_not_called(); model.assert_not_called()
        self.assertEqual(counts, (m.AiBusinessEvidenceRun.objects.count(), m.AiWorkflowRuns.objects.count(), AiWriteReceipt.objects.count(), AiMutationAudit.objects.count()))
        for principal in (self.owner, self.viewer):
            self.assertEqual(self.call("/api/ai/business-plan/preview", complete_request(), principal).status_code, 403)
        with override_settings(DJANGO_PROCESS_ROLE="ai_writer"), patch("ai_assistant.views.authority"):
            self.assertEqual(self.call("/api/ai/business-plan/preview", complete_request(), self.admin).status_code, 403)

    def test_preview_nineteen_sources_exact_persisted_header_and_idempotent_identity(self):
        # Patch only the owning-reader combination validator; catalog and actual
        # v2 creation/storage remain real. No source pages or provider calls.
        with patch("market.analysis.validate"):
            value = business_planning.preview(complete_request(), self.admin)
            self.assertTrue(value["canCollect"])
            body = {**value["evidenceRequest"], "clientRequestId": "planned-v2", "expectedPrincipalKey": value["principalKey"]}
            result = evidence.create(body, self.admin)
            repeated = evidence.create(body, self.admin)
        row = m.AiBusinessEvidenceRun.objects.get(pk=result["item"]["id"])
        self.assertEqual(row.plan_json, canonical(value["plan"]))
        self.assertEqual(digest(row.plan_json), value["planDigest"])
        self.assertEqual(json.loads(row.plan_json)["catalogDigest"], value["catalogDigest"])
        self.assertEqual(len(row.plan_json.encode()), value["capacity"]["planBytes"])
        self.assertEqual(m.AiBusinessEvidenceSource.objects.filter(run=row).count(), 19)
        self.assertTrue(repeated["replayed"])
        self.assertEqual(result["item"]["id"], repeated["item"]["id"])
        self.assertNotIn("expectedPrincipalKey", json.loads(row.plan_json))
        self.assertEqual(result["item"]["analysisRequestMeaning"], "requested_only_not_source_availability_or_dimension_coverage")
        reference = evidence_v2.workflow_reference(body["sources"], run_id=row.id, evidence_version=row.version, sealed_digest="a"*64,
            question=complete_request()["question"], analysis_request=body["analysisRequest"])
        self.assertLessEqual(len(contract_canonical(reference).encode()), value["capacity"]["workflowBytes"])

    def test_explicit_v2_48_then_49_and_unknown_schema(self):
        allowed = business_planning.preview(sources_request(48), self.admin)
        self.assertTrue(allowed["canCollect"])
        blocked = business_planning.preview(sources_request(49), self.admin)
        self.assertFalse(blocked["canCollect"])
        self.assertEqual(len(blocked["evidenceRequest"]["sources"]), 49)
        self.assertIsNone(blocked["planDigest"])
        for schema in (None, "business-plan-request-v1", "business-evidence-v2", True):
            self.assertEqual(self.call("/api/ai/business-plan/preview", {**complete_request(), "schemaVersion": schema}, self.admin).status_code, 400)

    def test_owning_reader_support_rejection_keeps_full_matrix(self):
        with patch("netshop.analysis.SOURCES", {}):
            result = business_planning.preview(complete_request(), self.admin)
        self.assertFalse(result["canCollect"])
        self.assertEqual(len(result["coverage"]), 19)
        self.assertIsNone(result["plan"])

    def test_account_change_and_forged_metadata_refuse_without_partial_creation(self):
        value = business_planning.preview(sources_request(48), self.admin)
        body = {**value["evidenceRequest"], "clientRequestId": "bound-v2", "expectedPrincipalKey": value["principalKey"]}
        response = self.call("/api/ai/business-evidence", body, self.other_admin)
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(m.AiBusinessEvidenceRun.objects.count(), 0)
        for mutate in (lambda b: b["analysisRequest"].update(requestedWindows=["current"]),
                       lambda b: b["sources"].pop(),
                       lambda b: b["sources"][0]["query"].update(startDate="2024-02-02")):
            bad = deepcopy(body); mutate(bad)
            with self.assertRaises(AiError):
                evidence.create(bad, self.admin)
            self.assertEqual(m.AiBusinessEvidenceRun.objects.count(), 0)

    def test_same_request_key_cannot_switch_legacy_to_v2(self):
        body = complete_request(); body["shops"][0]["datasets"] = ["promotion", "master"]; body["shops"][0]["salesChannels"] = []; body["markets"] = []
        legacy_body = {k: v for k, v in body.items() if k != "schemaVersion"}
        old = business_planning.preview(legacy_body, self.admin)
        newer = business_planning.preview(body, self.admin)
        result = evidence.create({**old["evidenceRequest"], "clientRequestId": "same-key"}, self.admin)
        with self.assertRaises(AiError):
            evidence.create({**newer["evidenceRequest"], "clientRequestId": "same-key"}, self.admin)
        self.assertEqual(m.AiBusinessEvidenceRun.objects.count(), 1)
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=result["item"]["id"]).plan_json,
                         canonical(result["item"]["plan"]))
