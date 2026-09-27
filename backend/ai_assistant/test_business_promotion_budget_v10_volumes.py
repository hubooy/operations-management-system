"""Owning v10 candidate bytes; isolated PostgreSQL tests, no file-run ready."""
import json
from unittest.mock import patch
import zipfile

from django import test as djtest
from business_analysis import budget_excel, volume_delivery
from business_analysis.test_report_files import ReportData
from . import business_promotion_budget_v10_volumes as service
from . import business_promotion_completed_receipts as receipts
from . import business_promotion_content_contract as contract
from . import models as m
from . import test_business_promotion_approved_content as fixture
from . import test_business_screening_diagnosis as answers
from .policy import AiError, canonical, digest, mutation


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionBudgetV10VolumeTests(djtest.TransactionTestCase):
    user = fixture.PromotionApprovedContentTests.user
    call = fixture.PromotionApprovedContentTests.call
    collect_body = fixture.PromotionApprovedContentTests.collect_body
    bundle = fixture.PromotionApprovedContentTests.bundle
    input_for = fixture.PromotionApprovedContentTests.input_for
    insert = fixture.PromotionApprovedContentTests.insert
    seed = fixture.PromotionApprovedContentTests.seed
    setUp = fixture.PromotionApprovedContentTests.setUp
    request_body = fixture.PromotionApprovedContentTests.request_body
    current_catalog = fixture.PromotionApprovedContentTests.current_catalog
    create_fixed_report = fixture.PromotionApprovedContentTests.create_fixed_report
    base = fixture.PromotionApprovedContentTests.base
    read = fixture.PromotionApprovedContentTests.read
    append = fixture.PromotionApprovedContentTests.append
    package = fixture.PromotionApprovedContentTests.package
    promotion = fixture.PromotionApprovedContentTests.promotion
    complete = fixture.PromotionApprovedContentTests.complete
    running_job = fixture.PromotionApprovedContentTests.running_job
    five_completed = fixture.PromotionApprovedContentTests.five_completed
    approved = fixture.PromotionApprovedContentTests.approved

    def _complete_budget_report(self):
        report = self.create_fixed_report(budget=True)
        for role in contract.ROLES:
            job = self.running_job(report, role)
            self.package(report, job)
            if role in receipts.screening_contract.BUDGET_NODES:
                offset = 0
                while offset is not None:
                    args = {**self.base(report), "offset": offset}
                    page = self.read(job, receipts.contract.BUDGET_TOOL, args)
                    self.append(job, receipts.contract.BUDGET_TOOL, args, page)
                    offset = page["budget"]["pagination"]["nextOffset"]
            self.complete(job, canonical(answers.answer(role)))
        with mutation(self.admin):
            flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
            flow.status = "waiting_review"
            flow.current_node_key = "human_review"
            flow.version += 1
            flow.save()
            human = m.AiWorkflowNodeRuns.objects.get(run=flow, node_key="human_review")
            human.status = "waiting_review"
            human.version += 1
            human.save()
        self.approved(report)
        return report

    def _candidate(self, report, *, budget):
        before = m.AiBusinessFileRun.objects.count()
        with patch.object(service.file_tables.runtime.transport, "catalog",
                side_effect=self.current_catalog), patch(
                "ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            with service.open_volumes(report.id, self.admin, max_tables=10) as prepared:
                receipt = prepared.manifest
                self.assertEqual(receipt["rendererVersion"], 10)
                self.assertFalse(receipt["deliveryAuthorized"])
                self.assertFalse(receipt["registeredRenderer"])
                self.assertFalse(receipt["authorityVerified"])
                compact = receipt["compactManifest"]
                raw = prepared.path(0, "json").read_bytes()
                full = volume_delivery.verify_full(compact, raw,
                    binding_digest=compact["bindingDigest"], attempt=1,
                    draft=False, report_id=report.id,
                    evidence_digest=json.loads(report.snapshot_json)["sealedDigest"],
                    renderer_version=10, max_tables=10)
                self.assertEqual(full["promotionBudgetProof"],
                    receipt["promotionBudgetProof"])
                self.assertEqual(full["promotionTrialProof"],
                    receipt["promotionTrialProof"])
                self.assertEqual(full["tableSchemaDigest"],
                    receipt["tableSchemaDigest"])
                self.assertEqual(full["volumes"][0]["nativeBudgetSheets"],
                    3 if budget else 0)
                self.assertEqual(full["volumes"][0]["offlineBudgetEnabled"], budget)
                self.assertEqual("budgetPlanDigest" in full, budget)
                if budget:
                    model_proof = full["volumes"][0]["budgetCalculator"]
                    self.assertEqual(digest({**model_proof,
                        "sheets": list(budget_excel.TITLES)}),
                        receipt["promotionBudgetProof"]["nativeModelProofDigest"])
                else:
                    self.assertNotIn("budgetCalculator", full["volumes"][0])
                    self.assertIsNone(receipt["promotionBudgetProof"]["nativeModelProofDigest"])
                expected = (["promotion-budget-allocation-v1",
                    "promotion-budget-scenarios-v1",
                    "promotion-budget-scenario-summary-v1"] if budget
                    else ["promotion-budget-gap-v1"])
                self.assertEqual(receipt["promotionBudgetProof"]["tableKeys"], expected)
                by_key = {item["key"]: item for item in full["tables"]}
                self.assertTrue(set(expected) <= set(by_key))
                for index in range(1, receipt["volumeCount"] + 1):
                    document = ReportData(prepared.path(index, "html").read_text(
                        encoding="utf-8")).value
                    with zipfile.ZipFile(prepared.path(index, "xlsx")) as book:
                        self.assertIsNone(book.testzip())
                        manifest = json.loads(book.read("teruisi-manifest.json"))
                        self.assertEqual([item["proof"] for item in document["tables"]],
                            manifest["tables"])
                        if index == 1 and budget:
                            self.assertEqual(len(full["volumes"][0]["budgetCalculator"]["sheets"]), 3)
                            for name in book.namelist():
                                if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                                    self.assertNotIn(b"MOD(", book.read(name))
                    self.assertEqual([item["proof"]["rowDigest"] for item in
                        document["tables"]], [item["rowDigest"] for item in
                        full["volumes"][index-1]["tables"]])
                saved_path = prepared.path(1, "html")
            with self.assertRaises(AiError):
                prepared.path(1, "html")
            self.assertFalse(saved_path.exists())
        self.assertEqual(m.AiBusinessFileRun.objects.count(), before)
        model.assert_not_called()
        remote.assert_not_called()

    def test_missing_fixed_budget_is_visible_gap_without_editable_amounts(self):
        report = self.five_completed(promotion_reference=True)
        self.approved(report)
        self._candidate(report, budget=False)

    def test_approved_fixed_budget_gets_source_bound_native_trial(self):
        report = self._complete_budget_report()
        self._candidate(report, budget=True)

    def test_unapproved_or_wrong_actor_never_renders(self):
        report = self.five_completed()
        with patch.object(service.file_tables.runtime.transport, "catalog",
                side_effect=self.current_catalog), patch.object(service.volume_files,
                "render") as render:
            with self.assertRaises(AiError):
                with service.open_volumes(report.id, self.admin):
                    self.fail("Unapproved report escaped")
            self.approved(report)
            with self.assertRaises(AiError):
                with service.open_volumes(report.id, self.viewer):
                    self.fail("Wrong account escaped")
            render.assert_not_called()
