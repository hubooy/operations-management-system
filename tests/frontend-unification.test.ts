import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

import { normalizeTableCellValue, tableRowMatchesColumnFilters } from "../lib/ui/table-column-filter";

const source = (path: string) => readFile(new URL(path, import.meta.url), "utf8");

test("column filters normalize cells and combine multi-select columns with AND semantics", () => {
  const filters = new Map<number, ReadonlySet<string>>([
    [0, new Set(["京东", "天猫"])],
    [2, new Set(["正常"])],
  ]);
  assert.equal(normalizeTableCellValue("  京东\n 自营  "), "京东 自营");
  assert.equal(tableRowMatchesColumnFilters(["京东", "净水", "正常"], filters), true);
  assert.equal(tableRowMatchesColumnFilters(["抖音", "净水", "正常"], filters), false);
  assert.equal(tableRowMatchesColumnFilters(["天猫", "净水", "异常"], filters), false);

  const dateFilter = new Map<number, ReadonlySet<string>>([[0, new Set(["2026-08-02"])]]);
  assert.equal(tableRowMatchesColumnFilters([["2026-08-01", "2026-08-02"]], dateFilter), true);
  assert.equal(tableRowMatchesColumnFilters([["2026-08-03", "2026-08-04"]], dateFilter), false);
});

