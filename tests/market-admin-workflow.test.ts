import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";

import type { AppPrincipal } from "../lib/auth/authorization";
import { executeToolCallWithRegistry, getOpenAiTools, type AiToolEntry } from "../lib/ai/tool-registry-contract";
import {
  applyPublishedMarketMappings,
  confirmMarketBrand,
  confirmMarketPrice,
  createMarketBrandRecognitionJob,
  createMarketPriceBandVersion,
  getMarketSkuComparison,
  getMarketBrandRecognitionJob,
  getMarketBrandSeedWorkspace,
  getMarketMasterDatabaseFilters,
  getMarketMasterDatabasePrimary,
  getMarketMasterDatabaseSecondary,
  getMarketMasterWorkspace,
  getMarketSubcategoryWorkspace,
  getMarketSystemKpis,
  listMarketMasterData,
  listPendingMarketPrices,
  matchMarketBrandSeeds,
  planMissingMarketDownloads,
  publishMarketPriceBandVersion,
  recordMarketDownloadAttempt,
  refreshMarketBrandSeeds,
  rollbackMarketPriceBandVersion,
  setMarketBrandRecognitionJobStatus,
  saveMarketSubcategorySettings,
  updateMarketSkuMasterData,
  upsertMarketDownloadConfig,
  upsertMarketBrandSeed,
  upsertMarketMapping,
} from "../lib/market/admin-service";
import { ensureAnnotationSchema } from "../lib/market/annotation-schema";
import { ensureMarketMasterIdentities, refreshMarketMasterIdentities } from "../lib/market/master-identity";
import { executeMarketDownloadTask } from "../lib/market/download-executor";
import { matchImportedMarketBrands, matchMarketBrandTitle, refreshSystemMarketBrandSeeds, type MarketBrandSeed } from "../lib/market/brand-seeds";
import type { MarketEntryForImport } from "../lib/market/import-core";
import { ensureMarketSchemaCached, ensureMarketSchemaCore, officialPriceBandSql, type MarketSchemaDatabase } from "../lib/market/schema-core";

function sqliteAdapter(sqlite: DatabaseSync, hooks: {
  beforeRun?: (sql: string) => Promise<void>;
  afterRun?: (sql: string) => Promise<void>;
  afterFirst?: (sql: string) => Promise<void>;
  beforeAll?: (sql: string) => Promise<void>;
  afterAll?: (sql: string) => Promise<void>;
  afterBatch?: () => Promise<void>;
} = {}): MarketSchemaDatabase {
  return {
    prepare(sql: string) {
      const statement = sqlite.prepare(sql);
      let values: unknown[] = [];
      return {
        bind(...nextValues: unknown[]) { values = nextValues; return this; },
        async first<T>() {
          const result = (statement.get(...values) ?? null) as T | null;
          await hooks.afterFirst?.(sql);
          return result;
        },
        async all<T>() {
          await hooks.beforeAll?.(sql);
          const results = statement.all(...values) as T[];
          await hooks.afterAll?.(sql);
          return { results };
        },
        async run() {
          await hooks.beforeRun?.(sql);
          const result = statement.run(...values);
          await hooks.afterRun?.(sql);
          return { meta: { changes: Number(result.changes) } };
        },
      };
    },
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      sqlite.exec("BEGIN");
      const output = [];
      try {
        for (const statement of statements) output.push(await statement.run());
        sqlite.exec("COMMIT");
      } catch (error) {
        sqlite.exec("ROLLBACK");
        throw error;
      }
      await hooks.afterBatch?.();
      return output;
    },
  };
}

const admin = { email: "admin@example.com", role: "admin" } as const;

test("market master GMV totals prefer full-month coverage, ignore rolling windows, and use child bands only as monthly fallback", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key,source_row_number,period_start,period_end,category,scope,price_band_filter,ranking_dimension,operation_mode,sku_code,product_name,gmv_cents,raw_json,last_import_batch_id)
    VALUES
    ('a-month',1,'2026-06-01','2026-06-30','净水','POP','全部','SKU','POP','SKU-A','A',1000,'{}','batch'),
    ('a-day-1',2,'2026-06-01','2026-06-01','净水','POP','全部','SKU','POP','SKU-A','A',100,'{}','batch'),
    ('a-day-2',3,'2026-06-02','2026-06-02','净水','POP','全部','SKU','POP','SKU-A','A',100,'{}','batch'),
    ('a-rolling',4,'2026-07-02','2026-07-31','净水','POP','全部','SKU','POP','SKU-A','A',999999,'{}','batch'),
    ('b-basis',5,'2026-07-01','2026-07-01','净水','POP','全部','SKU','POP','SKU-B','B',100,'{}','batch'),
    ('b-child-1',6,'2026-07-01','2026-07-01','净水','POP','0-500','SKU','POP','SKU-B','B',1000,'{}','batch'),
    ('b-child-2',7,'2026-07-02','2026-07-02','净水','POP','0-500','SKU','POP','SKU-B','B',1000,'{}','batch'),
    ('c-child-1',8,'2026-07-01','2026-07-01','净水','POP','0-500','SKU','POP','SKU-C','C',1000,'{}','batch'),
    ('c-child-2',9,'2026-07-02','2026-07-02','净水','POP','0-500','SKU','POP','SKU-C','C',1000,'{}','batch');`);
  const result = await listMarketMasterData(db as never, { pageSize: 20 });
  const totals = Object.fromEntries(result.items.map((row) => [row.skuCode, row.gmvTotalCents]));
  assert.deepEqual(totals, { "SKU-C": 2000, "SKU-A": 1000, "SKU-B": 100 });
  sqlite.close();
});

test("system settings KPIs split every pending AI identity into one highest-cost route", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await ensureAnnotationSchema(db as never);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,sku_code,product_name,raw_json,last_import_batch_id)
    VALUES
    ('reuse-june',1,'2026-06-01','2026-06-30','category-reuse','POP','SKU','POP','SKU-REUSE','Reuse June','{}','batch'),
    ('reuse-july',2,'2026-07-01','2026-07-31','category-reuse','POP','SKU','POP','SKU-REUSE','Reuse July','{}','batch'),
    ('price-july',3,'2026-07-01','2026-07-31','category-price','POP','SKU','POP','SKU-PRICE','Price only','{}','batch'),
    ('full-july',4,'2026-07-01','2026-07-31','category-full','POP','SKU','POP','SKU-FULL','Full','{}','batch'),
    ('full-august',10,'2026-08-01','2026-08-31','category-full','POP','SKU','POP','SKU-FULL','Full new image','{}','batch'),
    ('blocked-image',5,'2026-07-01','2026-07-31','category-no-image','POP','SKU','POP','SKU-NO-IMAGE','No image','{}','batch'),
    ('blocked-spu',6,'2026-07-01','2026-07-31','category-spu','self','SPU','自营','SPU-BLOCKED','SPU','{}','batch'),
    ('blocked-prompt',7,'2026-07-01','2026-07-31','category-no-prompt','POP','SKU','POP','SKU-NO-PROMPT','No prompt','{}','batch'),
    ('blocked-terminal',8,'2026-07-01','2026-07-31','category-terminal','POP','SKU','POP','SKU-TERMINAL','Terminal','{}','batch'),
    ('completed-ai',9,'2026-07-01','2026-07-31','category-completed','POP','SKU','POP','SKU-COMPLETED','Completed','{}','batch'),
    ('cross-month-result',11,'2026-07-01','2026-07-31','category-cross-month','POP','SKU','POP','SKU-CROSS','Cross month result','{}','batch'),
    ('cross-month-blocked',12,'2026-08-01','2026-08-31','category-cross-month','POP','SKU','POP','SKU-CROSS','Cross month missing image','{}','batch');
    INSERT INTO market_annotation_prompt_versions
      (id,category,version,source,status,segments_json,prompt_body,created_by)
    VALUES
      ('prompt-reuse','category-reuse',1,'manual','active','["segment-reuse","other"]','A reusable prompt body long enough for testing.','admin'),
      ('prompt-price','category-price',1,'manual','active','["segment-price","other"]','A price-only prompt body long enough for testing.','admin'),
      ('prompt-full','category-full',1,'manual','active','["segment-full","other"]','A full prompt body long enough for testing.','admin'),
      ('prompt-no-image','category-no-image',1,'manual','active','["segment-image","other"]','A no-image prompt body long enough for testing.','admin'),
      ('prompt-spu','category-spu',1,'manual','active','["segment-spu","other"]','A SPU prompt body long enough for testing.','admin'),
      ('prompt-terminal','category-terminal',1,'manual','active','["segment-terminal","other"]','A terminal prompt body long enough for testing.','admin'),
      ('prompt-cross-month','category-cross-month',1,'manual','active','["segment-cross","other"]','A cross-month prompt body long enough for testing.','admin');
    INSERT INTO market_price_snapshots
    (id,category,scope,sku_code,ranking_dimension,month,image_content_sha256,confirmed_market_price_cents,confirmation_status,ai_price_type)
    VALUES
    ('reuse-confirmed','category-reuse','POP','SKU-REUSE','SKU','2026-06','hash-reuse',10000,'confirmed','标准售价'),
    ('reuse-pending','category-reuse','POP','SKU-REUSE','SKU','2026-07','hash-reuse',NULL,'review_pending',''),
    ('price-pending','category-price','POP','SKU-PRICE','SKU','2026-07','hash-price-new',NULL,'review_pending',''),
    ('full-confirmed','category-full','POP','SKU-FULL','SKU','2026-06','hash-full-old',10000,'confirmed','标准售价'),
    ('full-reuse-pending','category-full','POP','SKU-FULL','SKU','2026-07','hash-full-old',NULL,'review_pending',''),
    ('full-pending','category-full','POP','SKU-FULL','SKU','2026-08','hash-full-new',NULL,'review_pending',''),
    ('image-pending','category-no-image','POP','SKU-NO-IMAGE','SKU','2026-07','',NULL,'review_pending',''),
    ('spu-pending','category-spu','self','SPU-BLOCKED','SPU','2026-07','hash-spu',NULL,'review_pending',''),
    ('prompt-pending','category-no-prompt','POP','SKU-NO-PROMPT','SKU','2026-07','hash-no-prompt',NULL,'review_pending',''),
    ('terminal-pending','category-terminal','POP','SKU-TERMINAL','SKU','2026-07','hash-terminal',NULL,'review_pending',''),
    ('completed-pending','category-completed','POP','SKU-COMPLETED','SKU','2026-07','hash-completed',NULL,'review_pending',''),
    ('cross-result','category-cross-month','POP','SKU-CROSS','SKU','2026-07','hash-cross',NULL,'review_pending',''),
    ('cross-blocked','category-cross-month','POP','SKU-CROSS','SKU','2026-08','',NULL,'review_pending','');
    INSERT INTO market_annotation_items
    (id,job_id,category,scope,sku_code,ranking_dimension,month,image_content_sha256,status,ai_segment,reviewed_segment,attempt_count)
    VALUES
    ('price-history','job-price','category-price','POP','SKU-PRICE','SKU','2026-06','hash-price-old','committed','','segment-price',1),
    ('full-old-result','job-full-old','category-full','POP','SKU-FULL','SKU','2026-07','hash-full-old','review_pending','segment-full','segment-full',1),
    ('terminal-failure','job-terminal','category-terminal','POP','SKU-TERMINAL','SKU','2026-07','hash-terminal','failed','','',3),
    ('completed-result','job-completed','category-completed','POP','SKU-COMPLETED','SKU','2026-07','hash-completed','review_pending','segment-completed','segment-completed',1),
    ('cross-result-item','job-cross','category-cross-month','POP','SKU-CROSS','SKU','2026-07','hash-cross','committed','segment-cross','segment-cross',1);`);

  const kpis = await getMarketSystemKpis(db as never);
  assert.equal(kpis.sameImageReuseCount + kpis.priceOnlyRecognitionCount + kpis.fullRecognitionCount + kpis.blockedRecognitionCount, kpis.pendingAiCount);
  assert.deepEqual(kpis, {
    marketIdentityTotal: 9,
    pendingPriceCount: 9,
    pendingAiCount: 8,
    completedAiCount: 1,
    sameImageReuseCount: 1,
    priceOnlyRecognitionCount: 1,
    fullRecognitionCount: 1,
    blockedRecognitionCount: 5,
  });
  sqlite.close();
});

test("market master identity cache selects the latest row and refreshes after imports", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,sku_code,product_name,raw_json,last_import_batch_id)
    VALUES
    ('identity-old',1,'2026-05-01','2026-05-31','category-cache','POP','SKU','POP','SKU-CACHE','Old','{}','batch'),
    ('identity-new',2,'2026-06-01','2026-06-30','category-cache','POP','SKU','POP','SKU-CACHE','New','{}','batch');`);
  await ensureMarketMasterIdentities(db);
  assert.equal((sqlite.prepare(`SELECT m.product_name productName FROM market_master_identities identity
    JOIN market_ranking_entries m ON m.id=identity.latest_entry_id`).get() as { productName: string }).productName, "New");

  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,sku_code,product_name,raw_json,last_import_batch_id)
    VALUES ('identity-latest',3,'2026-07-01','2026-07-31','category-cache','POP','SKU','POP','SKU-CACHE','Latest','{}','batch');`);
  await refreshMarketMasterIdentities(db);
  assert.equal((sqlite.prepare(`SELECT m.product_name productName FROM market_master_identities identity
    JOIN market_ranking_entries m ON m.id=identity.latest_entry_id`).get() as { productName: string }).productName, "Latest");
  sqlite.close();
});

