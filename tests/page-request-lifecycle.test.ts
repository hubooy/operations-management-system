import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { effectivePageForScope } from "../app/module-view-shared";

const pagePath = new URL("../app/page.tsx", import.meta.url);
const sharedPath = new URL("../app/module-view-shared.tsx", import.meta.url);
const shopPath = new URL("../app/shop-module-view.tsx", import.meta.url);
const salesPath = new URL("../app/sales-module-view.tsx", import.meta.url);
const importPath = new URL("../app/import-module-view.tsx", import.meta.url);
const productPath = new URL("../app/product-module-view.tsx", import.meta.url);
const inventoryPath = new URL("../app/inventory-module-view.tsx", import.meta.url);
const customerServicePath = new URL("../app/customer-service-view.tsx", import.meta.url);

function viewSource(page: string, start: string, end: string) {
  const startIndex = page.indexOf(start);
  const endIndex = page.indexOf(end, startIndex);
  assert.ok(startIndex >= 0, `missing view start: ${start}`);
  assert.ok(endIndex > startIndex, `missing view end: ${end}`);
  return page.slice(startIndex, endIndex);
}

test("filter scope changes issue exactly the first page request before committing pagination state", async () => {
  assert.equal(effectivePageForScope(7, "next-scope", "previous-scope"), 1);
  assert.equal(effectivePageForScope(7, "stable-scope", "stable-scope"), 7);
  assert.equal(effectivePageForScope(0, "stable-scope", "stable-scope"), 1);
  assert.equal(effectivePageForScope(
    effectivePageForScope(7, "next-scope", "previous-scope"),
    "next-scope",
    "next-scope",
  ), 1, "committing the new scope keeps the already-requested first page and cannot schedule a second page value");

  const [product, inventory, customerService] = await Promise.all([
    readFile(productPath, "utf8"),
    readFile(inventoryPath, "utf8"),
    readFile(customerServicePath, "utf8"),
  ]);
  assert.match(product, /const effectiveProductPage = effectivePageForScope\(productPage, productPageScopeKey, committedProductPageScopeKey\)/);
  assert.match(product, /page: String\(effectiveProductPage\)/);
  assert.match(product, /setCommittedProductPageScopeKey\(productPageScopeKey\);[\s\S]+setProductPage\(\(current\) => current === 1 \? current : 1\)/);
  assert.doesNotMatch(product, /page: String\(productPage\)/);

  for (const [effectivePage, statePage] of [["effectiveOverviewPage", "overviewPage"], ["effectivePlanPage", "planPage"], ["effectiveAgePage", "agePage"]]) {
    assert.match(inventory, new RegExp(`String\\(${effectivePage}\\)`));
    assert.doesNotMatch(inventory, new RegExp(`String\\(${statePage}\\)`));
  }
  assert.match(inventory, /effectivePageForScope\(overviewPage, overviewPageScopeKey, committedOverviewPageScopeKey\)/);
  assert.match(inventory, /effectivePageForScope\(planPage, planPageScopeKey, committedPlanPageScopeKey\)/);
  assert.match(inventory, /effectivePageForScope\(agePage, agePageScopeKey, committedAgePageScopeKey\)/);

  assert.match(customerService, /const effectivePage = effectivePageForScope\(page, customerListScopeKey, committedCustomerListScopeKey\)/);
  assert.match(customerService, /page: String\(effectivePage\)/);
  assert.match(customerService, /setCommittedCustomerListScopeKey\(customerListScopeKey\);[\s\S]+setPage\(\(current\) => current === 1 \? current : 1\)/);
  assert.doesNotMatch(customerService, /page: String\(page\)/);
});

test("ShopView makes one scoped summary request and rejects stale responses", async () => {
  const shop = await readFile(shopPath, "utf8");

  assert.equal((shop.match(/\/api\/sales\/summary/g) ?? []).length, 1);
  assert.match(shop, /activeTab === "products" \|\| activeTab === "promotion"/);
  assert.match(shop, /selectedOutletKeys\.forEach\(\(key\) => query\.append\("outlet", key\)\)/);
  assert.match(shop, /requestJson<SalesSummaryResponse>[\s\S]+signal: controller\.signal/);
  assert.match(shop, /requestControllerRef\.current\?\.abort\(\)/);
  assert.match(shop, /generation !== requestGenerationRef\.current/);
  assert.match(shop, /payload\.filterOptions\?\.shops \?\? payload\.outlets/);
  assert.doesNotMatch(shop, /filteredResponse|filteredPayload/);
});

