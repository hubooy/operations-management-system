import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

import type { AppPrincipal } from "../lib/auth/authorization";
import {
  readDjangoSalesConsumer,
  SALES_CONSUMER_QUERY_PATH,
  salesConsumerOperations,
  type SalesConsumerReaderConfig,
} from "../lib/django/sales-consumer-reader";
import { PublicApiError } from "../lib/http/api-error";

const secret = "test-only-django-sales-consumer-secret-minimum";
const revision = "sales:27/erp:9";
const principal: AppPrincipal = {
  email: "analyst@example.com",
  displayName: "分析员",
  role: "analyst",
  scope: {
    warehouses: ["主仓"],
    channels: ["直营网"],
    platforms: ["京东"],
  },
};
const config: SalesConsumerReaderConfig = {
  djangoBaseUrl: "http://127.0.0.1:8001",
  internalSecret: secret,
  timeoutMs: 1_000,
  maxRequestBytes: 64 * 1024,
  maxResponseBytes: 64 * 1024,
};

function jsonResponse(value: unknown, init: ResponseInit = {}): Response {
  const headers = new Headers(init.headers);
  if (!headers.has("content-type")) headers.set("content-type", "application/json; charset=utf-8");
  return new Response(JSON.stringify(value), { ...init, headers });
}

function assertUnavailable(error: unknown): boolean {
  return error instanceof PublicApiError
    && error.status === 503
    && error.code === "service_unavailable";
}

test("consumer reader exposes only the fixed non-SQL operation allowlist", () => {
  assert.deepEqual(salesConsumerOperations, [
    "analysis_records",
    "freshness",
    "summary",
    "inventory_demand",
    "inventory_inbound_windows",
    "product_performance",
    "customer_service_products",
    "netshop_product_metrics",
    "market_product_metrics",
    "order_search",
    "import_batch_search",
    "category_options",
  ]);
  assert.equal(SALES_CONSUMER_QUERY_PATH, "/api/sales/consumers/query");
});

test("consumer reader signs the exact POST body and returns the matching revision", async () => {
  const request = {
    operation: "product_performance" as const,
    startDate: "2026-08-01",
    endDate: "2026-08-28",
    productCodes: ["SKU-1"],
    outlets: [{ platform: "京东", shopName: "旗舰店", channel: "直营网" }],
  };
  let capturedUrl = "";
  let capturedInit: RequestInit | undefined;
  const result = await readDjangoSalesConsumer(principal, request, {
    config,
    now: () => 1_788_000_000_000,
    requestId: () => "consumer-request-1",
    fetchImpl: async (input, init) => {
      capturedUrl = String(input);
      capturedInit = init;
      return jsonResponse({ operation: request.operation, data: {
        dataStartDate: "2026-08-01",
        dataCutoffDate: "2026-08-28",
        latestBatch: null,
        rows: [],
        outletOptions: [],
        truncated: false,
      } }, { headers: {
        "x-sales-data-revision": revision,
        "x-sales-source-revision": revision,
      } });
    },
  });

  assert.equal(capturedUrl, `http://127.0.0.1:8001${SALES_CONSUMER_QUERY_PATH}`);
  assert.equal(capturedInit?.method, "POST");
  assert.equal(capturedInit?.redirect, "manual");
  assert.equal(capturedInit?.cache, "no-store");
  const body = Buffer.from(capturedInit?.body as Uint8Array).toString("utf8");
  assert.equal(body, JSON.stringify(request));
  const headers = new Headers(capturedInit?.headers);
  assert.deepEqual([...headers.keys()].sort(), [
    "accept",
    "content-type",
    "x-teruisi-content-sha256",
    "x-teruisi-principal",
    "x-teruisi-request-id",
    "x-teruisi-signature",
    "x-teruisi-timestamp",
  ]);
  const encodedPrincipal = headers.get("x-teruisi-principal");
  const bodySha256 = headers.get("x-teruisi-content-sha256");
  assert.ok(encodedPrincipal);
  assert.ok(bodySha256);
  const canonical = [
    "v1",
    "1788000000",
    "consumer-request-1",
    "POST",
    SALES_CONSUMER_QUERY_PATH,
    "",
    bodySha256,
    encodedPrincipal,
  ].join("\n");
  assert.equal(
    headers.get("x-teruisi-signature"),
    `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`,
  );
  assert.equal(result.revision, revision);
  assert.deepEqual(result.data.rows, []);
});

test("consumer reader permits HTTP only for exact loopback hosts", async () => {
  for (const djangoBaseUrl of [
    "http://sales.example.com",
    "http://127.0.0.1.evil.example.com",
    "http://localhost.evil.example.com",
    "https://user:password@sales.example.com",
    "https://sales.example.com/subpath",
  ]) {
    let fetched = false;
    await assert.rejects(
      readDjangoSalesConsumer(principal, { operation: "freshness" }, {
        config: { ...config, djangoBaseUrl },
        fetchImpl: async () => {
          fetched = true;
          return jsonResponse({});
        },
      }),
      assertUnavailable,
      djangoBaseUrl,
    );
    assert.equal(fetched, false, djangoBaseUrl);
  }
});