test("all rendered React tables receive accessible multi-select column filtering", async () => {
  const [page, shopView, component, styles, netshopQuery] = await Promise.all([
    source("../app/page.tsx"),
    source("../app/shop-module-view.tsx"),
    source("../app/ui/table-column-filters.tsx"),
    source("../app/globals.css"),
    source("../backend/netshop/query.py"),
  ]);
  assert.match(page, /<TableColumnFilters \/>/);
  assert.match(component, /querySelectorAll<HTMLTableElement>\("table"\)/);
  assert.match(component, /querySelectorAll<HTMLTableCellElement>\("thead th"\)/);
  assert.match(component, /aria-multiselectable="true"/);
  assert.match(component, /tableRowMatchesColumnFilters/);
  assert.match(component, /dataset\.columnFilterValues/);
  assert.match(component, /const pendingTables = new Set<HTMLTableElement>\(\)/);
  assert.match(component, /observer\.observe\(table, \{[\s\S]*?characterData: true/);
  assert.match(component, /documentObserver\.observe\(document\.body, \{[\s\S]*?childList: true,[\s\S]*?subtree: true,[\s\S]*?attributes: true/);
  assert.doesNotMatch(component, /documentObserver\.observe\(document\.body, \{[^}]*characterData/);
  assert.match(component, /document\.addEventListener\("input", scheduleEventTable\)/);
  assert.match(component, /document\.addEventListener\("change", scheduleEventTable\)/);
  assert.match(component, /closeOnExternalScroll/);
  assert.match(component, /popoverRef\.current\?\.contains\(source\)/);
  assert.match(shopView, /data-column-filter-values=\{item\.dates\?\.join\("\\u001f"\)\}/);
  assert.match(netshopQuery, /dates_by_identity:/);
  assert.match(netshopQuery, /values_list\("business_date", flat=True\)/);
  assert.match(netshopQuery, /"datesTruncated": len\(dates\) </);
  assert.match(styles, /\.column-filter-popover/);
  assert.match(styles, /overscroll-behavior: contain/);
  assert.match(styles, /\.column-filter-row-hidden/);
});

test("left navigation follows the task-oriented groups and exact order", async () => {
  const [page, catalog, navigation] = await Promise.all([
    source("../app/page.tsx"),
    source("../app/shell/navigation-catalog.ts"),
    source("../app/shell/sidebar-navigation.tsx"),
  ]);
  assert.match(catalog, /label: "协同执行",[\s\S]*?keys: \["workflow", "n8n_workflows"\]/);
  assert.match(catalog, /label: "经营分析",[\s\S]*?keys: \["dashboard", "shop", "market", "customer_service", "sales"\]/);
  assert.match(catalog, /label: "商品与供应链",[\s\S]*?keys: \["inventory", "product", "import"\]/);
  assert.match(catalog, /label: "系统与智能",[\s\S]*?keys: \["settings", "ai"\]/);
  assert.match(navigation, /navGroups\.map\(\(group, groupIndex\)/);
  assert.match(navigation, /<a[\s\S]*?href=\{hrefForModule\(moduleKey, moduleKey === "ai" \? "agents" : undefined\)\}/);
  assert.match(navigation, /aria-current=\{selected \? "page" : undefined\}/);
  assert.match(page, /<SidebarNavigation active=\{active\}/);
});

test("data import navigation switches real workspaces instead of rendering inert tabs", async () => {
  const importView = await source("../app/import-module-view.tsx");
  assert.match(importView, /type ImportTab = ModuleViewKey<"import">/);
  assert.match(importView, /const activeSection = moduleView/);
  assert.match(importView, /role="tablist" aria-label="数据导入工作区"/);
  assert.match(importView, /onClick=\{\(\) => onModuleViewChange\("history"\)\}/);
  assert.match(importView, /activeSection === "chains" && <ImportChainRulesView/);
  assert.doesNotMatch(importView, /数据连续性|latestBySource/);
  assert.match(importView, /activeSection === "history" && <ImportRunRecordsView/);
});

test("operational navigation and tables use the balanced density baseline", async () => {
  const [styles, tokens] = await Promise.all([
    source("../app/globals.css"),
    source("../app/styles/tokens.css"),
  ]);
  assert.match(tokens, /--app-sidebar-collapsed-width: 80px/);
  assert.match(styles, /\.nav-copy b \{ font-size: 14px; \}/);
  assert.match(styles, /\.nav-copy small \{ font-size: 11px; \}/);
  assert.match(styles, /\.subnav button \{ min-width: 92px; height: 36px; font-size: 12px; \}/);
  assert.match(styles, /\.data-table \{ font-size: 12px; \}/);
  assert.match(styles, /\.data-table th \{ padding-top: 11px; padding-bottom: 11px; font-size: 11px; \}/);
  assert.match(styles, /\.date-selector > \.searchable-select \.searchable-select-menu \{[^}]*right: 0; left: auto;/);
});

test("one global page head owns the shared period and passes it to every module", async () => {
  const [page, header, dashboard, market] = await Promise.all([
    source("../app/page.tsx"),
    source("../app/shell/global-header.tsx"),
    source("../app/dashboard-module-view.tsx"),
    source("../app/market-view.tsx"),
  ]);
  assert.doesNotMatch(page, /className="page-intro"/);
  assert.match(page, /title=\{current\.label\}/);
  assert.match(header, /<h1 id="global-page-title" ref=\{titleRef\} tabIndex=\{-1\}>\{title\}<\/h1>/);
  assert.match(page, /customStartDate=\{globalPeriod\.startDate\} customEndDate=\{globalPeriod\.endDate\}/);
  assert.match(dashboard, /new URLSearchParams\(\{ range: apiRange \}\)/);
  assert.match(dashboard, /query\.set\("startDate", customStartDate\)/);
  assert.match(dashboard, /query\.set\("endDate", customEndDate\)/);
  assert.match(market, /const marketStartDate = customStartDate/);
  assert.match(market, /const marketEndDate = customEndDate/);
  assert.match(market, /market-overview-period market-global-period/);
});

test("module filters transmit repeated values through their API boundaries", async () => {
  const [customer, customerRoute, marketAdmin, masterRoute, annotation, annotationRoute, inventoryRoute] = await Promise.all([
    source("../app/customer-service-view.tsx"),
    source("../app/api/customer-service/conversations/route.ts"),
    source("../app/market-master-admin-panel.tsx"),
    source("../app/api/market/master/route.ts"),
    source("../app/market-annotation-view.tsx"),
    source("../app/api/market/annotations/route.ts"),
    source("../app/api/inventory/overview/route.ts"),
  ]);
  assert.match(customer, /categories\.forEach\(\(value\) => params\.append\("category", value\)\)/);
  assert.match(customer, /shopNames\.forEach\(\(value\) => params\.append\("shopName", value\)\)/);
  assert.match(customerRoute, /searchParams\.getAll\("shopName"\)/);
  assert.match(customerRoute, /searchParams\.getAll\("category"\)/);
  assert.match(marketAdmin, /pendingPriceSources\.forEach\(\(value\) => params\.append\("pendingPriceSource", value\)\)/);
  assert.match(masterRoute, /pendingPriceSources: params\.getAll\("pendingPriceSource"\)/);
  assert.match(annotation, /itemSegments\.forEach\(\(value\) => params\.append\("itemSegment", value\)\)/);
  assert.match(annotationRoute, /itemSegments: params\.getAll\("itemSegment"\)/);
  assert.match(inventoryRoute, /"view", "startDate", "endDate"/);
  assert.match(inventoryRoute, /rawQuery: params\.toString\(\)/);
});

test("promotion analysis separates JD and Tmall pages with platform-specific labels and links", async () => {
  const [shopView, query, route] = await Promise.all([
    source("../app/shop-module-view.tsx"),
    source("../backend/netshop/query.py"),
    source("../app/api/netshop/promotion-performance/route.ts"),
  ]);
  assert.match(shopView, /title: "京东推广"/);
  assert.match(shopView, />京东推广<\/button>/);
  assert.match(shopView, />天猫推广<\/button>/);
  assert.match(shopView, /platform: pageConfig\.platform/);
  assert.match(shopView, /netshopProductUrl\(item\.platform, item\.id\)/);
  assert.match(shopView, /京准通总订单金额不是退款后的销售净额/);
  assert.match(query, /NetshopPromotionProductDaily/);
  assert.match(query, /NetshopPromotionShopDaily/);
  assert.match(route, /读取网店推广数据失败/);
});
