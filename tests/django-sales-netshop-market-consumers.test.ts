import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { registerHooks } from "node:module";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import type { SalesConsumerReader } from "../lib/django/sales-consumer-reader";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return { url: "data:text/javascript,export const env={};", shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const { readMarketSalesMetrics } = await import("../lib/market/database");
const { PublicApiError } = await import("../lib/http/api-error");

const principal: AppPrincipal = {
  email: "market-reader@example.com",
  displayName: "Market reader",
  role: "analyst",
  scope: null,
};

test("market sales consumer chunks products and long date ranges without losing the principal or revision", async () => {
  const calls: Array<{ principal: AppPrincipal; productCodes: string[]; startDate: string | null; endDate: string | null }> = [];
  const reader: SalesConsumerReader = {
    async read(actualPrincipal, request) {
      assert.equal(request.operation, "market_product_metrics");
      if (request.operation !== "market_product_metrics") throw new Error("unexpected operation");
      calls.push({
        principal: actualPrincipal,
        productCodes: request.productCodes,
        startDate: request.startDate ?? null,
        endDate: request.endDate ?? null,
      });
      return {
        revision: "sales:42",
        data: {
          rows: request.productCodes.map((productCode) => ({
            productCode,
            owned: productCode === "SKU-0000",
            ownSalesCents: 1,
          })),
        },
      } as never;
    },
  };
  const codes = Array.from({ length: 1_001 }, (_, index) => `SKU-${String(index).padStart(4, "0")}`);
  const result = await readMarketSalesMetrics(
    principal,
    reader,
    codes,
    { startDate: "2020-01-01", endDate: "2024-12-31" },
    "sales:42",
  );
  assert.equal(calls.length, 6);
  assert.ok(calls.every((call) => call.principal === principal));
  assert.ok(calls.every((call) => call.productCodes.length <= 1_000));
  assert.deepEqual(
    [...new Set(calls.map((call) => `${call.startDate}/${call.endDate}`))],
    ["2020-01-01/2021-12-31", "2021-12-31/2023-12-31", "2023-12-31/2025-01-01"],
  );
  assert.equal(result.revision, "sales:42");
  assert.deepEqual(result.metrics.get("SKU-0000"), { owned: true, ownSalesCents: 3 });
  assert.deepEqual(result.metrics.get("SKU-1000"), { owned: false, ownSalesCents: 3 });
});

test("market sales consumer fails closed on revision drift and duplicate rows", async () => {
  let calls = 0;
  const driftingReader: SalesConsumerReader = {
    async read(_principal, request) {
      assert.equal(request.operation, "market_product_metrics");
      if (request.operation !== "market_product_metrics") throw new Error("unexpected operation");
      calls += 1;
      return {
        revision: calls === 1 ? "sales:1" : "sales:2",
        data: { rows: request.productCodes.map((productCode) => ({ productCode, owned: false, ownSalesCents: 0 })) },
      } as never;
    },
  };
  await assert.rejects(
    readMarketSalesMetrics(
      principal,
      driftingReader,
      Array.from({ length: 1_001 }, (_, index) => `SKU-${index}`),
      {},
    ),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );

  const duplicateReader: SalesConsumerReader = {
    async read(_principal, request) {
      assert.equal(request.operation, "market_product_metrics");
      return {
        revision: "sales:1",
        data: {
          rows: [
            { productCode: "SKU-A", owned: true, ownSalesCents: 10 },
            { productCode: "SKU-A", owned: true, ownSalesCents: 10 },
          ],
        },
      } as never;
    },
  };
  await assert.rejects(
    readMarketSalesMetrics(principal, duplicateReader, ["SKU-A", "SKU-B"], {}),
    (error: unknown) => error instanceof PublicApiError && error.status === 503,
  );
});

test("netshop and market runtime paths have no D1 sales fallback", async () => {
  const sources = await Promise.all([
    "../lib/netshop/database.ts",
    "../app/api/netshop/products/route.ts",
    "../lib/market/database.ts",
    "../lib/market/overview-sql.ts",
    "../app/api/market/overview/route.ts",
    "../app/api/market/trend/route.ts",
    "../app/api/market/ai/route.ts",
  ].map((path) => readFile(new URL(path, import.meta.url), "utf8")));
  for (const source of sources) {
    assert.doesNotMatch(source, /sales_order_lines|ensureSalesSchema|getSalesDatabase/);
  }
  const monthlyCache = await readFile(new URL("../lib/market/monthly-summary-cache.ts", import.meta.url), "utf8");
  assert.doesNotMatch(monthlyCache, /ON sales_order_lines|AFTER (?:INSERT|UPDATE|DELETE)[\s\S]{0,100}sales_order_lines/);
  assert.match(monthlyCache, /DROP TRIGGER IF EXISTS market_monthly_summary_sales_insert/);
  const responseCache = await readFile(new URL("../lib/market/overview-response-cache.ts", import.meta.url), "utf8");
  assert.match(responseCache, /django-sales:\$\{normalizedSalesRevision\(salesRevision\)\}/);
});

test("market netshop projection confirms the durable activation when D1 omits batch change metadata", async () => {
  const projection = await readFile(new URL("../lib/market/netshop-projection.ts", import.meta.url), "utf8");
  assert.match(
    projection,
    /if \(Number\(activation\[0\]\?\.meta\?\.changes \?\? 0\) !== 1\) \{[\s\S]*SELECT active_revision,active_total[\s\S]*activated\?\.active_revision !== revision[\s\S]*Number\(activated\.active_total\) !== total/,
  );
});
