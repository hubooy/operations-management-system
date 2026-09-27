import assert from "node:assert/strict";
import { access, readFile, readdir, stat } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { Miniflare } from "miniflare";

const templateRoot = new URL("../", import.meta.url);

test("built Worker starts without D1 and reports missing Django services without a fallback", async () => {
  const serverRoot = fileURLToPath(new URL("../dist/server/", import.meta.url));
  const config = JSON.parse(await readFile(resolve(serverRoot, "wrangler.json"), "utf8"));
  assert.deepEqual(config.d1_databases, []);
  assert.equal(config.r2_buckets[0].binding, "SALES_IMPORT_FILES");
  await assert.rejects(access(new URL("../dist/.openai/drizzle", import.meta.url)));
  const modulePaths = (await readdir(serverRoot, { recursive: true })).filter((path) => /\.[cm]?js$/.test(path));
  modulePaths.sort((a, b) => a === "index.js" ? -1 : b === "index.js" ? 1 : a.localeCompare(b));
  const runtime = new Miniflare({
    modules: modulePaths.map((path) => ({ type: "ESModule", path: resolve(serverRoot, path) })),
    modulesRoot: serverRoot,
    compatibilityDate: config.compatibility_date,
    compatibilityFlags: config.compatibility_flags,
    // Deliberately omit every service URL and DB binding: this isolated runtime
    // must neither contact a real backend nor need D1 for health checks.
    bindings: { TERUISI_RUNTIME_ENV: "development" },
    cf: false,
    port: 0,
    host: "127.0.0.1",
  });
  try {
    const init = { headers: { "x-teruisi-local-health": "1" } };
    const live = await runtime.dispatchFetch("http://127.0.0.1/_teruisi/local/health/live", init);
    assert.equal(live.status, 200);
    assert.equal((await live.json()).status, "live");
    const ready = await runtime.dispatchFetch("http://127.0.0.1/_teruisi/local/health/ready", init);
    assert.equal(ready.status, 503);
    const payload = await ready.json();
    assert.equal(payload.code, "django_unavailable");
    assert.equal(payload.backend, "django-postgresql");
    assert.equal(payload.unavailableServices.length, 23);
  } finally {
    await runtime.dispose();
  }
});

test("build emits the operations console", async () => {
  const assetRoot = new URL("../dist/client/assets/", import.meta.url);
  const assetNames = await readdir(assetRoot);
  const pageAssets = assetNames.filter((name) => /^page-.*\.js$/.test(name));
  const salesAsset = assetNames.find((name) => /^sales-module-view-.*\.js$/.test(name));
  const importAsset = assetNames.find((name) => /^import-module-view-.*\.js$/.test(name));
  assert.ok(pageAssets.length > 0, "client page bundle is missing");
  assert.ok(salesAsset, "lazy sales module bundle is missing");
  assert.ok(importAsset, "lazy import module bundle is missing");

  const [server, pageChunks, sales, importModule] = await Promise.all([
    readFile(new URL("../dist/server/index.js", import.meta.url), "utf8"),
    Promise.all(pageAssets.map((name) => readFile(new URL(name, assetRoot), "utf8"))),
    readFile(new URL(salesAsset, assetRoot), "utf8"),
    readFile(new URL(importAsset, assetRoot), "utf8"),
  ]);
  const page = pageChunks.find((chunk) => chunk.includes("shell-account"));
  assert.ok(page, "client page entry bundle is missing");
  assert.match(server, /api\/imports\/sales/);
  assert.match(server, /api\/sales\/summary/);
  assert.match(server, /api\/imports\/inventory/);
  assert.match(server, /api\/imports\/inventory\/chunks/);
  assert.match(server, /api\/imports\/erp/);
  assert.match(server, /api\/imports\/erp\/chunks/);
  assert.match(server, /api\/inventory\/overview/);
  assert.match(server, /api\/inventory\/replenishment/);
  assert.match(server, /api\/inventory\/age-analysis/);
  assert.match(server, /api\/products\/summary/);
  assert.match(server, /api\/settings/);
  assert.match(server, /api\/auth\/me/);
  assert.match(server, /api\/search/);
  assert.match(page, /shell-account/);
  assert.doesNotMatch(page, /我的工作台/);
  assert.match(page, /销售分析/);
  assert.doesNotMatch(page, /渠道经营诊断|channel-detail-panel/);
  assert.match(sales, /渠道经营诊断/);
  assert.match(sales, /channel-detail-panel/);
  assert.doesNotMatch(page, /数据导入/);
  assert.match(importModule, /数据导入/);
  assert.doesNotMatch(page, /codex-preview|Your site is taking shape/i);
});

test("searches all allowlisted system data through the grouped authenticated search", async () => {
  const [page, dialog, route, search] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/global-search-dialog.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/search/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/search/global-search.ts", import.meta.url), "utf8"),
  ]);

  assert.match(page, /\/api\/search\?q=/);
  assert.doesNotMatch(page, /className="global-search"/);
  assert.match(page, /event\.metaKey \|\| event\.ctrlKey/);
  assert.match(page, /AbortController/);
  assert.match(page, /globalSearchGroupRequestKeyRef/);
  assert.match(page, /seenIds/);
  assert.match(page, /parseGlobalSearchTarget/);
  assert.match(dialog, /搜索商品、订单、库存、市场、客服、财务或批次/);
  assert.match(dialog, /result\.groups/);
  assert.match(dialog, /按字段白名单搜索/);
  assert.match(route, /requireAppPrincipal/);
  assert.match(route, /searchAllBusinessData/);
  assert.match(route, /principal/);
  for (const domain of [
    "Sales", "Finance", "Netshop", "Products", "Inventory", "CustomerService", "ErpReference", "Market", "Workflow",
  ]) assert.match(search, new RegExp(`createDjango${domain}ConsumerReader`));
  assert.match(search, /operation: "row_search"/);
  assert.match(search, /netshopReader\.read/);
  assert.doesNotMatch(search, /\b(?:FROM|JOIN)\s+netshop_rows\b/i);
  assert.match(search, /operation: "order_search"/);
  assert.match(search, /operation: "import_batch_search"/);
  assert.match(search, /operation: "sku_search"/);
  assert.match(search, /"sku_search" : "annotation_search"/);
  assert.match(search, /definition\.allowedRoles\.includes\(principal\.role\)/);
  assert.match(search, /scopeKind === "unscoped_only" && principal\.scope !== null/);
  assert.doesNotMatch(search, /GLOBAL_SEARCH_SCHEMA_TABLE_AUDIT|scopeSql|sqlite_master|\b(?:FROM|JOIN)\s+\w+/i);
  assert.doesNotMatch(route, /getD1Database|SELECT\s|LIKE\s|sqlite_master/i);
});

