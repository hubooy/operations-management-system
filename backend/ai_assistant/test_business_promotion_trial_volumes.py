"""Owning v9 trial files: real approved roots, temporary bytes only."""
import hashlib
import json
from unittest.mock import patch
import zipfile

from django import test as djtest

from business_analysis import volume_delivery
from business_analysis.contracts import canonical
from business_analysis.test_report_files import ReportData
from . import business_promotion_trial_volumes as service
from . import models as m
from . import test_business_promotion_approved_content as fixture
from .policy import AiError, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionTrialVolumeTests(djtest.TransactionTestCase):
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

    def test_all_trial_volumes_same_rows_and_no_native_budget(self):
        report = self.five_completed(promotion_reference=True)
        self.approved(report)
        before = m.AiBusinessFileRun.objects.count()
        with patch.object(service.file_tables.runtime.transport, "catalog",
                side_effect=self.current_catalog), patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            with service.open_volumes(report.id, self.admin, max_tables=8) as prepared:
                receipt = prepared.manifest
                self.assertGreater(receipt["volumeCount"], 1)
                self.assertEqual(receipt["rendererVersion"], 9)
                self.assertFalse(receipt["deliveryAuthorized"])
                self.assertFalse(receipt["authorityVerified"])
                compact = receipt["compactManifest"]
                full = volume_delivery.verify_full(compact,
                    prepared.path(0, "json").read_bytes(),
                    binding_digest=compact["bindingDigest"], attempt=1, draft=False,
                    report_id=report.id,
                    evidence_digest=json.loads(report.snapshot_json)["sealedDigest"],
                    renderer_version=9, max_tables=8)
                self.assertEqual(full["promotionTrialProof"], receipt["promotionTrialProof"])
                self.assertEqual(full["promotionFileProof"], receipt["promotionFileProof"])
                self.assertNotIn("budgetPlanDigest", full)
                self.assertEqual([table["key"] for table in full["tables"][-2:]],
                    ["promotion-keyword_sku", "promotion-keyword_sku_context"])
                by_key = {table["key"]: table for table in full["tables"]}
                for key in ("promotion-approved-actions-v1", "promotion-trial-source-scope",
                        "promotion-trial-boundaries", "complete-content", "sources"):
                    self.assertIn(key, by_key)
                self.assertEqual(by_key["promotion-trial-boundaries"]["rowCount"], 6)
                self.assertEqual(full["sourceTableCount"], receipt["sealedSourceTableCount"] + 8)
                action = by_key["promotion-approved-actions-v1"]
                approved = service.approved_content.build(report.id, self.admin)
                analyses = [approved["content"]["diagnosis"],
                    *approved["content"]["professionalAnalyses"].values()]
                self.assertEqual(action["rowCount"], sum(
                    finding["kind"] == "action" for analysis in analyses
                    for finding in analysis["findings"]))
                self.assertEqual(action["rowDigest"], receipt["promotionTrialProof"]["actionRowDigest"])
                seen = []
                for index in range(1, receipt["volumeCount"] + 1):
                    volume = full["volumes"][index-1]
                    self.assertEqual(volume["nativeBudgetSheets"], 0)
                    self.assertFalse(volume["offlineBudgetEnabled"])
                    html = prepared.path(index, "html").read_bytes()
                    document = ReportData(html.decode("utf-8")).value
                    with zipfile.ZipFile(prepared.path(index, "xlsx")) as archive:
                        self.assertIsNone(archive.testzip())
                        workbook = json.loads(archive.read("teruisi-manifest.json"))
                        self.assertEqual([table["proof"] for table in document["tables"]], workbook["tables"])
                    for item, part in zip(document["tables"], volume["tables"]):
                        self.assertEqual(item["proof"]["rowDigest"], part["rowDigest"])
                        self.assertEqual(item["proof"]["rowCount"], part["rowLimit"])
                        self.assertEqual(item["proof"]["rowDigest"], hashlib.sha256(
                            "".join(canonical(row)+"\n" for row in item["rows"]).encode()).hexdigest())
                        seen.append(part["key"])
                self.assertEqual(set(seen), set(by_key))
                old = prepared.path(1, "html")
            with self.assertRaises(AiError): prepared.path(1, "html")
            self.assertFalse(old.exists())
        self.assertEqual(m.AiBusinessFileRun.objects.count(), before)
        model.assert_not_called(); remote.assert_not_called()

    def test_unapproved_and_wrong_account_never_render(self):
        report = self.five_completed()
        with patch.object(service.file_tables.runtime.transport, "catalog",
                side_effect=self.current_catalog), patch.object(service.volume_files,
                "render") as render:
            with self.assertRaises(AiError):
                with service.open_volumes(report.id, self.admin):
                    self.fail("Unapproved trial escaped")
            self.approved(report)
            with self.assertRaises(AiError):
                with service.open_volumes(report.id, self.viewer):
                    self.fail("Wrong account escaped")
            render.assert_not_called()

    def test_v9_proof_rejects_action_digest_and_budget_spoof(self):
        report = self.five_completed()
        self.approved(report)
        with patch.object(service.file_tables.runtime.transport, "catalog",
                side_effect=self.current_catalog):
            with service.open_volumes(report.id, self.admin) as prepared:
                compact = prepared.manifest["compactManifest"]
                raw = prepared.path(0, "json").read_bytes()
                full = json.loads(raw)
                full["promotionTrialProof"]["actionRowDigest"] = "0"*64
                full["promotionTrialProof"]["proofDigest"] = digest({key: value
                    for key, value in full["promotionTrialProof"].items() if key != "proofDigest"})
                full["manifestDigest"] = digest({key: value for key, value in full.items()
                    if key != "manifestDigest"})
                with self.assertRaises(Exception):
                    volume_delivery.make(full, binding_digest=compact["bindingDigest"],
                        attempt=1, draft=False, renderer_version=9)
                full = json.loads(raw)
                full["volumes"][0]["nativeBudgetSheets"] = 3
                full["manifestDigest"] = digest({key: value for key, value in full.items()
                    if key != "manifestDigest"})
                with self.assertRaises(Exception):
                    volume_delivery.make(full, binding_digest=compact["bindingDigest"],
                        attempt=1, draft=False, renderer_version=9)
