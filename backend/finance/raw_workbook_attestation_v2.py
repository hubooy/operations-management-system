"""Closed owning append of backend-observed XLSX bytes and distinct cells.

The old finance-normalized-v1 import is never called or rewritten here. The
first version accepts only a completed single-month batch already published
from exactly these bytes. Production needs a separate narrow attestor role and
formal backup/restore approval before this owner can be enabled.
"""
from __future__ import annotations

import hashlib

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F

from . import (business_analysis_source as owner,
    import_service, raw_column_evidence_v2 as old,
    workbook_bytes_v2 as workbook,
    workbook_column_evidence_v2 as evidence_owner)
from .errors import FinanceApiError
from .models import (FinanceDataRevision, FinanceImportBatch, FinanceLine,
    FinanceMonth, FinanceRawEvidenceCell, FinanceRawEvidenceColumn,
    FinanceRawEvidenceMonth, FinanceRawWorkbookAttestation,
    FinanceRawWorkbookColumn, FinanceRawWorkbookCell)


SCHEMA = "finance-raw-workbook-byte-attestation-v1"
PARSER_CONTRACT = "finance-strict-ooxml-single-month-v1"


def _need(ok, message="财报原始工作簿与当前已发布月份不一致"):
    if not ok:
        raise FinanceApiError(message, status=409,
            code="finance_raw_workbook_unavailable")


def _closed(enabled):
    _need(enabled is True and settings.DJANGO_ENVIRONMENT == "test"
        and settings.DJANGO_PROCESS_ROLE == "development",
        "财报原始字节侧车仅在隔离测试显式启用")


def _sha_text(value: str):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cell_records(candidate, physical_columns, physical_cells):
    evidence = candidate["columnEvidence"][0]
    columns_by_index = {item["columnIndex"]: item for item in
        evidence["columns"]}
    column_digests = dict(physical_columns)
    cell_digests = {(section, row, column): digest for section, row,
        column, digest in physical_cells}
    column_rows = []
    for column in evidence["columns"]:
        index = column["columnIndex"]
        column_rows.append((index, column["scopeKey"], column["scopeType"],
            column["scopeName"], column["groupName"],
            old._hash([column["rawGroupCell"], column["rawShopCell"]]),
            column_digests[index]))
    numeric_rows = []
    for cell in evidence["cells"]:
        key = (cell["section"], cell["rowIndex"], cell["columnIndex"])
        column = columns_by_index[cell["columnIndex"]]
        numeric_rows.append((*key, cell["subjectName"], cell["metricKey"],
            cell["valueType"], cell["amountCents"], cell["rateBps"],
            _sha_text(cell["rawValue"]), cell_digests[key],
            cell["isTotal"], column["groupName"]))
    column_rows.sort()
    numeric_rows.sort()
    return column_rows, numeric_rows


def _receipt(row, *, replay=False):
    return {"schemaVersion": SCHEMA, "month": row.month,
        "batchId": row.batch_id, "rawFileSha256": row.raw_file_sha256,
        "evidenceDigest": row.evidence.evidence_digest,
        "columnCount": row.column_count, "cellCount": row.cell_count,
        "crossGroupSameNameRisk": row.cross_group_same_name_risk,
        "idempotentReplay": replay,
        "backendRawBytesObserved": True,
        "rawWorkbookBytesRetained": False,
        "distinctPhysicalColumnsPreserved": True,
        "legacyNormalizedLinesUnchanged": True,
        "singleMonthXlsxOnly": True,
        "stableNetshopShopIdentityVerified": False,
        "mappingAuthorityVerified": False,
        "reportAuthorityVerified": False}


