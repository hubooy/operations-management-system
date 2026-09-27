import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { isPublicAiPath } from "../lib/django/ai-service";
import { correspondingMarketBaselineDate, marketPairs, marketPriceBoundaryCents, marketSelector, validateMarketPreview,
  validateParkedCreate } from "../lib/ai/business-market-v2-preview";

const source = (key: string, window: string, category = "商用设备") => ({ key, domain: "market", query: {
  platform: "京东", category, scope: "POP", rankingDimension: "SKU", priceBandFilter: "全部",
  startDate: "2026-08-01", endDate: "2026-08-30", window,
} });
const current = source("market-current", "current"), baseline = source("market-previous", "previous");
const flags = { materialAdmitted: false, agentReadPersisted: false, actualAgentBound: false,
  authorityVerified: false, renderer: false, requestMaterialReplayed: true };
const columns = [{ key: "name", label: "名称", kind: "text" }];
const tables = ["price_band_summary", "price_band_members", "rank_entry_exit"].map(view =>
  ({ view, title: view, rowCount: 1, columns }));
const create = { item: { id: "parked-1", workflowStatus: "paused", pauseReason: "market_material_not_admitted",
  reportGenerationSupported: false, agentDispatchSupported: false, marketMaterialReady: false }, replayed: false, ...flags };
const preview = { schemaVersion: "business-market-v2-parked-preview-v1", reportId: "parked-1",
  sourceReportId: "source-1", workflowStatus: "paused", pauseReason: "market_material_not_admitted",
  marketManifestDigest: "a".repeat(64), resultDigest: "b".repeat(64), observationCoverage: {}, tables,
  page: null, marketTopSampleOnly: true, priceSummaryAndMembersAdditive: false, marketAndOwnSalesAdditive: false, ...flags };

test("market preview route is exact and does not expose arbitrary paths", () => {
  assert.equal(isPublicAiPath("/api/ai/market-v2-parked-reports"), true);
  assert.equal(isPublicAiPath("/api/ai/market-v2-parked-reports/parked-1/preview"), true);
  assert.equal(isPublicAiPath("/api/ai/market-v2-parked-reports/parked-1/files"), false);
});

