"""Pure v4 seal body canonicalization and policy invariants."""
import hashlib
from unittest import TestCase

from . import evidence_seal_v4 as seal
from .contracts import AnalysisContractError, canonical, digest


class EvidenceSealV4Tests(TestCase):
    def candidate(self):
        promotion = {"platform": "京东", "shop": "测试店",
            "dataset": "promotion", "startDate": "2026-08-20",
            "endDate": "2026-09-18", "window": "current"}
        finance = {"months": ["2026-08", "2026-09"],
            "scope": {"scope_key": "business", "scope_type": "business",
                "scope_name": "测试事业部", "group_name": ""},
            "analysisPeriod": {"startDate": "2026-08-20",
                "endDate": "2026-09-18"}}
        common = {"sourceVersion": 3, "pageCount": 2,
            "rowCount": 101, "storedBytes": 1024, "segmentCount": 1,
            "terminalSegmentDigest": "c" * 64,
            "receiptChainDigest": "d" * 64,
            "revisionFreshness": "current_revision"}
        sources = [{"sourceKey": "promotion-current", "domain": "netshop",
            "queryDigest": digest(promotion), "sourceRef": "a" * 64,
            "sourceRevision": "7:" + "a" * 12,
            "liveRevision": "7:" + "a" * 12,
            "window": "current", "coverage": {"status": "missing_dates",
                "presentDates": ["2026-08-20"]}, **common},
            {"sourceKey": "finance-context", "domain": "finance",
            "queryDigest": digest(finance), "sourceRef": "b" * 64,
            "sourceRevision": "2:" + "b" * 64,
            "liveRevision": "2:" + "b" * 64,
            "missingMonths": ["2026-09"], **common}]
        base = {"schemaVersion": seal.ADMISSION_SCHEMA,
            "runId": "run-one", "runVersion": 5,
            "attemptId": "attempt-one", "planDigest": "e" * 64,
            "directoryDigest": "f" * 64, "actorVersion": 1,
            "keyId": "a" * 16, "sources": sources,
            "sourceCount": 2, "sealed": False,
            "sourceRevisionWriteFencesVerified": True,
            "segmentedReceiptAndRequestProofVerified": True,
            "crossDomainSnapshotAtomic": False,
            "financeDailyProrationAllowed": False,
            "inferSkuProfit": False,
            "sumOverlappingErpB2bAdsAllowed": False,
            "upstreamSignatureVerified": False,
            "reportGenerationSupported": False,
            "agentDispatchSupported": False}
        return {**base, "candidateDigest": digest(base)}, {
            "promotion-current": promotion, "finance-context": finance}

    def test_body_is_exact_canonical_and_finance_months_stay_context(self):
        candidate, queries = self.candidate()
        result = seal.make(candidate, queries)
        self.assertEqual(seal.read(result["bodyJson"]), result["body"])
        self.assertEqual(result["bodyDigest"], hashlib.sha256(
            result["bodyJson"].encode("utf-8")).hexdigest())
        self.assertTrue(result["body"]["humanReviewRequired"])
        finance = result["body"]["sources"][1]
        self.assertEqual(finance["missingMonths"], ["2026-09"])
        self.assertEqual(finance["scope"], queries["finance-context"]["scope"])
        self.assertNotIn("skuProfit", finance)

    def test_extra_duplicate_noncanonical_and_false_policy_fail_closed(self):
        candidate, queries = self.candidate()
        raw = seal.make(candidate, queries)["bodyJson"]
        for changed in (raw[:-1] + ',"sourceAuthorityVerified":true}',
                        '{"schemaVersion":"duplicate",' + raw[1:],
                        raw.replace('"sealed"', '"sealed"') + " "):
            with self.subTest(changed=changed[:40]), self.assertRaises(AnalysisContractError):
                seal.read(changed)
        bad = dict(candidate)
        bad["sourceRevisionWriteFencesVerified"] = False
        with self.assertRaises(AnalysisContractError):
            seal.make(bad, queries)

    def test_historical_requires_strictly_newer_live_revision(self):
        candidate, queries = self.candidate()
        body = seal.make(candidate, queries)["body"]
        body["sources"][0]["revisionFreshness"] = "historical_revision"
        body["sources"][0]["liveRevision"] = "8:" + "b" * 12
        self.assertEqual(seal.read(canonical(body)), body)
        body["sources"][0]["liveRevision"] = "7:" + "b" * 12
        with self.assertRaises(AnalysisContractError):
            seal.read(canonical(body))
