from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, PageReconciler, digest
from .partitioned import PartitionedGroups
from .results import VIEWS, build_table, stream_table
from .test_results import fixture


def paged(page, size=100):
    items = page["items"]
    for start in range(0, max(len(items), 1), size):
        rows = items[start:start+size]
        more = start + size < len(items)
        yield {**page, "items": rows, "control": page["control"] if start == 0 else None,
            "pageEvidence": {"rowCount": len(rows), "sha256": digest(rows)},
            "pagination": {"hasMore": more, "nextCursor": str(start+size) if more else None}}


def proof_for(page):
    verifier = PageReconciler()
    for block in paged(page):
        verifier.consume(block, request_cursor=verifier.expected_cursor)
    return verifier.result()


class PartitionedTableTests(TestCase):
    def test_pages_equal_existing_math_ids_and_unicode_order(self):
        rows = [(word, amount, clicks, 100) for word, amount, clicks in
            [("𠮷", 100, 1), ("汉", 0, None), ('"=SUM(A1)', -20, 2), ("", 30, 3), ("𠮷", 200, 4)]]
        current, expected = fixture(rows=rows)
        for window in ("previous", "yearAgo"):
            baseline, before = fixture(window, [("缺期", 10, 1, 20), ("汉", None, 1, 100), ("𠮷", 80, 1, 100)])
            for dimension in VIEWS:
                args = {} if dimension == "daily" else {"baseline_pages": [baseline], "baseline_expected": before}
                complete = build_table([current], dimension, expected, **args)
                with stream_table([current], dimension, expected, **args) as (metadata, streamed):
                    self.assertEqual({**metadata, "rows": list(streamed)}, complete)
                for offset in range(complete["total"]+1):
                    result = build_table([current], dimension, expected, offset=offset, limit=1, **args)
                    self.assertEqual(result, {**complete, "rows": complete["rows"][offset:offset+1]})

    def test_more_than_25000_groups_last_page_complete_without_loading_full_result(self):
        page, _ = fixture(rows=[(f"词{i:06d}", i-15000, None if i % 3 == 0 else 2, 100) for i in range(30001)])
        expected = proof_for(page)
        result = build_table(paged(page), "keyword", expected, offset=29999, limit=100)
        self.assertEqual(result["total"], 30001)
        self.assertEqual([r["entity"]["keyword"] for r in result["rows"]], ["词029999", "词030000"])
        self.assertEqual([r["rowIndex"] for r in result["rows"]], [29999, 30000])
        self.assertIsNone(result["rows"][1]["metrics"]["clicks"]["value"])
        self.assertEqual(result["source"]["rowCount"], 30001)

    def test_bad_late_page_rejected_and_scratch_removed(self):
        page, expected = fixture()
        paths = []
        original = PartitionedGroups.__enter__
        def capture(store):
            result = original(store)
            paths.append(Path(store.directory.name))
            return result
        with patch.object(PartitionedGroups, "__enter__", capture):
            bad = deepcopy(page)
            bad["items"][-1]["metrics"]["spendCents"] = 999
            with self.assertRaises(AnalysisContractError):
                build_table([bad], "keyword", expected, limit=1)
            build_table([page], "keyword", expected, limit=1)
        self.assertTrue(paths)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_paged_analysis_cancellation_preserves_error_and_removes_scratch(self):
        page, _ = fixture(rows=[(f"词{i:03d}", i, 1, 100) for i in range(6)])
        expected = proof_for(page)
        paths = []
        original = PartitionedGroups.__enter__
        cancelled = TimeoutError("source page deadline")
        def capture(store):
            opened = original(store)
            paths.append(Path(store.directory.name))
            return opened
        def checkpoint(event):
            if event.get("stage") == "native_source_page" and event["page"] == 2:
                raise cancelled
        with patch.object(PartitionedGroups, "__enter__", capture):
            with self.assertRaises(TimeoutError) as raised:
                build_table(paged(page, size=1), "keyword", expected,
                    limit=1, checkpoint=checkpoint)
        self.assertIs(raised.exception, cancelled)
        self.assertTrue(paths and all(not path.exists() for path in paths))

    def test_partial_page_rollback_overflow_and_capacity(self):
        record = {"platform": "京东", "shopName": "A", "skuId": "1", "metrics": {"value": MAX_SAFE_INTEGER}}
        with PartitionedGroups() as store:
            store.configure(0, ["skuId"], ["value"])
            store.consume(0, [record])
            before = store.db.execute("SELECT payload FROM groups").fetchall()
            with self.assertRaises(AnalysisContractError):
                store.consume(0, [{**record, "skuId": "2"}, {**record, "metrics": {"value": 1}}])
            self.assertEqual(store.db.execute("SELECT payload FROM groups").fetchall(), before)
            self.assertEqual(store.counts[0], 1)
            with patch("business_analysis.partitioned.MAX_RESULT_GROUPS", 1):
                with self.assertRaises(AnalysisContractError):
                    store.consume(0, [{**record, "skuId": "2"}])
            self.assertEqual(store.db.execute("SELECT payload FROM groups").fetchall(), before)
            with self.assertRaises(AnalysisContractError):
                store.page(0, 1)

    def test_invalid_pagination_empty_evidence_and_union_capacity(self):
        empty, proof = fixture(rows=[])
        self.assertEqual(build_table([empty], "keyword", proof, limit=1)["rows"], [])
        for offset, limit in ((-1, 1), (True, 1), (0, 101), (0, 0), (250001, 1)):
            with self.assertRaises(AnalysisContractError):
                build_table([empty], "keyword", proof, offset=offset, limit=limit)
        current, proof = fixture(rows=[("A", 1, 1, 1)])
        baseline, before = fixture("previous", [("B", 1, 1, 1)])
        with patch("business_analysis.partitioned.MAX_RESULT_GROUPS", 1):
            with self.assertRaises(AnalysisContractError):
                build_table([current], "keyword", proof, limit=1, baseline_pages=[baseline], baseline_expected=before)
