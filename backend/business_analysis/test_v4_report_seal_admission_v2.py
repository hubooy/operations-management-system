"""No-DB report-capable seal admission stays unavailable for legacy v4."""
from copy import deepcopy
from unittest import TestCase

from . import v4_report_seal_admission_v2 as admission
from .contracts import AnalysisContractError, canonical, digest
from .test_report_v4_authorization_preflight_v1 import fixture as linked_fixture


def fixture():
    _, _, plan, verified, raw = linked_fixture()
    body = admission.evidence_seal_v4.read(raw)
    receipts = []
    for source in body["sources"]:
        if source["domain"] != "netshop":
            continue
        base = {"schemaVersion":
            "business-report-v4-promotion-stream-candidate-v1",
            "bridgeCandidateDigest": "1"*64,
            "sourceManifestDigest": "2"*64,
            "window": source["window"],
            "pageCount": source["pageCount"],
            "rowCount": source["rowCount"],
            "storedBytes": source["storedBytes"],
            "rowDigest": "3"*64,
            "evidenceDigest": "4"*64,
            "receiptChainDigest": source["receiptChainDigest"],
            "coverageDigest": digest(source["coverage"]),
            "twoCompletePassesEqual": True,
            "stagedUnpublished": True,
            "persistedSameReportLinkVerified": False,
            "sourceAuthorityVerified": False,
            "registeredRenderer": False, "publishable": False}
        receipts.append({**base,"resultDigest":digest(base)})
    return plan,verified,raw,receipts


class ReportSealAdmissionV2Tests(TestCase):
    def test_three_replays_and_hmac_still_cannot_upgrade_old_seal(self):
        plan,verified,raw,receipts = fixture()
        with self.assertRaises(AnalysisContractError):
            admission.inspect_candidate(plan,verified,raw,receipts)
        value=admission.inspect_candidate(plan,verified,raw,receipts,
            enabled=True)
        self.assertEqual(value["status"],
            "unavailable_report_capable_seal_not_issued")
        self.assertEqual(set(value["promotionStreamRoots"]),
            set(admission.WINDOWS))
        self.assertTrue(value["threeWindowsReplayedAndDatesObserved"])
        for key in ("reportCapableSealIssued",
                    "financeShopMappingAuthorityVerified",
                    "usableForReport", "usableForAgentCitation",
                    "usableForRendererOrDownload"):
            self.assertIs(value[key],False)
        self.assertEqual(value["candidateDigest"],digest({key:item
            for key,item in value.items() if key!="candidateDigest"}))

    def test_missing_or_forged_page_receipt_refuses(self):
        plan,verified,raw,receipts=fixture()
        with self.assertRaises(AnalysisContractError):
            admission.inspect_candidate(plan,verified,raw,receipts[:2],
                enabled=True)
        bad=deepcopy(receipts)
        bad[0]["rowCount"] += 1
        bad[0]["resultDigest"] = digest({key:item for key,item in
            bad[0].items() if key!="resultDigest"})
        with self.assertRaises(AnalysisContractError):
            admission.inspect_candidate(plan,verified,raw,bad,
                enabled=True)
        bad=deepcopy(receipts)
        bad[1]["publishable"] = True
        bad[1]["resultDigest"] = digest({key:item for key,item in
            bad[1].items() if key!="resultDigest"})
        with self.assertRaises(AnalysisContractError):
            admission.inspect_candidate(plan,verified,raw,bad,
                enabled=True)

    def test_missing_day_and_unknown_finance_mapping_refuse(self):
        plan,verified,raw,receipts=fixture()
        body=deepcopy(admission.evidence_seal_v4.read(raw))
        day=next(item for item in body["sources"] if item["domain"]=="netshop")
        day["coverage"]["presentDates"].pop()
        changed=canonical(body)
        refreshed=deepcopy(verified)
        import hashlib
        refreshed["sealedDigest"]=hashlib.sha256(changed.encode()).hexdigest()
        refreshed["proofDigest"]=digest({key:item for key,item in
            refreshed.items() if key!="proofDigest"})
        with self.assertRaises(AnalysisContractError):
            admission.inspect_candidate(plan,refreshed,changed,receipts,
                enabled=True)
        body=deepcopy(admission.evidence_seal_v4.read(raw))
        finance=next(item for item in body["sources"] if item["domain"]=="finance")
        finance["scope"]={"scope_key":"business","scope_type":"business",
            "scope_name":"事业部","group_name":""}
        changed=canonical(body)
        refreshed=deepcopy(verified)
        refreshed["sealedDigest"]=hashlib.sha256(changed.encode()).hexdigest()
        refreshed["proofDigest"]=digest({key:item for key,item in
            refreshed.items() if key!="proofDigest"})
        with self.assertRaises(AnalysisContractError):
            admission.inspect_candidate(plan,refreshed,changed,receipts,
                enabled=True)

    def test_caller_mapping_claim_and_legacy_policy_flip_refuse(self):
        plan,verified,raw,receipts=fixture()
        with self.assertRaises(AnalysisContractError):
            admission.inspect_candidate(plan,verified,raw,receipts,
                finance_mapping_witness={"shop":"测试店"},enabled=True)
        body=deepcopy(admission.evidence_seal_v4.read(raw))
        body["reportGenerationSupported"] = True
        with self.assertRaises(AnalysisContractError):
            admission.inspect_candidate(plan,verified,canonical(body),
                receipts,enabled=True)
