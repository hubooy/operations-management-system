import assert from "node:assert/strict";
import test from "node:test";
import type { AppPrincipal } from "../lib/auth/authorization";
import { withCurrentInventoryBatchOwnership } from "../lib/django/inventory-batch-ownership";
import type { InventoryConsumerReader, InventoryConsumerRequest, InventoryConsumerResponseMap } from "../lib/django/inventory-consumer-reader";

const principal: AppPrincipal = { email: "test@example.com", displayName: "Test", role: "admin", scope: null };
const revision = "17:abcdef012345";
const stockId = "a".repeat(64);
const ageId = `inventory_age:${"b".repeat(64)}`;
const date = "2026-09-06";

function fixture(dataset: "stock" | "age" = "stock", overrides: {
  currentId?: string | null; total?: unknown; metadataCount?: number;
  freshnessRevision?: string; factsRevision?: string; missingSource?: boolean;
  currentDate?: string;
} = {}) {
  const id = dataset === "stock" ? stockId : ageId;
  const calls: string[] = [];
  const signal = new AbortController().signal;
  const result = { status: 200, replayed: false, revision, data: {
    items: [{ id, dataset, snapshotDate: date, status: "completed", rowCount: overrides.metadataCount ?? 22642, excludedCount: 0 }],
    pagination: { total: 1 },
  } };
  const reader: InventoryConsumerReader = { async read<R extends InventoryConsumerRequest>(p: AppPrincipal, request: R, options?: { signal?: AbortSignal }) {
    assert.strictEqual(p, principal);
    assert.strictEqual(options?.signal, signal);
    calls.push(request.operation);
    let data: unknown;
    if (request.operation === "freshness") {
      const currentId = overrides.currentId === undefined ? id : overrides.currentId;
      data = overrides.missingSource ? {} : { [dataset]: currentId === null ? null : { id: currentId, snapshotDate: overrides.currentDate ?? date } };
    } else {
      assert.equal(request.operation, dataset === "stock" ? "inventory_search" : "age_search");
      assert.deepEqual(request, { operation: dataset === "stock" ? "inventory_search" : "age_search", query: "", offset: 0, limit: 1 });
      const total = overrides.total === undefined ? 22642 : overrides.total;
      data = { items: [{}], total, truncated: Number(total) > 1 };
    }
    return { revision: (request.operation === "freshness" ? overrides.freshnessRevision : overrides.factsRevision) ?? revision,
      data: data as InventoryConsumerResponseMap[R["operation"]] };
  } };
  return { id, result, calls, options: { reader, signal } };
}

test("exact stock and age ownership counts actual warehouse facts, independently of declared import totals", async () => {
  for (const dataset of ["stock", "age"] as const) {
    const f = fixture(dataset, { metadataCount: 999, total: dataset === "stock" ? 22642 : 5554 });
    const data = await withCurrentInventoryBatchOwnership(principal, dataset, f.id, f.result, f.options);
    assert.deepEqual(data.items, [{ ...f.result.data.items[0], ownedRowCount: dataset === "stock" ? 22642 : 5554, isCurrent: true }]);
    assert.equal("ownedRowCount" in f.result.data.items[0], false);
    assert.equal(f.calls.length, 2);
  }
});

test("ownership never borrows current facts for historical batches or an absent source", async () => {
  for (const currentId of ["c".repeat(64), null]) {
    const f = fixture("stock", { currentId });
    Object.assign(f.result.data.items[0], { ownedRowCount: 22642, isCurrent: true });
    const data = await withCurrentInventoryBatchOwnership(principal, "stock", f.id, f.result, f.options);
    const item = (data.items as Record<string, unknown>[])[0];
    assert.equal(item.isCurrent, false);
    assert.equal("ownedRowCount" in item, false);
  }
});

test("ownership fails closed across revisions, malformed counts, missing datasets, and snapshot changes", async () => {
  for (const overrides of [
    { freshnessRevision: "18:abcdef012345" }, { factsRevision: "18:abcdef012345" },
    { total: "22642" }, { total: -1 }, { total: 0 }, { total: 1.5 }, { total: null },
    { missingSource: true }, { currentDate: "2026-09-05" },
  ]) {
    const f = fixture("stock", overrides);
    await assert.rejects(withCurrentInventoryBatchOwnership(principal, "stock", f.id, f.result, f.options), { status: 503 });
  }
});

test("history lists and absent exact batches do not issue fact queries", async () => {
  const f = fixture();
  assert.strictEqual(await withCurrentInventoryBatchOwnership(principal, "stock", "", f.result, f.options), f.result.data);
  const empty = { ...f.result, data: { items: [] } };
  assert.strictEqual(await withCurrentInventoryBatchOwnership(principal, "stock", f.id, empty, f.options), empty.data);
  assert.equal(f.calls.length, 0);
});

test("ownership rejects scoped principals and mismatched batch identity before querying", async () => {
  const f = fixture();
  await assert.rejects(withCurrentInventoryBatchOwnership({ ...principal, scope: { warehouses: ["test"], channels: [], platforms: [] } }, "stock", f.id, f.result, f.options), { status: 403 });
  await assert.rejects(withCurrentInventoryBatchOwnership(principal, "age", f.id, f.result, f.options), { status: 503 });
  await assert.rejects(withCurrentInventoryBatchOwnership(principal, "stock", ageId, f.result, f.options), { status: 503 });
  assert.equal(f.calls.length, 0);
});
