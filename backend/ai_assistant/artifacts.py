"""Bounded table artifacts, preserving the browser/CSV and legacy digest contract."""

import csv
import io
import json
import math
import re
from .policy import AiError, digest, record

UNSAFE = re.compile(
    r"secret|password|token|api.?key|authorization|raw|content|message|chat|transcript",
    re.I,
)
MAX_BYTES = 64 * 1024


def column(value):
    return (
        isinstance(value, str)
        and len(value) <= 64
        and re.fullmatch(r"[^\W\d][\w-]*", value)
        and not UNSAFE.search(value)
    )


def pandas_column(value):
    # Keep units in validated calculation headers (e.g. 销售额（元）), while
    # retaining sensitive-field exclusion and passive table/CSV rendering.
    return (isinstance(value, str) and 1 <= len(value) <= 64
            and re.fullmatch(r"[^\W\d][\w ()（）%/.-]*", value)
            and not UNSAFE.search(value))


def scalar(value):
    return (
        value is None
        or type(value) in {str, bool, int}
        or type(value) is float
        and math.isfinite(value)
    )


def cell(value):
    if not scalar(value):
        return None
    if isinstance(value, str):
        value = value.replace("\x00", "")
        return value if len(value) <= 240 else value[:239] + "…"
    return value


def candidate(name, data):
    if name == "describe_system_datasets":
        return None  # Discovery metadata is not a queried business table.
    if name == "query_system_dataset":
        data = data.get("data")
        if not isinstance(data, dict):
            return None
    collection = data.get("items", data.get("daily"))
    if name in {"query_system_dataset", "get_system_dataset_records"} and collection is None:
        collection = data.get("rows")
    if (
        not isinstance(collection, list)
        or not collection
        or not all(isinstance(row, dict) for row in collection[:50])
    ):
        return None
    columns = list(
        dict.fromkeys(
            k
            for row in collection[:50]
            for k, v in row.items()
            if (pandas_column(k) if name == "run_pandas_analysis" else column(k)) and scalar(v)
        )
    )
    if not columns:
        return None
    rows = [[cell(row.get(k)) for k in columns[:12]] for row in collection[:50]]
    changed = any(
        cell(v) != v
        for row in collection[:50]
        for k, v in row.items()
        if k in columns[:12]
    )
    total = data.get("totalMatched", data.get("total", len(collection)))
    result = dict(
        kind="table",
        title=name[:160],
        sourceTool=name[:64],
        columns=columns[:12],
        rows=rows,
        rowCount=max(total if type(total) is int else 0, len(collection)),
        truncated=bool(data.get("truncated") or data.get("hasMore"))
        or len(collection) > 50
        or len(columns) > 12
        or changed,
    )
    while len(encoded(result).encode()) > MAX_BYTES and result["rows"]:
        result["rows"].pop()
        result["truncated"] = True
    return result if result["rows"] else None


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def payload(row):
    if len(row.columns_json.encode()) + len(row.rows_json.encode()) > MAX_BYTES:
        raise AiError("表格产物超过大小上限", "payload_too_large", 413)
    columns, rows = json.loads(row.columns_json), json.loads(row.rows_json)
    if (
        row.kind != "table"
        or not isinstance(columns, list)
        or not 1 <= len(columns) <= 12
        or not all((pandas_column(k) if row.source_tool == "run_pandas_analysis" else column(k)) for k in columns)
        or not isinstance(rows, list)
        or len(rows) > 50
        or any(
            not isinstance(values, list)
            or len(values) != len(columns)
            or any(not scalar(v) or cell(v) != v for v in values)
            for values in rows
        )
    ):
        raise AiError("表格产物结构无效", "service_unavailable", 503)
    result = dict(
        kind="table",
        title=row.title,
        sourceTool=row.source_tool,
        columns=columns,
        rows=rows,
        rowCount=row.row_count,
        truncated=bool(row.truncated),
    )
    if content_digest(result) != row.content_digest:
        raise AiError("表格产物摘要回查失败", "service_unavailable", 503)
    return result


def public(row):
    data = payload(row)
    data.update(record(row, "id file_name content_digest created_at"))
    data.update(
        mimeType="text/csv; charset=utf-8", downloadUrl="/api/ai/artifacts/" + row.id
    )
    return data


def csv_content(row):
    data = payload(row)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")

    def safe(value):
        if value is None:
            return ""
        if isinstance(value, str):
            return "'" + value if re.match(r"^[\x01-\x20]*[=+\-@]", value) else value
        return str(value).lower() if type(value) is bool else value

    for values in [data["columns"], *data["rows"]]:
        writer.writerow([safe(v) for v in values])
    return "\ufeff" + stream.getvalue().removesuffix("\r\n")


def content_digest(data):
    return digest(encoded(data))
