import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { safeApiErrorResponse } from "../lib/http/api-error";

const sensitiveRoutePaths = [
  "../app/api/market/import/route.ts",
  "../app/api/market/ai/route.ts",
  "../app/api/market/annotations/route.ts",
  "../app/api/market/daily-coverage/route.ts",
  "../app/api/market/images/cache/route.ts",
  "../app/api/market/images/repair/route.ts",
  "../app/api/market/master/route.ts",
  "../app/api/market/master/execute/route.ts",
  "../app/api/market/trend/route.ts",
  "../app/api/settings/route.ts",
  "../app/api/settings/warehouse-mappings/route.ts",
  "../app/api/jackyun/session/open/route.ts",
] as const;

test("market, settings, and Jackyun routes mask unknown failures and disable caching", async () => {
  for (const path of sensitiveRoutePaths) {
    const source = await readFile(new URL(path, import.meta.url), "utf8");
    assert.match(source, /safeApiErrorResponse\(error,/u, path);
    assert.match(source, /cache-control/u, path);
    assert.doesNotMatch(source, /error instanceof Error \? error\.message/u, path);
  }

  const response = safeApiErrorResponse(
    new Error("SQLITE: no such table secret_internal_table at D:\\private\\worker.db"),
    "操作失败",
  );
  assert.equal(response.status, 500);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(await response.json(), { error: "操作失败", code: "internal_error" });
});

test("global market writes and operating settings reject restricted principals", async () => {
  const scopeProtectedRoutes = [
    "../lib/ai/django-route.ts",
    "../app/api/market/import/route.ts",
    "../app/api/market/annotations/route.ts",
    "../app/api/market/images/[hash]/route.ts",
    "../app/api/market/daily-coverage/route.ts",
    "../app/api/market/images/cache/route.ts",
    "../app/api/market/images/repair/route.ts",
    "../app/api/market/master/route.ts",
    "../app/api/market/master/execute/route.ts",
    "../app/api/settings/route.ts",
    "../app/api/settings/warehouse-mappings/route.ts",
    "../app/api/jackyun/session/open/route.ts",
  ] as const;
  for (const path of scopeProtectedRoutes) {
    const source = await readFile(new URL(path, import.meta.url), "utf8");
    assert.match(source, /requireUnrestrictedDataScope\(principal,/u, path);
  }
  const settings = await readFile(new URL("../app/api/settings/route.ts", import.meta.url), "utf8");
  assert.match(settings, /requireUnrestrictedDataScope\(principal, "系统设置", "修改"\)/u);
});
