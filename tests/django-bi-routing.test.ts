import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


test("BI public route is a thin authenticated adapter to the dedicated Django reader", async () => {
  const [route, dashboard, backend] = await Promise.all([
    readFile(new URL("../app/api/bi/overview/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/dashboard-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../backend/bi/query.py", import.meta.url), "utf8"),
  ]);
  assert.match(route, /requireAppPrincipal/);
  assert.match(route, /requireUnrestrictedDataScope/);
  assert.match(route, /requestDjangoBiOverview/);
  assert.match(route, /last30/);
  assert.doesNotMatch(route, /getSalesSummary|getInventoryDashboardOverview|\.prepare\(/);
  assert.match(dashboard, /requestJson<BiDashboardResponse>\(`\/api\/bi\/overview\?/);
  assert.doesNotMatch(dashboard, /\/api\/sales\/summary|\/api\/inventory\/overview/);
  assert.doesNotMatch(dashboard, /100\s*-\s*inventory\.metrics\.urgentCount/);
  assert.match(backend, /get_sales_summary/);
  assert.match(backend, /last30/);
  assert.match(backend, /inventory_overview/);
  assert.match(backend, /before == after/);
  assert.match(backend, /inventoryHealthScore/);
});
