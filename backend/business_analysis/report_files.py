"""Streaming paired business files; typed source values are data, never code.

The owning report service supplies verified tables. Both outputs consume each
table exactly once and share the same ordered row digest. No model, business
query, external asset, macro or arbitrary spreadsheet formula is executed here.
"""
from dataclasses import dataclass
from decimal import Decimal
import base64
import hashlib
import html
import json
import math
import re
import zipfile
import zlib
from xml.sax.saxutils import escape, quoteattr

from .contracts import AnalysisContractError, canonical

MAX_ROWS = 1000000
MAX_TABLES = 120
MAX_COLUMNS = 160
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_SLIM_TABLE_NDJSON_BYTES = 256 * 1024 * 1024
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# Opt-in layout for renderer 4+. Keep HTML_HEAD and the default writer byte
# stream unchanged for existing renderer 1/2/3 deliverables.
HTML_LAYOUT_V2 = '''<style id="business-html-layout-v2">
.toolbar>*{min-width:0;max-width:100%}.toolbar input{flex-basis:220px}
.toolbar select,details select{min-width:0;max-width:100%}
.pager{flex-wrap:wrap}.pager>*{min-width:0;max-width:100%}
header h1,h2,.note,nav button,.pager span{overflow-wrap:anywhere}
details{min-width:0;max-width:100%}pre.meta{white-space:pre-wrap}
</style>'''


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    kind: str = "text"  # text, integer, decimal, ratio
    total: bool = False
    ratio_of: tuple | None = None  # zero-based numerator / denominator columns


@dataclass(frozen=True)
class Table:
    key: str
    title: str
    note: str
    columns: tuple
    rows: object  # one-pass iterable of complete typed row sequences
    row_count: int


def column_name(number):
    result = ""
    while number:
        number, remainder = divmod(number-1, 26)
        result = chr(65+remainder) + result
    return result


def text(value):
    if not isinstance(value, str) or re.search(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]", value):
        raise AnalysisContractError("文件文本包含不支持的字符；不能静默删改来源")
    if len(value.encode("utf-16-le")) // 2 > 32767:
        raise AnalysisContractError("单元格超过 Excel 文本容量，须先分列或分表")
    return value


def cell(value, reference, style=0, formula=None):
    attr = f' r="{reference}" s="{style}"'
    if value is None:
        return '<c'+attr+'/>'
    if type(value) is bool:
        return '<c'+attr+' t="b"><v>'+str(int(value))+'</v></c>'
    if type(value) in (int, float) and math.isfinite(value):
        if type(value) is int and abs(value) >= 10**15:
            # Excel stores 15 significant digits. Preserve long integers as text.
            value = str(value)
        else:
            return '<c'+attr+' t="n">'+('<f>'+escape(formula)+'</f>' if formula else '')+'<v>'+str(value)+'</v></c>'
    if not isinstance(value, str):
        raise AnalysisContractError("表格单元格不是受支持的标量")
    return '<c'+attr+' t="inlineStr"><is><t xml:space="preserve">'+escape(text(value))+'</t></is></c>'


def _sheet_names(tables):
    names, used = [], set()
    for table in tables:
        candidate = re.sub(r"[\[\]:*?/\\]", "_", table.title).strip("'") or "数据"
        candidate = candidate[:26]
        while len(candidate.encode("utf-16-le")) // 2 > 26:
            candidate = candidate[:-1]
        name, suffix = candidate, 1
        while name.casefold() in used:
            suffix += 1
            name = candidate + "_" + str(suffix)
        used.add(name.casefold())
        names.append(name)
    return names


