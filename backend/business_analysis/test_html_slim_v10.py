"""Opt-in renderer-10 HTML compaction; historical/default bytes stay fixed."""
import base64
import gzip
import hashlib
import io
import json
import re
from unittest import TestCase
from unittest.mock import patch

from . import report_files, volume_delivery, volume_files, volume_plan
from .contracts import AnalysisContractError, canonical
from .report_files import Column, Table


def render_v10(*, slim=False):
    # Synthetic fixtures are never owning report/evidence authorization.
    from pathlib import Path
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "tools" / "business-budget-v10-static-scale.py"
    spec = importlib.util.spec_from_file_location("v10_slim_fixture", path)
    assert spec and spec.loader
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    source, candidate, tables, request, plan, metadata = tool.prepared(5)
    outputs = [volume_files.VolumeStreams(io.BytesIO(), io.BytesIO())]
    full = volume_files.render(tables, outputs, report_id=request["reportId"],
        evidence_digest=request["evidenceDigest"], renderer_version=10,
        plan=plan, title="合成 v10 候选", metadata=metadata,
        offline_budget=candidate.offline_budget,
        excel_budget=candidate.excel_budget, html_slim_v10=slim)
    return source, full, outputs[0].html.getvalue(), outputs[0].xlsx.getvalue()


