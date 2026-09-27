"""Pure v4 followed-SKU grouping from synthetic complete replay pages."""
from copy import deepcopy
from unittest import TestCase

from . import promotion_attributed_sku_relation_v4 as relation
from . import test_promotion_relation_v4 as fixture
from .contracts import AnalysisContractError, canonical, digest


def data(rows, *, window="current"):
    source, pages, proof = fixture.data(rows, window=window)
    proof["payloadCursorChainVerified"] = True
    proof["requestCursorAuditVerified"] = True
    proof["proofDigest"] = digest({key:value for key,value in proof.items()
        if key != "proofDigest"})
    return source, pages, proof


class AttributedSkuRelationV4Tests(TestCase):
    def test_exact_six_keys_and_missing_bucket_conserve_each_metric(self):
        records = [fixture.fact(1, keyword="K", search="S", attributed="A", spend=100),
            fixture.fact(2, keyword="K", search="S", attributed="A", spend=200),
            fixture.fact(3, keyword="K", search=None, attributed=None, spend=300)]
        with relation.table(*data(records),
                view="keyword_searchterm_plan_unit_match_attributed_sku") as result:
            header = result.header()
            rows = list(result.scan())
            self.assertEqual(header["total"], 2)
            self.assertEqual(sum(row["currentRowCount"] for row in rows), 3)
            self.assertEqual(sum(row["metrics"]["spendCents"]["value"] for row in rows), 600)
            self.assertEqual(sum(row["metrics"]["reportedGmvCents"]["value"] for row in rows), 1500)
            self.assertEqual(header["identityCoverage"],
                {"qualifiedRows":2, "unqualifiedRows":1})
            self.assertTrue(any(not row["identityQualified"] and
                set(row["missingIdentityFields"]) == {"searchTerm", "attributedSkuId"}
                for row in rows))
            self.assertFalse(header["authorityVerified"])
            self.assertFalse(header["agentReadPersisted"])
            self.assertFalse(header["crossViewAdditive"])
            self.assertEqual(header["sourceWindow"], "current")
            self.assertEqual(result.page()["rows"], rows)
            self.assertEqual(result.read_row(rows[0]["rowIndex"], rows[0]["id"]),
                rows[0])

    def test_windows_are_independent_and_wrong_proof_refused(self):
        for window in ("current", "previous", "yearAgo"):
            with self.subTest(window=window):
                with relation.table(*data([fixture.fact(1)], window=window),
                        view="keyword_attributed_sku") as result:
                    self.assertEqual(result.header()["sourceWindow"], window)
        source, pages, proof = data([fixture.fact(1), fixture.fact(2)])
        altered = deepcopy(proof)
        altered["requestCursorAuditVerified"] = False
        altered["proofDigest"] = digest({key:value for key,value in altered.items()
            if key != "proofDigest"})
        with self.assertRaises(AnalysisContractError):
            with relation.table(source, pages, altered, view="keyword_attributed_sku"):
                self.fail("unverified request cursor cannot pass")
        with self.assertRaises(AnalysisContractError):
            with relation.table(source, pages[:-1], proof, view="keyword_attributed_sku"):
                self.fail("incomplete source cannot pass")
        changed = deepcopy(pages)
        changed[0]["items"][0]["metrics"]["spendCents"] += 1
        with self.assertRaises(AnalysisContractError):
            with relation.table(source, changed, proof, view="keyword_attributed_sku"):
                self.fail("changed spend cannot pass source reconciliation")

    def test_cancel_wide_page_and_tight_scratch_fail_without_table(self):
        source, pages, proof = data([fixture.fact(i, keyword=f"K{i}")
            for i in range(101)])
        stopped = TimeoutError("cancel v4 relation")
        def cancel(event):
            if event.get("phase") == "before_next" and event.get("page") == 2:
                raise stopped
        with self.assertRaises(TimeoutError) as raised:
            with relation.table(source, iter(pages), proof,
                    view="keyword_attributed_sku", checkpoint=cancel):
                self.fail("partial result must not appear")
        self.assertIs(raised.exception, stopped)
        with self.assertRaises(AnalysisContractError):
            with relation.table(source, pages, proof,
                    view="keyword_attributed_sku", max_scratch_bytes=1):
                self.fail("scratch budget must reject")
        changed = deepcopy(pages)
        changed[0]["metricSemantics"]["padding"] = "宽" * 70_000
        self.assertGreater(len(canonical(changed[0]).encode("utf-8")), 131_072)
        with self.assertRaises(AnalysisContractError):
            with relation.table(source, changed, proof, view="keyword_attributed_sku"):
                self.fail("wide page must not be truncated")
