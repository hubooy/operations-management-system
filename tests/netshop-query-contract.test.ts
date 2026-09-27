import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  boundedNetshopInteger,
  isNetshopIsoDate,
  netshopOutletKey,
  NETSHOP_OUTLET_MAX_ITEMS,
  NETSHOP_PROMOTION_QUERY_MAX_PAGE_SIZE,
  NETSHOP_QUERY_MAX_PAGE,
  NETSHOP_QUERY_MAX_PAGE_SIZE,
  NetshopQueryError,
  netshopQueryErrorPayload,
  normalizeNetshopOutletFilters,
  readNetshopOutletFilters,
  readNetshopProductCatalogView,
  readNetshopProductPerformanceView,
  readNetshopQueryInteger,
  readNetshopSnapshotToken,
  resolveNetshopQueryPeriod,
} from "../lib/netshop/query-contract";
import { NetshopSchemaUpgradePendingError } from "../lib/netshop/daily-row-migration";

test("netshop periods require valid paired natural dates and expose a left-closed right-open boundary", () => {
  assert.equal(resolveNetshopQueryPeriod(undefined, undefined), null);
  assert.deepEqual(resolveNetshopQueryPeriod("2026-08-01", "2026-08-18"), {
    startDate: "2026-08-01",
    endDate: "2026-08-18",
    endExclusive: "2026-08-19",
    days: 18,
  });
  assert.equal(isNetshopIsoDate("2024-02-29"), true);
  assert.equal(isNetshopIsoDate("2026-02-29"), false);
  assert.throws(() => resolveNetshopQueryPeriod("2026-08-01", undefined), NetshopQueryError);
  assert.throws(() => resolveNetshopQueryPeriod("2026-02-30", "2026-03-01"), /有效的 YYYY-MM-DD/);
  assert.throws(() => resolveNetshopQueryPeriod("2026-08-31", "2026-08-01"), /不能晚于/);
  assert.throws(() => resolveNetshopQueryPeriod("9999-12-31", "9999-12-31"), /超出支持范围/);
  assert.doesNotThrow(() => resolveNetshopQueryPeriod("2024-01-01", "2025-12-30"));
  assert.throws(() => resolveNetshopQueryPeriod("2024-01-01", "2025-12-31"), /最多支持 730 天/);
});

test("netshop pagination is strictly bounded at both URL and domain boundaries", () => {
  assert.equal(readNetshopQueryInteger(null, "page", 1, 1, NETSHOP_QUERY_MAX_PAGE), 1);
  assert.equal(readNetshopQueryInteger("10000", "page", 1, 1, NETSHOP_QUERY_MAX_PAGE), 10_000);
  assert.equal(boundedNetshopInteger(100, "pageSize", 50, 1, NETSHOP_QUERY_MAX_PAGE_SIZE), 100);
  assert.equal(boundedNetshopInteger(500, "pageSize", 20, 1, NETSHOP_PROMOTION_QUERY_MAX_PAGE_SIZE), 500);
  assert.throws(() => boundedNetshopInteger(501, "pageSize", 20, 1, NETSHOP_PROMOTION_QUERY_MAX_PAGE_SIZE), NetshopQueryError);
  for (const value of ["0", "1.5", "10001", "1e2", "1e308", "NaN", "+1", " 1 "]) {
    assert.throws(() => readNetshopQueryInteger(value, "page", 1, 1, NETSHOP_QUERY_MAX_PAGE), NetshopQueryError);
  }
  assert.throws(() => boundedNetshopInteger(Number.NaN, "pageSize", 50, 1, NETSHOP_QUERY_MAX_PAGE_SIZE), NetshopQueryError);
});

test("netshop product projections reject unknown or repeated views and fence tokens outside page", () => {
  const token = "a".repeat(64);
  assert.equal(readNetshopProductCatalogView([]), "full");
  assert.equal(readNetshopProductCatalogView(["page"]), "page");
  assert.equal(readNetshopProductPerformanceView([]), "full");
  assert.equal(readNetshopProductPerformanceView(["summary"]), "summary");
  for (const values of [["unknown"], ["full", "page"], [""]]) {
    assert.throws(() => readNetshopProductCatalogView(values), /view 必须且只能/);
  }
  for (const values of [["unknown"], ["summary", "page"], [""]]) {
    assert.throws(() => readNetshopProductPerformanceView(values), /view 必须且只能/);
  }
  assert.equal(readNetshopSnapshotToken([token], true), token);
  assert.throws(() => readNetshopSnapshotToken([], true), /必须提供 snapshotToken/);
  assert.throws(() => readNetshopSnapshotToken(["bad"], true), /64 位十六进制/);
  assert.throws(() => readNetshopSnapshotToken([token, token], true), /唯一/);
  assert.throws(() => readNetshopSnapshotToken([token], false), /只有 page 视图/);
});

