import assert from "node:assert/strict";
import test from "node:test";
import { previousYearPeriod, rangeForShellPeriod, salesRangeMap, shellPeriodForRange, skuSalesPeriod } from "../app/module-view-shared";
import { normalizeShellLocation, parseShellLocation, serializeShellLocation, updateModuleViewLocation } from "../app/shell/navigation-contract";

test("last 30 days includes Shanghai today and crosses month/year boundaries", (t) => {
  t.mock.timers.enable({ apis: ["Date"], now: new Date("2026-01-01T16:10:00Z") });
  assert.deepEqual(skuSalesPeriod("近30天", "2000-01-01", "2000-01-02"), {
    startDate: "2025-12-04", endDate: "2026-01-02",
  });
  assert.equal(salesRangeMap["近30天"], "last30");
  const state = shellPeriodForRange("近30天", "2026-01", "", "");
  assert.deepEqual(state, { kind: "last30" });
  assert.equal(rangeForShellPeriod(parseShellLocation(serializeShellLocation({ module: "sales", period: state })).period), "近30天");
});

test("previous year shifts the selected interval by calendar year and clamps leap day", () => {
  assert.deepEqual(previousYearPeriod({ startDate: "2026-09-01", endDate: "2026-09-12" }), {
    startDate: "2025-09-01", endDate: "2025-09-12",
  });
  assert.deepEqual(previousYearPeriod({ startDate: "2024-02-29", endDate: "2024-03-05" }), {
    startDate: "2023-02-28", endDate: "2023-03-05",
  });
  assert.deepEqual(previousYearPeriod({ startDate: "2025-12-20", endDate: "2026-01-05" }), {
    startDate: "2024-12-20", endDate: "2025-01-05",
  });
});

test("previous year dates survive reload and module/tab switches without another shift", () => {
  const dates = { startDate: "2025-09-01", endDate: "2025-09-12" };
  const period = shellPeriodForRange("去年同期", "2026-09", dates.startDate, dates.endDate);
  let url = serializeShellLocation({ module: "inventory", period }, "/?tenant=demo#table");
  url = updateModuleViewLocation(url, "sales", "channel");
  const restored = parseShellLocation(normalizeShellLocation(url));
  assert.deepEqual(restored.period, { kind: "previous_year", from: dates.startDate, to: dates.endDate });
  assert.equal(restored.view, "channel");
  assert.match(url, /tenant=demo/);
  assert.match(url, /#table$/);
  assert.deepEqual(skuSalesPeriod(rangeForShellPeriod(restored.period), dates.startDate, dates.endDate), dates);
  assert.equal(salesRangeMap["去年同期"], "custom");
});

test("invalid previous-year links cannot admit missing, duplicate, inverted or impossible dates", () => {
  for (const query of [
    "", "&from=2025-09-01", "&from=2025-02-29&to=2025-03-01",
    "&from=2025-09-12&to=2025-09-01", "&from=2025-09-01&from=2025-09-02&to=2025-09-12",
  ]) assert.deepEqual(parseShellLocation(`/?period=previous_year${query}`).period, { kind: "current_month" });
});
