"""Backend-owned physical column/cell evidence from independently read XLSX."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json

from .workbook_bytes_v2 import _need


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=False,
        separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def build_candidate(parsed, file_name: str):
    """Build the 0005-compatible structure without trusting Worker JSON.

    The 0005 compatibility flags stay false by its DB contract. A separate
    0006 row, after current-batch checks, is the byte observation authority.
    """
    source = parsed["evidenceInput"]
    month = parsed["month"]
    _need(type(file_name) is str and 0 < len(file_name) <= 255)
    dimensions = source["dimensions"]
    headers = source["headerCells"]
    _need(2 <= len(dimensions) <= 500 and len(headers) <= 500)
    by_header = {item["columnIndex"]: item for item in headers}
    _need(len(by_header) == len(headers))
    columns = []
    for dimension in dimensions:
        header = by_header.get(dimension["columnIndex"])
        _need(header is not None)
        columns.append({"columnIndex": dimension["columnIndex"],
            "scopeKey": dimension["scopeKey"],
            "scopeType": dimension["scopeType"],
            "scopeName": dimension["scopeName"],
            "groupName": dimension["groupName"],
            "rawGroupCell": header["rawGroupCell"],
            "rawShopCell": header["rawShopCell"]})
    columns.sort(key=lambda item: item["columnIndex"])
    cells = []
    for line, origin in zip(source["rawLines"], source["origins"],
            strict=True):
        cells.append({"section": line["section"],
            "rowIndex": origin["rowIndex"],
            "columnIndex": origin["columnIndex"],
            "subjectName": line["subjectName"],
            "metricKey": line["metricKey"],
            "scopeKey": line["scopeKey"],
            "valueType": line["valueType"],
            "amountCents": line["amountCents"],
            "rateBps": line["rateBps"],
            "rawValue": line["rawValue"],
            "isTotal": line["isTotal"]})
    _need(0 < len(cells) <= 100_000)
    cells.sort(key=lambda item: (item["rowIndex"],
        item["columnIndex"], item["section"]))
    by_scope = defaultdict(list)
    for column in columns:
        if column["scopeType"] == "shop":
            by_scope[column["scopeKey"]].append(column)
    collisions = []
    for scope_key, found in sorted(by_scope.items()):
        if len(found) < 2:
            continue
        member_indices = sorted(item["columnIndex"] for item in found)
        groups = sorted({item["groupName"] for item in found})
        names = sorted({item["scopeName"] for item in found})
        grouped = defaultdict(set)
        for cell in cells:
            if cell["columnIndex"] in member_indices:
                grouped[(cell["section"], cell["subjectName"])].add(
                    cell["columnIndex"])
        collisions.append({"legacyScopeKey": scope_key,
            "columnIndices": member_indices, "groupNames": groups,
            "shopNames": names,
            "kind": "cross_group_same_name" if len(groups) > 1 else
                "same_scope_key_multiple_names" if len(names) > 1 else
                "duplicate_shop_columns",
            "actualLegacyMergedSubjectCount": sum(len(group) > 1
                for group in grouped.values())})
    evidence = {"schemaVersion": "finance-column-cell-evidence-v2-candidate",
        "month": month["month"], "sheetName": month["sheetName"],
        "headerCells": headers, "columns": columns, "cells": cells,
        "collisions": collisions, "columnCount": len(columns),
        "cellCount": len(cells),
        "crossGroupSameNameRisk": any(item["kind"] ==
            "cross_group_same_name" for item in collisions),
        "sourceWorkbookBytesVerified": False,
        "financeShopMappingVerified": False, "candidateOnly": True}
    evidence = {**evidence, "evidenceDigest": _hash(evidence)}
    candidate = {"schemaVersion": "finance-normalized-v2-candidate",
        "fileName": file_name, "fileSizeBytes": parsed["fileSizeBytes"],
        "rawFileHash": parsed["rawFileHash"],
        "rawFileHashVerifiedByBackend": False,
        "completeWorkbookBindingVerified": False,
        "financeShopMappingVerified": False,
        "backendImportSupported": False,
        "disposition": "candidate_only", "warnings": [],
        "sourceSheetCount": parsed["sourceSheetCount"],
        "months": [month], "columnEvidence": [evidence]}
    return {**candidate, "candidateDigest": _hash(candidate)}
