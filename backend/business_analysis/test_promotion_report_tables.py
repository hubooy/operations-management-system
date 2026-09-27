from copy import deepcopy
import hashlib
import io
import json
from unittest import TestCase
from unittest.mock import patch
import zipfile
from . import promotion_report_tables as service, promotion_keyword_sku
from .test_promotion_keyword_sku import fact
from .test_promotion_views import fixture
from .contracts import AnalysisContractError, canonical, digest
from .report_files import write_pair


def materials(facts=None, with_baseline=False):
    data = fixture(facts if facts is not None else [fact(keyword='中<&"=SUM(A1)', spend=-20), fact(sku=None, spend=None)])
    prior = fixture([fact(keyword='中<&"=SUM(A1)', spend=50)], window="previous") if with_baseline else None
    kwargs = {"baseline_source": prior[0], "baseline_pages": prior[1], "baseline_expected": prior[2]} if prior else {}
    specs, streams = [], {}
    for view in service.VIEWS:
        with promotion_keyword_sku.table(*data, view=view, **kwargs) as table:
            header = table.header(); rows = list(table.scan())
        pages = [("".join(canonical(row)+"\n" for row in rows[offset:offset+20])).encode() for offset in range(0,len(rows),20)] or [b""]
        totals = {"value": None, "presentGroups": 0, "missingFactRows": 0}
        for row in rows:
            cell = row["metrics"]["spendCents"]; totals["missingFactRows"] += cell["missingRows"]
            if cell["value"] is not None:
                totals["value"] = (totals["value"] or 0)+cell["value"]; totals["presentGroups"] += 1
        baseline_totals = {"value": None, "presentGroups": 0, "missingFactRows": 0}
        for row in rows:
            cell = (row["baselineMetrics"] or {}).get("spendCents")
            if cell is not None:
                baseline_totals["missingFactRows"] += cell["missingRows"]
                if cell["value"] is not None:
                    baseline_totals["value"] = (baseline_totals["value"] or 0)+cell["value"]
                    baseline_totals["presentGroups"] += 1
        binding = {"reportBinding": {"reportId": "synthetic"}, "sourceKey": data[0]["key"], "baselineKey": prior[0]["key"] if prior else None,
                   "view": view, "algorithmVersion": promotion_keyword_sku.ALGORITHM_VERSION, "tableBindingDigest": header["tableBindingDigest"]}
        specs.append({"view": view, "binding": binding, "header": header, "rowCount": len(rows), "pageCount": len(pages),
            "ndjsonBytes": sum(map(len,pages)), "ndjsonSha256": hashlib.sha256(b"".join(pages)).hexdigest(),
            "spendTotals": {"current": totals, "baseline": baseline_totals},
            "missingPromotedSkuGroups": sum(row["entity"]["promotedSkuId"] is None for row in rows),
            "unqualifiedIdentityGroups": sum(not row["identityQualified"] for row in rows)})
        streams[view] = pages
    manifest = {"schemaVersion": "business-promotion-export-materials-v1", "reportBinding": {"reportId": "synthetic"},
        "sourceKey": data[0]["key"], "baselineKey": prior[0]["key"] if prior else None, "algorithmVersion": promotion_keyword_sku.ALGORITHM_VERSION,
        "tables": specs, "rowCount": sum(s["rowCount"] for s in specs), "ndjsonBytes": sum(s["ndjsonBytes"] for s in specs),
        "tableExpensesAreAdditive": False, "registeredRenderer": False, "limitations": ["synthetic"]}
    manifest["manifestDigest"] = digest(manifest)
    return manifest, streams


