"""Pure renderer-7 proof tests; no database, producer, or ready transition."""
from copy import deepcopy
import hashlib
import unittest

from business_analysis.contracts import digest
from . import business_promotion_content_contract as content
from . import business_promotion_file_proof as proof
from .test_business_promotion_content_contract import fixture as content_fixture


def materials(binding, *, baseline=True):
    source, previous = "ads", "ads-prior" if baseline else None
    report = {"reportId": binding["reportId"], "workflowId": binding["workflowId"],
        "ownerEmail": binding["ownerEmail"], "scope": None, "role": "admin",
        "evidenceRunId": binding["evidenceRunId"],
        "evidenceVersion": binding["evidenceVersion"],
        "sealedDigest": binding["sealedDigest"],
        "snapshotDigest": binding["snapshotDigest"],
        "workflowInputDigest": binding["workflowInputDigest"],
        "executionProfile": content.PROFILE,
        "budgetRef": {"planDigest": binding["budgetPlanDigest"]}
            if "budgetPlanDigest" in binding else None}
    tables = []
    for index, view in enumerate(content.VIEWS):
        table_digest = str(index+3)*64
        raw = (view+"\n").encode("utf-8")
        tables.append({"view": view, "binding": {"schemaVersion": proof.BINDING_SCHEMA,
            "reportBinding": report, "sourceKey": source, "baselineKey": previous,
            "view": view, "algorithmVersion": content.ALGORITHM,
            "tableBindingDigest": table_digest},
            "header": {"schemaVersion": "business-promotion-keyword-sku-table-v1",
                "view": view, "algorithmVersion": content.ALGORITHM,
                "authorityVerified": False, "total": 2,
                "tableBindingDigest": table_digest,
                "source": {"key": source},
                "baselineSource": {"key": previous} if previous else None},
            "rowCount": 2, "pageCount": 1, "ndjsonBytes": len(raw),
            "ndjsonSha256": hashlib.sha256(raw).hexdigest(),
            "spendTotals": {"current": {"value": 100, "presentGroups": 2, "missingFactRows": 1},
                "baseline": {"value": 80, "presentGroups": 2, "missingFactRows": 0}
                    if previous else {"value": None, "presentGroups": 0, "missingFactRows": 0}},
            "missingPromotedSkuGroups": 1,
            "unqualifiedIdentityGroups": 1})
    result = {"schemaVersion": proof.MATERIAL_SCHEMA,
        "reportBinding": report, "sourceKey": source, "baselineKey": previous,
        "algorithmVersion": content.ALGORITHM, "tables": tables,
        "rowCount": 4, "ndjsonBytes": sum(item["ndjsonBytes"] for item in tables),
        "tableExpensesAreAdditive": False, "registeredRenderer": False,
        "limitations": ["两表是同一推广事实的不同分组，花费不可相加。",
            "缺推广SKU桶保留原金额，不代表可操作的自家商品。"]}
    result["manifestDigest"] = digest(result)
    return result


def reseal(material):
    material["manifestDigest"] = digest({key: value for key, value in material.items()
        if key != "manifestDigest"})
    return material


