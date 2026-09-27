"""Real paired draft files from actual new-profile reports; no model dispatch."""
import hashlib
import io
import json
import re
from unittest.mock import patch
import zipfile
from django import test as djtest
from access_control.models import AppUser
from . import business_promotion_file_preview as service
from . import test_business_promotion_file_tables as fixtures
from .policy import AiError, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionFilePreviewTests(djtest.TransactionTestCase):
    user = fixtures.PromotionFileTablesTests.user
    call = fixtures.PromotionFileTablesTests.call
    collect_body = fixtures.PromotionFileTablesTests.collect_body
    bundle = fixtures.PromotionFileTablesTests.bundle
    input_for = fixtures.PromotionFileTablesTests.input_for
    insert = fixtures.PromotionFileTablesTests.insert
    seed = fixtures.PromotionFileTablesTests.seed
    request_body = fixtures.PromotionFileTablesTests.request_body
    current_catalog = fixtures.PromotionFileTablesTests.current_catalog
    create_report = fixtures.PromotionFileTablesTests.create_report
    setUp = fixtures.PromotionFileTablesTests.setUp

    def build(self, report, xlsx, html, **kwargs):
        with patch.object(service.owning.runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.build(report.id, self.admin, xlsx, html, **kwargs)

    def test_actual_pair_full_two_tables_notes_hashes_and_no_diagnostic_claim(self):
        report = self.create_report(); xlsx, html = io.BytesIO(), io.BytesIO()
        with patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote:
            result = self.build(report, xlsx, html)
        model.assert_not_called(); remote.assert_not_called()
        self.assertTrue(result["dataOnlyDraft"])
        self.assertFalse(result["diagnosisIncluded"]); self.assertFalse(result["deliveryAuthorized"])
        self.assertEqual(result["previewDigest"], digest({k:v for k,v in result.items() if k != "previewDigest"}))
        for kind, stream in (("xlsx",xlsx),("html",html)):
            raw = stream.getvalue()
            self.assertEqual(result["files"][kind], {"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()})
        page = html.getvalue().decode()
        data = json.loads(re.search(r'<script type="application/json" id="report-data">(.*?)</script>',page,re.S).group(1))
        self.assertEqual(len(data["tables"]), 2)
        self.assertEqual(data["metadata"]["materialManifestDigest"], result["materialManifestDigest"])
        for table in data["tables"]:
            self.assertIn("仅为数据草稿", table["note"])
            self.assertIn("来源：ads", table["note"])
            self.assertIn("基期：未选择", table["note"])
            self.assertIn("费用不可相加", table["note"])
            self.assertGreater(len(table["rows"]), 0)
        with zipfile.ZipFile(io.BytesIO(xlsx.getvalue())) as archive:
            self.assertIn("application/json",archive.read("[Content_Types].xml").decode())
            for index in (1,2):
                sheet = archive.read(f"xl/worksheets/sheet{index}.xml").decode()
                self.assertIn("仅为数据草稿",sheet); self.assertIn("来源：ads",sheet)
                self.assertNotIn("<f>",sheet)

    def test_revocation_after_rendering_rolls_back_caller_streams(self):
        report = self.create_report(); xlsx, html = io.BytesIO(), io.BytesIO()
        original = service._copy_verified
        def revoke(source,target):
            result = original(source,target)
            if target is html: AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return result
        with patch.object(service,"_copy_verified",side_effect=revoke), self.assertRaises(AiError):
            self.build(report,xlsx,html)
        self.assertEqual(xlsx.getvalue(),b""); self.assertEqual(html.getvalue(),b"")

    def test_writer_limit_and_cancellation_never_leave_partial_preview(self):
        report = self.create_report()
        for cancel in (False, True):
            xlsx, html = io.BytesIO(), io.BytesIO()
            error = RuntimeError("cancelled preview")
            def checkpoint(event):
                if cancel and event.get("stage") == "rendering": raise error
            with patch.object(service.report_files,"MAX_FILE_BYTES",256 if not cancel else 256*1024*1024):
                with self.assertRaises((AiError,RuntimeError)):
                    self.build(report,xlsx,html,checkpoint=checkpoint)
            self.assertEqual(xlsx.getvalue(),b""); self.assertEqual(html.getvalue(),b"")

    def test_nonempty_or_same_streams_reject_without_erasing_content(self):
        report = self.create_report(); stream = io.BytesIO(b"existing"); stream.seek(0)
        with self.assertRaises(AiError): self.build(report,stream,io.BytesIO())
        self.assertEqual(stream.getvalue(),b"existing")
        empty = io.BytesIO()
        with self.assertRaises(AiError): self.build(report,empty,empty)

    def test_short_write_rolls_back_both_outputs(self):
        class Short(io.BytesIO):
            def write(self, data): return super().write(data[:3])
        report = self.create_report(); xlsx, html = io.BytesIO(), Short()
        with self.assertRaises(OSError): self.build(report,xlsx,html)
        self.assertEqual(xlsx.getvalue(),b""); self.assertEqual(html.getvalue(),b"")
