from __future__ import annotations

from .models import MarketImportBatch
from .revisions import iso


def batch_payload(batch: MarketImportBatch) -> dict[str, object]:
    return {
        "id": batch.id,
        "sourceType": batch.source_type,
        "fileName": batch.file_name,
        "fileSizeBytes": batch.file_size_bytes,
        "fileHash": batch.raw_file_hash,
        "rawFileHash": batch.raw_file_hash,
        "contentHash": batch.content_hash,
        "sheetName": batch.sheet_name,
        "status": batch.status,
        "rowCount": batch.row_count,
        "insertedCount": batch.inserted_count,
        "updatedCount": batch.updated_count,
        "warningCount": batch.warning_count,
        "periodStart": batch.period_start,
        "periodEnd": batch.period_end,
        "warnings": batch.warnings_json,
        "createdAt": iso(batch.created_at),
        "completedAt": iso(batch.completed_at),
    }
