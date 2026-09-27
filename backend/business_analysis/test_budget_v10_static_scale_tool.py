"""Pure safety tests for the synthetic renderer-10 scale runner."""
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import zipfile

from . import volume_files
from .contracts import AnalysisContractError


TOOL = Path(__file__).resolve().parents[2] / "tools" / "business-budget-v10-static-scale.py"
SPEC = importlib.util.spec_from_file_location("budget_v10_static_scale", TOOL)
assert SPEC and SPEC.loader
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


class BudgetV10StaticScaleToolTests(TestCase):
    def test_small_complete_synthetic_run_checks_proofs_opc_and_formulas(self):
        with TemporaryDirectory() as base:
            output = Path(base) / "new-output"
            evidence = tool.run(output, rows=3)
            self.assertEqual(evidence["syntheticPromotionRows"], 3)
            self.assertTrue(evidence["completeManifestVerified"])
            self.assertTrue(evidence["allFileHashesVerified"])
            self.assertTrue(evidence["allZipMembersParsedAndCrcChecked"])
            self.assertFalse(evidence["owningSourceAuthorityVerified"])
            self.assertFalse(evidence["nativeExcelOpened"])
            self.assertEqual(evidence["sheets"][0]["sheets"][3]["rowsIncludingHeaders"], 6)
            self.assertGreater(sum(sheet["formulaCount"] for sheet in
                evidence["sheets"][0]["sheets"][-3:]), 0)
            self.assertTrue((output / "complete-manifest.json").exists())
            with self.assertRaises(FileExistsError):
                tool.run(output, rows=3)

    def test_tampered_budget_proof_fails_before_stream_or_source_read(self):
        source, candidate, tables, request, plan, metadata = tool.prepared(3)
        metadata["promotionBudgetProof"]["offlinePayloadDigest"] = "0" * 64
        outputs = [volume_files.VolumeStreams(io.BytesIO(), io.BytesIO())]
        with self.assertRaises(AnalysisContractError):
            volume_files.render(tables, outputs, report_id=request["reportId"],
                evidence_digest=request["evidenceDigest"], renderer_version=10,
                plan=plan, title="合成门禁", metadata=metadata,
                offline_budget=candidate.offline_budget,
                excel_budget=candidate.excel_budget)
        self.assertEqual(source.consumed, 0)
        self.assertEqual(outputs[0].xlsx.getvalue(), b"")
        self.assertEqual(outputs[0].html.getvalue(), b"")

    def test_explicit_fragment_capacity_preserves_all_synthetic_rows(self):
        with TemporaryDirectory() as base:
            evidence = tool.run(Path(base) / "split", rows=7, max_rows=3)
            self.assertEqual(evidence["syntheticPromotionRows"], 7)
            self.assertEqual(evidence["volumeCount"], 1)
            self.assertEqual([sheet["rowsIncludingHeaders"] for sheet in
                evidence["sheets"][0]["sheets"][3:6]], [6, 6, 4])
            self.assertTrue(evidence["completeManifestVerified"])

    def test_formula_text_tamper_fails_even_with_valid_zip_crc(self):
        with TemporaryDirectory() as base:
            output = Path(base) / "original"
            tool.run(output, rows=3)
            full = json.loads((output / "complete-manifest.json").read_text(encoding="utf-8"))
            _, candidate, *_ = tool.prepared(3)
            changed = Path(base) / "changed.xlsx"
            with (zipfile.ZipFile(output / "volume-001.xlsx") as original,
                    zipfile.ZipFile(changed, "w", zipfile.ZIP_DEFLATED) as target):
                for member in original.namelist():
                    data = original.read(member)
                    if member == "xl/worksheets/sheet10.xml":
                        self.assertIn(b"<f>", data)
                        data = data.replace(b"<f>", b"<f>1+", 1)
                    target.writestr(member, data)
            with self.assertRaises(AssertionError):
                tool.inspect_xlsx(changed, full["volumes"][0],
                    expected_large_rows=3, budget_payload=candidate.excel_budget)

    def test_over_capacity_rejects_without_truncating_source(self):
        source, candidate, tables, request, plan, metadata = tool.prepared(3)
        outputs = [volume_files.VolumeStreams(io.BytesIO(), io.BytesIO())]
        with self.assertRaises(AnalysisContractError):
            volume_files.render(tables, outputs, report_id=request["reportId"],
                evidence_digest=request["evidenceDigest"], renderer_version=10,
                plan=plan, title="合成门禁", metadata=metadata,
                offline_budget=candidate.offline_budget,
                excel_budget=candidate.excel_budget, max_file_bytes=1024)
        self.assertNotEqual(source.consumed, 3)
