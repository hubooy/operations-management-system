import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  assertJdMarketNativeDownloadRequest,
  isJdMarketRankingPageUrl,
  jdMarketDropdownClickMode,
  jdMarketHelperRequestError,
  validateJdMarketDailyConfig,
  withSingleJdMarketFilterSelectionRetry,
} from "../tools/jd-market-ranking-daily";
import { parseJdSilentNoWindowHeader } from "../tools/tmall-sycm-cookie-pipeline";

test("JD silent-window header is strict and shared by multi-store and market plans", () => {
  assert.equal(parseJdSilentNoWindowHeader(undefined), false);
  assert.equal(parseJdSilentNoWindowHeader("0"), false);
  assert.equal(parseJdSilentNoWindowHeader("1"), true);
  assert.throws(() => parseJdSilentNoWindowHeader("true"), /请求头无效/);
  assert.throws(() => parseJdSilentNoWindowHeader(["1"]), /请求头无效/);
});

test("JD market helper binds one execution and rejects foreign or out-of-order requests", () => {
  assert.equal(jdMarketHelperRequestError("ready", false, "/jd-market/plan", "execution-1", null), null);
  assert.deepEqual(jdMarketHelperRequestError("ready", false, "/jd-market/run", "execution-1", null), { error: "execution_not_claimed", expected: "/jd-market/plan" });
  assert.deepEqual(jdMarketHelperRequestError("planned", false, "/jd-market/run", "other", "execution-1"), { error: "execution_mismatch" });
  assert.deepEqual(jdMarketHelperRequestError("planned", true, "/jd-market/run", "execution-1", "execution-1"), { error: "pipeline_busy" });
  assert.deepEqual(jdMarketHelperRequestError("planned", false, "/jd-market/verify", "execution-1", "execution-1"), { error: "invalid_stage", expected: "executed|completed", actual: "planned" });
  assert.equal(jdMarketHelperRequestError("executed", false, "/jd-market/run", "execution-1", "execution-1"), null);
  assert.equal(jdMarketHelperRequestError("completed", false, "/jd-market/verify", "execution-1", "execution-1"), null);
});

test("JD promotion and market helper plan retries preserve persisted stages", async () => {
  const helper = await readFile(new URL("../tools/tmall-sycm-cookie-pipeline.ts", import.meta.url), "utf8");
  assert.match(helper, /jdPromotionPlan = await planJdPromotionN8nRun[\s\S]*?stage = jdPromotionPlan\.stage/);
  assert.match(helper, /jdMarketPlan = await planJdMarketDailyRun[\s\S]*?stage = jdMarketPlan\.stage/);
});

test("JD market filter selection retries one ignored click and fails closed after the second miss", async () => {
  let selections = 0;
  let verifications = 0;
  const recovered = await withSingleJdMarketFilterSelectionRetry(
    async () => { selections += 1; },
    async () => {
      verifications += 1;
      if (verifications === 1) throw new Error("SKU 未读回");
    },
  );
  assert.deepEqual(recovered, { retried: true });
  assert.equal(selections, 2);
  await assert.rejects(
    withSingleJdMarketFilterSelectionRetry(async () => undefined, async () => { throw new Error("仍为 SPU"); }),
    /连续两次未精确生效.*仍为 SPU/,
  );
});

test("JD market category control bypasses only known JD overlays", () => {
  assert.equal(jdMarketDropdownClickMode({ hitInsideControl: false, hitTagNames: ["AIHELPER-EXTENSION-EMBEDDED", "BODY"] }), "native_dispatch");
  assert.equal(jdMarketDropdownClickMode({ hitInsideControl: false, hitTagNames: ["DIV", "DIV"], hitClassNames: ["AiHelperOpenExtension-Popover__main", "AiHelperOpenExtension AiHelperOpenExtensionJM"] }), "native_dispatch");
  assert.equal(jdMarketDropdownClickMode({ hitInsideControl: false, hitTagNames: ["UL", "DIV"], hitClassNames: ["menu-list", "header-menu"] }), "native_dispatch");
  assert.equal(jdMarketDropdownClickMode({ hitInsideControl: true, hitTagNames: ["AIHELPER-EXTENSION-EMBEDDED"] }), "pointer");
  assert.equal(jdMarketDropdownClickMode({ hitInsideControl: false, hitTagNames: ["DIV"], hitClassNames: ["UnrelatedPopover"] }), "pointer");
  assert.equal(jdMarketDropdownClickMode({ hitInsideControl: false, hitTagNames: ["DIV", "UL"], hitClassNames: ["menu-list", "header-menu"] }), "pointer");
});

test("JD market accepts only the exact new industry-top page", () => {
  assert.equal(isJdMarketRankingPageUrl("https://jdsz.jd.com/szweb/view/industry/industry-top.html"), true);
  assert.equal(isJdMarketRankingPageUrl("https://jdsz.jd.com/szweb/view/industry/industry-top.html?legacy=1"), false);
  assert.equal(isJdMarketRankingPageUrl("https://jdsz.jd.com/szweb/view/industry/industry-product-rank-temp.html"), false);
  assert.equal(isJdMarketRankingPageUrl("https://example.com/szweb/view/industry/industry-top.html"), false);
});