test("pending market prices filter displayed AI sources and paginate non-AI sources", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec("CREATE TABLE netshop_rows (source TEXT, dataset TEXT, business_date TEXT, sku_id TEXT, spu_id TEXT, product_code TEXT, metrics_json TEXT)");
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, sku_code, product_name, brand, raw_json, last_import_batch_id)
    VALUES
    ('price-ai',1,'2026-06-01','2026-06-30','category-price','pop','SKU','POP','SKU-AI','AI price product','','{}','batch'),
    ('price-source',2,'2026-06-01','2026-06-30','category-price','pop','SKU','POP','SKU-SOURCE','Source price product','','{}','batch'),
    ('price-average',3,'2026-06-01','2026-06-30','category-price','pop','SKU','POP','SKU-AVERAGE','Average price product','','{}','batch'),
    ('price-missing',4,'2026-06-01','2026-06-30','category-price','pop','SKU','POP','SKU-MISSING','Missing price product','','{}','batch');
    INSERT INTO market_price_snapshots
    (id, category, scope, sku_code, ranking_dimension, month, source_price_cents, average_transaction_price_cents, ai_image_price_cents, confirmation_status)
    VALUES
    ('snapshot-ai','category-price','pop','SKU-AI','SKU','2026-06',NULL,500000,1150000,'ai_pending'),
    ('snapshot-source','category-price','pop','SKU-SOURCE','SKU','2026-06',769900,NULL,NULL,'source_table'),
    ('snapshot-average','category-price','pop','SKU-AVERAGE','SKU','2026-06',NULL,233300,NULL,'review_pending'),
    ('snapshot-missing','category-price','pop','SKU-MISSING','SKU','2026-06',NULL,NULL,NULL,'missing');`);

  const ai = await listPendingMarketPrices(db as never, { category: "category-price", candidatePriceSource: "ai", page: 1, pageSize: 20 });
  assert.equal(ai.pagination.total, 1);
  assert.equal(ai.items[0]?.skuCode, "SKU-AI");
  assert.equal(ai.items[0]?.candidatePriceSource, "ai_suggestion");

  const nonAiFirst = await listPendingMarketPrices(db as never, { category: "category-price", candidatePriceSource: "non_ai", page: 1, pageSize: 1 });
  const nonAiSecond = await listPendingMarketPrices(db as never, { category: "category-price", candidatePriceSource: "non_ai", page: 2, pageSize: 1 });
  assert.equal(nonAiFirst.pagination.total, 2);
  assert.equal(nonAiFirst.pagination.pageCount, 2);
  assert.equal(nonAiFirst.items.length, 1);
  assert.equal(nonAiSecond.items.length, 1);
  assert.notEqual(nonAiFirst.items[0]?.skuCode, nonAiSecond.items[0]?.skuCode);
  assert.ok(nonAiFirst.items.every((item) => item.candidatePriceSource !== "ai_suggestion"));
  assert.ok(nonAiSecond.items.every((item) => item.candidatePriceSource !== "ai_suggestion"));
  const allKnownSources = await listPendingMarketPrices(db as never, { category: "category-price", candidatePriceSources: ["ai", "non_ai"], page: 1, pageSize: 20 });
  assert.equal(allKnownSources.pagination.total, 3);
  assert.ok(allKnownSources.items.every((item) => item.candidatePriceSource !== "missing"));
  sqlite.close();
});

test("pending market prices keep one representative per identity month while retaining older months", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, sku_code, product_name, brand, gmv_cents, raw_json, last_import_batch_id)
    VALUES
    ('pending-history-june-day',1,'2026-06-15','2026-06-15','history-price','POP','SKU','POP','SKU-HISTORY','June day','',100,'{}','batch'),
    ('pending-history-june-month',2,'2026-06-01','2026-06-30','history-price','POP','SKU','POP','SKU-HISTORY','June month','',200,'{}','batch'),
    ('pending-history-may-month',3,'2026-05-01','2026-05-31','history-price','POP','SKU','POP','SKU-HISTORY','May month','',300,'{}','batch');
    INSERT INTO market_price_snapshots
    (id, category, scope, sku_code, ranking_dimension, month, source_price_cents, confirmation_status)
    VALUES
    ('pending-history-june','history-price','POP','SKU-HISTORY','SKU','2026-06',10000,'source_table'),
    ('pending-history-may','history-price','POP','SKU-HISTORY','SKU','2026-05',9000,'source_table');`);

  const pending = await listPendingMarketPrices(db as never, { category: "history-price", page: 1, pageSize: 20 });
  assert.deepEqual(pending.pagination, { page: 1, pageSize: 20, total: 2, pageCount: 1 });
  assert.deepEqual(pending.items.map((item) => item.month), ["2026-06", "2026-05"]);
  assert.equal(pending.items.find((item) => item.month === "2026-06")?.productName, "June month");
  sqlite.close();
});

test("pending market prices clamp an emptied or category-shrunk page and preserve the real total", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  const insertEntry = sqlite.prepare(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, sku_code, product_name, brand, gmv_cents, raw_json, last_import_batch_id)
    VALUES (?,?,'2026-06-01','2026-06-30',?,'POP','SKU','POP',?,?,?,?,?,'batch')`);
  const insertSnapshot = sqlite.prepare(`INSERT INTO market_price_snapshots
    (id, category, scope, sku_code, ranking_dimension, month, source_price_cents, confirmation_status)
    VALUES (?,?,'POP',?,'SKU','2026-06',10000,'source_table')`);
  for (let index = 1; index <= 21; index += 1) {
    const skuCode = `SKU-PAGE-${String(index).padStart(2, "0")}`;
    insertEntry.run(`pending-page-${index}`, index, "page-price", skuCode, skuCode, "", 10_000 - index, "{}");
    insertSnapshot.run(`pending-page-snapshot-${index}`, "page-price", skuCode);
  }
  for (let index = 1; index <= 3; index += 1) {
    const skuCode = `SKU-SMALL-${index}`;
    insertEntry.run(`pending-small-${index}`, 100 + index, "small-price", skuCode, skuCode, "", 100 - index, "{}");
    insertSnapshot.run(`pending-small-snapshot-${index}`, "small-price", skuCode);
  }

  const lastPage = await listPendingMarketPrices(db as never, { category: "page-price", page: 2, pageSize: 20 });
  assert.deepEqual(lastPage.pagination, { page: 2, pageSize: 20, total: 21, pageCount: 2 });
  assert.equal(lastPage.items.length, 1);
  sqlite.prepare(`UPDATE market_price_snapshots SET confirmed_market_price_cents=10000, confirmation_status='confirmed'
    WHERE category='page-price' AND sku_code=?`).run(lastPage.items[0]?.skuCode);

  const afterConfirmation = await listPendingMarketPrices(db as never, { category: "page-price", page: 2, pageSize: 20 });
  assert.deepEqual(afterConfirmation.pagination, { page: 1, pageSize: 20, total: 20, pageCount: 1 });
  assert.equal(afterConfirmation.items.length, 20);

  const afterCategoryShrink = await listPendingMarketPrices(db as never, { category: "small-price", page: 2, pageSize: 20 });
  assert.deepEqual(afterCategoryShrink.pagination, { page: 1, pageSize: 20, total: 3, pageCount: 1 });
  assert.equal(afterCategoryShrink.items.length, 3);

  const masterAfterCategoryShrink = await listMarketMasterData(db as never, { category: "small-price", page: 2, pageSize: 20 });
  assert.deepEqual(masterAfterCategoryShrink.pagination, { page: 1, pageSize: 20, total: 3, pageCount: 1 });
  assert.equal(masterAfterCategoryShrink.items.length, 3);
  sqlite.close();
});

test("market master pagination uses one snapshot query for valid, clamped, and empty pages", async () => {
  const sqlite = new DatabaseSync(":memory:");
  let paginationAllCount = 0;
  let legacyCountFirstCount = 0;
  const db = sqliteAdapter(sqlite, {
    async beforeAll(sql) {
      if (sql.includes("pagination_sentinel")) paginationAllCount += 1;
    },
    async afterFirst(sql) {
      if (sql.includes("SELECT COUNT(*) total FROM") && sql.includes("filtered")) {
        legacyCountFirstCount += 1;
        sqlite.prepare("DELETE FROM market_ranking_entries WHERE category='single-query-price'").run();
      }
    },
  });
  await ensureMarketSchemaCore(db);
  const insertEntry = sqlite.prepare(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, sku_code, product_name, brand, gmv_cents, raw_json, last_import_batch_id)
    VALUES (?,?,'2026-06-01','2026-06-30','single-query-price','POP','SKU','POP',?,?,?,?,'{}','batch')`);
  const insertSnapshot = sqlite.prepare(`INSERT INTO market_price_snapshots
    (id, category, scope, sku_code, ranking_dimension, month, source_price_cents, confirmation_status)
    VALUES (?,'single-query-price','POP',?,'SKU','2026-06',10000,'source_table')`);
  for (let index = 1; index <= 25; index += 1) {
    const skuCode = `SKU-SINGLE-${String(index).padStart(2, "0")}`;
    insertEntry.run(`single-query-${index}`, index, skuCode, skuCode, "", 10_000 - index);
    insertSnapshot.run(`single-query-snapshot-${index}`, skuCode);
  }

  const beforePending = paginationAllCount;
  const pending = await listPendingMarketPrices(db as never, { category: "single-query-price", page: 2, pageSize: 20 });
  assert.equal(paginationAllCount - beforePending, 1);
  assert.deepEqual(pending.pagination, { page: 2, pageSize: 20, total: 25, pageCount: 2 });
  assert.equal(pending.items.length, 5);

  const beforeMaster = paginationAllCount;
  const master = await listMarketMasterData(db as never, { category: "single-query-price", page: 2, pageSize: 20 });
  assert.equal(paginationAllCount - beforeMaster, 1);
  assert.deepEqual(master.pagination, { page: 2, pageSize: 20, total: 25, pageCount: 2 });
  assert.equal(master.items.length, 5);

  const beforeEmpty = paginationAllCount;
  const empty = await listPendingMarketPrices(db as never, { category: "no-such-category", page: 99, pageSize: 20 });
  assert.equal(paginationAllCount - beforeEmpty, 1);
  assert.deepEqual(empty.pagination, { page: 1, pageSize: 20, total: 0, pageCount: 1 });
  assert.deepEqual(empty.items, []);
  assert.equal(legacyCountFirstCount, 0);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE category='single-query-price'").get() as { count: number }).count, 25);
  sqlite.close();
});

test("market master and pending-price pages use the unique row id to stabilize fully tied sort keys", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  const insertEntry = sqlite.prepare(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode,
      rank, sku_code, product_name, brand, gmv_cents, raw_json, last_import_batch_id)
    VALUES (?,?,'2026-06-01','2026-06-30','tied-pagination','POP','SKU','POP',1,?,?, '',100,'{}','batch')`);
  const insertSnapshot = sqlite.prepare(`INSERT INTO market_price_snapshots
    (id, category, scope, sku_code, ranking_dimension, month, source_price_cents, confirmation_status)
    VALUES (?,'tied-pagination','POP',?,'SKU','2026-06',10000,'source_table')`);
  const expected = Array.from({ length: 23 }, (_, index) => `SKU-TIED-${String(index + 1).padStart(2, "0")}`);
  expected.forEach((skuCode, index) => {
    insertEntry.run(`tied-pagination-${index + 1}`, index + 1, skuCode, skuCode);
    insertSnapshot.run(`tied-pagination-snapshot-${index + 1}`, skuCode);
  });

  const collectPages = async (history: boolean) => {
    const pages = await Promise.all([1, 2, 3].map((page) => history
      ? listPendingMarketPrices(db as never, { category: "tied-pagination", page, pageSize: 10 })
      : listMarketMasterData(db as never, { category: "tied-pagination", page, pageSize: 10 })));
    pages.forEach((result, index) => assert.deepEqual(result.pagination, {
      page: index + 1, pageSize: 10, total: 23, pageCount: 3,
    }));
    return pages.flatMap((result) => result.items.map((item) => item.skuCode));
  };

  assert.deepEqual(await collectPages(false), expected);
  assert.deepEqual(await collectPages(false), expected, "repeated current-master reads must keep identical page membership");
  assert.deepEqual(await collectPages(true), expected);
  assert.deepEqual(await collectPages(true), expected, "repeated historical reads must keep identical page membership");
  sqlite.close();
});

test("database workspace returns the requested pending-price source and page", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, sku_code, product_name, brand, raw_json, last_import_batch_id)
    VALUES
    ('workspace-price-ai',1,'2026-06-01','2026-06-30','category-price','pop','SKU','POP','SKU-AI','AI price product','','{}','batch'),
    ('workspace-price-source',2,'2026-06-01','2026-06-30','category-price','pop','SKU','POP','SKU-SOURCE','Source price product','','{}','batch'),
    ('workspace-price-average',3,'2026-06-01','2026-06-30','category-price','pop','SKU','POP','SKU-AVERAGE','Average price product','','{}','batch');
    INSERT INTO market_price_snapshots
    (id, category, scope, sku_code, ranking_dimension, month, source_price_cents, average_transaction_price_cents, ai_image_price_cents, confirmation_status)
    VALUES
    ('workspace-snapshot-ai','category-price','pop','SKU-AI','SKU','2026-06',NULL,500000,1150000,'ai_pending'),
    ('workspace-snapshot-source','category-price','pop','SKU-SOURCE','SKU','2026-06',769900,NULL,NULL,'source_table'),
    ('workspace-snapshot-average','category-price','pop','SKU-AVERAGE','SKU','2026-06',NULL,233300,NULL,'review_pending');`);

  const nonAiSecondPage = await getMarketMasterWorkspace(db as never, {
    mode: "database",
    pendingPriceCategory: "category-price",
    pendingPriceSource: "non_ai",
    pendingPricePage: 2,
    pendingPricePageSize: 1,
  });
  assert.deepEqual(nonAiSecondPage.pendingPrices.pagination, { page: 2, pageSize: 1, total: 2, pageCount: 2 });
  assert.equal(nonAiSecondPage.pendingPrices.items.length, 1);
  assert.notEqual(nonAiSecondPage.pendingPrices.items[0]?.candidatePriceSource, "ai_suggestion");

  const aiFirstPage = await getMarketMasterWorkspace(db as never, {
    mode: "database",
    pendingPriceCategory: "category-price",
    pendingPriceSource: "ai",
    pendingPricePage: 1,
    pendingPricePageSize: 20,
  });
  assert.deepEqual(aiFirstPage.pendingPrices.pagination, { page: 1, pageSize: 20, total: 1, pageCount: 1 });
  assert.equal(aiFirstPage.pendingPrices.items[0]?.skuCode, "SKU-AI");
  assert.equal(aiFirstPage.pendingPrices.items[0]?.candidatePriceSource, "ai_suggestion");
  sqlite.close();
});

