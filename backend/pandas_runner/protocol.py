"""Bounded passive wire protocol shared by the application and isolated broker."""
import hashlib
import hmac
import json
import math
import re
from pathlib import Path

MAX_INPUT = 2 * 1024 * 1024
MAX_OUTPUT = 24000
MAX_ROWS = 2000
MAX_CODE = 16000
PORT = 8121
PATH = "/v1/pandas"
HEALTH_PATH = "/v1/status"


def package_digest():
    value = hashlib.sha256()
    for file in sorted(Path(__file__).parent.glob("*.py")):
        if file.name.startswith("test_"):
            continue
        value.update(file.name.encode() + b"\0" + file.read_bytes())
    return value.hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))


def signature(key, stamp, nonce, raw, direction="request", path=PATH):
    message = "\n".join(["pandas-v1", direction, "POST", path, stamp, nonce, hashlib.sha256(raw).hexdigest()])
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()


def validate_job(value):
    if not isinstance(value, dict) or set(value) != {"code", "frames"}:
        raise ValueError("invalid_job")
    if not isinstance(value["code"], str) or not 1 <= len(value["code"].encode()) <= MAX_CODE:
        raise ValueError("invalid_code")
    frames = value["frames"]
    if not isinstance(frames, dict) or not 1 <= len(frames) <= 3:
        raise ValueError("invalid_frames")
    count = 0
    for name, rows in frames.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name) or not isinstance(rows, list):
            raise ValueError("invalid_frame")
        count += len(rows)
        for row in rows:
            if not isinstance(row, dict) or len(row) > 100:
                raise ValueError("invalid_row")
            for key, cell in row.items():
                if not isinstance(key, str) or not 1 <= len(key) <= 128:
                    raise ValueError("invalid_column")
                if not (cell is None or type(cell) in {str, bool, int} or type(cell) is float and math.isfinite(cell)):
                    raise ValueError("non_scalar_cell")
    if count > MAX_ROWS or len(encode(value)) > MAX_INPUT:
        raise ValueError("input_limit")
    return value


def validate_result(value):
    if not isinstance(value, dict) or set(value) != {"rows", "columns"}:
        raise ValueError("invalid_result")
    columns, rows = value["columns"], value["rows"]
    if (not isinstance(columns, list) or not 1 <= len(columns) <= 20
            or any(not isinstance(c, str) or not 1 <= len(c) <= 64 for c in columns)
            or len(set(columns)) != len(columns) or not isinstance(rows, list) or len(rows) > 100):
        raise ValueError("output_limit")
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(columns):
            raise ValueError("invalid_result_row")
        for cell in row.values():
            if not (cell is None or type(cell) in {bool, int} or type(cell) is float and math.isfinite(cell)
                    or type(cell) is str and len(cell) <= 1000):
                raise ValueError("invalid_result_cell")
    if len(encode(value)) > MAX_OUTPUT:
        raise ValueError("output_limit")
    return value
