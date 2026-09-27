"""Real sealed market evidence -> temporary three-table HTML/XLSX preview."""
import io
import json
from pathlib import Path
from unittest.mock import patch
import zipfile

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from access_control.models import AppUser

from . import business_market_file_preview as service
from . import models as m
from . import test_business_market_export as fixtures
from .policy import AiError, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketFilePreviewTests(djtest.TransactionTestCase):
    user = fixtures.BusinessMarketExportTests.user
    call = fixtures.BusinessMarketExportTests.call
    bundle = fixtures.BusinessMarketExportTests.bundle
    input_for = fixtures.BusinessMarketExportTests.input_for
    insert = fixtures.BusinessMarketExportTests.insert
    seed = fixtures.BusinessMarketExportTests.seed
    collect_body = fixtures.BusinessMarketExportTests.collect_body
    setUp = fixtures.BusinessMarketExportTests.setUp

    def preview(self, **kwargs):
        return service.open_files(self.report.id, "market", "market-prior",
            self.admin, bands=fixtures.BANDS, **kwargs)

    def test_three_complete_typed_tables_notes_opc_and_no_file_run(self):
        before = m.AiBusinessFileRun.objects.count()
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            with self.preview() as prepared:
                receipt = prepared.manifest
                self.assertEqual(receipt["schemaVersion"], service.SCHEMA)
                self.assertTrue(receipt["dataOnly"])
                self.assertFalse(receipt["diagnosisIncluded"])
                self.assertFalse(receipt["deliveryAuthorized"])
                self.assertFalse(receipt["registeredRenderer"])
                self.assertEqual(receipt["receiptDigest"], digest({key: value for key, value in receipt.items()
                    if key != "receiptDigest"}))
                self.assertEqual([row["key"] for row in receipt["renderedTables"]],
                    ["market-price_band_summary", "market-price_band_members", "market-rank_entry_exit"])
                self.assertEqual([row["rowCount"] for row in receipt["renderedTables"]], [1, 2, 3])
                html = prepared.path("html").read_bytes()
                xlsx = prepared.path("xlsx").read_bytes()
                for phrase in ("TOP", "缺席", "不可相加", "价格带"):
                    self.assertIn(phrase.encode(), html)
                self.assertEqual(len(html), receipt["files"]["html"]["bytes"])
                self.assertEqual(len(xlsx), receipt["files"]["xlsx"]["bytes"])
                with zipfile.ZipFile(io.BytesIO(xlsx)) as archive:
                    self.assertIn(b'Extension="json" ContentType="application/json"',
                        archive.read("[Content_Types].xml"))
                    manifest = json.loads(archive.read("teruisi-manifest.json"))
                    self.assertEqual([row["rowCount"] for row in manifest["tables"]], [1, 2, 3])
                    self.assertEqual(manifest["metadata"]["materialManifestDigest"], receipt["materialManifestDigest"])
                old_path = prepared.path("html")
            with self.assertRaises(AiError): prepared.path("html")
            self.assertFalse(old_path.exists())
        model.assert_not_called(); remote.assert_not_called()
        self.assertFalse(any("market_ranking_entries" in item["sql"].lower() for item in queries))
        self.assertEqual(m.AiBusinessFileRun.objects.count(), before)

    def test_wrong_scope_capacity_and_tampered_material_are_rejected(self):
        for report_id, source_key, baseline_key, actor in (
                (self.report.id, "ads", "market-prior", self.admin),
                (self.report.id, "market", "market", self.admin),
                (self.report.id, "market", "market-prior", self.viewer),
                ("missing", "market", "market-prior", self.admin)):
            with self.subTest(report=report_id, source=source_key), self.assertRaises(AiError):
                with service.open_files(report_id, source_key, baseline_key, actor,
                        bands=fixtures.BANDS): self.fail("invalid preview escaped")
        with self.assertRaises(AiError) as caught:
            with self.preview(limits={"maxBytes": 100}): self.fail("capacity escaped")
        self.assertEqual(caught.exception.status, 413)
        original = service.export.PreparedMarketExport.manifest.fget
        def corrupt(prepared):
            value = original(prepared)
            value["tables"][0]["ndjsonSha256"] = "0"*64
            value["manifestDigest"] = digest({key: item for key, item in value.items()
                if key != "manifestDigest"})
            return value
        with patch.object(service.export.PreparedMarketExport, "manifest", property(corrupt)), self.assertRaises(AiError):
            with self.preview(): self.fail("tampered material escaped")

    def test_partial_temp_cleanup_and_exit_revocation(self):
        before = m.AiBusinessFileRun.objects.count()
        paths = []
        def partial(xlsx, html, **_):
            paths.extend([Path(xlsx.name), Path(html.name)])
            xlsx.write(b"partial-xlsx"); html.write(b"partial-html")
            raise RuntimeError("synthetic preview failure")
        with patch.object(service.report_files, "write_pair", side_effect=partial), self.assertRaisesRegex(
                RuntimeError, "preview failure"):
            with self.preview(): self.fail("partial preview escaped")
        self.assertTrue(paths and all(not path.exists() for path in paths))
        with self.assertRaises(AiError):
            with self.preview() as prepared:
                self.assertTrue(prepared.path("html").exists())
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        self.assertEqual(m.AiBusinessFileRun.objects.count(), before)
