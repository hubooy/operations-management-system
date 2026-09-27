from copy import deepcopy
from unittest import TestCase

from . import report_intent_v3 as intent, report_reference_v3 as reference
from .contracts import AnalysisContractError
from .test_report_reference_v3 import fixture


class ReportIntentV3Tests(TestCase):
    def test_staging_is_paused_and_requires_human_review(self):
        built, sealed = fixture()
        candidate = reference.build(built["header"], built["entries"], sealed)
        staged = intent.stage(candidate)
        self.assertEqual((staged["status"], staged["pauseReason"]),
                         ("paused", "v3_agents_not_registered"))
        graph = staged["workflowPlan"]
        self.assertEqual([node["key"] for node in graph["nodes"]],
            [*reference.AGENTS, "human_review"])
        self.assertEqual(graph["nodes"][-1]["dependsOn"], ["report"])
        self.assertTrue(graph["humanReviewRequired"])
        self.assertTrue(all(not node["dispatchRegistered"] for node in graph["nodes"]))
        self.assertFalse(staged["workflowInput"]["modelDispatchSupported"])
        self.assertEqual(staged["workflowInput"]["candidate"], candidate)

    def test_old_or_executable_candidate_never_stages(self):
        built, sealed = fixture()
        candidate = reference.build(built["header"], built["entries"], sealed)
        for changed in ({"executionProfile": "business-agent-reference-v2"},
                        {"candidateDigest": "0" * 64}, {"modelDispatchSupported": True},
                        {"reportGenerationSupported": True}):
            bad = {**deepcopy(candidate), **changed}
            with self.subTest(changed=changed), self.assertRaises(AnalysisContractError):
                intent.stage(bad)
