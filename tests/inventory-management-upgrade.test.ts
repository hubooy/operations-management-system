import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import test from "node:test";
import type { AppPrincipal } from "../lib/auth/authorization";
import type { SalesConsumerReader } from "../lib/django/sales-consumer-reader";

const testEnvironment: { DB?: unknown } = {};
(globalThis as typeof globalThis & { __inventoryManagementUpgradeEnv?: typeof testEnvironment }).__inventoryManagementUpgradeEnv = testEnvironment;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return {
        url: "data:text/javascript,export const env=globalThis.__inventoryManagementUpgradeEnv;",
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});

const { validateInventoryImportRows, assessInventoryOverviewQuality } = await import("../lib/inventory/data-quality");
const { getInventoryAgeAnalysis } = await import("../lib/inventory/age-analysis");
const { getInventoryInboundMonitor } = await import("../lib/inventory/inbound-monitor");
const { createInventoryWorkItem } = await import("../lib/inventory/work-items");
const { inferInventoryWarehouseType } = await import("../lib/inventory/warehouse-classification");

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

function futureShanghaiDate(days: number) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(Date.now() + days * 86_400_000));
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${value("year")}-${value("month")}-${value("day")}`;
}

function inventoryRow(overrides: Record<string, unknown> = {}) {
  return {
    sourceRowNumber: 2,
    rowKey: "华东仓\u001fP1",
    snapshotDate: "2026-08-24",
    warehouse: "华东仓",
    warehouseType: "owned" as const,
    warehouseCategory: "selfOperated" as const,
    includeInInventory: true,
    productCode: "P1",
    productName: "测试货品",
    brand: "",
    supplier: "供应商甲",
    specification: "",
    barcode: "",
    category: "测试类目",
    onHandQuantity: 10,
    availableQuantity: 10,
    lockedQuantity: 0,
    inTransitQuantity: 0,
    unitCostCents: 100,
    inventoryAgeDays: 20,
    sales7dQuantity: 1,
    sales30dQuantity: 3,
    ...overrides,
  };
}

test("库存导入质量门禁受负库存设置控制并拒绝汇总行与 Excel 日期序号库龄", () => {
  const negative = inventoryRow({ onHandQuantity: -1, availableQuantity: -1 });
  assert.equal(validateInventoryImportRows([negative], { allowNegativeInventory: false })[0]?.code, "NEGATIVE_INVENTORY_NOT_ALLOWED");
  assert.equal(validateInventoryImportRows([negative], { allowNegativeInventory: true }).length, 0);
  assert.equal(validateInventoryImportRows([inventoryRow({ productCode: "合计" })], { allowNegativeInventory: true })[0]?.code, "AGGREGATE_ROW_NOT_ALLOWED");
  assert.equal(validateInventoryImportRows([inventoryRow({ inventoryAgeDays: 45_000 })], { allowNegativeInventory: true })[0]?.code, "IMPLAUSIBLE_INVENTORY_AGE");
});

test("销量映射低于 60% 或自动补货关闭时暂停精确建议", () => {
  const lowCoverage = assessInventoryOverviewQuality({ hasInventory: true, salesDemandMatchRate: 0.59, skuWarehouseCount: 100, totalAvailableQuantity: 1000, knownStockValueCents: 100_000, inventoryStale: false, autoReplenishment: true });
  assert.equal(lowCoverage.status, "blocked");
  assert.equal(lowCoverage.recommendationsSuppressed, true);
  assert.equal(lowCoverage.issues[0]?.code, "LOW_SALES_MAPPING_COVERAGE");
  const disabled = assessInventoryOverviewQuality({ hasInventory: true, salesDemandMatchRate: 1, skuWarehouseCount: 1, totalAvailableQuantity: 1, knownStockValueCents: 100, inventoryStale: false, autoReplenishment: false });
  assert.equal(disabled.issues.some((issue) => issue.code === "AUTO_REPLENISHMENT_DISABLED"), true);
});

test("库龄新增 10 个数量/货值区间且保留旧四区间兼容契约", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE inventory_import_batches (
      id TEXT PRIMARY KEY, source TEXT, file_name TEXT, file_size_bytes INTEGER, file_hash TEXT, sheet_name TEXT,
      snapshot_date TEXT, status TEXT, row_count INTEGER, inserted_count INTEGER, warning_count INTEGER,
      warnings_json TEXT, totals_json TEXT, created_at TEXT, completed_at TEXT
    );
    CREATE TABLE erp_reference_import_batches (
      id TEXT PRIMARY KEY, source_key TEXT, source_label TEXT, file_name TEXT, file_size_bytes INTEGER, file_hash TEXT,
      sheet_name TEXT, snapshot_date TEXT, status TEXT, row_count INTEGER, inserted_count INTEGER, updated_count INTEGER,
      excluded_count INTEGER, warning_count INTEGER, warnings_json TEXT, totals_json TEXT, created_at TEXT, completed_at TEXT
    );
    CREATE TABLE erp_inventory_age_lines (
      id INTEGER PRIMARY KEY, snapshot_date TEXT, warehouse TEXT, warehouse_type TEXT, product_code TEXT, product_name TEXT,
      specification TEXT, category TEXT, available_quantity INTEGER, inventory_age_days INTEGER, sales_7d_quantity INTEGER,
      sales_30d_quantity INTEGER, unit_cost_cents INTEGER, stock_value_cents INTEGER, source_row_number INTEGER,
      last_import_batch_id TEXT
    );
    CREATE TABLE erp_product_master (
      product_code TEXT PRIMARY KEY, brand TEXT NOT NULL DEFAULT ''
    );
    INSERT INTO erp_reference_import_batches VALUES ('age-batch','inventory_age','库龄','age.xlsx',1,'hash','Sheet1','2026-08-24','completed',10,10,0,0,0,'[]','{}','2026-08-24','2026-08-24');
  `);
  const ages = [3, 10, 20, 40, 70, 100, 130, 160, 200, 400];
  const insert = sqlite.prepare(`INSERT INTO erp_inventory_age_lines VALUES (?, '2026-08-24', ?, 'owned', ?, ?, '', '类目', ?, ?, 1, 1, 100, ?, ?, 'age-batch')`);
  ages.forEach((age, index) => insert.run(index + 1, index === 9 ? "武汉常温C中件消费品15号库-CHN" : "华东仓", `P${index + 1}`, `货品${index + 1}`, index + 1, age, (index + 1) * 100, index + 1));
  const result = await getInventoryAgeAnalysis(sqliteAdapter(sqlite) as never, { page: 1, pageSize: 20 });
  assert.equal(result.distribution.length, 4);
  assert.deepEqual(result.fineDistribution.map((bucket) => bucket.key), ["0-7", "8-15", "16-30", "31-60", "61-90", "91-120", "121-150", "151-180", "181-360", "361+"]);
  assert.deepEqual(result.fineDistribution.map((bucket) => bucket.quantity), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  assert.ok(Math.abs(result.fineDistribution.reduce((sum, bucket) => sum + bucket.quantityShare, 0) - 1) < 1e-9);
  assert.equal(result.items.find((item) => item.productCode === "P10")?.warehouseType, "jd_rdc");
  sqlite.close();
});

