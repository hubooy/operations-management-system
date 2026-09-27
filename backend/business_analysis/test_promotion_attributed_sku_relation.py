"""Synthetic actual-projection checks for the distinct followed-SKU grouping."""
from copy import deepcopy
import unittest

from . import promotion_attributed_sku_relation as relation
from . import promotion_keyword_sku as promoted
from . import test_promotion_views as fixture
from .contracts import AnalysisContractError, canonical


def fact(*, keyword="切肉机", term="商用切肉机", attributed="FOLLOW-1",
         promoted_sku="PROMOTED-1", spend=100, plan="P1", unit="U1"):
    value = fixture.fact(plan=plan, unit=unit, spend=spend)
    value["raw"].update({"关键词": keyword, "搜索词": term,
        "跟单SKU ID": attributed, "推广SKU": promoted_sku})
    return value


class AttributedSkuRelationTests(unittest.TestCase):
    def test_two_distinct_views_each_conserve_source_without_cross_addition(self):
        data = fixture.fixture([
            fact(spend=100),
            fact(term="饭店切肉机", spend=200),
            fact(attributed="FOLLOW-2", spend=300),
            fact(attributed=None, spend=400),
        ])
        totals = []
        for view in relation.VIEWS:
            with relation.table(*data, view=view) as table:
                header = table.header()
                rows = list(table.scan())
                totals.append(sum(row["metrics"]["spendCents"]["value"]
                    for row in rows))
                self.assertEqual(sum(row["currentRowCount"] for row in rows), 4)
                self.assertEqual(header["sourceTraversal"]["rows"], 4)
                self.assertFalse(header["crossViewAdditive"])
                self.assertFalse(header["attributedSkuIsProductMasterOwnership"])
                self.assertFalse(header["agentReadPersisted"])
                self.assertFalse(header["authorityVerified"])
                self.assertTrue(any(not row["identityQualified"] and
                    "attributedSkuId" in row["missingIdentityFields"]
                    for row in rows))
                self.assertTrue(all("promotedSkuId" not in row["entity"]
                    and "triggerSkuId" not in row["entity"]
                    for row in rows))
        self.assertEqual(totals, [1000, 1000])
        with promoted.table(*data, view="keyword_sku") as old:
            self.assertEqual(sum(row["metrics"]["spendCents"]["value"]
                for row in old.scan()), 1000)
            self.assertTrue(all("attributedSkuId" not in row["entity"]
                for row in old.scan()))

    def test_baseline_identity_missingness_and_exact_row_reference(self):
        current = fixture.fixture([fact(spend=150),
            fact(attributed=None, spend=20)])
        previous = fixture.fixture([fact(spend=50),
            fact(attributed=None, spend=10)], window="previous")
        with relation.table(*current, view="keyword_attributed_sku",
                **fixture.baseline(previous)) as table:
            rows = list(table.scan())
            qualified = next(row for row in rows if row["identityQualified"])
            self.assertEqual(qualified["entity"]["attributedSkuId"], "FOLLOW-1")
            self.assertEqual(qualified["comparisons"]["spendCents"]["difference"], 100)
            self.assertEqual(table.read_row(qualified["rowIndex"], qualified["id"]),
                qualified)
            with self.assertRaises(AnalysisContractError):
                table.read_row(qualified["rowIndex"], "0" * 64)
            missing = next(row for row in rows if not row["identityQualified"])
            self.assertEqual(missing["comparisons"]["spendCents"]["status"],
                "unavailable")

    def test_late_page_corruption_or_wrong_domain_rejects_complete_result(self):
        data = fixture.fixture([fact(spend=100), fact(spend=200),
            fact(spend=300)])
        source, pages, expected = data
        changed = deepcopy(pages)
        changed[-1]["items"][0]["dimensions"]["attributedSkuId"] = "FORGED"
        with self.assertRaises(AnalysisContractError):
            with relation.table(source, changed, expected,
                    view="keyword_attributed_sku"):
                pass
        wrong = {**source, "query": {**source["query"], "platform": "天猫"}}
        with self.assertRaises(AnalysisContractError):
            with relation.table(wrong, pages, expected,
                    view="keyword_attributed_sku"):
                pass

    def test_cooperative_cancellation_does_not_expose_partial_table(self):
        data = fixture.fixture([fact(), fact(spend=200), fact(spend=300)])
        cancelled = TimeoutError("relation cancelled")
        seen = 0
        def checkpoint(event):
            nonlocal seen
            if event.get("phase") == "page":
                seen += 1
                if seen == 2:
                    raise cancelled
        with self.assertRaises(TimeoutError) as raised:
            with relation.table(*data, view="keyword_attributed_sku",
                    checkpoint=checkpoint):
                self.fail("partial relation was exposed")
        self.assertIs(raised.exception, cancelled)

    def test_reference_row_count_fails_before_reading_any_pages(self):
        source, pages, expected = fixture.fixture([fact()])
        def forbidden():
            self.fail("oversize source must not be iterated")
            yield from pages
        for source_rows in (281_759, 293_336, 575_095):
            with self.subTest(source_rows=source_rows):
                oversized = deepcopy(expected)
                oversized["rowCount"] = source_rows
                with self.assertRaises(AnalysisContractError):
                    with relation.table(source, forbidden(), oversized,
                            view="keyword_searchterm_plan_unit_match_attributed_sku"):
                        self.fail("oversize source must not yield a table")

    def test_wide_complete_page_rejected_without_truncation(self):
        wide = fact(keyword="词" * 240, term="搜索" * 120,
            attributed="跟单" * 120, plan="计划" * 120,
            unit="单元" * 120)
        data = fixture.fixture([wide for _ in range(100)], limit=100)
        self.assertGreater(len(canonical(data[1][0]).encode("utf-8")),
            128 * 1024)
        with self.assertRaises(AnalysisContractError):
            with relation.table(*data,
                    view="keyword_searchterm_plan_unit_match_attributed_sku"):
                self.fail("wide page must not yield a partial table")
