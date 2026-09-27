import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";
import { buildMarketOverviewAnalyticsSql, buildMarketRankingCtes } from "../lib/market/overview-sql";

test("market ranking limits candidate ids before expensive row enrichment", () => {
  const sql = buildMarketRankingCtes();
  assert.match(sql, /top_ranked_ids AS MATERIALIZED/);
  assert.match(sql, /LIMIT 200/);
  assert.ok(sql.indexOf("LIMIT 200") < sql.indexOf("market_image_cache"));
  assert.ok(sql.indexOf("LIMIT 200") < sql.indexOf("SELECT 1 FROM market_netshop_active_projection"));
  assert.doesNotMatch(sql, /market_effective_rows AS MATERIALIZED/);
});

test("market ranking can page a small candidate window before expensive enrichment", () => {
  const sql = buildMarketRankingCtes({ rankingLimit: 20, rankingOffset: 40 });
  assert.match(sql, /LIMIT 20 OFFSET 40/);
  assert.ok(sql.indexOf("LIMIT 20 OFFSET 40") < sql.indexOf("market_image_cache"));
  assert.ok(sql.indexOf("LIMIT 20 OFFSET 40") < sql.indexOf("SELECT 1 FROM market_netshop_active_projection"));
});

test("market price-band ranking still filters before the bounded enrichment stage", () => {
  const sql = buildMarketRankingCtes({
    factWhere: "WHERE m.category=?",
    where: "WHERE m.brand=?",
    priceBandWhere: "WHERE price_band IN (?3)",
  });
  assert.match(sql, /ranking_candidates AS MATERIALIZED/);
  assert.match(sql, /m\.category=\? AND m\.brand=\?/);
  assert.match(sql, /SELECT id FROM ranking_candidates WHERE price_band IN \(\?3\)/);
  assert.ok(sql.indexOf("SELECT id FROM ranking_candidates") < sql.indexOf("market_image_cache"));
});

test("market full analytics aggregates shared monthly rows without a section cross join", () => {
  const sql = buildMarketOverviewAnalyticsSql({ useEffectiveMetricsCache: true });
  assert.match(sql, /analytics_filtered AS MATERIALIZED/);
  assert.match(sql, /analytics_core AS MATERIALIZED/);
  assert.match(sql, /analytics_dimensions AS MATERIALIZED/);
  assert.doesNotMatch(sql, /CROSS JOIN analytics_sections/);
});