test("JD market native XLSX download requires exact product, hot, SKU, category and day identity", () => {
  const target = { secondIndId: "44744", thirdIndId: "44811" };
  const payload = { skuSpuType: "sku", rankTab: "hot", startDate: "2026-09-14", endDate: "2026-09-14", saleOrdCate3: ["44811"], popBusiness: "pop", channel: "all", realtime: false, interval: "DAY", dateType: "custom" };
  const request = {
    contentType: "application/json;charset=UTF-8",
    method: "POST",
    postData: JSON.stringify(payload),
    url: "https://szgateway.jd.com/api/lowcode/industryTop/indProductRank/downloadProductRank.ajax",
    capturedAt: 1,
  } as const;
  assert.deepEqual(assertJdMarketNativeDownloadRequest(request, target, "2026-09-14").payload, payload);
  assert.doesNotThrow(() => assertJdMarketNativeDownloadRequest({
    ...request,
    postData: JSON.stringify({ ...payload, saleOrdCate2: ["44744"] }),
  }, target, "2026-09-14"));
  for (const mutation of [
    { rankTab: "flow" }, { skuSpuType: "spu" }, { startDate: "2026-09-13" },
    { endDate: "2026-09-13" }, { saleOrdCate3: ["44757"] }, { popBusiness: "self" },
    { saleOrdCate2: ["44739"] }, { saleOrdCate3: ["44811", "44757"] },
    { saleOrdCate3: [] }, { realtime: true }, { interval: "MONTH" }, { channel: "self" },
  ]) {
    assert.throws(() => assertJdMarketNativeDownloadRequest({ ...request, postData: JSON.stringify({ ...payload, ...mutation }) }, target, "2026-09-14"), /身份不一致|不是 POP/);
  }
  assert.throws(() => assertJdMarketNativeDownloadRequest({ ...request, method: "GET" }, target, "2026-09-14"), /地址或方法已变化/);
  assert.throws(() => assertJdMarketNativeDownloadRequest({ ...request, url: request.url.replace("szgateway.jd.com", "example.com") }, target, "2026-09-14"), /地址或方法已变化/);
});

