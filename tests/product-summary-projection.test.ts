import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import type {
  ProductSummaryFullResponse,
  ProductSummaryPageResponse,
  ProductsSummaryReader,
} from "../lib/products/summary";
import { getProductSummary } from "../lib/products/summary";

const principal: AppPrincipal = {
  email: "product-reader@example.com",
  displayName: "Product reader",
  role: "viewer",
  scope: null,
};

const fullPayload: ProductSummaryFullResponse = {
  projection: "full",
  snapshotToken: "a".repeat(64),
  hasSales: false,
  range: "last30",
  sync: {
    salesThrough: null,
    salesWindowStart: null,
    requestedStartDate: null,
    requestedEndDate: null,
    dataStartDate: null,
    dataCutoffDate: null,
    inventoryAsOf: null,
    latestSalesFile: null,
  },
  filters: { platforms: [], shops: [], categories: [] },
  filtersApplied: { platforms: [], shops: [], query: "", categories: [], marginBands: [] },
  sort: { by: "netSalesCents", direction: "desc" },
  metrics: {
    skuCount: 0,
    grossSalesCents: 0,
    netSalesCents: 0,
    grossProfitCents: 0,
    grossMarginRate: null,
    lossSkuCount: 0,
    stockedSkuCount: 0,
    marginBuckets: {
      below35Count: 0,
      between35And40Count: 0,
      between40And45Count: 0,
      atLeast45Count: 0,
    },
  },
  pagination: { page: 1, pageSize: 25, total: 0, returned: 0, totalPages: 0, truncated: false },
  items: [],
};

test("商品汇总只向独立 Django products reader 传递有界查询", async () => {
  const calls: Array<{ principal: AppPrincipal; rawQuery: string }> = [];
  const reader: ProductsSummaryReader = {
    async requestJson(receivedPrincipal, input) {
      calls.push({ principal: receivedPrincipal, rawQuery: input.rawQuery });
      return { data: fullPayload as never };
    },
  };
  const result = await getProductSummary(principal, {
    range: "custom",
    startDate: "2026-08-01",
    endDate: "2026-08-31",
    platforms: ["京东"],
    shopKeys: ["京东\u001f测试店铺"],
    categories: ["类目 A"],
    marginBands: ["atLeast45"],
    query: "SKU A",
    page: 2,
    pageSize: 25,
    sortBy: "grossProfitCents",
    direction: "asc",
  }, reader);
  assert.equal(result, fullPayload);
  assert.equal(calls[0]?.principal, principal);
  assert.equal(calls.length, 1);
  const query = new URLSearchParams(calls[0]?.rawQuery);
  assert.equal(query.get("range"), "custom");
  assert.equal(query.get("startDate"), "2026-08-01");
  assert.equal(query.get("endDate"), "2026-08-31");
  assert.deepEqual(query.getAll("platform"), ["京东"]);
  assert.deepEqual(query.getAll("shop"), ["京东\u001f测试店铺"]);
  assert.equal(query.get("view"), null);
});

test("商品汇总 page 投影绑定同一 snapshot token", async () => {
  const pagePayload: ProductSummaryPageResponse = {
    projection: "page",
    snapshotToken: fullPayload.snapshotToken,
    sort: { by: "netSalesCents", direction: "desc" },
    pagination: { page: 7, pageSize: 25, total: 0, returned: 0, totalPages: 0, truncated: false },
    items: [],
  };
  let rawQuery = "";
  const reader: ProductsSummaryReader = {
    async requestJson(_principal, input) {
      rawQuery = input.rawQuery;
      return { data: pagePayload as never };
    },
  };
  const result = await getProductSummary(principal, {
    projection: "page",
    expectedSnapshotToken: fullPayload.snapshotToken,
    page: 7,
    pageSize: 25,
  }, reader);
  assert.equal(result, pagePayload);
  const query = new URLSearchParams(rawQuery);
  assert.equal(query.get("view"), "page");
  assert.equal(query.get("snapshotToken"), fullPayload.snapshotToken);
});

test("商品前端只在同一 bootstrap 和 snapshot 下请求 page 投影，版本漂移有界回到 full", async () => {
  const product = await readFile(new URL("../app/product-module-view.tsx", import.meta.url), "utf8");
  assert.match(product, /productSummaryBootstrapKeyRef/);
  assert.match(product, /productSummarySnapshotTokenRef/);
  assert.match(product, /productSummaryRestartedTokensRef = useRef\(new Set<string>\(\)\)/);
  assert.match(product, /const expectedSnapshotToken = productSummarySnapshotTokenRef\.current/);
  assert.match(product, /params\.set\("view", "page"\)[\s\S]+params\.set\("snapshotToken", expectedSnapshotToken\)/);
  assert.match(product, /response\.status === 503 \|\| pageSnapshotMismatch/);
  assert.match(product, /productSummarySnapshotTokenRef\.current = payload\.snapshotToken/);
});
