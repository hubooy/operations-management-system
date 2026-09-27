"""Pure renderer-11 temporary proof; no DB stage, ready or download."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import zipfile

from . import html_slim_payload_v11, report_files, volume_delivery, volume_files
from .contracts import AnalysisContractError, digest


TOOL = Path(__file__).resolve().parents[2] / "tools" / "business-budget-v10-static-scale.py"
SPEC = importlib.util.spec_from_file_location("v11_budget_scale_fixture", TOOL)
assert SPEC and SPEC.loader
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)
FROZEN_V10_HTML_SHA = "8f247be805cbb3aff19e5cca989bf6d1ccc6190ac35cf48aaf2f272ea795b7f8"


class BudgetV11SlimDeliveryTests(TestCase):
    def test_v11_is_separate_complete_candidate_with_same_workbook_xml(self):
        with TemporaryDirectory() as base:
            old, new = Path(base) / "v10", Path(base) / "v11"
            prior = tool.run(old, rows=3)
            latest = tool.run(new, rows=3, renderer_version=11)
            self.assertEqual(prior["rendererVersion"], 10)
            self.assertEqual(prior["files"][0]["sha256"], FROZEN_V10_HTML_SHA)
            self.assertEqual(latest["rendererVersion"], 11)
            self.assertEqual(latest["htmlPayloadVersion"], 2)
            full = json.loads((new / "complete-manifest.json").read_bytes())
            self.assertEqual(full["promotionBudgetProof"]["rendererVersion"], 10)
            slim = full["promotionSlimProof"]
            self.assertEqual((slim["rendererVersion"], slim["htmlPayloadVersion"],
                slim["candidateOnly"], slim["publicationStatus"]),
                (11, 2, True, "unpublished"))
            self.assertEqual(slim["sourceBudgetProofDigest"],
                full["promotionBudgetProof"]["proofDigest"])
            self.assertEqual(slim["browserRequirements"],
                volume_delivery.SLIM_BROWSER_REQUIREMENTS)
            self.assertEqual(html_slim_payload_v11.verify_file(new / "volume-001.html",
                full["volumes"][0])["rows"], full["volumes"][0]["rowCount"])
            with (zipfile.ZipFile(old / "volume-001.xlsx") as a,
                    zipfile.ZipFile(new / "volume-001.xlsx") as b):
                members = set(a.namelist()) | set(b.namelist())
                self.assertEqual(set(a.namelist()), set(b.namelist()))
                for name in members - {"teruisi-manifest.json"}:
                    self.assertEqual(a.read(name), b.read(name), name)

    def test_missing_or_modified_v11_slim_proof_rejects_full_manifest(self):
        with TemporaryDirectory() as base:
            folder = Path(base) / "v11"
            tool.run(folder, rows=3, renderer_version=11)
            full = json.loads((folder / "complete-manifest.json").read_bytes())
            for change in (lambda value: value.pop("promotionSlimProof"),
                    lambda value: value["promotionSlimProof"].update(htmlPayloadVersion=1),
                    lambda value: value["volumes"][0].pop("htmlPayload")):
                modified = deepcopy(full)
                change(modified)
                modified["manifestDigest"] = digest({key: child for key, child in
                    modified.items() if key != "manifestDigest"})
                with self.assertRaises((AnalysisContractError, KeyError)):
                    volume_delivery.make(modified, binding_digest="8" * 64,
                        attempt=1, draft=False, renderer_version=11)

    def test_re_signed_manifest_cannot_replace_actual_html_payload(self):
        with TemporaryDirectory() as base:
            folder = Path(base) / "v11"
            tool.run(folder, rows=3, renderer_version=11)
            full = json.loads((folder / "complete-manifest.json").read_bytes())
            changed = deepcopy(full)
            payload = changed["volumes"][0]["htmlPayload"]
            payload["tables"][0]["rowsGzipSha256"] = "0" * 64
            payload["proofDigest"] = digest({key: child for key, child in
                payload.items() if key != "proofDigest"})
            changed["promotionSlimProof"] = volume_delivery.slim_proof_v11(changed)
            changed["manifestDigest"] = digest({key: child for key, child in
                changed.items() if key != "manifestDigest"})
            # A self-consistent manifest is insufficient: actual HTML bytes
            # must be independently read and compared before any future stage.
            volume_delivery.make(changed, binding_digest="8" * 64,
                attempt=1, draft=False, renderer_version=11)
            with self.assertRaises(AnalysisContractError):
                html_slim_payload_v11.verify_file(folder / "volume-001.html",
                    changed["volumes"][0])

    def test_overwide_v11_candidate_has_no_complete_receipt(self):
        source, candidate, tables, request, plan, metadata = tool.prepared(3,
            renderer_version=11)
        output = volume_files.VolumeStreams(io.BytesIO(), io.BytesIO())
        with patch.object(report_files, "MAX_SLIM_TABLE_NDJSON_BYTES", 10):
            with self.assertRaises(AnalysisContractError):
                volume_files.render(tables, [output], report_id=request["reportId"],
                    evidence_digest=request["evidenceDigest"], renderer_version=11,
                    plan=plan, title="合成v11", metadata=metadata,
                    offline_budget=candidate.offline_budget,
                    excel_budget=candidate.excel_budget)
        self.assertEqual(source.consumed, 0)

    def test_v10_does_not_accept_v11_fields_or_opt_in_selector(self):
        with TemporaryDirectory() as base:
            folder = Path(base) / "v10"
            tool.run(folder, rows=3)
            old = json.loads((folder / "complete-manifest.json").read_bytes())
            modified = deepcopy(old)
            modified["promotionSlimProof"] = {"htmlPayloadVersion": 2}
            modified["manifestDigest"] = digest({key: child for key, child in
                modified.items() if key != "manifestDigest"})
            with self.assertRaises(AnalysisContractError):
                volume_delivery.make(modified, binding_digest="8" * 64,
                    attempt=1, draft=False, renderer_version=10)
        source, candidate, tables, request, plan, metadata = tool.prepared(3,
            renderer_version=11)
        output = volume_files.VolumeStreams(io.BytesIO(), io.BytesIO())
        with self.assertRaises(AnalysisContractError):
            volume_files.render(tables, [output], report_id=request["reportId"],
                evidence_digest=request["evidenceDigest"], renderer_version=11,
                plan=plan, title="合成v11", metadata=metadata,
                offline_budget=candidate.offline_budget,
                excel_budget=candidate.excel_budget, html_slim_v10=True)
        self.assertEqual(source.consumed, 0)
        self.assertEqual(output.html.getvalue(), b"")
