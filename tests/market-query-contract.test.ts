import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { PublicApiError } from "../lib/http/api-error";
import { parseMarketOverviewQuery } from "../lib/market/query-contract";

function params(entries: Array<[string, string]>) {
  const result = new URLSearchParams();
  for (const [key, value] of entries) result.append(key, value);
  return result;
}

test("market overview rejects single, duplicate, impossible, and reversed dates before database access", () => {
  for (const query of [
    params([["startDate", "2026-08-01"]]),
    params([["endDate", "2026-08-01"]]),
    params([["startDate", "2026-08-01"], ["startDate", "2026-08-02"], ["endDate", "2026-08-03"]]),
    params([["startDate", "2026-02-30"], ["endDate", "2026-03-01"]]),
    params([["startDate", "2026-08-03"], ["endDate", "2026-08-01"]]),
  ]) {
    assert.throws(() => parseMarketOverviewQuery(query), PublicApiError);
  }
  assert.deepEqual(
    parseMarketOverviewQuery(params([["startDate", "2024-02-29"], ["endDate", "2024-03-01"]])).filters.startDate,
    "2024-02-29",
  );
});

test("market overview pagination is strict decimal and facet selection has a shared hard ceiling", () => {
  assert.deepEqual(parseMarketOverviewQuery(params([["view", "full"]])).pagination, { page: 1, pageSize: 50 });
  assert.equal(parseMarketOverviewQuery(new URLSearchParams("view=ranking&page=10001")).pagination.page, 10001);
  for (const value of ["0", "1.5", "1e2", "+1", " 1 ", "1000001"]) {
    assert.throws(
      () => parseMarketOverviewQuery(params([["view", "ranking"], ["page", value]])),
      PublicApiError,
    );
  }
  const maximum = new URLSearchParams("view=ranking&page=1&pageSize=50");
  for (let index = 0; index < 30; index += 1) maximum.append("category", `category-${index}`);
  for (let index = 0; index < 30; index += 1) maximum.append("scope", `scope-${index}`);
  for (let index = 0; index < 30; index += 1) maximum.append("brand", `brand-${index}`);
  for (let index = 0; index < 10; index += 1) maximum.append("dimension", `dimension-${index}`);
  assert.equal(parseMarketOverviewQuery(maximum).pagination.pageSize, 50);
  maximum.append("dimension", "dimension-over-budget");
  assert.throws(() => parseMarketOverviewQuery(maximum), /合计不能超过 100/);
});

test("market route validates before the Django gateway and retained migration SQL keeps bounded JSON bindings", async () => {
  const [route, database] = await Promise.all([
    readFile(new URL("../app/api/market/overview/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/database.ts", import.meta.url), "utf8"),
  ]);
  assert.ok(route.indexOf("parseMarketOverviewQuery(") < route.indexOf("const result = await requestDjangoMarketService"));
  assert.match(route, /service: "reader"/);
  assert.doesNotMatch(route, /ensureMarketSchema|getD1Database/);
  assert.match(route, /safeApiErrorResponse\(error,/);
  assert.match(database, /IN \(SELECT CAST\(value AS TEXT\) FROM json_each\(\?\)\)/);
  assert.doesNotMatch(database, /targetClauses\.push\(`\$\{column\} IN \(\$\{normalized\.map/);
});