def _static_parts(archive, names, style_transform=lambda value: value, *, xlsx_opc_version=1):
    archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'+''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(names)+1))+('<Default Extension="json" ContentType="application/json"/>' if xlsx_opc_version == 2 else '')+'</Types>')
    archive.writestr("_rels/.rels", f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    archive.writestr("xl/workbook.xml", f'<workbook xmlns="{NS}" xmlns:r="{REL}"><bookViews><workbookView/></bookViews><sheets>'+''.join(f'<sheet name={quoteattr(name)} sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(names, 1))+'</sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>')
    archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(names)+1))+f'<Relationship Id="rStyles" Type="{REL}/styles" Target="styles.xml"/></Relationships>')
    archive.writestr("xl/styles.xml", style_transform(f'<styleSheet xmlns="{NS}"><numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0;[Red](#,##0);0"/><numFmt numFmtId="165" formatCode="#,##0.00;[Red](#,##0.00);0.00"/></numFmts><fonts count="3"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/></font><font><b/><sz val="16"/><color rgb="FF225B43"/><name val="Microsoft YaHei"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF225B43"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="10" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'))


def _json(value):
    # JSON is inside an inert script element; escaping '<' blocks all end tags.
    return canonical(value).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def write_pair(xlsx_file, html_file, *, title, metadata, tables, checkpoint=None, offline_budget=None, excel_budget=None, html_layout_version=1, xlsx_opc_version=1, html_payload_version=1):
    """Write both files to caller-owned temporary streams, return table proofs.

    The caller must publish neither stream when this function raises. A failed
    late row can leave temporary output bytes but never a successful manifest.
    """
    if type(html_layout_version) is not int or html_layout_version not in (1, 2):
        raise AnalysisContractError("HTML布局版本不受支持")
    if type(xlsx_opc_version) is not int or xlsx_opc_version not in (1, 2):
        raise AnalysisContractError("XLSX OPC版本不受支持")
    if type(html_payload_version) is not int or html_payload_version not in (1, 2) or (html_payload_version == 2 and (html_layout_version != 2 or xlsx_opc_version != 2)):
        raise AnalysisContractError("HTML压缩行格式仅供新版离线候选")
    text(title)
    if not 1 <= len(tables) <= MAX_TABLES or len({t.key for t in tables}) != len(tables):
        raise AnalysisContractError("报告表数量或身份无效")
    for table in tables:
        text(table.title), text(table.note)
        if type(table.row_count) is not int or not 0 <= table.row_count <= MAX_ROWS:
            raise AnalysisContractError("工作表超过容量，须明确分片")
        if not 1 <= len(table.columns) <= MAX_COLUMNS or len({c.key for c in table.columns}) != len(table.columns):
            raise AnalysisContractError("工作表列配置无效")
        for col in table.columns:
            text(col.label)
            if col.kind not in ("text", "integer", "decimal", "ratio") or (col.total and col.kind not in ("integer", "decimal")):
                raise AnalysisContractError("列类型或汇总规则无效")
            if col.ratio_of is not None and (len(col.ratio_of) != 2 or any(type(i) is not int or not 0 <= i < len(table.columns) for i in col.ratio_of)):
                raise AnalysisContractError("比率列引用无效")
            if col.ratio_of is not None and (col.kind not in ("ratio", "decimal") or any(table.columns[i].ratio_of is not None or table.columns[i].kind not in ("integer", "decimal") for i in col.ratio_of)):
                raise AnalysisContractError("比率只能引用原始数值列，不允许循环公式")
    def out(value):
        raw = value.encode("utf-8")
        if html_file.tell()+len(raw) > MAX_FILE_BYTES:
            raise AnalysisContractError("文件超过当前容量，须显式分片，禁止截断")
        html_file.write(raw)
    head = HTML_HEAD.replace("REPORT_TITLE", html.escape(title))
    if html_layout_version == 2:
        head = head.replace("</head>", HTML_LAYOUT_V2+"</head>", 1)
    out(head)
    out('<script type="application/json" id="report-data">{'+('"htmlPayloadVersion":2,' if html_payload_version == 2 else '')+'"title":'+_json(title)+',"metadata":'+_json(metadata)+',"tables":[')
    names, manifest, html_payload_tables = _sheet_names(tables), [], []
    model_sheets, model_proof = [], None
    if excel_budget is not None:
        from . import budget_excel
        if len(tables)+len(budget_excel.TITLES) > MAX_TABLES:
            raise AnalysisContractError("预算试算工作表超过报告容量")
        placeholders = [Table("calculator-"+str(i), title, "", (), (), 0) for i, title in enumerate(budget_excel.TITLES)]
        names = _sheet_names([*tables, *placeholders])
        model_sheets, model_proof = budget_excel.build(excel_budget, names[len(tables):], **({"formula_version": 2} if xlsx_opc_version == 2 else {}))
    with zipfile.ZipFile(xlsx_file, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        _static_parts(archive, names, budget_excel.styles if excel_budget is not None else lambda value: value, xlsx_opc_version=xlsx_opc_version)
        for table_index, (table, name) in enumerate(zip(tables, names), 1):
            if checkpoint:
                checkpoint({"stage": "rendering", "table": table_index, "totalTables": len(tables)})
            if table_index > 1:
                out(',')
            out('{"key":'+_json(table.key)+',"title":'+_json(table.title)+',"note":'+_json(table.note)+',"columns":'+_json([{"key": c.key, "label": c.label, "kind": c.kind} for c in table.columns])+(',"rowsGzipBase64":"' if html_payload_version == 2 else ',"rows":['))
            if html_payload_version == 2:
                compressor, pending, rows_ndjson_bytes, gzip_bytes = zlib.compressobj(level=6, wbits=31), b"", 0, 0
                compressed_sha = hashlib.sha256()
                def compressed_out(raw):
                    nonlocal pending, gzip_bytes
                    new = compressor.compress(raw)
                    compressed_sha.update(new)
                    gzip_bytes += len(new)
                    packed = pending + new
                    size = len(packed) // 3 * 3
                    if size:
                        out(base64.b64encode(packed[:size]).decode("ascii"))
                    pending = packed[size:]
            count, precision_text, row_digest = 0, 0, hashlib.sha256()
            totals = [Decimal(0) for _ in table.columns]
            present, precise = [0]*len(table.columns), [True]*len(table.columns)
            with archive.open(f"xl/worksheets/sheet{table_index}.xml", "w", force_zip64=True) as sheet:
                def xml(value):
                    sheet.write(value.encode("utf-8"))
                last_col = column_name(len(table.columns))
                last_row = table.row_count+3+int(any(c.total for c in table.columns))
                xml(f'<worksheet xmlns="{NS}"><dimension ref="A1:{last_col}{last_row}"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="3" topLeftCell="A4" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>'+''.join(f'<col min="{i}" max="{i}" width="{32 if col.kind == "text" else 19}" customWidth="1"/>' for i, col in enumerate(table.columns, 1))+'</cols><sheetData>')
                xml('<row r="1" ht="30" customHeight="1">'+cell(table.title, "A1", 5)+'</row>')
                xml('<row r="2" ht="48" customHeight="1">'+cell(table.note, "A2")+'</row>')
                xml('<row r="3" ht="32" customHeight="1">'+''.join(cell(col.label, column_name(i)+"3", 1) for i, col in enumerate(table.columns, 1))+'</row>')
                for row in table.rows:
                    if checkpoint and count % 1000 == 0:
                        checkpoint({"stage": "rendering", "table": table_index, "rows": count, "totalRows": table.row_count})
                    if not isinstance(row, (list, tuple)) or len(row) != len(table.columns) or count >= table.row_count:
                        raise AnalysisContractError("表格行宽或完整行数不一致")
                    values = list(row)
                    for i, (value, col) in enumerate(zip(values, table.columns)):
                        if value is not None and not (type(value) in (str, bool, int, float) and (type(value) is not float or math.isfinite(value))):
                            raise AnalysisContractError("报告行含不支持的值")
                        if isinstance(value, str):
                            text(value)
                        if value is not None and col.kind != "text" and type(value) not in (int, float):
                            raise AnalysisContractError("数值列含非数值，不能悄悄转换")
                        if col.kind == "integer" and value is not None and type(value) is not int:
                            raise AnalysisContractError("整数列不能包含小数")
                        if type(value) is int and abs(value) >= 10**15:
                            precision_text += 1
                            precise[i] = False
                            # JSON must preserve identifiers/large integers too.
                            values[i] = str(value)
                        if col.total and value is not None:
                            totals[i] += Decimal(str(value))
                            present[i] += 1
                    encoded = canonical(values)
                    row_digest.update((encoded+'\n').encode())
                    if html_payload_version == 2:
                        raw_row = (encoded + "\n").encode("utf-8")
                        rows_ndjson_bytes += len(raw_row)
                        if rows_ndjson_bytes > MAX_SLIM_TABLE_NDJSON_BYTES:
                            raise AnalysisContractError("压缩HTML单表解压后超过256MiB，禁止隐藏超大表")
                        compressed_out(raw_row)
                    else:
                        if count:
                            out(',')
                        out(_json(values))
                    excel_row = count+4
                    height = min(409, max(25, max((sum(max(1, math.ceil(len(line)*2/32)) for line in v.split('\n'))*16+8 for v in row if isinstance(v, str)), default=25)))
                    xml(f'<row r="{excel_row}" ht="{height}" customHeight="1">')
                    for i, (value, col) in enumerate(zip(row, table.columns)):
                        formula = None
                        if col.ratio_of is not None and value is not None:
                            a, b = col.ratio_of
                            numerator, denominator = row[a], row[b]
                            if type(numerator) not in (int, float) or type(denominator) not in (int, float) or denominator <= 0:
                                raise AnalysisContractError("比率列没有可用分子分母")
                            calculated = float(Decimal(str(numerator))/Decimal(str(denominator)))
                            if not math.isclose(float(value), calculated, rel_tol=1e-10, abs_tol=1e-10):
                                raise AnalysisContractError("比率缓存与确定性计算不一致")
                            if type(values[a]) is not str and type(values[b]) is not str:
                                left, right = column_name(a+1)+str(excel_row), column_name(b+1)+str(excel_row)
                                formula = f'IF(COUNT({left},{right})=2,IF({right}>0,{left}/{right},""),"")'
                        xml(cell(value, column_name(i+1)+str(excel_row), {"text": 0, "integer": 2, "decimal": 3, "ratio": 4}[col.kind], formula))
                    xml('</row>')
                    count += 1
                    if xlsx_file.tell() > MAX_FILE_BYTES:
                        raise AnalysisContractError("工作簿超过当前容量，须显式分片")
                if count != table.row_count:
                    raise AnalysisContractError("表格缺行，禁止生成完整文件回执")
                if any(c.total for c in table.columns):
                    excel_row = count+4
                    xml(f'<row r="{excel_row}" ht="28" customHeight="1">')
                    for i, col in enumerate(table.columns):
                        value, formula = ("已知值合计" if i == 0 and not col.total else None), None
                        if col.total and present[i]:
                            total = totals[i]
                            value = int(total) if col.kind == "integer" else float(total)
                            if precise[i] and abs(total) < 10**15:
                                formula = f'SUM({column_name(i+1)}4:{column_name(i+1)}{count+3})'
                        xml(cell(value, column_name(i+1)+str(excel_row), 2 if col.kind == "integer" else 3 if col.kind == "decimal" else 0, formula))
                    xml('</row>')
                xml(f'</sheetData><autoFilter ref="A3:{last_col}{count+3}"/>')
                if len(table.columns) > 1:
                    xml(f'<mergeCells count="2"><mergeCell ref="A1:{last_col}1"/><mergeCell ref="A2:{last_col}2"/></mergeCells>')
                xml('<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/><pageSetup orientation="landscape" paperSize="9"/></worksheet>')
            proof = {"key": table.key, "sheet": name, "rowCount": count, "columnCount": len(table.columns), "rowDigest": row_digest.hexdigest(), "precisionTextCells": precision_text}
            manifest.append(proof)
            if html_payload_version == 2:
                last = compressor.flush()
                compressed_sha.update(last)
                gzip_bytes += len(last)
                out(base64.b64encode(pending + last).decode("ascii"))
                out('","rowsNdjsonBytes":'+str(rows_ndjson_bytes)+',"rowsGzipSha256":"'+compressed_sha.hexdigest()+'","proof":'+_json(proof)+'}')
                html_payload_tables.append({"key": table.key, "rowCount": count,
                    "rowDigest": proof["rowDigest"],
                    "rowsNdjsonBytes": rows_ndjson_bytes,
                    "rowsGzipBytes": gzip_bytes,
                    "rowsGzipSha256": compressed_sha.hexdigest()})
            else:
                out('],"proof":'+_json(proof)+'}')
        for index, sheet in enumerate(model_sheets, len(tables)+1):
            if checkpoint: checkpoint({"stage": "rendering", "table": index, "rows": 0, "totalRows": len(excel_budget["plan"]["targets"])})
            budget_excel.write_sheet(archive, index, sheet)
        archive.writestr("teruisi-manifest.json", canonical({"schemaVersion": "business-files-v1", "title": title, "metadata": metadata, "tables": manifest,
            **({"budgetCalculator": model_proof} if model_proof else {})}))
    out(']}</script>'+(_slim_script_v10() if html_payload_version == 2 else HTML_SCRIPT))
    if offline_budget is not None:
        from .budget_offline import render
        out(render(offline_budget))
    out('</body></html>')
    if xlsx_file.tell() > MAX_FILE_BYTES:
        raise AnalysisContractError("工作簿超过当前容量，须显式分片")
    result = {"schemaVersion": "business-files-v1", "tables": manifest,
        **({"budgetCalculator": model_proof} if model_proof else {})}
    if html_payload_version == 2:
        payload = {"schemaVersion": "business-html-compressed-rows-v1",
            "htmlPayloadVersion": 2,
            "browserRequirements": ["DecompressionStream:gzip", "SubtleCrypto:SHA-256"],
            "tables": html_payload_tables}
        result["htmlPayload"] = {**payload,
            "proofDigest": hashlib.sha256(canonical(payload).encode()).hexdigest()}
    return result


HTML_HEAD = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"><title>REPORT_TITLE</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f6f3;color:#173b2c;font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif}header{padding:28px 32px;background:#173b2c;color:white}h1{margin:0;font-size:28px}header p{margin:8px 0 0;opacity:.8}.layout{display:grid;grid-template-columns:240px minmax(0,1fr);gap:20px;padding:24px;max-width:1800px;margin:auto}nav{background:white;border:1px solid #d8e3db;border-radius:12px;padding:14px;align-self:start;max-height:80vh;overflow:auto;position:sticky;top:16px}nav button{display:block;width:100%;text-align:left;border:0;background:transparent;padding:10px;border-radius:6px;cursor:pointer;color:#173b2c}nav button.active{background:#e3eee6;font-weight:700}main{min-width:0;background:white;border:1px solid #d8e3db;border-radius:12px;padding:24px}h2{font-size:22px;margin:0 0 8px}.note{white-space:pre-wrap;color:#51665b;border-left:3px solid #58966c;padding-left:12px;margin:12px 0}.toolbar{display:flex;flex-wrap:wrap;gap:10px;margin:20px 0}input,select,button{font:inherit;padding:8px 12px;border:1px solid #cbd9ce;border-radius:6px;background:white;color:inherit}input{min-width:220px;flex:1}button{cursor:pointer}button:disabled{opacity:.4;cursor:default}.scroll{overflow:auto;max-height:65vh;border:1px solid #d8e3db}table{border-collapse:separate;border-spacing:0;width:100%;font-size:13px}th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #e6ede7;vertical-align:top;min-width:125px;max-width:420px;overflow-wrap:anywhere;white-space:pre-wrap}th{position:sticky;top:0;background:#e7efe9;z-index:1;color:#225b43}td.number{text-align:right;font-variant-numeric:tabular-nums}tr:nth-child(even){background:#f8faf8}.empty{color:#87968c}.pager{display:flex;gap:12px;align-items:center;justify-content:flex-end;margin-top:16px}.meta{font-size:12px;color:#607468;overflow-wrap:anywhere;margin-top:24px}details{margin-top:20px}svg{max-width:100%;height:auto}.bar{fill:#4e8764}@media(max-width:800px){.layout{grid-template-columns:1fr;padding:12px}nav{position:static;max-height:200px}header{padding:20px}main{padding:16px}}@media print{nav,.toolbar,.pager{display:none}.layout{display:block;padding:0}.scroll{max-height:none;overflow:visible}main{border:0}header{background:white;color:#173b2c}}
</style></head><body><header><h1 id="title">REPORT_TITLE</h1><p>同一份核对数据 · 可离线检索 · 缺失不等于零</p></header><div class="layout"><nav id="nav" aria-label="报告工作表"></nav><main><h2 id="table-title"></h2><p id="note" class="note"></p><div class="toolbar"><input id="search" type="search" placeholder="搜索当前表全部数据" aria-label="搜索当前表"><select id="sort" aria-label="排序列"></select><button id="order">升序</button><select id="missing" aria-label="缺失筛选"><option value="all">全部行</option><option value="missing">含缺失值</option><option value="complete">无缺失值</option></select><button id="export">导出筛选结果 CSV</button></div><div class="scroll"><table><thead id="thead"></thead><tbody id="tbody"></tbody></table></div><div class="pager"><span id="count" aria-live="polite"></span><button id="previous">上一页</button><button id="next">下一页</button></div><details><summary>当前筛选下的数值前十项</summary><select id="chart-column" aria-label="图表指标"></select><div id="chart"></div></details><p id="proof" class="meta"></p><details><summary>报告范围与版本</summary><pre id="metadata" class="meta"></pre></details></main></div>'''

HTML_SCRIPT = '''<script>
"use strict";
const data=JSON.parse(document.getElementById("report-data").textContent),$=id=>document.getElementById(id),size=100;
let selected=0,page=0,descending=false,filtered=[];
$("title").textContent=data.title;$("metadata").textContent=JSON.stringify(data.metadata,null,2);
const display=(v,column)=>v===null?"—":typeof v==="number"&&column?.kind==="ratio"?new Intl.NumberFormat("zh-CN",{style:"percent",minimumFractionDigits:2,maximumFractionDigits:2}).format(v):String(v);
const node=(tag,value)=>{const element=document.createElement(tag);if(value!==undefined)element.textContent=value;return element;};
data.tables.forEach((t,index)=>{const button=node("button",t.title+" · "+t.rows.length.toLocaleString());button.onclick=()=>choose(index);$("nav").append(button);});
function choose(index){selected=index;page=0;const t=data.tables[index];$("table-title").textContent=t.title;$("note").textContent=t.note;$("search").value="";$("missing").value="all";$("sort").replaceChildren(new Option("原始顺序",""));$("chart-column").replaceChildren(new Option("选择数值指标",""));t.columns.forEach((c,i)=>{$("sort").add(new Option(c.label,String(i)));if(c.kind!=="text")$("chart-column").add(new Option(c.label,String(i)));});[...$("nav").children].forEach((b,i)=>b.classList.toggle("active",i===index));$("proof").textContent="完整表 "+t.proof.rowCount+" 行 / "+t.proof.columnCount+" 列 · 行摘要 "+t.proof.rowDigest+" · 超过 Excel 精度的整数按文本保留 "+t.proof.precisionTextCells+" 个";refresh();}
function refresh(){const t=data.tables[selected],query=$("search").value.toLocaleLowerCase(),missing=$("missing").value,key=$("sort").value;filtered=t.rows.map((_,i)=>i).filter(i=>{const row=t.rows[i],has=row.some(v=>v===null);return(missing==="all"||(missing==="missing"?has:!has))&&(!query||row.some(v=>v!==null&&String(v).toLocaleLowerCase().includes(query)));});if(key!==""){const k=Number(key);filtered.sort((a,b)=>{const x=t.rows[a][k],y=t.rows[b][k];if(x===null||y===null)return x===y?a-b:x===null?1:-1;const result=typeof x==="number"&&typeof y==="number"?x-y:String(x).localeCompare(String(y),"zh-CN");return(result?(descending?-result:result):a-b);});}page=Math.min(page,Math.max(0,Math.ceil(filtered.length/size)-1));render();}
function render(){const t=data.tables[selected],head=node("tr");t.columns.forEach(c=>head.append(node("th",c.label)));$("thead").replaceChildren(head);const body=document.createDocumentFragment();filtered.slice(page*size,(page+1)*size).forEach(i=>{const tr=node("tr");t.rows[i].forEach((v,j)=>{const td=node("td",display(v,t.columns[j]));if(v===null)td.className="empty";else if(t.columns[j].kind!=="text")td.className="number";tr.append(td);});body.append(tr);});$("tbody").replaceChildren(body);$("count").textContent=filtered.length.toLocaleString()+" / "+t.rows.length.toLocaleString()+" 行 · 第 "+(page+1)+" / "+Math.max(1,Math.ceil(filtered.length/size))+" 页";$("previous").disabled=page===0;$("next").disabled=(page+1)*size>=filtered.length;chart();}
function chart(){const target=$("chart"),choice=$("chart-column").value;target.replaceChildren();if(choice==="")return;const t=data.tables[selected],k=Number(choice),rows=filtered.filter(i=>typeof t.rows[i][k]==="number").sort((a,b)=>t.rows[b][k]-t.rows[a][k]).slice(0,10),ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg");svg.setAttribute("viewBox","0 0 900 "+Math.max(45,rows.length*38));const maximum=Math.max(1,...rows.map(i=>Math.abs(t.rows[i][k])));rows.forEach((i,pos)=>{const value=t.rows[i][k],label=document.createElementNS(ns,"text"),bar=document.createElementNS(ns,"rect"),number=document.createElementNS(ns,"text");label.setAttribute("x","0");label.setAttribute("y",String(pos*38+24));label.textContent=display(t.rows[i][0]).slice(0,18);bar.setAttribute("x",String(value<0?480-240*Math.abs(value)/maximum:480));bar.setAttribute("y",String(pos*38+8));bar.setAttribute("width",String(240*Math.abs(value)/maximum));bar.setAttribute("height","24");bar.setAttribute("class","bar");number.setAttribute("x","740");number.setAttribute("y",String(pos*38+24));number.textContent=String(value);svg.append(label,bar,number);});target.append(svg);}
$("search").oninput=()=>{page=0;refresh();};$("sort").onchange=$("missing").onchange=()=>{page=0;refresh();};$("order").onclick=()=>{descending=!descending;$("order").textContent=descending?"降序":"升序";refresh();};$("previous").onclick=()=>{page--;render();};$("next").onclick=()=>{page++;render();};$("chart-column").onchange=chart;
$("export").onclick=()=>{const t=data.tables[selected],escapeCsv=v=>{let s=v===null?"":String(v);if(typeof v==="string"&&/^[\\s]*[=+@-]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';},parts=["\\ufeff",t.columns.map(c=>escapeCsv(c.label)).join(",")+"\\r\\n"];filtered.forEach(i=>parts.push(t.rows[i].map(escapeCsv).join(",")+"\\r\\n"));const url=URL.createObjectURL(new Blob(parts,{type:"text/csv;charset=utf-8"})),link=node("a");link.href=url;link.download="经营分析-筛选数据.csv";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};choose(0);
</script>'''


def _slim_script_v10():
    """Reuse the existing offline UI, changing only row loading for v10 opt-in."""
    script = HTML_SCRIPT
    def replace_once(old, new):
        nonlocal script
        if script.count(old) != 1:
            raise RuntimeError("legacy offline UI changed; rebuild slim adapter explicitly")
        script = script.replace(old, new, 1)

    replace_once('const data=JSON.parse(document.getElementById("report-data").textContent),$=id=>document.getElementById(id),size=100;',
        'const source=document.getElementById("report-data"),data=JSON.parse(source.textContent),$=id=>document.getElementById(id),size=100;source.remove();')
    replace_once('let selected=0,page=0,descending=false,filtered=[];',
        'let selected=0,page=0,descending=false,filtered=[],loading=0,wanted=0;')
    loader = '''async function loadRows(t){
if(Array.isArray(t.rows))return;
if(typeof DecompressionStream!=="function"||!crypto?.subtle)throw Error("当前浏览器不支持离线GZIP与SHA-256，请使用已验收浏览器打开完整文件");
if(!Number.isSafeInteger(t.rowsNdjsonBytes)||t.rowsNdjsonBytes<0||t.rowsNdjsonBytes>268435456||typeof t.rowsGzipBase64!=="string"||!/^[A-Za-z0-9+/]*={0,2}$/.test(t.rowsGzipBase64)||!(/^[a-f0-9]{64}$/.test(t.rowsGzipSha256))||!(/^[a-f0-9]{64}$/.test(t.proof?.rowDigest)))throw Error("压缩行目录无效");
const bytes=Uint8Array.from(atob(t.rowsGzipBase64),c=>c.charCodeAt(0));
const sha=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes)),v=>v.toString(16).padStart(2,"0")).join("");
if(sha!==t.rowsGzipSha256)throw Error("压缩行摘要不符");
const reader=new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip")).getReader(),blocks=[];
let total=0;while(true){const next=await reader.read();if(next.done)break;total+=next.value.length;if(total>t.rowsNdjsonBytes){await reader.cancel();throw Error("压缩行解压容量超限");}blocks.push(next.value);}
if(total!==t.rowsNdjsonBytes)throw Error("压缩行解压长度不符");
const raw=new Uint8Array(total);let offset=0;for(const block of blocks){raw.set(block,offset);offset+=block.length;}
const rowSha=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",raw)),v=>v.toString(16).padStart(2,"0")).join("");
if(rowSha!==t.proof.rowDigest)throw Error("完整行摘要不符");
const text=new TextDecoder("utf-8",{fatal:true}).decode(raw),lines=text?text.split("\\n"):[];if(lines.length&&lines.at(-1)==="")lines.pop();
const rows=lines.map(line=>JSON.parse(line));
if(!Array.isArray(rows)||rows.length!==t.proof.rowCount||rows.some(row=>!Array.isArray(row)||row.length!==t.columns.length))throw Error("压缩行数量或列宽不符");
t.rows=rows;
}
'''
    replace_once('data.tables.forEach((t,index)=>{const button=node("button",t.title+" · "+t.rows.length.toLocaleString());',
        loader+'data.tables.forEach((t,index)=>{const button=node("button",t.title+" · "+t.proof.rowCount.toLocaleString());')
    replace_once('function choose(index){selected=index;page=0;',
        'async function choose(index){const ticket=++loading;wanted=index;$("count").textContent="正在离线校验并载入工作表";$("tbody").replaceChildren();try{await loadRows(data.tables[index]);}catch(error){if(ticket===loading){data.tables[selected].rows=null;$("note").textContent="离线表无法完整载入："+String(error);$("count").textContent="无法显示或导出此表";$("thead").replaceChildren();$("tbody").replaceChildren();}return;}if(ticket!==loading){if(wanted!==index)data.tables[index].rows=null;return;}if(selected!==index&&data.tables[selected])data.tables[selected].rows=null;selected=index;page=0;')
    replace_once('function refresh(){const t=data.tables[selected],query=',
        'function refresh(){const t=data.tables[selected];if(!Array.isArray(t.rows))return;const query=')
    replace_once('$("export").onclick=()=>{const t=data.tables[selected],escapeCsv=',
        '$("export").onclick=()=>{const t=data.tables[selected];if(!Array.isArray(t.rows))return;const escapeCsv=')
    return script