test("京东入仓监控只统计 RDC/DC，并披露固定成本与原生指标缺口", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(`
    CREATE TABLE inventory_import_batches (
      id TEXT PRIMARY KEY, source TEXT, file_name TEXT, file_size_bytes INTEGER, file_hash TEXT, sheet_name TEXT,
      snapshot_date TEXT, status TEXT, row_count INTEGER, inserted_count INTEGER, warning_count INTEGER,
      warnings_json TEXT, totals_json TEXT, created_at TEXT, completed_at TEXT
    );
    CREATE TABLE inventory_stock_lines (
      batch_id TEXT, product_code TEXT, product_name TEXT, brand TEXT, category TEXT, warehouse TEXT, warehouse_type TEXT,
      available_quantity INTEGER, in_transit_quantity INTEGER, inventory_age_days INTEGER, unit_cost_cents INTEGER
    );
    CREATE TABLE erp_product_master (product_code TEXT PRIMARY KEY, supplier TEXT);
    INSERT INTO inventory_import_batches VALUES ('stock-batch','inventory_stock','stock.xlsx',1,'hash','Sheet1','2026-08-24','completed',4,4,0,'[]','{}','2026-08-24','2026-08-24');
    INSERT INTO inventory_stock_lines VALUES
      ('stock-batch','P1','入仓货品1','品牌甲','类目甲','华东RDC仓','jd_rdc',30,5,20,100),
      ('stock-batch','P2','入仓货品2','品牌乙','类目乙','华北DC仓','jd_rdc',10,0,100,200),
      ('stock-batch','P3','自有货品','品牌甲','类目甲','自有仓','owned',99,0,10,100),
      ('stock-batch','P4','历史平台仓货品','品牌乙','类目乙','上海常温C平台仓9号库-CHN','owned',8,1,30,300);
    INSERT INTO erp_product_master VALUES ('P1','供应商甲'),('P2',''),('P4','供应商乙');
  `);
  const principal: AppPrincipal = { email: "admin@example.test", displayName: "Admin", role: "admin", scope: null };
  let consumerCalls = 0;
  const salesReader: SalesConsumerReader = {
    read: (async (_principal: AppPrincipal, request: Record<string, unknown>) => {
      consumerCalls += 1;
      assert.equal(request.operation, "inventory_inbound_windows");
      return {
        revision: "7:3",
        data: {
          asOfDate: "2026-08-24",
          dataStartDate: "2026-08-01",
          dataCutoffDate: "2026-08-24",
          rows: [{
            productCode: "P1",
            warehouseKey: "华东rdc",
            productName: "入仓货品1",
            sales7dQuantity: 6,
            sales30dQuantity: 6,
            sales90dQuantity: 6,
          }],
          truncated: false,
        },
      };
    }) as SalesConsumerReader["read"],
  };
  const result = await getInventoryInboundMonitor(
    sqliteAdapter(sqlite) as never,
    principal,
    { page: 1, pageSize: 20 },
    salesReader,
  );
  assert.equal(consumerCalls, 1, "每次入仓请求必须只读取一次 7/30/90 同 revision Django 聚合");
  assert.equal(result.metrics.itemCount, 3);
  assert.equal(result.metrics.warehouseCount, 3);
  assert.equal(result.metrics.missingSupplierCount, 1);
  assert.equal(result.items.some((item) => item.productCode === "P3"), false);
  assert.equal(result.items.some((item) => item.productCode === "P4"), true);
  assert.equal(inferInventoryWarehouseType("武汉常温C中件消费品15号库-CHN"), "jd_rdc");
  assert.equal(inferInventoryWarehouseType("广东仓"), "owned");
  assert.equal(result.scope.supplyPriceAvailable, false);
  assert.equal(result.scope.nativeComparisonAvailable, false);
  assert.match(result.disclosures.join("\n"), /固定成本|原生库存/);
  assert.deepEqual(result.filters.brands, ["品牌乙", "品牌甲"]);
  assert.deepEqual(result.filters.categories, ["类目乙", "类目甲"]);
  const supplierFiltered = await getInventoryInboundMonitor(
    sqliteAdapter(sqlite) as never,
    principal,
    { suppliers: ["供应商乙"], page: 1, pageSize: 20 },
    salesReader,
  );
  assert.equal(supplierFiltered.metrics.itemCount, 1);
  assert.deepEqual(supplierFiltered.regions.map((region) => region.warehouse), ["上海常温C平台仓9号库-CHN"]);
  const commonFiltered = await getInventoryInboundMonitor(
    sqliteAdapter(sqlite) as never,
    principal,
    {
      brands: ["品牌乙"],
      categories: ["类目乙"],
      warehouses: ["华北DC仓"],
      page: 1,
      pageSize: 20,
    },
    salesReader,
  );
  assert.equal(consumerCalls, 3);
  assert.deepEqual(commonFiltered.items.map((item) => [item.productCode, item.brand, item.category]), [["P2", "品牌乙", "类目乙"]]);
  sqlite.close();
});

