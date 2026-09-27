from copy import deepcopy
from unittest import TestCase

from . import promotion_budget_v10_download_fence as fence
from .contracts import AnalysisContractError, digest


def fixture():
    return {"schemaVersion": fence.SCHEMA,
        "report": {"id": "report-1", "snapshotSha256": "a" * 64},
        "workflow": {"id": "flow-1", "nodeRootDigest": "b" * 64,
            "jobRootDigest": "c" * 64, "reviewEventDigest": "d" * 64},
        "humanReview": {"nodeId": "human-1", "reviewerEmail": "admin@example.test"},
        "evidence": {"id": "evidence-1", "sealedDigest": "e" * 64},
        "budget": None,
        "file": {"runId": "run-1", "attempt": 1,
            "bindingDigest": "f" * 64, "compactSha256": "1" * 64,
            "storedBytes": 10, "attestationId": "2" * 64,
            "fullManifestSha256": "3" * 64,
            "fullManifestDigest": "4" * 64,
            "budgetProofDigest": "5" * 64},
        "approvedContentDigest": "6" * 64}


class BudgetV10DownloadFenceTests(TestCase):
    def test_current_structure_changes_digest_and_never_authorizes_itself(self):
        body = fixture()
        self.assertEqual(fence.fence_digest(body), digest(body))
        for key, changed in (("report", {"id": "other"}),
                ("workflow", {"nodeRootDigest": "0" * 64}),
                ("humanReview", {"reviewerEmail": "other@example.test"}),
                ("evidence", {"sealedDigest": "0" * 64}),
                ("budget", {"planDigest": "0" * 64}),
                ("file", {**body["file"], "attempt": 2})):
            altered = deepcopy(body)
            altered[key] = changed
            with self.subTest(key=key):
                self.assertNotEqual(fence.fence_digest(altered),
                    fence.fence_digest(body))

    def test_missing_file_identity_and_bool_integer_fail_closed(self):
        body = fixture()
        for altered in (dict(body, schemaVersion="older"),
                {**body, "file": {**body["file"], "attempt": True}},
                {**body, "file": {**body["file"], "storedBytes": 0}},
                {**body, "file": {**body["file"], "attestationId": "short"}}):
            with self.assertRaises(AnalysisContractError):
                fence.fence_digest(altered)
