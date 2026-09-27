"""Market numeric candidates never promote TOP samples to own sales."""
from copy import deepcopy
from unittest import TestCase

from . import market_dynamics, market_dynamics_v2, market_numeric_claims as claims
from .contracts import AnalysisContractError, digest
from .test_market_dynamics import BANDS, fixture
from ai_assistant.test_business_promotion_market_runtime_contract import inputs, source


def price():
    value = market_dynamics.price_band(*fixture(), BANDS)
    row = {**value["rows"][0], "rowIndex": 0}
    ref = {"jobId": "job-1", "role": "market_b2b", "reportId": "report-1",
        "sourceKey": "market-current", "view": "price_band",
        "bandsDigest": digest(BANDS), "bandKey": row["bandKey"],
        "tableBindingDigest": value["tableDigest"], "rowIndex": 0,
        "rowId": row["rowId"], "metric": "sampleGmvLowerCents",
        "field": "value", "attribution": claims.ATTRIBUTION}
    return ref, row


def rank(*, missing_day=False, top_absent=False):
    current, baseline, selector, _ = inputs()
    if missing_day:
        baseline = source("previous", ("Z",), observe_last=False)
    elif top_absent:
        baseline = source("previous", ("C", "A"))
    value = market_dynamics_v2.rank_entry_exit(*current[:3], *baseline[:3],
        selector["currentObservationDate"], selector["baselineObservationDate"])
    row = next(item for item in value["rows"] if item["skuId"] == "A")
    row = {**row, "rowIndex": value["rows"].index(row)}
    ref = {"jobId": "job-1", "role": "market_b2b", "reportId": "report-1",
        "sourceKey": current[0]["key"], "view": "rank_entry_exit",
        "baselineKey": baseline[0]["key"],
        "currentObservationDate": selector["currentObservationDate"],
        "baselineObservationDate": selector["baselineObservationDate"],
        "tableBindingDigest": value["tableDigest"],
        "rowIndex": row["rowIndex"], "rowId": row["rowId"],
        "metric": "rankImprovement", "field": "value",
        "attribution": claims.ATTRIBUTION}
    return ref, row, value["observationCoverage"]


def receipt(ref):
    return {**ref, "schemaVersion": claims.RECEIPT_SCHEMA,
        "ownerEmail": "owner@example.invalid", "bindingDigest": "b"*64,
        "responseDigest": "c"*64, "signedRequestDigest": "d"*64}


class MarketNumericClaimsTests(TestCase):
    def test_same_job_role_report_and_exact_row_candidate_never_grant_authority(self):
        ref, row = price()
        value = claims.bind_read(ref, receipt(ref), job_id="job-1",
            role="market_b2b", report_id="report-1",
            owner_email="owner@example.invalid")
        self.assertFalse(value["verification"]["agentReadPersisted"])
        self.assertFalse(value["verification"]["signedTransportVerified"])
        self.assertFalse(value["verification"]["authorityVerified"])
        self.assertEqual(claims.number(ref, row)["value"], 10)
        for changed in ({**ref, "jobId": "job-2"},
                {**ref, "role": "promotion"}, {**ref, "reportId": "report-2"},
                {**ref, "sourceKey": "other"},
                {**ref, "tableBindingDigest": "0"*64},
                {**ref, "rowId": "0"*64}):
            with self.assertRaises(AnalysisContractError):
                claims.bind_read(changed, receipt(ref), job_id="job-1",
                    role="market_b2b", report_id="report-1",
                    owner_email="owner@example.invalid")

    def test_market_sample_cannot_be_claimed_as_own_shop_erp_or_b2b(self):
        ref, row = price()
        for changed in ({**ref, "attribution": "own_shop"},
                {**ref, "metric": "erpNetSalesCents"},
                {**ref, "metric": "b2bSalesCents"},
                {**ref, "value": 999},
                {**ref, "bandsDigest": "bad"},
                {**ref, "rowIndex": True}):
            with self.assertRaises(AnalysisContractError):
                claims.reference(changed)
        empty = deepcopy(row)
        empty["metrics"]["sampleGmvLowerCents"]["value"] = None
        with self.assertRaises(AnalysisContractError):
            claims.number(ref, empty)

    def test_rank_movement_only_both_observed_with_real_ranks(self):
        ref, row, covered = rank()
        self.assertEqual(claims.number(ref, row, observation_coverage=covered)["value"], 0)
        for missing_day, top_absent in ((True, False), (False, True)):
            other, observed_row, proof = rank(missing_day=missing_day,
                top_absent=top_absent)
            with self.assertRaises(AnalysisContractError):
                claims.number(other, observed_row, observation_coverage=proof)
            side = {**other, "metric": "sampleGmvLowerCents", "field": "baseline"}
            with self.assertRaises(AnalysisContractError):
                claims.number(side, observed_row, observation_coverage=proof)
        ref, row, covered = rank(missing_day=True)
        current = {**ref, "metric": "rank", "field": "current"}
        self.assertEqual(claims.number(current, row,
            observation_coverage=covered)["value"], row["current"]["rank"])
        bad = deepcopy(row); bad["baseline"]["status"] = "not_observed_in_top_sample"
        with self.assertRaises(AnalysisContractError):
            claims.number(current, bad, observation_coverage=covered)

    def test_receipt_cross_view_bands_date_and_owner_fail(self):
        ref, _, _ = rank()
        for key, replacement in (("baselineKey", "other"),
                ("currentObservationDate", "2026-09-02"),
                ("rowIndex", 7), ("metric", "rank")):
            wrong = receipt(ref); wrong[key] = replacement
            with self.assertRaises(AnalysisContractError):
                claims.bind_read(ref, wrong, job_id="job-1", role="market_b2b",
                    report_id="report-1", owner_email="owner@example.invalid")
        with self.assertRaises(AnalysisContractError):
            claims.bind_read(ref, receipt(ref), job_id="job-1", role="market_b2b",
                report_id="report-1", owner_email="other@example.invalid")
