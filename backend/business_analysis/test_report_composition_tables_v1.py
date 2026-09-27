"""Same Table stream proves synthetic HTML/XLSX row parity without publication."""
from copy import deepcopy
import hashlib
import io
import json
from unittest import TestCase
import xml.etree.ElementTree as ET
import zipfile

from . import (cross_source_sku_window_compare as sku,
               cross_source_window_compare as shop,
               finance_b2b_source_proof as finance_b2b,
               report_composition_tables_v1 as delivery)
from .contracts import AnalysisContractError, canonical, digest
from .report_files import write_pair
from .test_cross_source_daily_columns import CONTEXT
from .test_report_composition_v1 import fixtures


def inputs(case):
    plan, sources, infos, keys, materials, category = case
    shop_result = shop.prepare_candidate(plan, sources, infos, CONTEXT,
                                         keys, materials)
    sku_result = sku.prepare_candidate(plan, sources, infos, CONTEXT,
                                       keys, materials)
    finance = finance_b2b.build_candidate()
    binding = {"schemaVersion": "business-report-composition-owning-proof-v1",
        "reportId": plan["reportId"], "planDigest": plan["planDigest"],
        "evidenceRunId": CONTEXT["evidenceRunId"],
        "sealedDigest": CONTEXT["sealedDigest"],
        "materialDigests": {window: material["materialDigest"]
            for window, material in materials.items()},
        "componentDigests": {"store": shop_result["comparisonDigest"],
            "sku": sku_result["comparisonDigest"],
            "categorySpu": category["comparisonDigest"],
            "keyword": None, "financeB2b": finance["candidateDigest"],
            "market": None},
        "externalContextSameReportClaimed": False}
    proof = {**binding, "bindingDigest": digest(binding)}
    return (plan, sources, infos, CONTEXT, keys, materials), {
        "category_spu_result": category, "owning_proof": proof,
        "verify_owning_proof": lambda supplied, expected:
            supplied["bindingDigest"] == digest(expected)}


class ReportCompositionTablesTests(TestCase):
    def test_same_table_stream_html_xlsx_and_audit_rows_match(self):
        args, options = inputs(fixtures())
        metadata, tables = delivery.prepare(*args, **options)
        self.assertEqual([table.key for table in tables], list(delivery.TABLE_KEYS))
        self.assertEqual(len(metadata["tableAudit"]), 12)
        self.assertEqual(tables[-1].row_count, 12)
        self.assertEqual(metadata["tableAudit"][0]["rowCount"], tables[0].row_count)
        self.assertTrue(all(table.row_count == len(table.rows) for table in tables))
        self.assertFalse(metadata["authorityVerified"])
        xlsx, html = io.BytesIO(), io.BytesIO()
        result = write_pair(xlsx, html, title="合成三期经营组合",
            metadata=metadata, tables=tables, xlsx_opc_version=2)
        html_text = html.getvalue().decode("utf-8")
        raw = html_text.split('<script type="application/json" id="report-data">', 1)[1]
        payload = json.loads(raw.split("</script>", 1)[0])
        self.assertEqual(len(payload["tables"]), len(tables))
        with zipfile.ZipFile(io.BytesIO(xlsx.getvalue())) as workbook:
            archive = json.loads(workbook.read("teruisi-manifest.json"))
            self.assertEqual(archive["tables"], result["tables"])
            for index, (table, proof) in enumerate(zip(tables, result["tables"]), 1):
                self.assertEqual(payload["tables"][index-1]["proof"], proof)
                self.assertEqual(len(payload["tables"][index-1]["rows"]),
                                 proof["rowCount"])
                self.assertEqual(hashlib.sha256("".join(canonical(row)+"\n"
                    for row in payload["tables"][index-1]["rows"]).encode(
                        "utf-8")).hexdigest(), proof["rowDigest"])
                xml = ET.fromstring(workbook.read(
                    f"xl/worksheets/sheet{index}.xml"))
                rows = xml.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheetData/"
                                   "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row")
                self.assertEqual(len(rows)-3, proof["rowCount"])
                if index <= 12:
                    audit = metadata["tableAudit"][index-1]
                    self.assertEqual(proof["rowCount"], audit["rowCount"])
                    self.assertEqual(proof["columnCount"], audit["columnCount"])
                    self.assertEqual(proof["rowDigest"], audit["rowDigest"])
        self.assertEqual(result["tables"][-1]["rowDigest"],
                         metadata["auditTableDigest"])

    def test_missing_comparisons_and_historical_erp_stay_null_in_delivered_rows(self):
        metadata, tables = delivery.prepare(*inputs(fixtures())[0],
                                            **inputs(fixtures())[1])
        lookup = {table.key: table for table in tables}
        for key in ("category", "spu_erp"):
            self.assertTrue(lookup[key].row_count)
            self.assertTrue(all(row[10] == "historical_identity_unverified"
                and row[11] is None and row[12] is None
                for row in lookup[key].rows))
        self.assertTrue(any(row[8] == "missing_source" and row[9] is None
            for row in lookup["store_comparison"].rows))
        self.assertEqual(lookup["finance_month"].columns[0].key, "periodRole")
        self.assertFalse(any(column.key in {"salesCents", "profitCents"}
            for key in ("finance_month", "b2b_daily", "market_sample")
            for column in lookup[key].columns))
        self.assertFalse(metadata["agentReadPersisted"])

    def test_proof_rejects_and_changed_material_cannot_render(self):
        case = fixtures()
        args, options = inputs(case)
        with self.assertRaises(AnalysisContractError):
            delivery.prepare(*args, **{**options,
                "verify_owning_proof": lambda *_: False})
        changed = deepcopy(case)
        changed[4]["current"]["shopDayRows"][0]["erpSales"][
            "netSalesCents"]["value"] += 1
        changed[4]["current"]["materialDigest"] = digest({key: value
            for key, value in changed[4]["current"].items()
            if key != "materialDigest"})
        with self.assertRaises(AnalysisContractError):
            delivery.prepare(*inputs(changed)[0], **inputs(changed)[1])

    def test_v2_row_cap_refuses_truncation(self):
        args, options = inputs(fixtures())
        from unittest.mock import patch
        with patch.object(delivery, "MAX_ROWS", 1), self.assertRaises(AnalysisContractError):
            delivery.prepare(*args, **options)