test("database primary and deferred secondary views preserve filters and pagination independently", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, subcategory, sku_code, product_name, brand, raw_json, last_import_batch_id)
    VALUES
    ('split-primary-a',1,'2026-06-01','2026-06-30','split-category','pop','SKU','POP','split-subcategory','SKU-SPLIT-A','Split A','','{}','batch'),
    ('split-primary-b',2,'2026-06-01','2026-06-30','other-category','pop','SKU','POP','other-subcategory','SKU-SPLIT-B','Split B','','{}','batch');
    INSERT INTO market_subcategory_taxonomy (id, category, subcategory, status)
    VALUES ('split-taxonomy','split-category','split-subcategory','active');
    INSERT INTO market_price_snapshots
    (id, category, scope, sku_code, ranking_dimension, month, source_price_cents, confirmation_status)
    VALUES ('split-price-a','split-category','pop','SKU-SPLIT-A','SKU','2026-06',199900,'review_pending');`);

  const primary = await getMarketMasterDatabasePrimary(db as never, {
    categories: ["split-category"], page: 1, pageSize: 20,
  });
  assert.equal(primary.masterData.pagination.total, 1);
  assert.equal(primary.masterData.items[0]?.skuCode, "SKU-SPLIT-A");

  const filters = await getMarketMasterDatabaseFilters(db as never, {
    categories: ["split-category"],
  });
  assert.ok(filters.categories.some((item) => item.value === "split-category"));
  assert.ok(filters.subcategories.some((item) => item.value === "split-subcategory"));

  const secondary = await getMarketMasterDatabaseSecondary(db as never, {
    categories: ["split-category"], candidatePriceSources: ["non_ai"], page: 1, pageSize: 1,
  });
  assert.deepEqual(secondary.pendingPrices.pagination, { page: 1, pageSize: 1, total: 1, pageCount: 1 });
  assert.equal(secondary.pendingPrices.items[0]?.skuCode, "SKU-SPLIT-A");
  assert.equal(secondary.statusCounts.pendingPrices, 1);
  sqlite.close();
});

test("unified SKU database filters annotation storage status and updates all editable master fields", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await ensureAnnotationSchema(db as never);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,subcategory,sku_code,product_name,brand,raw_json,last_import_batch_id)
    VALUES ('edit-key',1,'2026-06-01','2026-06-30','三级类目A','POP','SKU','POP','旧细分','SKU-EDIT','旧标题','旧品牌','{}','batch');
    INSERT INTO market_price_snapshots (id,category,scope,sku_code,ranking_dimension,month,confirmation_status)
    VALUES ('edit-price','三级类目A','POP','SKU-EDIT','SKU','2026-06','missing');
    INSERT INTO market_sku_annotations (id,category,sku_code,segment,image_price_cents,source_job_item_id,prompt_version_id,reviewed_by,reviewed_at)
    VALUES ('edit-annotation','三级类目A','SKU-EDIT','旧细分',10000,'source-item','source-prompt','admin@example.com',CURRENT_TIMESTAMP);
    INSERT INTO market_annotation_jobs (id,category,prompt_version_id,executor,status,total_count,committed_count,created_by)
    VALUES ('edit-history-job','三级类目A','source-prompt','cloud','committed',1,1,'admin@example.com');
    INSERT INTO market_annotation_items (id,job_id,category,scope,sku_code,ranking_dimension,month,status,reviewed_segment)
    VALUES ('edit-history-item','edit-history-job','三级类目A','POP','SKU-EDIT','SKU','2026-06','committed','旧细分');
    INSERT INTO market_subcategory_taxonomy (id,category,subcategory,status,created_by,updated_by)
    VALUES ('edit-target-taxonomy','三级类目B','新细分','active','test','test');`);

  const committed = await listMarketMasterData(db as never, { annotationStatus: "committed" });
  const pending = await listMarketMasterData(db as never, { annotationStatus: "pending" });
  assert.equal(committed.pagination.total, 1);
  assert.equal(pending.pagination.total, 0);
  assert.equal(committed.items[0]?.annotationStatus, "committed");

  await updateMarketSkuMasterData(db as never, {
    originalCategory: "三级类目A", category: "三级类目B", scope: "POP", rankingDimension: "SKU", skuCode: "SKU-EDIT", month: "2026-06",
    productName: "新标题", brand: "新品牌", operationMode: "自营", subcategory: "新细分", priceCents: 259900, priceType: "标准售价",
  }, admin);
  const master = sqlite.prepare("SELECT category,product_name productName,brand,operation_mode operationMode,subcategory,natural_key naturalKey FROM market_ranking_entries WHERE sku_code='SKU-EDIT'").get() as Record<string, string>;
  assert.deepEqual({ category: master.category, productName: master.productName, brand: master.brand, operationMode: master.operationMode, subcategory: master.subcategory },
    { category: "三级类目B", productName: "新标题", brand: "新品牌", operationMode: "自营", subcategory: "新细分" });
  assert.match(master.naturalKey, /\|13:三级类目B\|/);
  const price = sqlite.prepare("SELECT category,confirmed_market_price_cents price FROM market_price_snapshots WHERE id='edit-price'").get() as { category: string; price: number };
  assert.deepEqual({ ...price }, { category: "三级类目B", price: 259900 });
  assert.deepEqual({ ...(sqlite.prepare("SELECT category,segment FROM market_sku_annotations WHERE id='edit-annotation'").get() as Record<string, unknown>) }, { category: "三级类目B", segment: "新细分" });
  assert.deepEqual({ ...(sqlite.prepare("SELECT category,reviewed_segment segment FROM market_annotation_items WHERE id='edit-history-item'").get() as Record<string, unknown>) }, { category: "三级类目A", segment: "旧细分" });
  sqlite.close();
});

test("runtime taxonomy backfills existing subcategories and the settings workspace reads the dictionary table", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,subcategory,sku_code,product_name,raw_json,last_import_batch_id)
    VALUES ('legacy-taxonomy-key',1,'2026-06-01','2026-06-30','历史三级类目','POP','SKU','POP','历史细分','SKU-LEGACY-TAX','商品','{}','batch');
    DELETE FROM market_subcategory_taxonomy;
    DELETE FROM market_master_audit_logs WHERE entity_type='runtime_schema' AND entity_id='market-subcategory-taxonomy-v1';`);
  await ensureMarketSchemaCore(db);
  const workspace = await getMarketSubcategoryWorkspace(db as never, "历史三级类目");
  assert.deepEqual(workspace.items.map((item) => item.subcategory), ["历史细分"]);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_subcategory_taxonomy WHERE status='active'").get() as { count: number }).count > 1, true);
  sqlite.close();
});

test("0036 migration moves ranking, annotation, and prompt taxonomies into the dictionary table", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await ensureAnnotationSchema(db as never);
  sqlite.exec(`DELETE FROM market_subcategory_taxonomy;
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,subcategory,sku_code,product_name,raw_json,last_import_batch_id)
    VALUES ('migration-taxonomy',1,'2026-06-01','2026-06-30','迁移类目','POP','SKU','POP','榜单细分','SKU-MIGRATION','商品','{}','batch');
    INSERT INTO market_sku_annotations (id,category,sku_code,segment,source_job_item_id,prompt_version_id,reviewed_by,reviewed_at)
    VALUES ('migration-annotation','迁移类目','SKU-MIGRATION','标注细分','item','prompt','admin@example.com',CURRENT_TIMESTAMP);
    INSERT INTO market_annotation_prompt_versions (id,category,version,source,status,segments_json,prompt_body,created_by)
    VALUES ('migration-prompt','迁移类目',1,'manual','active','["Prompt细分","其他"]','这是一个用于迁移测试且长度足够的 Prompt 正文内容。','admin@example.com');`);
  sqlite.exec(await readFile(new URL("../drizzle/0036_backfill_market_subcategory_taxonomy.sql", import.meta.url), "utf8"));
  const values = (sqlite.prepare("SELECT subcategory FROM market_subcategory_taxonomy WHERE category='迁移类目' ORDER BY subcategory").all() as Array<{ subcategory: string }>).map((row) => row.subcategory);
  assert.deepEqual(values, ["Prompt细分", "其他", "标注细分", "榜单细分"]);
  sqlite.close();
});

test("subcategory rename fences active jobs and then creates an immutable successor prompt", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await ensureAnnotationSchema(db as never);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,subcategory,source_subcategory,sku_code,product_name,raw_json,last_import_batch_id)
    VALUES ('taxonomy-key',1,'2026-06-01','2026-06-30','三级类目','POP','SKU','POP','旧品类','上游名称','SKU-TAX','商品','{}','batch');
    INSERT INTO market_sku_annotations (id,category,sku_code,segment,source_job_item_id,prompt_version_id,reviewed_by,reviewed_at)
    VALUES ('taxonomy-annotation','三级类目','SKU-TAX','旧品类','source-item','source-prompt','admin@example.com',CURRENT_TIMESTAMP);
    INSERT INTO market_subcategory_taxonomy (id,category,subcategory,status,created_by,updated_by)
    VALUES ('taxonomy-old','三级类目','旧品类','active','admin@example.com','admin@example.com');
    INSERT INTO market_annotation_prompt_versions (id,category,version,source,status,segments_json,prompt_body,created_by)
    VALUES ('taxonomy-prompt','三级类目',1,'manual','active','["旧品类"]','这是一个用于测试细分品类同步刷新的足够长 Prompt 正文。','admin@example.com');
    INSERT INTO market_annotation_jobs (id,category,prompt_version_id,executor,status,total_count,created_by)
    VALUES ('taxonomy-job','三级类目','taxonomy-prompt','cloud','running',1,'admin@example.com');
    INSERT INTO market_annotation_items (id,job_id,category,scope,sku_code,ranking_dimension,month,product_name,status,ai_segment,reviewed_segment)
    VALUES ('taxonomy-item','taxonomy-job','三级类目','POP','SKU-TAX','SKU','2026-06','商品','review_pending','旧品类','旧品类');
    INSERT INTO market_annotation_validation_samples (id,category,sku_code,gold_segment,created_by)
    VALUES ('taxonomy-sample','三级类目','SKU-TAX','旧品类','admin@example.com');
    INSERT INTO market_master_mapping_rules (id,kind,category,source_value,target_value,status,version,effective_from,created_by)
    VALUES ('taxonomy-inbound-map','subcategory','','上游名称','旧品类','published',1,'1970-01-01','admin@example.com');`);

  await assert.rejects(() => saveMarketSubcategorySettings(db as never, { category: "三级类目", renames: [{ source: "旧品类", target: "新品类" }], additions: ["新增品类"] }, admin), /仍有任务引用/);
  assert.equal((sqlite.prepare("SELECT subcategory FROM market_ranking_entries WHERE sku_code='SKU-TAX'").get() as { subcategory: string }).subcategory, "旧品类");
  sqlite.prepare("UPDATE market_annotation_jobs SET status='cancelled' WHERE id='taxonomy-job'").run();
  const result = await saveMarketSubcategorySettings(db as never, { category: "三级类目", renames: [{ source: "旧品类", target: "新品类" }], additions: ["新增品类"] }, admin);
  assert.equal(result.changedRows, 3);
  assert.equal((sqlite.prepare("SELECT subcategory FROM market_ranking_entries WHERE sku_code='SKU-TAX'").get() as { subcategory: string }).subcategory, "新品类");
  assert.equal((sqlite.prepare("SELECT segment FROM market_sku_annotations WHERE id='taxonomy-annotation'").get() as { segment: string }).segment, "新品类");
  assert.deepEqual({ ...(sqlite.prepare("SELECT ai_segment aiSegment,reviewed_segment reviewedSegment FROM market_annotation_items WHERE id='taxonomy-item'").get() as Record<string, unknown>) }, { aiSegment: "旧品类", reviewedSegment: "旧品类" });
  assert.equal((sqlite.prepare("SELECT gold_segment gold FROM market_annotation_validation_samples WHERE id='taxonomy-sample'").get() as { gold: string }).gold, "新品类");
  assert.equal((sqlite.prepare("SELECT target_value target FROM market_master_mapping_rules WHERE id='taxonomy-inbound-map'").get() as { target: string }).target, "旧品类");
  assert.equal((sqlite.prepare("SELECT target_value targetValue FROM market_master_mapping_rules WHERE kind='subcategory' AND source_value='旧品类'").get() as { targetValue: string }).targetValue, "新品类");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_subcategory_taxonomy WHERE category='三级类目' AND status='active'").get() as { count: number }).count, 2);
  assert.equal((sqlite.prepare("SELECT status FROM market_annotation_prompt_versions WHERE id='taxonomy-prompt'").get() as { status: string }).status, "archived");
  assert.deepEqual(JSON.parse((sqlite.prepare("SELECT segments_json segments FROM market_annotation_prompt_versions WHERE id=?").get(result.successorPromptId) as { segments: string }).segments), ["新品类", "新增品类"]);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_annotation_prompt_audits WHERE prompt_id=? AND action='taxonomy_rename_successor'").get(result.successorPromptId) as { count: number }).count, 1);
  await applyPublishedMarketMappings(db as never, { category: "三级类目" }, admin);
  assert.equal((sqlite.prepare("SELECT subcategory FROM market_ranking_entries WHERE sku_code='SKU-TAX'").get() as { subcategory: string }).subcategory, "新品类");
  sqlite.close();
});

test("SKU category migration rejects every resumable annotation item before changing facts", async () => {
  for (const status of ["queued", "claimed", "inferencing", "failed", "review_pending", "approved", "rejected"]) {
    const sqlite = new DatabaseSync(":memory:");
    const db = sqliteAdapter(sqlite);
    await ensureMarketSchemaCore(db);
    await ensureAnnotationSchema(db as never);
    sqlite.exec(`INSERT INTO market_subcategory_taxonomy (id,category,subcategory,status,created_by,updated_by)
        VALUES ('target-taxonomy','目标类目','目标细分','active','test','test');
      INSERT INTO market_ranking_entries
        (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,subcategory,sku_code,product_name,raw_json,last_import_batch_id)
        VALUES ('move-key',1,'2026-06-01','2026-06-30','原类目','POP','SKU','POP','原细分','SKU-MOVE','商品','{}','batch');
      INSERT INTO market_price_snapshots (id,category,scope,sku_code,ranking_dimension,month) VALUES ('move-price','原类目','POP','SKU-MOVE','SKU','2026-06');
      INSERT INTO market_annotation_prompt_versions (id,category,version,source,status,segments_json,prompt_body,created_by)
        VALUES ('move-prompt','原类目',1,'manual','active','["原细分"]','这是一个用于跨类目迁移阻断测试且长度足够的 Prompt 正文。','admin@example.com');
      INSERT INTO market_annotation_jobs (id,category,prompt_version_id,executor,status,total_count,created_by)
        VALUES ('move-job','原类目','move-prompt','cloud','running',1,'admin@example.com');
      INSERT INTO market_annotation_items (id,job_id,category,scope,sku_code,ranking_dimension,month,status)
        VALUES ('move-item','move-job','原类目','POP','SKU-MOVE','SKU','2026-06','${status}');`);
    await assert.rejects(() => updateMarketSkuMasterData(db as never, {
      originalCategory: "原类目", category: "目标类目", scope: "POP", rankingDimension: "SKU", skuCode: "SKU-MOVE", month: "2026-06",
      productName: "商品", brand: "", operationMode: "POP", subcategory: "目标细分", priceCents: null,
    }, admin), /未完成的 AI 标注候选/, status);
    assert.equal((sqlite.prepare("SELECT category FROM market_ranking_entries WHERE sku_code='SKU-MOVE'").get() as { category: string }).category, "原类目");
    assert.equal((sqlite.prepare("SELECT category FROM market_price_snapshots WHERE id='move-price'").get() as { category: string }).category, "原类目");
    sqlite.close();
  }
});

test("SKU category migration blocks legacy empty candidate identities and shared cross-scope annotations", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await ensureAnnotationSchema(db as never);
  sqlite.exec(`INSERT INTO market_subcategory_taxonomy (id,category,subcategory,status,created_by,updated_by)
      VALUES ('legacy-target-taxonomy','目标类目','目标细分','active','test','test');
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,subcategory,sku_code,product_name,raw_json,last_import_batch_id) VALUES
      ('legacy-pop',1,'2026-06-01','2026-06-30','原类目','POP','SKU','POP','原细分','SKU-LEGACY','商品','{}','batch'),
      ('legacy-self',2,'2026-06-01','2026-06-30','原类目','自营','SKU','自营','原细分','SKU-SHARED','商品','{}','batch'),
      ('legacy-shared-pop',3,'2026-06-01','2026-06-30','原类目','POP','SKU','POP','原细分','SKU-SHARED','商品','{}','batch');
    INSERT INTO market_price_snapshots (id,category,scope,sku_code,ranking_dimension,month) VALUES
      ('legacy-price','原类目','POP','SKU-LEGACY','SKU','2026-06'),
      ('shared-price','原类目','POP','SKU-SHARED','SKU','2026-06');
    INSERT INTO market_annotation_prompt_versions (id,category,version,source,status,segments_json,prompt_body,created_by)
      VALUES ('legacy-prompt','原类目',1,'manual','active','["原细分"]','这是用于旧库空身份候选迁移阻断测试且长度足够的 Prompt 正文。','admin@example.com');
    INSERT INTO market_annotation_jobs (id,category,prompt_version_id,executor,status,total_count,created_by)
      VALUES ('legacy-job','原类目','legacy-prompt','cloud','running',1,'admin@example.com');
    INSERT INTO market_annotation_items (id,job_id,category,scope,sku_code,ranking_dimension,month,image_content_sha256,status)
      VALUES ('legacy-item','legacy-job','','','SKU-LEGACY','SKU','','','review_pending');`);
  const request = (skuCode: string) => ({
    originalCategory: "原类目", category: "目标类目", scope: "POP", rankingDimension: "SKU", skuCode, month: "2026-06",
    productName: "商品", brand: "", operationMode: "POP", subcategory: "目标细分", priceCents: null,
  });
  await assert.rejects(() => updateMarketSkuMasterData(db as never, request("SKU-LEGACY"), admin), /未完成的 AI 标注候选/);
  await assert.rejects(() => updateMarketSkuMasterData(db as never, request("SKU-SHARED"), admin), /其他店铺范围或榜单维度/);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE category='目标类目'").get() as { count: number }).count, 0);
  sqlite.close();
});