def _tool():
    from pathlib import Path
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "tools" / "business-budget-v10-static-scale.py"
    spec = importlib.util.spec_from_file_location("v10_slim_multivolume", path)
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class HtmlSlimV10Tests(TestCase):
    def test_default_v10_html_bytes_remain_legacy(self):
        _, original, html, _ = render_v10()
        _, _, explicit, _ = render_v10(slim=False)
        self.assertEqual(html, explicit)
        self.assertNotIn(b'"htmlPayloadVersion":2', html)
        self.assertEqual(original["rendererVersion"], 10)

    def test_slim_html_keeps_all_rows_proofs_and_complete_v10_manifest(self):
        source, full, html, xlsx = render_v10(slim=True)
        self.assertEqual(source.consumed, 5)
        self.assertIn(b'"htmlPayloadVersion":2', html)
        self.assertEqual(len(xlsx), full["volumes"][0]["files"]["xlsx"]["bytes"])
        raw = re.search(rb'<script type="application/json" id="report-data">(.*?)</script>',
            html, re.S)
        self.assertIsNotNone(raw)
        self.assertNotIn(b'"rows":[', raw.group(1))
        data = json.loads(raw.group(1))
        self.assertEqual(len(data["tables"]), full["fragmentCount"])
        for table, proof in zip(data["tables"], full["volumes"][0]["tables"]):
            packed = base64.b64decode(table["rowsGzipBase64"], validate=True)
            self.assertEqual(hashlib.sha256(packed).hexdigest(),
                table["rowsGzipSha256"])
            body = gzip.decompress(packed)
            self.assertEqual(len(body), table["rowsNdjsonBytes"])
            self.assertEqual(hashlib.sha256(body).hexdigest(), proof["rowDigest"])
            rows = [json.loads(line) for line in body.splitlines()]
            self.assertEqual(len(rows), proof["rowLimit"])
            sha = hashlib.sha256()
            for row in rows:
                sha.update((canonical(row) + "\n").encode())
            self.assertEqual(sha.hexdigest(), proof["rowDigest"])
        compact, manifest_bytes = volume_delivery.make(full,
            binding_digest="8" * 64, attempt=1, draft=False,
            renderer_version=10)
        self.assertEqual(volume_delivery.verify_full(compact, manifest_bytes,
            binding_digest="8" * 64, attempt=1, draft=False,
            report_id="promotion-1", evidence_digest="d" * 64,
            renderer_version=10), full)

    def test_old_renderer_cannot_enable_slim_format(self):
        table = Table("one", "合成", "仅测试", (Column("value", "值"),),
            [["x"]], 1)
        request = volume_files.request_for([table], report_id="synthetic",
            evidence_digest="a" * 64, renderer_version=9)
        plan = volume_plan.build(request)
        output = volume_files.VolumeStreams(io.BytesIO(), io.BytesIO())
        with self.assertRaises(AnalysisContractError):
            volume_files.render([table], [output], report_id="synthetic",
                evidence_digest="a" * 64, renderer_version=9,
                plan=plan, title="合成", metadata={}, html_slim_v10=True)
        self.assertEqual(output.html.getvalue(), b"")
        self.assertEqual(output.xlsx.getvalue(), b"")

    def test_explicit_slim_multivolume_keeps_one_pass_and_all_fragments(self):
        tool = _tool()
        source, candidate, tables, request, _, metadata = tool.prepared(5)
        plan = volume_plan.build(request, max_tables=7, native_budget_sheets=3)
        self.assertEqual(plan["volumeCount"], 2)
        outputs = [volume_files.VolumeStreams(io.BytesIO(), io.BytesIO()) for _ in range(2)]
        full = volume_files.render(tables, outputs, report_id=request["reportId"],
            evidence_digest=request["evidenceDigest"], renderer_version=10,
            plan=plan, title="合成双卷", metadata=metadata,
            offline_budget=candidate.offline_budget,
            excel_budget=candidate.excel_budget,
            max_tables=7, html_slim_v10=True)
        self.assertEqual(source.iterations, 1)
        self.assertEqual(source.consumed, 5)
        self.assertEqual(full["volumeCount"], 2)
        self.assertEqual(sum(v["rowCount"] for v in full["volumes"]), full["totalRows"])
        for output, volume in zip(outputs, full["volumes"]):
            html = output.html.getvalue()
            self.assertEqual(hashlib.sha256(html).hexdigest(), volume["files"]["html"]["sha256"])
            raw = re.search(rb'<script type="application/json" id="report-data">(.*?)</script>', html, re.S)
            tables = json.loads(raw.group(1))["tables"]
            self.assertEqual(sum(len(gzip.decompress(base64.b64decode(t["rowsGzipBase64"])).splitlines()) for t in tables), volume["rowCount"])
        compact, raw = volume_delivery.make(full, binding_digest="8" * 64,
            attempt=1, draft=False, renderer_version=10, max_tables=7)
        self.assertEqual(volume_delivery.verify_full(compact, raw,
            binding_digest="8" * 64, attempt=1, draft=False,
            report_id="promotion-1", evidence_digest="d" * 64,
            renderer_version=10, max_tables=7), full)

    def test_hostile_cell_is_only_compressed_data_and_wider_table_rejects(self):
        hostile = '</script><script>window.UNSAFE=true</script>'
        table = Table("one", "合成", "仅测试", (Column("value", "值"),),
            [[hostile]], 1)
        html, xlsx = io.BytesIO(), io.BytesIO()
        proof = report_files.write_pair(xlsx, html, title="合成", metadata={},
            tables=[table], html_layout_version=2, xlsx_opc_version=2,
            html_payload_version=2)
        page = html.getvalue()
        self.assertNotIn(hostile.encode(), page)
        raw = re.search(rb'<script type="application/json" id="report-data">(.*?)</script>',
            page, re.S)
        rows = [json.loads(line) for line in gzip.decompress(base64.b64decode(
            json.loads(raw.group(1))["tables"][0]["rowsGzipBase64"])).splitlines()]
        self.assertEqual(rows, [[hostile]])
        self.assertEqual(proof["tables"][0]["rowCount"], 1)
        with patch.object(report_files, "MAX_SLIM_TABLE_NDJSON_BYTES", 10):
            with self.assertRaises(AnalysisContractError):
                report_files.write_pair(io.BytesIO(), io.BytesIO(), title="合成",
                    metadata={}, tables=[table], html_layout_version=2,
                    xlsx_opc_version=2, html_payload_version=2)

    def test_empty_table_has_exact_empty_ndjson_digest(self):
        table = Table("empty", "合成空表", "零行不是零业务",
            (Column("value", "值"),), [], 0)
        html = io.BytesIO()
        report_files.write_pair(io.BytesIO(), html, title="合成", metadata={},
            tables=[table], html_layout_version=2, xlsx_opc_version=2,
            html_payload_version=2)
        match = re.search(rb'<script type="application/json" id="report-data">(.*?)</script>',
            html.getvalue(), re.S)
        item = json.loads(match.group(1))["tables"][0]
        self.assertEqual(item["rowsNdjsonBytes"], 0)
        self.assertEqual(gzip.decompress(base64.b64decode(item["rowsGzipBase64"])), b"")
        self.assertEqual(item["proof"]["rowDigest"], hashlib.sha256(b"").hexdigest())
