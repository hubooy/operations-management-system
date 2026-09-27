"""Synthetic persisted-chain identifiers never grant a model or citation."""
from copy import deepcopy
import json
from pathlib import Path
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError
from . import business_market_v2_active_synthetic_contract as contract
from . import business_market_v2_execution_plan_contract as plan_contract


def catalog():
    return json.loads((Path(__file__).parent / "fixtures" /
        "market_v2_five_tool_catalog.json").read_text(encoding="utf-8"))


def plan():
    root = {"executionReportId":"execution-1", "admittedReportId":"admitted-1",
        "parkedReportId":"parked-1", "sourceReportId":"source-1",
        "ownerEmail":"admin@example.invalid", "selectorDigest":"a"*64,
        "manifestDigest":"b"*64,"marketContextDigest":"c"*64,
        "contextProofDigest":"d"*64,"executionSnapshotDigest":"e"*64,
        "withBudget":False}
    return plan_contract.build(root, catalog())


class MarketV2SyntheticContractTests(TestCase):
    def test_distinct_synthetic_flow_job_and_no_read_authority(self):
        prepared = plan()
        value = contract.build("f"*64, prepared["plan"],
            prepared["planDigest"], catalog())
        all_ids = [value["reportId"],value["workflowId"],
            *value["jobIds"].values(), *value["providerDispatchIds"].values(),
            *value["toolDispatchIds"].values()]
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertEqual(tuple(value["jobIds"]), contract.ROLES)
        self.assertEqual(value["roleTools"]["market_b2b"],
            "get_business_promotion_market_v2")
        self.assertEqual(value["roleTools"]["report"],
            "get_business_market_v2_screening_package")
        self.assertEqual(value["modelId"], contract.MODEL_ID)
        self.assertEqual(value["status"], "paused")
        self.assertTrue(value["dryRun"])
        self.assertEqual(value["externalProviderCallsMade"], 0)
        self.assertEqual(value["syntheticPersistedChainsPlanned"], 5)
        self.assertFalse(value["snapshot"]["externalProviderCalled"])
        self.assertFalse(value["snapshot"]["agentReadPersisted"])
        self.assertFalse(value["readReceiptAuthorized"])

    def test_modified_plan_or_catalog_fails_before_any_write(self):
        prepared = plan()
        changed = deepcopy(prepared["plan"])
        changed["modelPolicy"]["paidCallsAllowed"] = True
        with self.assertRaises(AnalysisContractError):
            contract.build("f"*64, changed, prepared["planDigest"], catalog())
        wrong_catalog = deepcopy(catalog())
        wrong_catalog[0]["name"] = "old-tool"
        with self.assertRaises(AnalysisContractError):
            contract.build("f"*64, prepared["plan"],
                prepared["planDigest"], wrong_catalog)

    def test_budget_presence_selects_only_report_role_budget_tool(self):
        self.assertEqual(contract.role_tools(True)["report"],
            "get_business_market_v2_screening_budget")
        self.assertEqual(contract.role_tools(False)["report"],
            "get_business_market_v2_screening_package")
