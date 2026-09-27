import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import test from "node:test";
import type { AppPrincipal } from "../lib/auth/authorization";
import type { SalesConsumerReader } from "../lib/django/sales-consumer-reader";

const testEnvironment: { DB?: unknown } = {};
(globalThis as typeof globalThis & { __productInventoryEnv?: typeof testEnvironment }).__productInventoryEnv = testEnvironment;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return {
        url: "data:text/javascript,export const env=globalThis.__productInventoryEnv;",
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});

const { ensureInventorySchema } = await import("../lib/inventory/database");
const { ensureErpReferenceSchema } = await import("../lib/erp-reference/database");
const {
  getInventoryDashboardOverview: getInventoryDashboardOverviewCore,
  getInventoryFullOverview: getInventoryFullOverviewCore,
  getInventoryOverview: getInventoryOverviewCore,
  getInventoryPlanOverview: getInventoryPlanOverviewCore,
} = await import("../lib/inventory/overview");
const { parseInventoryOverviewView } = await import("../lib/inventory/query-contract");
const { getInventoryAgeAnalysis } = await import("../lib/inventory/age-analysis");

function sqliteAdapter(
  sqlite: DatabaseSync,
  onPrepare?: (sql: string) => void,
  transformFirst?: (sql: string, row: Record<string, unknown> | undefined) => Record<string, unknown> | undefined,
  transformAll?: (sql: string, rows: Array<Record<string, unknown>>) => Array<Record<string, unknown>>,
) {
  return {
    __sqlite: sqlite,
    prepare(sql: string) {
      onPrepare?.(sql);
      let values: SQLInputValue[] = [];
      return {
        bind(...nextValues: unknown[]) {
          values = nextValues as SQLInputValue[];
          return this;
        },
        async first<T>(column?: string) {
          const rawRow = sqlite.prepare(sql).get(...values) as Record<string, unknown> | undefined;
          const row = transformFirst ? transformFirst(sql, rawRow) : rawRow;
          return (column ? row?.[column] : row ?? null) as T | null;
        },
        async all<T>() {
          const rawRows = sqlite.prepare(sql).all(...values) as Array<Record<string, unknown>>;
          const rows = transformAll ? transformAll(sql, rawRows) : rawRows;
          return { results: rows as T[] };
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

const principal: AppPrincipal = {
  email: "admin@example.com",
  displayName: "管理员",
  role: "admin",
  scope: null,
};

function ensureSalesFixtureSchema(sqlite: DatabaseSync) {
  sqlite.exec(`
    CREATE TABLE sales_import_batches (
      id TEXT PRIMARY KEY, source TEXT, file_name TEXT, file_size_bytes INTEGER, file_hash TEXT,
      sheet_name TEXT, status TEXT, row_count INTEGER, inserted_count INTEGER, completed_at TEXT
    );
    CREATE TABLE sales_order_lines (
      source_line_key TEXT, source_row_hash TEXT, first_import_batch_id TEXT, last_import_batch_id TEXT,
      source_row_number INTEGER, order_no TEXT, online_order_no TEXT, channel TEXT, platform TEXT,
      shop_name TEXT, logistics_company TEXT, warehouse TEXT, product_code TEXT, online_spec_code TEXT,
      product_name TEXT, specification TEXT, barcode TEXT, supplier TEXT, category TEXT, quantity REAL,
      list_unit_price_cents INTEGER, cost_amount_cents INTEGER, allocated_unit_price_cents INTEGER,
      allocated_amount_cents INTEGER, fee_allocation_cents INTEGER, gross_profit_cents INTEGER,
      gross_margin_bps INTEGER, untaxed_gross_profit_cents INTEGER, untaxed_gross_margin_bps INTEGER,
      order_time TEXT, sales_time TEXT, ship_time TEXT, line_ship_time TEXT, business_type TEXT
    );
  `);
}

function salesReader(sqlite: DatabaseSync): SalesConsumerReader {
  const revision = "sales:fixture:1";
  const latestBatch = () => {
    const row = sqlite.prepare(`SELECT id,file_name,completed_at,row_count
      FROM sales_import_batches WHERE status='completed' ORDER BY completed_at DESC LIMIT 1`).get() as
      | { id: string; file_name: string; completed_at: string | null; row_count: number }
      | undefined;
    return row ? { id: row.id, fileName: row.file_name, completedAt: row.completed_at, rowCount: Number(row.row_count) } : null;
  };
  return {
    async read(_principal, request) {
      const coverage = sqlite.prepare(`SELECT MIN(SUBSTR(ship_time,1,10)) AS start_date,
        MAX(SUBSTR(ship_time,1,10)) AS end_date FROM sales_order_lines`).get() as
        { start_date: string | null; end_date: string | null };
      if (request.operation === "freshness") {
        return {
          revision,
          data: { dataStartDate: coverage.start_date, dataCutoffDate: coverage.end_date, latestBatch: latestBatch() },
        } as never;
      }
      if (request.operation !== "inventory_demand" && request.operation !== "product_performance") {
        throw new Error(`unexpected sales operation: ${request.operation}`);
      }
      const requestedCodes = new Set(request.productCodes ?? []);
      const sourceRows = sqlite.prepare(`SELECT ship_time,warehouse,product_code,product_name,specification,
        category,supplier,quantity,allocated_amount_cents,cost_amount_cents,fee_allocation_cents,
        gross_profit_cents,platform,shop_name,channel
        FROM sales_order_lines ORDER BY product_code`).all() as Array<Record<string, string | number>>;
      const eligible = sourceRows.filter((row) => {
        const date = String(row.ship_time).slice(0, 10);
        if (request.startDate && date < request.startDate) return false;
        if (request.endDate && date >= request.endDate) return false;
        if (String(row.warehouse).trim() === "刷刷仓") return false;
        return requestedCodes.size === 0 || requestedCodes.has(String(row.product_code));
      });
      const grouped = new Map<string, Array<Record<string, string | number>>>();
      for (const row of eligible) {
        const code = String(row.product_code);
        const rows = grouped.get(code) ?? [];
        rows.push(row);
        grouped.set(code, rows);
      }
      const dataStartDate = eligible.map((row) => String(row.ship_time).slice(0, 10)).sort()[0] ?? null;
      const dataCutoffDate = eligible.map((row) => String(row.ship_time).slice(0, 10)).sort().at(-1) ?? null;
      if (request.operation === "inventory_demand") {
        return {
          revision,
          data: {
            dataStartDate,
            dataCutoffDate,
            rows: [...grouped.entries()].map(([productCode, rows]) => ({
              productCode,
              warehouseKey: String(rows[0]!.warehouse).trim().replace(/仓$/, ""),
              productName: String(rows[0]!.product_name),
              salesQuantity: rows.reduce((sum, row) => sum + Number(row.quantity), 0),
              absoluteQuantity: rows.reduce((sum, row) => sum + Math.abs(Number(row.quantity)), 0),
              absoluteCostCents: rows.reduce((sum, row) => sum + Math.abs(Number(row.cost_amount_cents)), 0),
            })),
            truncated: false,
          },
        } as never;
      }
      const outletOptions = new Map<string, { platform: string; shopName: string; channel: string }>();
      const rows = [...grouped.entries()].map(([productCode, productRows]) => {
        const outlets = new Map<string, { platform: string; shopName: string; channel: string }>();
        for (const row of productRows) {
          const outlet = { platform: String(row.platform), shopName: String(row.shop_name), channel: String(row.channel) };
          const key = JSON.stringify(outlet);
          outlets.set(key, outlet);
          outletOptions.set(key, outlet);
        }
        const netSalesCents = productRows.reduce((sum, row) => sum + Number(row.allocated_amount_cents), 0);
        return {
          productCode,
          productName: String(productRows[0]!.product_name),
          specification: String(productRows[0]!.specification),
          category: String(productRows[0]!.category),
          supplier: String(productRows[0]!.supplier),
          netQuantity: productRows.reduce((sum, row) => sum + Number(row.quantity), 0),
          grossSalesCents: productRows.reduce((sum, row) => sum + Math.max(0, Number(row.allocated_amount_cents)), 0),
          refundAmountCents: productRows.reduce((sum, row) => sum + Math.max(0, -Number(row.allocated_amount_cents)), 0),
          netSalesCents,
          costCents: productRows.reduce((sum, row) => sum + Number(row.cost_amount_cents), 0),
          feeCents: productRows.reduce((sum, row) => sum + Number(row.fee_allocation_cents), 0),
          grossProfitCents: productRows.reduce((sum, row) => sum + Number(row.gross_profit_cents), 0),
          absoluteQuantity: productRows.reduce((sum, row) => sum + Math.abs(Number(row.quantity)), 0),
          absoluteCostCents: productRows.reduce((sum, row) => sum + Math.abs(Number(row.cost_amount_cents)), 0),
          outlets: [...outlets.values()],
        };
      });
      return {
        revision,
        data: {
          dataStartDate,
          dataCutoffDate,
          latestBatch: latestBatch(),
          rows,
          outletOptions: [...outletOptions.values()],
          truncated: false,
        },
      } as never;
    },
  };
}

function fixtureContext(db: unknown) {
  const sqlite = (db as { __sqlite: DatabaseSync }).__sqlite;
  return { sqlite, reader: salesReader(sqlite) };
}

function getInventoryDashboardOverview(db: unknown, input: Parameters<typeof getInventoryDashboardOverviewCore>[2] = {}) {
  const { reader } = fixtureContext(db);
  return getInventoryDashboardOverviewCore(db as never, principal, input, reader);
}

function getInventoryOverview(db: unknown, input: Parameters<typeof getInventoryOverviewCore>[2] = {}) {
  const { reader } = fixtureContext(db);
  return getInventoryOverviewCore(db as never, principal, input, reader);
}

function getInventoryFullOverview(db: unknown, input: Parameters<typeof getInventoryFullOverviewCore>[2] = {}) {
  const { reader } = fixtureContext(db);
  return getInventoryFullOverviewCore(db as never, principal, input, reader);
}

function getInventoryPlanOverview(db: unknown, input: Parameters<typeof getInventoryPlanOverviewCore>[2] = {}) {
  const { reader } = fixtureContext(db);
  return getInventoryPlanOverviewCore(db as never, principal, input, reader);
}

function insertSalesLine(sqlite: DatabaseSync, input: {
  productCode: string;
  productName: string;
  category: string;
  quantity: number;
  netSalesCents: number;
  costCents: number;
}) {
  sqlite.prepare(`INSERT INTO sales_order_lines (
    source_line_key, source_row_hash, first_import_batch_id, last_import_batch_id, source_row_number,
    order_no, online_order_no, channel, platform, shop_name, logistics_company, warehouse,
    product_code, online_spec_code, product_name, specification, barcode, supplier, category,
    quantity, list_unit_price_cents, cost_amount_cents, allocated_unit_price_cents,
    allocated_amount_cents, fee_allocation_cents, gross_profit_cents, gross_margin_bps,
    untaxed_gross_profit_cents, untaxed_gross_margin_bps, order_time, sales_time, ship_time,
    line_ship_time, business_type
  ) VALUES (
    ?, ?, 'sales-batch', 'sales-batch', 1,
    ?, '', '京东', '京东', '测试店铺', '', '上海仓',
    ?, '', ?, '', '', '', ?,
    ?, 0, ?, 0,
    ?, 0, ?, 0,
    0, 0, '2026-08-18 08:00:00', '2026-08-18 08:00:00', '2026-08-18 08:00:00',
    '2026-08-18 08:00:00', '销售'
  )`).run(
    `${input.productCode}-line`,
    `${input.productCode}-hash`,
    `${input.productCode}-order`,
    input.productCode,
    input.productName,
    input.category,
    input.quantity,
    input.costCents,
    input.netSalesCents,
    input.netSalesCents - input.costCents,
  );
}

function insertStockLine(sqlite: DatabaseSync, input: {
  rowKey: string;
  productCode: string;
  productName: string;
  brand?: string;
  category: string;
  availableQuantity: number;
  unitCostCents: number;
  ageDays: number;
}) {
  sqlite.prepare(`INSERT INTO inventory_stock_lines (
    batch_id, row_key, source_row_number, snapshot_date, warehouse, warehouse_type,
    product_code, product_name, brand, specification, barcode, category,
    on_hand_quantity, available_quantity, locked_quantity, in_transit_quantity,
    unit_cost_cents, inventory_age_days
  ) VALUES ('inventory-batch', ?, 1, '2026-08-18', '上海仓', 'owned', ?, ?, ?, '', '', ?, ?, ?, 0, 0, ?, ?)`)
    .run(
      input.rowKey,
      input.productCode,
      input.productName,
      input.brand ?? "",
      input.category,
      input.availableQuantity,
      input.availableQuantity,
      input.unitCostCents,
      input.ageDays,
    );
}

test("库存 overview 保留缺省 full 兼容并支持唯一受控投影", () => {
  assert.equal(parseInventoryOverviewView(new URLSearchParams()), "full");
  assert.equal(parseInventoryOverviewView(new URLSearchParams("view=full")), "full");
  assert.equal(parseInventoryOverviewView(new URLSearchParams("view=dashboard")), "dashboard");
  assert.equal(parseInventoryOverviewView(new URLSearchParams("view=overview")), "overview");
  assert.equal(parseInventoryOverviewView(new URLSearchParams("view=plan")), "plan");
  assert.throws(() => parseInventoryOverviewView(new URLSearchParams("view=overview&view=plan")), /view 只能提供一次/);
  assert.throws(() => parseInventoryOverviewView(new URLSearchParams("view=unknown")), /full、dashboard、overview 或 plan/);
});

test("库存真实 SQL 分页把明确零成本计为完整覆盖", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as never;
  testEnvironment.DB = db;
  ensureSalesFixtureSchema(sqlite);
  await Promise.all([ensureInventorySchema(db), ensureErpReferenceSchema(db)]);

  sqlite.prepare(`INSERT INTO sales_import_batches (
    id, source, file_name, file_size_bytes, file_hash, sheet_name, status, row_count, inserted_count, completed_at
  ) VALUES ('sales-batch', 'test', 'sales.xlsx', 1, 'sales-hash', 'Sheet1', 'completed', 2, 2, '2026-08-18 10:00:00')`).run();
  sqlite.prepare(`INSERT INTO inventory_import_batches (
    id, source, file_name, file_size_bytes, file_hash, sheet_name, snapshot_date, status,
    row_count, inserted_count, totals_json, completed_at
  ) VALUES ('inventory-batch', 'test', 'inventory.xlsx', 1, 'inventory-hash', 'Sheet1', '2026-08-18', 'completed', 3, 3, '{}', '2026-08-18 10:00:00')`).run();

  insertSalesLine(sqlite, { productCode: "A", productName: "货品A", category: "类目A", quantity: 10, netSalesCents: 10_000, costCents: 0 });
  insertSalesLine(sqlite, { productCode: "B", productName: "货品B", category: "类目B", quantity: 5, netSalesCents: 5_000, costCents: 2_500 });
  insertStockLine(sqlite, { rowKey: "a-priced", productCode: "A", productName: "货品A", brand: "品牌甲", category: "类目A", availableQuantity: 1, unitCostCents: 1_000, ageDays: 120 });
  insertStockLine(sqlite, { rowKey: "a-missing", productCode: "A", productName: "货品A", brand: "品牌甲", category: "类目A", availableQuantity: 99, unitCostCents: 0, ageDays: 120 });
  insertStockLine(sqlite, { rowKey: "b-priced", productCode: "B", productName: "货品B", brand: "品牌乙", category: "类目B", availableQuantity: 5, unitCostCents: 500, ageDays: 30 });
  sqlite.prepare(`INSERT INTO replenishment_plan_items (
    id, source_batch_id, product_code, product_name, warehouse,
    suggested_quantity, planned_quantity, coverage_days_tenths, reason, status
  ) VALUES ('plan-a', 'inventory-batch', 'A', '货品A', '上海仓', 20, 12, 300, '真实 SQL 投影测试', 'draft')`).run();

  const overview = await getInventoryOverview(db, {
    exactKey: `上海仓\u001fA`,
    startDate: "2026-08-18",
    endDate: "2026-08-18",
    page: 1,
    pageSize: 1,
  });
  assert.equal(overview.pagination.total, 1);
  assert.equal(overview.metrics.stockValueComplete, true);
  assert.equal(overview.metrics.knownStockValueCents, 1_000);
  assert.equal(overview.metrics.costCoverageRate, 1);
  assert.equal(overview.items[0]?.stockValueCents, 1_000);

  const fullSql: string[] = [];
  const fullOverview = await getInventoryOverview(sqliteAdapter(sqlite, (sql) => fullSql.push(sql)) as never, {
    startDate: "2026-08-18",
    endDate: "2026-08-18",
  });
  const dashboardSql: string[] = [];
  const dashboardOverview = await getInventoryDashboardOverview(
    sqliteAdapter(sqlite, (sql) => dashboardSql.push(sql)) as never,
    { startDate: "2026-08-18", endDate: "2026-08-18" },
  );
  assert.deepEqual(Object.keys(dashboardOverview).sort(), ["hasInventory", "health", "metrics", "sync"]);
  assert.deepEqual(dashboardOverview.sync, fullOverview.sync);
  assert.deepEqual(dashboardOverview.metrics, fullOverview.metrics);
  assert.deepEqual(dashboardOverview.health, fullOverview.health);
  assert.equal(fullOverview.projection, "overview");
  assert.deepEqual(fullOverview.plans, []);
  assert.deepEqual(fullOverview.items.map((item) => item.productCode), ["B", "A"]);
  assert.deepEqual(fullOverview.recommendations.map((item) => item.productCode), ["A", "B"]);
  assert.equal(fullOverview.metrics.skuWarehouseCount, 2);
  assert.equal(fullOverview.metrics.totalAvailableQuantity, 105);
  assert.equal(fullOverview.metrics.knownStockValueCents, 3_500);
  assert.equal(fullOverview.metrics.urgentCount, 1);
  assert.equal(fullOverview.metrics.replenishCount, 1);
  assert.equal(fullOverview.metrics.recommendationCount, 2);
  assert.equal(fullOverview.metrics.averageCoverageDays, 7);
  assert.deepEqual(new Set(fullOverview.filters.brands), new Set(["品牌甲", "品牌乙"]));
  assert.deepEqual(new Set(fullOverview.filters.categories), new Set(["类目A", "类目B"]));
  const legacyFullOverview = await getInventoryFullOverview(db, {
    startDate: "2026-08-18",
    endDate: "2026-08-18",
  });
  assert.equal("projection" in legacyFullOverview, false, "缺省 full 响应不得新增投影字段破坏旧契约");
  assert.deepEqual(legacyFullOverview.items, fullOverview.items);
  assert.equal(legacyFullOverview.plans.length, 1);
  assert.equal(legacyFullOverview.plans[0]?.id, "plan-a");
  assert.equal(legacyFullOverview.planSummary.draftCount, 1);
  assert.equal(fullSql.filter((sql) => sql.includes("WITH stock AS (")).length, 1);
  const combinedOverviewSql = fullSql.find((sql) => sql.includes("filtered AS MATERIALIZED"));
  assert.ok(combinedOverviewSql, "overview 应只物化一次筛选后的库存集合");
  assert.match(combinedOverviewSql, /metrics AS MATERIALIZED/);
  assert.match(combinedOverviewSql, /page_rows AS MATERIALIZED/);
  assert.match(combinedOverviewSql, /recommendation_rows AS MATERIALIZED/);
  assert.match(combinedOverviewSql, /page_projection AS MATERIALIZED/);
  assert.match(combinedOverviewSql, /recommendation_projection AS MATERIALIZED/);
  assert.match(combinedOverviewSql, /json_patch/);
  assert.match(combinedOverviewSql, /'metrics' AS section/);
  assert.match(combinedOverviewSql, /'page' AS section/);
  assert.match(combinedOverviewSql, /'recommendation' AS section/);
  assert.doesNotMatch(combinedOverviewSql, /json_group_array/);
  assert.match(combinedOverviewSql, /LIMIT 50 OFFSET 0/);
  assert.equal(fullSql.some((sql) => sql.includes("SELECT COUNT(*) AS total FROM replenishment_plan_items")), false);
  assert.equal(fullSql.some((sql) => sql.includes("AS draft_count")), false);
  assert.equal(dashboardSql.filter((sql) => sql.includes("WITH stock AS (")).length, 1);
  assert.equal(dashboardSql.some((sql) => sql.includes("filtered AS MATERIALIZED")), false);
  assert.equal(dashboardSql.some((sql) => sql.includes("page_json")), false);
  assert.equal(dashboardSql.some((sql) => sql.includes("SELECT DISTINCT warehouse FROM classified")), false);
  assert.equal(dashboardSql.some((sql) => sql.includes("LIMIT 50 OFFSET 0")), false);
  assert.equal(dashboardSql.some((sql) => sql.includes("LIMIT ? OFFSET ?")), false);
  assert.equal(dashboardSql.some((sql) => sql.includes("SELECT COUNT(*) AS total FROM replenishment_plan_items")), false);

  const filteredOverview = await getInventoryOverview(db, {
    query: "货品A",
    startDate: "2026-08-18",
    endDate: "2026-08-18",
    warehouses: ["上海仓"],
    brands: ["品牌甲"],
    categories: ["类目A"],
    warehouseTypes: ["owned"],
    statuses: ["replenish"],
    page: 1,
    pageSize: 50,
  });
  assert.equal(filteredOverview.pagination.total, 1);
  assert.deepEqual(filteredOverview.items.map((item) => item.productCode), ["A"]);
  assert.deepEqual(filteredOverview.recommendations.map((item) => item.productCode), ["A"]);
  assert.equal(filteredOverview.metrics.skuWarehouseCount, 1);
  assert.equal(filteredOverview.metrics.urgentCount, 0);
  assert.equal(filteredOverview.metrics.replenishCount, 1);
  assert.equal(filteredOverview.metrics.recommendationCount, 1);

  const noSalesOverview = await getInventoryOverview(db, {
    startDate: "2026-08-19",
    endDate: "2026-08-19",
    page: 1,
    pageSize: 50,
  });
  assert.equal(noSalesOverview.items.length, 2);
  assert.equal(noSalesOverview.items.every((item) => item.sales30d === null && item.suggestedQuantity === null), true);
  assert.equal(noSalesOverview.items.every((item) => item.status === "no_sales"), true);

  const maximumFilterSql: string[] = [];
  const maximumFilterOverview = await getInventoryOverview(
    sqliteAdapter(sqlite, (sql) => maximumFilterSql.push(sql)) as never,
    {
      query: "K1 K2 K3 K4 K5 K6 K7 K8",
      startDate: "2026-08-18",
      endDate: "2026-08-18",
      warehouses: Array.from({ length: 10 }, (_, index) => `仓库${index}`),
      brands: Array.from({ length: 20 }, (_, index) => `品牌${index}`),
      categories: Array.from({ length: 20 }, (_, index) => `类目${index}`),
      warehouseTypes: ["owned", "jd_rdc", "other"],
      statuses: ["urgent", "replenish", "healthy", "slow", "stagnant", "no_sales"],
      exactKey: "不存在的仓库\u001f不存在的货品",
      page: 1,
      pageSize: 100,
    },
  );
  assert.equal(maximumFilterOverview.pagination.total, 0);
  const maximumProjectionSql = maximumFilterSql.find((sql) => sql.includes("'metrics' AS section"));
  assert.ok(maximumProjectionSql);
  assert.ok((maximumProjectionSql.match(/\?/g) ?? []).length <= 100, "最大合法筛选必须保持在 D1 100 bind 上限内");
  assert.match(maximumProjectionSql, /INSTR\(LOWER\(product_code\), \?\) > 0/);
  assert.doesNotMatch(maximumProjectionSql, /\bLIKE\s+\?/);

  const longUnicodeQuerySql: string[] = [];
  const longUnicodeQueryOverview = await getInventoryOverview(
    sqliteAdapter(sqlite, (sql) => longUnicodeQuerySql.push(sql)) as never,
    {
      query: "超长中文检索词".repeat(15),
      startDate: "2026-08-18",
      endDate: "2026-08-18",
    },
  );
  assert.equal(longUnicodeQueryOverview.pagination.total, 0);
  const longUnicodeProjectionSql = longUnicodeQuerySql.find((sql) => sql.includes("'metrics' AS section"));
  assert.ok(longUnicodeProjectionSql);
  assert.match(longUnicodeProjectionSql, /INSTR\(LOWER\(resolved_product_name\), \?\) > 0/);
  assert.doesNotMatch(longUnicodeProjectionSql, /\bLIKE\s+\?/);

  const emptyOverview = await getInventoryOverview(db, {
    query: "不存在的货品",
    startDate: "2026-08-18",
    endDate: "2026-08-18",
    page: 1,
    pageSize: 1,
  });
  assert.equal(emptyOverview.pagination.total, 0);
  assert.equal(emptyOverview.pagination.totalPages, 0);
  assert.deepEqual(emptyOverview.items, []);
  assert.deepEqual(emptyOverview.recommendations, []);
  assert.equal(emptyOverview.metrics.skuWarehouseCount, 0);
  assert.equal(emptyOverview.metrics.recommendationCount, 0);
  assert.deepEqual(emptyOverview.filters.warehouses, ["上海仓"], "空筛选结果不应清空批次仓库 facet");
  assert.deepEqual(new Set(emptyOverview.filters.brands), new Set(["品牌甲", "品牌乙"]), "空筛选结果不应清空批次品牌 facet");
  assert.deepEqual(new Set(emptyOverview.filters.categories), new Set(["类目A", "类目B"]), "空筛选结果不应清空批次品类 facet");

  const beyondOverview = await getInventoryOverview(db, {
    startDate: "2026-08-18",
    endDate: "2026-08-18",
    page: 3,
    pageSize: 1,
  });
  assert.equal(beyondOverview.pagination.total, 2);
  assert.equal(beyondOverview.pagination.totalPages, 2);
  assert.equal(beyondOverview.pagination.returned, 0);
  assert.equal(beyondOverview.pagination.truncated, false);
  assert.deepEqual(beyondOverview.items, []);
  assert.deepEqual(beyondOverview.recommendations.map((item) => item.productCode), ["A", "B"]);
  assert.deepEqual(beyondOverview.metrics, fullOverview.metrics, "超页请求仍应返回完整且同口径的指标");

  const corruptProjection = (
    transform: (rows: Array<Record<string, unknown>>) => Array<Record<string, unknown>>,
  ) => getInventoryOverview(
    sqliteAdapter(sqlite, undefined, undefined, (sql, rows) => sql.includes("'metrics' AS section") ? transform(rows) : rows) as never,
    { startDate: "2026-08-18", endDate: "2026-08-18" },
  );
  await assert.rejects(
    () => corruptProjection((rows) => rows.map((row) => row.section === "page" && row.section_index === 0
      ? { ...row, item_json: '{"product_code":"A"}' }
      : row)),
    /无效的数据结构/,
    "JSON 业务行结构损坏时必须失败关闭",
  );
  await assert.rejects(
    () => corruptProjection((rows) => rows.map((row) => row.section === "page" && row.section_index === 0
      ? { ...row, section: "unknown" }
      : row)),
    /无效分区或索引/,
    "未知投影分区必须失败关闭",
  );
  await assert.rejects(
    () => corruptProjection((rows) => {
      const recommendationRows = rows.filter((row) => row.section === "recommendation");
      if (recommendationRows.length < 2) return rows;
      const duplicateTarget = recommendationRows[1];
      return rows.map((row) => row === duplicateTarget ? { ...row, section_index: 0 } : row);
    }),
    /重复索引/,
    "重复投影索引必须失败关闭",
  );
  await assert.rejects(
    () => corruptProjection((rows) => rows.filter((row) => row.section !== "metrics")),
    /缺少唯一指标行/,
    "缺少指标分区必须失败关闭",
  );
  await assert.rejects(
    () => corruptProjection((rows) => {
      const metricsRow = rows.find((row) => row.section === "metrics");
      return metricsRow ? [metricsRow, { ...metricsRow }, ...rows.filter((row) => row !== metricsRow)] : rows;
    }),
    /重复索引/,
    "重复指标分区必须失败关闭",
  );

  const planSql: string[] = [];
  const planOverview = await getInventoryPlanOverview(
    sqliteAdapter(sqlite, (sql) => planSql.push(sql)) as never,
    { startDate: "2026-08-18", endDate: "2026-08-18", planPage: 1, planPageSize: 50 },
  );
  assert.equal(planOverview.projection, "plan");
  assert.equal(planOverview.plans.length, 1);
  assert.equal(planOverview.plans[0]?.id, "plan-a");
  assert.equal(planOverview.planSummary.draftCount, 1);
  assert.equal(planOverview.planSummary.activeQuantity, 12);
  assert.equal(planSql.filter((sql) => sql.includes("WITH stock AS (")).length, 0);
  assert.equal(planSql.some((sql) => sql.includes("SELECT COUNT(*) AS total FROM replenishment_plan_items")), true);
  assert.equal(planSql.some((sql) => sql.includes("AS draft_count")), true);

  const filteredPlanOverview = await getInventoryPlanOverview(db, {
    query: "货品A",
    warehouses: ["上海仓"],
    brands: ["品牌甲"],
    categories: ["类目A"],
    planPage: 1,
    planPageSize: 50,
  });
  assert.deepEqual(filteredPlanOverview.plans.map((plan) => plan.id), ["plan-a"]);
  assert.equal(filteredPlanOverview.planSummary.draftCount, 1);
  const mismatchedPlanOverview = await getInventoryPlanOverview(db, {
    brands: ["品牌乙"],
    planPage: 1,
    planPageSize: 50,
  });
  assert.deepEqual(mismatchedPlanOverview.plans, []);
  assert.equal(mismatchedPlanOverview.planSummary.draftCount, 0);

  const age = await getInventoryAgeAnalysis(db, { query: "A", page: 1, pageSize: 1 });
  assert.equal(age.pagination.total, 1);
  assert.equal(age.metrics.stockValueComplete, true);
  assert.equal(age.items[0]?.stockValueCents, 1_000);
  assert.equal(age.items[0]?.sales30dQuantity, null);
  sqlite.close();
});

test("库存 overview 推荐投影严格限制为排序后的前 50 条", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as never;
  ensureSalesFixtureSchema(sqlite);
  await Promise.all([ensureInventorySchema(db), ensureErpReferenceSchema(db)]);
  sqlite.prepare(`INSERT INTO sales_import_batches (
    id, source, file_name, file_size_bytes, file_hash, sheet_name, status, row_count, inserted_count, completed_at
  ) VALUES ('sales-batch', 'test', 'sales.xlsx', 1, 'sales-hash', 'Sheet1', 'completed', 55, 55, '2026-08-18 10:00:00')`).run();
  sqlite.prepare(`INSERT INTO inventory_import_batches (
    id, source, file_name, file_size_bytes, file_hash, sheet_name, snapshot_date, status,
    row_count, inserted_count, totals_json, completed_at
  ) VALUES ('inventory-batch', 'test', 'inventory.xlsx', 1, 'inventory-hash', 'Sheet1', '2026-08-18', 'completed', 55, 55, '{}', '2026-08-18 10:00:00')`).run();

  const expectedRecommendationCodes: string[] = [];
  for (let index = 1; index <= 55; index += 1) {
    const productCode = `R${String(index).padStart(3, "0")}`;
    insertSalesLine(sqlite, {
      productCode,
      productName: `推荐货品${index}`,
      category: "推荐测试",
      quantity: 1,
      netSalesCents: 100,
      costCents: 50,
    });
    insertStockLine(sqlite, {
      rowKey: `stock-${productCode}`,
      productCode,
      productName: `推荐货品${index}`,
      category: "推荐测试",
      availableQuantity: 0,
      unitCostCents: 100,
      ageDays: 0,
    });
    if (index <= 50) expectedRecommendationCodes.push(productCode);
  }

  const overview = await getInventoryOverview(db, {
    startDate: "2026-08-18",
    endDate: "2026-08-18",
    page: 1,
    pageSize: 1,
  });
  assert.equal(overview.metrics.skuWarehouseCount, 55);
  assert.equal(overview.metrics.recommendationCount, 55, "指标必须披露完整推荐总数而非截断数量");
  assert.equal(overview.recommendations.length, 50);
  assert.deepEqual(overview.recommendations.map((item) => item.productCode), expectedRecommendationCodes);
  assert.deepEqual(overview.items.map((item) => item.productCode), ["R001"]);
  sqlite.close();
});
