"""Random independent test keys prove purpose/role shape, never authority."""
from copy import deepcopy
import hashlib
import hmac
import secrets
from unittest import TestCase

from . import (v4_report_capable_seal_v2_isolated as vector,
    v4_report_seal_transaction_protocol_v1 as protocol)
from .contracts import AnalysisContractError,canonical,digest
from .test_v4_report_seal_transaction_protocol_v1 import fixture
from .v4_final_commit_contract import PARENT_SEAL_PURPOSE
from .v4_report_signer_preparation_v1 import NEW_PURPOSE


FINANCE_TEST_PURPOSE=b"teruisi:finance:shop-map:isolated-v1\x00"


def synthetic():
    linked,stream,prepared=fixture()
    proposal=protocol.prepare_candidate(linked,stream,prepared,enabled=True)
    mapping={"schemaVersion":vector.FINANCE_SCHEMA,
        "ownerRole":vector.FINANCE_ROLE,
        "reportId":proposal["reportId"],
        "v4RunId":proposal["v4RunId"],
        "platform":"京东","shop":proposal["shop"],
        "scopeKey":"shop:京东:"+proposal["shop"],
        "originalPeriod":proposal["originalPeriod"],
        "financeSourceRef":proposal["financeSourceRef"],
        "financeSourceRevision":proposal["financeSourceRevision"],
        "publishedMonths":["2026-08","2026-09"],
        "sourceRowsChecked":True,"testVectorOnly":True}
    mapping["proofDigest"]=digest(mapping)
    finance_key=secrets.token_bytes(32)
    report_key=secrets.token_bytes(32)
    finance_mac=hmac.new(finance_key,FINANCE_TEST_PURPOSE+
        canonical(mapping).encode(),hashlib.sha256).hexdigest()
    body={"schemaVersion":vector.SCHEMA,
        "reportId":proposal["reportId"],
        "v4RunId":proposal["v4RunId"],
        "shop":proposal["shop"],
        "originalPeriod":proposal["originalPeriod"],
        "v2SealedDigest":proposal["v2SealedDigest"],
        "legacyV4SealedDigest":proposal["legacyV4SealedDigest"],
        "sourceBindingsDigest":proposal["sourceBindingsDigest"],
        "periodPlanDigest":proposal["periodPlanDigest"],
        "threeWindowRootsDigest":proposal["threeWindowRootsDigest"],
        "financeMappingProofDigest":mapping["proofDigest"],
        "financeScopeKey":mapping["scopeKey"],
        "financeSourceRef":mapping["financeSourceRef"],
        "financeSourceRevision":mapping["financeSourceRevision"],
        "keyId":hashlib.sha256(report_key).hexdigest()[:16],
        "purposeSha256":hashlib.sha256(NEW_PURPOSE).hexdigest(),
        "reportGenerationSupported":True,
        "agentDispatchSupported":False,
        "rendererRegistered":False,"downloadSupported":False,
        "humanReviewRequired":True,
        "testVectorOnly":True,"authorityVerified":False}
    raw=canonical(body)
    report_mac=hmac.new(report_key,NEW_PURPOSE+raw.encode(),
        hashlib.sha256).hexdigest()
    return proposal,mapping,raw,finance_key,report_key,finance_mac,report_mac


class IsolatedV4ReportSealV2Tests(TestCase):
    def test_independent_random_keys_and_roles_only_verify_synthetic_bytes(self):
        proposal,mapping,raw,finance_key,report_key,finance_mac,report_mac=(
            synthetic())
        self.assertNotEqual(finance_key,report_key)
        self.assertNotEqual(NEW_PURPOSE,PARENT_SEAL_PURPOSE)
        self.assertTrue(hmac.compare_digest(finance_mac,hmac.new(finance_key,
            FINANCE_TEST_PURPOSE+canonical(mapping).encode(),
            hashlib.sha256).hexdigest()))
        self.assertTrue(hmac.compare_digest(report_mac,hmac.new(report_key,
            NEW_PURPOSE+raw.encode(),hashlib.sha256).hexdigest()))
        self.assertFalse(hmac.compare_digest(report_mac,hmac.new(report_key,
            PARENT_SEAL_PURPOSE+raw.encode(),hashlib.sha256).hexdigest()))
        with self.assertRaises(AnalysisContractError):
            vector.inspect_test_vector(proposal,mapping,raw,
                mapping_mac_verified=True,signer_role=vector.SIGNER_ROLE)
        value=vector.inspect_test_vector(proposal,mapping,raw,
            mapping_mac_verified=True,signer_role=vector.SIGNER_ROLE,
            enabled=True)
        self.assertTrue(value["syntheticShapeVerified"])
        for key in ("protectedFinanceOwnerVerified",
                    "protectedReportSignerVerified",
                    "reportCapableSealIssued","authorityVerified",
                    "rendererRegistered","downloadSupported"):
            self.assertFalse(value[key])

    def test_missing_mapping_page_root_or_wrong_role_cannot_form_vector(self):
        proposal,mapping,raw,*_=synthetic()
        for witness,checked,role in (
                (None,False,vector.SIGNER_ROLE),
                (mapping,False,vector.SIGNER_ROLE),
                (mapping,True,"teruisi_ai_seal_writer")):
            with self.subTest(witness=witness is None,role=role), \
                    self.assertRaises(AnalysisContractError):
                vector.inspect_test_vector(proposal,witness,raw,
                    mapping_mac_verified=checked,signer_role=role,
                    enabled=True)
        changed=deepcopy(proposal)
        changed["threeWindowRootsDigest"]=None
        changed["proposalDigest"]=digest({key:value for key,value in
            changed.items() if key!="proposalDigest"})
        with self.assertRaises(AnalysisContractError):
            vector.inspect_test_vector(changed,mapping,raw,
                mapping_mac_verified=True,signer_role=vector.SIGNER_ROLE,
                enabled=True)
        changed_map=deepcopy(mapping)
        changed_map["ownerRole"]="teruisi_ai_writer"
        changed_map["proofDigest"]=digest({key:value for key,value in
            changed_map.items() if key!="proofDigest"})
        with self.assertRaises(AnalysisContractError):
            vector.inspect_test_vector(proposal,changed_map,raw,
                mapping_mac_verified=True,signer_role=vector.SIGNER_ROLE,
                enabled=True)

    def test_cross_shop_period_finance_source_and_old_seal_refuse(self):
        proposal,mapping,raw,*_=synthetic()
        for changed in (
                {"scopeKey":"shop:天猫:测试店"},
                {"shop":"另一店"},
                {"financeSourceRevision":"8:"+"a"*64},
                {"publishedMonths":["2026-08"]}):
            witness=deepcopy(mapping)
            witness.update(changed)
            witness["proofDigest"]=digest({key:value for key,value in
                witness.items() if key!="proofDigest"})
            with self.subTest(changed=changed),self.assertRaises(
                    AnalysisContractError):
                vector.inspect_test_vector(proposal,witness,raw,
                    mapping_mac_verified=True,signer_role=vector.SIGNER_ROLE,
                    enabled=True)
        old=__import__("json").loads(raw)
        old["schemaVersion"]="business-v4-parent-seal-internal-v1"
        old["reportGenerationSupported"]=False
        with self.assertRaises(AnalysisContractError):
            vector.inspect_test_vector(proposal,mapping,canonical(old),
                mapping_mac_verified=True,signer_role=vector.SIGNER_ROLE,
                enabled=True)
