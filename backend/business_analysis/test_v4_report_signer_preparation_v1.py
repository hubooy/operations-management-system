"""No-key negative contract for a distinct report-capable seal signer."""
from copy import deepcopy
from unittest import TestCase

from . import v4_report_signer_preparation_v1 as signer
from .contracts import AnalysisContractError, digest


def fixture():
    body={"schemaVersion":
        "business-v4-report-seal-admission-owning-v1",
        "status":"unavailable_report_capable_seal_not_issued",
        "runId":"v4-run",
        "legacySealedDigest":"a"*64,
        "decisionDigest":"b"*64,
        "threeRealOwningStreamsReplayedTwice":True,
        "legacyApplicationHmacRechecked":True,
        "financeShopMappingAuthorityVerified":False,
        "reportCapableSealIssued":False,
        "usableFor13Tables":False,
        "usableForVolumePublication":False,
        "usableForAgentOrDownload":False}
    return {**body,"resultDigest":digest(body)}


class V4ReportSignerPreparationTests(TestCase):
    def test_verified_legacy_scan_still_yields_no_key_or_signable_body(self):
        with self.assertRaises(AnalysisContractError):
            signer.inspect_candidate(fixture())
        value=signer.inspect_candidate(fixture(),enabled=True)
        self.assertEqual(value["status"],
            "blocked_no_finance_mapping_or_new_signer")
        self.assertTrue(value["purposeSeparatedFromLegacy"])
        self.assertNotEqual(value["legacyPurposeSha256"],
            value["newPurposeSha256"])
        for key in ("canonicalSignableBody","newKeyId","newBodyMac"):
            self.assertIsNone(value[key])
        for key in ("keyMaterialLoaded","signerAuthorized",
                    "reportCapableSealIssued","reportGenerationSupported",
                    "agentCitationSupported","rendererRegistered",
                    "downloadSupported"):
            self.assertIs(value[key],False)
        self.assertEqual(value["candidateDigest"],digest({key:item
            for key,item in value.items() if key!="candidateDigest"}))

    def test_caller_mapping_and_forged_owner_grants_refuse(self):
        with self.assertRaises(AnalysisContractError):
            signer.inspect_candidate(fixture(),
                finance_mapping_witness={"scopeKey":"shop:京东:测试店"},
                enabled=True)
        for changed in ({"legacyApplicationHmacRechecked":False},
                        {"threeRealOwningStreamsReplayedTwice":False},
                        {"reportCapableSealIssued":True},
                        {"financeShopMappingAuthorityVerified":True},
                        {"usableFor13Tables":True}):
            owned=deepcopy(fixture())
            owned.update(changed)
            owned["resultDigest"]=digest({key:item for key,item in
                owned.items() if key!="resultDigest"})
            with self.subTest(changed=changed),self.assertRaises(
                    AnalysisContractError):
                signer.inspect_candidate(owned,enabled=True)
        broken=fixture()
        broken["resultDigest"]="0"*64
        with self.assertRaises(AnalysisContractError):
            signer.inspect_candidate(broken,enabled=True)