test("netshop views send platform-qualified outlets instead of independent shop-name filters", async () => {
  const [shop, shared] = await Promise.all([readFile(shopPath, "utf8"), readFile(sharedPath, "utf8")]);
  const daily = viewSource(shop, "function ShopDailyProductPerformanceView(", "function ShopProductDataView(");
  const catalog = viewSource(shop, "function ShopSkuView(", "function ShopPromotionView(");
  const promotion = viewSource(shop, "function ShopPromotionView(", "type OutletTab =");
  const storeVisitors = viewSource(shop, "function StoreSpuVisitorMetric(", "function StoreAnalysisView(");

  for (const source of [daily, catalog]) {
    assert.match(source, /selectedOutletKeys\.forEach\(\(outlet\) => params\.append\("outlet", outlet\)\)/);
    assert.doesNotMatch(source, /params\.append\("shop"/);
    assert.match(source, /netshopOutletFilterKey\(shop\.platform, shop\.shopName\)/);
    assert.match(source, /label: `\$\{shop\.shopName[^}]*\} · \$\{shop\.platform\}`/);
  }
  assert.match(promotion, /selectedOutletKeys\.forEach\(\(outlet\) => params\.append\("outlet", outlet\)\)/);
  assert.match(promotion, /const promotionItemsSnapshotToken = scopedItems\?\.snapshotToken \?\? ""/);
  assert.match(promotion, /promotionOverview\?\.snapshotToken === promotionItemsSnapshotToken/);
  assert.match(promotion, /const currentItems = hasCompleteScopedPair \|\| !promotionDisplayPair/);
  assert.match(promotion, /const promotionShops = \(currentOverview\?\.filterOptions\.shops \?\? \[\]\)/);
  assert.match(promotion, /netshopOutletFilterKey\(shop\.platform, shop\.shopName\)/);
  assert.doesNotMatch(promotion, /params\.append\("shop"/);
  assert.match(promotion, /useState<PromotionPageSize>\(20\)/);
  assert.match(shop, /const promotionPageSizes: PromotionPageSize\[\] = \[20, 50, 100, 500\]/);
  assert.match(promotion, /pageSize: String\(pageSize\)/);
  assert.match(promotion, /setPageSize\(Number\(value\) as PromotionPageSize\); setPage\(1\)/);
  assert.match(promotion, /<SearchableSelect value=\{String\(pageSize\)\}/);
  assert.equal((promotion.match(/\/api\/netshop\/promotion-performance\/items/g) ?? []).length, 1);
  assert.equal((promotion.match(/\/api\/netshop\/promotion-performance\/overview/g) ?? []).length, 1);
  assert.doesNotMatch(promotion, /fetch\(`\/api\/netshop\/promotion-performance\?/);
  assert.match(promotion, /promotionItemsControllerRef\.current\?\.abort\(\)/);
  assert.match(promotion, /promotionOverviewControllerRef\.current\?\.abort\(\)/);
  assert.match(promotion, /generation === promotionItemsGenerationRef\.current/);
  assert.match(promotion, /generation === promotionOverviewGenerationRef\.current/);
  assert.match(promotion, /snapshotTokenPattern\.test\(payload\.snapshotToken\)/);
  assert.match(promotion, /if \(!promotionItemsSnapshotToken\)/);
  assert.doesNotMatch(promotion, /overviewReadyScopeKey|setOverviewReadyScopeKey/);
  const itemsRequest = viewSource(promotion, "  useEffect(() => {\n    const generation = promotionItemsGenerationRef.current + 1", "  useEffect(() => {\n    if (!promotionItemsSnapshotToken");
  assert.doesNotMatch(itemsRequest, /setPromotionItemsScopeKey\(""\)/);
  const overviewRequest = viewSource(promotion, "  useEffect(() => {\n    if (!promotionItemsSnapshotToken", "  const platformSubnav");
  assert.match(overviewRequest, /params\.set\("snapshotToken", promotionItemsSnapshotToken\)/);
  assert.match(overviewRequest, /payload\?\.snapshotToken !== promotionItemsSnapshotToken/);
  assert.match(overviewRequest, /response\.status === 503 \|\| tokenMismatch/);
  assert.match(overviewRequest, /claimSnapshotRestart\(promotionSnapshotRestartedTokensRef\.current, promotionItemsSnapshotToken\)/);
  assert.match(overviewRequest, /setPromotionItems\(null\)[\s\S]+setPromotionOverview\(null\)[\s\S]+setItemsRetryKey/);
  assert.match(overviewRequest, /payload\.snapshotToken !== promotionItemsSnapshotToken/);
  assert.doesNotMatch(overviewRequest, /debouncedQuery|pageSize|page: String\(page\)|q:/);
  assert.match(promotion, /重试概览/);
  assert.match(promotion, /重试商品排行/);
  assert.match(storeVisitors, /scopeOutlets\.forEach\(\(outlet\) => params\.append\("outlet", outlet\)\)/);
  assert.match(shop, /promotionOutlets\.forEach\(\(outlet\) => params\.append\("outlet", outlet\)\)/);
  assert.match(shop, /promotionPlatforms\.forEach\(\(platform\) => params\.append\("platform", platform\)\)/);
  const storeAnalysis = viewSource(shop, "function StoreAnalysisView(", "type ProductPerformanceColumnKey =");
  assert.equal((storeAnalysis.match(/\/api\/netshop\/promotion-performance\/overview/g) ?? []).length, 1);
  assert.doesNotMatch(storeAnalysis, /fetch\(`\/api\/netshop\/promotion-performance\?/);
  assert.doesNotMatch(storeAnalysis, /params\.set\("snapshotToken"/);
  assert.match(shared, /const snapshotRestartHistoryLimit = 8/);
  assert.match(shared, /if \(restartedTokens\.size >= snapshotRestartHistoryLimit\)[\s\S]+restartedTokens\.delete\(oldestToken\)/);
});

test("netshop catalog and daily product pages pair lightweight projections with their own snapshot", async () => {
  const shop = await readFile(shopPath, "utf8");
  const daily = viewSource(shop, "function ShopDailyProductPerformanceView(", "function ShopProductDataView(");
  const catalog = viewSource(shop, "function ShopSkuView(", "type PromotionPageKey =");
  const storeVisitors = viewSource(shop, "function StoreSpuVisitorMetric(", "function StoreTableMetric(");

  assert.match(storeVisitors, /new URLSearchParams\(\{ dimension: "spu", view: "summary", startDate, endDate \}\)/);
  assert.match(storeVisitors, /NetshopProductPerformanceSummaryResponse/);
  assert.match(storeVisitors, /snapshotTokenPattern\.test\(payload\.snapshotToken\)/);
  assert.doesNotMatch(storeVisitors, /pageSize|params\.set\("snapshotToken"/);

  assert.match(catalog, /const catalogBootstrapScopeKey = useMemo\(\(\) => JSON\.stringify\(\{[\s\S]+startDate:[\s\S]+endDate:[\s\S]+platforms:[\s\S]+outlets:/);
  const catalogBootstrapScope = viewSource(catalog, "  const catalogBootstrapScopeKey", "  const catalogPageScopeKey");
  assert.doesNotMatch(catalogBootstrapScope, /query|page/);
  assert.match(catalog, /const catalogPageScopeKey = useMemo\(\(\) => JSON\.stringify\(\{[\s\S]+bootstrap: catalogBootstrapScopeKey,[\s\S]+query: debouncedQuery\.trim\(\)/);
  assert.match(catalog, /const pageOnly = !forceFull[\s\S]+skuCatalogBootstrapKeyRef\.current === catalogBootstrapScopeKey[\s\S]+snapshotTokenPattern\.test\(expectedSnapshotToken\)/);
  assert.match(catalog, /params\.set\("view", "page"\);[\s\S]+params\.set\("snapshotToken", expectedSnapshotToken\)/);
  assert.match(catalog, /response\.status === 503 \|\| pageSnapshotMismatch/);
  assert.match(catalog, /claimSnapshotRestart\(skuCatalogRestartedTokensRef\.current, expectedSnapshotToken\)/);
  assert.match(catalog, /current\.snapshotToken === expectedSnapshotToken[\s\S]+items: pagePayload\.items, pagination: pagePayload\.pagination/);
  assert.match(catalog, /skuCatalogBootstrapKeyRef\.current = catalogBootstrapScopeKey;[\s\S]+skuCatalogSnapshotTokenRef\.current = fullPayload\.snapshotToken/);
  assert.match(catalog, /onChange=\{\(event\) => setQuery\(event\.target\.value\)\}/);

  assert.match(daily, /const \[showComparison, setShowComparison\] = useState\(false\)/);
  assert.match(daily, /const currentPerformanceScopeKey = useMemo\(\(\) => JSON\.stringify\(\{[\s\S]+dimension,[\s\S]+startDate:[\s\S]+endDate:[\s\S]+query: debouncedQuery\.trim\(\)[\s\S]+platforms:[\s\S]+outlets:/);
  assert.match(daily, /comparisonPeriod \? requestPerformance\("comparison", comparisonPeriod, comparisonPerformanceScopeKey\) : Promise\.resolve\(null\)/);
  assert.match(daily, /params\.set\("view", "page"\);[\s\S]+params\.set\("snapshotToken", expectedSnapshotToken\)/);
  assert.match(daily, /const pageSnapshotMismatch = pageOnly && response\.ok && payload\?\.snapshotToken !== expectedSnapshotToken/);
  assert.match(daily, /claimSnapshotRestart\(productPerformanceRestartedTokensRef\.current, restartResult\.snapshotToken\)/);
  assert.match(daily, /currentPerformanceSnapshotTokenRef\.current = currentResult\.payload\.snapshotToken/);
  assert.match(daily, /comparisonPerformanceSnapshotTokenRef\.current = comparisonResult\.payload\.snapshotToken/);
  assert.match(daily, /current\.snapshotToken === currentResult\.expectedSnapshotToken[\s\S]+items: currentResult\.payload\.items, pagination: currentResult\.payload\.pagination/);
  assert.match(daily, /current\.snapshotToken === comparisonResult\.expectedSnapshotToken[\s\S]+items: comparisonResult\.payload\.items, pagination: comparisonResult\.payload\.pagination/);
  assert.match(daily, /onChange=\{\(event\) => setQuery\(event\.target\.value\)\}/);
});

test("netshop promotion, catalog, and daily filters select page one before their first scoped request", async () => {
  const shop = await readFile(shopPath, "utf8");
  const daily = viewSource(shop, "function ShopDailyProductPerformanceView(", "function ShopProductDataView(");
  const catalog = viewSource(shop, "function ShopSkuView(", "type PromotionPageKey =");
  const promotion = viewSource(shop, "function ShopPromotionView(", "type OutletTab =");

  assert.match(promotion, /const promotionScopeKey = useMemo\(\(\) => JSON\.stringify\(\{[\s\S]+startDate:[\s\S]+endDate:[\s\S]+platform:[\s\S]+outlets: \[\.\.\.selectedOutletKeys\]\.sort\(\)/);
  assert.match(promotion, /const promotionQuery = query\.trim\(\) === "" \? "" : debouncedQuery\.trim\(\)/);
  assert.match(promotion, /const promotionPageScopeKey = useMemo\(\(\) => JSON\.stringify\(\{[\s\S]+bootstrap: promotionScopeKey,[\s\S]+query: promotionQuery,[\s\S]+pageSize/);
  assert.match(promotion, /const effectivePromotionPage = effectivePageForScope\(page, promotionPageScopeKey, committedPromotionPageScopeKey\)/);
  assert.match(promotion, /page: String\(effectivePromotionPage\)/);
  assert.match(promotion, /setCommittedPromotionPageScopeKey\(promotionPageScopeKey\);[\s\S]+setPage\(\(current\) => current === 1 \? current : 1\)/);
  assert.match(promotion, /if \(promotionQuery\) params\.set\("q", promotionQuery\)/);
  assert.match(promotion, /onChange=\{\(event\) => setQuery\(event\.target\.value\)\}/);
  assert.doesNotMatch(promotion, /setQuery\(event\.target\.value\); setPage\(1\)/);
  assert.doesNotMatch(promotion, /page: String\(page\)/);

  assert.match(catalog, /const catalogBootstrapScopeKey = useMemo\(\(\) => JSON\.stringify\(\{[\s\S]+startDate:[\s\S]+endDate:[\s\S]+platforms:[\s\S]+outlets:/);
  assert.match(catalog, /const catalogPageScopeKey = useMemo\(\(\) => JSON\.stringify\(\{[\s\S]+bootstrap: catalogBootstrapScopeKey,[\s\S]+query: debouncedQuery\.trim\(\)/);
  assert.match(catalog, /const effectiveCatalogPage = effectivePageForScope\(page, catalogPageScopeKey, committedCatalogPageScopeKey\)/);
  assert.match(catalog, /page: String\(effectiveCatalogPage\)/);
  assert.match(catalog, /setCommittedCatalogPageScopeKey\(catalogPageScopeKey\);[\s\S]+setPage\(\(current\) => current === 1 \? current : 1\)/);
  assert.doesNotMatch(catalog, /page: String\(page\)/);

  assert.match(daily, /const currentPerformanceScopeKey = useMemo\(\(\) => JSON\.stringify\(\{[\s\S]+dimension,[\s\S]+startDate:[\s\S]+endDate:[\s\S]+query:[\s\S]+platforms:[\s\S]+outlets:/);
  assert.match(daily, /const effectivePerformancePage = effectivePageForScope\(page, currentPerformancePageScopeKey, committedPerformancePageScopeKey\)/);
  assert.match(daily, /page: String\(effectivePerformancePage\)/);
  assert.match(daily, /setCommittedPerformancePageScopeKey\(currentPerformancePageScopeKey\);[\s\S]+setPage\(\(current\) => current === 1 \? current : 1\)/);
  assert.doesNotMatch(daily, /page: String\(page\)/);
});

test("ImportView loads history only for history views and isolates seven domain failures", async () => {
  const importView = await readFile(importPath, "utf8");

  assert.match(importView, /useState\(false\)[\s\S]+historyVisible = activeSection === "history"/);
  assert.ok(importView.indexOf("if (!historyVisible) return;") < importView.indexOf("Promise.allSettled(["));
  assert.equal((importView.match(/requestJson<[^\n]+\("\/api\//g) ?? []).length, 7);
  assert.match(importView, /Promise\.allSettled\(\[/);
  assert.match(importView, /historyRequestControllerRef\.current\?\.abort\(\)/);
  assert.match(importView, /generation !== historyRequestGenerationRef\.current/);
  assert.match(importView, /setHistory\(\(current\) => combined\.length > 0 \|\| domainErrors\.length === 0 \? combined : current\)/);
  assert.match(importView, /setHistoryDomainErrors\(domainErrors\)/);
  assert.match(importView, /if \(!historyVisible\) \{[\s\S]+historyRequestGenerationRef\.current \+= 1;[\s\S]+\.abort\(\)/);
  assert.doesNotMatch(importView, /await Promise\.all\(\[[\s\S]+\/api\/imports\/sales/);
  assert.match(importView, /const canImport = currentUser\?\.role === "admin"/);
  assert.match(importView, /if \(!canImport\) \{[\s\S]+仅管理员可以上传和导入业务数据/);
  assert.match(importView, /type="file"[\s\S]{0,180}disabled=\{!canImport\}/);
  assert.match(importView, /className=\{`dropzone[\s\S]{0,240}disabled=\{!canImport\}/);
  assert.match(importView, /disabled=\{!canImport \|\| !selectedFile \|\| uploading\}/);
  assert.match(importView, /当前账号仅可查看导入历史/);
});

test("heavy module views are lazy and the dashboard does not eagerly import their runtime chunks", async () => {
  const [page, sales] = await Promise.all([readFile(pagePath, "utf8"), readFile(salesPath, "utf8")]);
  for (const [scope, modulePath] of [
    ["market", "market-view"],
    ["n8n_workflows", "n8n-workflow-view"],
    ["workflow", "operations-view"],
    ["settings", "settings-view"],
    ["customer_service", "customer-service-view"],
    ["ai", "ai-module-view"],
    ["dashboard", "dashboard-module-view"],
    ["shop", "shop-module-view"],
    ["sales", "sales-module-view"],
    ["inventory", "inventory-module-view"],
    ["product", "product-module-view"],
    ["import", "import-module-view"],
  ]) {
    assert.match(page, new RegExp(`createReloadableLazy\\("${scope}", \\(\\) => import\\("\\./${modulePath}"\\)\\)`));
    assert.doesNotMatch(page, new RegExp(`^import (?!type )[^\\n]+from "\\./${modulePath}"`, "m"));
  }
  assert.match(sales, /createReloadableLazy\("sales", \(\) => import\("\.\/sales-category-view"\)\)/);
  assert.doesNotMatch(sales, /^import (?!type )[^\n]+from "\.\/sales-category-view"/m);
  assert.match(page, /const GlobalSearchDialog = lazy\(\(\) => import\("\.\/global-search-dialog"\)\)/);
  assert.match(page, /if \(!shellLocationReady \|\| !currentUser \|\| active !== "market" \|\| activeModuleView === "settings" \|\| activeModuleView === "compare"\) return;[\s\S]+import\("\.\/market-view"\)/);
  assert.match(page, /const \[shellLocationReady, setShellLocationReady\] = useState\(false\)/);
  assert.match(page, /setShellLocationReady\(true\)/);
  assert.match(page, /\{shellLocationReady \? <Suspense[\s\S]+<View range=/);
  assert.match(page, /正在打开目标工作区/);
  assert.match(page, /<Suspense fallback=\{<section className="panel data-state" role="status"/);
  assert.match(page, /\{searchOpen && <GlobalSearchLoadBoundary[\s\S]+<Suspense fallback=\{<GlobalSearchLoadingDialog[\s\S]+<GlobalSearchDialogView/);
  assert.match(page, /GlobalSearchLoadingDialog[\s\S]+<Dialog open onClose=\{onClose\}/);
  assert.match(page, /class GlobalSearchLoadBoundary[\s\S]+<Dialog open onClose=\{this\.props\.onClose\}/);
  assert.match(page, /setGlobalSearchLoadVersion\(\(version\) => version \+ 1\)/);
  assert.doesNotMatch(page, /fallback=\{<div className="modal-backdrop"/);
  assert.doesNotMatch(page, /function SettingsView\(|type OperatingSettings =/);
  assert.doesNotMatch(page, /function CustomerServiceView\(|function AiAssistantView\(/);
});

test("global search pagination is query-keyed, cancellable, deduplicated, and target-gated", async () => {
  const page = await readFile(pagePath, "utf8");
  const home = page.slice(page.indexOf("export default function Home()"));

  assert.match(home, /new URLSearchParams\(\{ q: query, group: groupKey, page: String\(nextPage\) \}\)/);
  assert.match(home, /Math\.min\(10_000, Math\.max\(2, page\)\)/);
  assert.match(home, /hasMore: nextPage < 10_000 && incomingGroup\.hasMore/);
  assert.match(home, /globalSearchGroupRequestKeyRef\.current = requestKey/);
  assert.match(home, /generation !== globalSearchGroupGenerationRef\.current \|\| requestKey !== globalSearchGroupRequestKeyRef\.current/);
  assert.match(home, /globalSearchGroupControllerRef\.current\?\.abort\(\)/);
  assert.match(home, /const seenIds = new Set\(currentGroup\.items\.map\(\(item\) => item\.id\)\)/);
  assert.match(home, /currentResult\.groups\.map\(\(group\) => group\.key === groupKey \? mergedGroup : group\)/);
  assert.match(home, /cancelGlobalSearchRequests\(\);[\s\S]+setGlobalSearchQuery\(value\)/);
  assert.match(home, /import\("\.\/global-search-dialog"\)[\s\S]+parseGlobalSearchTarget\(item\.target\)/);
  assert.match(home, /isGlobalSearchItemModuleValid,[\s\S]+isGlobalSearchTargetForItem,[\s\S]+parseGlobalSearchTarget/);
  assert.match(home, /if \(target && isGlobalSearchTargetForItem\(item, target\)\)/);
  assert.match(home, /targetModule = target\.module;[\s\S]+targetView = target\.view/);
  assert.match(home, /else if \(isGlobalSearchItemModuleValid\(item\)\)[\s\S]+targetModule = item\.module/);
  assert.match(home, /selectModule\(targetModule, undefined, targetView\)/);
  assert.match(home, /Navigation fails closed if the validation chunk cannot be loaded/);
});