test("SKU category migration transaction rechecks a concurrently inserted sibling identity", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const base = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(base);
  await ensureAnnotationSchema(base as never);
  sqlite.exec(`INSERT INTO market_subcategory_taxonomy (id,category,subcategory,status,created_by,updated_by)
      VALUES ('race-target-taxonomy','目标类目','目标细分','active','test','test');
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,subcategory,sku_code,product_name,raw_json,last_import_batch_id)
      VALUES ('race-primary',1,'2026-06-01','2026-06-30','原类目','POP','SKU','POP','原细分','SKU-RACE','商品','{}','batch');
    INSERT INTO market_price_snapshots (id,category,scope,sku_code,ranking_dimension,month)
      VALUES ('race-primary-price','原类目','POP','SKU-RACE','SKU','2026-06');
    INSERT INTO market_sku_annotations (id,category,sku_code,segment,source_job_item_id,prompt_version_id,reviewed_by,reviewed_at)
      VALUES ('race-annotation','原类目','SKU-RACE','原细分','history','prompt','admin@example.com',CURRENT_TIMESTAMP);`);
  let insertedSibling = false;
  const racing = sqliteAdapter(sqlite, { afterFirst: async (sql) => {
    if (!insertedSibling && sql.includes("scope<>?") && sql.includes("market_ranking_entries")) {
      insertedSibling = true;
      sqlite.exec(`INSERT INTO market_ranking_entries
        (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,subcategory,sku_code,product_name,raw_json,last_import_batch_id)
        VALUES ('race-sibling',2,'2026-06-01','2026-06-30','原类目','自营','SKU','自营','原细分','SKU-RACE','商品','{}','other-batch');`);
    }
  } });
  await assert.rejects(() => updateMarketSkuMasterData(racing as never, {
    originalCategory: "原类目", category: "目标类目", scope: "POP", rankingDimension: "SKU", skuCode: "SKU-RACE", month: "2026-06",
    productName: "商品", brand: "", operationMode: "POP", subcategory: "目标细分", priceCents: null,
  }, admin), /NOT NULL constraint failed/);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE category='原类目' AND sku_code='SKU-RACE'").get() as { count: number }).count, 2);
  assert.equal((sqlite.prepare("SELECT category FROM market_sku_annotations WHERE id='race-annotation'").get() as { category: string }).category, "原类目");
  sqlite.close();
});

test("SKU category migration publishes facts and precise master identities in one batch", async () => {
  const sqlite = new DatabaseSync(":memory:");
  let migrationBatchCount = 0;
  let observedAfterCategoryBatch: Array<Record<string, unknown>> = [];
  const db = sqliteAdapter(sqlite, { afterBatch: async () => {
    migrationBatchCount += 1;
    if (migrationBatchCount !== 2) return;
    observedAfterCategoryBatch = (sqlite.prepare(`SELECT 'ranking' kind, category, scope, ranking_dimension, sku_code, id latest_entry_id
        FROM market_ranking_entries WHERE sku_code IN ('SKU-ATOMIC-MOVE','SKU-UNCHANGED')
      UNION ALL
      SELECT 'identity' kind, category, scope, ranking_dimension, sku_code, latest_entry_id
        FROM market_master_identities WHERE sku_code IN ('SKU-ATOMIC-MOVE','SKU-UNCHANGED')
      ORDER BY kind, sku_code`).all() as Array<Record<string, unknown>>).map((row) => ({ ...row }));
  } });
  await ensureMarketSchemaCached(db);
  await ensureAnnotationSchema(db as never);
  sqlite.exec(`INSERT INTO market_subcategory_taxonomy (id,category,subcategory,status,created_by,updated_by)
      VALUES ('atomic-move-target','目标类目','目标细分','active','test','test');
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,subcategory,sku_code,product_name,raw_json,last_import_batch_id) VALUES
      ('atomic-move',1,'2026-06-01','2026-06-30','原类目','POP','SKU','POP','原细分','SKU-ATOMIC-MOVE','待迁商品','{}','batch'),
      ('atomic-unchanged',2,'2026-06-01','2026-06-30','无关类目','POP','SKU','POP','无关细分','SKU-UNCHANGED','无关商品','{}','batch');
    INSERT INTO market_price_snapshots (id,category,scope,sku_code,ranking_dimension,month)
      VALUES ('atomic-move-price','原类目','POP','SKU-ATOMIC-MOVE','SKU','2026-06');
    INSERT INTO market_sku_annotations (id,category,sku_code,segment,source_job_item_id,prompt_version_id,reviewed_by,reviewed_at)
      VALUES ('atomic-move-annotation','原类目','SKU-ATOMIC-MOVE','原细分','history','prompt','admin@example.com',CURRENT_TIMESTAMP);`);

  // Ignore schema setup batches. The first observed batch bootstraps the legacy-empty
  // identity table; the second is the category migration transaction itself.
  migrationBatchCount = 0;
  const result = await updateMarketSkuMasterData(db as never, {
    originalCategory: "原类目", category: "目标类目", scope: "POP", rankingDimension: "SKU",
    skuCode: "SKU-ATOMIC-MOVE", month: "2026-06", productName: "已迁商品", brand: "品牌",
    operationMode: "POP", subcategory: "目标细分", priceCents: 199_900, priceType: "标准售价",
  }, admin);

  assert.equal(result.changedRows, 5, "identity/control statements must not inflate business changedRows");
  assert.deepEqual(observedAfterCategoryBatch, [
    { kind: "identity", category: "目标类目", scope: "POP", ranking_dimension: "SKU", sku_code: "SKU-ATOMIC-MOVE", latest_entry_id: 1 },
    { kind: "identity", category: "无关类目", scope: "POP", ranking_dimension: "SKU", sku_code: "SKU-UNCHANGED", latest_entry_id: 2 },
    { kind: "ranking", category: "目标类目", scope: "POP", ranking_dimension: "SKU", sku_code: "SKU-ATOMIC-MOVE", latest_entry_id: 1 },
    { kind: "ranking", category: "无关类目", scope: "POP", ranking_dimension: "SKU", sku_code: "SKU-UNCHANGED", latest_entry_id: 2 },
  ]);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_master_identities WHERE category='原类目' AND sku_code='SKU-ATOMIC-MOVE'").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT suppress_identity_revision value FROM market_system_kpi_cache_control WHERE id=1").get() as { value: number }).value, 0);
  sqlite.close();
});

test("subcategory rename is atomic when its audit write fails", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const base = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(base);
  await ensureAnnotationSchema(base as never);
  sqlite.exec(`INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,subcategory,sku_code,product_name,raw_json,last_import_batch_id)
      VALUES ('atomic-taxonomy',1,'2026-06-01','2026-06-30','原子类目','POP','SKU','POP','旧名','SKU-ATOMIC','商品','{}','batch');
    INSERT INTO market_subcategory_taxonomy (id,category,subcategory,status,created_by,updated_by)
      VALUES ('atomic-old','原子类目','旧名','active','test','test');`);
  const failing = sqliteAdapter(sqlite, { afterRun: async (sql) => {
    if (sql.includes("save_market_subcategory_settings")) throw new Error("forced audit failure");
  } });
  await assert.rejects(() => saveMarketSubcategorySettings(failing as never, {
    category: "原子类目", renames: [{ source: "旧名", target: "新名" }], additions: ["新增名"],
  }, admin), /forced audit failure/);
  assert.equal((sqlite.prepare("SELECT subcategory FROM market_ranking_entries WHERE sku_code='SKU-ATOMIC'").get() as { subcategory: string }).subcategory, "旧名");
  assert.deepEqual((sqlite.prepare("SELECT subcategory,status FROM market_subcategory_taxonomy WHERE category='原子类目' ORDER BY subcategory").all() as Array<Record<string, unknown>>).map((row) => ({ ...row })), [
    { subcategory: "旧名", status: "active" },
  ]);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_master_mapping_rules WHERE category='原子类目'").get() as { count: number }).count, 0);
  sqlite.close();
});

test("subcategory rename stays below D1 binding limits and rejects a stale taxonomy read", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const base = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(base);
  await ensureAnnotationSchema(base as never);
  const insertTaxonomy = sqlite.prepare(`INSERT INTO market_subcategory_taxonomy
    (id,category,subcategory,status,sort_order,created_by,updated_by) VALUES (?, '批量重命名', ?, 'active', ?, 'test', 'test')`);
  for (let index = 0; index < 40; index += 1) insertTaxonomy.run(`bulk-taxonomy-${index}`, `旧-${index}`, index);
  let maxBindings = 0;
  const bounded = sqliteAdapter(sqlite, { beforeRun: async (sql) => {
    maxBindings = Math.max(maxBindings, sql.match(/\?/g)?.length ?? 0);
  } });
  await saveMarketSubcategorySettings(bounded as never, {
    category: "批量重命名",
    renames: Array.from({ length: 40 }, (_, index) => ({ source: `旧-${index}`, target: `新-${index}` })),
  }, admin);
  assert.equal(maxBindings <= 100, true);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_subcategory_taxonomy WHERE category='批量重命名' AND status='active' AND subcategory LIKE '新-%'").get() as { count: number }).count, 40);

  sqlite.exec(`INSERT INTO market_subcategory_taxonomy (id,category,subcategory,status,sort_order,created_by,updated_by) VALUES
    ('race-a','并发字典','A','active',0,'test','test'), ('race-b','并发字典','B','active',1,'test','test');`);
  await assert.rejects(() => saveMarketSubcategorySettings(base as never, {
    category: "并发字典", renames: [{ source: "A", target: "X" }], additions: ["A"],
  }, admin), /不能在同一次保存中重新新增/);
  let changedConcurrently = false;
  const racing = sqliteAdapter(sqlite, { afterFirst: async (sql) => {
    if (!changedConcurrently && sql.includes("FROM market_annotation_prompt_versions") && sql.includes("status='active'")) {
      changedConcurrently = true;
      sqlite.exec(`UPDATE market_subcategory_taxonomy SET status='archived' WHERE id='race-a';
        INSERT INTO market_subcategory_taxonomy (id,category,subcategory,status,sort_order,created_by,updated_by)
        VALUES ('race-y','并发字典','Y','active',0,'other','other');`);
    }
  } });
  await assert.rejects(() => saveMarketSubcategorySettings(racing as never, {
    category: "并发字典", renames: [{ source: "A", target: "X" }], additions: ["C"],
  }, admin), /NOT NULL constraint failed/);
  assert.deepEqual((sqlite.prepare("SELECT subcategory FROM market_subcategory_taxonomy WHERE category='并发字典' AND status='active' ORDER BY subcategory").all() as Array<{ subcategory: string }>).map((row) => row.subcategory), ["B", "Y"]);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_master_mapping_rules WHERE category='并发字典'").get() as { count: number }).count, 0);
  sqlite.close();
});

test("subcategory prompt successor replaces overlapping labels simultaneously", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await ensureAnnotationSchema(db as never);
  sqlite.exec(`INSERT INTO market_subcategory_taxonomy (id,category,subcategory,status,sort_order,created_by,updated_by) VALUES
      ('overlap-short','包含标签','台式','active',0,'test','test'),
      ('overlap-long','包含标签','台式净饮','active',1,'test','test');
    INSERT INTO market_annotation_prompt_versions (id,category,version,source,status,segments_json,prompt_body,created_by)
      VALUES ('overlap-prompt','包含标签',1,'manual','active','["台式","台式净饮"]',
        '分类规则：台式使用桌面结构；台式净饮必须同时具备净化和饮水能力。这是长度足够的测试正文。','admin@example.com');`);
  const result = await saveMarketSubcategorySettings(db as never, {
    category: "包含标签",
    renames: [{ source: "台式", target: "台式新版" }, { source: "台式净饮", target: "商用净饮" }],
  }, admin);
  const prompt = sqlite.prepare("SELECT segments_json segments,prompt_body body FROM market_annotation_prompt_versions WHERE id=?").get(result.successorPromptId) as { segments: string; body: string };
  assert.deepEqual(JSON.parse(prompt.segments), ["台式新版", "商用净饮"]);
  assert.match(prompt.body, /台式新版使用桌面结构/);
  assert.match(prompt.body, /商用净饮必须同时具备/);
  assert.doesNotMatch(prompt.body, /台式新版净饮/);
  sqlite.close();
});

test("brand seed matching prefers the earliest title brand and protects short ASCII seeds", () => {
  const seed = (canonicalBrand: string, seedText: string): MarketBrandSeed => ({
    id: seedText, canonicalBrand, seedText, normalizedSeed: seedText.toLowerCase(), source: "system", sourceRef: "test", status: "enabled",
  });
  const seeds = [seed("DEMASHI", "DEMASHI"), seed("德玛仕", "德玛仕"), seed("CK", "CK")];
  assert.equal(matchMarketBrandTitle("德玛仕（DEMASHI）商用净水器", seeds, "title_anywhere")?.brand, "德玛仕");
  assert.equal(matchMarketBrandTitle("BLACK 商用设备", seeds, "title_anywhere"), null);
});

test("system brand seeds refresh and apply B-store prefix versus C-store anywhere rules", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`CREATE TABLE erp_product_master (brand TEXT NOT NULL DEFAULT '');
    INSERT INTO erp_product_master (brand) VALUES ('品牌甲'), ('配件');
    INSERT INTO market_ranking_entries
      (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, sku_code, product_name, brand, raw_json, last_import_batch_id)
    VALUES
      ('seed-b-prefix',1,'2026-06-01','2026-06-30','净水','B店','SKU','自营','B-PREFIX','品牌甲商用净水机','', '{"店铺类型":"B店"}','batch'),
      ('seed-b-late',2,'2026-06-01','2026-06-30','净水','B店','SKU','自营','B-LATE','商用净水机品牌甲','', '{"店铺类型":"B店"}','batch'),
      ('seed-c-late',3,'2026-06-01','2026-06-30','净水','C店','SKU','POP','C-LATE','商用净水机品牌甲','', '{"店铺类型":"C店"}','batch');`);

  const refreshed = await refreshMarketBrandSeeds(db as never, admin);
  assert.equal(refreshed.discovered, 1);
  assert.equal(refreshed.inserted, 1);
  const matched = await matchMarketBrandSeeds(db as never, { category: "净水" }, admin);
  assert.deepEqual({
    scanned: matched.scanned,
    matchedSkuCount: matched.matchedSkuCount,
    prefixMatched: matched.prefixMatched,
    anywhereMatched: matched.anywhereMatched,
    remainingSkuCount: matched.remainingSkuCount,
  }, { scanned: 3, matchedSkuCount: 2, prefixMatched: 1, anywhereMatched: 1, remainingSkuCount: 1 });
  assert.equal((sqlite.prepare("SELECT brand FROM market_ranking_entries WHERE sku_code='B-PREFIX'").get() as { brand: string }).brand, "品牌甲");
  assert.equal((sqlite.prepare("SELECT brand FROM market_ranking_entries WHERE sku_code='B-LATE'").get() as { brand: string }).brand, "");
  assert.equal((sqlite.prepare("SELECT brand FROM market_ranking_entries WHERE sku_code='C-LATE'").get() as { brand: string }).brand, "品牌甲");

  let workspace = await getMarketBrandSeedWorkspace(db as never, { category: "净水" });
  assert.equal(workspace.dictionary.counts.system, 1);
  assert.equal(workspace.unknown.pagination.total, 1);
  await upsertMarketBrandSeed(db as never, {
    canonicalBrand: "品牌乙", seedText: "品牌乙", category: "净水", scope: "B店",
    rankingDimension: "SKU", skuCode: "B-LATE",
  }, admin);
  workspace = await getMarketBrandSeedWorkspace(db as never, { category: "净水" });
  assert.equal(workspace.dictionary.counts.manual, 1);
  assert.equal(workspace.unknown.pagination.total, 0);
});

