"""Independent raw-byte XLSX parser vectors; no database or Worker parser."""
from __future__ import annotations

import io
import hashlib
from decimal import Decimal
from pathlib import Path
import zipfile
from unittest.mock import patch

from django.test import SimpleTestCase

from finance import (raw_column_evidence_v2 as sidecar,
    workbook_bytes_v2 as source, workbook_column_evidence_v2 as evidence)


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
FIXTURE = (Path(__file__).resolve().parent / "fixtures" /
    "raw_bytes_v2_cross_group.xlsx")


def _cell(ref, value, *, formula=False):
    if type(value) is str:
        return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
    return (f'<c r="{ref}">{"<f>1+1</f>" if formula else ""}'
        f'<v>{value}</v></c>')


def _xlsx(*, amount=100, second=200, formula=False,
          duplicate_month=False, shared_count=0):
    rows = [
        (1, [("A1", "2026年1月"), ("B1", "事业部")]),
        (2, [("C2", "京东组"), ("E2", "天猫组")]),
        (3, [("C3", "组汇总"), ("D3", "同名店"),
             ("E3", "组汇总"), ("F3", "同名店")]),
        (4, [("A4", "一①销售额"), ("B4", 500), ("C4", 200),
             ("D4", amount), ("E4", 200), ("F4", second)]),
        (5, [("A5", "一③实际销售金额"), ("B5", 500), ("C5", 200),
             ("D5", amount), ("E5", 200), ("F5", second)]),
        (6, [("A6", "金蝶科目名称")]),
        (7, [("A7", "销售费用_推广"), ("B7", 50), ("C7", 20),
             ("D7", 10), ("E7", 20), ("F7", 20)]),
        (8, [("A8", "销售费用"), ("B8", 50), ("C8", 20),
             ("D8", 10), ("E8", 20), ("F8", 20)]),
    ]
    sheet = (f'<worksheet xmlns="{NS}"><sheetData>' + "".join(
        f'<row r="{number}">' + "".join(_cell(ref, value,
            formula=formula and ref == "D4") for ref, value in cells) +
        '</row>' for number, cells in rows) + '</sheetData></worksheet>')
    sheets = ('<sheet name="26.1" sheetId="1" r:id="rId1"/>' +
        ('<sheet name="2026-01" sheetId="2" r:id="rId2"/>'
            if duplicate_month else ""))
    book = (f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>' +
        sheets + '</sheets></workbook>')
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/'
        'package/2006/relationships"><Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/worksheet" Target="worksheets/sheet1.xml"/>' +
        ('<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
         'officeDocument/2006/relationships/worksheet" '
         'Target="worksheets/sheet2.xml"/>' if duplicate_month else "") +
        '</Relationships>')
    content_types = ('<Types xmlns="http://schemas.openxmlformats.org/'
        'package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
        'package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' +
        ('<Override PartName="/xl/sharedStrings.xml" ContentType="application/'
         'vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
         if shared_count else "") +
        ('<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/'
         'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
         if duplicate_month else "") + '</Types>')
    package_rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/'
        'package/2006/relationships"><Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>')
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        def put(name, value):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)
        put("[Content_Types].xml", content_types)
        put("_rels/.rels", package_rels)
        put("xl/workbook.xml", book)
        put("xl/_rels/workbook.xml.rels", rels)
        put("xl/worksheets/sheet1.xml", sheet)
        if shared_count:
            put("xl/sharedStrings.xml", f'<sst xmlns="{NS}">' +
                '<si><t></t></si>' * shared_count + '</sst>')
        if duplicate_month:
            put("xl/worksheets/sheet2.xml", sheet)
    return data.getvalue()


class WorkbookBytesTests(SimpleTestCase):
    def test_cross_runtime_golden_fixture_has_exact_source_and_evidence_hash(self):
        raw = FIXTURE.read_bytes()
        self.assertEqual(raw, _xlsx())
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
            "098c31d5f64b19347baea3b33b5daa059cc7cd7aadfde8466170943a87680f54")
        parsed = source.parse_single_month_xlsx(raw)
        candidate = evidence.build_candidate(parsed, "synthetic.xlsx")
        self.assertEqual(candidate["candidateDigest"],
            "673f6e3ff8f91cd3a9d79b4dd034569f638f00571faf8f1d7835fb96c07ae58a")
        self.assertEqual(candidate["columnEvidence"][0]["evidenceDigest"],
            "0b9f6f0021cad6b680c743995b0db96fbd1e769971cc3b729be8a731378f681d")

    def test_two_groups_same_name_retain_distinct_physical_columns(self):
        parsed = source.parse_single_month_xlsx(_xlsx())
        self.assertEqual(parsed["month"]["month"], "2026-01")
        self.assertEqual(parsed["month"]["shopCount"], 1)
        shop_lines = [line for line in parsed["month"]["lines"]
            if line["scopeKey"] == "shop:同名店" and
            line["metricKey"] == "gross_sales"]
        self.assertEqual(len(shop_lines), 1)
        self.assertEqual(shop_lines[0]["amountCents"], 30_000)
        physical = [(origin["columnIndex"], line["groupName"],
            line["amountCents"]) for line, origin in zip(
                parsed["evidenceInput"]["rawLines"],
                parsed["evidenceInput"]["origins"])
            if line["metricKey"] == "gross_sales"
            and line["scopeType"] == "shop"]
        self.assertEqual(physical, [(3, "京东组", 10_000),
            (5, "天猫组", 20_000)])

    def test_formula_high_precision_duplicate_month_and_xls_refuse(self):
        for raw in (_xlsx(formula=True), _xlsx(amount="100.005"),
                    _xlsx(amount="1E-1000000"),
                    _xlsx(duplicate_month=True), b"\xd0\xcf\x11\xe0"):
            with self.subTest(size=len(raw)), self.assertRaises(
                    source.WorkbookBytesError):
                source.parse_single_month_xlsx(raw)
        with patch.object(source, "MAX_XML_BYTES", 100), self.assertRaises(
                source.WorkbookBytesError):
            source.parse_single_month_xlsx(_xlsx())
        with patch.object(source, "MAX_SOURCE_CELLS", 1), self.assertRaises(
                source.WorkbookBytesError):
            source.parse_single_month_xlsx(_xlsx(shared_count=2))
        self.assertEqual(source._scaled(Decimal("0.1234"), 10_000), 1234)
        with self.assertRaises(source.WorkbookBytesError):
            source._scaled(Decimal("0.12345"), 10_000)

    def test_independent_evidence_replays_0005_header_and_cell_contract(self):
        parsed = source.parse_single_month_xlsx(_xlsx())
        candidate = evidence.build_candidate(parsed, "synthetic.xlsx")
        _, month, physical = sidecar._shape(candidate)
        columns, cells, grouped = sidecar._evidence_rows(physical)
        self.assertEqual(len(columns), 5)
        self.assertEqual(len(cells), 20)
        self.assertEqual(sidecar._verify_aggregate(month, grouped, physical),
            ["cross_group_same_name"])
        shop = [column for column in physical["columns"] if column[
            "scopeType"] == "shop"]
        self.assertEqual([(item["columnIndex"], item["groupName"])
            for item in shop], [(3, "京东组"), (5, "天猫组")])
