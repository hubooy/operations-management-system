from copy import deepcopy
from unittest import TestCase

from .contracts import AnalysisContractError, digest
from . import evidence_seal_v3 as seal


def proof(key, ordinal, domain, coverage):
    return {"sourceKey": key, "ordinal": ordinal, "domain": domain,
        "queryDigest": "a" * 64, "sourceVersion": 2, "checkpointDigest": "b" * 64,
        "pageCount": 1, "rowCount": 0, "storedBytes": 100,
        "sourceRef": "c" * 64, "sourceRevision": "1:2", "receiptCount": 1,
        "receiptChainDigest": "d" * 64, "coverage": coverage}


class EvidenceSealV3Tests(TestCase):
    def test_daily_date_bitmap_discloses_observation_not_zero(self):
        query = {"startDate": "2026-08-20", "endDate": "2026-08-22", "window": "current"}
        value = seal.date_observations(query, [{"items": [{"date": "2026-08-20"}]}])
        self.assertEqual((value["presentBits"], value["missingRowDateCount"],
                          value["observedRowCutoffDate"]), ("100", 2, "2026-08-20"))
        self.assertIn("不能解释为零", value["meaning"])
        snapshot = seal.date_observations(query, [{"items": [{}]}], snapshot=True)
        self.assertIsNone(snapshot["missingRowDateCount"])
        previous = seal.date_observations({**query, "window": "previous"},
            [{"items": [{"date": "2026-08-17"}]}])
        self.assertEqual(previous["presentBits"], "100")

    def test_month_publication_preserves_missing_months_without_proration(self):
        query = {"months": ["2026-08", "2026-09"]}
        complete = {"publication": {"months": [{"month": "2026-08"}], "batches": [1],
            "missingMonths": ["2026-09"]}, "coverage": []}
        value = seal.finance_months(query, complete)
        self.assertEqual((value["publishedBits"], value["missingMonths"]), ("10", ["2026-09"]))
        self.assertIn("不作零值", value["meaning"])
        altered = deepcopy(complete); altered["publication"]["missingMonths"] = []
        with self.assertRaises(AnalysisContractError): seal.finance_months(query, altered)

    def test_seal_is_digest_bound_and_remains_non_authorizing(self):
        daily = proof("sales", 1, "sales", {"kind": "observed_row_dates"})
        finance = proof("finance", 2, "finance", {"kind": "natural_month_publication"})
        result = seal.make(run_id="evidence-1", evidence_version=3,
            plan_digest="1" * 64, catalog_digest="2" * 64,
            sources=[daily, finance], stored_bytes=200)
        self.assertEqual(result["sealedDigest"], digest({k: v for k, v in result.items() if k != "sealedDigest"}))
        self.assertFalse(result["sourceAuthorityVerified"])
        self.assertFalse(result["reportGenerationSupported"])
        with self.assertRaises(AnalysisContractError):
            seal.make(run_id="evidence-1", evidence_version=3,
                plan_digest="1" * 64, catalog_digest="2" * 64,
                sources=[daily, {**finance, "receiptCount": 0}], stored_bytes=200)
        for malformed in ({"sourceKey": []}, {"ordinal": True}, {"domain": []},
                          {"pageCount": True}, {"receiptCount": True},
                          {"sourceVersion": True}, {"coverage": {"kind": []}}):
            with self.subTest(malformed=malformed), self.assertRaises(AnalysisContractError):
                seal.make(run_id="evidence-1", evidence_version=3,
                    plan_digest="1" * 64, catalog_digest="2" * 64,
                    sources=[{**daily, **malformed}, finance], stored_bytes=200)