def _children(row):
    column_scope = FinanceRawWorkbookColumn.objects.filter(attestation=row)
    cell_scope = FinanceRawWorkbookCell.objects.filter(attestation=row)
    _need(not column_scope.exclude(evidence_column__evidence_id=
        row.evidence_id).exists()
        and not column_scope.exclude(column_index=F(
            "evidence_column__column_index")).exists()
        and not column_scope.exclude(source_column_digest=F(
            "evidence_column__column_digest")).exists()
        and not cell_scope.exclude(evidence_cell__evidence_id=
            row.evidence_id).exists()
        and not cell_scope.exclude(column__attestation_id=row.id).exists()
        and not cell_scope.exclude(column_index=F(
            "column__column_index")).exists()
        and not cell_scope.exclude(column_index=F(
            "evidence_cell__column_index")).exists()
        and not cell_scope.exclude(row_index=F(
            "evidence_cell__row_index")).exists()
        and not cell_scope.exclude(section=F(
            "evidence_cell__section")).exists()
        and not cell_scope.exclude(source_cell_digest=F(
            "evidence_cell__cell_digest")).exists(),
        "财报原字节侧车与0005物理列格跨月或坐标错配")
    columns = list(FinanceRawWorkbookColumn.objects.filter(attestation=row)
        .order_by("column_index").values_list("column_index", "scope_key",
            "scope_type", "scope_name", "group_name", "header_digest",
            "source_column_digest"))
    cells = list(FinanceRawWorkbookCell.objects.filter(attestation=row)
        .order_by("section", "row_index", "column_index").values_list(
            "section", "row_index", "column_index", "subject_name",
            "metric_key", "value_type", "amount_cents", "rate_bps",
            "raw_value_digest", "source_cell_digest", "is_total",
            "column__group_name"))
    _need(len(columns) == row.column_count and len(cells) == row.cell_count
        and old._hash(columns) == row.column_chain_digest
        and old._hash(cells) == row.cell_chain_digest,
        "财报原字节侧车列格链已变化")
    return columns, cells


