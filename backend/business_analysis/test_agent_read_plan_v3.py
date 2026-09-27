from copy import deepcopy
from unittest import TestCase

from . import agent_read_plan_v3 as plan, report_reference_v3 as reference
from .contracts import AnalysisContractError, digest
from .test_report_reference_v3 import fixture


class AgentReadPlanV3Tests(TestCase):
    def test_roles_require_own_directory_and_page_reads_without_dispatch(self):
        built, sealed = fixture()
        candidate = reference.build(built["header"], built["entries"], sealed)
        result = plan.build(candidate)
        self.assertEqual(len(result["rolePlans"]), 5)
        self.assertEqual(result["totalSourcePages"], 2)
        self.assertTrue(result["currentReadBridgeCapacitySupported"])
        self.assertTrue(result["humanReviewRequired"])
        self.assertFalse(result["agentReadReceiptPersisted"])
        self.assertFalse(result["agentDispatchSupported"])
        roles = {item["role"]: item for item in result["rolePlans"]}
        self.assertTrue(roles["promotion"]["missingRequiredDomain"])
        self.assertTrue(roles["market_b2b"]["missingRequiredDomain"])
        self.assertEqual(set(roles["report"]["requiredSourceKeys"]), {"daily", "finance"})

    def test_cap_and_wrong_candidate_fail_closed(self):
        built, sealed = fixture()
        candidate = reference.build(built["header"], built["entries"], sealed)
        changed = deepcopy(candidate)
        changed["dailyFacts"][0]["pageCount"] = 65
        changed["candidateDigest"] = digest({k: v for k, v in changed.items() if k != "candidateDigest"})
        result = plan.build(changed)
        self.assertFalse(result["currentReadBridgeCapacitySupported"])
        changed["modelDispatchSupported"] = True
        with self.assertRaises(AnalysisContractError): plan.build(changed)
