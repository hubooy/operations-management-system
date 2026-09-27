"""Actual approved renderer-7 temporary volumes, without durable file runs."""
from copy import deepcopy
import json
from unittest.mock import patch
import zipfile

from django import test as djtest
from market.models import MarketDataRevision
from netshop.models import NetshopDataRevision
from sales.models import SalesDataRevision

from business_analysis import volume_delivery
from . import business_evidence, models as m
from . import business_promotion_formal_volumes as service
from . import test_business_export_catalog_acceptance as catalog_fixture
from . import test_business_promotion_approved_content as approval_fixture
from . import test_business_promotion_creation as creation_fixture
from .policy import AiError, canonical, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionFormalVolumeTests(djtest.TransactionTestCase):
    user = approval_fixture.PromotionApprovedContentTests.user
    call = approval_fixture.PromotionApprovedContentTests.call
    collect_body = approval_fixture.PromotionApprovedContentTests.collect_body
    bundle = approval_fixture.PromotionApprovedContentTests.bundle
    input_for = approval_fixture.PromotionApprovedContentTests.input_for
    insert = approval_fixture.PromotionApprovedContentTests.insert
    seed = approval_fixture.PromotionApprovedContentTests.seed
    setUp = approval_fixture.PromotionApprovedContentTests.setUp
    request_body = approval_fixture.PromotionApprovedContentTests.request_body
    current_catalog = approval_fixture.PromotionApprovedContentTests.current_catalog
    create_fixed_report = approval_fixture.PromotionApprovedContentTests.create_fixed_report
    base = approval_fixture.PromotionApprovedContentTests.base
    read = approval_fixture.PromotionApprovedContentTests.read
    append = approval_fixture.PromotionApprovedContentTests.append
    package = approval_fixture.PromotionApprovedContentTests.package
    promotion = approval_fixture.PromotionApprovedContentTests.promotion
    complete = approval_fixture.PromotionApprovedContentTests.complete
    running_job = approval_fixture.PromotionApprovedContentTests.running_job
    five_completed = approval_fixture.PromotionApprovedContentTests.five_completed
    approved = approval_fixture.PromotionApprovedContentTests.approved

    def test_complete_two_or_more_volumes_and_current_approval(self):
        report = self.five_completed(promotion_reference=True)
        self.approved(report)
        before = m.AiBusinessFileRun.objects.count()
        with patch.object(service.file_tables.runtime.transport, "catalog", side_effect=self.current_catalog), patch(
                "ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote:
            with service.open_volumes(report.id, self.admin, max_tables=8) as prepared:
                receipt = prepared.manifest
                self.assertGreater(receipt["volumeCount"], 1)
                self.assertEqual(receipt["sourceTableCount"], receipt["sealedSourceTableCount"] + 7)
                self.assertFalse(receipt["deliveryAuthorized"])
                self.assertFalse(receipt["registeredRenderer"])
                self.assertFalse(receipt["authorityVerified"])
                self.assertEqual(receipt["receiptDigest"], digest({key: value for key, value in receipt.items()
                    if key != "receiptDigest"}))
                compact = receipt["compactManifest"]
                full_bytes = prepared.path(0, "json").read_bytes()
                full = volume_delivery.verify_full(compact, full_bytes,
                    binding_digest=compact["bindingDigest"], attempt=1, draft=False,
                    report_id=report.id,
                    evidence_digest=json.loads(report.snapshot_json)["sealedDigest"],
                    renderer_version=7, max_tables=8)
                self.assertEqual(full["promotionFileProof"], receipt["promotionFileProof"])
                self.assertEqual(len(full["tables"]), receipt["sourceTableCount"])
                self.assertEqual([item["key"] for item in full["tables"][-2:]],
                    ["promotion-keyword_sku", "promotion-keyword_sku_context"])
                for index in range(1, receipt["volumeCount"] + 1):
                    volume = full["volumes"][index-1]
                    self.assertLessEqual(len(volume["tables"]), 8)
                    for kind in ("html", "xlsx"):
                        self.assertEqual(len(prepared.path(index, kind).read_bytes()),
                            volume["files"][kind]["bytes"])
                    with zipfile.ZipFile(prepared.path(index, "xlsx")) as archive:
                        self.assertIn(b'Extension="json" ContentType="application/json"',
                            archive.read("[Content_Types].xml"))
                old_path = prepared.path(1, "html")
            with self.assertRaises(AiError): prepared.path(1, "html")
            self.assertFalse(old_path.exists())
        model.assert_not_called(); remote.assert_not_called()
        self.assertEqual(m.AiBusinessFileRun.objects.count(), before)

    def test_volume_cap_and_unapproved_are_fail_closed(self):
        report = self.five_completed()
        with patch.object(service.file_tables.runtime.transport, "catalog", side_effect=self.current_catalog), patch.object(
                service.volume_files, "render") as render, self.assertRaises(AiError):
            with service.open_volumes(report.id, self.admin): self.fail("unapproved escaped")
        render.assert_not_called()
        self.approved(report)
        with patch.object(service.file_tables.runtime.transport, "catalog", side_effect=self.current_catalog), patch.object(
                service.volume_files, "render") as render, self.assertRaises(AiError) as caught:
            with service.open_volumes(report.id, self.admin,
                    max_tables=5, max_volumes=1): self.fail("truncated one-volume escaped")
        self.assertEqual(caught.exception.status, 413)
        render.assert_not_called()


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionNineteenSourceVolumeTests(djtest.TransactionTestCase):
    """Real sealed 19-source catalog plus real completed five-Agent review."""
    user = catalog_fixture.BusinessExportCatalogAcceptanceTests.user
    request_body = creation_fixture.PromotionCreationTests.request_body
    current_catalog = creation_fixture.PromotionCreationTests.current_catalog
    create_fixed_report = approval_fixture.PromotionApprovedContentTests.create_fixed_report
    base = approval_fixture.PromotionApprovedContentTests.base
    read = approval_fixture.PromotionApprovedContentTests.read
    append = approval_fixture.PromotionApprovedContentTests.append
    package = approval_fixture.PromotionApprovedContentTests.package
    promotion = approval_fixture.PromotionApprovedContentTests.promotion
    complete = approval_fixture.PromotionApprovedContentTests.complete
    running_job = approval_fixture.PromotionApprovedContentTests.running_job
    five_completed = approval_fixture.PromotionApprovedContentTests.five_completed
    approved = approval_fixture.PromotionApprovedContentTests.approved

    def planned_evidence_body(self, _):
        sources = deepcopy(self.sources)
        for source in sources:
            if source["key"] == "promotion-current": source["key"] = "ads"
        return {"schemaVersion": "business-evidence-v2", "clientRequestId": "nineteen-promotion",
            "collectionMode": "bulk", "sources": sources,
            "analysisRequest": {"schemaVersion": "business-analysis-request-v1",
                "question": "词货分析", "requestedDimensions": ["shop", "sku", "spu"],
                "requestedWindows": ["current", "previous", "yearAgo"]}}

    def setUp(self):
        self.skip_draft_report = True
        NetshopDataRevision.objects.update_or_create(domain="netshop",
            defaults={"revision": 1, "source_digest": "a"*64})
        MarketDataRevision.objects.update_or_create(domain="market",
            defaults={"revision": 1, "source_digest": "a"*64})
        for domain in ("sales", "erp"):
            SalesDataRevision.objects.update_or_create(domain=domain,
                defaults={"revision": 1, "source_digest": "a"*64})
        catalog_fixture.BusinessExportCatalogAcceptanceTests.setUp(self)
        self.parent = business_evidence.get_run(self.run_id, self.admin)
        self.serial = 0

    def test_actual_nineteen_sources_render_two_complete_volumes(self):
        report = self.five_completed()
        self.approved(report)
        with patch.object(service.file_tables.runtime.transport, "catalog", side_effect=self.current_catalog), patch(
                "ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote:
            with service.open_volumes(report.id, self.admin) as prepared:
                receipt = prepared.manifest
                self.assertEqual(receipt["sealedSourceCount"], 19)
                self.assertEqual(receipt["volumeCount"], 2)
                full = json.loads(prepared.path(0, "json").read_bytes())
                raw = {row["key"][4:] for row in full["tables"] if row["key"].startswith("raw-")}
                self.assertEqual(raw, {source["key"] for source in self.sources})
                self.assertIn("b2b-current", raw)
                self.assertIn("market-current", raw)
                self.assertEqual([row["key"] for row in full["tables"][-2:]],
                    ["promotion-keyword_sku", "promotion-keyword_sku_context"])
                self.assertEqual(sum(volume["rowCount"] for volume in full["volumes"]), full["totalRows"])
                self.assertTrue(all(len(volume["tables"]) <= 120 for volume in full["volumes"]))
                for index in range(1, 3):
                    with zipfile.ZipFile(prepared.path(index, "xlsx")) as archive:
                        self.assertIsNone(archive.testzip())
        model.assert_not_called(); remote.assert_not_called()
