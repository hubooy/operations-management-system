"""Pure completed-content DTO tests; no Django setup, database or model calls."""
from copy import deepcopy
import unittest

from business_analysis.contracts import digest
from . import business_promotion_content_contract as contract


def fixture(*, baseline=True, budget=False, approved=False):
    selector = {"sourceKey": "ads", "views": list(contract.VIEWS)}
    if baseline: selector["baselineKey"] = "ads-prior"
    binding = {"schemaVersion": contract.BINDING_SCHEMA,
        "executionProfile": contract.PROFILE, "reportId": "report-1", "workflowId": "workflow-1",
        "evidenceRunId": "evidence-1", "evidenceVersion": 3,
        "sealedDigest": "a"*64, "snapshotDigest": "b"*64,
        "workflowInputDigest": "c"*64, "ledgerDigest": "d"*64,
        "screeningId": "screen-1", "screeningRootDigest": "e"*64,
        "ownerEmail": "owner@example.invalid", "scope": None,
        "promotionSelector": selector, "contextDigest": "f"*64,
        "promotionCatalogDigest": "1"*64,
        "promotionAlgorithmVersion": contract.ALGORITHM,
        "jobs": {}, "humanReview": {"status": "approved" if approved else "pending",
            "reviewDigest": "2"*64 if approved else None}}
    proofs = {}
    for index, role in enumerate(contract.ROLES):
        job_id = "job-"+str(index)
        proof = {"role": role, "jobId": job_id, "proof": {"pageCount": index+1,
            "ledgerRoot": str(index)*64}}
        binding["jobs"][role] = {"jobId": job_id, "outputDigest": str(index+3)*64,
            "readProofDigest": digest(proof)}
        proofs[role] = proof
    content = {"sections": [{"title": title, "body": title+"的完整分析"}
        for title in contract.SECTIONS],
        "diagnosis": {"summary": "核验后的整体判断", "findings": [{"id": "f-1", "kind": "gap"}]},
        "professionalAnalyses": {role: {"summary": role+"的诊断", "findings": []}
            for role in contract.SPECIALISTS},
        "independentReview": {"approved": True, "conflicts": [], "limitations": ["仍须人工批准"]},
        "screening": {"schemaVersion": contract.SCREENING_SCHEMA,
            "coverage": [{"kind": "requested", "value": {"sourceKey": "ads", "supported": True}}],
            "readProofs": proofs, "limitations": list(contract.LIMITATIONS),
            "candidateDisclosure": {"fullCandidatesIncluded": False,
                "crossPartitionAmountsAdditive": False}}}
    if budget:
        binding["budgetPlanDigest"] = "9"*64
        content["budget"] = {"planDigest": "9"*64, "limitations": ["情景是假设"]}
    return binding, content


def reseal(value):
    value["bindingDigest"] = digest(value["binding"])
    value["contentDigest"] = digest(value["content"])
    value["dtoDigest"] = digest({key: item for key, item in value.items() if key != "dtoDigest"})
    return value


class CompletedPromotionContentContractTests(unittest.TestCase):
    def test_fixed_five_roles_two_views_budget_and_immutable_detachment(self):
        for baseline, budget, approved in ((True, False, False), (False, True, True)):
            with self.subTest(baseline=baseline, budget=budget, approved=approved):
                binding, content = fixture(baseline=baseline, budget=budget, approved=approved)
                frozen = contract.prepare(binding, content)
                first = frozen.value
                self.assertEqual(first, contract.check(first))
                self.assertFalse(first["authorityVerified"])
                self.assertFalse(first["registered"])
                self.assertFalse(first["aggregationPolicy"]["viewSpendAdditive"])
                self.assertEqual(set(first["binding"]["jobs"]), set(contract.ROLES))
                self.assertEqual(first["contentDigest"], digest(first["content"]))
                binding["ownerEmail"] = "changed@example.invalid"
                content["sections"][0]["body"] = "被修改"
                first["content"]["sections"][0]["body"] = "调用方再次修改"
                self.assertNotEqual(frozen.value["content"]["sections"][0]["body"], "调用方再次修改")
                self.assertNotEqual(frozen.value["binding"]["ownerEmail"], binding["ownerEmail"])
        with self.assertRaises(contract.ContentContractError):
            contract.PreparedContent(None, {})

    def test_roles_jobs_read_proofs_budget_and_review_are_cross_checked(self):
        binding, content = fixture()
        for change in (
            lambda b,c: b["jobs"]["report"].update(jobId=b["jobs"]["promotion"]["jobId"]),
            lambda b,c: c["screening"]["readProofs"]["promotion"].update(jobId="other"),
            lambda b,c: c["screening"]["readProofs"].pop("market_b2b"),
            lambda b,c: c["screening"]["readProofs"]["report"]["proof"].update(pageCount=999),
            lambda b,c: c["independentReview"].update(approved=False),
            lambda b,c: c["independentReview"].update(conflicts=["未解决"]),
            lambda b,c: b["humanReview"].update(status="approved"),
            lambda b,c: b.update(budgetPlanDigest="8"*64),
            lambda b,c: c.update(budget={"planDigest": "8"*64}),
        ):
            changed = (deepcopy(binding), deepcopy(content))
            change(*changed)
            with self.subTest(change=change), self.assertRaises(contract.ContentContractError):
                contract.prepare(*changed)

    def test_source_selector_limitations_and_non_additive_policy(self):
        binding, content = fixture()
        for change in (
            lambda b,c: b["promotionSelector"].update(views=["keyword_sku"]),
            lambda b,c: b["promotionSelector"].update(baselineKey="ads"),
            lambda b,c: b["promotionSelector"].update(extra=True),
            lambda b,c: c["screening"]["limitations"].pop(0),
            lambda b,c: c["screening"]["candidateDisclosure"].update(crossPartitionAmountsAdditive=True),
            lambda b,c: b.update(scope={"shops": ["other"]}),
            lambda b,c: b.update(executionProfile="old"),
        ):
            changed = (deepcopy(binding), deepcopy(content)); change(*changed)
            with self.subTest(change=change), self.assertRaises(contract.ContentContractError):
                contract.prepare(*changed)
        valid = contract.prepare(*fixture()).value
        for change in (lambda d: d["aggregationPolicy"].update(viewSpendAdditive=True),
                       lambda d: d.update(authorityVerified=True),
                       lambda d: d.update(registered=True)):
            changed = deepcopy(valid); change(changed); reseal(changed)
            with self.assertRaises(contract.ContentContractError): contract.check(changed)

    def test_capacity_invalid_numbers_and_tampered_digests_fail_closed(self):
        binding, content = fixture()
        content["sections"][0]["body"] = "x" * (contract.MAX_BYTES + 1)
        with self.assertRaises(contract.ContentContractError): contract.prepare(binding, content)
        binding, content = fixture()
        content["diagnosis"]["findings"].append({"value": float("nan")})
        with self.assertRaises(contract.ContentContractError): contract.prepare(binding, content)
        binding, content = fixture()
        binding["evidenceVersion"] = True
        with self.assertRaises(contract.ContentContractError): contract.prepare(binding, content)
        valid = contract.prepare(*fixture()).value
        for key in ("bindingDigest", "contentDigest", "dtoDigest"):
            changed = deepcopy(valid); changed[key] = "0"*64
            with self.subTest(key=key), self.assertRaises(contract.ContentContractError): contract.check(changed)


if __name__ == "__main__":
    unittest.main()
