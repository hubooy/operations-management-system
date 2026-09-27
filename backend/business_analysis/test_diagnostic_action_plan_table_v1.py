"""The opt-in fourteenth Table must survive both writers byte-for-byte in data."""
from copy import deepcopy
import hashlib
import io
import json
from unittest import TestCase
import xml.etree.ElementTree as ET
import zipfile

from . import (diagnostic_action_plan_v1 as contract,
               report_composition_tables_v1 as delivery,
               report_composition_v1 as composition)
from .contracts import AnalysisContractError, canonical, digest
from .report_files import column_name, write_pair
from .test_diagnostic_action_plan_v1 import five_dimensions, market_result
from .test_report_composition_tables_v1 import inputs
from .test_report_composition_v1 import fixtures


_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def prepared():
    args, options = inputs(fixtures())
    summary = composition.compose_candidate(*args, **options)
    actions = five_dimensions(summary)
    actions[-1]["budget"] = {"principle": "manual_cap_required",
        "capCents": None, "capSource": "pending_approved_budget_plan"}
    candidate = contract.prepare_candidate(summary, actions, enabled=True,
        verify_current_composition=lambda current: current is summary)
    return args, options, summary, candidate


def _html_payload(raw):
    body = raw.decode("utf-8").split(
        '<script type="application/json" id="report-data">', 1)[1]
    return json.loads(body.split("</script>", 1)[0])