class PromotionReportTablesTests(TestCase):
    def test_baseline_value_and_full_comparison_state_are_preserved(self):
        with service.tables(*materials(with_baseline=True)) as (_,tables):
            for table in tables:
                index = next(i for i,c in enumerate(table.columns) if c.key == "baselineMetrics.spendCents.value")
                rows = list(table.rows)
                self.assertEqual([r[index] for r in rows if r[index] is not None], [50])
                self.assertTrue(any(json.loads(r[-1])["comparisons"] for r in rows))
    def test_readable_explicit_headers_nulls_negative_and_missing_sku(self):
        manifest, streams = materials(); before = deepcopy(manifest)
        with service.tables(manifest, streams) as (summary, tables):
            self.assertFalse(summary["authorityVerified"]); self.assertFalse(summary["expensesAreAdditive"])
            for table in tables:
                self.assertEqual(len(table.columns), 66)
                self.assertTrue(all(not column.total for column in table.columns))
                self.assertIn("本期·推广费用（分）", [c.label for c in table.columns])
                rows = list(table.rows)
                cost = next(i for i,c in enumerate(table.columns) if c.key == "metrics.spendCents.value")
                self.assertEqual(sorted([r[cost] for r in rows if r[cost] is not None]), [-20])
                self.assertTrue(any(r[3] is None for r in rows))
                self.assertTrue(any(r[cost] is None for r in rows))
        self.assertEqual(manifest, before)

    def test_actual_pair_writers_escape_xml_and_preserve_raw_text(self):
        with service.tables(*materials()) as (summary,tables):
            xlsx, html = io.BytesIO(), io.BytesIO()
            write_pair(xlsx, html, title="词货核验", metadata=summary, tables=tables, xlsx_opc_version=2)
        with zipfile.ZipFile(io.BytesIO(xlsx.getvalue())) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn("本期·推广费用", sheet)
            self.assertIn("中&lt;&amp;", sheet)
            self.assertNotIn("<f>", sheet)
        self.assertIn("关键词与推广SKU", html.getvalue().decode())

    def test_more_than_100_rows_multichunk_stream_complete_and_closed_after_context(self):
        manifest, streams = materials([fact(sku=str(i), spend=i) for i in range(105)])
        with service.tables(manifest, {k:iter(v) for k,v in streams.items()}) as (_, tables):
            self.assertEqual([len(list(t.rows)) for t in tables], [105,105])
        with service.tables(*materials()) as (_, tables): pending = tables[0].rows
        with self.assertRaises(AnalysisContractError): next(pending)

    def test_manifest_hash_chunk_hash_count_and_table_binding_tampering_fail_before_yield(self):
        for change in (lambda m,s: m.update(manifestDigest="0"*64), lambda m,s: s["keyword_sku"].__setitem__(0,s["keyword_sku"][0]+b" "),
                       lambda m,s: m["tables"][0].update(rowCount=99),
                       lambda m,s: m["tables"][0]["binding"].update(tableBindingDigest="0"*64)):
            m,s = materials(); change(m,s)
            if m["manifestDigest"] != "0"*64: m["manifestDigest"] = digest({k:v for k,v in m.items() if k != "manifestDigest"})
            with self.assertRaises(AnalysisContractError):
                with service.tables(m,s): self.fail("partial material escaped")

    def test_capacities_xml_controls_and_unsafe_integer_reject(self):
        for setting, value in (("MAX_ROWS",1),("MAX_BYTES",100),("MAX_CHUNK_BYTES",10)):
            with patch.object(service,setting,value), self.assertRaises(AnalysisContractError):
                with service.tables(*materials()): pass
        m,s = materials(); spec = m["tables"][0]; row = json.loads(s["keyword_sku"][0].splitlines()[0])
        for value in ("bad\x01", "x"*32768):
            changed = deepcopy(row); changed["entity"]["keyword"] = value
            changed["id"] = digest([spec["header"]["tableBindingDigest"],changed["entity"]])
            with self.assertRaises(AnalysisContractError): service._project(changed,"keyword_sku",spec["header"]["tableBindingDigest"],changed["rowIndex"])
        row["metrics"]["spendCents"]["value"] = 2**53
        with self.assertRaises(AnalysisContractError): service._project(row,"keyword_sku",spec["header"]["tableBindingDigest"],row["rowIndex"])
