import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const readSource = (path: string) => readFile(new URL(path, import.meta.url), "utf8");

function between(source: string, start: string, end: string) {
  const startIndex = source.indexOf(start);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.ok(startIndex >= 0, `missing start marker: ${start}`);
  assert.ok(endIndex > startIndex, `missing end marker: ${end}`);
  return source.slice(startIndex, endIndex);
}

test("multi-select choices stay open while another option is selected", async () => {
  const source = await readSource("../app/ui/searchable-select.tsx");
  const multiSelect = source.slice(source.indexOf("export function SearchableMultiSelect"));
  const toggle = multiSelect.slice(multiSelect.indexOf("const toggle"), multiSelect.indexOf("const selectAll"));

  assert.match(multiSelect, /aria-multiselectable="true"/);
  assert.match(toggle, /onChange\(nextSearchableMultiSelection\(values, nextValue, maxSelections\)\)/);
  assert.doesNotMatch(toggle, /setOpen\(false\)/);
  assert.match(multiSelect, /onClick=\{\(\) => toggle\(option\.value\)\}/);
});

test("filter-driven requests keep the last successful layout mounted", async () => {
  const [shop, inventory, operations, marketMaster, aiAssistant, salesCategory, sales, product, importView] = await Promise.all([
    readSource("../app/shop-module-view.tsx"),
    readSource("../app/inventory-module-view.tsx"),
    readSource("../app/operations-view.tsx"),
    readSource("../app/market-master-admin-panel.tsx"),
    readSource("../app/ai-assistant-view.tsx"),
    readSource("../app/sales-category-view.tsx"),
    readSource("../app/sales-module-view.tsx"),
    readSource("../app/product-module-view.tsx"),
    readSource("../app/import-module-view.tsx"),
  ]);

  assert.match(shop, /const currentPerformance = scopedCurrentPerformance \?\? currentPerformanceResponse/);
  assert.match(shop, /const catalog = scopedCatalog \?\? catalogResponse/);
  assert.match(shop, /const currentItems = hasCompleteScopedPair \|\| !promotionDisplayPair/);
  assert.match(shop, /if \(itemsLoading && !currentItems && !currentOverview\)/);
  assert.match(shop, /loading && current\.items\.length === 0/);
  assert.match(shop, /loading && catalog\.items\.length === 0/);
  assert.match(shop, /promotion\?\.dataCutoffDate \? formatCurrencyFromCents\(promotion\.summary\.spendCents\) : promotionLoading/);

  const performanceRestart = between(shop, "claimSnapshotRestart(productPerformanceRestartedTokensRef.current", "if (!controller.signal.aborted && generation === productPerformanceGenerationRef.current)");
  assert.doesNotMatch(performanceRestart, /setCurrentPerformanceResponse\(null\)|setComparisonPerformanceResponse\(null\)/);
  const catalogRestart = between(shop, "claimSnapshotRestart(skuCatalogRestartedTokensRef.current", "if (!response.ok || !payload)");
  assert.doesNotMatch(catalogRestart, /setCatalog\(null\)/);
  const promotionRestart = between(shop, "claimSnapshotRestart(promotionSnapshotRestartedTokensRef.current", "if (!response.ok || !payload\?\.summary");
  assert.doesNotMatch(promotionRestart, /setPromotionDisplayPair\(null\)/);

  assert.match(inventory, /ageAnalysis\?\.hasInventory && activeTab === "age"/);
  assert.match(inventory, /ageAnalysis\?\.hasInventory && activeTab === "stale"/);
  assert.doesNotMatch(inventory, /!ageLoading && ageAnalysis && activeTab === (?:"age"|"stale")/);
  assert.match(inventory, /inbound-region-panel data-refresh-region" aria-busy=\{inboundLoading\}/);
  assert.match(inventory, /inbound-detail-panel data-refresh-region" aria-busy=\{inboundLoading\}/);

  assert.match(operations, /loading && items\.length === 0 \? <DataState/);
  assert.match(operations, /loading && tasks\.length === 0 \? <DataState/);
  assert.match(operations, /workflow-plan-table-panel data-refresh-region" aria-busy=\{loading\}/);

  assert.doesNotMatch(marketMaster, /setDatabaseSecondaryLoaded\(false\)/);
  assert.match(marketMaster, /settings-market-master-live data-refresh-region/);

  assert.match(aiAssistant, /\(configurationState === "ready" \|\| configurationLoaded\)/);
  assert.match(aiAssistant, /ai-admin-card data-refresh-region" aria-busy=\{configurationState === "loading"\}/);
  assert.match(aiAssistant, /if \(showChat && chatLoading && !conversationLoaded\)/);
  const categoryDetailRefresh = between(salesCategory, "if (!detailCategory) return;", "const filterOptions = data?.filterOptions");
  assert.doesNotMatch(categoryDetailRefresh, /setDetailData\(null\)/);
  assert.match(salesCategory, /const visibleDetailData = detailDataCategory === detailCategory \? detailData : null/);

  assert.match(sales, /finance-target-list-panel data-refresh-region" aria-busy=\{loading\}/);
  assert.match(sales, /loading && !targetsLoaded \? <div className="table-state">/);
  assert.match(product, /const productDetail = productDetailSnapshot\?\.productCode === detailProductCode/);
  assert.match(product, /setProductDetailSnapshot\(\{ productCode: requestedProductCode, detail: payload \}\)/);
  assert.match(importView, /<ImportRunRecordsView history=\{history\} loading=\{historyLoading\} loaded=\{historyLoaded\}/);
  const importRecords = await readSource("../app/import-run-records-view.tsx");
  assert.match(importRecords, /loading && !loaded/);
  assert.match(importRecords, /import-history-panel data-refresh-region" aria-busy=\{loading\}/);
});

test("refresh feedback is a non-blocking gradient with reduced-motion support", async () => {
  const styles = await readSource("../app/globals.css");
  const refreshStyles = styles.slice(styles.indexOf(".module-stage,"));

  assert.match(refreshStyles, /\.data-refresh-region[\s\S]*position: relative/);
  assert.match(refreshStyles, /\.data-refresh-region::after[\s\S]*pointer-events: none/);
  assert.match(refreshStyles, /\.data-refresh-region\[aria-busy="true"\]/);
  assert.match(refreshStyles, /@keyframes data-refresh-gradient/);
  assert.match(refreshStyles, /@media \(prefers-reduced-motion: reduce\)/);
  assert.doesNotMatch(refreshStyles, /pointer-events:\s*none[\s\S]{0,120}\.data-refresh-region\[aria-busy/);
});

test("module navigation remains client-side and preserves the previous lazy view", async () => {
  const page = await readSource("../app/page.tsx");

  assert.match(page, /const \[moduleTransitionPending, startModuleTransition\] = useTransition\(\)/);
  assert.match(page, /window\.history\.pushState\(null, "", nextUrl\)/);
  assert.match(page, /startModuleTransition\(\(\) => \{[\s\S]*setModuleViewSelection\(key, nextView\)[\s\S]*setImportSource\(nextSource \?\? null\)[\s\S]*setActive\(key\)/);
  assert.match(page, /module-stage-pending/);
  assert.doesNotMatch(page, /window\.location\.(?:reload|replace)\(/);
});

test("refresh errors retain already rendered business data", async () => {
  const [dashboard, sales, customer, operations, searchDialog, page] = await Promise.all([
    readSource("../app/dashboard-module-view.tsx"),
    readSource("../app/sales-module-view.tsx"),
    readSource("../app/customer-service-view.tsx"),
    readSource("../app/operations-view.tsx"),
    readSource("../app/global-search-dialog.tsx"),
    readSource("../app/page.tsx"),
  ]);

  assert.doesNotMatch(dashboard, /setSales\(null\)|setInventory\(null\)/);
  assert.match(dashboard, /经营看板刷新失败/);
  assert.match(sales, /if \(error && !summary\)/);
  assert.match(sales, /销售数据刷新失败/);
  assert.match(customer, /loading && !data && <tr>/);
  assert.doesNotMatch(customer, /\{loading && <tr>/);
  assert.match(operations, /loading && !loaded \? <DataState/);
  assert.match(operations, /operations-detail-body data-refresh-region" aria-busy=\{loading\}/);
  assert.match(searchDialog, /search-results data-refresh-region/);
  assert.match(searchDialog, /disabled=\{loading\}[\s\S]+onClick=\{\(\) => onSelectItem\(item\)\}/);
  const searchQueryUpdate = between(page, "const updateGlobalSearchQuery", "const selectGlobalSearchItem");
  assert.doesNotMatch(searchQueryUpdate, /setGlobalSearchResult\(null\)/);
});
