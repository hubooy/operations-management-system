"""Renderer-7 pure volume contract, with frozen 4/6 behavior untouched."""
from copy import deepcopy
import io
import json
import unittest
import zipfile

from . import report_files, volume_delivery, volume_files, volume_plan
from ai_assistant.business_promotion_content_contract import prepare as content_prepare
from ai_assistant.business_promotion_file_proof import prepare as proof_prepare
from ai_assistant.test_business_promotion_content_contract import fixture as content_fixture
from ai_assistant.test_business_promotion_file_proof import materials


def valid():
    binding, content = content_fixture(approved=True)
    proof = proof_prepare(content_prepare(binding, content), materials(binding)).value
    tables = [report_files.Table("test", "合成", "不含真实来源", (report_files.Column("value", "值", "integer"),),
        [[7], [11]], 2)]
    request = volume_files.request_for(tables, report_id=binding["reportId"],
        evidence_digest=binding["sealedDigest"], renderer_version=7)
    plan = volume_plan.build(request)
    outputs = [volume_files.VolumeStreams(io.BytesIO(), io.BytesIO()) for _ in plan["volumes"]]
    return binding, proof, tables, plan, outputs


class PromotionVolumeDeliveryTests(unittest.TestCase):
    def test_seven_uses_opc2_and_binds_full_manifest_then_compact(self):
        binding, proof, tables, plan, outputs = valid()
        full = volume_files.render(tables, outputs, report_id=binding["reportId"],
            evidence_digest=binding["sealedDigest"], renderer_version=7, plan=plan,
            title="候选多卷", metadata={"promotionFileProof": proof})
        self.assertEqual(full["promotionFileProof"], proof)
        with zipfile.ZipFile(outputs[0].xlsx) as archive:
            self.assertIn(b'Extension="json" ContentType="application/json"',
                archive.read("[Content_Types].xml"))
        compact, raw = volume_delivery.make(full, binding_digest="8"*64,
            attempt=1, draft=False, renderer_version=7)
        self.assertEqual(volume_delivery.verify_full(compact, raw,
            binding_digest="8"*64, attempt=1, draft=False,
            report_id=binding["reportId"], evidence_digest=binding["sealedDigest"],
            renderer_version=7), full)
        self.assertEqual(json.loads(raw)["promotionFileProof"]["proofDigest"], proof["proofDigest"])

    def test_seven_requires_proof_and_four_six_reject_it(self):
        binding, proof, tables, plan, outputs = valid()
        with self.assertRaises(Exception):
            volume_files.render(tables, outputs, report_id=binding["reportId"],
                evidence_digest=binding["sealedDigest"], renderer_version=7,
                plan=plan, title="候选", metadata={})
        for version in (4, 6):
            request = volume_files.request_for(tables, report_id=binding["reportId"],
                evidence_digest=binding["sealedDigest"], renderer_version=version)
            old_plan = volume_plan.build(request)
            old_outputs = [volume_files.VolumeStreams(io.BytesIO(), io.BytesIO())]
            old = volume_files.render(tables, old_outputs, report_id=binding["reportId"],
                evidence_digest=binding["sealedDigest"], renderer_version=version,
                plan=old_plan, title="旧版", metadata={})
            tampered = deepcopy(old)
            tampered["promotionFileProof"] = proof
            tampered["manifestDigest"] = volume_delivery.digest({k:v for k,v in tampered.items() if k != "manifestDigest"})
            with self.subTest(version=version), self.assertRaises(Exception):
                volume_delivery.make(tampered, binding_digest="8"*64, attempt=1,
                    draft=False, renderer_version=version)

    def test_proof_report_or_expense_tamper_rejected_even_with_new_digest(self):
        binding, proof, tables, plan, outputs = valid()
        full = volume_files.render(tables, outputs, report_id=binding["reportId"],
            evidence_digest=binding["sealedDigest"], renderer_version=7,
            plan=plan, title="候选", metadata={"promotionFileProof": proof})
        for change in (lambda p: p.update(reportId="other"),
                       lambda p: p.update(tableExpensesAreAdditive=True),
                       lambda p: p["tables"][1]["spendTotals"]["current"].update(value=999),
                       lambda p: p.update(authorityVerified=True)):
            changed = deepcopy(full)
            change(changed["promotionFileProof"])
            changed["promotionFileProof"]["proofDigest"] = volume_delivery.digest({k:v for k,v in changed["promotionFileProof"].items() if k != "proofDigest"})
            changed["manifestDigest"] = volume_delivery.digest({k:v for k,v in changed.items() if k != "manifestDigest"})
            with self.subTest(change=change), self.assertRaises(Exception):
                volume_delivery.make(changed, binding_digest="8"*64, attempt=1,
                    draft=False, renderer_version=7)
