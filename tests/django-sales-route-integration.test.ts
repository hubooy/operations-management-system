import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

const migratedRoutes = [
  "app/api/sales/summary/route.ts",
  "app/api/sales/category-analysis/route.ts",
  "app/api/sales/category-analysis/detail/route.ts",
];

test("all public sales analysis routes use the Django-only signed gateway", async () => {
  for (const route of migratedRoutes) {
    const source = await readFile(resolve(route), "utf8");
    assert.match(source, /requireAppPrincipal\(/, route);
    assert.match(source, /routeDjangoSalesReadRequest\(\{/, route);
    assert.match(source, /routeDjangoSalesReadRequest\(\{\s*request,\s*principal\s*\}\)/, route);
    assert.doesNotMatch(source, /getSalesDatabase|ensureSalesSchema|legacy|shadow/, route);
  }
});

test("sales imports use the Django writer and never write D1", async () => {
  const writerRoutes = [
    "app/api/imports/sales/route.ts",
    "app/api/imports/sales/chunks/route.ts",
    "app/api/imports/sales/verify/route.ts",
  ];
  for (const route of writerRoutes) {
    const source = await readFile(resolve(route), "utf8");
    assert.doesNotMatch(source, /getSalesDatabase|ensureSalesSchema|saveSalesImport/, route);
  }

  const gateway = await readFile(resolve("lib/django/sales-gateway.ts"), "utf8");
  assert.doesNotMatch(gateway, /routeSalesReadRequest|legacy|shadow|TERUISI_SALES_BACKEND/);
});
