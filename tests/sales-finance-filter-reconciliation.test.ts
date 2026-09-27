import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  decideFinanceDimensionReconciliation,
  financeAnalysisPayloadForRequest,
  financeDimensionOptionsToSalesOptions,
  MAX_SALES_SHARED_FILTER_SELECTIONS,
  parseFinanceDimensionFilterIssues,
  reconcileFinanceDimensionFilters,
  salesOutletIdentityKey,
  salesOutletKeyToFinanceKey,
} from "../app/sales-filter-bar";
import {
  nextSearchableMultiSelection,
  searchableMultiSelectAllValues,
} from "../app/ui/searchable-select";
import { FinanceDimensionFilterError, resolveFinanceDimensionFilters } from "../lib/finance/analysis";

const jdShop = salesOutletIdentityKey("京东", "旗舰店");
const tmallShop = salesOutletIdentityKey("天猫", "专卖店");

test("财报结构化筛选错误只接受有界平台与复合店铺字段", () => {
  assert.deepEqual(parseFinanceDimensionFilterIssues({
    invalidPlatforms: ["天猫"],
    invalidShops: [{ platform: "天猫", name: "专卖店" }],
    incompatibleShops: [],
  }), {
    invalidPlatforms: ["天猫"],
    invalidShops: [{ platform: "天猫", name: "专卖店" }],
    incompatibleShops: [],
  });
  assert.equal(parseFinanceDimensionFilterIssues({ invalidPlatforms: ["天猫"] }), null);
  assert.equal(parseFinanceDimensionFilterIssues({
    invalidPlatforms: [],
    invalidShops: [{ platform: "天猫", name: "" }],
    incompatibleShops: [],
  }), null);
});

test("财报候选使用 platform+shop 转换为销售公共筛选身份", () => {
  const options = financeDimensionOptionsToSalesOptions({
    platforms: ["京东"],
    shops: [{ key: JSON.stringify(["京东", "旗舰店"]), platform: "京东", name: "旗舰店" }],
  });
  assert.deepEqual(options, {
    platforms: ["京东"],
    shops: [{ key: jdShop, platform: "京东", name: "旗舰店" }],
    categories: [],
  });
  assert.equal(salesOutletKeyToFinanceKey(jdShop), JSON.stringify(["京东", "旗舰店"]));
  assert.equal(salesOutletKeyToFinanceKey("损坏的直链店铺键"), null);
});

test("回退财务期间只移除不适用的平台和店铺并保留有效复合身份", () => {
  const result = reconcileFinanceDimensionFilters(
    ["天猫", "京东"],
    [tmallShop, jdShop],
    { invalidPlatforms: ["天猫"], invalidShops: [{ platform: "天猫", name: "专卖店" }], incompatibleShops: [] },
  );
  assert.deepEqual(result, {
    platforms: ["京东"],
    outletKeys: [jdShop],
    removedPlatforms: ["天猫"],
    removedShops: ["天猫 · 专卖店"],
    changed: true,
    canReconcile: true,
  });
});

test("有效财报筛选不改变，平台与店铺不兼容时只移除店铺", () => {
  const noIssues = { invalidPlatforms: [], invalidShops: [], incompatibleShops: [] };
  assert.deepEqual(reconcileFinanceDimensionFilters(["京东"], [jdShop], noIssues), {
    platforms: ["京东"],
    outletKeys: [jdShop],
    removedPlatforms: [],
    removedShops: [],
    changed: false,
    canReconcile: true,
  });
  const incompatible = reconcileFinanceDimensionFilters(["京东"], [tmallShop], {
    invalidPlatforms: [],
    invalidShops: [],
    incompatibleShops: [{ platform: "天猫", name: "专卖店" }],
  });
  assert.deepEqual(incompatible.platforms, ["京东"]);
  assert.deepEqual(incompatible.outletKeys, []);
  assert.deepEqual(incompatible.removedShops, ["天猫 · 专卖店"]);
  assert.equal(incompatible.canReconcile, true);
});

test("只删除服务端明确列出的项，未出现在候选页中的第501个有效店铺仍保留", () => {
  const page501 = salesOutletIdentityKey("京东", "第501店");
  const result = reconcileFinanceDimensionFilters(["京东"], [jdShop, page501], {
    invalidPlatforms: [],
    invalidShops: [{ platform: "京东", name: "旗舰店" }],
    incompatibleShops: [],
  });
  assert.deepEqual(result.outletKeys, [page501]);
  assert.deepEqual(result.removedShops, ["京东 · 旗舰店"]);
  assert.equal(result.canReconcile, true);
});

test("全部筛选无效时必须等待显式确认，部分有效时才可自动协调", () => {
  const allInvalid = reconcileFinanceDimensionFilters(["天猫"], [tmallShop], {
    invalidPlatforms: ["天猫"],
    invalidShops: [{ platform: "天猫", name: "专卖店" }],
    incompatibleShops: [],
  });
  assert.equal(decideFinanceDimensionReconciliation(allInvalid), "require_confirmation");

  const partiallyValid = reconcileFinanceDimensionFilters(["天猫", "京东"], [tmallShop, jdShop], {
    invalidPlatforms: ["天猫"],
    invalidShops: [{ platform: "天猫", name: "专卖店" }],
    incompatibleShops: [],
  });
  assert.equal(decideFinanceDimensionReconciliation(partiallyValid), "auto_apply");
  assert.equal(decideFinanceDimensionReconciliation({
    ...partiallyValid,
    canReconcile: false,
  }), "reject");
});

