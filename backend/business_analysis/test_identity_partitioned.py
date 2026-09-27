from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import tracemalloc
from unittest import TestCase
from unittest.mock import patch

from . import identity_partitioned as partitioned
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, PageReconciler, canonical, digest
from .identity import product_reconciliation


def pages(kind, count, *, records=None, shop="合成店", page_size=100):
    """Generate facts lazily; no full-source list hidden outside the measured run."""
    totals = ({"salesCents": count*(count-1)//2-count*(count//2), "quantity": count} if kind == "sales" else {})
    if records is not None:
        totals = {}
        for row in records:
            for key, value in row["metrics"].items(): totals[key] = totals.get(key, 0)+(value or 0)
        count = len(records)
    for start in range(0, max(count, 1), page_size):
        items = []
        for i in range(start, min(start+page_size, count)):
            row = {"rowId": str(i+1), "platform": "京东", "shopName": shop, "metrics": {}}
            if kind == "sales":
                row.update(onlineSpecCode=f"C{i:06}", productCode="NOT-A-FALLBACK",
                    metrics={"salesCents": i-count//2, "quantity": 1})
            else:
                row.update(skuId=f"S{i:06}", spuId=f"P{i:06}", dimensions={"merchantCode": f"C{i:06}"})
            items.append(deepcopy(records[i]) if records is not None else row)
        more = start+page_size < count
        yield {"schemaVersion": "business-analysis-v1", "source": "erp_sales" if kind == "sales" else "jd_product_master",
            "sourceDataset": None if kind == "sales" else "product_master", "sourceRef": kind+"-fixed",
            "filters": {"platform": "京东", "shop": shop}, "items": items,
            "control": {"rowCount": count, "typedTotals": totals} if start == 0 else None,
            "pageEvidence": {"rowCount": len(items), "sha256": digest(items)},
            "pagination": {"hasMore": more, "nextCursor": str(start+page_size) if more else None}}


def proof(blocks):
    verifier = PageReconciler()
    for page in blocks: verifier.consume(page, request_cursor=verifier.expected_cursor)
    return verifier.result()


class Once:
    def __init__(self, values): self.values, self.iterations, self.count = values, 0, 0
    def __iter__(self):
        self.iterations += 1
        if self.iterations != 1: raise AssertionError("source scanned twice")
        for item in self.values:
            self.count += 1
            yield item


class PartitionedIdentityTests(TestCase):
    def test_private_scratch_budget_can_only_reduce_fixed_limit(self):
        for invalid in (True, False, 0, -1, 1.5, "4096", partitioned.MAX_SCRATCH_BYTES+1):
            with self.subTest(invalid=invalid), self.assertRaises(AnalysisContractError):
                with partitioned.reconcile_products(pages("sales", 1), pages("master", 1),
                        max_scratch_bytes=invalid):
                    self.fail("invalid scratch limit accepted")
        with partitioned.reconcile_products(pages("sales", 2), pages("master", 2),
                max_scratch_bytes=128*1024*1024) as result:
            self.assertEqual(result.stats()["scratchLimitBytes"], 128*1024*1024)
            self.assertEqual(result.summary()["rowCount"], 2)

    def test_small_sample_matches_old_groups_totals_and_semantics(self):
        sales = list(pages("sales", 8))[0]["items"]
        masters = list(pages("master", 8))[0]["items"]
        masters[1].update(skuId=masters[0]["skuId"], spuId=masters[0]["spuId"], dimensions=deepcopy(masters[0]["dimensions"]))
        masters[3]["dimensions"] = deepcopy(masters[2]["dimensions"])
        sales[1]["onlineSpecCode"] = "C000000"  # exact duplicate candidate stays unique
        sales[3]["onlineSpecCode"] = "C000002"  # two distinct identities are ambiguous
        sales[4]["onlineSpecCode"] = None; sales[4]["productCode"] = "C000004"
        sales[5]["onlineSpecCode"] = ""; sales[5]["productCode"] = "C000005"
        sales[6]["onlineSpecCode"] = "UNKNOWN"
        left, right = list(pages("sales", 0, records=sales, page_size=3)), list(pages("master", 0, records=masters, page_size=3))
        old = product_reconciliation(left, right)
        with partitioned.reconcile_products(left, right, sales_expected=proof(left), master_expected=proof(right)) as result:
            summary, scanned = result.summary(), list(result.scan())
            for key in old.keys()-{"schemaVersion", "groups"}: self.assertEqual(summary[key], old[key], key)
            stripped = [{k:v for k,v in row.items() if k not in {"rowIndex", "id"}} for row in scanned]
            self.assertEqual(sorted(stripped, key=canonical), sorted(old["groups"], key=canonical))
            self.assertEqual(summary["coverage"], {"matched": 3, "ambiguous": 2, "unmatched": 3})
            self.assertEqual([row["rowIndex"] for row in scanned], list(range(len(scanned))))
            gathered, offset = [], 0
            while True:
                page = result.page(offset, 1)
                self.assertEqual(page["pageDigest"], digest({k:v for k,v in page.items() if k != "pageDigest"}))
                gathered.extend(page["rows"])
                offset = page["pagination"]["nextOffset"]
                if offset is None: break
            self.assertEqual(gathered, scanned)
            altered = result.summary(); altered["totals"].clear()
            self.assertEqual(result.summary()["totals"], old["totals"])

    def test_5000_and_5001_rows_without_legacy_cap_or_repeated_source_scan(self):
        for count in (5000, 5001):
            with self.subTest(count=count):
                sales, master = Once(pages("sales", count)), Once(pages("master", count))
                with partitioned.reconcile_products(sales, master) as result:
                    self.assertEqual(result.summary()["rowCount"], count)
                    self.assertEqual(result.summary()["groupCount"], count)
                    tail = result.page(count-1)
                    self.assertEqual(tail["rows"][0]["rowIndex"], count-1)
                    self.assertIsNone(tail["pagination"]["nextOffset"])
                    self.assertEqual(result.stats()["sourcePages"], ((count+99)//100)*2)
                self.assertEqual((sales.iterations, master.iterations), (1, 1))

    def test_30001_streaming_scale_memory_disk_and_full_scan(self):
        count = 30001
        started = time.monotonic()
        tracemalloc.start()
        try:
            sales, master = Once(pages("sales", count)), Once(pages("master", count))
            with partitioned.reconcile_products(sales, master) as result:
                stats = result.stats()
                observed, total = 0, 0
                for row in result.scan():
                    self.assertEqual(row["rowIndex"], observed)
                    observed += 1; total += row["metrics"]["salesCents"]
                self.assertEqual(observed, count)
                self.assertEqual(total, result.summary()["totals"]["salesCents"])
                self.assertEqual(result.page(30000)["rows"][0]["skuId"], "S030000")
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual((sales.iterations, master.iterations), (1, 1))
        self.assertEqual(stats["sourcePages"], 602)
        self.assertLess(peak, 16*1024*1024)
        self.assertLessEqual(stats["scratchBytes"], partitioned.MAX_SCRATCH_BYTES)
        self.assertLessEqual(stats["sourceBytes"], partitioned.MAX_SOURCE_BYTES)
        print(canonical({"case": "product-mapping-30001", "rowsPerSource": count,
            **stats, "pythonPeakBytes": peak, "elapsedSeconds": round(time.monotonic()-started, 3)}))

    def test_empty_and_context_lifecycle(self):
        with partitioned.reconcile_products(pages("sales", 0), pages("master", 0)) as result:
            self.assertEqual(result.summary()["coverage"], {"matched":0, "ambiguous":0, "unmatched":0})
            self.assertEqual(result.page()["rows"], [])
            scan = result.scan()
        for call in (result.summary, result.page, result.stats, lambda: next(scan)):
            with self.assertRaises(AnalysisContractError): call()
        with partitioned.reconcile_products(pages("sales", 2), pages("master", 2)) as result:
            scan = result.scan()
            self.assertEqual(next(scan)["rowIndex"], 0)
        with self.assertRaises(AnalysisContractError): next(scan)

    def test_wide_unicode_rows_use_complete_prefix_and_single_row_fails_before_publish(self):
        masters = list(pages("master", 3))[0]["items"]
        for i, row in enumerate(masters): row["skuId"] = "中"*5000+str(i)
        with partitioned.reconcile_products(pages("sales", 3), pages("master", 0, records=masters)) as result:
            first = result.page()
            self.assertEqual(len(first["rows"]), 2)
            self.assertEqual(first["pagination"]["nextOffset"], 2)
            self.assertLessEqual(len(canonical(first).encode()), 38000)
            self.assertEqual(first["rows"]+result.page(2)["rows"], list(result.scan()))
        masters[0]["skuId"] = "中"*13000
        published = False
        with self.assertRaises(AnalysisContractError):
            with partitioned.reconcile_products(pages("sales", 3), pages("master", 0, records=masters)):
                published = True
        self.assertFalse(published)

    def test_late_corruption_incomplete_replayed_and_rehashed_wrong_shop_never_publish(self):
        base = list(pages("sales", 201))
        bads = []
        changed = deepcopy(base); changed[-1]["items"][0]["metrics"]["salesCents"] += 1; bads.append(changed)
        bads.append(base[:-1]); bads.append(base+[base[-1]])
        changed = deepcopy(base); changed[-1]["items"][0]["shopName"] = "other"; changed[-1]["pageEvidence"]["sha256"] = digest(changed[-1]["items"]); bads.append(changed)
        changed = deepcopy(base); changed[-1]["sourceRef"] = "changed"; bads.append(changed)
        paths = []
        def directory(**kwargs):
            value = TemporaryDirectory(**kwargs); paths.append(Path(value.name)); return value
        for blocks in bads:
            with self.subTest(kind=len(blocks)), patch.object(partitioned, "TemporaryDirectory", side_effect=directory):
                published = False
                with self.assertRaises(AnalysisContractError):
                    with partitioned.reconcile_products(blocks, pages("master", 201)):
                        published = True
                self.assertFalse(published)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_wrong_scope_source_and_trusted_proof_rejected(self):
        for options in ({"master_pages": pages("master", 1, shop="other")},
                        {"sales_pages": pages("master", 1)},
                        {"sales_expected": {"reconciled": True}}, {"master_expected": {"reconciled": True}}):
            args = {"sales_pages": pages("sales", 1), "master_pages": pages("master", 1), **options}
            with self.subTest(options=list(options)), self.assertRaises(AnalysisContractError):
                with partitioned.reconcile_products(**args): pass

    def test_master_last_page_damage_rejected_before_any_publication(self):
        masters = list(pages("master", 201))
        masters[-1]["items"][0]["skuId"] = "CHANGED"
        sales = Once(pages("sales", 201))
        published = False
        with self.assertRaises(AnalysisContractError):
            with partitioned.reconcile_products(sales, masters):
                published = True
        self.assertFalse(published)
        self.assertEqual(sales.iterations, 0)

    def test_success_and_disk_full_also_remove_actual_private_directory(self):
        paths = []
        def directory(**kwargs):
            value = TemporaryDirectory(**kwargs); paths.append(Path(value.name)); return value
        with patch.object(partitioned, "TemporaryDirectory", side_effect=directory):
            with partitioned.reconcile_products(pages("sales", 2), pages("master", 2)) as result:
                self.assertTrue(paths[-1].exists())
                self.assertEqual(result.summary()["groupCount"], 2)
            self.assertFalse(paths[-1].exists())
            with patch.object(partitioned, "MAX_SCRATCH_BYTES", 4096), self.assertRaises(AnalysisContractError):
                with partitioned.reconcile_products(pages("sales", 2), pages("master", 2)): pass
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_capacity_limits_reject_atomically_and_remove_scratch(self):
        for constant, limit in (("MAX_SCRATCH_BYTES", 4096), ("MAX_RESULT_GROUPS", 1),
                                ("MAX_SOURCE_PAGES", 1), ("MAX_SOURCE_BYTES", 100), ("MAX_RESULT_BYTES", 50)):
            published = False
            with self.subTest(constant=constant), patch.object(partitioned, constant, limit), self.assertRaises(AnalysisContractError):
                with partitioned.reconcile_products(pages("sales", 3), pages("master", 3)):
                    published = True
            self.assertFalse(published)

    def test_missing_or_unsafe_money_and_invalid_pagination(self):
        for invalid in (None, True, MAX_SAFE_INTEGER+1):
            rows = list(pages("sales", 1))[0]["items"]
            rows[0]["metrics"]["salesCents"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(AnalysisContractError):
                with partitioned.reconcile_products(pages("sales", 0, records=rows), pages("master", 1)): pass
        with partitioned.reconcile_products(pages("sales", 1), pages("master", 1)) as result:
            for offset, limit in ((True, 20), (0, True), (0.0, 20), (-1, 20), (2,20), (0,101), (0,0)):
                with self.subTest(offset=offset, limit=limit), self.assertRaises(AnalysisContractError): result.page(offset, limit)

    def test_unicode_and_many_distinct_candidates_preserve_legacy_ambiguity(self):
        sales = list(pages("sales", 3))[0]["items"]
        masters = list(pages("master", 100))[0]["items"]
        for row in masters: row["dimensions"]["merchantCode"] = '𠮷"='
        for row in sales: row["onlineSpecCode"] = '𠮷"='
        left, right = list(pages("sales", 0, records=sales)), list(pages("master", 0, records=masters))
        old = product_reconciliation(left, right)
        with partitioned.reconcile_products(left, right) as result:
            actual = result.page()["rows"][0]
            self.assertEqual({k:v for k,v in actual.items() if k not in {"id", "rowIndex"}}, old["groups"][0])
            self.assertNotIn("candidateCount", actual)
