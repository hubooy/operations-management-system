"""Pure replay probes; test callbacks deliberately carry no real authority."""
import copy
import hashlib
import unittest

from . import finance_collection_state_v4 as finance
from . import v4_sealer_finance_segment_replay as replay
from .contracts import AnalysisContractError, canonical, digest
from .test_finance_collection_state import owned_page, sources
from .test_finance_source import row


class V4FinanceSegmentReplayTests(unittest.TestCase):
    def fixture(self, count=2, *, empty=False):
        records = [] if empty else [row(index, subject_name=f"销售-{index}")
                                    for index in range(1, count + 1)]
        _, query, publication, owned_rows = sources(records,
            include_september=not empty, include_october=empty)
        count = 1 if empty else count
        state, chain, size = None, digest([]), 0
        pages, segments = [], []
        identity = {"runId": "run-1", "attemptId": "attempt-1",
            "actorEmail": "admin@example.test", "actorVersion": 1,
            "sourceId": "source-1", "sourceKey": "finance-current",
            "sourceRoot": "e" * 64, "sourceVersion": count + 1,
            "sourcePageCount": count, "sourceRowCount": len(owned_rows),
            "sourceStoredBytes": 0, "sourceRef": "b" * 64,
            "sourceRevision": "7:" + "a" * 64, "keyId": "f" * 16,
            "query": query}
        prior_digest = replay.ZERO
        for sequence in range(1, count + 1):
            page = owned_page(query, publication,
                owned_rows[sequence - 1:sequence] if not empty else [],
                offset=sequence - 1 if not empty else 0, total=len(owned_rows))
            expected = ({"query": query, "offset": 0, "afterId": 0} if state is None
                        else finance.next_arguments(state, trusted_query=query))
            state = finance.consume(state, page, trusted_query=query)
            raw = canonical(page)
            payload_digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            size += len(raw.encode("utf-8"))
            index = (sequence - 1) // 16 + 1
            record = {"source_key": identity["sourceKey"], "domain": "finance",
                "source_version": count + 1, "source_ref": identity["sourceRef"],
                "source_revision": identity["sourceRevision"],
                "segment_id": f"segment-{index}", "chunk_id": f"chunk-{sequence}",
                "page_sequence": sequence, "row_count": len(page["rows"]),
                "payload_json": raw, "payload_digest": payload_digest,
                "payload_bytes": len(raw.encode("utf-8")),
                "audit_id": f"audit-{sequence}",
                "request_id": f"request-{sequence}",
                "invocation_id": f"invocation-{sequence}",
                "tool_name": "get_business_finance_source_page",
                "arguments_digest": digest(expected),
                "response_digest": payload_digest,
                "run_bound_capability_verified": True}
            pages.append(record)
            chain = digest([chain, sequence, payload_digest, record["audit_id"],
                record["invocation_id"], identity["sourceRef"], identity["sourceRevision"]])
            if sequence == min(index * 16, count):
                progress = {"schemaVersion": replay.PROGRESS_SCHEMA,
                    "sourceKey": identity["sourceKey"], "domain": "finance",
                    "sourceRef": identity["sourceRef"],
                    "sourceRevision": identity["sourceRevision"],
                    "pageCount": sequence, "rowCount": sequence if not empty else 0,
                    "storedBytes": size, "lastChunkDigest": payload_digest,
                    "receiptChainDigest": chain, "domainState": state}
                progress_json = canonical(progress)
                progress_digest = hashlib.sha256(progress_json.encode("utf-8")).hexdigest()
                proof = {"schemaVersion": replay.ATTEMPT_SCHEMA,
                    "attemptId": identity["attemptId"], "runId": identity["runId"],
                    "sourceId": identity["sourceId"], "segmentIndex": index,
                    "startSequence": (index - 1) * 16 + 1,
                    "endSequence": sequence, "sourceVersion": count + 1,
                    "sourceRef": identity["sourceRef"],
                    "sourceRevision": identity["sourceRevision"],
                    "previousSegmentDigest": prior_digest,
                    "progressDigest": progress_digest, "keyId": identity["keyId"]}
                segment = {"segment_id": f"segment-{index}",
                    "segment_index": index, "start_sequence": proof["startSequence"],
                    "end_sequence": sequence, "source_version": count + 1,
                    "source_ref": identity["sourceRef"],
                    "source_revision": identity["sourceRevision"],
                    "previous_segment_digest": prior_digest,
                    "progress_json": progress_json, "progress_digest": progress_digest,
                    "proof_digest": digest(proof), "proof_mac": "d" * 64,
                    "run_bound_capability_verified": True}
                segments.append(segment)
                prior_digest = segment["proof_digest"]
        identity["sourceStoredBytes"] = size
        return identity, segments, pages

    @staticmethod
    def replay(identity, segment, pages, **kwargs):
        return replay.replay_finance_segment(identity, segment, pages,
            verify_claim=lambda *_: True,
            verify_segment_mac=lambda *_: True, **kwargs)

    def test_17_pages_cross_segment_and_require_previous_receipt(self):
        identity, segments, pages = self.fixture(17)
        first = self.replay(identity, segments[0], pages[:16])
        self.assertEqual(first["previousCandidateDigest"], replay.ZERO)
        self.assertEqual(first["progress"]["pageCount"], 16)
        self.assertFalse(first["progress"]["financeState"]["finished"])
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, segments[1], pages[16:], previous=first)
        second = self.replay(identity, segments[1], pages[16:], previous=first,
            verify_previous_result=lambda *_: True)
        self.assertEqual(second["previousCandidateDigest"], first["candidateDigest"])
        self.assertEqual(second["progress"]["rowCount"], 17)
        self.assertTrue(second["progress"]["financeState"]["finished"])
        self.assertTrue(second["candidateOnly"])
        self.assertTrue(second["financeReplayed"])
        self.assertFalse(second["authorityVerified"])
        self.assertFalse(second["sealCommitted"])

    def test_wrong_month_or_batch_is_rejected_even_with_rehashed_raw(self):
        identity, segments, pages = self.fixture()
        for mutate in (
            lambda page: page["rows"][0].update(month="2026-09"),
            lambda page: page["publication"]["months"][0].update(batch_id="foreign"),
        ):
            changed = copy.deepcopy(pages)
            page = finance_page(changed[0])
            mutate(page)
            page["pageEvidence"]["sha256"] = digest(page["rows"])
            page["pageDigest"] = digest({key: value for key, value in page.items()
                                         if key != "pageDigest"})
            replace_raw(changed[0], page)
            with self.subTest(mutate=mutate), self.assertRaises(AnalysisContractError):
                self.replay(identity, segments[0], changed)

    def test_changed_original_bytes_and_missing_page_are_rejected(self):
        identity, segments, pages = self.fixture()
        changed = copy.deepcopy(pages)
        changed[0]["payload_json"] = changed[0]["payload_json"].replace("销售-1", "变造")
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, segments[0], changed)
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, segments[0], pages[:1])
        changed = copy.deepcopy(pages)
        changed[1]["arguments_digest"] = "0" * 64
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, segments[0], changed)

    def test_forged_previous_candidate_cannot_continue(self):
        identity, segments, pages = self.fixture(17)
        first = self.replay(identity, segments[0], pages[:16])
        forged = copy.deepcopy(first)
        forged["segmentProofDigest"] = "0" * 64
        forged["candidateDigest"] = digest({key: value for key, value in forged.items()
                                             if key != "candidateDigest"})
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, segments[1], pages[16:], previous=forged,
                verify_previous_result=lambda *_: False)
        with self.assertRaises(AnalysisContractError):
            self.replay(identity, segments[1], pages[16:], previous=forged,
                verify_previous_result=lambda *_: True)

    def test_real_zero_row_page_keeps_missing_months(self):
        identity, segments, pages = self.fixture(empty=True)
        candidate = self.replay(identity, segments[0], pages)
        complete = finance.result(candidate["progress"]["financeState"],
                                  trusted_query=identity["query"])
        statuses = {item["month"]: item["metrics"]["net_sales"]["status"]
                    for item in complete["coverage"]}
        self.assertEqual(statuses["2026-08"], "missing_subject")
        self.assertEqual(statuses["2026-09"], "missing_month")
        self.assertEqual(statuses["2026-10"], "missing_month")
        self.assertEqual(candidate["progress"]["rowCount"], 0)

    def test_claim_and_mac_callbacks_are_mandatory(self):
        identity, segments, pages = self.fixture()
        with self.assertRaises(AnalysisContractError):
            replay.replay_finance_segment(identity, segments[0], pages)
        with self.assertRaises(AnalysisContractError):
            replay.replay_finance_segment(identity, segments[0], pages,
                verify_claim=lambda *_: True, verify_segment_mac=lambda *_: False)


def finance_page(item):
    import json
    return json.loads(item["payload_json"])


def replace_raw(item, page):
    raw = canonical(page)
    item["payload_json"] = raw
    item["payload_digest"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    item["response_digest"] = item["payload_digest"]
    item["payload_bytes"] = len(raw.encode("utf-8"))
