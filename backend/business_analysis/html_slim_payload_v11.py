"""Independent bounded check of actual v11 HTML bytes against its payload proof.

The pure verifier authenticates no report, actor or source. It must be called
only after an owning report fence, and never substitutes for a SQL receipt.
"""
import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
import zlib

from . import volume_delivery
from .contracts import AnalysisContractError, canonical


SCRIPT = re.compile(rb'<script type="application/json" id="report-data">(.*?)</script>', re.S)
BASE64 = re.compile(r"[A-Za-z0-9+/]*={0,2}\Z")
BLOCK = 2 * 1024 * 1024


def _fail(message):
    raise AnalysisContractError(message)


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            _fail("压缩HTML包含重复JSON键")
        value[key] = item
    return value


def _rows(value, expected, compressed_sha, row_digest, row_count, columns,
          checkpoint=None):
    if (type(value) is not str or BASE64.fullmatch(value) is None
            or type(expected) is not int or not 0 <= expected <= volume_delivery.MAX_FILE_BYTES):
        _fail("renderer 11 压缩行编码或容量无效")
    try:
        packed = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise AnalysisContractError("renderer 11 压缩行base64无效") from error
    if hashlib.sha256(packed).hexdigest() != compressed_sha:
        _fail("renderer 11 压缩字节摘要不同")
    decoder, raw_sha = zlib.decompressobj(wbits=31), hashlib.sha256()
    total = seen = 0
    pending = b""
    try:
        for index in range(0, len(packed), 65536):
            if checkpoint:
                checkpoint()
            compressed = packed[index:index+65536]
            while compressed:
                before = len(compressed)
                output = decoder.decompress(compressed,
                    min(BLOCK, expected - total + 1))
                compressed = decoder.unconsumed_tail
                total += len(output)
                if total > expected:
                    _fail("renderer 11 解压行超过声明容量")
                raw_sha.update(output)
                parts = (pending + output).split(b"\n")
                pending = parts.pop()
                for line in parts:
                    row = json.loads(line, object_pairs_hook=_pairs)
                    if type(row) is not list or len(row) != columns:
                        _fail("renderer 11 解压行列宽不同")
                    if (canonical(row) + "\n").encode() != line + b"\n":
                        _fail("renderer 11 解压行不是规范JSON")
                    seen += 1
                    if checkpoint and seen % 1000 == 0:
                        checkpoint()
                    if seen > row_count:
                        _fail("renderer 11 解压行超过声明行数")
                if compressed and not output and len(compressed) == before:
                    _fail("renderer 11 解压过程无进展")
    except (zlib.error, ValueError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("renderer 11 压缩行无法完整解码") from error
    if (not decoder.eof or decoder.unused_data or pending or total != expected
            or seen != row_count or raw_sha.hexdigest() != row_digest):
        _fail("renderer 11 解压行数或摘要不符")
    return len(packed)


def _verify_file(path, volume, checkpoint=None):
    """Check one file and every table against the already checked v11 manifest."""
    path = Path(path)
    if not 1 <= path.stat().st_size <= volume_delivery.MAX_FILE_BYTES:
        _fail("renderer 11 HTML文件超过容量")
    if checkpoint:
        checkpoint()
    raw_html = path.read_bytes()
    html_sha = hashlib.sha256(raw_html).hexdigest()
    if (len(raw_html) != volume["files"]["html"]["bytes"] or
            html_sha != volume["files"]["html"]["sha256"]):
        _fail("renderer 11 HTML实际文件摘要不同")
    matches = SCRIPT.findall(raw_html)
    if len(matches) != 1:
        _fail("renderer 11 缺少唯一惰性HTML数据段")
    try:
        data = json.loads(matches[0], object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("renderer 11 HTML数据段无效") from error
    if (type(data) is not dict or set(data) != {"htmlPayloadVersion", "title",
            "metadata", "tables"} or data["htmlPayloadVersion"] != 2 or
            type(data["tables"]) is not list or
            len(data["tables"]) != len(volume["tables"])):
        _fail("renderer 11 HTML表目录无效")
    volume_delivery._html_payload_v11(volume["htmlPayload"], volume)
    for table, part, payload in zip(data["tables"], volume["tables"],
            volume["htmlPayload"]["tables"]):
        if checkpoint:
            checkpoint()
        if (type(table) is not dict or set(table) != {"key", "title", "note",
                "columns", "rowsGzipBase64", "rowsNdjsonBytes",
                "rowsGzipSha256", "proof"} or
                table["key"] != part["fragmentKey"] or
                table["proof"]["rowCount"] != part["rowLimit"] or
                table["proof"]["rowDigest"] != part["rowDigest"] or
                table["rowsNdjsonBytes"] != payload["rowsNdjsonBytes"] or
                table["rowsGzipSha256"] != payload["rowsGzipSha256"]):
            _fail("renderer 11 HTML表片证明不同")
        size = _rows(table["rowsGzipBase64"], payload["rowsNdjsonBytes"],
            payload["rowsGzipSha256"], part["rowDigest"],
            part["rowLimit"], part["columnCount"], checkpoint)
        if size != payload["rowsGzipBytes"]:
            _fail("renderer 11 压缩字节数不同")
    return {"volumeIndex": volume["volumeIndex"],
        "tables": len(data["tables"]), "rows": volume["rowCount"],
        "htmlSha256": html_sha, "htmlPayloadVersion": 2}


def verify_file(path, volume, checkpoint=None):
    """Fail closed with one contract error for malformed or changed bytes."""
    try:
        return _verify_file(path, volume, checkpoint)
    except AnalysisContractError:
        raise
    except (OSError, KeyError, TypeError, ValueError, UnicodeError,
            RecursionError, binascii.Error) as error:
        raise AnalysisContractError("renderer 11 HTML无法完整核验") from error
