import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import test from "node:test";

const testEnvironment: { DB?: unknown } = {};
(globalThis as typeof globalThis & { __inventoryAgeQueryEnv?: typeof testEnvironment }).__inventoryAgeQueryEnv = testEnvironment;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return {
        url: "data:text/javascript,export const env=globalThis.__inventoryAgeQueryEnv;",
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});

const { getInventoryAgeAnalysis } = await import("../lib/inventory/age-analysis");

function sqliteAdapter(sqlite: DatabaseSync, preparedSql: string[]) {
  return {
    prepare(sql: string) {
      preparedSql.push(sql);
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
  };
}

function createFixture() {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE inventory_import_batches (
      id TEXT PRIMARY KEY, source TEXT NOT NULL, file_name TEXT NOT NULL, file_size_bytes INTEGER NOT NULL,
      file_hash TEXT NOT NULL, sheet_name TEXT NOT NULL, snapshot_date TEXT NOT NULL, status TEXT NOT NULL,
      row_count INTEGER NOT NULL, inserted_count INTEGER NOT NULL, warning_count INTEGER NOT NULL DEFAULT 0,
      warnings_json TEXT NOT NULL DEFAULT '[]', totals_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT
    );
    CREATE TABLE erp_reference_import_batches (
      id TEXT PRIMARY KEY, source_key TEXT NOT NULL, source_label TEXT NOT NULL, file_name TEXT NOT NULL,
      file_size_bytes INTEGER NOT NULL, file_hash TEXT NOT NULL, sheet_name TEXT NOT NULL, snapshot_date TEXT,
      status TEXT NOT NULL, row_count INTEGER NOT NULL DEFAULT 0, inserted_count INTEGER NOT NULL DEFAULT 0,
      updated_count INTEGER NOT NULL DEFAULT 0, excluded_count INTEGER NOT NULL DEFAULT 0,
      warning_count INTEGER NOT NULL DEFAULT 0, warnings_json TEXT NOT NULL DEFAULT '[]',
      totals_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT
    );
    CREATE TABLE erp_inventory_age_lines (
      id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_date TEXT NOT NULL, warehouse TEXT NOT NULL,
      warehouse_type TEXT NOT NULL, product_code TEXT NOT NULL, product_name TEXT NOT NULL DEFAULT '',
      specification TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '', available_quantity INTEGER NOT NULL,
      inventory_age_days INTEGER, sales_7d_quantity INTEGER, sales_30d_quantity INTEGER,
      unit_cost_cents INTEGER NOT NULL, stock_value_cents INTEGER NOT NULL, source_row_number INTEGER NOT NULL,
      last_import_batch_id TEXT NOT NULL
    );
    CREATE TABLE erp_product_master (
      product_code TEXT PRIMARY KEY, brand TEXT NOT NULL DEFAULT ''
    );
    INSERT INTO erp_reference_import_batches (
      id, source_key, source_label, file_name, file_size_bytes, file_hash, sheet_name, snapshot_date,
      status, row_count, inserted_count, completed_at
    ) VALUES (
      'age-batch', 'inventory_age', '库龄', 'age.xlsx', 1, 'age-hash', 'Sheet1', '2026-08-24',
      'completed', 7, 7, '2026-08-24 10:00:00'
    );
  `);

  const insert = sqlite.prepare(`INSERT INTO erp_inventory_age_lines (
    snapshot_date, warehouse, warehouse_type, product_code, product_name, specification, category,
    available_quantity, inventory_age_days, sales_7d_quantity, sales_30d_quantity,
    unit_cost_cents, stock_value_cents, source_row_number, last_import_batch_id
  ) VALUES ('2026-08-24', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'age-batch')`);
  const rows: Array<[string, string, string, string, string, string, number, number | null, number | null, number | null, number, number, number]> = [
    ["华东仓", "owned", "A1", "健康货品", "常规规格", "类目甲", 5, 20, 2, 10, 100, 500, 1],
    ["华东仓", "owned", "B1", "低动销货品", "目标规格", "类目甲", 6, 70, 1, 3, 100, 600, 2],
    ["华北仓", "jd_rdc", "C1", "滞销货品", "常规规格", "类目乙", 7, 100, 0, 0, 100, 700, 3],
    ["华北仓", "jd_rdc", "D1", "高库龄货品", "常规规格", "类目乙", 8, 100, 2, 10, 0, 0, 4],
    ["华南仓", "other", "E1", "零库存货品", "常规规格", "类目丙", 0, 200, 0, 0, 0, 0, 5],
    ["华南仓", "other", "F1", "待补库龄货品", "常规规格", "类目丙", 4, null, null, null, 100, 400, 6],
    ["刷刷仓", "other", "X1", "排除货品", "目标规格", "类目甲", 99, 200, 0, 0, 100, 9_900, 7],
  ];
  for (const row of rows) insert.run(...row);
  return sqlite;
}

const expectedMetrics = {
  skuWarehouseCount: 6,
  stockValueComplete: true,
  aged90Count: 2,
  aged90ValueCents: 700,
  stagnantCount: 1,
  stagnantValueCents: 700,
  zeroSalesCount: 1,
  cleanupCount: 3,
};

const expectedDistribution = [
  { key: "0-30", label: "0–30 天", count: 1, valueCents: 500 },
  { key: "31-60", label: "31–60 天", count: 0, valueCents: 0 },
  { key: "61-90", label: "61–89 天", count: 1, valueCents: 600 },
  { key: "90+", label: "90 天以上", count: 3, valueCents: 700 },
];

function ageCteQueries(sql: string[]) {
  return sql.filter((statement) => statement.includes("WITH base AS ("));
}

test("库龄常规分页通过窗口聚合保持完整指标，并把重型 CTE 从三次降为两次", async () => {
  const sqlite = createFixture();
  const preparedSql: string[] = [];
  const result = await getInventoryAgeAnalysis(sqliteAdapter(sqlite, preparedSql) as never, { page: 1, pageSize: 2 });

  assert.deepEqual(result.metrics, expectedMetrics);
  assert.deepEqual(result.distribution, expectedDistribution);
  assert.deepEqual(result.pagination, {
    page: 1, pageSize: 2, limit: 2, total: 6, returned: 2, totalPages: 3, truncated: true,
  });
  assert.deepEqual(result.items.map((item) => [item.productCode, item.status]), [
    ["C1", "stagnant"],
    ["B1", "slow"],
  ]);
  assert.deepEqual(new Set(result.filters.warehouses), new Set(["华东仓", "华北仓", "华南仓"]));

  const heavyQueries = ageCteQueries(preparedSql);
  assert.equal(heavyQueries.length, 2);
  assert.equal(heavyQueries.filter((sql) => sql.includes("COUNT(*) OVER () AS total")).length, 1);
  assert.equal(heavyQueries.filter((sql) => sql.includes("COUNT(*) AS total")).length, 0);
  sqlite.close();
});

test("库龄窗口聚合继续对查询、仓库与状态筛选采用同一口径", async () => {
  const sqlite = createFixture();
  const preparedSql: string[] = [];
  const result = await getInventoryAgeAnalysis(sqliteAdapter(sqlite, preparedSql) as never, {
    query: "目标规格",
    warehouses: ["华东仓"],
    statuses: ["slow"],
    page: 1,
    pageSize: 10,
  });

  assert.deepEqual(result.metrics, {
    skuWarehouseCount: 1,
    stockValueComplete: true,
    aged90Count: 0,
    aged90ValueCents: 0,
    stagnantCount: 0,
    stagnantValueCents: 0,
    zeroSalesCount: 0,
    cleanupCount: 1,
  });
  assert.deepEqual(result.distribution, [
    { key: "0-30", label: "0–30 天", count: 0, valueCents: 0 },
    { key: "31-60", label: "31–60 天", count: 0, valueCents: 0 },
    { key: "61-90", label: "61–89 天", count: 1, valueCents: 600 },
    { key: "90+", label: "90 天以上", count: 0, valueCents: 0 },
  ]);
  assert.equal(result.items[0]?.productCode, "B1");
  assert.equal(ageCteQueries(preparedSql).length, 2);
  sqlite.close();
});

test("库龄空结果与越界页仅触发一次有界指标回退并保持原分页语义", async () => {
  const sqlite = createFixture();

  const emptySql: string[] = [];
  const empty = await getInventoryAgeAnalysis(sqliteAdapter(sqlite, emptySql) as never, {
    query: "完全不存在",
    page: 1,
    pageSize: 2,
  });
  assert.deepEqual(empty.metrics, {
    skuWarehouseCount: 0,
    stockValueComplete: true,
    aged90Count: 0,
    aged90ValueCents: 0,
    stagnantCount: 0,
    stagnantValueCents: 0,
    zeroSalesCount: 0,
    cleanupCount: 0,
  });
  assert.deepEqual(empty.distribution.map((bucket) => [bucket.count, bucket.valueCents]), [[0, 0], [0, 0], [0, 0], [0, 0]]);
  assert.deepEqual(empty.pagination, {
    page: 1, pageSize: 2, limit: 2, total: 0, returned: 0, totalPages: 0, truncated: false,
  });
  assert.equal(ageCteQueries(emptySql).length, 3);
  assert.equal(ageCteQueries(emptySql).filter((sql) => sql.includes("COUNT(*) AS total")).length, 1);

  const overflowSql: string[] = [];
  const overflow = await getInventoryAgeAnalysis(sqliteAdapter(sqlite, overflowSql) as never, {
    page: 4,
    pageSize: 2,
  });
  assert.deepEqual(overflow.metrics, expectedMetrics);
  assert.deepEqual(overflow.distribution, expectedDistribution);
  assert.deepEqual(overflow.pagination, {
    page: 4, pageSize: 2, limit: 2, total: 6, returned: 0, totalPages: 3, truncated: false,
  });
  assert.deepEqual(overflow.items, []);
  assert.equal(ageCteQueries(overflowSql).length, 3);
  assert.equal(ageCteQueries(overflowSql).filter((sql) => sql.includes("COUNT(*) AS total")).length, 1);
  sqlite.close();
});
