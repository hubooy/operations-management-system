import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoInventoryConsumerReader,
  type InventoryConsumerReader,
} from "@/lib/django/inventory-consumer-reader";

/**
 * Resolve the current system-cost reference through the inventory domain.
 *
 * Sales callers receive only the immutable batch/date/cost snapshot and never
 * acquire a database connection. Inventory remains the sole fact owner.
 */
export async function findLatestAuthoritativeSystemCostSnapshot(
  principal: AppPrincipal,
  options: { reader?: InventoryConsumerReader; signal?: AbortSignal } = {},
) {
  const reader = options.reader ?? createDjangoInventoryConsumerReader();
  const result = await reader.read(principal, { operation: "system_cost_snapshot" }, { signal: options.signal });
  return result.data.snapshot;
}