class DiagnosticActionPlanTableTests(TestCase):
    def test_opt_in_fourteenth_table_has_same_html_xlsx_rows_and_audit(self):
        args, options, summary, candidate = prepared()
        metadata, tables = delivery.prepare(*args, **options,
            diagnostic_action_candidate=candidate)
        self.assertEqual(metadata["schemaVersion"], delivery.DIAGNOSTIC_SCHEMA)
        self.assertEqual(tuple(metadata["tableKeys"]), delivery.DIAGNOSTIC_TABLE_KEYS)
        self.assertEqual(len(tables), 14)
        self.assertEqual(len(metadata["tableAudit"]), 13)
        self.assertEqual(tables[-2].key, "diagnostic_action_plan")
        self.assertEqual(tables[-2].row_count, candidate["actionCount"])
        self.assertEqual(metadata["diagnosticActionCandidateDigest"],
                         candidate["candidateDigest"])
        self.assertFalse(metadata["registeredRenderer"])
        self.assertFalse(metadata["agentReadPersisted"])

        xlsx, html = io.BytesIO(), io.BytesIO()
        writer = write_pair(xlsx, html, title="合成经营分析人工复核预览",
                            metadata=metadata, tables=tables, xlsx_opc_version=2)
        payload = _html_payload(html.getvalue())
        index = metadata["tableKeys"].index("diagnostic_action_plan")
        html_table = payload["tables"][index]
        receipt = writer["tables"][index]
        audit = metadata["tableAudit"][index]
        self.assertEqual(html_table["proof"], receipt)
        self.assertEqual(receipt["rowCount"], candidate["actionCount"])
        self.assertEqual(receipt["rowDigest"], audit["rowDigest"])
        self.assertEqual(hashlib.sha256("".join(canonical(row) + "\n" for row in
            html_table["rows"]).encode("utf-8")).hexdigest(), receipt["rowDigest"])
        self.assertEqual(audit["sourceDigest"], candidate["candidateDigest"])
        cols = {column.key: pos for pos, column in enumerate(tables[index].columns)}
        rows = html_table["rows"]
        for row, action in zip(rows, candidate["actions"]):
            self.assertEqual(row[cols["reportRoot"]],
                             canonical(candidate["reportRoot"]))
            self.assertEqual(row[cols["primarySourceDigest"]],
                             action["primaryEvidence"]["sourceDigest"])
            self.assertEqual(row[cols["primarySourceStatus"]],
                             action["primaryEvidence"]["status"])
            self.assertEqual(row[cols["ownerRole"]], action["ownerRole"])
            self.assertEqual(row[cols["stopConditions"]],
                             canonical(action["stopConditions"]))
            self.assertEqual(row[cols["rollback"]], action["rollback"])
            self.assertIsNone(row[cols["kpiBaselineValue"]])
            self.assertIsNone(row[cols["kpiTargetValue"]])
            self.assertFalse(row[cols["executionAllowed"]])
        self.assertEqual(rows[-1][cols["primarySourceStatus"]], "not_supplied")
        self.assertIsNone(rows[-1][cols["budgetCapCents"]])
        self.assertEqual(rows[-1][cols["budgetCapStatus"]], "pending_approved_cap")

        with zipfile.ZipFile(io.BytesIO(xlsx.getvalue())) as workbook:
            manifest = json.loads(workbook.read("teruisi-manifest.json"))
            self.assertEqual(manifest["tables"], writer["tables"])
            xml = ET.fromstring(workbook.read(
                f"xl/worksheets/sheet{index + 1}.xml"))
            sheet_rows = xml.findall(f".//{{{_NS}}}sheetData/{{{_NS}}}row")
            self.assertEqual(len(sheet_rows) - 3, candidate["actionCount"])
            parsed = []
            for data_row in sheet_rows[3:]:
                cells = {cell.attrib["r"]: cell for cell in
                         data_row.findall(f"{{{_NS}}}c")}
                values = []
                for col_index, column in enumerate(tables[index].columns, 1):
                    address = f"{column_name(col_index)}{data_row.attrib['r']}"
                    cell = cells[address]
                    kind = cell.attrib.get("t")
                    if kind == "inlineStr":
                        content = cell.find(f"{{{_NS}}}is/{{{_NS}}}t")
                        values.append(content.text if content is not None else "")
                    elif kind == "b":
                        values.append(cell.find(f"{{{_NS}}}v").text == "1")
                    elif kind == "n":
                        number = cell.find(f"{{{_NS}}}v").text
                        values.append(int(number) if column.kind == "integer"
                                      else float(number))
                    else:
                        values.append(None)
                parsed.append(values)
            self.assertEqual(parsed, html_table["rows"])
            self.assertEqual(hashlib.sha256("".join(canonical(row) + "\n"
                for row in parsed).encode("utf-8")).hexdigest(),
                receipt["rowDigest"])
            last = sheet_rows[-1]
            cells = {cell.attrib["r"]: cell for cell in last.findall(f"{{{_NS}}}c")}
            # Budget unknown and KPI targets stay empty native cells, not zero.
            for key in ("budgetCapCents", "kpiBaselineValue", "kpiTargetValue"):
                letter = column_name(cols[key] + 1)
                self.assertIsNone(cells[f"{letter}{len(sheet_rows)}"].find(f"{{{_NS}}}v"))
            self.assertNotIn(b"<f>", workbook.read(
                f"xl/worksheets/sheet{index + 1}.xml"))
        self.assertEqual(writer["tables"][-1]["rowDigest"],
                         metadata["auditTableDigest"])

    def test_legacy_thirteen_table_candidate_stays_version_one(self):
        args, options, _, _ = prepared()
        metadata, tables = delivery.prepare(*args, **options)
        self.assertEqual(metadata["schemaVersion"], delivery.SCHEMA)
        self.assertEqual(tuple(metadata["tableKeys"]), delivery.TABLE_KEYS)
        self.assertEqual(len(tables), 13)
        self.assertNotIn("diagnosticActionCandidateDigest", metadata)
        with self.assertRaises(AnalysisContractError):
            delivery.prepare(*args, **options, diagnostic_market_result={})

    def test_stale_or_modified_plan_cannot_be_projected(self):
        args, options, _, candidate = prepared()
        for edit in (lambda value: value["actions"][0]["primaryEvidence"].update(
                         sourceDigest="0" * 64),
                     lambda value: value["actions"][0]["budget"].update(
                         capCents=20000),
                     lambda value: value["actions"][0].update(
                         executionAllowed=True),
                     lambda value: value["reportRoot"].update(
                         compositionDigest="f" * 64),
                     lambda value: value.update(registeredRenderer=True)):
            bad = deepcopy(candidate); edit(bad)
            with self.subTest(edit=edit), self.assertRaises(AnalysisContractError):
                delivery.prepare(*args, **options,
                                 diagnostic_action_candidate=bad)

    def test_market_result_without_sample_or_verified_callback_cannot_render(self):
        args, options, _, candidate = prepared()
        with self.assertRaises(AnalysisContractError):
            delivery.prepare(*args, **options,
                diagnostic_action_candidate=candidate,
                diagnostic_market_result={})

    def test_market_five_role_result_only_yields_external_sample_reference(self):
        args, options = inputs(fixtures())
        preview = {"schemaVersion": "business-market-v2-fifth-read-preview-v1",
            "serverFullMarketMaterialVerified": True, "persistedRead": False,
            "registeredTool": False, "authorityVerified": False,
            "mode": "summary", "marketManifestDigest": "b" * 64,
            "sourceReportId": "separate-market-report"}
        preview["resultDigest"] = digest(preview)
        options["market_preview"] = preview
        proof = deepcopy(options["owning_proof"])
        proof["componentDigests"]["market"] = preview["resultDigest"]
        proof["bindingDigest"] = digest({key: part for key, part in proof.items()
                                         if key != "bindingDigest"})
        options["owning_proof"] = proof
        summary = composition.compose_candidate(*args, **options)
        actions = five_dimensions(summary)
        market = next(item for item in summary["tableManifest"]
                      if item["tableKey"] == "market_sample")
        actions[0]["contextEvidence"] = [{field: market[field]
                                          for field in contract.REF_FIELDS}]
        result = market_result()
        candidate = contract.prepare_candidate(summary, actions, enabled=True,
            verify_current_composition=lambda current: current is summary,
            market_result=result, verify_market_result=lambda *_: True)
        with self.assertRaises(AnalysisContractError):
            delivery.prepare(*args, **options,
                diagnostic_action_candidate=candidate,
                diagnostic_market_result=result)
        metadata, tables = delivery.prepare(*args, **options,
            diagnostic_action_candidate=candidate,
            diagnostic_market_result=result,
            verify_diagnostic_market_result=lambda *_: True)
        table = tables[-2]
        cols = {col.key: index for index, col in enumerate(table.columns)}
        self.assertEqual(metadata["schemaVersion"], delivery.DIAGNOSTIC_SCHEMA)
        self.assertEqual(json.loads(table.rows[0][cols["externalContextRefs"]])[0]
                         ["tableKey"], "market_sample")
        self.assertEqual(table.rows[0][cols["factBasis"]],
                         "table_level_candidate_only")
        self.assertIsNone(table.rows[0][cols["kpiBaselineValue"]])
        self.assertFalse(table.rows[0][cols["executionAllowed"]])
