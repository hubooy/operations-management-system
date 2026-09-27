import hashlib
from html.parser import HTMLParser
import io
import json
from unittest import TestCase
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from .contracts import AnalysisContractError, canonical
from .report_files import Column, Table, NS, write_pair


class ReportData(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.inside, self.scripts, self.parts = False, 0, []
        self.feed(source)
        self.value = json.loads(''.join(self.parts)) if self.parts else None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts += 1
            self.inside = dict(attrs).get("id") == "report-data"

    def handle_data(self, data):
        if self.inside:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False


def pair(tables):
    xlsx, html = io.BytesIO(), io.BytesIO()
    proof = write_pair(xlsx, html, title="合成经营分析", metadata={"scope": "仅合成数据", "draft": True}, tables=tables)
    return xlsx.getvalue(), html.getvalue().decode(), proof


class ReportFilesTests(TestCase):
    def test_opc_v2_declares_manifest_and_changes_no_other_part_or_html(self):
        def render(**kwargs):
            xlsx, html = io.BytesIO(), io.BytesIO()
            proof = write_pair(xlsx, html, title="仅合成", metadata={}, tables=[self.table()], **kwargs)
            return xlsx.getvalue(), html.getvalue(), proof
        with patch("zipfile.time.localtime", return_value=(2026, 1, 1, 0, 0, 0, 3, 1, 0)):
            old, explicit, new = render(), render(xlsx_opc_version=1), render(xlsx_opc_version=2)
        self.assertEqual(old, explicit)
        self.assertEqual(old[1:], new[1:])
        with zipfile.ZipFile(io.BytesIO(old[0])) as before, zipfile.ZipFile(io.BytesIO(new[0])) as after:
            self.assertEqual(before.namelist(), after.namelist())
            for name in before.namelist():
                if name != "[Content_Types].xml":
                    self.assertEqual(before.read(name), after.read(name), name)
            declaration = b'<Default Extension="json" ContentType="application/json"/>'
            self.assertNotIn(declaration, before.read("[Content_Types].xml"))
            self.assertEqual(after.read("[Content_Types].xml").replace(declaration, b""), before.read("[Content_Types].xml"))
        for bad in (True, "2", 0, 3, None):
            with self.assertRaises(AnalysisContractError):
                render(xlsx_opc_version=bad)

    def test_layout_v2_is_opt_in_and_leaves_legacy_file_bytes_unchanged(self):
        def render(**kwargs):
            xlsx, html = io.BytesIO(), io.BytesIO()
            proof = write_pair(xlsx, html, title="窄屏合成", metadata={"synthetic": True}, tables=[self.table()], **kwargs)
            return xlsx.getvalue(), html.getvalue(), proof
        with patch("zipfile.time.localtime", return_value=(2026, 1, 1, 0, 0, 0, 3, 1, 0)):
            default, legacy, current = render(), render(html_layout_version=1), render(html_layout_version=2)
        self.assertEqual(default, legacy)
        self.assertEqual(current[0], legacy[0])
        self.assertEqual(current[2], legacy[2])
        self.assertNotIn(b"business-html-layout-v2", legacy[1])
        self.assertIn(b".toolbar>*{min-width:0;max-width:100%}", current[1])
        self.assertIn(b".toolbar select,details select{min-width:0;max-width:100%}", current[1])
        self.assertEqual(ReportData(current[1].decode()).value, ReportData(legacy[1].decode()).value)
        for version in (True, 0, 3, "2"):
            with self.assertRaises(AnalysisContractError):
                render(html_layout_version=version)

    def table(self, rows=None, title="推广与搜索"):
        rows = [["词A", 100, 2, .02], ["词B", 0, 0, None], ["缺失", None, None, None]] if rows is None else rows
        return Table("promotion", title, "曝光、点击采用完整来源；比率缺失不补零。", (
            Column("keyword", "关键词"), Column("impressions", "曝光次数", "integer", True),
            Column("clicks", "点击次数", "integer", True), Column("ctr", "点击率", "ratio", ratio_of=(2, 1))), rows, len(rows))

    def test_same_rows_digest_units_formulas_freeze_and_totals(self):
        table = self.table()
        xlsx, document, proof = pair([table])
        data = ReportData(document)
        self.assertEqual(data.scripts, 2)
        self.assertEqual(data.value["tables"][0]["rows"], table.rows)
        expected = hashlib.sha256(''.join(canonical(r)+'\n' for r in table.rows).encode()).hexdigest()
        self.assertEqual(proof["tables"][0]["rowDigest"], expected)
        with zipfile.ZipFile(io.BytesIO(xlsx)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(json.loads(archive.read("teruisi-manifest.json"))["tables"], proof["tables"])
            root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            cells = {c.get("r"): c for c in root.findall('.//{'+NS+'}c')}
            find = lambda key, tag: cells[key].find('{'+NS+'}'+tag)
            self.assertEqual(find("D4", "f").text, 'IF(COUNT(C4,B4)=2,IF(B4>0,C4/B4,""),"")')
            self.assertEqual(find("D4", "v").text, "0.02")
            self.assertIsNone(find("D5", "f"))
            self.assertIsNone(find("D6", "v"))
            self.assertEqual(find("B7", "f").text, "SUM(B4:B6)")
            self.assertEqual(find("B7", "v").text, "100")
            self.assertEqual(root.find('.//{'+NS+'}pane').get("topLeftCell"), "A4")
            self.assertEqual(root.find('{'+NS+'}autoFilter').get("ref"), "A3:D6")
            self.assertEqual(root.find('{'+NS+'}dimension').get("ref"), "A1:D7")

    def test_untrusted_cells_never_become_html_or_excel_code_and_ids_stay_exact(self):
        attack = '</script><script>window.attacked=true</script><img src=x onerror=alert(1)>'
        values = [[attack, "=HYPERLINK(\"http://invalid/\")", 9999999999999999]]
        table = Table("data", 'A"B/<>&', "原文保留", (Column("a", "名称"), Column("b", "原值"), Column("id", "长整数", "integer", True)), values, 1)
        xlsx, document, proof = pair([table])
        parsed = ReportData(document)
        self.assertEqual(parsed.scripts, 2)
        self.assertEqual(parsed.value["tables"][0]["rows"][0], [attack, values[0][1], "9999999999999999"])
        self.assertEqual(proof["tables"][0]["precisionTextCells"], 1)
        with zipfile.ZipFile(io.BytesIO(xlsx)) as archive:
            for name in archive.namelist():
                if name.endswith(".xml") or name.endswith(".rels"):
                    ET.fromstring(archive.read(name))
            root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            self.assertEqual(root.findall('.//{'+NS+'}f'), [])
            value = root.find('.//{'+NS+'}c[@r="C4"]')
            self.assertEqual(value.get("t"), "inlineStr")
            self.assertEqual(value.find('.//{'+NS+'}t').text, "9999999999999999")

    def test_late_rows_bad_ratios_text_limits_and_resource_limits_fail_closed(self):
        cases = [self.table([["wrong", 100, 2, .03]]), self.table([["bad\x00value", 100, 2, .02]]),
            self.table([["𠮷"*16384, 100, 2, .02]]), self.table([["invalid", float("nan"), 1, None]])]
        normal = self.table()
        cases.append(Table(normal.key, normal.title, normal.note, normal.columns, iter(normal.rows[:-1]), 3))
        cases.append(Table(normal.key, normal.title, normal.note, normal.columns, iter(normal.rows), 2))
        for table in cases:
            with self.subTest(title=table.title), self.assertRaises(AnalysisContractError):
                pair([table])
        with patch("business_analysis.report_files.MAX_FILE_BYTES", 128), self.assertRaises(AnalysisContractError):
            pair([normal])

    def test_more_than_legacy_2000_rows_are_streamed_once_without_truncation(self):
        seen = []
        def records():
            for i in range(5001):
                seen.append(i)
                yield [str(i), i]
        table = Table("all", "完整数据", "合成数据", (Column("id", "行号"), Column("amount", "金额（分）", "integer", True)), records(), 5001)
        xlsx, document, proof = pair([table])
        self.assertEqual(len(seen), 5001)
        self.assertEqual(proof["tables"][0]["rowCount"], 5001)
        self.assertEqual(ReportData(document).value["tables"][0]["rows"][-1], ["5000", 5000])
        with zipfile.ZipFile(io.BytesIO(xlsx)) as archive:
            root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            self.assertEqual(len(root.findall('.//{'+NS+'}row')), 5005)
            self.assertEqual(root.find('.//{'+NS+'}c[@r="B5005"]/{'+NS+'}v').text, str(5000*5001//2))

    def test_duplicate_sheet_titles_are_safe_and_empty_total_is_not_zero(self):
        first = self.table([], title="A/B")
        second = Table("second", "A:B", "", first.columns, [], 0)
        xlsx, _, proof = pair([first, second])
        self.assertEqual([p["sheet"] for p in proof["tables"]], ["A_B", "A_B_2"])
        with zipfile.ZipFile(io.BytesIO(xlsx)) as archive:
            root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            self.assertIsNone(root.find('.//{'+NS+'}c[@r="B4"]/{'+NS+'}v'))
