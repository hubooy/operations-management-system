"""Synthetic, no-database v11 full-byte assertion contract checks."""
from copy import deepcopy
from importlib import util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import zipfile

from . import (html_slim_payload_v11, promotion_budget_attestation_v11,
    volume_delivery, xlsx_opc_v11)
from .contracts import AnalysisContractError, canonical, digest


TOOL = Path(__file__).resolve().parents[2] / "tools" / "business-budget-v10-static-scale.py"
SPEC = util.spec_from_file_location("v11_attestation_fixture", TOOL)
assert SPEC and SPEC.loader
tool = util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


class BudgetV11AttestationPureTests(TestCase):
    def test_process_digest_is_a_claim_not_an_unforgeable_verifier_receipt(self):
        """A caller can choose valid-looking process hashes without running OPC.

        This pins the 0067 publication blocker, not an expected delivery path.
        SQL can authenticate the staged roots but cannot infer that these
        Python-only checks were actually performed from a plain SHA-256 value.
        """
        with TemporaryDirectory() as base:
            folder = Path(base) / "candidate"
            tool.run(folder, rows=3, renderer_version=11)
            raw = (folder / "complete-manifest.json").read_bytes()
            full = json.loads(raw)
            compact, rebuilt = volume_delivery.make(full,
                binding_digest="8" * 64, attempt=1, draft=False,
                renderer_version=11)
            self.assertEqual(raw, rebuilt)
            content = full["promotionFileProof"]
            result = promotion_budget_attestation_v11.compose(
                run_id="synthetic-run", attempt=1, run_version=4,
                binding_digest="8" * 64, compact_json=canonical(compact),
                full_json_bytes=raw, full_manifest=full,
                approved_content_digest=content["contentDtoDigest"],
                human_review_digest=content["humanReviewDigest"],
                budget_present=True,
                budget_plan_digest=full["budgetPlanDigest"],
                budget_roots_digest="a" * 64,
                report_snapshot_sha256="b" * 64,
                workflow_input_sha256="c" * 64, actor_version=1,
                file_byte_verification_digest="1" * 64,
                html_rows_digest="2" * 64,
                xlsx_opc_formula_digest="3" * 64,
                fresh_semantics_verified=True)
            body = json.loads(result["attestationText"])
            self.assertEqual(body["fileByteVerificationDigest"], "1" * 64)
            self.assertEqual(body["htmlRowsDigest"], "2" * 64)
            self.assertEqual(body["xlsxOpcFormulaDigest"], "3" * 64)
            self.assertFalse(result["databaseCanIndependentlyVerifyProcessAssertions"])
            self.assertFalse(result["readyAuthorized"])

    def test_opc_digest_changes_on_formula_text_and_rejects_active_member(self):
        with TemporaryDirectory() as base:
            folder = Path(base) / "candidate"
            tool.run(folder, rows=3, renderer_version=11)
            full = json.loads((folder / "complete-manifest.json").read_bytes())
            original = folder / "volume-001.xlsx"
            volume = full["volumes"][0]
            before = xlsx_opc_v11.inspect(original, volume)
            changed = Path(base) / "formula-changed.xlsx"
            with (zipfile.ZipFile(original) as source,
                    zipfile.ZipFile(changed, "w", zipfile.ZIP_DEFLATED) as target):
                for name in source.namelist():
                    raw = source.read(name)
                    if name.startswith("xl/worksheets/sheet") and b"<f>" in raw:
                        raw = raw.replace(b"<f>", b"<f>1+", 1)
                    target.writestr(name, raw)
            after = xlsx_opc_v11.inspect(changed, volume)
            self.assertNotEqual(before["formulaDigest"], after["formulaDigest"])
            self.assertNotEqual(before["memberDigest"], after["memberDigest"])
            active = Path(base) / "active.xlsx"
            with (zipfile.ZipFile(original) as source,
                    zipfile.ZipFile(active, "w", zipfile.ZIP_DEFLATED) as target):
                for name in source.namelist():
                    target.writestr(name, source.read(name))
                target.writestr("xl/vbaProject.bin", b"active")
            with self.assertRaises(AnalysisContractError):
                xlsx_opc_v11.inspect(active, volume)

    def test_synthetic_full_byte_html_opc_and_formula_assertion(self):
        with TemporaryDirectory() as base:
            folder = Path(base) / "candidate"
            tool.run(folder, rows=3, renderer_version=11)
            raw = (folder / "complete-manifest.json").read_bytes()
            full = json.loads(raw)
            compact, rebuilt = volume_delivery.make(full,
                binding_digest="8" * 64, attempt=1, draft=False,
                renderer_version=11)
            self.assertEqual(raw, rebuilt)
            self.assertEqual(html_slim_payload_v11.verify_file(
                folder / "volume-001.html", full["volumes"][0])["rows"],
                full["volumes"][0]["rowCount"])
            opc = xlsx_opc_v11.inspect(folder / "volume-001.xlsx",
                full["volumes"][0])
            self.assertGreater(opc["sheetCount"], 3)
            content = full["promotionFileProof"]
            result = promotion_budget_attestation_v11.compose(
                run_id="synthetic-run", attempt=1, run_version=4,
                binding_digest="8" * 64, compact_json=canonical(compact),
                full_json_bytes=raw, full_manifest=full,
                approved_content_digest=content["contentDtoDigest"],
                human_review_digest=content["humanReviewDigest"],
                budget_present=True,
                budget_plan_digest=full["budgetPlanDigest"],
                budget_roots_digest="a" * 64,
                report_snapshot_sha256="b" * 64,
                workflow_input_sha256="c" * 64, actor_version=1,
                file_byte_verification_digest=digest(opc),
                html_rows_digest=digest(full["volumes"][0]["htmlPayload"]),
                xlsx_opc_formula_digest=opc["formulaDigest"],
                fresh_semantics_verified=True)
            body = json.loads(result["attestationText"])
            self.assertEqual(body["slimProofDigest"],
                full["promotionSlimProof"]["proofDigest"])
            self.assertFalse(result["readyAuthorized"])
            changed = deepcopy(full)
            changed["promotionSlimProof"]["sourceBudgetProofDigest"] = "0" * 64
            with self.assertRaises(AnalysisContractError):
                promotion_budget_attestation_v11.compose(
                    run_id="synthetic-run", attempt=1, run_version=4,
                    binding_digest="8" * 64,
                    compact_json=canonical(compact), full_json_bytes=raw,
                    full_manifest=changed,
                    approved_content_digest=content["contentDtoDigest"],
                    human_review_digest=content["humanReviewDigest"],
                    budget_present=True,
                    budget_plan_digest=full["budgetPlanDigest"],
                    budget_roots_digest="a" * 64,
                    report_snapshot_sha256="b" * 64,
                    workflow_input_sha256="c" * 64, actor_version=1,
                    file_byte_verification_digest=digest(opc),
                    html_rows_digest=digest(full["volumes"][0]["htmlPayload"]),
                    xlsx_opc_formula_digest=opc["formulaDigest"],
                    fresh_semantics_verified=True)
