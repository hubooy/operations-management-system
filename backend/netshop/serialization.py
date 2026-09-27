from __future__ import annotations

from .models import NetshopImportBatch


def batch_payload(batch: NetshopImportBatch) -> dict[str, object]:
    return {
        "id": batch.id,
        "source": batch.source,
        "dataset": batch.dataset,
        "platform": batch.platform,
        "shopName": batch.shop_name,
        "fileName": batch.file_name,
        "fileSizeBytes": batch.file_size_bytes,
        "fileHash": batch.file_hash,
        "sheetName": batch.sheet_name,
        "status": batch.status,
        "rowCount": batch.row_count,
        "insertedCount": batch.inserted_count,
        "duplicateCount": batch.duplicate_count,
        "warningCount": batch.warning_count,
        "dateMin": batch.date_min,
        "dateMax": batch.date_max,
        "snapshotDate": batch.snapshot_date,
        "warnings": batch.warnings_json,
        "totals": batch.totals_json,
        "note": batch.note,
        "createdAt": batch.created_at,
        "completedAt": batch.completed_at,
    }

