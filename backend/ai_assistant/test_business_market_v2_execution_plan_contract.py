"""Five-agent plan is bounded while actual execution remains closed."""
from copy import deepcopy
import json
from pathlib import Path
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, digest
from . import business_market_v2_execution_plan_contract as contract
from . import business_market_v2_execution_snapshot_contract as previous


def catalog():
    return json.loads((Path(__file__).parent / "fixtures" /
        "market_v2_five_tool_catalog.json").read_text(encoding="utf-8"))


def root(with_budget=False):
    return {"executionReportId":"execution-1", "admittedReportId":"admitted-1",
        "parkedReportId":"parked-1", "sourceReportId":"source-1",
        "ownerEmail":"admin@example.invalid", "selectorDigest":"a"*64,
        "manifestDigest":"b"*64,"marketContextDigest":"c"*64,
        "contextProofDigest":"d"*64,"executionSnapshotDigest":"e"*64,
        "withBudget":with_budget}


class MarketV2ExecutionPlanContractTests(TestCase):
    def test_both_budget_variants_are_exact_and_paid_calls_remain_zero(self):
        for with_budget in (False, True):
            value = contract.build(root(with_budget), catalog())
            plan = value["plan"]
            self.assertEqual(value["planDigest"], digest(plan))
            self.assertEqual(plan["toolCatalogDigest"], previous.CATALOG_DIGEST)
            self.assertEqual(plan["graphDigest"], previous.GRAPH_DIGESTS[with_budget])
            self.assertEqual(plan["proposedTools"], list(previous.TOOL_ORDER))
            self.assertEqual(len(plan["fiveAgentRoles"]), 5)
            self.assertEqual(plan["budgetPolicy"]["withBudget"], with_budget)
            self.assertEqual(plan["modelPolicy"]["maxPaidCostCents"], 0)
            self.assertEqual(plan["modelPolicy"]["maxProviderRoundsNow"], 0)
            self.assertFalse(plan["agentJobsAllowed"])
            self.assertFalse(plan["providerCallsAllowed"])
            self.assertFalse(plan["numericCitationAllowed"])

    def test_changed_context_owner_or_catalog_cannot_build_same_plan(self):
        for key, wrong in (("contextProofDigest", "wrong"),
                ("executionSnapshotDigest", "wrong"),
                ("ownerEmail", "UPPER@example.invalid"),
                ("admittedReportId", "execution-1")):
            changed = root(); changed[key] = wrong
            with self.assertRaises(AnalysisContractError):
                contract.build(changed, catalog())
        changed_catalog = deepcopy(catalog())
        changed_catalog[0]["name"] = "get_old_v1_package"
        with self.assertRaises(AnalysisContractError):
            contract.build(root(), changed_catalog)