test("newly discovered system brand seeds match the current import without pre-publish writes", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec("CREATE TABLE erp_product_master (brand TEXT NOT NULL); INSERT INTO erp_product_master VALUES ('品牌新')");
  const matched = await matchImportedMarketBrands(db, [{
    brand: "", productName: "商用品牌新净水机", scope: "C店", operationMode: "POP", raw: { 店铺类型: "C店" },
  } as MarketEntryForImport]);
  assert.equal(matched.rows[0]?.brand, "品牌新");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_brand_seeds").get() as { count: number }).count, 0);
  sqlite.exec(`INSERT INTO market_brand_seeds
    (id,canonical_brand,seed_text,normalized_seed,source,source_ref,status,created_by)
    VALUES ('stale-seed','旧品牌','旧品牌','旧品牌','system','old-source','enabled','system')`);
  const stale = await matchImportedMarketBrands(db, [{
    brand: "", productName: "旧品牌商用净水机", scope: "C店", operationMode: "POP", raw: { 店铺类型: "C店" },
  } as MarketEntryForImport]);
  assert.equal(stale.rows[0]?.brand, "");
  sqlite.close();
});

test("market import can atomically persist its discovered system seed snapshot without rescanning source tables", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  const snapshot: MarketBrandSeed[] = [{
    id: "snapshot-seed",
    canonicalBrand: "品牌快照",
    seedText: "品牌快照",
    normalizedSeed: "品牌快照",
    source: "system",
    sourceRef: "market_confirmed_brand,erp_product_master",
    status: "enabled",
  }];
  const refreshed = await refreshSystemMarketBrandSeeds(db, "market-import", { systemSeedSnapshot: snapshot });
  assert.equal(refreshed.discovered, 1);
  assert.equal(refreshed.inserted, 1);
  assert.deepEqual({ ...sqlite.prepare("SELECT canonical_brand brand, source_ref sourceRef FROM market_brand_seeds WHERE normalized_seed=?")
    .get("品牌快照") }, { brand: "品牌快照", sourceRef: "erp_product_master,market_confirmed_brand" });
  sqlite.close();
});

test("system brand seed refresh is atomic when one discovered seed is rejected", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec("CREATE TABLE erp_product_master (brand TEXT NOT NULL)");
  const insert = sqlite.prepare("INSERT INTO erp_product_master VALUES (?)");
  for (let index = 0; index < 81; index += 1) insert.run(`品牌${index}`);
  sqlite.exec(`CREATE TRIGGER reject_last_system_seed BEFORE INSERT ON market_brand_seeds
    WHEN NEW.canonical_brand='品牌80' BEGIN SELECT RAISE(ABORT, 'forced seed failure'); END;`);
  await assert.rejects(refreshSystemMarketBrandSeeds(db, "admin@example.com"), /forced seed failure/);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_brand_seeds WHERE source='system'").get() as { count: number }).count, 0);
  sqlite.close();
});

test("brand recognition jobs count unique pending identities and persist pause/resume progress", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, sku_code, product_name, brand, gmv_cents, quantity, visitors, raw_json, last_import_batch_id)
    VALUES
    ('brand-a-may',1,'2026-05-01','2026-05-31','净水','pop','SKU','POP','SKU-A','商品A旧标题','',100,1,1,'{}','batch'),
    ('brand-a-jun',2,'2026-06-01','2026-06-30','净水','pop','SKU','POP','SKU-A','商品A新标题','',200,2,2,'{}','batch'),
    ('brand-b-jun',3,'2026-06-01','2026-06-30','净水','pop','SKU','POP','SKU-B','商品B','',300,3,3,'{}','batch');
    INSERT INTO market_brand_suggestions
      (id, category, scope, ranking_dimension, sku_code, product_name, ai_brand, status, model_id)
    VALUES ('suggestion-a','净水','pop','SKU','SKU-A','商品A新标题','品牌A','ai_pending','model-1');`);
  const created = await createMarketBrandRecognitionJob(db as never, { modelId: "model-1", category: "净水" }, admin);
  assert.equal(created.totalCount, 1);
  assert.equal(created.remainingCount, 1);
  const paused = await setMarketBrandRecognitionJobStatus(db as never, { id: created.id, status: "paused" }, admin);
  assert.equal(paused?.status, "paused");
  const resumed = await setMarketBrandRecognitionJobStatus(db as never, { id: created.id, status: "queued" }, admin);
  assert.equal(resumed?.status, "queued");
  const fetched = await getMarketBrandRecognitionJob(db as never, { category: "净水" });
  assert.equal(fetched?.id, created.id);
  assert.equal(fetched?.progressBps, 0);
});

test("installment price confirmation is rejected and scoped confirmation cannot alter another scope", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  const installment = "\u5206\u671f\u91d1\u989d";
  const standard = "\u6807\u51c6\u552e\u4ef7";
  const selfOperated = "\u81ea\u8425";
  await ensureMarketSchemaCore(db);
  sqlite.exec(`INSERT INTO market_price_snapshots (id, category, scope, sku_code, ranking_dimension, month, ai_price_type)
    VALUES ('pop-price','category-1','pop','SKU-1','SKU','2026-06','分期金额'), ('self-price','category-1','自营','SKU-1','SKU','2026-06','标准售价');`);
  await assert.rejects(() => confirmMarketPrice(db as never, {
    category: "category-1", scope: "pop", skuCode: "SKU-1", rankingDimension: "SKU", month: "2026-06",
    priceCents: 199900, priceType: installment,
  }, admin));
  await assert.rejects(() => confirmMarketPrice(db as never, {
    category: "category-1", scope: "pop", skuCode: "SKU-1", rankingDimension: "SKU", month: "2026-06",
    priceCents: 199900,
  }, admin));
  sqlite.prepare("UPDATE market_price_snapshots SET image_content_sha256='expected-hash' WHERE id='pop-price'").run();
  await assert.rejects(() => confirmMarketPrice(db as never, {
    category: "category-1", scope: "pop", skuCode: "SKU-1", rankingDimension: "SKU", month: "2026-06",
    priceCents: 199900, priceType: standard,
  }, admin));
  await confirmMarketPrice(db as never, {
    category: "category-1", scope: "pop", skuCode: "SKU-1", rankingDimension: "SKU", month: "2026-06",
    imageContentSha256: "expected-hash", priceCents: 199900, priceType: standard,
  }, admin);
  const prices = sqlite.prepare("SELECT scope, confirmed_market_price_cents price FROM market_price_snapshots ORDER BY scope").all() as Array<{ scope: string; price: number | null }>;
  assert.deepEqual(prices.map((row) => ({ ...row })), [{ scope: "pop", price: 199900 }, { scope: selfOperated, price: null }]);
  sqlite.close();
});

test("published master mappings apply to ranking facts and are audited", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, subcategory, sku_code, product_name, brand, raw_json, last_import_batch_id)
    VALUES ('mapping-1',1,'2026-06-01','2026-06-30','category-map','raw-store','SKU','未知','old-segment','SKU-MAP','Fresh segment product','old-brand','{}','batch');`);
  const brandRule = await upsertMarketMapping(db as never, { kind: "brand_alias", category: "category-map", sourceValue: "old-brand", targetValue: "new-brand", status: "published" }, admin);
  await upsertMarketMapping(db as never, { kind: "operation_mode", category: "category-map", sourceValue: "raw-store", targetValue: "POP", status: "published" }, admin);
  await upsertMarketMapping(db as never, { kind: "subcategory", category: "category-map", sourceValue: "Fresh", targetValue: "fresh-segment", status: "published" }, admin);
  const result = await applyPublishedMarketMappings(db as never, { category: "category-map" }, admin);
  assert.equal(result.changed, 3);
  const row = sqlite.prepare("SELECT brand, operation_mode mode, subcategory FROM market_ranking_entries WHERE sku_code='SKU-MAP'").get() as { brand: string; mode: string; subcategory: string };
  assert.deepEqual({ ...row }, { brand: "new-brand", mode: "POP", subcategory: "fresh-segment" });
  await upsertMarketMapping(db as never, { id: brandRule.id, kind: "brand_alias", category: "category-map", sourceValue: "old-brand", targetValue: "newest-brand", status: "published" }, admin);
  await applyPublishedMarketMappings(db as never, { category: "category-map" }, admin);
  assert.equal((sqlite.prepare("SELECT brand FROM market_ranking_entries WHERE sku_code='SKU-MAP'").get() as { brand: string }).brand, "newest-brand");
  assert.equal((sqlite.prepare("SELECT source_brand sourceBrand FROM market_ranking_entries WHERE sku_code='SKU-MAP'").get() as { sourceBrand: string }).sourceBrand, "old-brand");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_master_audit_logs WHERE action='apply_published_mappings'").get() as { count: number }).count, 2);
  sqlite.close();
});

test("download executor validates, stages, imports, caches, creates price tasks, recovers, and remains idempotent", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await upsertMarketDownloadConfig(db as never, { category: "category-download", scope: "pop", rankingDimension: "SKU", monthStart: "2026-06", monthEnd: "2026-06" }, admin);
  await planMissingMarketDownloads(db as never, {}, admin);
  const task = sqlite.prepare("SELECT id FROM market_download_tasks LIMIT 1").get() as { id: string };
  let attempts = 0;
  let cached = 0;
  let priceTasks = 0;
  const csv = [
    "period_start,period_end,category,scope,dimension,rank,sku_code,product_name,brand,price,gmv,quantity,visitors,image_url",
    "2026-06-01,2026-06-30,category-download,pop,SKU,1,SKU-DL,Download product,Brand,1999,10000,5,20,https://img.example/dl.jpg",
  ].join("\n");
  const deps = {
    download: async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("temporary JD download failure");
      return { bytes: new TextEncoder().encode(csv), fileName: "market_SKU_2026-06.csv", jdTaskId: "jd-task-1" };
    },
    cacheImages: async () => { cached += 1; return { queued: 1 }; },
    createPriceTasks: async () => { priceTasks += 1; return { created: 1 }; },
  };
  const failed = await executeMarketDownloadTask(db as never, { taskId: task.id }, admin, deps);
  assert.equal(failed.status, "failed");
  sqlite.prepare("UPDATE market_download_tasks SET next_retry_at=NULL WHERE id=?").run(task.id);
  const imported = await executeMarketDownloadTask(db as never, { taskId: task.id }, admin, deps);
  assert.equal(imported.status, "imported");
  const duplicate = await executeMarketDownloadTask(db as never, { taskId: task.id }, admin, deps);
  assert.equal(duplicate.duplicate, true);
  assert.equal(cached, 1);
  assert.equal(priceTasks, 1);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE sku_code='SKU-DL'").get() as { count: number }).count, 1);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_download_staging_rows").get() as { count: number }).count, 1);
  assert.equal((sqlite.prepare("SELECT status, header_valid headerValid, period_valid periodValid, category_valid categoryValid, dimension_valid dimensionValid FROM market_download_tasks WHERE id=?").get(task.id) as { status: string; headerValid: number; periodValid: number; categoryValid: number; dimensionValid: number }).status, "imported");
  const validation = JSON.parse((sqlite.prepare("SELECT validation_json validation FROM market_download_tasks WHERE id=?").get(task.id) as { validation: string }).validation) as { importIdentityHash: string };
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_batches WHERE file_hash=?").get(validation.importIdentityHash) as { count: number }).count, 1);
  sqlite.close();
});

