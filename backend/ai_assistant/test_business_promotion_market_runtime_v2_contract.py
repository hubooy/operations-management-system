"""Pure market-v2 fifth-tool graph and same-role citation requirements."""
from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, digest
from business_analysis import market_numeric_claims
from . import business_promotion_market_runtime_v2_contract as service
from . import business_promotion_market_runtime_contract as market
from . import business_promotion_runtime_contract as old
from .test_business_promotion_market_runtime_contract import inputs, CONTEXT


def admission():
    current, baseline, selector, proofs = inputs()
    candidate = market.prepare_candidate([current[0],baseline[0]],
        CONTEXT,selector,proofs)
    binding = {"schemaVersion":service.ADMISSION_SCHEMA,
        "reportId":CONTEXT["reportId"],"reportBindingDigest":"b"*64,
        "evidenceRunId":CONTEXT["runId"],"evidenceVersion":8,
        "sealedDigest":CONTEXT["sealedDigest"],
        "sourcesDigest":"c"*64,"sourceInfosDigest":"d"*64,
        "ownerEmail":"owner@example.invalid","actorVersion":1,
        "marketCandidateDigest":candidate["candidateDigest"],
        "selectedProofDigests":candidate["coverageProofDigests"]}
    value = {"schemaVersion":service.ADMISSION_SCHEMA,"binding":binding,
        "candidate":candidate,
        "observationStatus":{"current":"observed_date",
            "baseline":"observed_date"},"candidateEligible":True,
        "reportProfileRegistered":False,"sourceCoverageVerified":False,
        "marketRowsReplayed":False,"marketAndOwnSalesAdditive":False,
        "ownProductIdentityVerified":False,"authorityVerified":False,
        "limitations":["仅候选"]}
    value["bindingDigest"] = digest(value)
    return value


