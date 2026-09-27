"""Real owning-reader pages, v1 byte parity and v2 internal budget resolution."""
from copy import deepcopy
import json
from unittest.mock import patch
from urllib.parse import urlencode

from django.db import connection
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from netshop.analysis import read_page, validate_request

from . import business_budget, business_evidence as evidence, business_reports, models as m
from .business_sealed import Reader
from . import test_business_budget as budget_fixtures, test_business_evidence as evidence_fixtures
from .policy import AiError, canonical, digest


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessBudgetV2Tests(TestCase):
    user = evidence_fixtures.BusinessEvidenceTests.user
    call = evidence_fixtures.BusinessEvidenceTests.call

    def setUp(self):
        budget_fixtures.BusinessBudgetTests.setUp(self)
        self.legacy_id = self.evidence_id
        sources = json.loads(evidence.get_run(self.legacy_id, self.admin).plan_json)["sources"]
        self.v2_id = evidence.create({"schemaVersion": "business-evidence-v2", "clientRequestId": "budget-evidence-v2",
            "sources": sources, "collectionMode": "bulk"}, self.admin)["item"]["id"]
        tools = [self.catalog[0], {**self.catalog[0], "name": "get_business_source_page"}]
        def execute(name, args, principal, **kwargs):
            values = {k: v for k, v in args.items() if k != "domain"}
            data = {"dataCutoffDate": "2026-08-01"} if name == "get_data_freshness" else read_page(*validate_request(QueryDict(urlencode(values))))
            return {"ok": True, "toolName": name, "auditStatus": "recorded", "data": data}
        with patch("ai_assistant.transport.catalog", return_value=tools), patch("ai_assistant.transport.execute_tool", side_effect=execute):
            evidence.collect(self.v2_id, {"sourceKey": "ads", "expectedVersion": 1}, self.admin, "budget-v2-source")
        evidence.finish(self.v2_id, {"expectedVersion": 2, "action": "seal"}, self.admin)
        rows = evidence.analysis_table(self.v2_id, {"sourceKey": "ads", "dimension": "sku"}, self.admin)["rows"]
        self.v2_plan = deepcopy(self.plan)
        for target, row in zip(self.v2_plan["targets"], rows):
            target.update(rowId=row["id"], rowIndex=row["rowIndex"])

    def test_resolve_v2_uses_only_sealed_records_and_preserves_arithmetic(self):
        with patch("ai_assistant.transport.execute_tool") as source, patch("ai_assistant.provider.turn") as model, CaptureQueriesContext(connection) as queries:
            result = business_budget.resolve(self.v2_id, self.v2_plan, self.admin)
        source.assert_not_called(); model.assert_not_called()
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertEqual(result["scenarios"][0]["summary"]["projectedAttributedGmvCents"], 45000)
        self.assertEqual(result["allocation"]["allocatedCents"], 9000)
        row = evidence.get_run(self.v2_id, self.admin)
        self.assertEqual((result["evidenceRunId"], result["evidenceVersion"], result["evidencePlanDigest"]), (row.id, row.version, digest(row.plan_json)))
        self.assertEqual(result["allocation"], business_budget.resolve(self.legacy_id, self.plan, self.admin)["allocation"])

    def test_legacy_budget_entire_canonical_output_is_unchanged(self):
        class LegacyReader:
            """Exact pre-adapter projections, used as a byte-parity oracle."""
            def __init__(self, row, principal):
                self.row = row
                self.sources = json.loads(row.plan_json)["sources"]
                self.state = json.loads(row.state_json)
            def info(self, key):
                return {"expected": evidence._restore(self.state[key]["verifier"]).result()}
            def pages(self, key):
                for chunk in m.AiBusinessEvidenceChunk.objects.filter(run=self.row, source_key=key).order_by("sequence").iterator(chunk_size=10):
                    yield json.loads(chunk.payload_json)
        before = evidence.get_run(self.legacy_id, self.admin)
        original = (before.plan_json, before.state_json, before.request_digest)
        with patch.object(business_budget, "Reader", LegacyReader):
            expected = business_budget.resolve(self.legacy_id, self.plan, self.admin)
        actual = business_budget.resolve(self.legacy_id, self.plan, self.admin)
        self.assertEqual(canonical(actual), canonical(expected))
        before.refresh_from_db()
        self.assertEqual((before.plan_json, before.state_json, before.request_digest), original)

    def test_v2_wrong_reference_owner_scope_rejected_and_valid_budget_admitted(self):
        for principal in (self.viewer, self.user("budget-v2-other@example.invalid", "admin", None)):
            with self.assertRaises(AiError): business_budget.resolve(self.v2_id, self.v2_plan, principal)
        wrong = deepcopy(self.v2_plan)
        wrong["targets"][0]["rowId"] = "0"*64
        with self.assertRaises(AiError): business_budget.resolve(self.v2_id, wrong, self.admin)
        wrong = deepcopy(self.v2_plan)
        wrong["targets"][0]["sourceKey"] = "missing"
        with self.assertRaises(AiError): business_budget.resolve(self.v2_id, wrong, self.admin)
        count = m.AiWorkflowRuns.objects.count()
        with patch("ai_assistant.transport.catalog") as catalog, patch("ai_assistant.provider.turn") as provider:
            item = business_reports.create({"clientRequestId": "v2-budget-admitted", "evidenceRunId": self.v2_id,
                "question": "预算", "dryRun": True, "budgetPlan": self.v2_plan}, self.admin)["item"]
        catalog.assert_not_called(); provider.assert_not_called()
        self.assertEqual(m.AiWorkflowRuns.objects.count(), count+1)
        self.assertTrue(m.AiReportRun.objects.get(pk=item["id"]).budget_plan_id)


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessSealedRealPagesTests(TestCase):
    user = evidence_fixtures.BusinessEvidenceTests.user
    call = evidence_fixtures.BusinessEvidenceTests.call
    setUp = evidence_fixtures.BusinessEvidenceTests.setUp
    execute = evidence_fixtures.BusinessEvidenceTests.execute
    collect = evidence_fixtures.BusinessEvidenceTests.collect

    def test_standard_ten_row_pages_and_first_tail_metadata_match_exactly(self):
        run_id = evidence.create(self.body, self.admin)["item"]["id"]
        self.collect(run_id, 1); self.collect(run_id, 2)
        evidence.finish(run_id, {"expectedVersion": 3, "action": "seal"}, self.admin)
        row = evidence.get_run(run_id, self.admin)
        chunks = list(m.AiBusinessEvidenceChunk.objects.filter(run=row).order_by("sequence"))
        with patch("ai_assistant.transport.execute_tool") as remote:
            reader = Reader(row, self.admin)
            pages = list(reader.pages("sales"))
        remote.assert_not_called()
        self.assertEqual([len(page["items"]) for page in pages], [10, 2])
        self.assertEqual([canonical(page) for page in pages], [chunk.payload_json for chunk in chunks])
        self.assertIsNotNone(pages[0]["coverage"])
        self.assertIsNone(pages[1]["coverage"])
        self.assertEqual(reader.info("sales")["expected"]["rowCount"], 12)
        self.assertEqual(reader.info("sales")["metadata"], json.loads(row.state_json)["sales"]["metadata"])
