import copy
import unittest

from .contracts import AnalysisContractError, PageReconciler, canonical, digest
from . import cursor_renewal as renewal


def fixture(domain="netshop"):
    query = {"platform": "京东", "startDate": "2026-09-01", "endDate": "2026-09-02", "window": "current"}
    query.update({"category": "厨房机械", "scope": "POP", "rankingDimension": "SKU", "priceBandFilter": "全部"}
        if domain == "market" else {"shop": "合成店", **({"channel": "零售"} if domain == "sales" else {"dataset": "promotion"})})
    ref, cursor = "a"*64, "signed-expired-logical-cursor"
    page = {"schemaVersion": "business-analysis-v1", "sourceRef": ref, "sourceRevision": "revision-1",
        "control": {"rowCount": 3, "typedTotals": {"spendCents": 600}},
        "items": [{"rowId": "5", "metrics": {"spendCents": 100}}, {"rowId": "99", "metrics": {"spendCents": 200}}],
        "pagination": {"hasMore": True, "nextCursor": cursor}}
    page["pageEvidence"] = {"rowCount": 2, "sha256": digest(page["items"])}
    verifier = PageReconciler()
    verifier.consume(page)
    descriptor = {"source": {"key": "source-a", "domain": domain, "query": query},
        "revision": "revision-1", "sourceRef": ref, "pageSize": 100}
    snapshot = {"runId": "evidence-example", "runVersion": 9, "sourceVersion": 2,
        "principalDigest": "b"*64, "scopeDigest": "c"*64, "planDigest": "d"*64,
        "evidenceSchema": "business-evidence-v2", "status": "collecting", "descriptor": descriptor,
        "checkpointJson": canonical({"pageCount": 1, "verifier": verifier.__dict__, "metadata": {"sourceRevision": "revision-1"}}),
        "lastPage": {"sequence": 1, "payloadDigest": digest(page), "lastId": 99, "nextCursor": cursor,
            "sourceRef": ref, "sourceRevision": "revision-1"}}
    return snapshot, copy.deepcopy(descriptor), {"binding": ref, "lastId": 99}, page