test("旧财报结果只在请求签名完全一致时可见", () => {
  const unfilteredResult = {
    requestSignature: "unfiltered",
    payload: { current: 100 },
  };
  assert.deepEqual(financeAnalysisPayloadForRequest(unfilteredResult, "unfiltered"), { current: 100 });
  assert.equal(financeAnalysisPayloadForRequest(unfilteredResult, "tmall-filter"), null);
  assert.equal(financeAnalysisPayloadForRequest(null, "tmall-filter"), null);
});

test("财报多选严格限制50项且候选超限时禁用显式全选", () => {
  const selected = Array.from({ length: MAX_SALES_SHARED_FILTER_SELECTIONS }, (_, index) => `店铺${index + 1}`);
  assert.deepEqual(nextSearchableMultiSelection(selected, "店铺51", MAX_SALES_SHARED_FILTER_SELECTIONS), selected);
  assert.deepEqual(nextSearchableMultiSelection(selected, "店铺1", MAX_SALES_SHARED_FILTER_SELECTIONS), selected.slice(1));

  const options = Array.from({ length: 501 }, (_, index) => ({ value: `店铺${index + 1}`, label: `店铺${index + 1}` }));
  assert.equal(searchableMultiSelectAllValues(options, MAX_SALES_SHARED_FILTER_SELECTIONS), null);
  assert.deepEqual(searchableMultiSelectAllValues(options.slice(0, 2), MAX_SALES_SHARED_FILTER_SELECTIONS), ["店铺1", "店铺2"]);
});

test("服务端继续以400失败关闭并精确返回无效与跨平台店铺", () => {
  const jdFinanceKey = JSON.stringify(["京东", "旗舰店"]);
  const tmallFinanceKey = JSON.stringify(["天猫", "专卖店"]);
  assert.throws(
    () => resolveFinanceDimensionFilters(
      [{ key: jdFinanceKey, platform: "京东", name: "旗舰店" }],
      ["京东"],
      [tmallFinanceKey, JSON.stringify(["京东", "不存在店"])],
    ),
    (error: unknown) => {
      assert.ok(error instanceof FinanceDimensionFilterError);
      assert.equal(error.status, 400);
      assert.equal(error.code, "invalid_request");
      assert.deepEqual(error.invalidPlatforms, []);
      assert.deepEqual(error.invalidShops.map(({ platform, name }) => ({ platform, name })), [
        { platform: "天猫", name: "专卖店" },
        { platform: "京东", name: "不存在店" },
      ]);
      assert.deepEqual(error.incompatibleShops.map(({ platform, name }) => ({ platform, name })), [
        { platform: "天猫", name: "专卖店" },
      ]);
      return true;
    },
  );
});

test("财报前端按结构化400只清理无效项、同步URL并显示调整提示", async () => {
  const [source, route] = await Promise.all([
    readFile(new URL("../app/sales-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/finance/analysis/route.ts", import.meta.url), "utf8"),
  ]);
  assert.match(source, /payload\?\.code === "finance_dimension_filter_out_of_scope"/);
  assert.match(source, /const issues = parseFinanceDimensionFilterIssues\(payload\)/);
  assert.match(source, /reconcileFinanceDimensionFilters\([\s\S]*?selectedPlatforms,[\s\S]*?validSelectedShopKeys,[\s\S]*?issues/);
  assert.match(source, /decision === "require_confirmation"[\s\S]*?setPendingDimensionChange/);
  assert.match(source, /清除筛选并查看全部财报/);
  assert.match(source, /onDimensionFiltersChange\(reconciliation\.platforms, reconciliation\.outletKeys\)/);
  assert.match(source, /financeFilterOptions \?\? salesFilterOptions/);
  assert.match(source, /financeAnalysisPayloadForRequest\(dataResult, requestSignature\)/);
  assert.match(source, /setDataResult\(\{ requestSignature, payload \}\)/);
  const filterBar = await readFile(new URL("../app/sales-filter-bar.tsx", import.meta.url), "utf8");
  assert.match(filterBar, /maxSelectionsPerDimension = MAX_SALES_SHARED_FILTER_SELECTIONS/);
  assert.match(source, /已调整财报筛选/);
  assert.match(source, /onFilterOptionsChange\(financeDimensionOptionsToSalesOptions\(payload\.filters\)\)/);
  assert.match(route, /upstreamCode === "finance_dimension_filter_out_of_scope"/);
  assert.match(route, /Response\.json\(error\.payload, \{ status: error\.status/);
});

test("初始财务月份回退保持显式且手动选择后恢复严格读取", async () => {
  const [source, route] = await Promise.all([
    readFile(new URL("../app/sales-module-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/finance/analysis/route.ts", import.meta.url), "utf8"),
  ]);
  assert.match(route, /getAll\("initialMonthFallback"\)/);
  assert.match(route, /fallbackValues\[0\] !== "latest_completed"/);
  assert.match(source, /allowInitialMonthFallback && selectedMonths !== null && selectedMonths\.length > 0/);
  assert.match(source, /query\.set\("initialMonthFallback", "latest_completed"\)/);
  assert.match(source, /const selectMonthsStrictly[\s\S]*?setAllowInitialMonthFallback\(false\);[\s\S]*?setSelectedMonths\(months\)/);
  assert.equal((source.match(/onChange=\{selectMonthsStrictly\}/g) ?? []).length, 2);
  assert.match(source, /const resetMonthsStrictly[\s\S]*?setAllowInitialMonthFallback\(false\);[\s\S]*?setSelectedMonths\(globalMonths\)/);
  assert.match(source, /已显示最新可用财报[\s\S]*?手动选择月份后将严格按选择读取/);
});
