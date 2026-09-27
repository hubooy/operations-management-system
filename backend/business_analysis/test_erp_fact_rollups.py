"""ERP-only five-grain rollups conserve every signed source amount."""
from copy import deepcopy
import hashlib
from unittest import TestCase
from unittest.mock import patch

from . import erp_fact_assignment as assignment, erp_fact_rollups as service
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER
from .test_erp_fact_assignment import SOURCES, PLAN, PAIR, fixture


class ErpFactRollupTests(TestCase):
    def open_ledger(self):
        sales_pages, sales_expected, master_pages, master_expected = fixture()
        return assignment.assign_facts(SOURCES, PLAN["plan"], PAIR,
            sales_pages, master_pages, sales_expected, master_expected)

    def test_five_tables_conserve_refunds_costs_and_unassigned_pool(self):
        with self.open_ledger() as ledger, service.prepare(ledger) as prepared:
            manifest = prepared.manifest
            self.assertEqual(manifest["schemaVersion"], service.SCHEMA)
            self.assertEqual([spec["kind"] for spec in manifest["tables"]],
                list(service.KINDS))
            self.assertEqual(manifest["sourceRowCount"], 5)
            self.assertEqual(manifest["sourceTotals"]["netSalesCents"], 155)
            self.assertEqual(manifest["sourceTotals"]["refundCents"], 30)
            self.assertEqual(manifest["sourceTotals"]["costCents"], 30)
            self.assertEqual(manifest["matchedTotals"]["netSalesCents"], 100)
            self.assertEqual(manifest["unassignedTotals"]["netSalesCents"], 55)
            self.assertEqual(manifest["unassignedTotals"]["refundCents"], 30)
            self.assertEqual(manifest["unassignedTotals"]["costCents"], -10)
            self.assertEqual([spec["sourceFactCount"] for spec in manifest["tables"]],
                [5, 1, 1, 1, 4])
            self.assertFalse(manifest["historicalOwnershipVerified"])
            self.assertFalse(manifest["netshopAdFinanceCombined"])
            self.assertFalse(manifest["authorityVerified"])
            for spec in manifest["tables"]:
                raw = b"".join(prepared.ndjson_pages(spec["kind"]))
                self.assertEqual(len(raw), spec["ndjsonBytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), spec["ndjsonSha256"])
            tables = prepared.tables()
            self.assertEqual([len(list(table.rows)) for table in tables],
                [spec["rowCount"] for spec in manifest["tables"]])
            category = list(prepared.tables()[1].rows)[0]
            keys = [column.key for column in prepared.tables()[1].columns]
            self.assertEqual(category[keys.index("netSalesCents")], 100)
            self.assertEqual(category[keys.index("refundCents")], 0)
            self.assertEqual(category[keys.index("costCents")], 40)
            self.assertEqual(len(category[keys.index("sourceRowDigest")]), 64)
            self.assertNotIn("sourceRowIds", keys)
            unassigned = list(prepared.tables()[-1].rows)
            self.assertEqual({row[keys.index("status")] for row in unassigned},
                {"ambiguous", "unmatched"})
            self.assertTrue(all(row[keys.index("skuId")] is None
                and row[keys.index("spuId")] is None for row in unassigned))
        with self.assertRaises(AnalysisContractError):
            prepared.manifest

    def test_duplicate_fact_missing_date_overflow_and_scratch_cap_reject(self):
        with self.open_ledger() as ledger:
            original = list(ledger.scan())
            duplicate_row = deepcopy(original[0])
            duplicate_row["rowIndex"] = 1
            duplicate = [original[0], duplicate_row, *original[2:]]
            missing = deepcopy(original)
            missing[0]["businessDate"] = None
            overflow = deepcopy(original)
            for row in overflow[:2]:
                row["metrics"].update(netSalesCents=MAX_SAFE_INTEGER,
                    positiveSalesCents=MAX_SAFE_INTEGER, refundCents=0,
                    costCents=0, grossProfitCents=MAX_SAFE_INTEGER)
            for facts in (duplicate, missing, overflow):
                with self.subTest(kind=len(facts)), patch.object(ledger, "scan",
                        return_value=iter(facts)), self.assertRaises(AnalysisContractError):
                    with service.prepare(ledger):
                        pass
            with self.assertRaises(AnalysisContractError):
                with service.prepare(ledger, max_scratch_bytes=4096):
                    pass

    def test_suspended_table_and_ndjson_iterators_fail_after_close(self):
        with self.open_ledger() as ledger, service.prepare(ledger) as prepared:
            stream = iter(prepared.tables()[0].rows)
            self.assertIsNotNone(next(stream))
            pages = iter(prepared.ndjson_pages("shop_day"))
            self.assertTrue(next(pages))
        with self.assertRaises(AnalysisContractError):
            next(stream)
        with self.assertRaises(AnalysisContractError):
            next(pages)
