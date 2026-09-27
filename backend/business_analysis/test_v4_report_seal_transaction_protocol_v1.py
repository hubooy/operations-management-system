"""Pure one-time transaction proposal and replay refusal vectors."""
from copy import deepcopy
from unittest import TestCase

from . import (v4_report_seal_transaction_protocol_v1 as protocol,
    v4_report_signer_preparation_v1 as signer)
from .contracts import AnalysisContractError,digest


def fixture():
    linked={"schemaVersion":
        "business-report-v4-owning-authorization-preflight-v1",
        "status":"blocked_legacy_v4_report_authority",
        "reportId":"report-one","v4RunId":"v4-run",
        "shop":"测试店",
        "originalPeriod":{"startDate":"2026-08-16",
            "endDate":"2026-09-14"},
        "decisionDigest":"1"*64,
        "v2SealedDigest":"2"*64,
        "v4SealedDigest":"3"*64,
        "periodPlanDigest":"4"*64,
        "sourceBindingsDigest":"5"*64,
        "sqlCreationTimeLinkRechecked":True,
        "currentV4ApplicationHmacRechecked":True,
        "legacySealCanGenerateReport":False,
        "v4RowsReferencableIn13Tables":False,
        "v4VolumesPublishable":False,
        "agentCitationSupported":False,
        "downloadSupported":False}
    linked["resultDigest"]=digest(linked)
    stream={"schemaVersion":
        "business-v4-report-seal-admission-owning-v1",
        "status":"unavailable_report_capable_seal_not_issued",
        "runId":"v4-run",
        "shop":"测试店",
        "originalPeriod":{"startDate":"2026-08-16",
            "endDate":"2026-09-14"},
        "legacySealedDigest":"3"*64,
        "decisionDigest":"6"*64,
        "periodPlanDigest":"4"*64,
        "threeWindowRootsDigest":"7"*64,
        "financeSourceKey":"finance-context",
        "financeSourceRef":"8"*64,
        "financeSourceRevision":"0:"+"b"*64,
        "threeRealOwningStreamsReplayedTwice":True,
        "legacyApplicationHmacRechecked":True,
        "financeShopMappingAuthorityVerified":False,
        "reportCapableSealIssued":False,
        "usableFor13Tables":False,
        "usableForVolumePublication":False,
        "usableForAgentOrDownload":False}
    stream["resultDigest"]=digest(stream)
    return linked,stream,signer.inspect_candidate(stream,enabled=True)


class V4ReportSealTransactionProtocolTests(TestCase):
    def test_same_report_three_root_candidate_stays_unissued(self):
        linked,stream,signed=fixture()
        with self.assertRaises(AnalysisContractError):
            protocol.prepare_candidate(linked,stream,signed)
        value=protocol.prepare_candidate(linked,stream,signed,
            enabled=True)
        self.assertEqual(value["reportId"],"report-one")
        self.assertEqual(value["v4RunId"],"v4-run")
        self.assertEqual(value["threeWindowRootsDigest"],"7"*64)
        self.assertEqual(value["requiredTransactionOrder"],
            list(protocol.ORDER))
        for key in ("signableBody","ticketId","nonceHash",
                    "claimHash","newBodyMac"):
            self.assertIsNone(value[key])
        for key in ("issued","claimed","committed","consumed",
                    "authorityVerified","reportGenerationSupported",
                    "rendererRegistered","downloadSupported"):
            self.assertIs(value[key],False)
        trace=protocol.inspect_trace(value,[],enabled=True)
        self.assertEqual(trace["status"],"blocked_unissued")
        self.assertFalse(trace["protectedConsumptionVerified"])

    def test_cross_report_or_source_root_and_forged_grants_refuse(self):
        linked,stream,signed=fixture()
        for name,changed in (("v4RunId","different-run"),
                             ("shop","另一店"),
                             ("v4SealedDigest","8"*64),
                             ("periodPlanDigest","9"*64),
                             ("sqlCreationTimeLinkRechecked",False),
                             ("v4RowsReferencableIn13Tables",True)):
            bad=deepcopy(linked)
            bad[name]=changed
            bad["resultDigest"]=digest({key:value for key,value in
                bad.items() if key!="resultDigest"})
            with self.subTest(name=name),self.assertRaises(
                    AnalysisContractError):
                protocol.prepare_candidate(bad,stream,signed,
                    enabled=True)
        bad=deepcopy(stream)
        bad["threeRealOwningStreamsReplayedTwice"]=False
        bad["resultDigest"]=digest({key:value for key,value in
            bad.items() if key!="resultDigest"})
        with self.assertRaises(AnalysisContractError):
            protocol.prepare_candidate(linked,bad,signed,enabled=True)
        # A self-hashed caller can forge a different ordinary digest. The
        # pure layer must still grant nothing; only the owning SQL reader can
        # authenticate the binding bytes.
        bad=deepcopy(linked)
        bad["sourceBindingsDigest"]="a"*64
        bad["resultDigest"]=digest({key:value for key,value in
            bad.items() if key!="resultDigest"})
        value=protocol.prepare_candidate(bad,stream,signed,enabled=True)
        self.assertFalse(value["authorityVerified"])
        self.assertFalse(value["issued"])

    def test_missing_map_and_every_attempted_transaction_event_refuse(self):
        linked,stream,signed=fixture()
        with self.assertRaises(AnalysisContractError):
            protocol.prepare_candidate(linked,stream,signed,
                finance_mapping_witness={"scopeKey":"shop:京东:测试店"},
                enabled=True)
        proposal=protocol.prepare_candidate(linked,stream,signed,
            enabled=True)
        for phase in (*protocol.ORDER,"unknown_commit_result",
                      "retry_commit"):
            with self.subTest(phase=phase),self.assertRaises(
                    AnalysisContractError):
                protocol.inspect_trace(proposal,[{"phase":phase}],
                    enabled=True)
        replay=deepcopy(proposal)
        replay["issued"]=True
        replay["proposalDigest"]=digest({key:value for key,value in
            replay.items() if key!="proposalDigest"})
        with self.assertRaises(AnalysisContractError):
            protocol.inspect_trace(replay,[],enabled=True)
