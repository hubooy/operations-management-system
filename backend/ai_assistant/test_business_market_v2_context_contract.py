"""SQL/Python canonical context compatibility and narrow receipt shape."""
from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, digest
from . import business_market_v2_context_contract as contract


class MarketV2ContextContractTests(TestCase):
    def test_fixed_context_bytes_match_four_source_fields(self):
        result = contract.context("source-1", "run-1", "screen-1", "a"*64)
        self.assertEqual(result["canonicalJson"],
            '{"reportId":"source-1","runId":"run-1",'
            '"screeningId":"screen-1","sealedDigest":"' + "a"*64 + '"}')
        self.assertEqual(result["contextDigest"], digest({"reportId":"source-1",
            "runId":"run-1","screeningId":"screen-1","sealedDigest":"a"*64}))
        for values in (("source\"1", "run-1", "screen-1", "a"*64),
                ("source-1", "run-1", "screen-1", "wrong")):
            with self.assertRaises(AnalysisContractError):
                contract.context(*values)

    def test_receipt_remains_proof_not_agent_read(self):
        value = {"schemaVersion":contract.SCHEMA,"reportId":"execution-1",
            "ownerEmail":"admin@example.invalid","admittedReportId":"admitted-1",
            "parkedReportId":"parked-1","sourceReportId":"source-1",
            "selectorDigest":"a"*64,"manifestDigest":"b"*64,
            "contextDigest":"c"*64,"proofDigest":"d"*64,
            "proofPersisted":True,"agentReadPersisted":False,"executionReady":False}
        self.assertEqual(contract.receipt(value, execution_report_id="execution-1",
            owner_email="admin@example.invalid", context_digest="c"*64), value)
        for key, wrong in (("agentReadPersisted", True), ("executionReady", True),
                ("proofPersisted", False), ("contextDigest", "e"*64),
                ("sourceReportId", "wrong\"id")):
            changed = deepcopy(value); changed[key] = wrong
            with self.assertRaises(AnalysisContractError):
                contract.receipt(changed, execution_report_id="execution-1",
                    owner_email="admin@example.invalid", context_digest="c"*64)
