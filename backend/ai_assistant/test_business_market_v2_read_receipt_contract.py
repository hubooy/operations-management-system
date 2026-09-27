"""Same-job and numeric constraints never turn synthetic DTOs into authority."""
from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError
from business_analysis.test_market_numeric_claims import price
from . import business_market_v2_read_receipt_contract as contract
from . import business_market_v2_execution_snapshot_contract as execution


def sample_receipt():
    return {"schemaVersion":contract.RECEIPT_SCHEMA,
        "executionReportId":"execution-1", "admittedReportId":"report-1",
        "ownerEmail":"owner@example.invalid", "jobId":"job-1",
        "providerDispatchId":"provider-1", "providerCallId":"call-1",
        "toolDispatchId":"tool-1", "toolName":execution.TOOL_ORDER[4],
        "role":"market_b2b", "mode":"row", "contextProofDigest":"a"*64,
        "toolResultDigest":"b"*64, "receiptDigest":"c"*64,
        "persistedRead":True, "numericCitationAllowed":False,
        "agentExecutionAuthorized":False}


class MarketV2ReadReceiptContractTests(TestCase):
    def test_numeric_candidate_requires_same_job_role_and_owning_row(self):
        ref, row = price()
        result = contract.numeric_requirements(ref, sample_receipt(), row,
            table_binding_digest=ref["tableBindingDigest"])
        self.assertTrue(result["sameJobIdentityMatched"])
        self.assertTrue(result["owningNumericCellRecomputed"])
        self.assertEqual(result["candidateNumber"]["value"], 10)
        self.assertFalse(result["numericCitationAllowed"])
        self.assertFalse(result["independentCellProofPersisted"])
        for field, wrong in (("jobId", "other-job"), ("role", "report"),
                ("admittedReportId", "other-report"),
                ("toolName", execution.TOOL_ORDER[0])):
            changed = sample_receipt(); changed[field] = wrong
            with self.assertRaises(AnalysisContractError):
                contract.numeric_requirements(ref, changed, row,
                    table_binding_digest=ref["tableBindingDigest"])
        with self.assertRaises(AnalysisContractError):
            contract.numeric_requirements(ref, sample_receipt(), row,
                table_binding_digest="d"*64)

    def test_claimed_authority_and_wrong_numeric_are_rejected(self):
        ref, row = price()
        for field, wrong in (("persistedRead", False),
                ("numericCitationAllowed", True),
                ("agentExecutionAuthorized", True),
                ("contextProofDigest", "bad")):
            changed = sample_receipt(); changed[field] = wrong
            with self.assertRaises(AnalysisContractError):
                contract.receipt(changed)
        changed_row = deepcopy(row)
        changed_row["rowId"] = "e"*64
        with self.assertRaises(AnalysisContractError):
            contract.numeric_requirements(ref, sample_receipt(), changed_row,
                table_binding_digest=ref["tableBindingDigest"])
