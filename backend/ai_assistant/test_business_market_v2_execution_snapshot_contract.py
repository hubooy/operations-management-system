from copy import deepcopy
import json
from pathlib import Path
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, digest
from . import business_market_v2_execution_snapshot_contract as contract


def catalog():
    return json.loads((Path(__file__).resolve().parent / "fixtures" /
        "market_v2_five_tool_catalog.json").read_text(encoding="utf-8"))


def root(with_budget=False):
    return {"admittedReportId": "admitted-1", "parkedReportId": "parked-1",
        "sourceReportId": "source-1", "ownerEmail": "admin@example.invalid",
        "selectorDigest": "a"*64, "manifestDigest": "b"*64,
        "marketContextDigest": "c"*64, "withBudget": with_budget}


class MarketV2ExecutionSnapshotContractTests(TestCase):
    def test_five_callable_entries_and_graph_are_frozen_without_v1_mutation(self):
        entries = catalog()
        self.assertEqual(tuple(item["name"] for item in entries), contract.TOOL_ORDER)
        self.assertEqual(digest(entries), contract.CATALOG_DIGEST)
        self.assertEqual(contract.catalog(entries), entries)
        for budget in (False, True):
            graph = contract.graph(budget)
            self.assertEqual(digest(graph), contract.GRAPH_DIGESTS[budget])
            instructions = " ".join(node["instruction"] for node in graph["nodes"])
            for name in (name for name in contract.TOOL_ORDER
                    if budget or name != contract.TOOL_ORDER[2]):
                self.assertIn(name, instructions)
            for name in contract.BASE_TOOLS:
                self.assertNotIn(name, instructions)

    def test_build_is_paused_and_exactly_binds_admitted_material(self):
        for budget in (False, True):
            built = contract.build("execution-1", root(budget), catalog())
            self.assertEqual(built["snapshot"]["executionRoot"], root(budget))
            self.assertEqual(built["workflowInput"]["allowedTools"],
                list(contract.TOOL_ORDER))
            self.assertEqual(built["workflowInput"]["graphDigest"],
                contract.GRAPH_DIGESTS[budget])
            self.assertEqual(built["snapshot"]["toolCatalogDigest"],
                contract.CATALOG_DIGEST)
            self.assertEqual(built["status"], "paused")
            self.assertFalse(built["agentDispatchSupported"])
            self.assertFalse(built["snapshot"]["agentReadPersisted"])
            self.assertEqual(built["buildDigest"], digest({key: value for key,
                value in built.items() if key != "buildDigest"}))

    def test_cross_root_and_catalog_edits_fail_closed(self):
        wrong = deepcopy(catalog())
        wrong[0]["execution"]["allowedSurfaces"] = ["business_agent_screening_promotion_v1"]
        with self.assertRaises(AnalysisContractError):
            contract.catalog(wrong)
        changed = root(); changed["ownerEmail"] = "UPPER@example.invalid"
        with self.assertRaises(AnalysisContractError):
            contract.build("execution-1", changed, catalog())
        changed = root(); changed["manifestDigest"] = "wrong"
        with self.assertRaises(AnalysisContractError):
            contract.build("execution-1", changed, catalog())
        with self.assertRaises(AnalysisContractError):
            contract.build("admitted-1", root(), catalog())
