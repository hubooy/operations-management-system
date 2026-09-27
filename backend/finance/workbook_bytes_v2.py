"""Strict, independent XLSX byte reader for future finance attestation.

Only a complete single-month, formula-free OOXML workbook is accepted. The
existing Worker parser/import path is untouched; ambiguity is a refusal here.
No source bytes or raw customer text are persisted by this module.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import io
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile


MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_ZIP_ENTRIES = 1024
MAX_SOURCE_CELLS = 200_000
MAX_ROW_INDEX = 1_000_000
MAX_COLUMN_INDEX = 500
MAX_SHARED_TEXT = 8 * 1024 * 1024
MAX_SAFE_INTEGER = 9_007_199_254_740_991
RELATIONSHIP_ATTR = (
    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
CELL_REF = re.compile(r"([A-Z]{1,3})([1-9][0-9]{0,6})\Z")
SHEET_MONTH = re.compile(r"(\d{2}|20\d{2})[.\-/年](\d{1,2})(?:月)?\Z")
TITLE_MONTH = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月")
SUMMARY_RULES = tuple((re.compile(pattern), key) for pattern, key in (
    (r"^一①.*销售额", "gross_sales"),
    (r"^一②.*退货金额", "return_amount"),
    (r"^一③.*实际销售金额", "net_sales"),
    (r"^二①.*发货总成本", "shipping_cost"),
    (r"^二②.*退货退回成本", "return_cost"),
    (r"^二③.*包材", "packaging_cost"),
    (r"^二④.*实际发货成本", "net_cost"),
    (r"^三①.*其他业务收入", "other_income"),
    (r"^四①.*大毛利", "gross_profit"),
    (r"^四②.*大毛利率", "gross_margin"),
    (r"^六①.*销售费用合计", "selling_expense_total"),
    (r"^六②.*销售费用.*运营费", "operation_expense"),
    (r"^六③.*销售费用.*工资", "salary_expense"),
    (r"^六④.*小毛利", "small_profit"),
    (r"^六⑤.*小毛利率", "small_margin"),
    (r"^七①.*其他费用合计", "other_expense_total"),
    (r"^七①.*仓库租金", "warehouse_rent"),
    (r"^七②.*管理费用", "management_expense"),
    (r"^七③.*管理部.*工资", "management_salary"),
    (r"^七④.*财务费用", "finance_expense"),
    (r"^七⑤.*税费", "tax_expense"),
    (r"^八.*营业外收入", "non_operating_income"),
    (r"^九.*其他业务支出", "other_business_expense"),
    (r"^十.*利[润潤]", "profit"),
    (r"^利润率", "profit_margin"),
))


class WorkbookBytesError(ValueError):
    pass


def _need(ok: bool, message: str = "财报原始XLSX不属于首版可独立验真范围"):
    if not ok:
        raise WorkbookBytesError(message)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _number_text(value: Decimal) -> str:
    _need(value.is_finite() and abs(value) <= MAX_SAFE_INTEGER)
    if value == 0:
        return "0"
    _need(-12 <= value.adjusted() <= 15,
        "数值指数超出跨语言验真范围")
    return format(value.normalize(), "f")


def _clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return _number_text(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).replace("\r", "").strip()


def _raw(value) -> str:
    if isinstance(value, Decimal):
        return _number_text(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _compact(value) -> str:
    return re.sub(r"[\s　]+", "", _clean(value))


def _numeric(value):
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if not isinstance(value, str):
        return None
    raw = value.strip().replace(",", "")
    if not raw:
        return None
    _need(len(raw) <= 64, "数值文本超出验真容量")
    percent = raw.endswith("%")
    try:
        number = Decimal(raw[:-1] if percent else raw)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    _number_text(number)
    return number / 100 if percent else number


def _scaled(value, factor: int):
    numeric = _numeric(value)
    if numeric is None:
        return None
    scaled = numeric * factor
    _need(scaled == scaled.to_integral_value()
        and abs(scaled) <= MAX_SAFE_INTEGER,
        "原始金额或费率需要跨语言舍入，首版拒绝")
    return int(scaled)


def _parse_month(sheet_name: str, title) -> str | None:
    sheet = SHEET_MONTH.fullmatch(sheet_name.strip())
    if sheet is None:
        return None
    found = TITLE_MONTH.search(_clean(title))
    year = int(found.group(1) if found else sheet.group(1))
    year = year + 2000 if year < 100 else year
    month = int(found.group(2) if found else sheet.group(2))
    if not 2000 <= year <= 2100 or not 1 <= month <= 12:
        return None
    return f"{year:04d}-{month:02d}"


def _col_number(letters: str) -> int:
    value = 0
    for letter in letters:
        value = value * 26 + ord(letter) - 64
    return value - 1


def _xml(raw: bytes):
    _need(0 < len(raw) <= MAX_XML_BYTES
        and b"<!DOCTYPE" not in raw.upper()
        and b"<!ENTITY" not in raw.upper(),
        "工作簿XML过大或包含外部实体")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as error:
        raise WorkbookBytesError("工作簿XML结构无效") from error


def _zip(raw: bytes):
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_SOURCE_BYTES
        and raw.startswith(b"PK\x03\x04"),
        "原始文件不是容量内的XLSX")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        entries = archive.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        raise WorkbookBytesError("XLSX压缩包无效") from error
    try:
        _need(0 < len(entries) <= MAX_ZIP_ENTRIES)
        names = set()
        folded_names = set()
        size = 0
        for entry in entries:
            name = entry.filename
            canonical_name = name[:-1] if entry.is_dir() else name
            _need(name not in names and canonical_name.casefold() not in folded_names
                and not name.startswith("/")
                and "\\" not in name and ".." not in name.split("/")
                and posixpath.normpath(canonical_name) == canonical_name
                and not entry.flag_bits & 1,
                "工作簿包含重复、越界或加密ZIP成员")
            names.add(name)
            folded_names.add(canonical_name.casefold())
            size += entry.file_size
            _need(entry.file_size <= MAX_XML_BYTES
                and entry.file_size <= max(entry.compress_size, 1) * 200
                and size <= MAX_UNCOMPRESSED_BYTES,
                "工作簿ZIP展开超过固定容量")
        _need("xl/workbook.xml" in names
            and "xl/_rels/workbook.xml.rels" in names)
        return archive, names
    except Exception:
        archive.close()
        raise


def _read(archive, name: str) -> bytes:
    try:
        raw = archive.read(name)
    except (KeyError, OSError, zipfile.BadZipFile,
            NotImplementedError) as error:
        raise WorkbookBytesError("XLSX成员缺失或校验失败") from error
    _need(len(raw) <= MAX_XML_BYTES)
    return raw


def _shared_strings(archive, names):
    if "xl/sharedStrings.xml" not in names:
        return []
    root = _xml(_read(archive, "xl/sharedStrings.xml"))
    values = []
    total = 0
    for item in root:
        if _local(item.tag) != "si":
            continue
        value = "".join(node.text or "" for node in item.iter()
            if _local(node.tag) == "t")
        total += len(value.encode("utf-8"))
        _need(total <= MAX_SHARED_TEXT and len(values) < MAX_SOURCE_CELLS,
            "共享字符串数量或字节超过固定容量")
        values.append(value)
    return values


def _sheet_cells(raw: bytes, shared):
    root = _xml(raw)
    cells = {}
    seen = 0
    for item in root.iter():
        if _local(item.tag) != "c":
            continue
        match = CELL_REF.fullmatch(item.get("r", ""))
        _need(match is not None, "原始格缺Excel坐标")
        col, row = _col_number(match.group(1)), int(match.group(2)) - 1
        _need(0 <= col <= MAX_COLUMN_INDEX and 0 <= row <= MAX_ROW_INDEX)
        key = (row, col)
        _need(key not in cells, "工作表存在重复物理格")
        seen += 1
        _need(seen <= MAX_SOURCE_CELLS)
        _need(not any(_local(node.tag) == "f" for node in item),
            "公式格缓存与跨语言重算未获验收")
        kind = item.get("t", "n")
        values = [node for node in item if _local(node.tag) == "v"]
        inline = [node for node in item if _local(node.tag) == "is"]
        _need(len(values) <= 1 and len(inline) <= 1)
        if kind == "inlineStr":
            value = "".join(node.text or "" for node in inline[0].iter()
                if _local(node.tag) == "t") if inline else ""
        elif kind == "s":
            _need(len(values) == 1 and values[0].text is not None)
            try:
                index = int(values[0].text)
                _need(index >= 0)
                value = shared[index]
            except (ValueError, IndexError) as error:
                raise WorkbookBytesError("共享字符串下标无效") from error
        elif kind == "b":
            _need(len(values) == 1 and values[0].text in {"0", "1"})
            value = values[0].text == "1"
        elif kind in {"n", "str"}:
            if not values or values[0].text is None:
                value = None
            elif kind == "str":
                value = values[0].text
            else:
                _need(len(values[0].text) <= 64)
                try:
                    value = Decimal(values[0].text)
                except InvalidOperation as error:
                    raise WorkbookBytesError("数值格无效") from error
                _number_text(value)
        else:
            raise WorkbookBytesError("工作表含不支持的格类型")
        cells[key] = value
    return cells


def _dimensions(cells):
    used = [col for row, col in cells if row in {0, 1, 2}]
    _need(used)
    maximum = max(used) + 1
    business = _compact(cells.get((0, 1))) or "事业部汇总"
    dimensions = [{"columnIndex": 1,
        "scopeKey": "business:" + business.lower(),
        "scopeType": "business", "scopeName": business, "groupName": ""}]
    group = ""
    for index in range(2, maximum):
        raw_group = _compact(cells.get((1, index)))
        if raw_group:
            group = raw_group
        raw_shop = _compact(cells.get((2, index)))
        if not raw_shop:
            continue
        is_group = raw_shop == "组汇总"
        name = group or f"第{index + 1}列组汇总" if is_group else raw_shop
        dimensions.append({"columnIndex": index,
            "scopeKey": ("group:" if is_group else "shop:") + name.lower(),
            "scopeType": "group" if is_group else "shop",
            "scopeName": name, "groupName": group})
    _need(2 <= len(dimensions) <= MAX_COLUMN_INDEX,
        "财报缺少可识别店铺/分组列")
    return dimensions


def _line(month, section, metric, subject, dimension, value_type, raw_value,
          row_index, is_total):
    numeric = _numeric(raw_value)
    amount = (_scaled(numeric, 100) if numeric is not None
        and value_type == "amount" else None)
    rate = (_scaled(numeric, 10_000) if numeric is not None
        and value_type == "rate" else None)
    return {"month": month, "section": section, "metricKey": metric,
        "subjectName": subject, "scopeKey": dimension["scopeKey"],
        "scopeType": dimension["scopeType"],
        "scopeName": dimension["scopeName"],
        "groupName": dimension["groupName"], "valueType": value_type,
        "amountCents": amount if value_type == "amount" else None,
        "rateBps": rate if value_type == "rate" else None,
        "rawValue": _clean(raw_value), "sourceRowCount": 1,
        "sortOrder": row_index + 1, "isTotal": is_total}


def _month_sheet(sheet_name, cells):
    month = _parse_month(sheet_name, cells.get((0, 0)))
    if month is None:
        return None
    headers = sorted(row for (row, col), value in cells.items()
        if col == 0 and _compact(value) == "金蝶科目名称")
    _need(headers and headers[0] >= 4,
        "有效月份缺少金蝶科目明细分界")
    boundary = headers[0]
    dimensions = _dimensions(cells)
    raw_lines = []
    origins = []
    for row in range(1, boundary):
        subject = _compact(cells.get((row, 0)))
        if not subject:
            continue
        metric = next((key for pattern, key in SUMMARY_RULES
            if pattern.search(subject)), "dynamic:" + subject)
        for dimension in dimensions:
            column = dimension["columnIndex"]
            value = cells.get((row, column))
            if value is None or value == "":
                continue
            kind = ("rate" if re.search(r"率|占比", subject) else
                "text" if re.search(r"时间|日期", subject) else
                "text" if _numeric(value) is None else "amount")
            raw_lines.append(_line(month, "summary", metric, subject,
                dimension, kind, value, row, "合计" in subject))
            origins.append({"rowIndex": row, "columnIndex": column})
    for row in sorted({r for r, _ in cells if r > boundary}):
        subject = _compact(cells.get((row, 0)))
        if not subject:
            continue
        for dimension in dimensions:
            column = dimension["columnIndex"]
            numeric = _numeric(cells.get((row, column)))
            if numeric is None:
                continue
            raw_lines.append(_line(month, "kingdee",
                "selling_expense_total" if subject == "销售费用" else
                "subject:" + subject, subject, dimension, "amount", numeric,
                row, subject == "销售费用"))
            origins.append({"rowIndex": row, "columnIndex": column})
    _need(0 < len(raw_lines) <= 100_000,
        "单月可验真格超过固定容量")
    grouped = {}
    for line in raw_lines:
        key = (line["section"], line["scopeKey"], line["subjectName"])
        current = grouped.get(key)
        if current is None:
            grouped[key] = dict(line)
            continue
        for metric in ("amountCents", "rateBps"):
            if line[metric] is not None:
                current[metric] = (current[metric] or 0) + line[metric]
        current["sourceRowCount"] += 1
        current["sortOrder"] = min(current["sortOrder"], line["sortOrder"])
        current["isTotal"] = current["isTotal"] or line["isTotal"]
    lines = sorted(grouped.values(), key=lambda line: (line["section"],
        line["sortOrder"], line["scopeType"], line["scopeName"]))
    by_scope = {}
    for line in lines:
        if line["section"] == "kingdee":
            by_scope.setdefault(line["scopeKey"], []).append(line)
    for scope_lines in by_scope.values():
        parent = next((line for line in scope_lines if line["subjectName"] ==
            "销售费用"), None)
        if parent is None:
            continue
        amount = sum((line["amountCents"] or 0) for line in scope_lines
            if line["subjectName"].startswith("销售费用_") and not line["isTotal"])
        parent["amountCents"] = amount
        parent["rawValue"] = _number_text(Decimal(amount) / 100)
    last_column = max(item["columnIndex"] for item in dimensions)
    header_cells = [{"columnIndex": index,
        "rawGroupCell": None if cells.get((1, index)) is None else
            _raw(cells[(1, index)]),
        "rawShopCell": None if cells.get((2, index)) is None else
            _raw(cells[(2, index)])}
        for index in range(1, last_column + 1)]
    parsed = {"month": month, "sheetName": sheet_name,
        "businessName": dimensions[0]["scopeName"],
        "shopCount": len({item["scopeName"] for item in dimensions
            if item["scopeType"] == "shop"}),
        "subjectCount": len({line["subjectName"] for line in lines
            if line["section"] == "kingdee"}), "lines": lines}
    return parsed, {"month": month, "sheetName": sheet_name,
        "dimensions": dimensions, "rawLines": raw_lines,
        "origins": origins, "headerCells": header_cells}


def parse_single_month_xlsx(raw: bytes):
    archive, names = _zip(raw)
    try:
        book = _xml(_read(archive, "xl/workbook.xml"))
        relations = _xml(_read(archive,
            "xl/_rels/workbook.xml.rels"))
        targets = {item.get("Id"): item.get("Target") for item in relations
            if _local(item.tag) == "Relationship"
            and item.get("Type", "").endswith("/worksheet")
            and item.get("TargetMode") != "External"}
        shared = _shared_strings(archive, names)
        sheets = []
        month = None
        evidence_input = None
        for item in book.iter():
            if _local(item.tag) != "sheet":
                continue
            name = item.get("name", "")
            relation = targets.get(item.get(RELATIONSHIP_ATTR))
            _need(bool(name) and len(name) <= 200 and relation)
            path = (relation.lstrip("/") if relation.startswith("/") else
                posixpath.normpath(posixpath.join("xl", relation)))
            _need(path.startswith("xl/worksheets/") and path in names)
            xml = _read(archive, path)
            sheets.append({"name": name, "path": path,
                "hidden": item.get("state", "visible") != "visible",
                "sha256": hashlib.sha256(xml).hexdigest()})
            if SHEET_MONTH.fullmatch(name.strip()) is None:
                continue
            _need(item.get("state", "visible") == "visible")
            parsed = _month_sheet(name, _sheet_cells(xml, shared))
            _need(parsed is not None)
            _need(month is None, "一个文件含多个有效月份或重复月份表")
            month, evidence_input = parsed
        _need(month is not None and evidence_input is not None
            and len(sheets) <= 500)
        return {"rawFileHash": hashlib.sha256(raw).hexdigest(),
            "fileSizeBytes": len(raw), "sourceSheetCount": len(sheets),
            "sheetManifest": sheets, "month": month,
            "evidenceInput": evidence_input}
    finally:
        archive.close()
