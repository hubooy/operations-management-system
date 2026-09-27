"""Sealed two-target budget -> renderer-4 files, without live model calls.

Report prose is a synthetic completed-node fixture, explicitly patched at the
content boundary. These tests do not claim to prove the five-Agent receipts;
the runtime acceptance suite owns that independent gate.
"""
import base64
from copy import deepcopy
import hashlib
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from unittest.mock import patch

from django.test import TestCase, override_settings
from netshop.models import NetshopDataRevision
from business_analysis import budget_reference, volume_delivery
from business_analysis.report_files import NS
from business_analysis.test_report_files import ReportData
from business_analysis.volume_files import VolumeStreams
from . import business_budget, business_budget_store as store, business_evidence as evidence
from . import business_evidence_store, business_export as export, business_files as files
from . import business_reports, business_volume_files as volumes, models as m
from . import test_business_budget_v2 as fixtures
from .policy import AiError, canonical, digest, mutation


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessBudgetVolumeExportTests(TestCase):
    user = fixtures.BusinessBudgetV2Tests.user
    call = fixtures.BusinessBudgetV2Tests.call

    def setUp(self):
        NetshopDataRevision.objects.get_or_create(domain="netshop", defaults={"revision": 0, "source_digest": "a"*64})
        fixtures.BusinessBudgetV2Tests.setUp(self)
        parent = evidence.get_run(self.v2_id, self.admin)
        self.prepared = store.prepare(parent, self.v2_plan, self.admin, "budget-volume-report")
        bound = self.prepared.binding
        snapshot = {"schemaVersion": business_reports.SCHEMA, "executionMode": "parallel-v1",
            "executionProfile": budget_reference.PROFILE, "evidenceProtocol": "reference-v2",
            "reportId": "budget-volume-report", "budgetRef": self.prepared.reference,
            **{key: bound[key] for key in ("evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest")},
            "sourceCount": len(business_evidence_store.catalog(parent)), "question": "合成固定预算文件验收",
            "scope": {"platform": "京东", "shop": "合成店", "startDate": "2026-08-01", "endDate": "2026-08-01"}}
        with mutation(self.admin):
            saved = store.insert(self.prepared, self.admin)
            flow = m.AiWorkflowRuns.objects.create(id="budget-volume-flow", owner_email=self.admin.email,
                scope_json="null", client_request_id="budget-volume-flow", request_digest=digest(snapshot),
                name="合成已分析预算", graph_json="{}", graph_digest=digest({}), input_json="{}", dry_run=0,
                status="waiting_review")
            self.report = m.AiReportRun.objects.create(id=snapshot["reportId"], owner_email=self.admin.email,
                scope_json="null", client_request_id="budget-volume-report", request_digest=digest(snapshot),
                snapshot_json=canonical(snapshot), workflow=flow, budget_plan=saved)
            for position, key in enumerate(("commerce", "promotion", "market_b2b", "independent_review", "report")):
                m.AiWorkflowNodeRuns.objects.create(id="budget-node-"+key, run=flow, node_key=key,
                    position=position, node_type="agent", instruction="仅合成文件夹具", status="completed", output_json="{}")
        self.content = {"sections": [{"title": "合成预算", "body": "情景不是实际收益，所有文件均为待复核草稿。"}],
            "diagnosis": {"findings": [{"id": "gap", "kind": "gap", "title": "业务验证待完成", "explanation": "仅合成验收", "facts": []}]},
            "budget": business_budget.for_report(self.report, self.admin)}

    def start(self):
        with patch("ai_assistant.business_reports.content", side_effect=self.verified_content):
            return files.create(self.report.id, {"deliveryMode": "volumes", "draft": True,
                "expectedPrincipalKey": evidence.principal_key(self.admin)}, self.admin)["item"]["id"]

    def verified_content(self, report, principal):
        return {**self.content, "budget": business_budget.for_report(report, principal)}

    def read_file(self, run_id, descriptor):
        return b"".join(base64.b64decode(volumes.chunk(run_id, str(descriptor["volumeIndex"]), descriptor["format"],
            {"sequence": str(sequence)}, self.admin)["base64"]) for sequence in range(1, descriptor["chunkCount"]+1))

    def test_two_volume_preparation_preserves_budget_only_in_first_volume(self):
        # A smaller deterministic sheet policy forces two volumes for the small
        # fixture; persistent production delivery keeps its unchanged 120 cap.
        with patch("ai_assistant.business_reports.content", side_effect=self.verified_content), \
                patch("ai_assistant.transport.execute_tool") as remote, patch("ai_assistant.provider.turn") as model:
            with export.prepare_volumes(self.report, self.admin, draft=True, max_tables=15) as prepared:
                self.assertEqual(prepared.plan["volumeCount"], 2)
                self.assertEqual(prepared.metadata["budgetRef"], self.prepared.reference)
                outputs = [VolumeStreams(io.BytesIO(), io.BytesIO()) for _ in range(2)]
                full = export.build_volumes(prepared, outputs)
            remote.assert_not_called(); model.assert_not_called()
        self.assertEqual(full["budgetPlanDigest"], self.prepared.reference["planDigest"])
        self.assertEqual([v["nativeBudgetSheets"] for v in full["volumes"]], [3, 0])
        combined = []
        for index, output in enumerate(outputs):
            html = output.html.getvalue().decode()
            self.assertEqual('id="budget-data"' in html, index == 0)
            data = ReportData(html).value
            self.assertEqual(data["metadata"]["budgetRef"], self.prepared.reference)
            combined.extend(data["tables"])
            with zipfile.ZipFile(output.xlsx) as archive:
                self.assertIsNone(archive.testzip())
                manifest = json.loads(archive.read("teruisi-manifest.json"))
                self.assertEqual("budgetCalculator" in manifest, index == 0)
                if index == 0:
                    self.assertEqual(manifest["budgetCalculator"]["planDigest"], full["budgetPlanDigest"])
                    sheets = [name for name in archive.namelist() if name.startswith("xl/worksheets/sheet")]
                    native = [ET.fromstring(archive.read(name)) for name in sheets[-3:]]
                    self.assertTrue(any(node.findall('.//{'+NS+'}f') for node in native))
                    self.assertTrue(any(node.findall('.//{'+NS+'}dataValidation') for node in native))
                    self.assertFalse(any("externalLink" in name for name in archive.namelist()))
        targets = next(table for table in combined if table["title"] == "预算对象与约束")
        self.assertEqual(len(targets["rows"]), 2)
        compact, raw = volume_delivery.make(full, binding_digest="a"*64, attempt=1, draft=True, max_tables=15)
        verified = volume_delivery.verify_full(compact, raw, binding_digest="a"*64, attempt=1, draft=True,
            report_id=self.report.id, evidence_digest=self.prepared.binding["sealedDigest"], max_tables=15)
        self.assertEqual(verified["budgetPlanDigest"], self.prepared.reference["planDigest"])

    def test_durable_draft_download_revalidates_parameters_without_fact_scan(self):
        run_id = self.start()
        with patch("ai_assistant.business_reports.content", side_effect=self.verified_content):
            self.assertEqual(files.tick()["status"], "ready")
        row = files.get(run_id, self.admin)
        compact = json.loads(row.manifest_json)
        with patch("ai_assistant.business_budget.resolve", side_effect=AssertionError("download must not scan facts")), \
                patch("ai_assistant.business_sealed.Reader.pages", side_effect=AssertionError("no fact pages")):
            for descriptor in [*compact["files"], compact["manifestFile"]]:
                raw = self.read_file(run_id, descriptor)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), descriptor["sha256"])
                if descriptor["format"] == "json":
                    self.assertEqual(json.loads(raw)["budgetPlanDigest"], self.prepared.reference["planDigest"])
        with patch.object(m.AiBusinessBudgetPlan.objects, "filter") as plans:
            plans.return_value.first.return_value = None
            with self.assertRaises(AiError):
                volumes.chunk(run_id, "0", "json", {"sequence": "1"}, self.admin)

    def test_staged_resume_recalculates_budget_and_reuses_complete_bytes(self):
        run_id = self.start()
        audit = files.audit
        def stop(row, principal, action):
            if action == "volumes_ready":
                raise RuntimeError("synthetic final audit interruption")
            return audit(row, principal, action)
        with patch("ai_assistant.business_reports.content", side_effect=self.verified_content), \
                patch("ai_assistant.business_files.audit", side_effect=stop):
            self.assertEqual(files.tick()["status"], "paused")
        row = files.get(run_id, self.admin)
        self.assertNotEqual(row.manifest_json, "{}")
        chunks = list(m.AiBusinessVolumeChunk.objects.filter(run=row).values_list("id", "content_digest"))
        with patch.object(m.AiBusinessBudgetPlan.objects, "filter") as plans:
            plans.return_value.first.return_value = None
            with self.assertRaises(AiError): files.control(run_id, {"expectedVersion": row.version, "action": "resume"}, self.admin)
        with patch("ai_assistant.business_budget.resolve", wraps=business_budget.resolve) as resolve, \
                patch("ai_assistant.business_reports.content", side_effect=self.verified_content):
            files.control(run_id, {"expectedVersion": row.version, "action": "resume"}, self.admin)
            with patch("ai_assistant.business_export.prepare_volumes", side_effect=AssertionError("do not rebuild staged files")):
                self.assertEqual(files.tick()["status"], "ready")
            self.assertGreaterEqual(resolve.call_count, 3)
        self.assertEqual(list(m.AiBusinessVolumeChunk.objects.filter(run=row).values_list("id", "content_digest")), chunks)

    def test_mismatched_budget_content_or_missing_parameter_prevents_output(self):
        wrong = deepcopy(self.content); wrong["budget"]["planDigest"] = "f"*64
        with patch("ai_assistant.business_reports.content", return_value=wrong), self.assertRaises(AiError):
            with export.prepare_volumes(self.report, self.admin, draft=True):
                self.fail("mismatched budget cannot prepare output")
        with patch.object(m.AiBusinessBudgetPlan.objects, "filter") as plans:
            plans.return_value.first.return_value = None
            with self.assertRaises(AiError): self.start()
        self.assertFalse(m.AiBusinessFileRun.objects.exists())

    def test_receipt_revalidation_failure_blocks_file_creation(self):
        with patch("ai_assistant.business_reports.content", side_effect=AiError("missing durable budget receipts")):
            with self.assertRaisesMessage(AiError, "missing durable budget receipts"):
                files.create(self.report.id, {"deliveryMode": "volumes", "draft": True,
                    "expectedPrincipalKey": evidence.principal_key(self.admin)}, self.admin)
        self.assertFalse(m.AiBusinessFileRun.objects.exists())

    def test_staged_manifest_budget_digest_is_bound_to_authoritative_parameter(self):
        run_id = self.start()
        with patch("ai_assistant.business_reports.content", side_effect=self.verified_content):
            self.assertEqual(files.tick()["status"], "ready")
        row = files.get(run_id, self.admin)
        verify = volume_delivery.verify_full
        def substituted(*args, **kwargs):
            # Structural validation alone cannot authenticate a plan digest;
            # require the additional comparison to the report's parameter row.
            full = verify(*args, **kwargs)
            full["budgetPlanDigest"] = "f"*64
            full["volumes"][0]["budgetCalculator"]["planDigest"] = "f"*64
            return full
        with patch("ai_assistant.business_volume_files.volume_delivery.verify_full", side_effect=substituted):
            with self.assertRaisesMessage(AiError, "多卷预算清单与报告固定参数不一致"):
                volumes._verify_staged(row, self.admin, lambda: None)
