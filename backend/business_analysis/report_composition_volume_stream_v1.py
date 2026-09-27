"""Bounded, default-closed promotion detail volumes beside a 13-table preview.

An owning adapter must supply a trusted current-source recheck and an
unpublished staging sink. The complete source root is verified only after the
last row; a partial or changed run aborts every staged volume. This does not
make a sealed-v2 source large enough to supply reference-scale rows.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import math
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from .contracts import AnalysisContractError, canonical, digest
from .report_files import Column, Table, write_pair


SCHEMA = "business-composition-promotion-volume-source-v1"
RESULT_SCHEMA = "business-composition-promotion-volume-candidate-v1"
MAX_TOTAL_ROWS = 1_000_000
MAX_VOLUME_ROWS = 50_000
MAX_VOLUME_RAW_BYTES = 16 * 1024 * 1024
MAX_VOLUMES = 128
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _need(ok, message="推广明细分卷来源、行数或文件摘要无效"):
    if not ok:
        raise AnalysisContractError(message)


def _sha(value):
    return type(value) is str and len(value) == 64 and all(
        letter in "0123456789abcdef" for letter in value)


def _base(base):
    _need(type(base) is dict
        and base.get("schemaVersion") ==
            "business-report-composition-owning-preview-v1"
        and base.get("pairedBytesVerified") is True
        and base.get("tableCount") == 13
        and base.get("published") is False
        and base.get("agentReadPersisted") is False
        and type(base.get("reportBinding")) is dict
        and _sha(base.get("planDigest"))
        and type(base.get("sourceEvidenceDigests")) is dict
        and type(base.get("sourceRevisions")) is dict
        and type(base.get("promotionSourceKeys")) is list
        and base.get("resultDigest") == digest({key: value for key, value
            in base.items() if key != "resultDigest"}))


def _source(base, source):
    _base(base)
    _need(type(source) is dict and set(source) == {
        "schemaVersion", "reportBindingDigest", "planDigest", "sourceKey",
        "sourceEvidenceDigest", "sourceRevision", "rowCount", "rowDigest",
        "manifestDigest"})
    _need(source["schemaVersion"] == SCHEMA
        and source["reportBindingDigest"] == digest(base["reportBinding"])
        and source["planDigest"] == base["planDigest"]
        and type(source["sourceKey"]) is str
        and source["sourceKey"] in base["promotionSourceKeys"]
        and source["sourceKey"] in base["sourceEvidenceDigests"]
        and source["sourceEvidenceDigest"] ==
            base["sourceEvidenceDigests"][source["sourceKey"]]
        and source["sourceRevision"] ==
            base["sourceRevisions"][source["sourceKey"]]
        and type(source["rowCount"]) is int
        and 0 <= source["rowCount"] <= MAX_TOTAL_ROWS
        and _sha(source["rowDigest"])
        and source["manifestDigest"] == digest({key: value for key, value
            in source.items() if key != "manifestDigest"}))


def _verify_volume(table, receipt, xlsx_path, html_path):
    """Read back the compressed HTML and physical XLSX after each write."""
    _need(len(receipt["tables"]) == 1
        and receipt["tables"][0]["key"] == table.key
        and receipt["tables"][0]["rowCount"] == table.row_count)
    written = receipt["tables"][0]
    expected = hashlib.sha256()
    for row in table.rows:
        normalized = [str(value) if type(value) is int and
            abs(value) >= 10**15 else value for value in row]
        expected.update((canonical(normalized) + "\n").encode("utf-8"))
    _need(expected.hexdigest() == written["rowDigest"])
    html = Path(html_path).read_bytes().decode("utf-8")
    marker = '<script type="application/json" id="report-data">'
    _need(html.count(marker) == 1)
    payload = json.loads(html.split(marker, 1)[1].split("</script>", 1)[0])
    _need(payload.get("htmlPayloadVersion") == 2
        and len(payload["tables"]) == 1)
    part = payload["tables"][0]
    packed = base64.b64decode(part["rowsGzipBase64"], validate=True)
    raw = gzip.decompress(packed)
    _need(part["key"] == table.key and part["proof"] == written
        and part["rowsNdjsonBytes"] == len(raw)
        and part["rowsGzipSha256"] == hashlib.sha256(packed).hexdigest()
        and written["rowDigest"] == hashlib.sha256(raw).hexdigest()
        and raw.count(b"\n") == table.row_count)
    with zipfile.ZipFile(xlsx_path) as archive:
        stored = json.loads(archive.read("teruisi-manifest.json"))
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        physical = sheet.findall(f".//{_NS}sheetData/{_NS}row")
        _need(stored["tables"] == receipt["tables"]
            and len(physical) - 3 == table.row_count)
    return {"rowCount": table.row_count, "rowDigest": written["rowDigest"],
        "htmlSha256": hashlib.sha256(Path(html_path).read_bytes()).hexdigest(),
        "xlsxSha256": hashlib.sha256(Path(xlsx_path).read_bytes()).hexdigest(),
        "htmlBytes": Path(html_path).stat().st_size,
        "xlsxBytes": Path(xlsx_path).stat().st_size}


def build_candidate(base_manifest, source_manifest, columns, rows, stage_sink,
                    verify_current, *, enabled=False,
                    max_volume_rows=MAX_VOLUME_ROWS, checkpoint=None):
    """Stage complete paired volumes; return only an unpublished manifest.

    ``stage_sink`` must have stage(metadata, html_path, xlsx_path), abort(),
    and complete(manifest). It must keep all stages private until complete;
    no caller may interpret a partial stage as a downloadable file.
    """
    _need(enabled is True, "推广全量分卷默认关闭")
    _source(base_manifest, source_manifest)
    _need(callable(verify_current) and callable(getattr(stage_sink,
        "stage", None)) and callable(getattr(stage_sink, "abort", None))
        and callable(getattr(stage_sink, "complete", None)))
    _need(type(max_volume_rows) is int and 1 <= max_volume_rows <=
        MAX_VOLUME_ROWS and math.ceil(source_manifest["rowCount"] /
            max_volume_rows) <= MAX_VOLUMES)
    _need(type(columns) is tuple and 3 <= len(columns) <= 24
        and all(type(col) is Column for col in columns)
        and columns[0].key == "sourceKey"
        and columns[1].key == "rowIndex")
    _need(verify_current(base_manifest, source_manifest) is True,
        "分卷开始前来源修订未获拥有方确认")
    raw_sha, count, pending_bytes, pending, volumes = (
        hashlib.sha256(), 0, 0, [], [])

    def flush():
        nonlocal pending_bytes, pending
        if not pending:
            return
        _need(len(volumes) < MAX_VOLUMES)
        number = len(volumes) + 1
        start = count - len(pending)
        table = Table("promotion_detail", "推广来源完整明细",
            "封存来源逐行数据；缺失保持空值，财报/B端/市场不混算。",
            columns, tuple(pending), len(pending))
        metadata = {"schemaVersion": RESULT_SCHEMA,
            "sourceManifestDigest": source_manifest["manifestDigest"],
            "reportBindingDigest": source_manifest["reportBindingDigest"],
            "sourceKey": source_manifest["sourceKey"], "volume": number,
            "firstRowIndex": start, "lastRowIndex": count - 1,
            "published": False, "agentReadPersisted": False}
        with tempfile.TemporaryDirectory(prefix="business-composition-volume-") as tmp:
            html_path = Path(tmp) / "report.html"
            xlsx_path = Path(tmp) / "report.xlsx"
            with html_path.open("w+b") as html_file, xlsx_path.open("w+b") as xlsx_file:
                receipt = write_pair(xlsx_file, html_file,
                    title=f"推广来源明细 · 第 {number} 卷",
                    metadata=metadata, tables=(table,), checkpoint=checkpoint,
                    html_layout_version=2, xlsx_opc_version=2,
                    html_payload_version=2)
            proof = _verify_volume(table, receipt, xlsx_path, html_path)
            volume = {**metadata, **proof}
            volume["volumeDigest"] = digest(volume)
            stage_sink.stage(volume, html_path, xlsx_path)
            volumes.append(volume)
        pending, pending_bytes = [], 0
        if checkpoint:
            checkpoint({"stage": "composition_volume",
                "volume": number, "rows": count})

    try:
        for row in rows:
            _need(type(row) in (tuple, list) and len(row) == len(columns)
                and row[0] == source_manifest["sourceKey"]
                and type(row[1]) is int and row[1] == count,
                "推广行身份不连续或跨来源")
            encoded = (canonical(list(row)) + "\n").encode("utf-8")
            _need(len(encoded) <= MAX_VOLUME_RAW_BYTES)
            if pending and (len(pending) == max_volume_rows
                    or pending_bytes + len(encoded) > MAX_VOLUME_RAW_BYTES):
                flush()
            _need(count < source_manifest["rowCount"])
            raw_sha.update(encoded)
            pending.append(tuple(row))
            pending_bytes += len(encoded)
            count += 1
        flush()
        _need(count == source_manifest["rowCount"]
            and raw_sha.hexdigest() == source_manifest["rowDigest"]
            and sum(volume["rowCount"] for volume in volumes) == count
            and all(volume["firstRowIndex"] == (0 if index == 0 else
                volumes[index - 1]["lastRowIndex"] + 1)
                for index, volume in enumerate(volumes)),
            "完整推广行数、顺序或来源根摘要不符")
        _need(verify_current(base_manifest, source_manifest) is True,
            "分卷完成后来源、报告或管理员修订变化")
        body = {"schemaVersion": RESULT_SCHEMA,
            "reportBindingDigest": source_manifest["reportBindingDigest"],
            "planDigest": source_manifest["planDigest"],
            "sourceManifestDigest": source_manifest["manifestDigest"],
            "sourceRowCount": count, "sourceRowDigest": raw_sha.hexdigest(),
            "volumes": volumes, "volumeCount": len(volumes),
            "status": "staged_unpublished", "agentReadPersisted": False,
            "authorityVerified": False, "registeredRenderer": False}
        manifest = {**body, "resultDigest": digest(body)}
        stage_sink.complete(manifest)
        return manifest
    except Exception:
        stage_sink.abort()
        raise
