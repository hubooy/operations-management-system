"""Pure contract checks; callback stubs never represent real claim/MAC authority."""
import copy
import hashlib
import json
import unittest

from . import promotion_views, v4_sealer_segment_replay as replay
from .contracts import AnalysisContractError, PageReconciler, canonical, digest


class V4PromotionSegmentReplayTests(unittest.TestCase):
    def fixture(self, *, count=2):
        source_ref, revision = "a" * 64, "3:" + "b" * 12
        query = {"platform": "京东", "shop": "志高商用设备旗舰店",
            "dataset": "promotion", "startDate": "2026-08-25",
            "endDate": "2026-09-23", "window": "current"}
        verifier = PageReconciler()
        records, chain, size = [], digest([]), 0
        for sequence in range(1, count + 1):
            row = {"rowId": str(sequence), "sourceRowHash": "c" * 64,
                "batchId": "batch-1", "platform": "京东",
                "shopName": query["shop"], "date": "2026-08-25",
                "snapshotDate": "2026-09-23", "skuId": "sku-1", "spuId": "spu-1",
                "productCode": "code", "productName": "样例", "category": "商用设备",
                "dimensions": {key: "x" for key in promotion_views.DIMENSIONS},
                "metrics": {key: 1 for key in promotion_views.METRICS}}
            items = [row]
            page = {"schemaVersion": "business-analysis-v1", "sourceRef": source_ref,
                "items": items,
                "control": {"rowCount": count,
                    "typedTotals": {key: count for key in promotion_views.BASE_METRICS}}
                    if sequence == 1 else None,
                "pageEvidence": {"rowCount": 1, "sha256": digest(items)},
                "pagination": {"hasMore": sequence < count,
                    "nextCursor": f"next-{sequence}" if sequence < count else None}}
            cursor = verifier.expected_cursor
            expected = ({"domain": "netshop", **query, "limit": 100} if sequence == 1
                else {**query, "limit": 100, "cursor": cursor,
                    "expectedSourceRef": source_ref,
                    "expectedRevision": revision,
                    "expectedLastId": verifier.last_id})
            verifier.consume(page, request_cursor=cursor)
            raw = canonical(page)
            payload_digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            size += len(raw.encode("utf-8"))
            record = {"source_key": "current", "domain": "netshop",
                "source_version": count + 1, "source_ref": source_ref,
                "source_revision": revision, "segment_id": "segment-1",
                "chunk_id": f"chunk-{sequence}", "page_sequence": sequence,
                "row_count": 1, "payload_json": raw,
                "payload_digest": payload_digest, "payload_bytes": len(raw.encode("utf-8")),
                "audit_id": f"audit-{sequence}", "request_id": f"request-{sequence}",
                "invocation_id": f"invocation-{sequence}",
                "tool_name": "get_business_source_page" if sequence == 1
                    else "get_business_netshop_continuation_page",
                "arguments_digest": digest(expected),
                "response_digest": payload_digest,
                "run_bound_capability_verified": True}
            records.append(record)
            chain = digest([chain, sequence, payload_digest, record["audit_id"],
                record["invocation_id"], source_ref, revision])
        identity = {"runId": "run-1", "attemptId": "attempt-1",
            "actorEmail": "admin@example.test", "actorVersion": 1,
            "sourceId": "source-1", "sourceKey": "current",
            "sourceRoot": "e" * 64, "sourceVersion": count + 1,
            "sourcePageCount": count, "sourceRowCount": count,
            "sourceStoredBytes": size, "sourceRef": source_ref,
            "sourceRevision": revision, "keyId": "f" * 16, "query": query}
        progress = {"schemaVersion": replay.PROGRESS_SCHEMA,
            "sourceKey": "current", "domain": "netshop", "sourceRef": source_ref,
            "sourceRevision": revision, "pageCount": count, "rowCount": count,
            "storedBytes": size, "lastChunkDigest": records[-1]["payload_digest"],
            "receiptChainDigest": chain,
            "domainState": {"verifier": verifier.__dict__,
                "metadata": {}, "observedDates": ["2026-08-25"]}}
        progress_json = canonical(progress)
        progress_digest = hashlib.sha256(progress_json.encode("utf-8")).hexdigest()
        proof = {"schemaVersion": replay.ATTEMPT_SCHEMA,
            "attemptId": identity["attemptId"], "runId": identity["runId"],
            "sourceId": identity["sourceId"], "segmentIndex": 1,
            "startSequence": 1, "endSequence": count,
            "sourceVersion": count + 1, "sourceRef": source_ref,
            "sourceRevision": revision, "previousSegmentDigest": replay.ZERO,
            "progressDigest": progress_digest, "keyId": identity["keyId"]}
        segment = {"segment_id": "segment-1", "segment_index": 1,
            "start_sequence": 1, "end_sequence": count,
            "source_version": count + 1, "source_ref": source_ref,
            "source_revision": revision, "previous_segment_digest": replay.ZERO,
            "progress_json": progress_json, "progress_digest": progress_digest,
            "proof_digest": digest(proof), "proof_mac": "d" * 64,
            "run_bound_capability_verified": True}
        return identity, segment, records

    @staticmethod
    def replay(identity, segment, records, **kwargs):
        return replay.replay_promotion_segment(identity, segment, records,
            verify_claim=lambda *_: True,
            verify_segment_mac=lambda *_: True, **kwargs)

    def test_complete_segment_is_candidate_only_and_bounded(self):
        identity, segment, records = self.fixture()
        result = self.replay(identity, segment, records)
        self.assertEqual(result["progress"]["rowCount"], 2)
        self.assertEqual(result["progress"]["storedBytes"], identity["sourceStoredBytes"])
        self.assertTrue(result["candidateOnly"])
        self.assertFalse(result["authorityVerified"])
        self.assertFalse(result["sourceMetadataVerified"])
        self.assertFalse(result["sealCommitted"])
        self.assertEqual(result["candidateDigest"], digest({key: value for key, value
            in result.items() if key != "candidateDigest"}))

    def test_requires_external_claim_and_real_segment_mac_checks(self):
        identity, segment, records = self.fixture()
        with self.assertRaises(AnalysisContractError):
            replay.replay_promotion_segment(identity, segment, records)
        with self.assertRaises(AnalysisContractError):
            replay.replay_promotion_segment(identity, segment, records,
                verify_claim=lambda *_: True,
                verify_segment_mac=lambda *_: False)
        with self.assertRaises(AnalysisContractError):
            replay.replay_promotion_segment(identity, segment, records,
                verify_claim=lambda *_: False,
                verify_segment_mac=lambda *_: True)

    def test_changed_raw_bytes_and_request_digest_are_rejected(self):
        identity, segment, records = self.fixture()
        changed = copy.deepcopy(records)
        changed[0]["payload_json"] = changed[0]["payload_json"].replace("样例", "变造")
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, segment, changed)
        changed = copy.deepcopy(records)
        changed[1]["arguments_digest"] = "0" * 64
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, segment, changed)

    def test_progress_digest_uses_policy_raw_utf8_not_contracts_string_json(self):
        identity, segment, records = self.fixture()
        raw = segment["progress_json"]
        self.assertEqual(segment["progress_digest"],
            hashlib.sha256(raw.encode("utf-8")).hexdigest())
        self.assertNotEqual(segment["progress_digest"], digest(raw))
        changed = copy.deepcopy(segment)
        changed["progress_digest"] = digest(raw)
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, changed, records)

    def test_duplicate_page_and_forged_segment_progress_are_rejected(self):
        identity, segment, records = self.fixture()
        changed = copy.deepcopy(records)
        changed[1] = copy.deepcopy(changed[0])
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, segment, changed)
        changed_segment = copy.deepcopy(segment)
        changed_segment["previous_segment_digest"] = "0" * 63 + "1"
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, changed_segment, records)

    def test_finance_identity_and_unbounded_segment_are_rejected(self):
        identity, segment, records = self.fixture()
        changed = copy.deepcopy(identity)
        changed["query"]["dataset"] = "finance"
        with self.assertRaises(AnalysisContractError):
            self.replay(changed, segment, records)
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, segment, records + records)

    def test_seventeen_pages_resume_only_from_matching_previous_candidate(self):
        identity, terminal, records = self.fixture(count=17)
        first = copy.deepcopy(terminal)
        first["end_sequence"] = 16
        verifier, chain, size = PageReconciler(), digest([]), 0
        for record in records[:16]:
            raw = record["payload_json"]
            verifier.consume(json.loads(raw), request_cursor=verifier.expected_cursor)
            size += len(raw.encode("utf-8"))
            chain = digest([chain, record["page_sequence"], record["payload_digest"],
                record["audit_id"], record["invocation_id"],
                identity["sourceRef"], identity["sourceRevision"]])
        state = json.loads(first["progress_json"])
        state.update(pageCount=16, rowCount=16, storedBytes=size,
            lastChunkDigest=records[15]["payload_digest"], receiptChainDigest=chain)
        state["domainState"]["verifier"] = verifier.__dict__
        first["progress_json"] = canonical(state)
        first["progress_digest"] = hashlib.sha256(
            first["progress_json"].encode("utf-8")).hexdigest()
        proof = {"schemaVersion": replay.ATTEMPT_SCHEMA,
            "attemptId": identity["attemptId"], "runId": identity["runId"],
            "sourceId": identity["sourceId"], "segmentIndex": 1,
            "startSequence": 1, "endSequence": 16,
            "sourceVersion": identity["sourceVersion"],
            "sourceRef": identity["sourceRef"],
            "sourceRevision": identity["sourceRevision"],
            "previousSegmentDigest": replay.ZERO,
            "progressDigest": first["progress_digest"], "keyId": identity["keyId"]}
        first["proof_digest"] = digest(proof)
        accepted = self.replay(identity, first, records[:16])
        self.assertEqual(accepted["previousCandidateDigest"], replay.ZERO)
        self.assertEqual(accepted["progress"]["pageCount"], 16)
        self.assertFalse(accepted["progress"]["verifier"]["finished"])
        second = copy.deepcopy(terminal)
        second.update(segment_id="segment-2", segment_index=2,
            start_sequence=17, end_sequence=17,
            previous_segment_digest=accepted["segmentProofDigest"])
        proof.update(segmentIndex=2, startSequence=17, endSequence=17,
            previousSegmentDigest=accepted["segmentProofDigest"],
            progressDigest=second["progress_digest"])
        second["proof_digest"] = digest(proof)
        final_page = copy.deepcopy(records[16])
        final_page["segment_id"] = "segment-2"
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, second, [final_page], previous=accepted)
        protected = lambda candidate, *_: candidate["candidateDigest"] == accepted["candidateDigest"]
        final = self.replay(identity, second, [final_page], previous=accepted,
                           verify_previous_result=protected)
        self.assertEqual(final["previousCandidateDigest"], accepted["candidateDigest"])
        self.assertEqual(final["progress"]["pageCount"], 17)
        self.assertTrue(final["progress"]["verifier"]["finished"])
        wrong = copy.deepcopy(accepted)
        wrong["sourceRoot"] = "0" * 64
        wrong["candidateDigest"] = digest({key: value for key, value in wrong.items()
            if key != "candidateDigest"})
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, second, [final_page], previous=wrong,
                        verify_previous_result=protected)
        forged = copy.deepcopy(accepted)
        forged["progress"]["rowCount"] = 15
        forged["candidateDigest"] = digest({key: value for key, value in forged.items()
            if key != "candidateDigest"})
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, second, [final_page], previous=forged,
                        verify_previous_result=protected)
        oversized = copy.deepcopy(accepted)
        oversized["progress"]["verifier"]["evidence_digest"] = "x" * 100_000
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, second, [final_page], previous=oversized,
                        verify_previous_result=lambda *_: True)
        cyclic = copy.deepcopy(accepted)
        cyclic["cycle"] = cyclic
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, second, [final_page], previous=cyclic,
                        verify_previous_result=lambda *_: True)


if __name__ == "__main__":
    unittest.main()