test("download executor republishes a failed import batch instead of treating it as a duplicate", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await upsertMarketDownloadConfig(db as never, { category: "category-retry", scope: "pop", rankingDimension: "SKU", monthStart: "2026-06", monthEnd: "2026-06" }, admin);
  await planMissingMarketDownloads(db as never, {}, admin);
  const task = sqlite.prepare("SELECT id FROM market_download_tasks LIMIT 1").get() as { id: string };
  const csv = [
    "period_start,period_end,category,scope,dimension,rank,sku_code,product_name,brand,gmv,quantity",
    "2026-06-01,2026-06-30,category-retry,pop,SKU,1,SKU-RETRY,Retry,Brand,200,2",
  ].join("\n");
  const bytes = new TextEncoder().encode(csv);
  const importIdentityHash = createHash("sha256").update(bytes)
    .update(["", "category-retry", "pop", "SKU", "2026-06"].join("\0")).digest("hex");
  sqlite.prepare(`INSERT INTO market_import_batches
    (id,source_type,file_name,file_size_bytes,file_hash,sheet_name,status,row_count)
    VALUES ('failed-import','jd_market_download','retry.csv',?,?, 'CSV','failed',1)`)
    .run(bytes.byteLength, importIdentityHash);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key,source_row_number,period_start,period_end,category,scope,price_band_filter,ranking_dimension,operation_mode,sku_code,product_name,gmv_cents,quantity,raw_json,last_import_batch_id)
    VALUES ('legacy-partial',2,'2026-06-01','2026-06-30','category-retry','pop','全部','SKU','POP','SKU-RETRY','Old',100,1,'{}','failed-import')`);
  sqlite.exec(`CREATE TRIGGER reject_normal_imported_task_update BEFORE UPDATE ON market_download_tasks
    WHEN NEW.status='imported' AND NEW.error_code=''
    BEGIN SELECT RAISE(ABORT, 'forced task metadata failure'); END;`);

  const result = await executeMarketDownloadTask(db as never, { taskId: task.id }, admin, {
    download: async () => ({ bytes, fileName: "retry.csv" }),
    cacheImages: async () => { throw new Error("forced cache failure"); },
    createPriceTasks: async () => { throw new Error("forced price-task failure"); },
  });

  assert.equal(result.status, "imported");
  assert.equal(result.duplicate, false);
  assert.equal(result.batchId, "failed-import");
  assert.equal(result.maintenanceFailed, true);
  assert.equal(result.reconciliationPending, true);
  const importedTask = sqlite.prepare(`SELECT status, source_file_name sourceFileName, file_hash fileHash, row_count rowCount,
    header_valid headerValid, period_valid periodValid, category_valid categoryValid, dimension_valid dimensionValid,
    import_batch_id importBatchId, validation_json validationJson
    FROM market_download_tasks WHERE id=?`).get(task.id) as Record<string, unknown>;
  assert.equal(importedTask.status, "imported");
  assert.equal(importedTask.sourceFileName, "retry.csv");
  assert.equal(importedTask.fileHash, createHash("sha256").update(bytes).digest("hex"));
  assert.equal(importedTask.rowCount, 1);
  assert.deepEqual([importedTask.headerValid, importedTask.periodValid, importedTask.categoryValid, importedTask.dimensionValid], [1, 1, 1, 1]);
  assert.equal(importedTask.importBatchId, "failed-import");
  assert.equal(JSON.parse(String(importedTask.validationJson)).importIdentityHash, importIdentityHash);
  assert.deepEqual({ ...(sqlite.prepare("SELECT status,row_count rowCount FROM market_import_batches WHERE id='failed-import'").get() as Record<string, unknown>) }, { status: "completed", rowCount: 1 });
  assert.deepEqual({ ...(sqlite.prepare("SELECT product_name productName,gmv_cents gmv,last_import_batch_id batchId FROM market_ranking_entries WHERE sku_code='SKU-RETRY'").get() as Record<string, unknown>) }, { productName: "Retry", gmv: 20000, batchId: "failed-import" });
  assert.deepEqual(await planMissingMarketDownloads(db as never, {}, admin), { created: 0, reused: 0 });
  sqlite.close();
});

test("download executor reconciles a completed import after consecutive task-status update failures", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await upsertMarketDownloadConfig(db as never, { category: "category-reconcile", scope: "pop", rankingDimension: "SKU", monthStart: "2026-06", monthEnd: "2026-06" }, admin);
  await planMissingMarketDownloads(db as never, {}, admin);
  const task = sqlite.prepare("SELECT id FROM market_download_tasks LIMIT 1").get() as { id: string };
  const csv = [
    "period_start,period_end,category,scope,dimension,rank,sku_code,product_name,brand,gmv,quantity",
    "2026-06-01,2026-06-30,category-reconcile,pop,SKU,1,SKU-RECONCILE,Reconcile,Brand,300,3",
  ].join("\n");
  const bytes = new TextEncoder().encode(csv);
  let downloads = 0;
  sqlite.exec(`CREATE TRIGGER reject_all_imported_task_updates BEFORE UPDATE ON market_download_tasks
    WHEN NEW.status='imported'
    BEGIN SELECT RAISE(ABORT, 'forced persistent task status failure'); END;`);

  const first = await executeMarketDownloadTask(db as never, { taskId: task.id }, admin, {
    download: async () => {
      downloads += 1;
      return { bytes, fileName: "reconcile.csv", jdTaskId: "jd-reconcile" };
    },
  });
  assert.equal(first.status, "imported");
  assert.equal(first.reconciliationPending, true);
  const pendingTask = sqlite.prepare(`SELECT status, import_batch_id importBatchId, file_hash fileHash, row_count rowCount,
    header_valid headerValid, period_valid periodValid, category_valid categoryValid, dimension_valid dimensionValid
    FROM market_download_tasks WHERE id=?`).get(task.id) as Record<string, unknown>;
  assert.equal(pendingTask.status, "downloading");
  assert.ok(pendingTask.importBatchId);
  assert.equal(pendingTask.fileHash, createHash("sha256").update(bytes).digest("hex"));
  assert.equal(pendingTask.rowCount, 1);
  assert.deepEqual([pendingTask.headerValid, pendingTask.periodValid, pendingTask.categoryValid, pendingTask.dimensionValid], [1, 1, 1, 1]);

  sqlite.exec("DROP TRIGGER reject_all_imported_task_updates;");
  const reconciled = await executeMarketDownloadTask(db as never, { taskId: task.id }, admin);
  assert.equal(reconciled.status, "imported");
  assert.equal(reconciled.reconciled, true);
  assert.equal(downloads, 1);
  assert.equal((sqlite.prepare("SELECT status FROM market_download_tasks WHERE id=?").get(task.id) as { status: string }).status, "imported");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE sku_code='SKU-RECONCILE'").get() as { count: number }).count, 1);
  assert.deepEqual(await planMissingMarketDownloads(db as never, {}, admin), { created: 0, reused: 0 });
  sqlite.close();
});

test("same downloaded bytes remain idempotent per category scope dimension and month", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await upsertMarketDownloadConfig(db as never, { category: "category-identity", scope: "pop", rankingDimension: "SKU", monthStart: "2026-06", monthEnd: "2026-07" }, admin);
  await planMissingMarketDownloads(db as never, {}, admin);
  const tasks = sqlite.prepare("SELECT id, month FROM market_download_tasks ORDER BY month").all() as Array<{ id: string; month: string }>;
  const csv = [
    "category,scope,dimension,rank,sku_code,product_name,brand,price,gmv,quantity,visitors",
    "category-identity,pop,SKU,1,SKU-SAME,Same bytes,Brand,1999,10000,5,20",
  ].join("\n");
  for (const task of tasks) {
    const result = await executeMarketDownloadTask(db as never, { taskId: task.id }, admin, {
      download: async () => ({ bytes: new TextEncoder().encode(csv), fileName: "same.csv" }),
    });
    assert.equal(result.status, "imported");
  }
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_batches").get() as { count: number }).count, 2);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE sku_code='SKU-SAME'").get() as { count: number }).count, 2);
  assert.deepEqual((sqlite.prepare("SELECT DISTINCT substr(period_end,1,7) month FROM market_ranking_entries ORDER BY month").all() as Array<{ month: string }>).map((row) => row.month), ["2026-06", "2026-07"]);
  sqlite.close();
});

test("download execution claim prevents concurrent imports and waiting login does not consume retries", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await upsertMarketDownloadConfig(db as never, { category: "category-claim", scope: "pop", rankingDimension: "SKU", monthStart: "2026-06", monthEnd: "2026-06" }, admin);
  await planMissingMarketDownloads(db as never, {}, admin);
  const task = sqlite.prepare("SELECT id FROM market_download_tasks LIMIT 1").get() as { id: string };
  const waiting = await executeMarketDownloadTask(db as never, { taskId: task.id }, admin);
  assert.equal(waiting.status, "waiting_login");
  assert.equal((sqlite.prepare("SELECT attempt_count count FROM market_download_tasks WHERE id=?").get(task.id) as { count: number }).count, 0);
  let release!: () => void;
  let started!: () => void;
  const startedPromise = new Promise<void>((resolve) => { started = resolve; });
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const csv = [
    "period_start,period_end,category,scope,dimension,rank,sku_code,product_name,brand,gmv,quantity",
    "2026-06-01,2026-06-30,category-claim,pop,SKU,1,SKU-CLAIM,Claim,Brand,100,1",
  ].join("\n");
  const first = executeMarketDownloadTask(db as never, { taskId: task.id }, admin, { download: async () => { started(); await gate; return { bytes: new TextEncoder().encode(csv), fileName: "claim.csv" }; } });
  await startedPromise;
  const concurrent = await executeMarketDownloadTask(db as never, { taskId: task.id }, admin, { download: async () => ({ bytes: new TextEncoder().encode(csv), fileName: "claim.csv" }) });
  assert.equal(concurrent.busy, true);
  await assert.rejects(() => recordMarketDownloadAttempt(db as never, { taskId: task.id, status: "waiting_login" }, admin), /不能从客户端改写/);
  release();
  assert.equal((await first).status, "imported");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_batches").get() as { count: number }).count, 1);
  await assert.rejects(() => recordMarketDownloadAttempt(db as never, { taskId: task.id, status: "imported" as never }, admin), /客户端/);
  sqlite.close();
});

test("download executor takes over an expired pre-publish execution lease", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await upsertMarketDownloadConfig(db as never, { category: "category-stale-lease", scope: "pop", rankingDimension: "SKU", monthStart: "2026-06", monthEnd: "2026-06" }, admin);
  await planMissingMarketDownloads(db as never, {}, admin);
  const task = sqlite.prepare("SELECT id FROM market_download_tasks LIMIT 1").get() as { id: string };
  sqlite.prepare(`UPDATE market_download_tasks SET status='downloading', last_attempt_at='2026-01-01T00:00:00.000Z' WHERE id=?`).run(task.id);
  const csv = [
    "period_start,period_end,category,scope,dimension,rank,sku_code,product_name,brand,gmv,quantity",
    "2026-06-01,2026-06-30,category-stale-lease,pop,SKU,1,SKU-STALE,Recovered,Brand,400,4",
  ].join("\n");

  const result = await executeMarketDownloadTask(db as never, { taskId: task.id }, admin, {
    download: async () => ({ bytes: new TextEncoder().encode(csv), fileName: "stale.csv" }),
  });

  assert.equal(result.status, "imported");
  assert.equal((sqlite.prepare("SELECT status FROM market_download_tasks WHERE id=?").get(task.id) as { status: string }).status, "imported");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE sku_code='SKU-STALE'").get() as { count: number }).count, 1);
  sqlite.close();
});

test("download execution fencing rejects an expired worker that resumes after takeover", async () => {
  const sqlite = new DatabaseSync(":memory:");
  let pauseOldWorker = false;
  let oldWorkerPaused = false;
  let signalOldWorkerPaused!: () => void;
  let resumeOldWorker!: () => void;
  const oldWorkerPausedPromise = new Promise<void>((resolve) => { signalOldWorkerPaused = resolve; });
  const resumeOldWorkerPromise = new Promise<void>((resolve) => { resumeOldWorker = resolve; });
  const oldWorkerDb = sqliteAdapter(sqlite, {
    afterRun: async (sql) => {
      if (!pauseOldWorker || oldWorkerPaused || !sql.includes("SET jd_task_id=COALESCE") || !sql.includes("execution_token=?")) return;
      oldWorkerPaused = true;
      signalOldWorkerPaused();
      await resumeOldWorkerPromise;
    },
  });
  const newWorkerDb = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(oldWorkerDb);
  await upsertMarketDownloadConfig(oldWorkerDb as never, { category: "category-fence", scope: "pop", rankingDimension: "SKU", monthStart: "2026-06", monthEnd: "2026-06" }, admin);
  await planMissingMarketDownloads(oldWorkerDb as never, {}, admin);
  const task = sqlite.prepare("SELECT id FROM market_download_tasks LIMIT 1").get() as { id: string };
  const oldCsv = [
    "period_start,period_end,category,scope,dimension,rank,sku_code,product_name,brand,gmv,quantity",
    "2026-06-01,2026-06-30,category-fence,pop,SKU,1,SKU-OLD,Old worker,Brand,100,1",
  ].join("\n");
  const newCsv = [
    "period_start,period_end,category,scope,dimension,rank,sku_code,product_name,brand,gmv,quantity",
    "2026-06-01,2026-06-30,category-fence,pop,SKU,1,SKU-NEW,New worker,Brand,500,5",
  ].join("\n");
  pauseOldWorker = true;
  const oldExecution = executeMarketDownloadTask(oldWorkerDb as never, { taskId: task.id }, admin, {
    download: async () => ({ bytes: new TextEncoder().encode(oldCsv), fileName: "old.csv" }),
  });
  await oldWorkerPausedPromise;
  sqlite.prepare("UPDATE market_download_tasks SET last_attempt_at='2026-01-01T00:00:00.000Z' WHERE id=?").run(task.id);
  let signalNewWorkerClaimed!: () => void;
  let resumeNewWorker!: () => void;
  const newWorkerClaimed = new Promise<void>((resolve) => { signalNewWorkerClaimed = resolve; });
  const resumeNewWorkerPromise = new Promise<void>((resolve) => { resumeNewWorker = resolve; });
  const newExecution = executeMarketDownloadTask(newWorkerDb as never, { taskId: task.id }, admin, {
    download: async () => {
      signalNewWorkerClaimed();
      await resumeNewWorkerPromise;
      return { bytes: new TextEncoder().encode(newCsv), fileName: "new.csv" };
    },
  });
  await newWorkerClaimed;
  resumeOldWorker();
  const fenced = await oldExecution;
  assert.notEqual(fenced.status, "imported");
  resumeNewWorker();
  assert.equal((await newExecution).status, "imported");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE sku_code='SKU-OLD'").get() as { count: number }).count, 0);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE sku_code='SKU-NEW'").get() as { count: number }).count, 1);
  assert.equal((sqlite.prepare("SELECT source_file_name fileName FROM market_download_tasks WHERE id=?").get(task.id) as { fileName: string }).fileName, "new.csv");
  sqlite.close();
});

test("fenced worker cannot clean up a same-hash batch owned by its replacement", async () => {
  const sqlite = new DatabaseSync(":memory:");
  let pauseOldWorker = false;
  let oldWorkerPaused = false;
  let signalOldWorkerPaused!: () => void;
  let resumeOldWorker!: () => void;
  const oldWorkerPausedPromise = new Promise<void>((resolve) => { signalOldWorkerPaused = resolve; });
  const resumeOldWorkerPromise = new Promise<void>((resolve) => { resumeOldWorker = resolve; });
  const oldWorkerDb = sqliteAdapter(sqlite, {
    afterRun: async (sql) => {
      if (!pauseOldWorker || oldWorkerPaused || !sql.includes("SET jd_task_id=COALESCE") || !sql.includes("execution_token=?")) return;
      oldWorkerPaused = true;
      signalOldWorkerPaused();
      await resumeOldWorkerPromise;
    },
  });
  let pauseReplacement = false;
  let replacementPaused = false;
  let signalReplacementPaused!: () => void;
  let resumeReplacement!: () => void;
  const replacementPausedPromise = new Promise<void>((resolve) => { signalReplacementPaused = resolve; });
  const resumeReplacementPromise = new Promise<void>((resolve) => { resumeReplacement = resolve; });
  const replacementDb = sqliteAdapter(sqlite, {
    afterFirst: async (sql) => {
      if (!pauseReplacement || replacementPaused || !sql.includes("market_import_staging_rows s") || !sql.includes("JOIN market_ranking_entries m")) return;
      replacementPaused = true;
      signalReplacementPaused();
      await resumeReplacementPromise;
    },
  });
  await ensureMarketSchemaCore(oldWorkerDb);
  await upsertMarketDownloadConfig(oldWorkerDb as never, { category: "category-same-hash-fence", scope: "pop", rankingDimension: "SKU", monthStart: "2026-06", monthEnd: "2026-06" }, admin);
  await planMissingMarketDownloads(oldWorkerDb as never, {}, admin);
  const task = sqlite.prepare("SELECT id FROM market_download_tasks LIMIT 1").get() as { id: string };
  const csv = [
    "period_start,period_end,category,scope,dimension,rank,sku_code,product_name,brand,gmv,quantity",
    "2026-06-01,2026-06-30,category-same-hash-fence,pop,SKU,1,SKU-SAME-HASH,Same hash,Brand,700,7",
  ].join("\n");
  const bytes = new TextEncoder().encode(csv);
  pauseOldWorker = true;
  const oldExecution = executeMarketDownloadTask(oldWorkerDb as never, { taskId: task.id }, admin, {
    download: async () => ({ bytes, fileName: "old-copy.csv" }),
  });
  await oldWorkerPausedPromise;
  sqlite.prepare("UPDATE market_download_tasks SET last_attempt_at='2026-01-01T00:00:00.000Z' WHERE id=?").run(task.id);
  pauseReplacement = true;
  const replacementExecution = executeMarketDownloadTask(replacementDb as never, { taskId: task.id }, admin, {
    download: async () => ({ bytes, fileName: "replacement-copy.csv" }),
  });
  await replacementPausedPromise;
  resumeOldWorker();
  assert.notEqual((await oldExecution).status, "imported");
  assert.equal((sqlite.prepare("SELECT status FROM market_import_batches LIMIT 1").get() as { status: string }).status, "processing");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_staging_rows").get() as { count: number }).count, 1);
  resumeReplacement();
  assert.equal((await replacementExecution).status, "imported");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE sku_code='SKU-SAME-HASH'").get() as { count: number }).count, 1);
  assert.equal((sqlite.prepare("SELECT source_file_name fileName FROM market_download_tasks WHERE id=?").get(task.id) as { fileName: string }).fileName, "replacement-copy.csv");
  sqlite.close();
});

test("persistent batch ownership prevents ABA cleanup after a same-id replacement", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const pausePoint = (sql: string) => sql.includes("market_import_staging_rows s") && sql.includes("JOIN market_ranking_entries m");
  let pauseOld = false;
  let oldPaused = false;
  let signalOldPaused!: () => void;
  let resumeOld!: () => void;
  const oldPausedPromise = new Promise<void>((resolve) => { signalOldPaused = resolve; });
  const resumeOldPromise = new Promise<void>((resolve) => { resumeOld = resolve; });
  const oldDb = sqliteAdapter(sqlite, {
    afterFirst: async (sql) => {
      if (!pauseOld || oldPaused || !pausePoint(sql)) return;
      oldPaused = true;
      signalOldPaused();
      await resumeOldPromise;
    },
  });
  let pauseReplacement = false;
  let replacementPaused = false;
  let signalReplacementPaused!: () => void;
  let resumeReplacement!: () => void;
  const replacementPausedPromise = new Promise<void>((resolve) => { signalReplacementPaused = resolve; });
  const resumeReplacementPromise = new Promise<void>((resolve) => { resumeReplacement = resolve; });
  const replacementDb = sqliteAdapter(sqlite, {
    afterFirst: async (sql) => {
      if (!pauseReplacement || replacementPaused || !pausePoint(sql)) return;
      replacementPaused = true;
      signalReplacementPaused();
      await resumeReplacementPromise;
    },
  });
  await ensureMarketSchemaCore(oldDb);
  await upsertMarketDownloadConfig(oldDb as never, { category: "category-batch-aba", scope: "pop", rankingDimension: "SKU", monthStart: "2026-06", monthEnd: "2026-06" }, admin);
  await planMissingMarketDownloads(oldDb as never, {}, admin);
  const task = sqlite.prepare("SELECT id FROM market_download_tasks LIMIT 1").get() as { id: string };
  const csv = [
    "period_start,period_end,category,scope,dimension,rank,sku_code,product_name,brand,gmv,quantity",
    "2026-06-01,2026-06-30,category-batch-aba,pop,SKU,1,SKU-ABA,ABA,Brand,800,8",
  ].join("\n");
  const bytes = new TextEncoder().encode(csv);
  pauseOld = true;
  const oldExecution = executeMarketDownloadTask(oldDb as never, { taskId: task.id }, admin, {
    download: async () => ({ bytes, fileName: "old-aba.csv" }),
  });
  await oldPausedPromise;
  const oldOwner = (sqlite.prepare("SELECT owner_token owner FROM market_import_batches LIMIT 1").get() as { owner: string }).owner;
  assert.ok(oldOwner);
  sqlite.prepare("UPDATE market_download_tasks SET last_attempt_at='2026-01-01T00:00:00.000Z' WHERE id=?").run(task.id);
  sqlite.prepare("UPDATE market_import_batches SET created_at='2026-01-01T00:00:00.000Z'").run();
  pauseReplacement = true;
  const replacementExecution = executeMarketDownloadTask(replacementDb as never, { taskId: task.id }, admin, {
    download: async () => ({ bytes, fileName: "replacement-aba.csv" }),
  });
  await replacementPausedPromise;
  const replacementOwner = (sqlite.prepare("SELECT owner_token owner FROM market_import_batches LIMIT 1").get() as { owner: string }).owner;
  assert.ok(replacementOwner);
  assert.notEqual(replacementOwner, oldOwner);
  resumeOld();
  assert.notEqual((await oldExecution).status, "imported");
  assert.deepEqual({ ...(sqlite.prepare("SELECT status,owner_token owner FROM market_import_batches LIMIT 1").get() as Record<string, unknown>) }, { status: "processing", owner: replacementOwner });
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_staging_rows").get() as { count: number }).count, 1);
  resumeReplacement();
  assert.equal((await replacementExecution).status, "imported");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE sku_code='SKU-ABA'").get() as { count: number }).count, 1);
  assert.equal((sqlite.prepare("SELECT source_file_name fileName FROM market_download_tasks WHERE id=?").get(task.id) as { fileName: string }).fileName, "replacement-aba.csv");
  sqlite.close();
});

test("expired batch owner cannot claim a replacement owner's import range", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const batchCreated = (sql: string) => sql.includes("INSERT OR IGNORE INTO market_import_batches");
  let pauseOld = false;
  let oldPaused = false;
  let signalOldPaused!: () => void;
  let resumeOld!: () => void;
  const oldPausedPromise = new Promise<void>((resolve) => { signalOldPaused = resolve; });
  const resumeOldPromise = new Promise<void>((resolve) => { resumeOld = resolve; });
  const oldDb = sqliteAdapter(sqlite, {
    afterRun: async (sql) => {
      if (!pauseOld || oldPaused || !batchCreated(sql)) return;
      oldPaused = true;
      signalOldPaused();
      await resumeOldPromise;
    },
  });
  let pauseReplacement = false;
  let replacementPaused = false;
  let signalReplacementPaused!: () => void;
  let resumeReplacement!: () => void;
  const replacementPausedPromise = new Promise<void>((resolve) => { signalReplacementPaused = resolve; });
  const resumeReplacementPromise = new Promise<void>((resolve) => { resumeReplacement = resolve; });
  const replacementDb = sqliteAdapter(sqlite, {
    afterRun: async (sql) => {
      if (!pauseReplacement || replacementPaused || !batchCreated(sql)) return;
      replacementPaused = true;
      signalReplacementPaused();
      await resumeReplacementPromise;
    },
  });
  await ensureMarketSchemaCore(oldDb);
  await upsertMarketDownloadConfig(oldDb as never, { category: "category-claim-owner", scope: "pop", rankingDimension: "SKU", monthStart: "2026-06", monthEnd: "2026-06" }, admin);
  await planMissingMarketDownloads(oldDb as never, {}, admin);
  const task = sqlite.prepare("SELECT id FROM market_download_tasks LIMIT 1").get() as { id: string };
  const csv = [
    "period_start,period_end,category,scope,dimension,rank,sku_code,product_name,brand,gmv,quantity",
    "2026-06-01,2026-06-30,category-claim-owner,pop,SKU,1,SKU-CLAIM-OWNER,Owner,Brand,900,9",
  ].join("\n");
  const bytes = new TextEncoder().encode(csv);
  pauseOld = true;
  const oldExecution = executeMarketDownloadTask(oldDb as never, { taskId: task.id }, admin, {
    download: async () => ({ bytes, fileName: "old-owner.csv" }),
  });
  await oldPausedPromise;
  const oldOwner = (sqlite.prepare("SELECT owner_token owner FROM market_import_batches LIMIT 1").get() as { owner: string }).owner;
  sqlite.prepare("UPDATE market_download_tasks SET last_attempt_at='2026-01-01T00:00:00.000Z' WHERE id=?").run(task.id);
  sqlite.prepare("UPDATE market_import_batches SET created_at='2026-01-01T00:00:00.000Z'").run();
  pauseReplacement = true;
  const replacementExecution = executeMarketDownloadTask(replacementDb as never, { taskId: task.id }, admin, {
    download: async () => ({ bytes, fileName: "replacement-owner.csv" }),
  });
  await replacementPausedPromise;
  const replacementOwner = (sqlite.prepare("SELECT owner_token owner FROM market_import_batches LIMIT 1").get() as { owner: string }).owner;
  assert.notEqual(replacementOwner, oldOwner);
  resumeOld();
  assert.notEqual((await oldExecution).status, "imported");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_import_range_claims").get() as { count: number }).count, 0);
  assert.deepEqual({ ...(sqlite.prepare("SELECT status,owner_token owner FROM market_import_batches LIMIT 1").get() as Record<string, unknown>) }, { status: "processing", owner: replacementOwner });
  resumeReplacement();
  assert.equal((await replacementExecution).status, "imported");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_ranking_entries WHERE sku_code='SKU-CLAIM-OWNER'").get() as { count: number }).count, 1);
  sqlite.close();
});

test("active download worker still runs maintenance when a concurrent request reconciles its completed batch", async () => {
  const sqlite = new DatabaseSync(":memory:");
  let pauseCompletion = false;
  let completionPaused = false;
  let signalCompletionPaused!: () => void;
  let resumeCompletion!: () => void;
  const completionPausedPromise = new Promise<void>((resolve) => { signalCompletionPaused = resolve; });
  const resumeCompletionPromise = new Promise<void>((resolve) => { resumeCompletion = resolve; });
  const activeDb = sqliteAdapter(sqlite, {
    beforeRun: async (sql) => {
      if (!pauseCompletion || completionPaused || !sql.includes("SET status='imported', execution_token=''")) return;
      completionPaused = true;
      signalCompletionPaused();
      await resumeCompletionPromise;
    },
  });
  const reconcilerDb = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(activeDb);
  await upsertMarketDownloadConfig(activeDb as never, { category: "category-live-reconcile", scope: "pop", rankingDimension: "SKU", monthStart: "2026-06", monthEnd: "2026-06" }, admin);
  await planMissingMarketDownloads(activeDb as never, {}, admin);
  const task = sqlite.prepare("SELECT id FROM market_download_tasks LIMIT 1").get() as { id: string };
  const csv = [
    "period_start,period_end,category,scope,dimension,rank,sku_code,product_name,brand,gmv,quantity",
    "2026-06-01,2026-06-30,category-live-reconcile,pop,SKU,1,SKU-LIVE,Live,Brand,600,6",
  ].join("\n");
  let cached = 0;
  let prices = 0;
  pauseCompletion = true;
  const activeExecution = executeMarketDownloadTask(activeDb as never, { taskId: task.id }, admin, {
    download: async () => ({ bytes: new TextEncoder().encode(csv), fileName: "live.csv" }),
    cacheImages: async () => { cached += 1; return { queued: 1 }; },
    createPriceTasks: async () => { prices += 1; return { created: 1 }; },
  });
  await completionPausedPromise;
  const reconciled = await executeMarketDownloadTask(reconcilerDb as never, { taskId: task.id }, admin);
  assert.equal(reconciled.status, "imported");
  assert.equal(reconciled.reconciled, true);
  resumeCompletion();
  assert.equal((await activeExecution).status, "imported");
  assert.equal(cached, 1);
  assert.equal(prices, 1);
  sqlite.close();
});

test("market price band configuration versions publish and rollback reproducibly", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, sku_code, product_name, brand, gmv_cents, quantity, visitors, raw_json, last_import_batch_id)
    VALUES ('key-1',1,'2026-06-01','2026-06-30','净水','pop','SKU','POP','SKU-1','商品1','品牌1',1000,1,1,'{}','batch');
    INSERT INTO market_price_snapshots (id, category, sku_code, ranking_dimension, month, confirmed_market_price_cents, confirmation_status)
    VALUES ('ps-1','净水','SKU-1','SKU','2026-06',150000,'confirmed');`);
  const firstDraft = await createMarketPriceBandVersion(db as never, {
    category: "净水",
    items: [{ label: "stable-low", minCents: 0, maxCents: 160000 }, { label: "stable-high", minCents: 160000, maxCents: null }],
  }, admin);
  await publishMarketPriceBandVersion(db as never, firstDraft.id, admin);
  const draft = await createMarketPriceBandVersion(db as never, {
    category: "净水",
    items: [{ label: "custom-low", minCents: 0, maxCents: 200000 }, { label: "custom-high", minCents: 200000, maxCents: null }],
  }, admin);
  await publishMarketPriceBandVersion(db as never, draft.id, admin);
  let row = sqlite.prepare(`SELECT ${officialPriceBandSql("ps.confirmed_market_price_cents")} band
    FROM market_ranking_entries m JOIN market_price_snapshots ps ON ps.category=m.category AND ps.sku_code=m.sku_code AND ps.ranking_dimension=m.ranking_dimension AND ps.month=substr(m.period_end,1,7)`).get() as { band: string };
  assert.equal(row.band, "custom-low");
  await rollbackMarketPriceBandVersion(db as never, { targetVersionId: firstDraft.id }, admin);
  row = sqlite.prepare(`SELECT ${officialPriceBandSql("ps.confirmed_market_price_cents")} band
    FROM market_ranking_entries m JOIN market_price_snapshots ps ON ps.category=m.category AND ps.sku_code=m.sku_code AND ps.ranking_dimension=m.ranking_dimension AND ps.month=substr(m.period_end,1,7)`).get() as { band: string };
  assert.equal(row.band, "stable-low");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_master_audit_logs WHERE action IN ('create_price_band_version','publish_price_band_version','rollback_price_band_version')").get() as { count: number }).count, 5);
  sqlite.close();
});

