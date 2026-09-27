import type { DiagnosticPeriod } from "./promotion-diagnostic-report";

/** The system-owned import scope is authoritative for this report's store. */
export function promotionSystemSourceReady(...periods: DiagnosticPeriod[]): boolean {
  return periods.length > 0 && periods.every((period) => period.identity.platform === "京东"
    && period.identity.shopName === "志高商用设备旗舰店"
    && period.coverage.complete && period.coverage.aggregateReconciled && period.coverage.batchOwnershipReconciled
    && period.sourceBatches.length === period.coverage.requestedDates.length
    && period.sourceBatches.every((batch) => batch.ownership.length === batch.batchIds.length && batch.ownership.length > 0
        && batch.ownership.every((owner) => owner.status === "completed" && owner.source === "jd_promotion"
          && owner.dataset === "ad" && owner.platform === "京东"
          && owner.shopName === period.identity.shopName && owner.dateMin <= batch.date && owner.dateMax >= batch.date
          && owner.warningCount === 0)
        && batch.aggregateOwnership.status === "completed" && batch.aggregateOwnership.source === "jd_promotion"
        && batch.aggregateOwnership.dataset === "ad" && batch.aggregateOwnership.platform === "京东"
        && batch.aggregateOwnership.shopName === period.identity.shopName
        && batch.aggregateOwnership.dateMin <= batch.date && batch.aggregateOwnership.dateMax >= batch.date
        && batch.aggregateOwnership.warningCount === 0));
}