def stage(principal, raw_bytes: bytes, month: str, batch_id: str,
          expected_state_token: str | None = None, *, enabled=False):
    """Parse before DB locks, then atomically add 0005+0006 exact rows."""
    _closed(enabled)
    _need(type(month) is str and type(batch_id) is str
        and (expected_state_token is None or
            type(expected_state_token) is str))
    try:
        parsed = workbook.parse_single_month_xlsx(raw_bytes)
    except workbook.WorkbookBytesError as error:
        raise FinanceApiError(str(error), status=422,
            code="invalid_finance_workbook_bytes") from error
    _need(parsed["month"]["month"] == month)
    try:
        with transaction.atomic():
            actor = owner._actor(principal)
            published = FinanceMonth.objects.select_for_update().filter(
                pk=month).first()
            _need(published is not None and published.status == "completed"
                and published.batch_id == batch_id)
            batch = FinanceImportBatch.objects.select_for_update().filter(
                pk=batch_id).first()
            revision = FinanceDataRevision.objects.select_for_update().filter(
                domain="finance").first()
            _need(batch is not None and revision is not None
                and batch.status == "completed"
                and batch.months_json == [month]
                and batch.raw_file_hash == parsed["rawFileHash"]
                and batch.file_size_bytes == parsed["fileSizeBytes"]
                and (expected_state_token is None or
                    batch.published_state_token == expected_state_token)
                and revision.source_digest == batch.published_state_token
                and published.sheet_name == parsed["month"]["sheetName"]
                and published.source_file_name == batch.file_name)
            candidate = evidence_owner.build_candidate(parsed, batch.file_name)
            normalized, normalized_month, evidence = old._shape(candidate)
            physical_columns, physical_cells, grouped = old._evidence_rows(
                evidence)
            flags = old._verify_aggregate(normalized_month, grouped, evidence)
            _need(batch.content_hash == import_service._fingerprint(
                normalized["months"])[1]
                and batch.row_count == len(normalized_month["lines"])
                and old._same_published(normalized_month,
                    old._current_lines(month)))
            old_id = hashlib.sha256(old._json(["finance-raw-v2", month,
                batch.id]).encode("utf-8")).hexdigest()
            original = FinanceRawEvidenceMonth.objects.filter(
                month=month, batch=batch).first()
            if original is None:
                original = FinanceRawEvidenceMonth.objects.create(id=old_id,
                    month=month, batch=batch,
                    finance_revision=revision.revision,
                    finance_source_digest=revision.source_digest,
                    raw_file_hash=batch.raw_file_hash,
                    batch_content_hash=batch.content_hash,
                    batch_published_state_token=batch.published_state_token,
                    candidate_digest=candidate["candidateDigest"],
                    evidence_digest=evidence["evidenceDigest"],
                    header_digest=old._hash(evidence["headerCells"]),
                    cells_digest=old._hash(evidence["cells"]),
                    column_chain_digest=old._hash(physical_columns),
                    cell_chain_digest=old._hash(physical_cells),
                    collision_digest=old._hash(evidence["collisions"]),
                    column_count=len(physical_columns),
                    cell_count=len(physical_cells),
                    cross_group_same_name_risk=evidence[
                        "crossGroupSameNameRisk"],
                    ambiguity_flags_json=flags)
                FinanceRawEvidenceColumn.objects.bulk_create([
                    FinanceRawEvidenceColumn(evidence=original,
                        column_index=index, column_digest=digest)
                    for index, digest in physical_columns], batch_size=500)
                FinanceRawEvidenceCell.objects.bulk_create([
                    FinanceRawEvidenceCell(evidence=original,
                        section=section, row_index=row_index,
                        column_index=column_index, cell_digest=digest)
                    for section, row_index, column_index, digest in
                        physical_cells], batch_size=500)
            else:
                _need(original.id == old_id
                    and original.finance_revision == revision.revision
                    and original.finance_source_digest == revision.source_digest
                    and original.raw_file_hash == batch.raw_file_hash
                    and original.batch_content_hash == batch.content_hash
                    and original.batch_published_state_token ==
                        batch.published_state_token
                    and original.candidate_digest == candidate[
                        "candidateDigest"]
                    and original.evidence_digest == evidence[
                        "evidenceDigest"]
                    and original.header_digest == old._hash(evidence[
                        "headerCells"])
                    and original.cells_digest == old._hash(evidence["cells"])
                    and original.column_chain_digest == old._hash(
                        physical_columns)
                    and original.cell_chain_digest == old._hash(physical_cells)
                    and original.collision_digest == old._hash(evidence[
                        "collisions"])
                    and original.cross_group_same_name_risk == evidence[
                        "crossGroupSameNameRisk"]
                    and original.ambiguity_flags_json == flags)
                _need(list(FinanceRawEvidenceColumn.objects.filter(
                    evidence=original).order_by("column_index").values_list(
                    "column_index", "column_digest")) == physical_columns
                    and list(FinanceRawEvidenceCell.objects.filter(
                    evidence=original).order_by("section", "row_index",
                    "column_index").values_list("section", "row_index",
                    "column_index", "cell_digest")) == physical_cells,
                    "已有0005列格与独立字节解析不一致")
            column_rows, cell_rows = _cell_records(candidate,
                physical_columns, physical_cells)
            attestation_id = hashlib.sha256(old._json([
                "finance-workbook-bytes-v2", month, batch.id,
                parsed["rawFileHash"]]).encode("utf-8")).hexdigest()
            existing = FinanceRawWorkbookAttestation.objects.select_for_update(
                ).filter(evidence=original).first()
            if existing is not None:
                _need(existing.id == attestation_id
                    and existing.month == month and existing.batch_id == batch.id
                    and existing.source_sheet_count == parsed[
                        "sourceSheetCount"]
                    and existing.column_count == len(column_rows)
                    and existing.cell_count == len(cell_rows)
                    and existing.finance_revision == revision.revision
                    and existing.finance_source_digest == revision.source_digest
                    and existing.batch_content_hash == batch.content_hash
                    and existing.batch_published_state_token ==
                        batch.published_state_token
                    and existing.raw_file_sha256 == parsed["rawFileHash"]
                    and existing.sheet_manifest_digest == old._hash(parsed[
                        "sheetManifest"])
                    and existing.parser_contract_digest == _sha_text(
                        PARSER_CONTRACT)
                    and existing.column_chain_digest == old._hash(column_rows)
                    and existing.cell_chain_digest == old._hash(cell_rows)
                    and existing.cross_group_same_name_risk == evidence[
                        "crossGroupSameNameRisk"])
                _children(existing)
                _need(owner._actor(principal) == actor
                    and owner._revision() == {"revision": revision.revision,
                        "source_digest": revision.source_digest}
                    and FinanceMonth.objects.get(pk=month).batch_id ==
                        batch.id
                    and old._same_published(normalized_month,
                        old._current_lines(month)))
                return _receipt(existing, replay=True)
            attestation = FinanceRawWorkbookAttestation.objects.create(
                id=attestation_id, evidence=original, month=month,
                batch=batch, finance_revision=revision.revision,
                finance_source_digest=revision.source_digest,
                batch_content_hash=batch.content_hash,
                batch_published_state_token=batch.published_state_token,
                raw_file_sha256=parsed["rawFileHash"],
                source_sheet_count=parsed["sourceSheetCount"],
                sheet_manifest_digest=old._hash(parsed["sheetManifest"]),
                parser_contract_digest=_sha_text(PARSER_CONTRACT),
                column_chain_digest=old._hash(column_rows),
                cell_chain_digest=old._hash(cell_rows),
                column_count=len(column_rows), cell_count=len(cell_rows),
                cross_group_same_name_risk=evidence[
                    "crossGroupSameNameRisk"])
            evidence_columns = {row.column_index: row for row in
                FinanceRawEvidenceColumn.objects.filter(evidence=original)}
            evidence_cells = {(row.section, row.row_index,
                row.column_index): row for row in
                FinanceRawEvidenceCell.objects.filter(evidence=original)}
            columns = {}
            for (index, scope_key, scope_type, scope_name, group_name,
                    header, source_digest) in column_rows:
                _need(index in evidence_columns)
                columns[index] = FinanceRawWorkbookColumn.objects.create(
                    attestation=attestation,
                    evidence_column=evidence_columns[index],
                    column_index=index, scope_key=scope_key,
                    scope_type=scope_type, scope_name=scope_name,
                    group_name=group_name, header_digest=header,
                    source_column_digest=source_digest)
            FinanceRawWorkbookCell.objects.bulk_create([
                FinanceRawWorkbookCell(attestation=attestation,
                    column=columns[index],
                    evidence_cell=evidence_cells[(section, row, index)],
                    section=section, row_index=row, column_index=index,
                    subject_name=subject, metric_key=metric,
                    value_type=value_type, amount_cents=amount,
                    rate_bps=rate, raw_value_digest=raw_digest,
                    source_cell_digest=source_digest, is_total=is_total)
                for section, row, index, subject, metric, value_type,
                    amount, rate, raw_digest, source_digest, is_total,
                    _group in cell_rows], batch_size=500)
            _need(owner._actor(principal) == actor
                and owner._revision() == {"revision": revision.revision,
                    "source_digest": revision.source_digest}
                and FinanceMonth.objects.get(pk=month).batch_id == batch.id
                and old._same_published(normalized_month,
                    old._current_lines(month)))
            _children(attestation)
            return _receipt(attestation)
    except IntegrityError as error:
        raise FinanceApiError("财报字节侧车并发身份冲突", status=409,
            code="finance_raw_workbook_conflict") from error


