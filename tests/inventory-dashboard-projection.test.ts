import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("BI 首页通过 Django BI reader 获取库存 dashboard 轻量投影", async () => {
  const [dashboardView, route, biQuery, overview] = await Promise.all([
    readFile(new URL("../app/dashboard-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/bi/overview/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/bi/query.py", import.meta.url), "utf8"),
    readFile(new URL("../backend/inventory/query.py", import.meta.url), "utf8"),
  ]);

  assert.match(dashboardView, /new URLSearchParams\(\{ range: apiRange \}\)/);
  assert.match(dashboardView, /requestJson<BiDashboardResponse>/);
  assert.match(route, /requestDjangoBiOverview/);
  assert.match(route, /params\.toString\(\)/);
  assert.doesNotMatch(route, /getInventoryDatabase|getD1Database|env\.DB/);
  assert.match(biQuery, /"view": "dashboard"/);
  assert.match(biQuery, /inventory_overview/);
  assert.match(biQuery, /source_revisions/);
  assert.match(overview, /if view == "dashboard":/);
  assert.match(overview, /return \{key: response\[key\] for key in \("hasInventory", "sync", "metrics", "health"\)\}/);
  assert.match(overview, /if view == "plan":[\s\S]*?query_plans/);
  assert.match(overview, /"operation": "freshness"/);
  assert.match(overview, /freshness\.get\("dataStartDate"\)[\s\S]*freshness\.get\("dataCutoffDate"\)/);
  assert.doesNotMatch(overview, /sales_order_lines|sales_import_batches/);
});
