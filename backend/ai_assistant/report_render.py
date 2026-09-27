"""Passive HTML and XLSX rendering from already persisted, bounded evidence."""
import base64
import html
import io
import math
import re
import unicodedata
import zipfile
from xml.sax.saxutils import escape
from .policy import AiError, canonical
from .artifacts import UNSAFE

NOTE = "取数明细仅包含本次任务实际收到的有界结果，不等于业务全量导出。日期覆盖、分页与截断以来源回执为准，缺失不等于零。"


def safe_text(value):
    return re.sub(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]", "", str(value))


def source_tables(sources):
    tables, count = [], 0

    def walk(value, path):
        nonlocal count
        if isinstance(value, dict):
            scalars = {k: v for k, v in value.items() if not UNSAFE.search(k) and (v is None or type(v) in (str, bool, int, float))}
            if scalars:
                rows = [[key, value] for key, value in scalars.items()]
                tables.append((path, ["字段", "值"], rows))
                count += len(rows)
            for key, child in value.items():
                if not UNSAFE.search(key) and isinstance(child, (dict, list)):
                    walk(child, path + "." + key)
        elif isinstance(value, list) and value:
            if all(isinstance(v, dict) for v in value):
                columns = list(dict.fromkeys(k for row in value for k, v in row.items() if not UNSAFE.search(k) and (v is None or type(v) in (str, bool, int, float))))
                if len(columns) > 64:
                    raise AiError("取数明细列数超过64列", "payload_too_large", 413)
                if columns:
                    tables.append((path, columns, [[row.get(k) if row.get(k) is None or type(row.get(k)) in (str, bool, int, float) else None for k in columns] for row in value]))
                    count += len(value)
                # Preserve nested fields as separate tables; never silently discard them.
                for index, row in enumerate(value):
                    for key, child in row.items():
                        if not UNSAFE.search(key) and isinstance(child, (dict, list)):
                            walk(child, f"{path}[{index}].{key}")
            elif all(v is None or type(v) in (str, bool, int, float) for v in value):
                tables.append((path, ["值"], [[v] for v in value])); count += len(value)
            else:
                for index, child in enumerate(value):
                    if child is None or type(child) in (str, bool, int, float):
                        tables.append((f"{path}[{index}]", ["值"], [[child]])); count += 1
                    else:
                        walk(child, f"{path}[{index}]")
        if len(tables) > 100 or count > 2000:
            raise AiError("取数明细超过100个分表或2000行，当前结果保留，请缩小后续报告范围", "payload_too_large", 413)

    for index, source in enumerate(sources):
        # Source parameters and envelope flags are evidence, not instructions.
        walk(source["arguments"], f"来源{index+1}.参数")
        walk(source["result"], f"来源{index+1}.{source['tool']}")
    return tables