test("netshop outlet filters preserve platform plus shop identity and reject malformed or oversized input", () => {
  const values = [
    netshopOutletKey("京东", "同名店"),
    netshopOutletKey("天猫", "同名店"),
    netshopOutletKey("京东", "同名店"),
  ];
  assert.deepEqual(readNetshopOutletFilters(values), [
    { platform: "京东", shopName: "同名店" },
    { platform: "天猫", shopName: "同名店" },
  ]);
  for (const invalid of ["同名店", "京东\u001f", "\u001f同名店", "京东\u001f同名店\u001f额外", "京\u0000东\u001f同名店"]) {
    assert.throws(() => readNetshopOutletFilters([invalid]), NetshopQueryError);
  }
  const oversized = Array.from({ length: NETSHOP_OUTLET_MAX_ITEMS + 1 }, () => netshopOutletKey("京东", "同名店"));
  assert.throws(() => readNetshopOutletFilters(oversized), /最多 50 项/);
  try {
    readNetshopOutletFilters(oversized);
    assert.fail("oversized outlet filter should fail");
  } catch (error) {
    assert.deepEqual(netshopQueryErrorPayload(error, "读取失败"), {
      body: { error: "outlet 筛选最多 50 项", code: "too_many_outlet_filters" },
      status: 400,
    });
  }
  assert.throws(
    () => normalizeNetshopOutletFilters(Array.from({ length: NETSHOP_OUTLET_MAX_ITEMS + 1 }, (_, index) => ({ platform: "京东", shopName: `店-${index}` }))),
    /最多 50 项/,
  );
});

test("netshop query errors are public while unexpected database details are redacted", () => {
  const expected = netshopQueryErrorPayload(new NetshopQueryError("invalid_date", "日期无效"), "读取失败");
  assert.deepEqual(expected, { body: { error: "日期无效", code: "invalid_date" }, status: 400 });
  const unexpected = netshopQueryErrorPayload(new Error("no such table: secret_rows"), "读取失败");
  assert.deepEqual(unexpected, { body: { error: "读取失败", code: "internal_error" }, status: 500 });
  const pending = netshopQueryErrorPayload(new NetshopSchemaUpgradePendingError(), "读取失败");
  assert.deepEqual(pending, {
    body: { error: "网店数据正在升级，请稍后重试", code: "service_unavailable" },
    status: 503,
  });
});