test("wires the sales import and PostgreSQL analytics capabilities", async () => {
  const [shell, dashboardModule, shopModule, salesModule, inventoryModule, productModule, importModule, businessUiModule, searchableSelect, salesFilterBar, layout, schema, salesModels, importRoute, chunkRoute, chunkService, summaryRoute, summaryService, summaryQuery, packageJson, hosting, og] =
    await Promise.all([
      readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/dashboard-module-view.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/shop-module-view.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/sales-module-view.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/inventory-module-view.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/product-module-view.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/import-module-view.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/module-view-business-ui.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/ui/searchable-select.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/sales-filter-bar.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
      readFile(new URL("../db/schema.ts", import.meta.url), "utf8"),
      readFile(new URL("../backend/sales/models.py", import.meta.url), "utf8"),
      readFile(new URL("../app/api/imports/sales/route.ts", import.meta.url), "utf8"),
      readFile(new URL("../app/api/imports/sales/chunks/route.ts", import.meta.url), "utf8"),
      readFile(new URL("../lib/sales/chunked-upload.ts", import.meta.url), "utf8"),
      readFile(new URL("../app/api/sales/summary/route.ts", import.meta.url), "utf8"),
      readFile(new URL("../backend/sales/summary.py", import.meta.url), "utf8"),
      readFile(new URL("../backend/sales/query.py", import.meta.url), "utf8"),
      readFile(new URL("../package.json", import.meta.url), "utf8"),
      readFile(new URL("../.openai/hosting.json", import.meta.url), "utf8"),
      stat(new URL("../public/og.png", import.meta.url)),
    ]);
  const clientSources = [shell, dashboardModule, shopModule, salesModule, inventoryModule, productModule, importModule, businessUiModule].join("\n");

  assert.match(clientSources, /\/api\/imports\/sales/);
  assert.match(clientSources, /\/api\/sales\/summary/);
  assert.match(clientSources, /onModuleViewChange/);
  assert.match(clientSources, /平台汇总/);
  assert.match(clientSources, /网店总览/);
  assert.match(clientSources, /outlets/);
  assert.match(clientSources, /近15天/);
  assert.match(clientSources, /昨天/);
  assert.match(searchableSelect, /function SearchableSelect/);
  assert.match(searchableSelect, /function SearchableMultiSelect/);
  assert.match(searchableSelect, /searchable-select-search/);
  assert.match(clientSources, /搜索店铺或平台/);
  assert.match(clientSources, /columnPickerSearch/);
  const salesView = salesModule.slice(salesModule.indexOf("export default function SalesView"));
  assert.doesNotMatch(salesView, /<select\b/);
  assert.doesNotMatch(clientSources, /本季度/);
  assert.doesNotMatch(clientSources, /DateRangeSlider/);
  assert.match(clientSources, /选择统计月份/);
  assert.match(productModule, /export default function ProductView\([^)]*customStartDate[^)]*customEndDate/);
  assert.match(clientSources, /全局统计周期 \{customStartDate\} 至 \{customEndDate\}/);
  assert.match(clientSources, /货品情况/);
  assert.match(clientSources, /销售分布/);
  assert.match(clientSources, /平台维度/);
  assert.match(clientSources, /净销量已按退货后的销量计算/);
  assert.match(clientSources, /输入或粘贴货品规格代码/);
  assert.match(clientSources, /MultiFilterSelect/);
  assert.match(clientSources, /aria-multiselectable/);
  assert.match(clientSources, /platformFilters/);
  assert.match(clientSources, /shopFilters/);
  assert.match(clientSources, /stat-period-picker/);
  assert.match(clientSources, /趋势指标选择/);
  assert.match(clientSources, /对应数据/);
  assert.match(clientSources, /当前日期的净销售额、净销量和大毛利率/);
  assert.match(clientSources, /product-trend-detail-area/);
  assert.match(clientSources, /valueLabel/);
  assert.match(clientSources, /trend-data-hint/);
  assert.match(clientSources, /setStatPeriodPickerOpen\(false\)/);
  assert.match(clientSources, /日维度/);
  assert.match(clientSources, /周维度/);
  assert.match(clientSources, /月维度/);
  assert.match(clientSources, /<canvas/);
  assert.match(clientSources, /销售同比/);
  assert.match(clientSources, /净销售同比/);
  assert.match(clientSources, /formatNetSalesYearOverYear/);
  assert.match(clientSources, /netSalesYearOverYearTone/);
  assert.match(clientSources, /product-multi-query/);
  assert.match(clientSources, /输入或粘贴货品规格代码/);
  assert.match(clientSources, /multi-filter-search/);
  assert.match(clientSources, /搜索一个或多个货品规格代码/);
  assert.match(clientSources, /正在同步 BI 经营看板/);
  assert.match(clientSources, /数据同步状态/);
  assert.match(clientSources, /库存快照/);
  assert.match(clientSources, /summary\?\.shops/);
  assert.match(clientSources, /SalesFilterBar/);
  assert.match(salesFilterBar, /销售分析平台/);
  assert.match(salesFilterBar, /销售分析店铺/);
  assert.match(salesFilterBar, /销售分析品类/);
  assert.match(clientSources, /selectedPlatforms/);
  assert.match(clientSources, /selectedShopKeys/);
  assert.match(clientSources, /filters\.categories/);
  assert.match(clientSources, /selectedOutletKeys/);
  assert.match(clientSources, /filters\.outletKeys/);
  assert.match(clientSources, /marginFilters/);
  assert.match(clientSources, /type="file"/);
  assert.match(layout, /generateMetadata/);
  assert.match(layout, /const title = "电商运营中台"/);
  assert.match(layout, /\/favicon\.svg\?v=xiaote-20260922/);
  assert.match(layout, /\/og\.png/);
  assert.doesNotMatch(schema, /sales_import_batches|sales_order_lines|sales_import_uploads/);
  assert.match(salesModels, /class SalesImportBatch/);
  assert.match(salesModels, /db_table = "sales_import_batches"/);
  assert.match(salesModels, /class SalesOrderLine/);
  assert.match(salesModels, /db_table = "sales_order_lines"/);
  assert.match(salesModels, /class SalesRawUploadSession/);
  assert.match(salesModels, /class SalesStagedImportSession/);
  assert.match(importRoute, /importSalesLedgerBytes/);
  assert.match(chunkRoute, /assembleSalesUpload/);
  assert.match(chunkService, /SALES_UPLOAD_CHUNK_BYTES/);
  assert.match(summaryRoute, /routeDjangoSalesReadRequest/);
  assert.match(summaryRoute, /categories/);
  assert.match(summaryRoute, /selections\(params, "platforms", "platform"\)/);
  assert.match(summaryRoute, /selections\(params, "outlet", "outlets"\)/);
  assert.match(summaryRoute, /parseShopFilterKey/);
  assert.match(summaryQuery, /gross_sales_cents/);
  assert.match(summaryQuery, /net_sales_excluding_accessories_cents/);
  assert.match(salesModels, /赠品配件/);
  assert.match(summaryService, /salesYearOverYearRate/);
  assert.match(summaryService, /"filterOptions": options/);
  assert.match(summaryQuery, /resolved_category__in/);
  assert.match(summaryService, /def _filter_options/);
  assert.match(summaryQuery, /platform_key__in/);
  assert.match(summaryQuery, /for outlet in outlets/);
  assert.match(summaryService, /last15/);
  assert.match(summaryService, /daily_rows/);
  assert.match(summaryQuery, /product_code__in/);
  assert.match(summaryRoute, /productCodes/);
  assert.match(summaryService, /report_shop_key/);
  assert.match(packageJson, /"fflate"/);
  assert.equal(JSON.parse(hosting).d1, undefined);
  assert.equal(JSON.parse(hosting).r2, "SALES_IMPORT_FILES");
  assert.ok(og.size > 10_000);

  await assert.rejects(access(new URL("app/_sites-preview", templateRoot)));
});