test("parked preview proxy requires administrator and explicit Next flag before backend forwarding", async () => {
  const bundle = await build({ entryPoints: ["lib/ai/django-route.ts"], bundle: true, write: false,
    platform: "node", format: "esm", plugins: [{ name: "market-boundary", setup(builder) {
      builder.onResolve({ filter: /^@\/(?:lib\/(?:auth\/authorization|django\/(?:ai-service|ai-stream)|ai\/page-context)|app\/api\/ai\/route-helpers)$/ },
        args => ({ path: args.path, namespace: "market-test" }));
      builder.onLoad({ filter: /.*/, namespace: "market-test" }, args => {
        const part = args.path;
        const contents = part.includes("authorization") ? `
          export const requireAppPrincipal=async()=>({email:'admin@example.invalid',role:globalThis.marketTestRole,scope:null});
          export const requireUnrestrictedDataScope=()=>{};`
          : part.includes("ai-service") ? `
          export const isPublicAiPath=()=>true;
          export const aiEnvironment=async()=>({AI_MARKET_V2_PREVIEW_ENABLED:globalThis.marketTestFlag});
          export const requestDjangoAi=async(_principal,input)=>{globalThis.marketTestCalls.push(input);
            return {status:200,revision:'1',data:{schemaVersion:'test',path:input.path}}};`
          : part.includes("ai-stream") ? `export const requestDjangoAiStream=()=>{throw Error('unexpected stream')};`
          : part.includes("page-context") ? `export const normalizeAiPageContext=value=>value;`
          : `export const aiJsonResponse=(data,init)=>Response.json(data,init);
             export const aiRouteErrorResponse=error=>Response.json({error:String(error)}, {status:error.status||500});
             export const readAiJsonObject=request=>request.json();
             export const requireAiSameOriginWrite=()=>{};`;
        return { contents, loader: "js" };
      });
    } }] });
  const route = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`) as
    { forwardAiRequest(request: Request): Promise<Response> };
  const state = globalThis as typeof globalThis & { marketTestFlag?: string; marketTestRole?: string; marketTestCalls?: unknown[] };
  state.marketTestCalls = []; state.marketTestRole = "admin"; state.marketTestFlag = undefined;
  const url = "https://fixture.invalid/api/ai/market-v2-parked-reports";
  try {
    assert.equal((await route.forwardAiRequest(new Request(url))).status, 409);
    assert.equal(state.marketTestCalls.length, 0);
    state.marketTestFlag = "true"; state.marketTestRole = "viewer";
    assert.equal((await route.forwardAiRequest(new Request(url))).status, 403);
    assert.equal(state.marketTestCalls.length, 0);
    state.marketTestRole = "admin";
    assert.equal((await route.forwardAiRequest(new Request(url + "/parked-1/preview?view=price_band_summary&offset=0&limit=20"))).status, 200);
    assert.equal(state.marketTestCalls.length, 1);
  } finally { delete state.marketTestFlag; delete state.marketTestRole; delete state.marketTestCalls; }
});

test("market pair uses exact sealed directory condition except window", () => {
  assert.deepEqual(marketPairs([current, baseline]), [{ current, baseline }]);
  assert.deepEqual(marketPairs([current, source("wrong", "previous", "别的类目")]), []);
  assert.deepEqual(marketPairs([current, source("year", "yearAgo")]).length, 1);
  assert.throws(() => marketPairs([current, current]));
});

test("selector requires two explicit in-range dates and exact yuan-to-cent split", () => {
  assert.equal(marketPriceBoundaryCents("1000.05"), 100005);
  assert.throws(() => marketPriceBoundaryCents("0"));
  assert.throws(() => marketPriceBoundaryCents("100.005"));
  const pair = marketPairs([current, baseline])[0];
  assert.equal(correspondingMarketBaselineDate("2026-08-30", "2026-08-01", "2026-08-30", "previous"), "2026-07-31");
  assert.equal(correspondingMarketBaselineDate("2024-02-29", "2024-02-01", "2024-02-29", "yearAgo"), "2023-02-28");
  const selected = marketSelector(pair, "2026-08-30", "2026-07-31", "1000.05");
  assert.equal(selected.rankCurrentSourceKey, "market-current");
  assert.equal(selected.bands[1].lowerCents, 100005);
  assert.throws(() => marketSelector(pair, "2026-09-01", "2026-07-31", "1000"));
  assert.throws(() => marketSelector(pair, "2026-08-30", "2026-08-01", "1000"));
});

test("create and preview remain parked even when malicious flags claim authority", () => {
  assert.equal(validateParkedCreate(create, "source-1"), "parked-1");
  assert.throws(() => validateParkedCreate({ ...create, agentReadPersisted: true }, "source-1"));
  assert.equal(validateMarketPreview(preview, { reportId: "parked-1", sourceReportId: "source-1" }).page, null);
  assert.throws(() => validateMarketPreview({ ...preview, renderer: true }, { reportId: "parked-1", sourceReportId: "source-1" }));
  assert.throws(() => validateMarketPreview(preview, { reportId: "other", sourceReportId: "source-1" }));
});

test("paged sample is limited to the requested exact view, offset and table columns", () => {
  const requested = { reportId: "parked-1", sourceReportId: "source-1", view: "price_band_summary" as const, offset: 0 };
  const good = { ...preview, page: { view: "price_band_summary", offset: 0, limit: 20, total: 1, columns, rows: [["样本"]] } };
  assert.equal(validateMarketPreview(good, requested).page?.rows.length, 1);
  assert.throws(() => validateMarketPreview({ ...good, page: { ...good.page, view: "rank_entry_exit" } }, requested));
  assert.throws(() => validateMarketPreview({ ...good, page: { ...good.page, rows: [["样本"], ["重复"]] } }, requested));
});