test("every netshop route validates at the edge and delegates to the bounded Django reader", async () => {
  const queryRoutes = await Promise.all([
    "products",
    "product-performance",
    "promotion-performance",
  ].map((name) => readFile(new URL(`../app/api/netshop/${name}/route.ts`, import.meta.url), "utf8")));
  const safeRoutes = await Promise.all([
    "overview",
    "import",
  ].map((name) => readFile(new URL(`../app/api/netshop/${name}/route.ts`, import.meta.url), "utf8")));
  for (const source of queryRoutes) {
    assert.match(source, /netshopQueryErrorPayload\(error,/);
    assert.match(source, /readNetshopOutletFilters\(params\.getAll\("outlet"\)\)/);
    assert.match(source, /params\.has\("shop"\)/);
    assert.doesNotMatch(source, /getAll\("shop"\)[\s\S]{0,120}slice\(0, 50\)/);
  }
  for (const source of safeRoutes) assert.match(source, /safeApiErrorResponse\(error,/);
  for (const source of [...queryRoutes, ...safeRoutes]) {
    assert.doesNotMatch(source, /error instanceof Error \? error\.message/);
    assert.match(source, /cache-control["']?:?\s*["']no-store/);
  }
  for (const source of queryRoutes) {
    assert.match(source, /createDjangoNetshopService\(\)\.request/);
    assert.doesNotMatch(source, /getNetshopDatabase|ensureNetshopSchema|netshop_rows/);
  }
  assert.match(queryRoutes[0]!, /readNetshopProductCatalogView\(params\.getAll\("view"\)\)/);
  assert.match(queryRoutes[0]!, /NETSHOP_PRODUCTS_PATH/);
  assert.match(queryRoutes[1]!, /readNetshopProductPerformanceView\(params\.getAll\("view"\)\)/);
  assert.match(queryRoutes[1]!, /NETSHOP_PRODUCT_PERFORMANCE_PATH/);
  for (const source of queryRoutes.slice(0, 2)) {
    assert.match(source, /readNetshopSnapshotToken\(params\.getAll\("snapshotToken"\), view === "page"\)/);
  }
  assert.match(queryRoutes[2]!, /params\.get\("pageSize"\), "pageSize", 20, 1, NETSHOP_PROMOTION_QUERY_MAX_PAGE_SIZE/);

  const promotionSplitRoutes = await Promise.all([
    "overview",
    "items",
  ].map((name) => readFile(new URL(`../app/api/netshop/promotion-performance/${name}/route.ts`, import.meta.url), "utf8")));
  for (const source of promotionSplitRoutes) {
    assert.match(source, /netshopQueryErrorPayload\(error,/);
    assert.match(source, /readNetshopOutletFilters\(params\.getAll\("outlet"\)\)/);
    assert.match(source, /params\.has\("shop"\)/);
    assert.match(source, /cache-control["']?:?\s*["']no-store/);
    assert.match(source, /if \(!period\).*invalid_date_range/);
    assert.match(source, /if \(!requestedPlatforms\.length\).*invalid_platform_filter/);
    assert.match(source, /createDjangoNetshopService\(\)\.request/);
    assert.doesNotMatch(source, /getNetshopDatabase|ensureNetshopSchema|netshop_rows/);
  }
  assert.doesNotMatch(promotionSplitRoutes[0]!, /readNetshopQueryInteger/);
  assert.match(promotionSplitRoutes[0]!, /params\.get\("snapshotToken"\)/);
  assert.match(promotionSplitRoutes[0]!, /NETSHOP_PROMOTION_OVERVIEW_PATH/);
  assert.match(promotionSplitRoutes[1]!, /params\.get\("pageSize"\), "pageSize", 20, 1, NETSHOP_PROMOTION_QUERY_MAX_PAGE_SIZE/);
});

test("promotion payment dependencies atomically bump bounded platform and exact-shop revisions", async () => {
  const [runtime, platformMigration, scopeMigration] = await Promise.all([
    readFile(new URL("../lib/netshop/database.ts", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0073_netshop_product_daily_revision.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0074_netshop_promotion_maintenance_fence.sql", import.meta.url), "utf8"),
  ]);
  for (const source of [runtime, platformMigration]) {
    assert.match(source, /CREATE TABLE IF NOT EXISTS netshop_product_daily_revisions/);
    assert.match(source, /platform TEXT PRIMARY KEY NOT NULL/);
    assert.match(source, /data_version INTEGER NOT NULL DEFAULT 0/);
  }
  for (const source of [runtime, scopeMigration]) {
    assert.match(source, /CREATE TABLE IF NOT EXISTS [`]?netshop_product_daily_scope_revisions[`]?/);
    assert.match(source, /PRIMARY KEY \([`]?platform[`]?,\s*[`]?shop_name[`]?\)/);
  }
  assert.match(runtime, /INSERT INTO netshop_product_daily_revisions[\s\S]*status = 'processing'[\s\S]*data_version = netshop_product_daily_revisions\.data_version \+ 1/);
  assert.match(runtime, /INSERT INTO netshop_product_daily_scope_revisions[\s\S]*status = 'processing'[\s\S]*data_version = netshop_product_daily_scope_revisions\.data_version \+ 1/);
  assert.match(runtime, /SELECT platform, data_version[\s\S]*FROM netshop_product_daily_revisions/);
  assert.match(runtime, /LEFT JOIN netshop_product_daily_scope_revisions revision[\s\S]*revision\.shop_name = requested_shops\.shop_name/);
  assert.doesNotMatch(runtime, /readNetshopProductDailyRevisions[\s\S]{0,1200}COUNT\(\*\)[\s\S]{0,200}netshop_import_batches/);
});

test("netshop query indexes are identical in the forward migration and runtime upgrade", async () => {
  const [runtime, dailyMigration, migration, projectionMigration] = await Promise.all([
    readFile(new URL("../lib/netshop/database.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/netshop/daily-row-migration.ts", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0060_market_netshop_query_safety.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0076_netshop_product_query_projections.sql", import.meta.url), "utf8"),
  ]);
  for (const source of [runtime, migration]) {
    assert.match(source, /netshop_rows_source_dataset_scope_date_idx/);
    assert.match(source, /netshop_import_batches_latest_product_idx/);
  }
  for (const source of [dailyMigration, migration]) {
    assert.match(source, /netshop_rows_daily_natural_identity_idx/);
    assert.match(source, /CASE WHEN `?dataset`?\s*=\s*'sku_daily' THEN `?sku_id`? ELSE `?spu_id`? END/);
  }
  assert.match(runtime, /DAILY_ROW_NATURAL_IDENTITY_INDEX_SQL/);
  for (const source of [runtime, projectionMigration]) {
    assert.match(source, /netshop_rows_product_batch_page_idx/);
    assert.match(source, /last_import_batch_id[\s\S]*shop_name[\s\S]*product_name[\s\S]*sku_id/);
    assert.match(source, /source[^\n]*IN \('jd_product_master',\s*'tmall_product_master'\)[\s\S]*dataset[^\n]*=[^\n]*'product_master'/);
  }
  assert.doesNotMatch(runtime, /PRAGMA index_(?:info|list)[\s\S]{0,200}\.catch\(\(\) =>/);
  assert.match(runtime, /\.then\(\(\) => ensureDailyRowNaturalKeys\(db\)\)[\s\S]*?schemaReadyByDatabase\.delete\(key\)/);
});

test("netshop import-history route parses strict decimal pagination before calling Django", async () => {
  const route = await readFile(new URL("../app/api/netshop/import/route.ts", import.meta.url), "utf8");
  assert.match(route, /readNetshopQueryInteger\(params\.get\("page"\)/);
  assert.match(route, /params\.get\("pageSize"\) \?\? params\.get\("limit"\)/);
  assert.match(route, /createDjangoNetshopService\(\)\.request/);
  assert.match(route, /NETSHOP_IMPORTS_PATH/);
  assert.doesNotMatch(route, /getNetshopDatabase|listNetshopImportBatches|netshop_rows/);
  assert.doesNotMatch(route, /Number\(params\.get\("(?:page|pageSize|limit)"\)/);
});
