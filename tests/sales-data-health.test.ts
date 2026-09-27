import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import type { AppPrincipal } from "../lib/auth/authorization";
import { getSalesDataHealth } from "../lib/sales/data-health";
import { PublicApiError } from "../lib/http/api-error";

const root = fileURLToPath(new URL("..", import.meta.url));
const principal: AppPrincipal = {
  email: "operator@example.com",
  displayName: "运营员",
  role: "operator",
  scope: null,
};

test("sales data health reports Django single-write coverage without changing a UI template", async () => {
  const requests: unknown[] = [];
  const health = await getSalesDataHealth(principal, {
    now: new Date("2026-08-30T03:00:00.000Z"),
    read: async (actor, request) => {
      assert.equal(actor, principal);
      requests.push(request);
      return {
        revision: "8:5",
        data: {
          dataStartDate: "2025-01-01",
          dataCutoffDate: "2026-08-29",
          latestBatch: {
            id: "batch-88",
            fileName: "sales-202608.xlsx",
            completedAt: "2026-08-30T01:02:03+08:00",
            rowCount: 572_015,
          },
        },
      };
    },
  });
  assert.deepEqual(requests, [{ operation: "freshness" }]);
  assert.equal(health.status, "available");
  assert.equal(health.source, "django_postgresql");
  assert.equal(health.authority, "sales_single_write");
  assert.equal(health.revision, "8:5");
  assert.equal(health.timeZone, "Asia/Shanghai");
  assert.equal(health.currentBusinessDate, "2026-08-30");
  assert.equal(health.expectedThroughDate, "2026-08-29");
  assert.deepEqual(health.coverage, {
    startDate: "2025-01-01",
    cutoffDate: "2026-08-29",
    coveredDayCount: 606,
    lagDaysToCurrentBusinessDate: 1,
    throughYesterday: true,
  });
  assert.equal(health.latestBatch?.rowCount, 572_015);
});

test("sales data health represents an empty authority without inventing freshness", async () => {
  const health = await getSalesDataHealth(principal, {
    now: new Date("2026-08-30T15:59:59.000Z"),
    read: async () => ({
      revision: "1:1",
      data: { dataStartDate: null, dataCutoffDate: null, latestBatch: null },
    }),
  });
  assert.equal(health.currentBusinessDate, "2026-08-30");
  assert.equal(health.status, "empty");
  assert.deepEqual(health.coverage, {
    startDate: null,
    cutoffDate: null,
    coveredDayCount: 0,
    lagDaysToCurrentBusinessDate: null,
    throughYesterday: false,
  });
  assert.equal(health.latestBatch, null);
});

test("sales data health fails closed on malformed, future, partial, or unrevisioned evidence", async () => {
  const fixtures = [
    { revision: "", data: { dataStartDate: null, dataCutoffDate: null, latestBatch: null } },
    { revision: "8:5", data: { dataStartDate: "2026-08-01", dataCutoffDate: null, latestBatch: null } },
    { revision: "8:5", data: { dataStartDate: "2026-08-01", dataCutoffDate: "2026-08-31", latestBatch: null } },
    { revision: "8:5", data: { dataStartDate: "2026-08-01", dataCutoffDate: "2026-08-29", latestBatch: null, extra: true } },
    { revision: "8:5", data: { dataStartDate: "2026-08-01", dataCutoffDate: "2026-08-29", latestBatch: {
      id: "batch", fileName: "bad\nname.xlsx", completedAt: null, rowCount: 1,
    } } },
  ];
  for (const fixture of fixtures) {
    await assert.rejects(
      getSalesDataHealth(principal, {
        now: new Date("2026-08-30T03:00:00.000Z"),
        read: async () => fixture as never,
      }),
      (error: unknown) => error instanceof PublicApiError
        && error.status === 503
        && error.code === "service_unavailable",
    );
  }
});

test("sales data-health route is operator-only, unscoped, no-store, and safely redacted", async () => {
  const route = await readFile(path.join(root, "app", "api", "sales", "data-health", "route.ts"), "utf8");
  const page = await readFile(path.join(root, "app", "page.tsx"), "utf8");
  assert.match(route, /requireAppPrincipal\(\["operator", "admin"\]\)/);
  assert.match(route, /requireUnrestrictedDataScope\(principal, "销售数据健康状态"\)/);
  assert.match(route, /getSalesDataHealth\(principal\)/);
  assert.match(route, /cache-control": "no-store"/);
  assert.match(route, /authorizationErrorResponse/);
  assert.match(route, /safeApiErrorResponse/);
  assert.doesNotMatch(route, /DATABASE_URL|DINGTALK|monitoring\\|postgres-daily/);
  assert.doesNotMatch(page, /api\/sales\/data-health/);
});