def workbook(sheets):
    def row_xml(row, index):
        def column_name(column):
            result = ""
            while column:
                column, remainder = divmod(column-1, 26)
                result = chr(65+remainder) + result
            return result
        lines = max((sum(max(1, math.ceil(sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in line)/(28 if column == 0 else 53))) for line in safe_text(value).split("\n")) for column, value in enumerate(row)), default=1)
        return '<row r="' + str(index+1) + '" ht="' + str(min(409, max(25, lines*17+8))) + '" customHeight="1">' + ''.join(cell(value, index == 0, column_name(column+1)+str(index+1)) for column, value in enumerate(row)) + '</row>'
    def cell(value, header=False, reference="A1"):
        style = ' r="' + reference + '"' + (' s="2"' if header else ' s="1"')
        if type(value) in (int, float) and math.isfinite(value) and abs(value) < 10**15:
            return '<c t="n"' + style + '><v>' + str(value) + '</v></c>'
        # Text is always inline; formulas and external relationships cannot execute.
        raw = safe_text("" if value is None else value)
        if len(raw) > 32767:
            raise AiError("单元格超过Excel文本上限", "payload_too_large", 413)
        return '<c t="inlineStr"' + style + '><is><t xml:space="preserve">' + escape(raw) + '</t></is></c>'
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets)+1)) + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView/></bookViews><sheets>' + ''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1)) + '</sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets)+1)) + '<Relationship Id="rStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        archive.writestr("xl/styles.xml", '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><sz val="11"/><color rgb="FF246749"/><name val="Microsoft YaHei"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFEDF5EF"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        for i, (_, rows) in enumerate(sheets, 1):
            archive.writestr(f"xl/worksheets/sheet{i}.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="1" width="30" customWidth="1"/><col min="2" max="64" width="55" customWidth="1"/></cols><sheetData>' + ''.join(row_xml(row, index) for index, row in enumerate(rows)) + '</sheetData></worksheet>')
    return output.getvalue()


def render(report_id, snapshot, sections, sources, format, draft):
    label = "待复核草稿" if draft else "已通过人工复核"
    title = snapshot["template"]["name"]
    filters = snapshot["scope"]
    metadata = [["报告编号", report_id], ["状态", label], ["平台", filters["platform"]], ["店铺 / 仓别", filters["shop"]],
        ["起始日期（含）", filters["startDate"]], ["结束日期（含）", filters["endDate"]], ["模板及方法库版本", snapshot["libraryVersion"]],
        ["库摘要", snapshot["libraryDigest"]], ["模板", title], ["使用方法", "、".join(s["name"] for s in snapshot["skills"])], ["完整性说明", NOTE]]
    tables = source_tables(sources)
    provenance = [["工具", "参数", "结果摘要"]] + [[s["tool"], canonical(s["arguments"]), s["digest"]] for s in sources]
    if format == "xlsx":
        report_rows = [[s["title"] if offset == 0 else "", s["body"][offset:offset+400]] for s in sections for offset in range(0, len(s["body"]), 400)]
        sheets = [("报告说明", [["项目", "内容"], *metadata]), ("分析报告", [["章节", "正文"], *report_rows]), ("取数来源", provenance)]
        sheets.extend((f"明细{i}", [["来源路径", path], columns, *rows]) for i, (path, columns, rows) in enumerate(tables, 1))
        raw = workbook(sheets)
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        esc = lambda value: html.escape(safe_text(value))
        def table(headers, rows):
            return '<div class="scroll"><table><thead><tr>' + ''.join('<th>'+esc(c)+'</th>' for c in headers) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join('<td>'+esc('' if c is None else c)+'</td>' for c in row)+'</tr>' for row in rows) + '</tbody></table></div>'
        raw = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+'</title><style>body{margin:0;background:#f4f7f5;color:#172820;font:15px/1.75 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1080px;margin:40px auto;padding:36px;background:white;border-top:6px solid #246749}h1{font-size:30px}h2{font-size:20px;color:#246749;border-bottom:1px solid #dbe7df;padding-bottom:10px}section{margin:32px 0}.body{white-space:pre-wrap;overflow-wrap:anywhere}.note{padding:16px;background:#edf5ef}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #dbe7df;padding:8px;text-align:left;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere;max-width:600px}th{background:#edf5ef}@media(max-width:700px){main{margin:0;padding:20px}}@media print{main{margin:0;padding:0}section{break-inside:avoid}}</style><main><p>'+esc(label)+'</p><h1>'+esc(title)+'</h1><p>'+esc(filters["platform"]+' · '+filters["shop"]+' · '+filters["startDate"]+' 至 '+filters["endDate"])+'</p><p class="note">'+esc(NOTE)+'</p>' + ''.join('<section><h2>'+esc(s["title"])+'</h2><div class="body">'+esc(s["body"])+'</div></section>' for s in sections) + '<section><h2>报告版本与范围</h2>'+table(["项目", "内容"], metadata)+'</section><section><h2>取数来源</h2>'+table(provenance[0], provenance[1:])+'</section>'+''.join('<details><summary>'+esc(path)+'</summary>'+table(columns, rows)+'</details>' for path, columns, rows in tables)+'</main></html>').encode()
        mime = "text/html; charset=utf-8"
    if len(raw) > 2*1024*1024:
        raise AiError("报告文件超过2 MiB", "payload_too_large", 413)
    return {"base64": base64.b64encode(raw).decode(), "mimeType": mime, "fileName": f"{title}-{filters['startDate']}-{label}.{format}"}
