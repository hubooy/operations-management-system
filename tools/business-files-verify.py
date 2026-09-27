"""Independently read streamed XLSX rows and verify paired file manifests."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("directory", type=Path)
directory = parser.parse_args().directory.resolve()
manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
verified = []
with zipfile.ZipFile(directory / "report.xlsx") as archive:
    assert archive.testzip() is None
    assert json.loads(archive.read("teruisi-manifest.json"))["tables"] == manifest["tables"]
    for index, proof in enumerate(manifest["tables"], 1):
        count, sha, formulas = 0, hashlib.sha256(), 0
        with archive.open(f"xl/worksheets/sheet{index}.xml") as stream:
            for event, element in ET.iterparse(stream, events=("end",)):
                if element.tag != ns+"row":
                    continue
                row_number = int(element.get("r"))
                if 4 <= row_number < proof["rowCount"]+4:
                    cells = element.findall(ns+"c")
                    assert len(cells) == proof["columnCount"]
                    values = []
                    for cell in cells:
                        kind = cell.get("t")
                        value, formula = cell.find(ns+"v"), cell.find(ns+"f")
                        if formula is not None:
                            formulas += 1
                            assert formula.text.startswith("IF(COUNT(")
                            assert "[" not in formula.text and "!" not in formula.text
                        if kind == "inlineStr":
                            values.append(''.join(cell.itertext()))
                        elif value is None:
                            values.append(None)
                        elif kind == "b":
                            values.append(value.text == "1")
                        else:
                            raw = value.text
                            values.append(float(raw) if any(c in raw for c in ".eE") else int(raw))
                    sha.update((canonical(values)+'\n').encode())
                    count += 1
                element.clear()
        assert count == proof["rowCount"] and sha.hexdigest() == proof["rowDigest"], proof["key"]
        verified.append({"key": proof["key"], "rows": count, "columns": proof["columnCount"], "rowDigestVerified": True, "formulas": formulas})
for suffix, expected in manifest["files"].items():
    sha = hashlib.sha256()
    file = directory / ("report."+suffix)
    with file.open("rb") as stream:
        while block := stream.read(1024*1024):
            sha.update(block)
    assert file.stat().st_size == expected["bytes"] and sha.hexdigest() == expected["sha256"]
result = {"passed": True, "verifiedTables": verified, "fileDigestsVerified": True, "productionReads": False}
(directory / "xlsx-evidence.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))
