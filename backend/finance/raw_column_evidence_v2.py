"""Test-only persistence of untrusted Worker v2 column/cell digests.

No XLSX bytes reach this module. A successful row proves only that one Worker
candidate matched the *current normalized* finance month and batch. It never
proves raw-file provenance, a stable netshop shop, or report authority.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction

from . import business_analysis_source as owner, import_service
from .errors import FinanceApiError
from .models import (FinanceDataRevision, FinanceImportBatch, FinanceLine,
    FinanceMonth, FinanceRawEvidenceCell, FinanceRawEvidenceColumn,
    FinanceRawEvidenceMonth)


SCHEMA = "finance-raw-column-evidence-owning-candidate-v1"
MAX_COLUMNS = 500
MAX_CELLS = 100_000
MAX_CANDIDATE_BYTES = 32 * 1024 * 1024
MAX_PERSISTED_DIGEST_BYTES = 16 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_LINE_MAP = {"month": "month", "section": "section", "metricKey": "metric_key",
    "subjectName": "subject_name", "scopeKey": "scope_key",
    "scopeType": "scope_type", "scopeName": "scope_name",
    "groupName": "group_name", "valueType": "value_type",
    "amountCents": "amount_cents", "rateBps": "rate_bps",
    "rawValue": "raw_value", "sourceRowCount": "source_row_count",
    "sortOrder": "sort_order", "isTotal": "is_total"}


def _deny(message="财报原始列候选不完整或当前来源已变化"):
    raise FinanceApiError(message, status=409,
        code="finance_raw_evidence_unavailable")


def _need(ok, message="财报原始列候选不完整或当前来源已变化"):
    if not ok:
        _deny(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=False,
        separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _sha(value):
    _need(type(value) is str and _SHA.fullmatch(value) is not None)


def _closed(enabled):
    _need(enabled is True and settings.DJANGO_ENVIRONMENT == "test"
        and settings.DJANGO_PROCESS_ROLE == "development",
        "财报原始列候选仅在隔离测试显式启用")


def _shape(candidate):
    _need(type(candidate) is dict and set(candidate) == {
        "schemaVersion", "fileName", "fileSizeBytes", "rawFileHash",
        "rawFileHashVerifiedByBackend", "completeWorkbookBindingVerified",
        "financeShopMappingVerified", "backendImportSupported",
        "disposition", "warnings", "sourceSheetCount", "months",
        "columnEvidence", "candidateDigest"})
    _need(candidate["schemaVersion"] == "finance-normalized-v2-candidate"
        and candidate["disposition"] == "candidate_only"
        and all(candidate[key] is False for key in (
            "rawFileHashVerifiedByBackend", "completeWorkbookBindingVerified",
            "financeShopMappingVerified", "backendImportSupported")))
    _need(type(candidate["fileSizeBytes"]) is int
        and 0 < candidate["fileSizeBytes"] <= 8 * 1024 * 1024)
    _sha(candidate["rawFileHash"]); _sha(candidate["candidateDigest"])
    _need(type(candidate["months"]) is list
        and type(candidate["columnEvidence"]) is list
        and len(candidate["months"]) == len(candidate["columnEvidence"]) == 1,
        "首版只接受完整单月Worker候选，不拆分多月批次")
    try:
        encoded = _json({key: value for key, value in candidate.items()
            if key != "candidateDigest"}).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise FinanceApiError("财报候选无法规范编码", status=422,
            code="invalid_finance_raw_evidence") from error
    _need(len(encoded) <= MAX_CANDIDATE_BYTES
        and hashlib.sha256(encoded).hexdigest() == candidate["candidateDigest"],
        "Worker候选字节或摘要变化")
    legacy = {"schemaVersion": "finance-normalized-v1",
        "disposition": "prepared", "fileName": candidate["fileName"],
        "fileSizeBytes": candidate["fileSizeBytes"],
        "rawFileHash": candidate["rawFileHash"],
        "warnings": candidate["warnings"],
        "sourceSheetCount": candidate["sourceSheetCount"],
        "months": candidate["months"]}
    normalized = import_service.validate_import_payload(legacy)
    month = normalized["months"][0]
    evidence = candidate["columnEvidence"][0]
    _need(type(evidence) is dict and set(evidence) == {
        "schemaVersion", "month", "sheetName", "headerCells", "columns",
        "cells", "collisions", "columnCount", "cellCount",
        "crossGroupSameNameRisk", "sourceWorkbookBytesVerified",
        "financeShopMappingVerified", "candidateOnly", "evidenceDigest"}
        and evidence["schemaVersion"] ==
            "finance-column-cell-evidence-v2-candidate"
        and evidence["month"] == month["month"]
        and evidence["sheetName"] == month["sheetName"]
        and evidence["sourceWorkbookBytesVerified"] is False
        and evidence["financeShopMappingVerified"] is False
        and evidence["candidateOnly"] is True)
    _sha(evidence["evidenceDigest"])
    _need(evidence["evidenceDigest"] == _hash({key: value
        for key, value in evidence.items() if key != "evidenceDigest"}))
    return normalized, month, evidence


def _evidence_rows(evidence):
    headers, columns, cells = (evidence[key] for key in
        ("headerCells", "columns", "cells"))
    _need(type(headers) is list and type(columns) is list and type(cells) is list
        and 2 <= len(columns) <= MAX_COLUMNS
        and 1 <= len(cells) <= MAX_CELLS
        and evidence["columnCount"] == len(columns)
        and evidence["cellCount"] == len(cells))
    _need(type(evidence["collisions"]) is list
        and len(evidence["collisions"]) <= MAX_COLUMNS)
    indices, persisted_columns, bytes_used = set(), [], 0
    by_column = {}
    normalized_headers = {}
    for header in headers:
        _need(type(header) is dict and set(header) == {
            "columnIndex", "rawGroupCell", "rawShopCell"}
            and type(header["columnIndex"]) is int
            and 1 <= header["columnIndex"] <= 10_000
            and header["columnIndex"] not in normalized_headers
            and all(header[key] is None or type(header[key]) is str
                and len(header[key]) <= 1_000
                for key in ("rawGroupCell", "rawShopCell")))
        normalized_headers[header["columnIndex"]] = header
    for column in columns:
        _need(type(column) is dict and set(column) == {"columnIndex",
            "scopeKey", "scopeType", "scopeName", "groupName",
            "rawGroupCell", "rawShopCell"})
        index = column["columnIndex"]
        _need(type(index) is int and 1 <= index <= 10_000
            and index not in indices
            and column["scopeType"] in {"business", "group", "shop"}
            and all(type(column[key]) is str and len(column[key]) <= limit
                for key, limit in (("scopeKey", 2_000),
                    ("scopeName", 1_000), ("groupName", 1_000)))
            and type(column["rawGroupCell"]) in (str, type(None))
            and type(column["rawShopCell"]) in (str, type(None)))
        indices.add(index)
        by_column[index] = column
        header = normalized_headers.get(index)
        _need(header is not None
            and header["rawGroupCell"] == column["rawGroupCell"]
            and header["rawShopCell"] == column["rawShopCell"])
        cell = (index, _hash(column))
        persisted_columns.append(cell)
        bytes_used += len(_json(cell).encode("utf-8"))
        _need(bytes_used <= MAX_PERSISTED_DIGEST_BYTES)
    _need(set(normalized_headers) == set(range(1, max(indices) + 1)))
    _need(min(indices) == 1 and by_column[1]["scopeType"] == "business")
    effective_group = ""
    for index in range(1, max(indices) + 1):
        header = normalized_headers[index]
        current = re.sub(r"[\s　]+", "", (header["rawGroupCell"] or "").strip())
        if index > 1 and current:
            effective_group = current
        column = by_column.get(index)
        if column is None:
            continue
        raw_shop = re.sub(r"[\s　]+", "", (header["rawShopCell"] or "").strip())
        if column["scopeType"] == "shop":
            _need(raw_shop == column["scopeName"])
        elif column["scopeType"] == "group":
            _need(raw_shop == "组汇总")
        if column["scopeType"] != "business":
            _need(effective_group == column["groupName"],
                "财报原始组名继承与列归属不一致")
    seen_cells, persisted_cells = set(), []
    grouped = {}
    for cell in cells:
        _need(type(cell) is dict and set(cell) == {"section", "rowIndex",
            "columnIndex", "subjectName", "metricKey", "scopeKey",
            "valueType", "amountCents", "rateBps", "rawValue", "isTotal"})
        index, row = cell["columnIndex"], cell["rowIndex"]
        _need(type(index) is int and index in by_column
            and type(row) is int and 1 <= row <= 1_000_000
            and cell["section"] in {"summary", "kingdee"}
            and type(cell["subjectName"]) is str
            and 1 <= len(cell["subjectName"]) <= 2_000
            and type(cell["metricKey"]) is str
            and len(cell["metricKey"]) <= 500
            and cell["scopeKey"] == by_column[index]["scopeKey"]
            and cell["valueType"] in {"amount", "rate", "number", "text"}
            and type(cell["rawValue"]) is str
            and len(cell["rawValue"]) <= 4_000
            and type(cell["isTotal"]) is bool
            and all(value is None or type(value) is int
                and abs(value) <= 9_007_199_254_740_991 for value in (
                cell["amountCents"], cell["rateBps"]))
            and (cell["amountCents"] is None or cell["valueType"] == "amount")
            and (cell["rateBps"] is None or cell["valueType"] == "rate"))
        identity = (cell["section"], row, index)
        _need(identity not in seen_cells)
        seen_cells.add(identity)
        record = (*identity, _hash(cell))
        persisted_cells.append(record)
        bytes_used += len(_json(record).encode("utf-8"))
        _need(bytes_used <= MAX_PERSISTED_DIGEST_BYTES)
        key = (cell["section"], cell["scopeKey"], cell["subjectName"])
        item = grouped.get(key)
        if item is None:
            item = {"first": cell, "column": by_column[index],
                "count": 0, "sortOrder": row + 1, "isTotal": False,
                "amountCents": None, "rateBps": None}
            grouped[key] = item
        item["count"] += 1
        item["sortOrder"] = min(item["sortOrder"], row + 1)
        item["isTotal"] |= cell["isTotal"]
        for metric in ("amountCents", "rateBps"):
            if cell[metric] is not None:
                item[metric] = (item[metric] or 0) + cell[metric]
                _need(abs(item[metric]) <= 9_007_199_254_740_991)
    persisted_columns.sort()
    persisted_cells.sort()
    return persisted_columns, persisted_cells, grouped


def _verify_aggregate(month, grouped, evidence):
    lines = {(line["section"], line["scopeKey"], line["subjectName"]): line
        for line in month["lines"]}
    _need(set(lines) == set(grouped), "原始来源格与完整已发布科目身份不守恒")
    for key, item in grouped.items():
        line, cell, column = lines[key], item["first"], item["column"]
        expected_amount = item["amountCents"]
        expected_raw = cell["rawValue"]
        if key[0] == "kingdee" and key[2] == "销售费用":
            expected_amount = sum((part["amountCents"] or 0)
                for other, part in grouped.items() if other[0] == "kingdee"
                and other[1] == key[1] and other[2].startswith("销售费用_")
                and part["isTotal"] is False)
            # The legacy parser rewrites the Kingdee parent after aggregation;
            # all other rows retain the first source cell's raw text.
            expected_raw = format((Decimal(expected_amount) / 100).normalize(),
                "f")
        _need(line["month"] == month["month"]
            and line["metricKey"] == cell["metricKey"]
            and line["scopeType"] == column["scopeType"]
            and line["scopeName"] == column["scopeName"]
            and line["groupName"] == column["groupName"]
            and line["valueType"] == cell["valueType"]
            and line["amountCents"] == expected_amount
            and line["rateBps"] == item["rateBps"]
            and line["rawValue"] == expected_raw
            and line["sourceRowCount"] == item["count"]
            and line["sortOrder"] == item["sortOrder"]
            and line["isTotal"] == item["isTotal"],
            "来源格不能逐项回卷当前财报规范事实")
    # The Worker collision list is a claim. Recompute the risk independently.
    by_scope = defaultdict(list)
    for column in evidence["columns"]:
        if column["scopeType"] == "shop":
            by_scope[column["scopeKey"]].append(column)
    expected = {}
    for scope_key, columns in by_scope.items():
        if len(columns) <= 1:
            continue
        indices = sorted(item["columnIndex"] for item in columns)
        groups = sorted({item["groupName"] for item in columns})
        names = sorted({item["scopeName"] for item in columns})
        shared = defaultdict(set)
        for cell in evidence["cells"]:
            if cell["columnIndex"] in indices:
                shared[(cell["section"], cell["subjectName"])].add(
                    cell["columnIndex"])
        expected[scope_key] = {"legacyScopeKey": scope_key,
            "columnIndices": indices, "groupNames": groups,
            "shopNames": names, "kind": "cross_group_same_name"
                if len(groups) > 1 else "same_scope_key_multiple_names"
                if len(names) > 1 else "duplicate_shop_columns",
            "actualLegacyMergedSubjectCount": sum(len(value) > 1
                for value in shared.values())}
    actual = {item["legacyScopeKey"]: item for item in
        evidence["collisions"] if type(item) is dict
        and "legacyScopeKey" in item}
    _need(len(actual) == len(evidence["collisions"])
        and actual == expected
        and evidence["crossGroupSameNameRisk"] is
            any(item["kind"] == "cross_group_same_name"
                for item in expected.values()),
        "跨组同名合并风险未与原始列一致披露")
    return sorted({item["kind"] for item in expected.values()})


def _current_lines(month):
    values = list(FinanceLine.objects.filter(month=month)
        .values(*_LINE_MAP.values()).order_by("id")[:MAX_CELLS + 1])
    _need(len(values) <= MAX_CELLS)
    return [{name: row[field] for name, field in _LINE_MAP.items()}
        for row in values]


def _same_published(month, lines):
    return sorted((_json(item) for item in month["lines"])) == sorted(
        (_json(item) for item in lines))


def _receipt(row, *, replay=False):
    return {"schemaVersion": SCHEMA, "month": row.month,
        "batchId": row.batch_id, "candidateDigest": row.candidate_digest,
        "evidenceDigest": row.evidence_digest,
        "columnCount": row.column_count, "cellCount": row.cell_count,
        "crossGroupSameNameRisk": row.cross_group_same_name_risk,
        "ambiguityFlags": list(row.ambiguity_flags_json),
        "idempotentReplay": replay,
        "singleMonthBatchOnly": True,
        "rawWorkbookBytesIndependentlyVerified": False,
        "stableNetshopShopIdentityVerified": False,
        "mappingAuthorityVerified": False,
        "candidateOnly": True}


def stage(principal, candidate, *, enabled=False):
    """Atomically append complete digest rows for one current single-month batch."""
    _closed(enabled)
    normalized, month, evidence = _shape(candidate)
    columns, cells, grouped = _evidence_rows(evidence)
    flags = _verify_aggregate(month, grouped, evidence)
    try:
        with transaction.atomic():
            actor = owner._actor(principal)
            published = FinanceMonth.objects.select_for_update().filter(
                month=month["month"]).first()
            _need(published is not None and published.status == "completed")
            batch = FinanceImportBatch.objects.select_for_update().filter(
                pk=published.batch_id).first()
            revision = FinanceDataRevision.objects.select_for_update().filter(
                domain="finance").first()
            _need(batch is not None and revision is not None
                and batch.status == "completed"
                and batch.months_json == [month["month"]]
                and batch.raw_file_hash == candidate["rawFileHash"]
                and batch.file_size_bytes == candidate["fileSizeBytes"]
                and batch.content_hash == import_service._fingerprint(
                    normalized["months"])[1]
                and batch.row_count == len(month["lines"])
                and type(batch.published_state_token) is str
                and _SHA.fullmatch(batch.published_state_token) is not None
                and published.sheet_name == month["sheetName"]
                and published.source_file_name == candidate["fileName"])
            before_lines = _current_lines(month["month"])
            _need(_same_published(month, before_lines))
            identity = hashlib.sha256(_json(["finance-raw-v2", month["month"],
                batch.id]).encode("utf-8")).hexdigest()
            existing = FinanceRawEvidenceMonth.objects.filter(
                month=month["month"], batch=batch).first()
            if existing is not None:
                _need(existing.candidate_digest == candidate["candidateDigest"]
                    and existing.evidence_digest == evidence["evidenceDigest"]
                    and existing.finance_revision == revision.revision
                    and existing.finance_source_digest == revision.source_digest)
                read(principal, month["month"], enabled=True)
                return _receipt(existing, replay=True)
            row = FinanceRawEvidenceMonth.objects.create(id=identity,
                month=month["month"], batch=batch,
                finance_revision=revision.revision,
                finance_source_digest=revision.source_digest,
                raw_file_hash=batch.raw_file_hash,
                batch_content_hash=batch.content_hash,
                batch_published_state_token=batch.published_state_token,
                candidate_digest=candidate["candidateDigest"],
                evidence_digest=evidence["evidenceDigest"],
                header_digest=_hash(evidence["headerCells"]),
                cells_digest=_hash(evidence["cells"]),
                column_chain_digest=_hash(columns),
                cell_chain_digest=_hash(cells),
                collision_digest=_hash(evidence["collisions"]),
                column_count=len(columns), cell_count=len(cells),
                cross_group_same_name_risk=evidence["crossGroupSameNameRisk"],
                ambiguity_flags_json=flags)
            FinanceRawEvidenceColumn.objects.bulk_create([
                FinanceRawEvidenceColumn(evidence=row, column_index=index,
                    column_digest=digest) for index, digest in columns],
                batch_size=500)
            FinanceRawEvidenceCell.objects.bulk_create([
                FinanceRawEvidenceCell(evidence=row, section=section,
                    row_index=row_index, column_index=column_index,
                    cell_digest=digest)
                for section, row_index, column_index, digest in cells],
                batch_size=500)
            _need(owner._actor(principal) == actor
                and owner._revision() == {"revision": revision.revision,
                    "source_digest": revision.source_digest}
                and FinanceMonth.objects.get(pk=month["month"]).batch_id == batch.id
                and _same_published(month, _current_lines(month["month"])))
            return _receipt(row)
    except IntegrityError as error:
        raise FinanceApiError("财报原始列候选并发身份冲突", status=409,
            code="finance_raw_evidence_conflict") from error


def read(principal, month, *, enabled=False):
    """Read one candidate only while its current batch and revision still match."""
    _closed(enabled)
    with transaction.atomic():
        return _read_locked(principal, month)


def _read_locked(principal, month):
    _need(type(month) is str and re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", month))
    actor = owner._actor(principal)
    revision = owner._revision()
    published = FinanceMonth.objects.filter(pk=month).first()
    _need(published is not None and published.status == "completed")
    batch = FinanceImportBatch.objects.filter(pk=published.batch_id).first()
    row = FinanceRawEvidenceMonth.objects.select_for_update().filter(month=month,
        batch_id=published.batch_id).first()
    _need(batch is not None and row is not None
        and batch.status == "completed"
        and row.finance_revision == revision["revision"]
        and row.finance_source_digest == revision["source_digest"]
        and row.raw_file_hash == batch.raw_file_hash
        and row.batch_content_hash == batch.content_hash
        and row.batch_published_state_token == batch.published_state_token
        and row.column_count == FinanceRawEvidenceColumn.objects.filter(
            evidence=row).count()
        and row.cell_count == FinanceRawEvidenceCell.objects.filter(
            evidence=row).count())
    columns = list(FinanceRawEvidenceColumn.objects.filter(evidence=row)
        .order_by("column_index").values_list("column_index", "column_digest"))
    cells = list(FinanceRawEvidenceCell.objects.filter(evidence=row)
        .order_by("section", "row_index", "column_index")
        .values_list("section", "row_index", "column_index", "cell_digest"))
    _need(_hash(columns) == row.column_chain_digest
        and _hash(cells) == row.cell_chain_digest
        and owner._actor(principal) == actor
        and owner._revision() == revision
        and FinanceMonth.objects.get(pk=month).batch_id == batch.id)
    return _receipt(row)
