import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { registerHooks } from "node:module";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import type {
  SalesConsumerReader,
  SalesConsumerRequest,
} from "../lib/django/sales-consumer-reader";
import { PublicApiError } from "../lib/http/api-error";

const testEnvironment: { DB?: unknown } = {};
(globalThis as typeof globalThis & { __djangoInventoryProductEnv?: typeof testEnvironment })
  .__djangoInventoryProductEnv = testEnvironment;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return {
        url: "data:text/javascript,export const env=globalThis.__djangoInventoryProductEnv;",
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});

const { ensureInventorySchema } = await import("../lib/inventory/database");
const { ensureErpReferenceSchema } = await import("../lib/erp-reference/database");
const { getInventoryOverview } = await import("../lib/inventory/overview");

const principal: AppPrincipal = {
  email: "scoped@example.com",
  displayName: "受限分析员",
  role: "analyst",
  scope: {
    warehouses: ["上海仓"],
    channels: ["京东"],
    platforms: ["京东"],
  },
};

function sqliteAdapter(sqlite: DatabaseSync) {
  return {
    prepare(sql: string) {
      let values: SQLInputValue[] = [];
      return {
        bind(...nextValues: unknown[]) {
          values = nextValues as SQLInputValue[];
          return this;
        },
        async first<T>(column?: string) {
          const row = sqlite.prepare(sql).get(...values) as Record<string, unknown> | undefined;
          return (column ? row?.[column] : row ?? null) as T | null;
        },
        async all<T>() {
          return { results: sqlite.prepare(sql).all(...values) as T[] };
        },
        async run() {
          const result = sqlite.prepare(sql).run(...values);
          return { meta: { changes: Number(result.changes), last_row_id: Number(result.lastInsertRowid) } };
        },
      };
    },
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      const results = [];
      for (const statement of statements) results.push(await statement.run());
      return results;
    },
  };
}

async function setupDatabase(productCount: number) {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as never;
  testEnvironment.DB = db;
  await Promise.all([ensureInventorySchema(db), ensureErpReferenceSchema(db)]);
  sqlite.prepare(`INSERT INTO inventory_import_batches (
    id, source, file_name, file_size_bytes, file_hash, sheet_name, snapshot_date, status,
    row_count, inserted_count, totals_json, completed_at
  ) VALUES ('inventory-batch', 'test', 'inventory.xlsx', 1, 'inventory-hash', 'Sheet1',
    '2026-08-28', 'completed', ?, ?, '{}', '2026-08-28 10:00:00')`).run(productCount, productCount);
  const insert = sqlite.prepare(`INSERT INTO inventory_stock_lines (
    batch_id, row_key, source_row_number, snapshot_date, warehouse, warehouse_type,
    product_code, product_name, brand, specification, barcode, category,
    on_hand_quantity, available_quantity, locked_quantity, in_transit_quantity,
    unit_cost_cents, inventory_age_days
  ) VALUES ('inventory-batch', ?, ?, '2026-08-28', '上海仓', 'owned', ?, ?, '', '', '', '类目', 1, 1, 0, 0, 100, 10)`);
  sqlite.exec("BEGIN");
  for (let index = 0; index < productCount; index += 1) {
    const code = `SKU-${String(index).padStart(5, "0")}`;
    insert.run(`row-${index}`, index + 1, code, `货品${index}`);
  }
  sqlite.exec("COMMIT");
  return { sqlite, db };
}

function readerFrom(
  handler: (receivedPrincipal: AppPrincipal, request: SalesConsumerRequest) => Promise<unknown> | unknown,
): SalesConsumerReader {
  return {
    read: (async (receivedPrincipal: AppPrincipal, request: SalesConsumerRequest) => (
      handler(receivedPrincipal, request)
    )) as SalesConsumerReader["read"],
  };
}

function freshness() {
  return {
    revision: "sales:9/erp:4",
    data: {
      dataStartDate: "2026-08-01",
      dataCutoffDate: "2026-08-28",
      latestBatch: {
        id: "sales-batch",
        fileName: "sales.xlsx",
        completedAt: "2026-08-28 10:00:00",
        rowCount: 1,
      },
    },
  };
}

function isUnavailable(error: unknown) {
  return error instanceof PublicApiError
    && error.status === 503
    && error.code === "service_unavailable";
}