test("keeps sales overview multi-selects mounted while filtered results refresh", async () => {
  const [salesModule, searchableSelect, salesFilterBar] = await Promise.all([
    readFile(new URL("../app/sales-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/ui/searchable-select.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/sales-filter-bar.tsx", import.meta.url), "utf8"),
  ]);
  const multiSelect = searchableSelect.slice(searchableSelect.indexOf("function SearchableMultiSelect"));
  const toggle = multiSelect.slice(multiSelect.indexOf("const toggle"), multiSelect.indexOf("const selectAll"));

  assert.match(multiSelect, /const toggle = \(nextValue: string\)/);
  assert.doesNotMatch(toggle, /setOpen\(false\)/);
  assert.match(salesModule, /if \(loading && !summary\)/);
  assert.match(salesModule, /updating=\{usesSalesSummary && loading\}/);
  assert.match(salesFilterBar, /aria-busy=\{updating\}/);
  assert.match(salesFilterBar, /正在按公共筛选更新\$\{scopeLabel\}/);
  assert.match(salesModule, /const usesSalesSummary = activeTab === "overview" \|\| activeTab === "channel"/);
  assert.match(salesModule, /if \(!usesSalesSummary\) return/);
});

test("keeps shop analysis isolated by platform and matches year-over-year by the same shop key", async () => {
  const [shopModule, navigation, summaryService] = await Promise.all([
    readFile(new URL("../app/shop-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/shell/navigation-catalog.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/sales/summary.py", import.meta.url), "utf8"),
  ]);

  // A shop name can legitimately occur on several marketplaces.  Its platform
  // is therefore part of both the aggregation key and the year-over-year key.
  assert.match(summaryService, /group_key=Concat\(F\("report_platform_key"\), Value\("\\x1f"\), F\("report_shop_key"\)/);
  assert.match(summaryService, /queryset\.values\("group_key", "group_name"\)/);
  assert.match(summaryService, /year_ago_net_sales_cents/);
  assert.match(summaryService, /"yearAgoNetSalesCents"/);

  // Stable, platform-qualified keys prevent React from reusing a 拼多多 row
  // while the user has filtered the table to 京东.
  assert.match(shopModule, /key=\{`\$\{activeTab\}-\$\{item\.platform\}-\$\{item\.name\}`\}/);
  assert.match(navigation, /label: "网店分析"/);
  assert.match(shopModule, /aria-label="网店分析子版块"/);
});

test("wires inventory health, synchronization, and replenishment", async () => {
  const [inventoryModule, importModule, schema, parser, importRoute, chunkRoute, chunkService, overview, overviewRoute, replenishmentRoute, database, migration, uploadMigration, completionMigration] =
    await Promise.all([
      readFile(new URL("../app/inventory-module-view.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/import-module-view.tsx", import.meta.url), "utf8"),
      readFile(new URL("../db/schema.ts", import.meta.url), "utf8"),
      readFile(new URL("../lib/imports/inventory-stock.ts", import.meta.url), "utf8"),
      readFile(new URL("../app/api/imports/inventory/route.ts", import.meta.url), "utf8"),
      readFile(new URL("../app/api/imports/inventory/chunks/route.ts", import.meta.url), "utf8"),
      readFile(new URL("../lib/inventory/django-chunked-upload.ts", import.meta.url), "utf8"),
      readFile(new URL("../backend/inventory/query.py", import.meta.url), "utf8"),
      readFile(new URL("../app/api/inventory/overview/route.ts", import.meta.url), "utf8"),
      readFile(new URL("../app/api/inventory/replenishment/route.ts", import.meta.url), "utf8"),
      readFile(new URL("../lib/inventory/database.ts", import.meta.url), "utf8"),
      readFile(new URL("../drizzle/0002_broken_kid_colt.sql", import.meta.url), "utf8"),
      readFile(new URL("../drizzle/0003_strange_energizer.sql", import.meta.url), "utf8"),
      readFile(new URL("../drizzle/0004_purple_runaways.sql", import.meta.url), "utf8"),
    ]);
  const inventoryUiSources = `${inventoryModule}\n${importModule}`;

  assert.match(inventoryUiSources, /\/api\/inventory\/overview/);
  assert.match(inventoryUiSources, /\/api\/imports\/inventory/);
  assert.match(inventoryUiSources, /\/api\/imports\/inventory\/chunks/);
  assert.match(inventoryUiSources, /\/api\/inventory\/replenishment/);
  assert.match(inventoryUiSources, /库存与近30天销量已联动/);
  assert.match(inventoryUiSources, /备货计划/);
  assert.match(inventoryUiSources, /payload\?\.errors/);
  assert.doesNotMatch(inventoryUiSources, /2,684,700/);
  assert.match(schema, /inventory_import_batches/);
  assert.match(schema, /inventory_stock_lines/);
  assert.match(schema, /replenishment_plan_items/);
  assert.match(parser, /实盘数量/);
  assert.match(parser, /固定成本价/);
  assert.match(parser, /吉客云库龄/);
  assert.match(importRoute, /importInventoryStockToDjango/);
  assert.match(importRoute, /INVENTORY_IMPORTS_PATH/);
  assert.match(chunkRoute, /assembleDjangoInventoryUpload/);
  assert.match(chunkRoute, /claimDjangoInventoryUpload/);
  assert.match(chunkService, /createDjangoInventoryService/);
  assert.match(chunkService, /service: "writer"/);
  assert.match(chunkService, /chunk\.sha256/);
  assert.match(overview, /def inventory_overview/);
  assert.match(overview, /inventoryStale/);
  assert.match(overview, /刷刷仓/);
  assert.match(overviewRoute, /INVENTORY_OVERVIEW_PATH/);
  assert.match(overviewRoute, /service: "reader"/);
  assert.doesNotMatch(overviewRoute, /getInventoryOverview|@\/lib\/inventory\/overview/);
  assert.match(replenishmentRoute, /INVENTORY_REPLENISHMENT_PATH/);
  assert.match(replenishmentRoute, /service: "writer"/);
  assert.match(replenishmentRoute, /acknowledgeStale/);
  assert.match(database, /ensureInventorySchema/);
  assert.match(migration, /CREATE TABLE `inventory_stock_lines`/);
  assert.match(migration, /sales_order_lines_inventory_demand_idx/);
  assert.match(uploadMigration, /CREATE TABLE `inventory_import_uploads`/);
  assert.match(completionMigration, /inventory_import_upload_results/);
  assert.match(completionMigration, /replenishment_plan_items_draft_key_uq/);
});

test("wires product profitability to synchronized sales and inventory facts", async () => {
  const [productModule, businessUiModule, route, summary, djangoQuery, parser, inventoryDatabase, migration] = await Promise.all([
    readFile(new URL("../app/product-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/module-view-business-ui.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/products/summary/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/products/summary.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/products/query.py", import.meta.url), "utf8"),
    readFile(new URL("../lib/imports/inventory-stock.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/inventory/database.ts", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0008_young_sunspot.sql", import.meta.url), "utf8"),
  ]);
  const productUiSources = `${productModule}\n${businessUiModule}`;

  assert.match(productUiSources, /\/api\/products\/summary/);
  assert.match(productUiSources, /毛利测算/);
  assert.match(productUiSources, /全局统计周期 \{customStartDate\} 至 \{customEndDate\}/);
  assert.match(productUiSources, /range: "custom", startDate: customStartDate, endDate: customEndDate/);
  assert.match(productUiSources, /ariaLabel="销售平台"/);
  assert.match(productUiSources, /ariaLabel="销售店铺"/);
  assert.match(productUiSources, /<th>品牌<\/th><th>供应商<\/th>/);
  assert.match(productUiSources, /实际大毛利率低于35%/);
  assert.match(productUiSources, /实际大毛利率35%/);
  assert.match(productUiSources, /规格代码：/);
  assert.match(productUiSources, /<th>退货率<\/th><th>快递费率<\/th><th>操作<\/th>/);
  assert.match(productUiSources, /ProductDetailView/);
  assert.match(productUiSources, /ProductPlatformSalesShare/);
  assert.match(productUiSources, /shopOptions\.some\(\(option\) => option\.value === item\)/);
  assert.match(productUiSources, /next\.length === current\.length \? current : next/);
  assert.match(productUiSources, /\/api\/sales\/summary/);
  assert.doesNotMatch(productUiSources, /TRS-SM-1182/);
  assert.match(route, /getProductSummary/);
  assert.match(route, /startDate/);
  assert.match(route, /endDate/);
  assert.match(summary, /createDjangoProductsService/);
  assert.match(summary, /PRODUCTS_SUMMARY_PATH/);
  assert.match(summary, /service: "reader"/);
  assert.doesNotMatch(summary, /sales_order_lines|inventory_stock_lines|erp_product_master/);
  assert.match(djangoQuery, /execute_consumer_query/);
  assert.match(djangoQuery, /ProductInventoryProjection/);
  assert.match(djangoQuery, /ProductShippingRate/);
  assert.match(summary, /grossProfitCents/);
  assert.match(summary, /refundAmountCents/);
  assert.match(summary, /marginBuckets/);
  assert.match(summary, /supplierName/);
  assert.match(summary, /outlets/);
  assert.match(djangoQuery, /brand/);
  assert.match(parser, /品牌名称/);
  assert.match(inventoryDatabase, /syncInventoryStockDimensions/);
  assert.match(migration, /ADD `brand`/);
});

test("wires inventory age analysis and stale cleanup", async () => {
  const [inventoryModule, parser, route, analysis, migration] = await Promise.all([
    readFile(new URL("../app/inventory-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/imports/inventory-stock.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/inventory/age-analysis/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/inventory/query.py", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0006_quiet_forgotten_one.sql", import.meta.url), "utf8"),
  ]);

  assert.match(inventoryModule, /库龄分析/);
  assert.match(inventoryModule, /滞销清理/);
  assert.match(inventoryModule, /snapshotDate/);
  assert.match(parser, /前30天销量/);
  assert.match(route, /INVENTORY_AGE_ANALYSIS_PATH/);
  assert.match(route, /service: "reader"/);
  assert.doesNotMatch(route, /getInventoryAgeAnalysis|@\/lib\/inventory\/age-analysis/);
  assert.match(analysis, /def inventory_age_analysis/);
  assert.match(analysis, /InventoryAgeLine/);
  assert.match(analysis, /刷刷仓/);
  assert.match(migration, /inventory_age_metrics/);
});

test("wires all five ERP imports and excludes 刷刷仓 from operating analysis", async () => {
  const [importModule, schema, parser, service, route, chunkRoute, salesService, inventoryService, salesModels, inventoryOverview, productSummary, productQuery, projectionSync, ageAnalysis, migration] = await Promise.all([
    readFile(new URL("../app/import-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../db/schema.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/imports/erp-reference.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/erp-reference/import-service.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/imports/erp/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/imports/erp/chunks/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/sales/import-service.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/inventory/import_service.py", import.meta.url), "utf8"),
    readFile(new URL("../backend/sales/models.py", import.meta.url), "utf8"),
    readFile(new URL("../backend/inventory/query.py", import.meta.url), "utf8"),
    readFile(new URL("../lib/products/summary.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/products/query.py", import.meta.url), "utf8"),
    readFile(new URL("../lib/products/inventory-projection-sync.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/inventory/consumers.py", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0009_wonderful_blindfold.sql", import.meta.url), "utf8"),
  ]);

  for (const label of ["销售明细", "分仓库存", "货品主数据", "库龄", "组合装"]) assert.match(importModule, new RegExp(label));
  assert.match(importModule, /\/api\/imports\/erp/);
  assert.match(importModule, /inventory_age/);
  assert.match(schema, /erp_reference_import_batches/);
  assert.match(schema, /erp_product_master/);
  assert.match(schema, /erp_inventory_age_lines/);
  assert.match(schema, /erp_combo_items/);
  assert.match(parser, /货品编号/);
  assert.match(parser, /库存天数/);
  assert.match(parser, /子件编号/);
  assert.match(service, /EXCLUDED_BRUSH_WAREHOUSE/);
  assert.match(route, /importErpReferenceToDjango/);
  assert.match(route, /createDjangoErpReferenceService/);
  assert.match(route, /importInventoryAgeToDjango/);
  assert.match(chunkRoute, /importErpReferenceToDjango/);
  assert.match(chunkRoute, /assembleDjangoInventoryUpload/);
  assert.doesNotMatch(`${route}\n${chunkRoute}`, /@\/lib\/erp-reference\/(?:database|import-service|projection-outbox)/);
  assert.match(salesService, /isExcludedSalesWarehouse/);
  assert.match(inventoryService, /刷刷仓/);
  for (const analysis of [salesModels, inventoryOverview, ageAnalysis]) assert.match(analysis, /刷刷仓/);
  assert.match(projectionSync, /createDjangoInventoryConsumerReader/);
  assert.doesNotMatch(projectionSync, /inventory_stock_lines|@\/lib\/inventory\/database/);
  assert.match(ageAnalysis, /InventoryAgeLine/);
  assert.doesNotMatch(productSummary, /刷刷仓|erp_product_master|inventory_stock_lines/);
  assert.match(productQuery, /ErpProductMaster/);
  assert.match(productQuery, /ProductInventoryProjection/);
  assert.match(migration, /CREATE TABLE `erp_product_master`/);
  assert.match(migration, /CREATE TABLE `erp_inventory_age_lines`/);
  assert.match(migration, /CREATE TABLE `erp_combo_items`/);
});

test("imports dynamic monthly financial reports and exposes target-linked analysis", async () => {
  const [salesModule, schema, parser, database, analysis, importRoute, analysisRoute, targetRoute, migration, packageJson] = await Promise.all([
    readFile(new URL("../app/sales-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../db/schema.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/finance/parser.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/finance/database.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/finance/analysis.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/imports/finance/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/finance/analysis/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/finance/targets/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0010_finance_reporting.sql", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  for (const label of ["财报分析", "目标进度情况", "月度财报", "费用同环比与异常点", "大毛利率", "退货率", "费用率", "全部月份", "8系列"]) assert.match(salesModule, new RegExp(label));
  const importModule = await readFile(new URL("../app/import-module-view.tsx", import.meta.url), "utf8");
  assert.match(importModule, /key: "finance"[^\r\n]+directEndpoint: "\/api\/imports\/finance"[^\r\n]+extensions: \["\.xls", "\.xlsx"\]/);
  assert.match(salesModule, /\/api\/finance\/analysis/);
  assert.match(salesModule, /\/api\/finance\/targets/);
  assert.match(schema, /financeImportBatches/);
  assert.match(schema, /financeMonths/);
  assert.match(schema, /financeLines/);
  assert.match(schema, /financeTargets/);
  assert.match(parser, /aggregateLines/);
  assert.match(parser, /sourceRowCount/);
  assert.match(parser, /销售费用/);
  assert.match(database, /existingBatch\?\.status === "completed"/);
  assert.match(database, /await db\.batch\(publishStatements\)/);
  assert.match(database, /ON CONFLICT\(month, section, scope_key, subject_name\)/);
  assert.match(analysis, /promotionFeeRatioBps/);
  assert.match(analysis, /returnRateBps/);
  assert.match(analysis, /feeRateBps/);
  assert.match(analysis, /yearAgoFeeRateBps/);
  assert.match(analysis, /platformFilter/);
  assert.match(analysis, /isSelectableShopName/);
  assert.match(analysis, /momRate/);
  assert.match(salesModule, /formatFinanceWan/);
  assert.match(salesModule, /FinanceSortButton/);
  assert.match(salesModule, /expenseSearch/);
  assert.match(salesModule, /yearAgoFeeRateBps/);
  assert.match(importRoute, /application\/octet-stream/);
  assert.match(importRoute, /requireAppPrincipal\(\["admin"\]\)/);
  assert.match(analysisRoute, /path: FINANCE_ANALYSIS_PATH, query: searchParams, service: "reader"/);
  assert.doesNotMatch(analysisRoute, /getFinanceDatabase|legacy|shadow/);
  assert.match(targetRoute, /periodType === "project"/);
  assert.match(targetRoute, /requireAppPrincipal\(\["admin"\]\)/);
  assert.match(migration, /CREATE TABLE `finance_lines`/);
  assert.match(migration, /CREATE TABLE `finance_targets`/);
  assert.match(packageJson, /"xlsx": "0\.18\.5"/);
});

test("connects JD SPU daily workbooks to the netshop import API", async () => {
  const [importModule, route, service, models, dailyContract] = await Promise.all([
    readFile(new URL("../app/import-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/netshop/import/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/netshop/normalized-import.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/netshop/models.py", import.meta.url), "utf8"),
    readFile(new URL("../lib/netshop/daily-contract.ts", import.meta.url), "utf8"),
  ]);

  assert.match(importModule, /jd_spu_daily/);
  assert.match(importModule, /京东商品 SPU 日数据/);
  assert.match(importModule, /formSource: "jd_sku_daily"/);
  assert.match(importModule, /dataset === "spu_daily"/);
  assert.match(route, /prepareNormalizedNetshopImport/);
  assert.match(route, /createDjangoNetshopService/);
  assert.match(route, /NETSHOP_IMPORTS_PATH/);
  assert.match(route, /service: "writer"/);
  assert.match(route, /requireAppPrincipal\(\["admin"\]\)/);
  assert.match(service, /source === "jd_sku_daily"/);
  assert.match(dailyContract, /return "spu_daily"/);
  assert.match(service, /isDailyAggregateRow/);
  assert.match(service, /=== "合计"/);
  assert.match(models, /db_table = "netshop_rows"/);
  assert.match(models, /spu_id = models\.TextField/);
  assert.match(models, /business_date = models\.CharField/);
});

test("links imported JD SKU and SPU daily data to shop product analysis", async () => {
  const [shopModule, query, route, access] = await Promise.all([
    readFile(new URL("../app/shop-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../backend/netshop/query.py", import.meta.url), "utf8"),
    readFile(new URL("../app/api/netshop/product-performance/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/netshop/access.ts", import.meta.url), "utf8"),
  ]);

  for (const label of [
    "SKU（京东）",
    "SPU",
    "平台",
    "店铺",
    "显示对比数据",
    "显示对比值",
    "环比",
    "同比",
    "商品浏览量",
    "访客数",
    "成交人数",
    "加购人数",
    "UV价值",
    "总转化率",
    "搜索曝光次数",
    "推广ROI",
    "企业购出库金额",
    "列设置",
    "打开商品",
  ]) {
    assert.match(shopModule, new RegExp(label));
  }
  assert.match(shopModule, /product-performance-kpi-grid/);
  assert.match(shopModule, /商品数据 KPI/);
  assert.match(shopModule, /成交金额，不等同销售净额/);
  assert.match(shopModule, /导入并同步/);
  assert.match(shopModule, /\/api\/netshop\/product-performance/);
  assert.match(shopModule, /SearchableMultiSelect/);
  assert.match(shopModule, /productComparisonPeriod/);
  assert.match(shopModule, /netshopProductUrl/);
  assert.match(shopModule, /当前筛选周期暂无\{dimensionLabel\}商品日数据/);
  assert.match(shopModule, /系统数据覆盖 \{availableCoverageLabel\}/);
  assert.match(shopModule, /availableDateMin/);
  assert.match(shopModule, /全局统计周期/);
  assert.match(shopModule, /requestPerformance\("current", selectedPeriod, currentPerformanceScopeKey\)/);
  assert.match(query, /def product_performance\(/);
  assert.match(query, /identity_field = "sku_id" if dimension == "sku" else "spu_id"/);
  assert.match(query, /date_min=Min\("business_date"\)/);
  assert.match(query, /Count\("business_date", distinct=True\)/);
  assert.match(query, /page_views/);
  assert.match(query, /search_impressions/);
  assert.match(query, /add_cart_customers/);
  assert.match(query, /transaction_amount_cents/);
  assert.match(query, /"availableDateMin"/);
  assert.match(route, /readDimension/);
  assert.match(route, /getAll\("platform"\)/);
  assert.match(route, /readNetshopOutletFilters\(params\.getAll\("outlet"\)\)/);
  assert.match(route, /params\.has\("shop"\)/);
  assert.match(route, /netshopPlatformsForPrincipal/);
  assert.match(route, /NETSHOP_PRODUCT_PERFORMANCE_PATH/);
  assert.match(route, /service: "reader"/);
  assert.match(access, /NETSHOP_SUPPORTED_PLATFORMS = \["京东", "天猫"\]/);
  assert.match(access, /principal\.scope\.platforms/);
});

test("shows filtered SPU visitors only as a product-by-day accumulation", async () => {
  const shopModule = await readFile(new URL("../app/shop-module-view.tsx", import.meta.url), "utf8");

  assert.match(shopModule, /function StoreSpuVisitorMetric/);
  assert.match(shopModule, /dimension: "spu"/);
  assert.doesNotMatch(shopModule, /Math\.round\(sourceVisitors \* 0\.9\)/);
  assert.match(shopModule, /商品×日累计，非店铺去重 UV/);
  assert.match(shopModule, /待导入匹配店铺的 SPU 日数据/);
  assert.match(shopModule, /dimension === "sku" \? "jd_sku_daily" : "tmall_product_daily"/);
});

test("guards JD daily SKU and SPU imports with stable identity and full date coverage", async () => {
  const [importModule, service, database, dailyContract, dailyMigration] = await Promise.all([
    readFile(new URL("../app/import-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/netshop/normalized-import.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/netshop/database.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/netshop/daily-contract.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/netshop/daily-row-migration.ts", import.meta.url), "utf8"),
  ]);

  // A filename containing SPU must not override a SKU workbook's exact header contract.
  assert.match(dailyContract, /hasTime && hasSku && hasSkuName && !hasSpu && !hasSpuName/);
  assert.match(service, /DATASET_HEADER_MISMATCH/);
  assert.match(service, /MISSING_SKU_ID/);
  assert.match(service, /MISSING_SPU_ID/);
  assert.match(service, /MISSING_EXPECTED_DATES/);
  assert.match(service, /OUT_OF_RANGE_DATES/);
  assert.match(service, /dailyRowKey\(context\.dataset, context\.platform, context\.shopName, businessDate/);
  assert.match(database, /ensureDailyRowNaturalKeys/);
  assert.match(dailyMigration, /DAILY_ROW_NATURAL_KEY_MIGRATION/);
  assert.match(dailyMigration, /DELETE FROM netshop_rows WHERE id =/);
  assert.match(dailyMigration, /r\.source_row_key IS NOT json_array/);
  assert.match(dailyMigration, /DAILY_ROW_NATURAL_KEY_MIGRATION_MAX_BATCHES/);
  assert.match(dailyMigration, /NetshopSchemaUpgradePendingError/);
  // Both independent upload entries carry their expected dimension and range.
  assert.match(importModule, /jd_sku_daily/);
  assert.match(importModule, /expectedDataset: "sku_daily"/);
  assert.match(importModule, /expectedDataset: "spu_daily"/);
  assert.match(importModule, /expectedStartDate/);
  assert.match(importModule, /dataset === "sku_daily"/);
});

test("connects Tmall product, BI daily, and promotion data with scoped APIs", async () => {
  const [shopModule, importModule, sharedModule, service, database, importRoute, productRoute, performanceRoute, promotionRoute, registry, migration] = await Promise.all([
    readFile(new URL("../app/shop-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/import-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/module-view-shared.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/netshop/normalized-import.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/netshop/query.py", import.meta.url), "utf8"),
    readFile(new URL("../app/api/netshop/import/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/netshop/products/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/netshop/product-performance/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/netshop/promotion-performance/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/ai/tool-registry.ts", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0052_tmall_netshop_query_indexes.sql", import.meta.url), "utf8"),
  ]);
  const netshopUiSources = `${shopModule}\n${importModule}`;

  for (const token of ["tmall_product_master", "tmall_product_assets", "tmall_product_daily", "tmall_promotion"]) {
    assert.match(netshopUiSources, new RegExp(token));
    assert.match(service, new RegExp(token));
  }
  for (const shopName of [
    "天猫-志高亿玖专卖店",
    "天猫-志高马思图专卖店",
    "天猫-志高丽力专卖店",
    "天猫-志高炊之王专卖店",
    "天猫-志高亿用专卖店",
    "天猫-志高拓丰专卖店",
  ]) assert.match(netshopUiSources, new RegExp(shopName));
  assert.match(netshopUiSources, /\/api\/netshop\/import\/chunks/);
  assert.match(sharedModule, /MAX_TMALL_PRODUCT_ASSET_FILE_SIZE = 64 \* 1024 \* 1024/);
  assert.match(sharedModule, /TMALL_PRODUCT_ASSET_CHUNK_SIZE = 2 \* 1024 \* 1024/);
  assert.match(service, /resolveEnabledTmallShop/);
  assert.match(service, /TextDecoder\("gb18030", \{ fatal: true \}\)/);
  assert.match(service, /天猫推广 ZIP 必须且只能包含一个 CSV/);
  const writer = await readFile(new URL("../backend/netshop/import_service.py", import.meta.url), "utf8");
  assert.match(writer, /verification_count != len\(rows\)/);
  assert.match(writer, /网店业务范围落库归属回查不一致/);
  assert.match(service, /DUPLICATE_MERCHANT_CODE/);
  assert.match(database, /def product_catalog\(/);
  assert.match(database, /def promotion_performance\(/);
  assert.match(database, /"spendCents"/);
  assert.match(database, /"platformPaymentAmountCents"/);
  assert.match(importRoute, /requireAppPrincipal\(\["admin"\]\)/);
  assert.match(importRoute, /netshopPlatformsForPrincipal/);
  for (const route of [importRoute, productRoute, performanceRoute, promotionRoute]) {
    assert.match(route, /cache-control/i);
    assert.match(route, /no-store/);
  }
  for (const route of [productRoute, performanceRoute, promotionRoute]) {
    assert.match(route, /netshopPlatformsForPrincipal/);
  }
  assert.match(registry, /get_netshop_performance/);
  assert.match(registry, /scopePolicy: "principal_scope"/);
  assert.match(migration, /dataset`?,`?platform`?,`?shop_name`?,`?business_date`?,`?spu_id/);
  assert.match(netshopUiSources, /推广费率/);
  assert.match(netshopUiSources, /推广成交占比/);
  assert.match(netshopUiSources, /ROAS/);
  assert.match(netshopUiSources, /detail\.tmall\.com\/item\.htm\?id=/);
});

test("exposes the five operational collaboration workspaces", async () => {
  const [page, operations] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/operations-view.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(page, /Component: OperationsView \} = createReloadableLazy\("workflow", \(\) => import\("\.\/operations-view"\)\)/);
  assert.match(page, /<OperationsView currentUser=/);
  for (const label of ["工作计划", "巡店检查", "评价维护", "新品上架", "变量配置"]) assert.match(operations, new RegExp(label));
  for (const label of ["评论", "活动", "提醒", "关联对象", "附件"]) assert.match(operations, new RegExp(label));
  for (const state of ["loading", "empty", "error", "permission"]) assert.match(operations, new RegExp(`kind: \\"${state}\\"|kind=\\"${state}\\"`));
  assert.match(operations, /\/api\/workflow\/operations-records/);
  assert.match(operations, /\/api\/workflow\/templates/);
  assert.match(operations, /\/collaboration/);
  assert.match(operations, /URLSearchParams/);
  assert.match(operations, /const TASK_PAGE_SIZE = 50/);
  assert.match(operations, /set\("pageSize", String\(TASK_PAGE_SIZE\)\)/);
  assert.match(operations, /toShanghaiApiDateTime/);
  assert.match(operations, /:00\+08:00/);
  assert.match(operations, /单个不超过 10MB/);
  assert.match(operations, /\.pdf,.png,.jpg,.jpeg,.webp,.xls,.xlsx,.docx,.txt,.csv/);
  assert.match(operations, /currentUser\?\.role === "operator"/);
  assert.doesNotMatch(operations, /createObjectURL/);
  assert.doesNotMatch(operations, /志高 ZK-30|近 30 天评价|今日已巡店/);
});

test("persists work-plan creation, full-field edits, status archiving, and deletion through Django", async () => {
  const [page, route, operations, models, migration] = await Promise.all([
    readFile(new URL("../app/operations-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/workflow/tasks/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/workflow/operations.py", import.meta.url), "utf8"),
    readFile(new URL("../backend/workflow/models.py", import.meta.url), "utf8"),
    readFile(new URL("../backend/workflow/migrations/0005_operations_domain.py", import.meta.url), "utf8"),
  ]);

  assert.match(page, /requestJson<TaskListPayload>\(`\/api\/workflow\/tasks\?\$\{listParams\}`/);
  assert.match(page, /method: "DELETE"/);
  assert.match(page, /method: "PATCH"/);
  assert.match(page, /const \[saving, setSaving\]/);
  assert.match(route, /requireAppPrincipal\(\["operator", "admin"\]\)/);
  assert.match(route, /export async function DELETE/);
  assert.match(route, /path: WORKFLOW_TASKS_PATH/);
  assert.match(route, /service: "writer"/);
  assert.match(route, /rawQuery: new URL\(request\.url\)\.searchParams\.toString\(\)/);
  assert.doesNotMatch(route, /getD1Database|updateWorkflowTask/);
  assert.match(operations, /def create_task\(/);
  assert.match(operations, /def update_task\(/);
  assert.match(operations, /def delete_task\(/);
  assert.match(operations, /select_for_update\(\)/);
  assert.match(operations, /deleted_at__isnull=True/);
  assert.match(operations, /expectedVersion/);
  assert.match(operations, /截止时间不能早于开始时间/);
  assert.match(operations, /工作项紧急程度无效/);
  assert.match(models, /class WorkflowTask\(models\.Model\)/);
  assert.match(migration, /name='WorkflowTask'/);
  assert.match(page, /workflow-plan-actions/);
  assert.match(page, /statusFilter === "open"/);
  assert.match(page, /item\.status !== "已完成"/);
});

test("requires an authenticated principal for reads and keeps writes on their declared role gates", async () => {
  const readRouteUrls = [
    "../app/api/sales/summary/route.ts",
    "../app/api/inventory/overview/route.ts",
    "../app/api/inventory/age-analysis/route.ts",
    "../app/api/inventory/replenishment/route.ts",
    "../app/api/products/summary/route.ts",
    "../app/api/imports/sales/route.ts",
    "../app/api/imports/inventory/route.ts",
    "../app/api/imports/erp/route.ts",
    "../app/api/imports/finance/route.ts",
    "../app/api/finance/analysis/route.ts",
    "../app/api/finance/targets/route.ts",
    "../app/api/market/overview/route.ts",
    "../app/api/market/trend/route.ts",
    "../app/api/workflow/tasks/route.ts",
    "../app/api/settings/route.ts",
  ];
  const writeRouteUrls = [
    "../app/api/imports/sales/route.ts",
    "../app/api/imports/sales/chunks/route.ts",
    "../app/api/imports/inventory/route.ts",
    "../app/api/imports/inventory/chunks/route.ts",
    "../app/api/imports/erp/route.ts",
    "../app/api/imports/erp/chunks/route.ts",
    "../app/api/imports/finance/route.ts",
    "../app/api/finance/targets/route.ts",
    "../app/api/inventory/replenishment/route.ts",
  ];
  const [page, schema, authorization, authRoute, migration, ...routes] =
    await Promise.all([
      readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
      readFile(new URL("../db/schema.ts", import.meta.url), "utf8"),
      readFile(new URL("../lib/auth/authorization.ts", import.meta.url), "utf8"),
      readFile(new URL("../app/api/auth/me/route.ts", import.meta.url), "utf8"),
      readFile(new URL("../drizzle/0005_slow_tyrannus.sql", import.meta.url), "utf8"),
      ...[...readRouteUrls, ...writeRouteUrls].map((url) => readFile(new URL(url, import.meta.url), "utf8")),
    ]);

  assert.match(page, /\/api\/auth\/me/);
  assert.match(page, /signin-with-chatgpt/);
  assert.match(page, /signout-with-chatgpt/);
  assert.doesNotMatch(page, /IdentityGate/);
  assert.doesNotMatch(page, /林晓 · 管理员/);
  assert.doesNotMatch(schema, /sqliteTable\(\s*["']app_users["']/);
  assert.match(schema, /ai_tool_audit_logs/);
  assert.match(authorization, /dengweizhang321@gmail\.com/);
  assert.match(authorization, /getChatGPTUser/);
  assert.match(authorization, /createDjangoAccessControlService/);
  assert.match(authorization, /ACCESS_CONTROL_RESOLVE_PATH/);
  assert.doesNotMatch(authorization, /FROM app_users|WHERE email = \? COLLATE NOCASE/);
  assert.match(authRoute, /requireAppPrincipal/);
  assert.match(migration, /INSERT INTO `app_users`/);
  assert.match(migration, /dengweizhang321@gmail\.com/);

  const readRoutes = routes.slice(0, readRouteUrls.length);
  const writeRoutes = routes.slice(readRouteUrls.length);
  for (const route of readRoutes) {
    assert.match(route, /requireAppPrincipal\(\["viewer", "analyst", "operator", "admin"\]\)/);
    assert.match(route, /requireUnrestrictedDataScope\(principal,/);
    assert.match(route, /authorizationErrorResponse/);
  }
  for (const [index, route] of writeRoutes.entries()) {
    const expectedRoleGate = writeRouteUrls[index].includes("/inventory/replenishment/")
      ? /requireAppPrincipal\(\["operator", "admin"\]\)/
      : /requireAppPrincipal\(\["admin"\]\)/;
    assert.match(route, expectedRoleGate);
    assert.match(route, /authorizationErrorResponse/);
  }
});

test("exposes the centrally registered, fail-closed Codex MCP connection", async () => {
  const [mcpRoute, registry, contract, budget, audit, config, agents] = await Promise.all([
    readFile(new URL("../app/mcp/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/ai/tool-registry.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/ai/tool-registry-contract.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/ai/mcp-execution-budget.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/ai/tool-audit.ts", import.meta.url), "utf8"),
    readFile(new URL("../.codex/config.toml", import.meta.url), "utf8"),
    readFile(new URL("../AGENTS.md", import.meta.url), "utf8"),
  ]);

  assert.match(mcpRoute, /CODEX_MCP_TOKEN/);
  assert.match(mcpRoute, /tools\/list/);
  assert.match(mcpRoute, /tools\/call/);
  assert.match(mcpRoute, /Bearer/);
  assert.match(mcpRoute, /getVisibleToolCatalog/);
  assert.match(mcpRoute, /createRegisteredToolExecutionRuntime/);
  assert.match(mcpRoute, /MAX_BATCH_REQUESTS = 20/);
  assert.match(mcpRoute, /mcp-\$\{digest\.slice/);
  assert.doesNotMatch(mcpRoute, /BOOTSTRAP_ADMIN_EMAIL/);
  assert.match(budget, /still waits for the[\s\S]*underlying operation to settle/);
  assert.match(budget, /runSequentialBatchWithinBudget/);
  for (const toolName of ["search_system_knowledge", "get_data_freshness", "get_sales_summary", "get_inventory_health", "get_product_performance", "list_replenishment_plans", "get_customer_service_conversations", "search_system_data"]) {
    assert.match(registry, new RegExp(`name: "${toolName}"`));
  }
  assert.match(registry, /readOnlyHint: true/);
  assert.match(registry, /validateToolRegistry\(aiToolRegistry\)/);
  assert.match(contract, /status: "started"/);
  assert.match(contract, /if \(!preflightAudited\).*audit_unavailable/);
  assert.match(contract, /if \(!audited\)/);
  assert.match(contract, /工具执行未返回数据/);
  assert.match(audit, /aiConsumer/);
  assert.match(audit, /operation: "tool-audit", entry: input/);
  const domainAudit = await readFile(new URL("../backend/ai_assistant/views.py", import.meta.url), "utf8");
  assert.match(domainAudit, /entry\["actorEmail"\].lower\(\) != principal.email.lower\(\)/);
  assert.match(domainAudit, /entry\["actorRole"\] != principal.role/);
  assert.match(config, /mcp_servers\.teruisi_operations/);
  assert.match(config, /TERUISI_CODEX_MCP_TOKEN/);
  assert.match(agents, /get_data_freshness/);
  assert.match(agents, /read-only/i);
  assert.match(agents, /lib\/ai\/tool-registry\.ts/);
});
