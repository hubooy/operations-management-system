from __future__ import annotations

from .models import FinanceImportBatch, FinanceTarget


def batch_payload(batch: FinanceImportBatch) -> dict[str, object]:
    return {
        "id": batch.id,
        "source": batch.source,
        "fileName": batch.file_name,
        "fileSizeBytes": int(batch.file_size_bytes),
        "fileHash": batch.file_hash,
        "status": batch.status,
        "rowCount": int(batch.row_count),
        "insertedCount": int(batch.inserted_count),
        "duplicateCount": int(batch.duplicate_count),
        "warningCount": int(batch.warning_count),
        "parsedMonthCount": int(batch.parsed_month_count),
        "importedMonthCount": int(batch.imported_month_count),
        "skippedMonthCount": int(batch.skipped_month_count),
        "subjectCount": int(batch.subject_count),
        "months": list(batch.months_json or []),
        "warnings": list(batch.warnings_json or []),
        "createdAt": batch.created_at,
        "completedAt": batch.completed_at,
    }


def target_payload(target: FinanceTarget) -> dict[str, object]:
    return {
        "id": target.id,
        "periodType": target.period_type,
        "periodKey": target.period_key,
        "platform": target.platform,
        "shopName": target.shop_name,
        "category": target.category,
        "manager": target.manager,
        "salesTargetCents": int(target.sales_target_cents),
        "profitTargetCents": int(target.profit_target_cents),
        "grossMarginBps": int(target.gross_margin_bps),
        "smallMarginBps": int(target.small_margin_bps),
        "inventoryCleanupTargetCents": int(target.inventory_cleanup_target_cents),
        "promotionFeeRatioBps": int(target.promotion_fee_ratio_bps),
        "stagnantInventoryTargetCents": int(target.stagnant_inventory_target_cents),
        "version": int(target.version),
        "createdAt": target.created_at,
        "updatedAt": target.updated_at,
    }
