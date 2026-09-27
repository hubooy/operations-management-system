"""Pure future-profile market selection cannot grant source authority."""
from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, PageReconciler, comparison_periods, coverage, digest
from business_analysis.test_market_dynamics import BANDS, fixture
from . import business_promotion_market_runtime_contract as contract


RANGE = ("2026-09-01", "2026-09-03")
CONTEXT = {"reportId": "report-1", "runId": "evidence-1",
    "screeningId": "screening-1", "sealedDigest": "a"*64}


def source(window, identities, *, dimension="SKU", observe_last=True):
    rows = [{"skuId": identity} if dimension == "SKU" else {"spuId": identity}
        for identity in identities]
    entry, pages, _ = fixture(rows, window=window, dimension=dimension, days=RANGE)
    periods = comparison_periods(*RANGE)
    first, last = periods[window]["startDate"], periods[window]["endDate"]
    for index, row in enumerate(pages[0]["items"]):
        row["date"] = last if observe_last and index == 0 else first
    page = pages[0]
    page["coverage"] = coverage(periods[window], {row["date"] for row in page["items"]})
    page["pageEvidence"] = {"rowCount": len(page["items"]), "sha256": digest(page["items"])}
    verifier = PageReconciler(); verifier.consume(page)
    expected = verifier.result()
    proof = {"sourceKey": entry["key"], "sourceRef": expected["sourceRef"],
        "evidenceDigest": expected["evidenceDigest"], "rowCount": expected["rowCount"],
        "reconciled": True, "coverage": page["coverage"]}
    return entry, pages, expected, proof


def inputs():
    current = source("current", ("A", "B"))
    baseline = source("previous", ("A", "C"))
    selector = {"priceBandSourceKey": current[0]["key"],
        "rankCurrentSourceKey": current[0]["key"],
        "rankBaselineKey": baseline[0]["key"], "bands": deepcopy(BANDS),
        "currentObservationDate": "2026-09-03",
        "baselineObservationDate": "2026-08-31"}
    proofs = {current[0]["key"]: current[3], baseline[0]["key"]: baseline[3]}
    return current, baseline, selector, proofs


class MarketSelectionCandidateTests(TestCase):
    def test_interval_source_and_exact_observation_days_are_candidate_only(self):
        current, baseline, selector, proofs = inputs()
        value = contract.prepare_candidate([current[0], baseline[0]], CONTEXT,
            selector, proofs)
        self.assertEqual(value["sources"]["current"]["query"]["startDate"], RANGE[0])
        self.assertEqual(value["observationCoverage"], {"currentDatePresent": True,
            "baselineDatePresent": True, "bothDatesPresent": True})
        self.assertFalse(value["authorityVerified"])
        self.assertFalse(value["sourceCoverageVerified"])
        self.assertFalse(value["registered"])
        self.assertEqual(contract.check_candidate([current[0], baseline[0]], CONTEXT,
            selector, proofs, value), value)

    def test_wrong_grain_missing_baseline_and_cross_report_reject(self):
        current, baseline, selector, proofs = inputs()
        wrong = source("previous", ("A",), dimension="SPU")
        with self.assertRaises(AnalysisContractError):
            contract.prepare_candidate([current[0], wrong[0]], CONTEXT,
                {**selector, "rankBaselineKey": wrong[0]["key"]},
                {current[0]["key"]: current[3], wrong[0]["key"]: wrong[3]})
        for changed in ({key: value for key, value in selector.items() if key != "rankBaselineKey"},
                        {**selector, "rankBaselineKey": "foreign"},
                        {**selector, "rankCurrentSourceKey": baseline[0]["key"]},
                        {**selector, "baselineObservationDate": "2026-08-30"}):
            with self.assertRaises(AnalysisContractError):
                contract.prepare_candidate([current[0], baseline[0]], CONTEXT, changed, proofs)
        candidate = contract.prepare_candidate([current[0], baseline[0]], CONTEXT, selector, proofs)
        with self.assertRaises(AnalysisContractError):
            contract.check_candidate([current[0], baseline[0]],
                {**CONTEXT, "reportId": "other-report"}, selector, proofs, candidate)

    def test_coverage_claim_is_coherent_but_never_authority(self):
        current, baseline, selector, proofs = inputs()
        wrong = deepcopy(proofs)
        wrong[baseline[0]["key"]]["coverage"]["status"] = "dates_present"
        with self.assertRaises(AnalysisContractError):
            contract.prepare_candidate([current[0], baseline[0]], CONTEXT, selector, wrong)
        missing = source("previous", ())
        proofs[baseline[0]["key"]] = missing[3]
        value = contract.prepare_candidate([current[0], baseline[0]], CONTEXT,
            selector, proofs)
        self.assertFalse(value["observationCoverage"]["bothDatesPresent"])
        self.assertFalse(value["sourceCoverageVerified"])
        self.assertIn("不等于零销量", " ".join(value["limitations"]))

    def test_bands_and_source_proof_binding_fail_closed(self):
        current, baseline, selector, proofs = inputs()
        for bands in ([BANDS[1], BANDS[0]],
                      [{**BANDS[0], "upperExclusiveCents": 300}, BANDS[1]],
                      [{**BANDS[0], "lowerCents": True}, BANDS[1]]):
            with self.assertRaises(AnalysisContractError):
                contract.prepare_candidate([current[0], baseline[0]], CONTEXT,
                    {**selector, "bands": bands}, proofs)
        wrong = deepcopy(proofs); wrong[baseline[0]["key"]]["sourceKey"] = current[0]["key"]
        with self.assertRaises(AnalysisContractError):
            contract.prepare_candidate([current[0], baseline[0]], CONTEXT, selector, wrong)