def read(principal, month: str, *, enabled=False):
    _closed(enabled)
    with transaction.atomic():
        actor = owner._actor(principal)
        revision = owner._revision()
        published = FinanceMonth.objects.filter(pk=month,
            status="completed").first()
        _need(published is not None)
        batch = FinanceImportBatch.objects.filter(pk=published.batch_id,
            status="completed").first()
        old_receipt = old._read_locked(principal, month)
        row = FinanceRawWorkbookAttestation.objects.select_for_update().filter(
            month=month, batch_id=published.batch_id).select_related(
            "evidence").first()
        _need(batch is not None and row is not None
            and batch.months_json == [month]
            and row.evidence.month == month
            and row.evidence.batch_id == batch.id
            and row.evidence.evidence_digest is not None
            and row.evidence.evidence_digest == old_receipt[
                "evidenceDigest"]
            and row.parser_contract_digest == _sha_text(PARSER_CONTRACT)
            and row.raw_workbook_bytes_observed is True
            and row.raw_workbook_bytes_retained is False
            and row.stable_netshop_shop_identity_verified is False
            and row.mapping_authority_verified is False
            and row.finance_revision == revision["revision"]
            and row.finance_source_digest == revision["source_digest"]
            and row.batch_content_hash == batch.content_hash
            and row.batch_published_state_token == batch.published_state_token
            and row.raw_file_sha256 == batch.raw_file_hash
            and row.source_sheet_count >= 1
            and row.evidence.finance_revision == revision["revision"]
            and row.evidence.finance_source_digest == revision["source_digest"])
        _children(row)
        _need(owner._actor(principal) == actor
            and owner._revision() == revision
            and FinanceMonth.objects.get(pk=month).batch_id == batch.id)
        return _receipt(row)