test("market download tasks are idempotent and failed tasks recover until the third failure", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  await upsertMarketDownloadConfig(db as never, { category: "净水", rankingDimension: "SKU", monthStart: "2026-06", monthEnd: "2026-07" }, admin);
  assert.deepEqual(await planMissingMarketDownloads(db as never, {}, admin), { created: 2, reused: 0 });
  await planMissingMarketDownloads(db as never, {}, admin);
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_download_tasks").get() as { count: number }).count, 2);
  const task = sqlite.prepare("SELECT id FROM market_download_tasks WHERE month='2026-06'").get() as { id: string };
  await recordMarketDownloadAttempt(db as never, { taskId: task.id, status: "failed", errorCode: "network", errorMessage: "timeout" }, admin);
  await planMissingMarketDownloads(db as never, {}, admin);
  let row = sqlite.prepare("SELECT status, attempt_count attemptCount FROM market_download_tasks WHERE id=?").get(task.id) as { status: string; attemptCount: number };
  assert.deepEqual({ ...row }, { status: "planned", attemptCount: 1 });
  await recordMarketDownloadAttempt(db as never, { taskId: task.id, status: "failed", errorCode: "network", errorMessage: "timeout" }, admin);
  await recordMarketDownloadAttempt(db as never, { taskId: task.id, status: "failed", errorCode: "network", errorMessage: "timeout" }, admin);
  await planMissingMarketDownloads(db as never, {}, admin);
  row = sqlite.prepare("SELECT status, attempt_count attemptCount FROM market_download_tasks WHERE id=?").get(task.id) as { status: string; attemptCount: number };
  assert.deepEqual({ ...row }, { status: "failed", attemptCount: 3 });
  await upsertMarketDownloadConfig(db as never, { category: "scope-category", scope: "pop", rankingDimension: "SKU", monthStart: "2026-06", monthEnd: "2026-06" }, admin);
  await upsertMarketDownloadConfig(db as never, { category: "scope-category", scope: "self", rankingDimension: "SKU", monthStart: "2026-06", monthEnd: "2026-06" }, admin);
  assert.deepEqual(await planMissingMarketDownloads(db as never, { category: "scope-category" }, admin), { created: 2, reused: 0 });
  assert.equal((sqlite.prepare("SELECT COUNT(DISTINCT scope) count FROM market_download_tasks WHERE category='scope-category'").get() as { count: number }).count, 2);
  sqlite.close();
});