class CursorRenewalTests(unittest.TestCase):
    def test_three_domains_stable_copy_no_authority(self):
        for domain in ("netshop", "sales", "market"):
            snapshot, current, payload, _ = fixture(domain)
            before = copy.deepcopy(snapshot)
            proposal = renewal.prepare(snapshot, current, payload)
            self.assertEqual(renewal.validate(proposal, snapshot, current, payload), proposal)
            self.assertEqual(snapshot, before)
            self.assertFalse(proposal["authorityVerified"])
            self.assertFalse(proposal["renewalAuthorized"])
            self.assertFalse(proposal["modelReplayAllowed"])
            payload["lastId"] = 100
            self.assertEqual(proposal["cursorPayload"]["lastId"], 99)

    def test_source_query_revision_scope_and_page_size_changes_reject(self):
        for path, replacement in [("revision", "revision-2"), ("sourceRef", "e"*64), ("pageSize", 99),
                ("shop", "另一店"), ("window", "previous"), ("startDate", "2026-08-31"), ("key", "source-b")]:
            snapshot, current, payload, _ = fixture()
            target = current if path in current else current["source"] if path == "key" else current["source"]["query"]
            target[path] = replacement
            with self.subTest(path=path), self.assertRaises(AnalysisContractError):
                renewal.prepare(snapshot, current, payload)

    def test_proposal_cas_owner_scope_plan_and_checkpoint_bytes(self):
        for key, value in [("runVersion", 10), ("sourceVersion", 3), ("principalDigest", "e"*64),
                ("scopeDigest", "e"*64), ("planDigest", "e"*64), ("runId", "evidence-another")]:
            snapshot, current, payload, _ = fixture()
            proposal = renewal.prepare(snapshot, current, payload)
            snapshot[key] = value
            with self.subTest(key=key), self.assertRaises(AnalysisContractError):
                renewal.validate(proposal, snapshot, current, payload)
        snapshot, current, payload, _ = fixture()
        proposal = renewal.prepare(snapshot, current, payload)
        snapshot["checkpointJson"] += " "
        with self.assertRaises(AnalysisContractError):
            renewal.validate(proposal, snapshot, current, payload)

    def test_payload_exact_last_key_is_not_row_count(self):
        for payload in ({"binding": "a"*64, "lastId": 2}, {"binding": "b"*64, "lastId": 99},
                {"binding": "a"*64, "lastId": 99, "expired": True}):
            snapshot, current, _, _ = fixture()
            with self.assertRaises(AnalysisContractError):
                renewal.prepare(snapshot, current, payload)

    def test_last_committed_page_exact(self):
        for key, value in [("sequence", 2), ("lastId", 100), ("nextCursor", "different"),
                ("sourceRef", "e"*64), ("sourceRevision", "revision-2")]:
            snapshot, current, payload, _ = fixture()
            snapshot["lastPage"][key] = value
            with self.subTest(key=key), self.assertRaises(AnalysisContractError):
                renewal.prepare(snapshot, current, payload)

    def test_completed_empty_capacity_and_corrupt_state(self):
        import json
        for section, key, value in [("verifier", "finished", True), ("verifier", "rows", 0),
                ("verifier", "rows", 101), ("verifier", "source_ref", "e"*64),
                ("metadata", "sourceRevision", "revision-2"), (None, "pageCount", 2000),
                ("verifier", "control", None), ("verifier", "present", {"spendCents": 3})]:
            snapshot, current, payload, _ = fixture()
            entry = json.loads(snapshot["checkpointJson"])
            (entry[section] if section else entry)[key] = value
            snapshot["checkpointJson"] = canonical(entry)
            with self.subTest(key=key, value=value), self.assertRaises(AnalysisContractError):
                renewal.prepare(snapshot, current, payload)

    def test_boolean_and_float_not_integer(self):
        for key in ("runVersion", "sourceVersion"):
            for value in (True, 9.0):
                snapshot, current, payload, _ = fixture()
                snapshot[key] = value
                with self.assertRaises(AnalysisContractError):
                    renewal.prepare(snapshot, current, payload)
        for value in (True, 99.0, "99"):
            snapshot, current, payload, _ = fixture()
            payload["lastId"] = value
            with self.assertRaises(AnalysisContractError):
                renewal.prepare(snapshot, current, payload)

    def test_unknown_nested_duplicate_and_oversize_rejected(self):
        for raw in ('{"pageCount":1,"pageCount":2}', '['*1000+'0'+']'*1000, '"'+'中'*12000+'"', '{"x":NaN}'):
            snapshot, current, payload, _ = fixture()
            snapshot["checkpointJson"] = raw
            with self.assertRaises(AnalysisContractError):
                renewal.prepare(snapshot, current, payload)
        snapshot, current, payload, _ = fixture()
        proposal = renewal.prepare(snapshot, current, payload)
        proposal["cursorPayload"] = {"binding": {"x": [0]*10000}, "lastId": 99}
        with self.assertRaises(AnalysisContractError):
            renewal.validate(proposal, snapshot, current, payload)

    def test_rehashed_proposal_is_not_current_authority(self):
        snapshot, current, payload, _ = fixture()
        proposal = renewal.prepare(snapshot, current, payload)
        proposal["renewalAuthorized"] = True
        proposal["proposalDigest"] = digest({k: v for k, v in proposal.items() if k != "proposalDigest"})
        with self.assertRaises(AnalysisContractError):
            renewal.validate(proposal, snapshot, current, payload)

    def test_only_collecting_v2(self):
        for key, value in [("status", "sealed"), ("status", "cancelled"), ("evidenceSchema", "business-evidence-v1")]:
            snapshot, current, payload, _ = fixture()
            snapshot[key] = value
            with self.assertRaises(AnalysisContractError):
                renewal.prepare(snapshot, current, payload)

    def test_transport_alias_preserves_original_replay_chain(self):
        snapshot, current, payload, first = fixture()
        renewal.prepare(snapshot, current, payload)
        old_bytes = canonical(first)
        next_page = {"schemaVersion": "business-analysis-v1", "sourceRef": "a"*64, "control": None,
            "items": [{"rowId": "103", "metrics": {"spendCents": 300}}],
            "pagination": {"hasMore": False, "nextCursor": None}}
        next_page["pageEvidence"] = {"rowCount": 1, "sha256": digest(next_page["items"])}
        verifier = PageReconciler()
        verifier.consume(first)
        # A newly signed transport token must NOT replace the logical cursor.
        with self.assertRaises(AnalysisContractError):
            verifier.consume(next_page, request_cursor="renewed-transport-token")
        verifier.consume(next_page, request_cursor=first["pagination"]["nextCursor"])
        self.assertTrue(verifier.result()["reconciled"])
        self.assertEqual(verifier.result()["rowCount"], 3)
        self.assertEqual(canonical(first), old_bytes)
        replay = PageReconciler()
        replay.consume(first)
        replay.consume(next_page, request_cursor=replay.expected_cursor)
        self.assertEqual(replay.result(), verifier.result())