class PromotionFileProofTests(unittest.TestCase):
    def ready_inputs(self, *, baseline=True, budget=False):
        binding, value = content_fixture(baseline=baseline, budget=budget, approved=True)
        return content.prepare(binding, value), materials(binding, baseline=baseline)

    def test_formal_two_view_proof_is_bounded_detached_and_non_authoritative(self):
        for baseline, budget in ((True, False), (False, True)):
            with self.subTest(baseline=baseline, budget=budget):
                complete, source = self.ready_inputs(baseline=baseline, budget=budget)
                prepared = proof.prepare(complete, source)
                result = prepared.value
                self.assertEqual(result, proof.check(result, complete, source))
                self.assertEqual(result["rendererVersion"], 7)
                self.assertFalse(result["authorityVerified"])
                self.assertFalse(result["registered"])
                self.assertFalse(result["tableExpensesAreAdditive"])
                self.assertEqual([item["view"] for item in result["tables"]], list(content.VIEWS))
                self.assertEqual(result["contentDtoDigest"], complete.value["dtoDigest"])
                self.assertEqual(result["materialManifestDigest"], source["manifestDigest"])
                self.assertEqual(result["tables"][0]["spendTotals"]["current"]["value"], 100)
                source["tables"][0]["rowCount"] = 99
                result["tables"][0]["rowCount"] = 99
                self.assertEqual(prepared.value["tables"][0]["rowCount"], 2)
        with self.assertRaises(proof.FileProofError): proof.PreparedFileProof(None, {})

    def test_report_roots_source_baseline_algorithm_and_approval_must_match(self):
        complete, source = self.ready_inputs()
        for change in (
            lambda m: m.update(sourceKey="other"),
            lambda m: m.update(baselineKey="other"),
            lambda m: m.update(algorithmVersion="other"),
            lambda m: m["reportBinding"].update(reportId="other"),
            lambda m: m["reportBinding"].update(snapshotDigest="0"*64),
            lambda m: m["reportBinding"].update(workflowInputDigest="0"*64),
            lambda m: m["reportBinding"].update(evidenceRunId="other"),
            lambda m: m["reportBinding"].update(ownerEmail="other@example.invalid"),
            lambda m: m["reportBinding"].update(budgetRef={"planDigest": "0"*64}),
        ):
            changed = deepcopy(source); change(changed); reseal(changed)
            with self.subTest(change=change), self.assertRaises(proof.FileProofError):
                proof.prepare(complete, changed)
        binding, value = content_fixture(approved=False)
        with self.assertRaises(proof.FileProofError):
            proof.prepare(content.prepare(binding, value), source)

    def test_full_material_order_hash_rows_identity_and_expenses_are_not_additive(self):
        complete, source = self.ready_inputs()
        for change in (
            lambda m: m.update(tableExpensesAreAdditive=True),
            lambda m: m.update(registeredRenderer=True),
            lambda m: m["tables"].reverse(),
            lambda m: m["tables"][1]["spendTotals"]["current"].update(value=200),
            lambda m: m["tables"][1]["spendTotals"]["baseline"].update(missingFactRows=3),
            lambda m: m["tables"][0].update(missingPromotedSkuGroups=3),
            lambda m: m["tables"][0]["binding"].update(tableBindingDigest="0"*64),
            lambda m: m["tables"][0]["header"].update(total=1),
            lambda m: m["tables"][0].update(ndjsonSha256="bad"),
            lambda m: m.update(rowCount=3),
            lambda m: m["limitations"].clear(),
        ):
            changed = deepcopy(source); change(changed); reseal(changed)
            with self.subTest(change=change), self.assertRaises(proof.FileProofError):
                proof.prepare(complete, changed)
        changed = deepcopy(source); changed["tables"][0]["rowCount"] = 1
        with self.assertRaises(proof.FileProofError): proof.prepare(complete, changed)

    def test_tampered_fragment_or_oversized_material_rejected(self):
        complete, source = self.ready_inputs()
        result = proof.prepare(complete, source).value
        for change in (
            lambda v: v.update(contentDtoDigest="0"*64),
            lambda v: v.update(materialManifestDigest="0"*64),
            lambda v: v.update(tableExpensesAreAdditive=True),
            lambda v: v.update(registered=True),
            lambda v: v["tables"][0].update(rowCount=999),
        ):
            changed = deepcopy(result); change(changed)
            changed["proofDigest"] = digest({key: value for key, value in changed.items()
                if key != "proofDigest"})
            with self.subTest(change=change), self.assertRaises(proof.FileProofError):
                proof.check(changed, complete, source)
        changed = deepcopy(source)
        changed["limitations"].append("x" * (proof.MAX_MATERIAL_BYTES + 1))
        reseal(changed)
        with self.assertRaises(proof.FileProofError): proof.prepare(complete, changed)


if __name__ == "__main__":
    unittest.main()