test("market SKU comparison returns real metrics and monthly trends for 2 to 5 SKUs", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec("CREATE TABLE netshop_rows (source TEXT, dataset TEXT, business_date TEXT, sku_id TEXT, spu_id TEXT, product_code TEXT, metrics_json TEXT)");
  const insert = sqlite.prepare(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, rank, sku_code, product_name, brand, gmv_cents, quantity, visitors, raw_json, last_import_batch_id)
    VALUES (?, ?, ?, ?, '净水', 'pop', 'SKU', 'POP', ?, ?, ?, ?, ?, ?, ?, '{}', 'batch')`);
  insert.run("a1", 1, "2026-05-01", "2026-05-31", 2, "SKU-A", "商品A", "品牌A", 1000, 2, 10);
  insert.run("a2", 2, "2026-06-01", "2026-06-30", 1, "SKU-A", "商品A新标题", "品牌A", 3000, 3, 10);
  insert.run("b1", 3, "2026-06-01", "2026-06-30", 3, "SKU-B", "商品B", "品牌B", 2000, 4, 20);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, rank, sku_code, product_name, brand, gmv_cents, quantity, visitors, raw_json, last_import_batch_id)
    VALUES ('a-self',4,'2026-06-01','2026-06-30','净水','self','SKU','自营',5,'SKU-A','商品A','品牌A',9000,9,9,'{}','batch')`);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, rank, sku_code, product_name, brand, gmv_cents, quantity, visitors, raw_json, last_import_batch_id)
    VALUES
      ('a-day',5,'2026-06-15','2026-06-15','净水','pop','SKU','POP',1,'SKU-A','商品A日榜','品牌A',777,7,7,'{}','batch'),
      ('a-rolling',6,'2026-06-02','2026-06-30','净水','pop','SKU','POP',1,'SKU-A','商品A滚动榜','品牌A',999999,99,99,'{}','batch')`);
  sqlite.exec(`INSERT INTO market_price_snapshots (id, category, sku_code, ranking_dimension, month, confirmed_market_price_cents, average_transaction_price_cents, confirmation_status)
    VALUES ('pa','净水','SKU-A','SKU','2026-06',120000,1000,'confirmed'), ('pb','净水','SKU-B','SKU','2026-06',90000,500,'confirmed');`);
  const compared = await getMarketSkuComparison(db as never, { skuCodes: ["SKU-A", "SKU-B"], categories: ["净水"], rankingDimensions: ["SKU"], operationModes: ["POP"] });
  assert.equal(compared.items.length, 2);
  assert.deepEqual(compared.missingSkuCodes, []);
  assert.equal(compared.items.find((item) => item.skuCode === "SKU-A")?.gmvCents, 4000);
  assert.equal(compared.items.find((item) => item.skuCode === "SKU-A")?.productName, "商品A新标题");
  assert.equal(compared.items.find((item) => item.skuCode === "SKU-A")?.trend.length, 2);
  assert.equal(compared.items.find((item) => item.skuCode === "SKU-A")?.trend.at(-1)?.gmvCents, 3000);
  assert.equal(compared.items.find((item) => item.skuCode === "SKU-B")?.bestRank, 3);
  const withMissing = await getMarketSkuComparison(db as never, {
    skuCodes: ["SKU-A", "SKU-MISSING"],
    categories: ["净水"],
    rankingDimensions: ["SKU"],
    operationModes: ["POP"],
  });
  assert.deepEqual(withMissing.missingSkuCodes, ["SKU-MISSING"]);
  const canonicalSkuCode = "A".repeat(80);
  await assert.rejects(
    () => getMarketSkuComparison(db as never, { skuCodes: [canonicalSkuCode, `${canonicalSkuCode}X`] }),
    /2 到 5 个 SKU/,
  );
  await assert.rejects(
    () => getMarketSkuComparison(db as never, { skuCodes: ["1", "2", "3", "4", "5", "6"] }),
    /2 到 5 个 SKU/,
  );
  sqlite.close();
});

test("market SKU comparison keeps exact category scope dimension identities and applies search", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`CREATE TABLE netshop_rows (source TEXT, dataset TEXT, business_date TEXT, sku_id TEXT, spu_id TEXT, product_code TEXT, metrics_json TEXT);
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,ranking_dimension,operation_mode,rank,sku_code,product_name,brand,gmv_cents,quantity,visitors,raw_json,last_import_batch_id)
    VALUES
      ('exact-a-pop',1,'2026-07-01','2026-07-31','exact-category','POP','SKU','POP',1,'EXACT-A','Hidden A POP','HiddenBrand',100,1,10,'{}','b'),
      ('exact-a-self',2,'2026-07-01','2026-07-31','exact-category','self','SKU','自营',2,'EXACT-A','Visible A self','VisibleBrand',900,9,10,'{}','b'),
      ('exact-b-self',3,'2026-07-01','2026-07-31','exact-category','self','SKU','自营',3,'EXACT-B','Visible B self','VisibleBrand',200,2,10,'{}','b');`);
  const aPop = { skuCode: "EXACT-A", category: "exact-category", scope: "POP", rankingDimension: "SKU" as const };
  const aSelf = { skuCode: "EXACT-A", category: "exact-category", scope: "self", rankingDimension: "SKU" as const };
  const bSelf = { skuCode: "EXACT-B", category: "exact-category", scope: "self", rankingDimension: "SKU" as const };

  const brandFiltered = await getMarketSkuComparison(db as never, {
    selections: [aPop, bSelf],
    brands: ["VisibleBrand"],
  });
  assert.deepEqual(brandFiltered.items.map((item) => [item.skuCode, item.scope, item.gmvCents]), [["EXACT-B", "self", 200]]);
  assert.deepEqual(brandFiltered.missingSelections, [aPop]);

  const searchFiltered = await getMarketSkuComparison(db as never, {
    selections: [aPop, bSelf],
    q: "Visible B",
  });
  assert.deepEqual(searchFiltered.items.map((item) => item.skuCode), ["EXACT-B"]);
  assert.deepEqual(searchFiltered.missingSelections, [aPop]);

  const sameSkuDifferentScopes = await getMarketSkuComparison(db as never, { selections: [aPop, aSelf] });
  assert.deepEqual(sameSkuDifferentScopes.items.map((item) => [item.scope, item.gmvCents]).sort(), [["POP", 100], ["self", 900]]);
  assert.deepEqual(sameSkuDifferentScopes.missingSelections, []);
  sqlite.close();
});

test("market SKU comparison price-band filters fall back to displayed import prices after month selection", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`CREATE TABLE netshop_rows (source TEXT, dataset TEXT, business_date TEXT, sku_id TEXT, spu_id TEXT, product_code TEXT, metrics_json TEXT);
    INSERT INTO market_ranking_entries
      (natural_key,source_row_number,period_start,period_end,category,scope,price_band_filter,ranking_dimension,operation_mode,rank,sku_code,product_name,brand,price_cents,gmv_cents,quantity,visitors,raw_json,last_import_batch_id)
    VALUES
      ('fallback-a',1,'2026-07-01','2026-07-31','comparison-fallback','POP','','SKU','POP',1,'SKU-FALLBACK-A','Fallback A','Brand A',129900,1000,2,10,'{}','b'),
      ('fallback-b',2,'2026-07-01','2026-07-31','comparison-fallback','POP','','SKU','POP',2,'SKU-FALLBACK-B','Fallback B','Brand B',159900,2000,4,20,'{}','b');`);

  const compared = await getMarketSkuComparison(db as never, {
    skuCodes: ["SKU-FALLBACK-A", "SKU-FALLBACK-B"],
    categories: ["comparison-fallback"],
    scopes: ["POP"],
    rankingDimensions: ["SKU"],
    priceBands: ["1000-1999"],
  });
  assert.deepEqual(compared.items.map((item) => item.skuCode).sort(), ["SKU-FALLBACK-A", "SKU-FALLBACK-B"]);
  assert.ok(compared.items.every((item) => item.marketPriceCents === null));
  assert.ok(compared.items.every((item) => item.trend.length === 1));
  sqlite.close();
});

test("market SKU comparison keeps the full 121-month summary while bounding trend metadata and UI", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec("CREATE TABLE netshop_rows (source TEXT, dataset TEXT, business_date TEXT, sku_id TEXT, spu_id TEXT, product_code TEXT, metrics_json TEXT)");
  const insert = sqlite.prepare(`INSERT INTO market_ranking_entries
    (natural_key,source_row_number,period_start,period_end,category,scope,price_band_filter,ranking_dimension,operation_mode,rank,sku_code,product_name,brand,gmv_cents,quantity,visitors,raw_json,last_import_batch_id)
    VALUES (?,?,?,?,'comparison-long','POP','','SKU','POP',1,?,?,?, ?,1,1,'{}','b')`);
  const months: string[] = [];
  let expectedGmv = 0;
  for (let index = 0; index < 121; index += 1) {
    const first = new Date(Date.UTC(2016, index, 1));
    const last = new Date(Date.UTC(2016, index + 1, 0));
    const periodStart = first.toISOString().slice(0, 10);
    const periodEnd = last.toISOString().slice(0, 10);
    const gmv = index + 1;
    months.push(periodEnd.slice(0, 7));
    expectedGmv += gmv;
    insert.run(`comparison-long-a-${index}`, index * 2 + 1, periodStart, periodEnd, "SKU-LONG-A", "Long A", "Brand A", gmv);
    insert.run(`comparison-long-b-${index}`, index * 2 + 2, periodStart, periodEnd, "SKU-LONG-B", "Long B", "Brand B", gmv);
  }

  const compared = await getMarketSkuComparison(db as never, {
    skuCodes: ["SKU-LONG-A", "SKU-LONG-B"],
    categories: ["comparison-long"],
    scopes: ["POP"],
    rankingDimensions: ["SKU"],
  });
  for (const item of compared.items) {
    assert.equal(item.gmvCents, expectedGmv);
    assert.equal(item.trend.length, 120);
    assert.equal(item.trendTotalMonths, 121);
    assert.equal(item.trendTruncated, true);
    assert.equal(item.trend[0]?.month, months[1]);
    assert.equal(item.trend.at(-1)?.month, months.at(-1));
  }

  const view = await readFile(new URL("../app/market-view.tsx", import.meta.url), "utf8");
  assert.match(view, /主指标按当前筛选范围完整汇总；月度火花图只展示最近 12 个月/);
  assert.match(view, /服务端趋势最近 120 \/ 共 \{count\(item\.trendTotalMonths\)\} 个月/);
  assert.match(view, /item\.trend\.slice\(-12\)/);
  sqlite.close();
});

test("manual brand confirmation persists a replayable per-product override", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureMarketSchemaCore(db);
  sqlite.exec(`INSERT INTO market_ranking_entries
    (natural_key, source_row_number, period_start, period_end, category, scope, ranking_dimension, operation_mode, rank, sku_code, product_name, brand, raw_json, last_import_batch_id)
    VALUES ('brand-empty',1,'2026-06-01','2026-06-30','净水','pop','SKU','POP',1,'SKU-BRAND','美的（Midea）商用净水机','', '{}','batch')`);
  await confirmMarketBrand(db as never, {
    category: "净水", scope: "pop", rankingDimension: "SKU", skuCode: "SKU-BRAND", brand: "美的",
  }, admin);
  assert.equal((sqlite.prepare("SELECT brand FROM market_ranking_entries WHERE sku_code='SKU-BRAND'").get() as { brand: string }).brand, "美的");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_master_mapping_rules WHERE kind='brand_override'").get() as { count: number }).count, 1);
  sqlite.exec("UPDATE market_ranking_entries SET brand='' WHERE sku_code='SKU-BRAND'");
  await applyPublishedMarketMappings(db as never, {}, admin);
  assert.equal((sqlite.prepare("SELECT brand FROM market_ranking_entries WHERE sku_code='SKU-BRAND'").get() as { brand: string }).brand, "美的");
  assert.equal((sqlite.prepare("SELECT COUNT(*) count FROM market_master_audit_logs WHERE action='confirm_market_brand'").get() as { count: number }).count, 1);
  sqlite.close();
});

test("central market AI tools enforce roles, bounded schemas, and audit execution", async () => {
  const registrySource = await readFile(new URL("../lib/ai/tool-registry.ts", import.meta.url), "utf8");
  const marketNames = ["get_market_overview", "get_market_sku_trend", "get_market_brand_analysis", "get_market_price_band_analysis", "get_market_pending_review_summary"];
  for (const name of marketNames) assert.match(registrySource, new RegExp(`name: "${name}"`));
  assert.match(registrySource, /additionalProperties: false/g);
  assert.match(registrySource, /risk: "read_only"/);
  assert.match(registrySource, /callMarketTool\("get_market_overview"/);
  const viewer: AppPrincipal = { email: "viewer@example.com", displayName: "Viewer", role: "viewer", scope: null };
  const analyst: AppPrincipal = { ...viewer, email: "analyst@example.com", role: "analyst" };
  const overviewEntry: AiToolEntry = {
    name: "get_market_overview",
    title: "Market overview",
    description: "Bounded market overview",
    inputSchema: {
      type: "object",
      properties: { category: { type: "string", maxLength: 120 } },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    risk: "read_only",
    allowedRoles: ["analyst", "operator", "admin"],
    scopePolicy: "unscoped_only",
    execution: {
      environment: "worker_inline",
      mode: "direct",
      allowedSurfaces: ["ai_chat", "test"],
      timeoutMs: 1_000,
      maxResultCharacters: 4_000,
      maxCallsPerRequest: 4,
    },
    handler: async () => ({ returned: 1, truncated: false }),
  };
  assert.equal(getOpenAiTools(viewer, "ai_chat", [overviewEntry]).some((item) => item.function.name === "get_market_overview"), false);
  assert.equal(getOpenAiTools(analyst, "ai_chat", [overviewEntry]).some((item) => item.function.name === "get_market_overview"), true);
  let called = false;
  const stubEntry: AiToolEntry = { ...overviewEntry, handler: async () => { called = true; return { returned: 1, truncated: false }; } };
  const audits: Array<Record<string, unknown>> = [];
  const invalid = await executeToolCallWithRegistry("get_market_overview", { arbitrarySql: "select *" }, { principal: analyst, surface: "test", requestId: "market-ai-invalid" }, {
    entries: [stubEntry],
    audit: async (input) => { audits.push(input); },
  });
  assert.equal(invalid.ok, false);
  assert.equal(called, false);
  const ok = await executeToolCallWithRegistry("get_market_overview", { category: "净水" }, { principal: analyst, surface: "test", requestId: "market-ai-ok" }, {
    entries: [stubEntry],
    audit: async (input) => { audits.push(input); },
  });
  assert.equal(ok.ok, true);
  assert.equal(called, true);
  assert.equal(audits.filter((item) => item.requestId === "market-ai-ok").length, 2);
});