test("consumer reader rejects unknown request fields, unknown operations, and oversized bodies before fetch", async () => {
  const fixtures: Array<{ request: unknown; readerConfig: SalesConsumerReaderConfig }> = [
    { request: { operation: "freshness", sql: "SELECT * FROM sales_order_lines" }, readerConfig: config },
    { request: { operation: "raw_sql", query: "SELECT 1" }, readerConfig: config },
    {
      request: { operation: "order_search", query: "x".repeat(2_000), page: 1, pageSize: 10 },
      readerConfig: { ...config, maxRequestBytes: 128 },
    },
  ];
  for (const fixture of fixtures) {
    let fetched = false;
    await assert.rejects(
      readDjangoSalesConsumer(
        principal,
        fixture.request as Parameters<typeof readDjangoSalesConsumer>[1],
        {
          config: fixture.readerConfig,
          fetchImpl: async () => {
            fetched = true;
            return jsonResponse({});
          },
        },
      ),
      assertUnavailable,
    );
    assert.equal(fetched, false);
  }
});

test("consumer reader fails closed on status, content type, revision, envelope, redirect, and size errors", async () => {
  const validHeaders = {
    "x-sales-data-revision": revision,
    "x-sales-source-revision": revision,
  };
  const fetchers: Array<{ label: string; fetchImpl: typeof fetch }> = [
    { label: "status", fetchImpl: async () => jsonResponse({ error: "no" }, { status: 500 }) },
    {
      label: "content type",
      fetchImpl: async () => new Response("ok", { status: 200, headers: { ...validHeaders, "content-type": "text/plain" } }),
    },
    { label: "missing revision", fetchImpl: async () => jsonResponse({ operation: "freshness", data: {} }) },
    {
      label: "mismatched revision",
      fetchImpl: async () => jsonResponse({ operation: "freshness", data: {} }, { headers: {
        "x-sales-data-revision": revision,
        "x-sales-source-revision": "stale",
      } }),
    },
    {
      label: "wrong operation",
      fetchImpl: async () => jsonResponse({ operation: "category_options", data: {} }, { headers: validHeaders }),
    },
    {
      label: "redirect",
      fetchImpl: async () => jsonResponse({}, { status: 302, headers: { location: "https://example.com" } }),
    },
    {
      label: "oversized",
      fetchImpl: async () => jsonResponse({ operation: "freshness", data: { padding: "x".repeat(2_000) } }, {
        headers: validHeaders,
      }),
    },
  ];
  for (const fixture of fetchers) {
    await assert.rejects(
      readDjangoSalesConsumer(principal, { operation: "freshness" }, {
        config: { ...config, maxResponseBytes: fixture.label === "oversized" ? 128 : config.maxResponseBytes },
        fetchImpl: fixture.fetchImpl,
      }),
      assertUnavailable,
      fixture.label,
    );
  }
});

test("consumer reader fails closed on network errors and timeout", async () => {
  const fetchers: typeof fetch[] = [
    async () => { throw new Error("connection refused"); },
    async (_input, init) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener(
        "abort",
        () => reject(new DOMException("aborted", "AbortError")),
        { once: true },
      );
    }),
  ];
  for (const fetchImpl of fetchers) {
    await assert.rejects(
      readDjangoSalesConsumer(principal, { operation: "freshness" }, {
        config: { ...config, timeoutMs: 5 },
        fetchImpl,
      }),
      assertUnavailable,
    );
  }
});

test("generic D1 consumers no longer depend on the sales database module", async () => {
  const genericFiles = [
    "../lib/auth/authorization.ts",
    "../lib/settings/service.ts",
    "../lib/erp-reference/database.ts",
    "../lib/inventory/database.ts",
    "../lib/search/ai-tool.ts",
    "./legacy/ai/artifacts.ts",
    "./legacy/ai/assistant-service.ts",
    "./legacy/ai/conversation-management.ts",
    "./legacy/ai/conversation-scope.ts",
    "./legacy/ai/data-knowledge.ts",
    "./legacy/ai/question-workflow.ts",
    "./legacy/ai/tool-audit.ts",
    "../lib/ai/tool-registry.ts",
    "../lib/workflow/collaboration.ts",
    "../lib/workflow/operations-records.ts",
    "../lib/workflow/tasks.ts",
    "../app/api/search/route.ts",
    "../app/api/customer-service/analyze/route.ts",
  ];
  for (const relativePath of genericFiles) {
    const source = await readFile(new URL(relativePath, import.meta.url), "utf8");
    assert.doesNotMatch(source, /lib\/sales\/database|getSalesDatabase|SalesDatabase/, relativePath);
  }
});
