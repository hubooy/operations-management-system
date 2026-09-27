"""Verify a synthetic opt-in v10 HTML against its exact complete manifest.

This checks bytes, compressed payloads and all row digests; it is not report
authority, a browser compatibility proof, or Microsoft Excel recalculation.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import tracemalloc
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from business_analysis.contracts import canonical


MAX_FILE = 256 * 1024 * 1024
MAX_TABLE_NDJSON = 256 * 1024 * 1024
MAX_INFLATE_BLOCK = 2 * 1024 * 1024
SCRIPT = re.compile(rb'<script type="application/json" id="report-data">(.*?)</script>', re.S)
HEX = re.compile(r"[0-9a-f]{64}\Z")


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError("duplicate JSON key in slim HTML")
        value[key] = item
    return value


def _sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _verify_rows(value, expected, compressed_sha, row_digest, row_count, columns):
    """Verify a table with bounded decompression and one row in memory."""
    if (type(expected) is not int or not 0 <= expected <= MAX_TABLE_NDJSON
            or type(value) is not str or not re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", value)):
        raise ValueError("compressed row descriptor is invalid")
    packed = base64.b64decode(value, validate=True)
    if hashlib.sha256(packed).hexdigest() != compressed_sha:
        raise ValueError("compressed table SHA differs")
    decoder = zlib.decompressobj(wbits=31)
    raw_sha, seen, total, pending = hashlib.sha256(), 0, 0, b""
    for index in range(0, len(packed), 65536):
        compressed = packed[index:index+65536]
        while compressed:
            before = len(compressed)
            output = decoder.decompress(compressed,
                min(MAX_INFLATE_BLOCK, expected - total + 1))
            compressed = decoder.unconsumed_tail
            total += len(output)
            if total > expected:
                raise ValueError("compressed table exceeds declared byte capacity")
            raw_sha.update(output)
            parts = (pending + output).split(b"\n")
            pending = parts.pop()
            for line in parts:
                row = json.loads(line, object_pairs_hook=_pairs)
                if type(row) is not list or len(row) != columns:
                    raise ValueError("decompressed table row width differs")
                if (canonical(row) + "\n").encode() != line + b"\n":
                    raise ValueError("decompressed table row is not canonical")
                seen += 1
                if seen > row_count:
                    raise ValueError("decompressed table has extra rows")
            if compressed and not output and len(compressed) == before:
                raise ValueError("compressed stream made no progress")
    if not decoder.eof or decoder.unused_data or pending or total != expected:
        raise ValueError("compressed table is incomplete or contains trailing data")
    if seen != row_count or raw_sha.hexdigest() != row_digest:
        raise ValueError("decompressed NDJSON row count or digest differs")
    return seen


def verify(html_path, manifest_path, *, volume_index=1):
    html_path, manifest_path = Path(html_path), Path(manifest_path)
    if not 1 <= html_path.stat().st_size <= MAX_FILE:
        raise ValueError("HTML exceeds v10 file cap")
    full = json.loads(manifest_path.read_bytes(), object_pairs_hook=_pairs)
    if (full["rendererVersion"] != 10 or type(volume_index) is not int
            or not 1 <= volume_index <= full["volumeCount"]):
        raise ValueError("selected synthetic v10 volume is invalid")
    volume = full["volumes"][volume_index - 1]
    actual_sha = _sha(html_path)
    if (actual_sha != volume["files"]["html"]["sha256"] or
            html_path.stat().st_size != volume["files"]["html"]["bytes"]):
        raise ValueError("HTML bytes differ from complete v10 manifest")
    matches = SCRIPT.findall(html_path.read_bytes())
    if len(matches) != 1:
        raise ValueError("expected exactly one inert report-data script")
    data = json.loads(matches[0], object_pairs_hook=_pairs)
    if (set(data) != {"htmlPayloadVersion", "title", "metadata", "tables"}
            or data["htmlPayloadVersion"] != 2 or type(data["tables"]) is not list
            or len(data["tables"]) != len(volume["tables"])):
        raise ValueError("slim report directory does not match bound fragments")
    row_total = 0
    for item, proof in zip(data["tables"], volume["tables"]):
        if (set(item) != {"key", "title", "note", "columns", "rowsGzipBase64",
                "rowsNdjsonBytes", "rowsGzipSha256", "proof"}
                or item["key"] != proof["fragmentKey"]
                or item["proof"]["rowDigest"] != proof["rowDigest"]
                or item["proof"]["rowCount"] != proof["rowLimit"]
                or not HEX.fullmatch(item["rowsGzipSha256"])):
            raise ValueError("slim table fragment proof differs from manifest")
        row_total += _verify_rows(item["rowsGzipBase64"],
            item["rowsNdjsonBytes"], item["rowsGzipSha256"],
            proof["rowDigest"], proof["rowLimit"], proof["columnCount"])
    if row_total != volume["rowCount"]:
        raise ValueError("slim HTML omitted rows")
    return {"schemaVersion": "business-v10-slim-html-static-check-v1",
        "syntheticOnly": True, "nativeExcelOpened": False,
        "manifestDigest": full["manifestDigest"], "htmlSha256": actual_sha,
        "volumeIndex": volume_index, "tables": len(data["tables"]), "rows": row_total,
        "allCompressedShaAndRowDigestsVerified": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html")
    parser.add_argument("manifest")
    parser.add_argument("output")
    parser.add_argument("--volume-index", type=int, default=1)
    args = parser.parse_args()
    output = Path(args.output)
    started = time.monotonic()
    tracemalloc.start()
    result = verify(args.html, args.manifest,
        volume_index=args.volume_index)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result["staticVerifySeconds"] = round(time.monotonic() - started, 3)
    result["pythonPeakTracedBytes"] = peak
    with output.open("x", encoding="utf-8") as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
    print(output)