test("JD market n8n workflow stays inactive and preserves the hidden Profile 3 three-stage chain", async () => {
  const workflow = JSON.parse(await readFile(new URL("../automation/n8n/jd-market-ranking-daily.workflow.json", import.meta.url), "utf8")) as {
    name: string;
    active: boolean;
    nodes: Array<{ type: string; parameters?: { url?: string; rule?: { interval?: Array<{ expression?: string }> }; headerParameters?: { parameters?: Array<{ name?: string; value?: string }> }; options?: { timeout?: number } } }>;
  };
  assert.equal(workflow.active, false);
  assert.match(workflow.name, /Profile 3隐藏Chromium/);
  const schedule = workflow.nodes.find((node) => node.type === "n8n-nodes-base.scheduleTrigger");
  assert.equal(schedule?.parameters?.rule?.interval?.[0]?.expression, "30 10 * * *");
  const requests = workflow.nodes.filter((node) => node.type === "n8n-nodes-base.httpRequest" && /^http:\/\/127\.0\.0\.1:5791\/jd-market\//.test(node.parameters?.url ?? ""));
  assert.deepEqual(requests.map((node) => node.parameters?.url), [
    "http://127.0.0.1:5791/jd-market/plan",
    "http://127.0.0.1:5791/jd-market/run",
    "http://127.0.0.1:5791/jd-market/verify",
  ]);
  assert.deepEqual(requests.map((node) => node.parameters?.options?.timeout), [900_000, 21_600_000, 900_000]);
});

test("JD market config fixes seven unique categories and one native XLSX day per file", async () => {
  const config = JSON.parse(await readFile(new URL("../config/jd-market-ranking-daily.json", import.meta.url), "utf8"));
  const validated = validateJdMarketDailyConfig(config);
  assert.equal(validated.version, 4);
  assert.equal(validated.silentNoWindow, true);
  assert.equal(validated.maxDaysPerFile, 1);
  assert.deepEqual(validated.categories.map((target) => [target.categoryPath.join(" > "), target.systemCategory, target.secondIndId, target.thirdIndId]), [
    ["商用净饮水设备 > 商用净水设备", "商用净水设备", "44742", "44756"],
    ["商用净饮水设备 > 商用开水器/蒸气奶泡机", "商用开水器蒸气奶泡机", "44742", "44790"],
    ["商用加热类设备 > 商用炒菜机", "商用炒菜机", "44739", "44771"],
    ["商用食品机械设备 > 商用绞肉机/切肉机/切片机", "商用绞肉机切肉机切片机", "44744", "44799"],
    ["商用食品机械设备 > 商用切菜机", "商用切菜机", "44744", "44757"],
    ["商用消毒/清洗/清洁类设备 > 商用洗碗机", "商用洗碗机", "44740", "44759"],
    ["商用食品机械设备 > 商用磨粉机/粉碎机", "商用磨粉机粉碎机", "44744", "44811"],
  ]);
  assert.throws(() => validateJdMarketDailyConfig({ ...config, maxDaysPerFile: 2 }), /配置无效/);
  assert.throws(() => validateJdMarketDailyConfig({ ...config, scope: "self" }), /配置无效/);
  assert.throws(() => validateJdMarketDailyConfig({ ...config, silentNoWindow: false }), /配置无效/);
  assert.throws(() => validateJdMarketDailyConfig({ ...config, categories: config.categories.slice(0, 6) }), /配置无效/);
  assert.throws(() => validateJdMarketDailyConfig({
    ...config,
    categories: [...config.categories.slice(0, -1), { ...config.categories[0], key: "duplicate-category-identity" }],
  }), /配置无效/);
});

test("JD market store key resolves only to the controlled Profile 3 identity", async () => {
  const [config, registry] = await Promise.all([
    readFile(new URL("../config/jd-market-ranking-daily.json", import.meta.url), "utf8").then((raw) => JSON.parse(raw)),
    readFile(new URL("../config/jd-store-accounts.json", import.meta.url), "utf8").then((raw) => JSON.parse(raw)),
  ]);
  const selected = registry.stores.filter((store: { storeKey?: string }) => store.storeKey === config.storeKey);
  assert.equal(selected.length, 1);
  assert.deepEqual({
    shopName: selected[0].shopName,
    shopId: selected[0].shopId,
    profileName: selected[0].browser.profileName,
    debugPort: selected[0].browser.debugPort,
  }, { shopName: "志高商用洗碗机旗舰店", shopId: "711743", profileName: "Profile 3", debugPort: 9227 });
});

test("JD market runner follows the new seven UI steps and verifies native XLSX before import", async () => {
  const runner = await readFile(new URL("../tools/jd-market-ranking-daily.ts", import.meta.url), "utf8");
  assert.match(runner, /jdMarketRankingPageUrl = "https:\/\/jdsz\.jd\.com\/szweb\/view\/industry\/industry-top\.html"/);
  assert.match(runner, /waitForRankingSurface\(frame\)/);
  assert.match(runner, /#jdsz-container/);
  assert.match(runner, /industry-top-head-filter-content/);
  assert.match(runner, /getByText\("商品榜", \{ exact: true \}\)/);
  assert.match(runner, /getByText\("SKU", \{ exact: true \}\)/);
  assert.match(runner, /selectUniqueCategoryPath[\s\S]*target\.categoryPath/);
  assert.match(runner, /getByText\("热销排名", \{ exact: true \}\)/);
  assert.match(runner, /getByText\("下载数据", \{ exact: true \}\)/);
  assert.match(runner, /data-event-content="当前时间_自定义"/);
  assert.match(runner, /当前时间自定义_\$\{date\}/);
  assert.match(runner, /jdDateRangeSelectionPlan\(date, date\)/);
  assert.match(runner, /page\.waitForEvent\("download"/);
  assert.match(runner, /downloadProductRank\.ajax/);
  assert.match(runner, /assertJdMarketNativeDownloadRequest\(nativeRequest, target, date\)/);
  assert.match(runner, /suggestedName[\s\S]*\\\.xlsx/);
  assert.match(runner, /bytes\[0\] !== 0x50 \|\| bytes\[1\] !== 0x4b/);
  assert.match(runner, /原生下载只允许单个自然日分块/);
  assert.match(runner, /plan\.version !== 4/);
  assert.match(runner, /saveEvidenceScreenshot\(page, plan, targetPlan, "downloadReady"\)/);
  assert.match(runner, /inspectSignedChunk\(plan, config, target, chunk\)/);
  assert.match(runner, /application\/vnd\.openxmlformats-officedocument\.spreadsheetml\.sheet/);
  assert.match(runner, /validateJdMarketImportResponse\(response\.status, body, evidence\)/);
  assert.match(runner, /assertJdMarketImportProof\(chunk\.importProof, evidence\)/);
  assert.match(runner, /missingAfterImport\.length/);
  assert.match(runner, /withJackyunRunLock\(\{/);
  assert.match(runner, /keepWindowHidden: plan\.silentNoWindow/);
  assert.match(runner, /closeChromeBrowser\(store\.browser\.debugPort\)/);
  assert.match(runner, /page\.screenshot\(\{ path: filePath, fullPage: false, timeout: 30_000, animations: "disabled" \}\)/);
  assert.doesNotMatch(runner.slice(runner.indexOf("export async function runJdMarketDailyPlan")), /fetchRankDay|buildCsv|fetchImages|#jdsz-export-panel/);
});
