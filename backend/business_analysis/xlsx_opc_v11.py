"""Streaming OPC/formula inspection for unpublished renderer-11 workbooks."""
from __future__ import annotations

import hashlib
import json
import xml.parsers.expat
import zipfile

from .contracts import AnalysisContractError, canonical, digest


BLOCK = 512 * 1024
MAX_MEMBER_BYTES = 1024 * 1024 * 1024


def _fail(message="v11 XLSX OPC或公式文本与已声明报告不一致"):
    raise AnalysisContractError(message)


def inspect(path, volume, *, checkpoint=lambda: None):
    """Read every member/CRC and XML formula without loading large sheets."""
    members, formulas, sheets = [], [], []
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            required = {"[Content_Types].xml", "_rels/.rels",
                "xl/workbook.xml", "xl/_rels/workbook.xml.rels",
                "xl/styles.xml", "teruisi-manifest.json"}
            if (len(names) > 2048 or len(names) != len(set(names)) or
                    not required.issubset(names)
                    or any(name.startswith("/") or "\\" in name or
                        ".." in name.split("/") or
                        "externalLink" in name or "vbaProject" in name
                        for name in names)):
                _fail()
            for name in ("[Content_Types].xml", "xl/workbook.xml",
                    "teruisi-manifest.json"):
                if archive.getinfo(name).file_size > 16 * 1024 * 1024:
                    _fail("v11 XLSX 元数据成员超过固定上限")
            if (b'Extension="json" ContentType="application/json"' not in
                    archive.read("[Content_Types].xml") or
                    b'fullCalcOnLoad="1"' not in archive.read("xl/workbook.xml")):
                _fail()
            embedded = json.loads(archive.read("teruisi-manifest.json"))
            if ([item["rowDigest"] for item in embedded["tables"]] !=
                    [item["rowDigest"] for item in volume["tables"]] or
                    embedded.get("budgetCalculator") !=
                    volume.get("budgetCalculator")):
                _fail()
            for name in names:
                checkpoint()
                info = archive.getinfo(name)
                if info.file_size > MAX_MEMBER_BYTES:
                    _fail("v11 XLSX 单成员超过固定展开上限")
                sha, size = hashlib.sha256(), 0
                current_cell, formula_parts = None, None
                formula_count, formula_sha = 0, hashlib.sha256()
                parser = None
                if name.endswith((".xml", ".rels")):
                    parser = xml.parsers.expat.ParserCreate()
                    parser.SetParamEntityParsing(
                        xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)

                    def opening(tag, attrs):
                        nonlocal current_cell, formula_parts, formula_count
                        if tag == "c":
                            current_cell = attrs.get("r")
                        elif tag == "f":
                            formula_count += 1
                            formula_parts = []

                    def characters(value):
                        if formula_parts is not None:
                            formula_parts.append(value)

                    def closing(tag):
                        nonlocal current_cell, formula_parts
                        if tag == "f":
                            formula_sha.update((canonical([current_cell,
                                "".join(formula_parts)]) + "\n").encode())
                            formula_parts = None
                        elif tag == "c":
                            current_cell = None

                    parser.StartElementHandler = opening
                    parser.CharacterDataHandler = characters
                    parser.EndElementHandler = closing
                    parser.StartDoctypeDeclHandler = lambda *args: _fail()
                    parser.ExternalEntityRefHandler = lambda *args: _fail()
                with archive.open(info) as source:
                    for block in iter(lambda: source.read(BLOCK), b""):
                        checkpoint()
                        size += len(block)
                        sha.update(block)
                        if parser is not None:
                            parser.Parse(block, False)
                if parser is not None:
                    parser.Parse(b"", True)
                if size != info.file_size:
                    _fail()
                members.append([name, size, sha.hexdigest()])
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
                    sheets.append(name)
                    formulas.append([name, formula_count, formula_sha.hexdigest()])
            if len(sheets) != len(volume["tables"]) + volume["nativeBudgetSheets"]:
                _fail()
            if volume["nativeBudgetSheets"] == 3:
                if not any(item[1] > 0 for item in formulas[-3:]):
                    _fail("v11 固定预算原生试算公式缺失")
            elif volume["nativeBudgetSheets"] != 0:
                _fail()
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile,
            RuntimeError, xml.parsers.expat.ExpatError) as error:
        raise AnalysisContractError("v11 XLSX OPC内容无法完整核验") from error
    return {"memberDigest": digest(members), "formulaDigest": digest(formulas),
        "sheetCount": len(sheets)}