class MarketRuntimeV2ContractTests(TestCase):
    def test_five_roles_fifth_tool_and_v1_graph_bytes_unchanged(self):
        for budget in (False,True):
            before = digest(old.graph(budget))
            candidate = service.prepare(admission(),with_budget=budget)
            self.assertEqual(digest(old.graph(budget)),before)
            self.assertEqual(candidate["allowedTools"],
                [*old.TOOL_ORDER,service.MARKET_TOOL])
            self.assertEqual([node["key"] for node in candidate["graph"]["nodes"]
                if node["type"] == "agent"],list(service.ROLES))
            self.assertEqual(candidate["graphDigest"],digest(candidate["graph"]))
            self.assertFalse(candidate["authorityVerified"])
            self.assertFalse(candidate["agentReadPersisted"])
            self.assertFalse(candidate["marketRowsReadByAgent"])
            policy = candidate["roleReadPolicy"]
            self.assertTrue(policy["market_b2b"]["summaryRequired"])
            self.assertTrue(policy["market_b2b"]["firstPageForEachNonemptyViewRequired"])
            self.assertTrue(policy["independent_review"]["summaryRequired"])
            self.assertTrue(policy["report"]["summaryRequired"])
            self.assertFalse(policy["commerce"]["marketToolAllowed"])
            self.assertFalse(policy["promotion"]["marketToolAllowed"])
            self.assertFalse(policy["report"]["mayAttributeTopSampleToOwnSales"])
            text = next(node["instruction"] for node in candidate["graph"]["nodes"]
                if node["key"] == "market_b2b")
            self.assertIn(service.MARKET_TOOL,text)
            self.assertIn("缺日期、未入TOP和零销量",text)

    def test_missing_or_forged_admission_does_not_define_runtime(self):
        base = admission()
        changes = []
        wrong = deepcopy(base); wrong["candidateEligible"] = False; changes.append(wrong)
        wrong = deepcopy(base); wrong["observationStatus"]["baseline"] = "date_not_covered"; changes.append(wrong)
        wrong = deepcopy(base); wrong["binding"]["marketCandidateDigest"] = "0"*64; changes.append(wrong)
        wrong = deepcopy(base); wrong["candidate"]["selector"]["rankBaselineKey"] = "other"; changes.append(wrong)
        wrong = deepcopy(base); wrong["authorityVerified"] = True; changes.append(wrong)
        for bad in changes:
            bad["bindingDigest"] = digest({key:value for key,value in bad.items()
                if key != "bindingDigest"})
            with self.subTest(bad=bad["candidateEligible"]),self.assertRaises(AnalysisContractError):
                service.prepare(bad)

    def test_summary_page_row_have_fixed_selector_and_role(self):
        runtime = service.prepare(admission())
        base = {"reportId":runtime["reportId"],
            "marketContextDigest":runtime["marketContextDigest"]}
        for args in ({**base,"mode":"summary"},
                {**base,"mode":"page","view":"price_band","offset":0,"limit":20},
                {**base,"mode":"row","view":"rank_entry_exit",
                    "rowIndex":3,"rowId":"a"*64}):
            self.assertEqual(service.arguments(runtime,"market_b2b",args),args)
            with self.assertRaises(AnalysisContractError):
                service.arguments(runtime,"commerce",args)
        for bad in ({**base,"mode":"summary","sourceKey":"foreign"},
                {**base,"mode":"page","view":"price_band","offset":0,"limit":100},
                {**base,"mode":"page","view":"price_band","offset":True,"limit":20},
                {**base,"mode":"row","view":"rank_entry_exit",
                    "rowIndex":0,"rowId":"wrong"}):
            with self.assertRaises(AnalysisContractError):
                service.arguments(runtime,"market_b2b",bad)
        changed = deepcopy(runtime)
        changed["allowedTools"] = list(old.TOOL_ORDER)
        changed["runtimeDigest"] = digest({key:value for key,value in changed.items()
            if key != "runtimeDigest"})
        with self.assertRaises(AnalysisContractError):
            service.arguments(changed,"market_b2b",{**base,"mode":"summary"})

    def test_numeric_references_pin_own_job_selector_and_sample_attribution(self):
        runtime = service.prepare(admission())
        selector = runtime["marketSelector"]
        price = {"jobId":"job-1","role":"market_b2b",
            "reportId":runtime["reportId"],
            "sourceKey":selector["priceBandSourceKey"],"view":"price_band",
            "bandsDigest":digest(selector["bands"]),"bandKey":"low",
            "tableBindingDigest":"a"*64,"rowIndex":0,"rowId":"b"*64,
            "metric":"sampleGmvLowerCents","field":"value",
            "attribution":market_numeric_claims.ATTRIBUTION}
        rank = {"jobId":"job-1","role":"market_b2b",
            "reportId":runtime["reportId"],
            "sourceKey":selector["rankCurrentSourceKey"],"view":"rank_entry_exit",
            "baselineKey":selector["rankBaselineKey"],
            "currentObservationDate":selector["currentObservationDate"],
            "baselineObservationDate":selector["baselineObservationDate"],
            "tableBindingDigest":"a"*64,"rowIndex":1,"rowId":"c"*64,
            "metric":"rank","field":"current",
            "attribution":market_numeric_claims.ATTRIBUTION}
        for ref in (price,rank):
            result = service.reference_requirements(runtime,"market_b2b","job-1",ref)
            self.assertTrue(result["sameJobPersistedDispatchRequired"])
            self.assertTrue(result["owningReadRowRecomputationRequired"])
            self.assertFalse(result["agentReadPersisted"])
            self.assertFalse(result["ownSalesAttributionVerified"])
            with self.assertRaises(AnalysisContractError):
                service.reference_requirements(runtime,"market_b2b","job-2",ref)
        for bad in ({**price,"sourceKey":"other"},
                {**price,"bandsDigest":"0"*64},
                {**rank,"baselineKey":"other"},
                {**rank,"baselineObservationDate":"2026-08-30"},
                {**rank,"attribution":"own_shop_sales"}):
            with self.assertRaises(AnalysisContractError):
                service.reference_requirements(runtime,"market_b2b","job-1",bad)