test("inventory consumer uses fixed chunks, exclusive end dates, and the exact principal", async () => {
  const { sqlite, db } = await setupDatabase(1_001);
  const requests: Array<{ principal: AppPrincipal; request: SalesConsumerRequest }> = [];
  const reader = readerFrom((receivedPrincipal, request) => {
    requests.push({ principal: receivedPrincipal, request });
    if (request.operation === "freshness") return freshness();
    if (request.operation === "inventory_demand") return {
      revision: "sales:9/erp:4",
      data: {
        dataStartDate: "2026-08-01",
        dataCutoffDate: "2026-08-28",
        rows: [],
        truncated: false,
      },
    };
    throw new Error(`unexpected operation: ${request.operation}`);
  });

  const overview = await getInventoryOverview(db, principal, {
    startDate: "2026-08-01",
    endDate: "2026-08-28",
    page: 1,
    pageSize: 1,
  }, reader);
  assert.equal(overview.pagination.total, 1_001);
  const inventoryRequests = requests.map((item) => item.request)
    .filter((request): request is Extract<SalesConsumerRequest, { operation: "inventory_demand" }> => request.operation === "inventory_demand");
  assert.deepEqual(inventoryRequests.map((request) => request.productCodes?.length), [500, 500, 1]);
  assert.ok(inventoryRequests.every((request) => request.limit === 10_000 && request.endDate === "2026-08-29"));
  assert.ok(requests.every((item) => item.principal === principal));
  sqlite.close();
});

test("inventory consumer fails closed on truncated and out-of-request aggregate rows", async () => {
  for (const data of [
    {
      dataStartDate: "2026-08-01", dataCutoffDate: "2026-08-28", rows: [], truncated: true,
    },
    {
      dataStartDate: "2026-08-01", dataCutoffDate: "2026-08-28",
      rows: [{
        productCode: "OUTSIDE", warehouseKey: "上海", productName: "越界",
        salesQuantity: 1, absoluteQuantity: 1, absoluteCostCents: 100,
      }],
      truncated: false,
    },
  ]) {
    const { sqlite, db } = await setupDatabase(1);
    const reader = readerFrom((_receivedPrincipal, request) => request.operation === "freshness"
      ? freshness()
      : { revision: "sales:9/erp:4", data });
    await assert.rejects(
      getInventoryOverview(db, principal, { page: 1, pageSize: 1 }, reader),
      isUnavailable,
    );
    sqlite.close();
  }
});

test("inventory consumer fails closed on incomplete batch metadata", async () => {
  {
    const { sqlite, db } = await setupDatabase(1);
    const incompleteFreshness = freshness() as unknown as {
      revision: string;
      data: { dataStartDate: string; dataCutoffDate: string; latestBatch: Record<string, unknown> };
    };
    delete incompleteFreshness.data.latestBatch.rowCount;
    const reader = readerFrom(() => incompleteFreshness);
    await assert.rejects(
      getInventoryOverview(db, principal, { page: 1, pageSize: 1 }, reader),
      isUnavailable,
    );
    sqlite.close();
  }
});

test("inventory and product online paths contain no D1 sales dependency", async () => {
  const paths = [
    "../lib/inventory/overview.ts",
    "../app/api/inventory/overview/route.ts",
    "../app/api/inventory/replenishment/route.ts",
    "../lib/products/summary.ts",
    "../app/api/products/summary/route.ts",
  ];
  for (const path of paths) {
    const source = await readFile(new URL(path, import.meta.url), "utf8");
    assert.doesNotMatch(
      source,
      /sales_order_lines|sales_import_batches|ensureSalesSchema|getSalesDatabase|findLatestSalesImportBatch/,
      path,
    );
  }
  const inventoryRoute = await readFile(new URL("../app/api/inventory/overview/route.ts", import.meta.url), "utf8");
  const replenishmentRoute = await readFile(new URL("../app/api/inventory/replenishment/route.ts", import.meta.url), "utf8");
  const productRoute = await readFile(new URL("../app/api/products/summary/route.ts", import.meta.url), "utf8");
  assert.match(inventoryRoute, /createDjangoInventoryService/);
  assert.match(inventoryRoute, /INVENTORY_OVERVIEW_PATH/);
  assert.match(replenishmentRoute, /createDjangoInventoryService/);
  assert.match(replenishmentRoute, /INVENTORY_REPLENISHMENT_PATH/);
  assert.doesNotMatch(inventoryRoute, /getInventoryDatabase|getD1Database|env\.DB/);
  assert.doesNotMatch(replenishmentRoute, /getInventoryDatabase|getD1Database|env\.DB/);
  assert.match(productRoute, /getProductSummary\(principal,/);
  assert.doesNotMatch(productRoute, /getInventoryDatabase|env\.DB/);
});
