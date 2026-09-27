"""Synthetic real-projection pure tests; no database or model calls."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from . import promotion_keyword_sku as joint
from . import test_promotion_views as native_fixture
from .contracts import AnalysisContractError, canonical, digest


def fact(keyword="切肉机", sku="001", **kwargs):
    value = native_fixture.fact(**kwargs)
    value["raw"].update({"关键词": keyword, "推广SKU": sku, "搜索词": "不同的搜索词",
        "触发SKU ID": "TRIGGER", "跟单SKU ID": "ATTRIBUTED"})
    return value


def opened(data, **kwargs):
    return joint.table(*data, **kwargs)


class KeywordSkuTests(unittest.TestCase):
    def test_exact_roles_full_source_and_context_totals_conserved(self):
        rows = [fact(spend=100), fact(match="广泛", spend=200), fact(sku="002", spend=300),
            fact(keyword="绞肉机", spend=400), fact(sku=None, spend=500)]
        data = native_fixture.fixture(rows)
        for view, groups in (("keyword_sku", 4), ("keyword_sku_context", 5)):
            with opened(data, view=view) as table:
                result = list(table.scan())
                self.assertEqual(len(result), groups)
                self.assertEqual(sum(r["metrics"]["spendCents"]["value"] for r in result), 1500)
                self.assertEqual(sum(r["currentRowCount"] for r in result), 5)
                self.assertEqual(table.header()["sourceTraversal"], {"pages": 3, "rows": 5})
                self.assertFalse(table.header()["authorityVerified"])
                for row in result:
                    self.assertNotIn("skuId", row["entity"])
                    self.assertNotIn("searchTerm", row["entity"])
                    self.assertNotIn(row["entity"]["promotedSkuId"], {"same-sku", "TRIGGER", "ATTRIBUTED"})

    def test_no_fallback_missing_buckets_and_exact_unicode_identity(self):
        values = [fact(keyword=None), fact(sku=None), fact(keyword=None, sku=None),
            fact(keyword="未知", sku="001"), fact(keyword="未知", sku="1"), fact(keyword="𠮷", sku="001")]
        data = native_fixture.fixture(values)
        with opened(data, **native_fixture.baseline(native_fixture.fixture(values, window="previous"))) as table:
            rows = list(table.scan())
            self.assertEqual(len(rows), 6)
            self.assertEqual(sum(r["identityQualified"] for r in rows), 3)
            self.assertEqual(table.header()["identityCoverage"]["current"]["unqualifiedRows"], 3)
            for row in rows:
                if not row["identityQualified"]:
                    self.assertTrue(row["missingIdentityFields"])
                    self.assertTrue(all(c["status"] == "unavailable" for c in row["comparisons"].values()))

    def test_names_trigger_search_and_attribution_do_not_change_join(self):
        a, b = fact(), fact()
        b["raw"].update({"计划名称": "改名", "触发SKU ID": "OTHER", "搜索词": "新搜索", "跟单SKU ID": "OTHER"})
        with opened(native_fixture.fixture([a, b])) as table:
            self.assertEqual(table.header()["total"], 1)
            self.assertEqual(table.page()["rows"][0]["currentRowCount"], 2)

    def test_both_periods_and_views_stable_references_exact_lookup(self):
        data = native_fixture.fixture([fact()]); ids = []
        for view in joint.VIEWS:
            for window in ("previous", "yearAgo"):
                before = native_fixture.fixture([fact(spend=50)], window=window)
                snapshots = []
                for _ in range(2):
                    with opened(data, view=view, **native_fixture.baseline(before)) as table:
                        page = table.page(); row = page["rows"][0]; snapshots.append(page)
                        self.assertEqual(row["comparisons"]["spendCents"]["difference"], 50)
                        self.assertEqual(table.read_row(0, row["id"]), row)
                        with self.assertRaises(AnalysisContractError): table.read_row(0, "f" * 64)
                        with self.assertRaises(AnalysisContractError): table.read_row(True, row["id"])
                self.assertEqual(snapshots[0], snapshots[1]); ids.append(row["id"])
        self.assertEqual(len(set(ids)), 4)

    def test_missing_metrics_missing_side_and_incomplete_dates(self):
        a = native_fixture.fixture([fact(spend=None), fact(keyword="new")])
        b = native_fixture.fixture([fact(keyword="old")], window="previous")
        with opened(a, **native_fixture.baseline(b)) as table:
            rows = list(table.scan())
            self.assertEqual(len(rows), 3)
            for row in rows:
                self.assertEqual(row["comparisons"]["spendCents"]["status"], "unavailable")
                if row["currentRowCount"] is None: self.assertIsNone(row["metrics"])
                if row["baselineRowCount"] is None: self.assertIsNone(row["baselineMetrics"])
        a = native_fixture.fixture([fact()], last="2026-08-02")
        b = native_fixture.fixture([fact()], last="2026-08-02", window="previous")
        with opened(a, **native_fixture.baseline(b)) as table:
            self.assertFalse(table.header()["dateCoverageComparable"])
            self.assertEqual(table.page()["rows"][0]["comparisons"]["spendCents"]["status"], "unavailable")

    def test_weighted_rates_zero_negative_baselines_and_missing_money(self):
        data = native_fixture.fixture([fact(spend=100, clicks=1, impressions=10), fact(spend=300, clicks=3, impressions=90)])
        for value, status in ((0, "zero_baseline"), (-100, "negative_baseline"), (100, "comparable")):
            with opened(data, **native_fixture.baseline(native_fixture.fixture([fact(spend=value)], window="previous"))) as table:
                row = table.page()["rows"][0]
                self.assertEqual(row["ratios"]["ctr"], .04)
                self.assertEqual(row["comparisons"]["spendCents"]["status"], status)
                self.assertEqual(row["comparisons"]["spendCents"]["difference"], 400-value)
                self.assertIsNone(row["metrics"]["directGmvCents"]["value"])

    def test_cross_scope_windows_and_incomplete_baseline_rejected_before_read(self):
        data = native_fixture.fixture([fact()])
        for before in (native_fixture.fixture([fact()], shop="乙店", window="previous"),
                native_fixture.fixture([fact()], first="2026-08-02", last="2026-08-02", window="previous"),
                native_fixture.fixture([fact()], key="another")):
            with self.assertRaises(AnalysisContractError), opened(data, **native_fixture.baseline(before)): self.fail("yield")
        with self.assertRaises(AnalysisContractError), opened(data, baseline_source=data[0]): self.fail("yield")
        source = deepcopy(data[0]); source["query"]["platform"] = "天猫"
        with self.assertRaises(AnalysisContractError), joint.table(source, data[1], data[2]): self.fail("yield")

    def test_entire_tail_both_sources_verified_before_yield(self):
        a = native_fixture.fixture([fact(sku=str(i)) for i in range(5)])
        def late(pages):
            yield from pages
            raise RuntimeError("tail failed")
        for side in (0, 1):
            before = native_fixture.fixture([fact()], window="previous")
            with self.assertRaisesRegex(RuntimeError, "tail failed"):
                with joint.table(a[0], late(a[1]) if side == 0 else a[1], a[2], baseline_source=before[0],
                        baseline_pages=late(before[1]) if side == 1 else before[1], baseline_expected=before[2]):
                    self.fail("tail must be consumed first")
        source, pages, expected = deepcopy(a)
        pages[-1]["items"][0]["dimensions"]["promotedSkuId"] = "forged"
        pages[-1]["pageEvidence"]["sha256"] = digest(pages[-1]["items"])
        with self.assertRaises(AnalysisContractError), joint.table(source, pages, expected): self.fail("yield")

    def test_utf8_prefix_pages_all_rows_and_digest(self):
        data = native_fixture.fixture([fact(keyword="中"*180 + str(i), sku="商"*180,
            plan="计"*180, unit="单"*180, match="精"*180) for i in range(23)], limit=50)
        with opened(data, view="keyword_sku_context", limits={"maxResponseBytes":15000}) as table:
            offset = 0; rows = []
            while offset is not None:
                page = table.page(offset)
                self.assertLessEqual(len(canonical(page).encode("utf-8")), 15000)
                self.assertEqual(page["pageDigest"], digest({k:v for k,v in page.items() if k != "pageDigest"}))
                self.assertGreater(len(page["rows"]), 0)
                rows.extend(page["rows"]); offset = page["pagination"]["nextOffset"]
            self.assertEqual(rows, list(table.scan()))
            self.assertEqual([r["rowIndex"] for r in rows], list(range(23)))

    def test_capacity_strict_numbers_shared_both_sides_and_no_single_row_drop(self):
        data = native_fixture.fixture([fact(sku=str(i)) for i in range(3)])
        for limits in ({"maxGroups":2}, {"maxSourceRows":2}, {"maxPages":1}, {"maxScratchBytes":1},
                {"maxResponseBytes":1}, {"maxGroups":True}, {"maxGroups":1.0}, {"maxGroups":0}, {"unknown":1}, {"maxGroups":250001}):
            with self.subTest(limits=limits), self.assertRaises(AnalysisContractError), opened(data, limits=limits): pass
        with self.assertRaises(AnalysisContractError), opened(data, limits={"maxSourceRows":3},
                **native_fixture.baseline(native_fixture.fixture([fact()], window="previous"))): pass
        with opened(data) as table: size = len(canonical(table.page(3)).encode())
        with opened(data, limits={"maxResponseBytes":size+10}) as table:
            with self.assertRaises(AnalysisContractError): table.page()
            self.assertEqual(len(list(table.scan())), 3)

    def test_empty_source_and_invalid_offsets(self):
        with opened(native_fixture.fixture([])) as table:
            self.assertEqual(table.header()["total"], 0)
            self.assertEqual(table.page()["rows"], [])
            self.assertEqual(list(table.scan()), [])
            with self.assertRaises(AnalysisContractError): table.read_row(0, "a"*64)
            for offset in (True, 1.0, -1, 1):
                with self.assertRaises(AnalysisContractError): table.page(offset)

    def test_cancel_before_tail_and_swallowed_read_cancel_rethrown_after_cleanup(self):
        class Cancelled(RuntimeError): pass
        error = Cancelled("synthetic cancel")
        data = native_fixture.fixture([fact(sku=str(i)) for i in range(10)])
        def before(event):
            if event.get("phase") == "source_complete": raise error
        with self.assertRaises(Cancelled) as caught:
            with opened(data, checkpoint=before): self.fail("yield")
        self.assertIs(caught.exception, error)
        armed = False
        def callback(event):
            if armed and event.get("phase") == "read": raise error
        with self.assertRaises(Cancelled) as caught:
            with opened(data, checkpoint=callback) as table:
                path = Path(table._store.directory.name); armed = True
                try: table.page()
                except Cancelled: pass
        self.assertIs(caught.exception, error)
        self.assertFalse(path.exists())

    def test_sqlite_cancel_identity_and_half_scans_cleanup(self):
        error = RuntimeError("sqlite cancellation")
        armed = False
        def callback(event):
            if armed and event.get("phase") == "sqlite": raise error
        with self.assertRaises(RuntimeError) as caught:
            with opened(native_fixture.fixture([fact()]), checkpoint=callback) as table:
                path = Path(table._store.directory.name); armed = True
                table._store.db.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT sum(x) FROM n").fetchone()
        self.assertIs(caught.exception, error); self.assertFalse(path.exists())
        with opened(native_fixture.fixture([fact(sku=str(i)) for i in range(10)])) as table:
            path = Path(table._store.directory.name); streams = [table.scan(), table.scan()]
            for stream in streams: next(stream)
        self.assertFalse(path.exists())
        for stream in streams:
            with self.assertRaises(AnalysisContractError): next(stream)

    def test_disk_failure_and_consumer_error_cleanup(self):
        with patch.object(joint.PartitionedGroups, "consume", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(AnalysisContractError), opened(native_fixture.fixture([fact()])): self.fail("yield")
        with self.assertRaisesRegex(RuntimeError, "consumer"):
            with opened(native_fixture.fixture([fact()])) as table:
                path = Path(table._store.directory.name); stream = table.scan(); next(stream)
                raise RuntimeError("consumer")
        self.assertFalse(path.exists())

    def test_copies_and_old_contracts_unregistered(self):
        from . import promotion_views, results
        self.assertEqual(set(promotion_views.VIEWS), {"plan", "unit", "unit_match"})
        self.assertEqual(set(results.VIEWS), {"shop", "category", "spu", "sku", "keyword", "searchTerm", "daily", "brand"})
        with opened(native_fixture.fixture([fact()])) as table:
            original = table.page()
            modified = table.page(); modified["rows"][0]["metrics"]["spendCents"]["value"] = 999
            self.assertEqual(table.page(), original)
        with self.assertRaises(AnalysisContractError): table.page()