test("market read indexes cover ranking order and distinct image aggregation", async () => {
  const [schema, migration] = await Promise.all([
    readFile(new URL("../lib/market/schema-core.ts", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0047_market_read_indexes.sql", import.meta.url), "utf8"),
  ]);
  for (const source of [schema, migration]) {
    assert.match(source, /market_entries_rank_order_idx/);
    assert.match(source, /market_entries_image_url_idx/);
  }
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec("CREATE TABLE market_ranking_entries (id INTEGER PRIMARY KEY, rank INTEGER, gmv_cents INTEGER, image_url TEXT)");
  for (const statement of migration.split("--> statement-breakpoint").map((item) => item.trim()).filter(Boolean)) sqlite.exec(statement);
  const indexes = sqlite.prepare("PRAGMA index_list('market_ranking_entries')").all() as Array<{ name: string }>;
  assert.deepEqual(new Set(indexes.map((item) => item.name)), new Set(["market_entries_image_url_idx", "market_entries_rank_order_idx"]));
  sqlite.close();
});

test("inventory APIs bound response rows while preserving totals and recommendations", async () => {
  const [query, route, inventoryView] = await Promise.all([
    readFile(new URL("../backend/inventory/query.py", import.meta.url), "utf8"),
    readFile(new URL("../app/api/inventory/overview/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/inventory-module-view.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(query, /filtered\[offset : offset \+ page_size\]/);
  assert.match(query, /"total": len\(filtered\)/);
  assert.match(query, /"returned": len\(filtered\[offset : offset \+ page_size\]\)/);
  assert.match(query, /recommendations = \[\] if suppressed else sorted\([\s\S]*?\)\[:50\]/);
  assert.match(query, /"items": filtered\[offset : offset \+ page_size\]/);
  assert.match(route, /normalizeInventorySelections\(params\.getAll\("warehouse"\)/);
  assert.match(route, /normalizeInventorySelections\(params\.getAll\("brand"\)/);
  assert.match(route, /normalizeInventorySelections\(params\.getAll\("category"\)/);
  assert.match(route, /rawQuery: params\.toString\(\)/);
  assert.match(inventoryView, /debouncedInventoryQuery/);
  assert.match(inventoryView, /overviewGenerationRef/);
});

test("sales and inventory tabs only request the data source needed by the visible tab", async () => {
  const [salesView, inventoryView] = await Promise.all([
    readFile(new URL("../app/sales-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/inventory-module-view.tsx", import.meta.url), "utf8"),
  ]);

  assert.equal((salesView.match(/\/api\/sales\/summary/g) ?? []).length, 1);
  assert.match(salesView, /const usesSalesSummary = activeTab === "overview" \|\| activeTab === "channel"/);
  assert.match(salesView, /if \(!usesSalesSummary\) return;/);
  assert.match(salesView, /retryKey, usesSalesSummary\]/);

  assert.equal((inventoryView.match(/\/api\/inventory\/overview/g) ?? []).length, 1);
  assert.equal((inventoryView.match(/\/api\/inventory\/age-analysis/g) ?? []).length, 1);
  assert.match(inventoryView, /const usesInventoryOverview = activeTab === "overview" \|\| activeTab === "plan"/);
  assert.match(inventoryView, /const projection = activeTab === "plan" \? "plan" : "overview"/);
  assert.match(inventoryView, /new URLSearchParams\(\{ view: projection \}\)/);
  assert.doesNotMatch(inventoryView, /view: projection, startDate: customStartDate, endDate: customEndDate/);
  assert.match(inventoryView, /if \(projection === "plan"\)[\s\S]*?params\.set\("planPage"[\s\S]*?else[\s\S]*?params\.set\("page"/);
  assert.match(inventoryView, /const usesInventoryAgeAnalysis = activeTab === "age" \|\| activeTab === "stale"/);
  assert.match(inventoryView, /if \(!usesInventoryOverview\) return;/);
  assert.match(inventoryView, /if \(!usesInventoryAgeAnalysis\) return;/);
  assert.match(inventoryView, /if \(tab === "age" \|\| tab === "stale"\) await loadAgeAnalysis\(tab\);\s+else if \(tab === "inbound"\) await loadInboundMonitor\(\);\s+else await loadOverview\(\);/);
  assert.ok((inventoryView.match(/await refreshActiveInventoryTab\(\)/g) ?? []).length >= 5);
  assert.match(inventoryView, /const importReplenishmentPlans = useCallback[\s\S]*?await refreshActiveInventoryTab\(\)/);
  assert.match(inventoryView, /const batchSyncPlansToDingTalk = useCallback[\s\S]*?await refreshActiveInventoryTab\(\)/);
  assert.doesNotMatch(inventoryView, /Promise\.all\(\[loadOverview\(\), loadAgeAnalysis\(\)\]\)/);
});

test("customer, sales, and product views avoid superseded or duplicate work", async () => {
  const [productView, customer, customerRoute, customerDatabase, sales] = await Promise.all([
    readFile(new URL("../app/product-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/customer-service-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/customer-service/conversations/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/customer-service/database.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/sales/summary.py", import.meta.url), "utf8"),
  ]);
  assert.match(customer, /debouncedCustomerQuery/);
  assert.match(customer, /includeOptions/);
  assert.match(customer, /listControllerRef\.current\?\.abort\(\)/);
  assert.match(customer, /listGenerationRef\.current === generation/);
  assert.match(customer, /listRequestKeyRef\.current === requestKey/);
  assert.match(productView, /products\/summary\?\$\{params\}[\s\S]*signal/);
  assert.match(customerRoute, /includeOptions: url\.searchParams\.get\("includeOptions"\) !== "false"/);
  assert.doesNotMatch(customerDatabase, /SELECT COUNT\(\*\) AS total FROM customer_service_conversations \$\{where\}[\s\S]*SELECT COUNT\(\*\) AS total, SUM/);
  const aiConversationQuery = customerDatabase.slice(customerDatabase.indexOf("export async function getCustomerServiceConversationsForAi"));
  assert.match(aiConversationQuery, /listCustomerServiceConversations\(\{[\s\S]*?includeOptions: false \}, principal, options\)/);
  assert.match(sales, /def _period_metrics\([\s\S]*queryset\.aggregate\(\*\*aggregate_fields\)/);
  assert.match(sales, /def _grouped_yoy\([\s\S]*\.annotate\([\s\S]*year_ago_net_sales_cents/);
});
