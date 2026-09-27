from copy import deepcopy
import io
import json
from unittest.mock import patch
import zipfile
from django.test import TestCase, override_settings

from business_analysis.test_report_files import ReportData
from . import business_evidence as evidence, business_reports, business_export, models as m
from . import test_business_evidence as source_tests
from .policy import AiError, canonical


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessExportTests(TestCase):
    user = source_tests.BusinessEvidenceTests.user
    execute = source_tests.BusinessEvidenceTests.execute
    collect = source_tests.BusinessEvidenceTests.collect

    def setUp(self):
        source_tests.BusinessEvidenceTests.setUp(self)
        self.evidence_id = evidence.create(self.body, self.admin)["item"]["id"]
        self.collect(self.evidence_id, 1)
        self.collect(self.evidence_id, 2)
        evidence.finish(self.evidence_id, {"expectedVersion": 3, "action": "seal"}, self.admin)
        with patch("ai_assistant.transport.catalog", return_value=self.catalog):
            created = business_reports.create({"clientRequestId": "export", "evidenceRunId": self.evidence_id, "question": "完整销售分析", "dryRun": True}, self.admin)
        self.report = m.AiReportRun.objects.select_related("workflow").get(pk=created["item"]["id"])
        self.content = {"sections": [{"title": "销售诊断", "body": "仅合成数据；需要补充市场与B端证据。"}],
            "diagnosis": {"findings": [{"id": "gap-1", "kind": "gap", "title": "数据缺口", "explanation": "仅有销售来源", "facts": []}]}}

    def test_sealed_raw_and_all_dimensions_share_full_sources_and_do_not_query_live(self):
        with patch("ai_assistant.business_reports.content", return_value=self.content), patch("ai_assistant.transport.execute_tool") as remote, patch("ai_assistant.provider.turn") as model:
            xlsx, html = io.BytesIO(), io.BytesIO()
            proof = business_export.build(self.report, self.admin, xlsx, html, draft=True)
            remote.assert_not_called()
            model.assert_not_called()
        data = ReportData(html.getvalue().decode()).value
        tables = {t["key"]: t for t in data["tables"]}
        self.assertEqual(tables["raw-sales"]["proof"]["rowCount"], 12)
        self.assertEqual(len([t for t in data["tables"] if t["key"].startswith("analysis-")]), 8)
        shop = next(t for t in data["tables"] if t["title"] == "sales_店铺_本期")
        keys = [c["key"] for c in shop["columns"]]
        self.assertEqual(shop["rows"][0][keys.index("/metrics/netSalesCents/value")], 120000)
        api = evidence.analysis_table(self.evidence_id, {"sourceKey": "sales", "dimension": "shop"}, self.admin)
        self.assertEqual(shop["rows"][0][keys.index("/id")], api["rows"][0]["id"])
        with zipfile.ZipFile(io.BytesIO(xlsx.getvalue())) as archive:
            self.assertEqual(json.loads(archive.read("teruisi-manifest.json"))["tables"], proof["tables"])

    def test_formal_review_permissions_and_snapshot_binding_are_required(self):
        with self.assertRaises(AiError):
            business_export.build(self.report, self.admin, io.BytesIO(), io.BytesIO(), draft=False)
        with self.assertRaises(AiError):
            business_export.build(self.report, self.viewer, io.BytesIO(), io.BytesIO(), draft=True)
        snapshot = json.loads(self.report.snapshot_json)
        self.report.snapshot_json = canonical({**snapshot, "evidenceVersion": 1})
        with patch("ai_assistant.business_reports.content", return_value=self.content), self.assertRaises(AiError):
            business_export.build(self.report, self.admin, io.BytesIO(), io.BytesIO(), draft=True)

    def test_late_chunk_tampering_stops_file_pair(self):
        # The immutable DB trigger prevents actual edits; corrupt the reader's
        # in-memory projection to prove the exporter still checks every chunk.
        original = m.AiBusinessEvidenceChunk.objects.all().order_by("sequence")
        chunks = list(original)
        chunks[-1].payload_digest = "0"*64
        with patch("ai_assistant.business_reports.content", return_value=self.content), patch("ai_assistant.business_sealed.m.AiBusinessEvidenceChunk.objects.filter") as query:
            query.return_value.order_by.return_value.iterator.side_effect = lambda **kwargs: iter(deepcopy(chunks))
            with self.assertRaises(AiError):
                business_export.build(self.report, self.admin, io.BytesIO(), io.BytesIO(), draft=True)
