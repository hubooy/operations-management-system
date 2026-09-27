import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoInventoryConsumerReader,
  type InventoryConsumerReader,
} from "@/lib/django/inventory-consumer-reader";
import type { DjangoInventoryServiceResult } from "@/lib/django/inventory-service";
import { PublicApiError } from "@/lib/http/api-error";

function unavailable() {
  return new PublicApiError(503, "service_unavailable", "库存批次与当前事实的版本或归属不一致，请重新查询。");
}

/** Enrich exact current-batch reads from existing, revision-bound Django facts. */
export async function withCurrentInventoryBatchOwnership(
  principal: AppPrincipal,
  dataset: "stock" | "age",
  batchId: string,
  result: DjangoInventoryServiceResult<Record<string, unknown>>,
  options: { signal?: AbortSignal; reader?: InventoryConsumerReader } = {},
): Promise<Record<string, unknown>> {
  if (!batchId) return result.data;
  if (principal.scope !== null) throw new PublicApiError(403, "access_denied", "受限数据范围账号不能核验库存批次归属。");
  const items = result.data.items;
  if (!Array.isArray(items) || items.length > 1) throw unavailable();
  if (!items.length) return result.data;
  const batch = items[0] as Record<string, unknown> | null;
  if (!batch || batch.id !== batchId || batch.dataset !== dataset || !/^\d+:[a-f0-9]{12}$/.test(result.revision)) throw unavailable();
  const reader = options.reader ?? createDjangoInventoryConsumerReader();
  const [freshness, facts] = await Promise.all([
    reader.read(principal, { operation: "freshness" }, { signal: options.signal }),
    reader.read(principal, {
      operation: dataset === "stock" ? "inventory_search" : "age_search",
      query: "", offset: 0, limit: 1,
    }, { signal: options.signal }),
  ]);
  if (freshness.revision !== result.revision || facts.revision !== result.revision) throw unavailable();
  const current = freshness.data?.[dataset];
  if (current === undefined || (current !== null && (typeof current.id !== "string" || !current.id))) throw unavailable();
  // Search totals count current warehouse facts, unlike the SKU-grouped stock projection.
  // Historical ownership cannot be inferred from current facts; leave it explicitly unknown.
  const { ownedRowCount: _oldCount, isCurrent: _oldCurrent, ...metadata } = batch;
  void _oldCount;
  void _oldCurrent;
  if (!current || current.id !== batchId) {
    return { ...result.data, items: [{ ...metadata, isCurrent: false }] };
  }
  const total = facts.data?.total;
  if (batch.status !== "completed" || current.snapshotDate !== batch.snapshotDate
    || !Number.isSafeInteger(total) || total <= 0
    || !Array.isArray(facts.data.items) || facts.data.items.length !== 1
    || facts.data.truncated !== (total > 1)) throw unavailable();
  return { ...result.data, items: [{ ...metadata, ownedRowCount: total, isCurrent: true }] };
}
