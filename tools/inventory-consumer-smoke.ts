import { callOperationsTool } from "@/lib/ai/operations-tools";
import { readDjangoInventoryConsumer } from "@/lib/django/inventory-consumer-reader";
import { findLatestAuthoritativeSystemCostSnapshot } from "@/lib/inventory/system-cost-reference";

const principal = {
  email: "local-admin@teruisi.local",
  displayName: "本地管理员",
  role: "admin" as const,
  scope: null,
};

const freshness = await readDjangoInventoryConsumer(principal, {
  operation: "freshness",
});
const projection = await readDjangoInventoryConsumer(principal, {
  operation: "stock_projection",
  offset: 0,
  limit: 1,
});
const systemCost = await findLatestAuthoritativeSystemCostSnapshot(principal);
const aiInventory = await callOperationsTool(
  "get_inventory_health",
  { limit: 1 },
  principal,
);

const result = {
  status: "passed",
  freshness: {
    revision: freshness.revision,
    stockDate: freshness.data.stock?.snapshotDate ?? null,
    ageDate: freshness.data.age?.snapshotDate ?? null,
  },
  projection: {
    revision: projection.revision,
    batchId: projection.data.batchId,
    snapshotDate: projection.data.snapshotDate,
    total: projection.data.total,
    returned: projection.data.rows.length,
  },
  systemCost: {
    batchId: systemCost?.batchId ?? null,
    snapshotDate: systemCost?.snapshotDate ?? null,
    costCount: systemCost?.costs.length ?? 0,
  },
  ai: {
    returned: Array.isArray(aiInventory.items) ? aiInventory.items.length : null,
  },
};

if (
  freshness.data.stock?.snapshotDate !== "2026-09-01"
  || freshness.data.age?.snapshotDate !== "2026-09-01"
  || projection.data.snapshotDate !== "2026-09-01"
  || projection.data.total < 1
  || projection.data.rows.length !== 1
  || systemCost?.snapshotDate !== "2026-09-01"
  || systemCost.costs.length < 1
  || !Array.isArray(aiInventory.items)
  || aiInventory.items.length !== 1
) {
  throw new Error(`库存消费链路 smoke 未满足正式数据契约: ${JSON.stringify(result)}`);
}

process.stdout.write(`${JSON.stringify(result)}\n`);