test("已确认补货计划幂等创建采购执行事项并关联原计划", async () => {
  const principal: AppPrincipal = {
    email: "operator@example.com",
    displayName: "Operator",
    role: "operator",
    scope: null,
  };
  const inventoryReader = {
    read: async () => ({
      revision: "1:000000000000",
      data: {
        plan: {
          id: "plan-1",
          productCode: "P1",
          productName: "采购货品",
          warehouse: "华东仓",
          plannedQuantity: 18,
          coverageDays: 5,
          status: "confirmed",
        },
        supplier: "供应商甲",
      },
    }),
  } as never;
  let savedTask: { id: string; title: string; status: string } | null = null;
  const savedPayloads: Record<string, unknown>[] = [];
  const workflowWriter = {
    create: async (_principal: AppPrincipal, payload: Record<string, unknown>) => {
      if (savedTask) return { created: false, task: savedTask };
      savedPayloads.push(payload);
      savedTask = { id: "task-pg-1", title: String(payload.title), status: "待开始" };
      return { created: true, task: savedTask };
    },
  };
  const expectedArrivalDate = futureShanghaiDate(7);
  const first = await createInventoryWorkItem({ kind: "procurement", planId: "plan-1", owner: "采购组", planType: "daily", expectedArrivalDate, dueDate: expectedArrivalDate }, principal, inventoryReader, workflowWriter);
  const second = await createInventoryWorkItem({ kind: "procurement", planId: "plan-1", owner: "采购组", planType: "daily", expectedArrivalDate, dueDate: expectedArrivalDate }, principal, inventoryReader, workflowWriter);
  assert.equal(first.created, true);
  assert.equal(second.created, false);
  assert.equal(savedPayloads[0]?.entityId, "replenishment-plan:plan-1");
  assert.equal(savedPayloads[0]?.entityType, "product");
  assert.match(String(savedPayloads[0]?.workContent), /供应商甲|计划数量：18/);
});
