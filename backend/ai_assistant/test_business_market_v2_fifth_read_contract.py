from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, digest
from . import business_market_v2_fifth_read_contract as contract


def call():
    return {"schemaVersion": contract.SCHEMA,
        "admittedReportId": "admitted-report", "jobId": "future-job",
        "providerDispatchId": "future-provider", "providerCallId": "call-1",
        "role": "market_b2b", "marketManifestDigest": "a"*64,
        "marketContextDigest": "b"*64}


def selector():
    return {"priceBandSourceKey": "market-current",
        "rankCurrentSourceKey": "market-current",
        "rankBaselineKey": "market-baseline",
        "bands": [{"key": "under-100", "lowerCents": 0,
            "upperExclusiveCents": 10000}],
        "currentObservationDate": "2026-08-31",
        "baselineObservationDate": "2026-07-31"}


class FifthReadContractTests(TestCase):
    def test_injected_identity_is_exact_and_not_authority(self):
        self.assertEqual(contract.injected(call()), call())
        for field, value in (("role", "commerce"), ("jobId", "admitted-report"),
                ("marketContextDigest", "wrong"),
                ("providerCallId", "\n")):
            changed = deepcopy(call()); changed[field] = value
            with self.subTest(field=field), self.assertRaises(AnalysisContractError):
                contract.injected(changed)

    def test_price_number_citation_keeps_market_attribution_and_no_receipt(self):
        row = {"rowIndex": 3, "rowId": "c"*64,
            "bandKey": "under-100", "metrics": {
                "sampleGmvLowerCents": {"value": 1250,
                    "presentRows": 2, "missingRows": 1}}}
        base = contract.citation_base(call(), selector(), "price_band", row,
            "d"*64)
        self.assertEqual(base["bandsDigest"], digest(selector()["bands"]))
        number = contract.verified_number(base, row,
            "sampleGmvLowerCents", "value", observation_coverage=None)
        self.assertEqual(number["number"], {"value": 1250, "partial": True,
            "population": "market_top_sample_only",
            "ownSalesAttributionVerified": False})
        self.assertEqual(number["reference"]["reportId"], "admitted-report")
        self.assertFalse(number["persistedRead"])
        bad = deepcopy(row); bad["metrics"]["sampleGmvLowerCents"]["value"] = None
        with self.assertRaises(AnalysisContractError):
            contract.verified_number(base, bad, "sampleGmvLowerCents", "value",
                observation_coverage=None)

    def test_rank_reference_binds_dates_and_rejects_cross_row(self):
        row = {"rowIndex": 0, "rowId": "e"*64}
        base = contract.citation_base(call(), selector(), "rank_entry_exit", row,
            "f"*64)
        self.assertEqual(base["baselineKey"], "market-baseline")
        self.assertEqual(base["currentObservationDate"], "2026-08-31")
        with self.assertRaises(AnalysisContractError):
            contract.verified_number(base, {**row, "rowId": "0"*64},
                "rank", "current", observation_coverage={
                    "currentDatePresent": True, "baselineDatePresent": True,
                    "bothDatesPresent": True})
