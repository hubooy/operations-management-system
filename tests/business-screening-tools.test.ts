import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import { registerHooks } from "node:module";
import test, { type TestContext } from "node:test";
import type { AppPrincipal } from "../lib/auth/authorization";
import { aiToolSurfaces, validateToolArguments, validateToolRegistry, type AiToolEntry, type AiToolSurface } from "../lib/ai/tool-registry-contract";

registerHooks({ resolve(specifier, context, nextResolve) { return specifier === "cloudflare:workers" ? { url: "data:text/javascript,export const env={};", shortCircuit: true } : nextResolve(specifier, context); } });
const { aiToolRegistry, getToolsForPrincipal, getOpenAiTools, getAnthropicTools, executeRegisteredToolCall } = await import("../lib/ai/tool-registry");
const { canonicalAiEdge, handleAiEdge } = await import("../lib/ai/django-edge");
const { createAiToolExecutionRuntime } = await import("../lib/ai/tool-execution-runtime");
const { aiHeaders, isPublicAiPath } = await import("../lib/django/ai-service");
const admin: AppPrincipal = { email: "synthetic@example.invalid", displayName: "Synthetic", role: "admin", scope: null };
const surface = "business_agent_screening_v1" as const;
const names = ["get_business_screening_package_v1", "get_business_screening_analysis_table_v1", "get_business_screening_budget_v1"];
const [directory, table, budget] = names.map(name => aiToolRegistry.find(entry => entry.name === name)!);
const strip = ({ handler, ...entry }: AiToolEntry) => { void handler; return entry; };
const sha = (value: string) => createHash("sha256").update(value, "utf8").digest("hex");
const context = { principal: admin, surface, requestId: "integrated-fixture" };
const base = { reportId: "report-1", runId: "run-1", screeningId: "screen-1" }, pairKey = "a".repeat(64), baselinePairKey = "b".repeat(64);
const environment = { TERUISI_DJANGO_AI_READER_BASE_URL: "http://127.0.0.1:18191", TERUISI_DJANGO_AI_WRITER_BASE_URL: "http://127.0.0.1:18192", TERUISI_DJANGO_INTERNAL_SECRET: "synthetic-integrated-secret-at-least-32-bytes" };
function isolated(t: TestContext) {
  const previous = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]])), oldFetch = globalThis.fetch;
  Object.assign(process.env, environment);
  t.after(() => { globalThis.fetch = oldFetch; for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
}
const response = (data: object, status = 200) => Response.json(data, { status, headers: { "x-ai-revision": "1" } });
const packageArgs = { ...base, role: "commerce" };
const boundPayload = (name: string) => name === names[0] ? {
  schemaVersion: "business-screening-role-package-v1", reportId: base.reportId, evidenceRunId: base.runId, role: "commerce",
} : {
  schemaVersion: name === names[1] ? "business-screening-analysis-v1" : "business-screening-budget-v1",
  reference: { evidenceRunId: base.runId, reportId: base.reportId, screeningIntent: { id: base.screeningId } },
};
const baseline = {"surfaces":["ai_chat","dingtalk_chat","ai_agent","ai_sandbox","market_ai","customer_service_ai","codex_mcp","test","business_collection","business_agent_v2","business_agent_budget_v1","business_agent_integrated_v1"],"names":["get_business_integrated_directory_v1","get_business_integrated_analysis_table_v1","get_business_integrated_budget_v1","get_business_budget_directory_v1","get_business_budget_analysis_table_v1","get_business_budget_scenarios_v1","get_business_evidence_directory_v2","get_business_analysis_table_v2","run_pandas_analysis","get_system_dataset_records","describe_system_datasets","query_system_dataset","search_system_knowledge","search_personal_memory","list_my_agent_jobs","list_my_agent_workflows","get_data_freshness","get_sales_summary","get_sales_category_analysis","get_inventory_health","get_product_performance","list_replenishment_plans","get_customer_service_conversations","get_market_overview","get_market_sku_trend","get_market_brand_analysis","get_market_price_band_analysis","get_market_pending_review_summary","get_business_source_page","get_netshop_analysis_records","get_sales_analysis_records","get_market_analysis_records","get_business_analysis_table","get_business_budget_scenarios","get_business_analysis_evidence","get_netshop_performance","search_system_data","get_finance_page_data","get_inventory_page_data","get_netshop_page_data","get_workflow_page_data","get_import_status","get_automation_run_status","get_market_workspace_data","get_operating_settings_summary","describe_analysis_datasets","run_analysis_plan"],"registry":{"count":47,"sha256":"97a20fecfeeca62cca5436599a1b76eb1c158f11ee10cc44b3b09f6ca6106a01"},"catalogs":{"ai_chat/viewer/unscoped":{"count":18,"sha256":"fd72da2559c20058d73a884089b12322333420130ca573e1173e1d769f326832"},"ai_chat/viewer/scoped":{"count":13,"sha256":"21df8c15b7e40c0e7a1377c13f9bfd1a69122c5ecd702647099075c47950a910"},"ai_chat/analyst/unscoped":{"count":31,"sha256":"3df08c901679d6942ab9b5e2c8f7580065b7109bc2f98130bf1344a05522a6fc"},"ai_chat/analyst/scoped":{"count":16,"sha256":"2690f244a7a0941dd10a8f4d037bfb6e964fce594fba5d3b1b94dccb102af26f"},"ai_chat/operator/unscoped":{"count":31,"sha256":"3df08c901679d6942ab9b5e2c8f7580065b7109bc2f98130bf1344a05522a6fc"},"ai_chat/operator/scoped":{"count":16,"sha256":"2690f244a7a0941dd10a8f4d037bfb6e964fce594fba5d3b1b94dccb102af26f"},"ai_chat/admin/unscoped":{"count":38,"sha256":"a9110d7244ea0aeebefc315e534e0cab9d0d97cbf6af64afda4bffdf7d35c354"},"ai_chat/admin/scoped":{"count":16,"sha256":"2690f244a7a0941dd10a8f4d037bfb6e964fce594fba5d3b1b94dccb102af26f"},"dingtalk_chat/viewer/unscoped":{"count":15,"sha256":"2612bd2c84b308ee286afac1f7cc8723d80fd9582a7a4be741de95ca29828e6e"},"dingtalk_chat/viewer/scoped":{"count":10,"sha256":"129678035b61c189c537da8612a95764e1a687732ae1fb3235a4b7e1c3a3fad2"},"dingtalk_chat/analyst/unscoped":{"count":27,"sha256":"6390bb2285649b3717f04765bd18e88dceaee70f388dec785590d963e5bf80e6"},"dingtalk_chat/analyst/scoped":{"count":12,"sha256":"6185a8011422397c099ce55b0635c10be5bf2505055077b7310ca2f01f0143a7"},"dingtalk_chat/operator/unscoped":{"count":27,"sha256":"6390bb2285649b3717f04765bd18e88dceaee70f388dec785590d963e5bf80e6"},"dingtalk_chat/operator/scoped":{"count":12,"sha256":"6185a8011422397c099ce55b0635c10be5bf2505055077b7310ca2f01f0143a7"},"dingtalk_chat/admin/unscoped":{"count":28,"sha256":"03d2dc1c06d3d516fc5ddf9d7aa8ad540bc14a2eb2e442e8c104f700187e11c7"},"dingtalk_chat/admin/scoped":{"count":12,"sha256":"6185a8011422397c099ce55b0635c10be5bf2505055077b7310ca2f01f0143a7"},"ai_agent/viewer/unscoped":{"count":18,"sha256":"fd72da2559c20058d73a884089b12322333420130ca573e1173e1d769f326832"},"ai_agent/viewer/scoped":{"count":13,"sha256":"21df8c15b7e40c0e7a1377c13f9bfd1a69122c5ecd702647099075c47950a910"},"ai_agent/analyst/unscoped":{"count":31,"sha256":"3df08c901679d6942ab9b5e2c8f7580065b7109bc2f98130bf1344a05522a6fc"},"ai_agent/analyst/scoped":{"count":16,"sha256":"2690f244a7a0941dd10a8f4d037bfb6e964fce594fba5d3b1b94dccb102af26f"},"ai_agent/operator/unscoped":{"count":31,"sha256":"3df08c901679d6942ab9b5e2c8f7580065b7109bc2f98130bf1344a05522a6fc"},"ai_agent/operator/scoped":{"count":16,"sha256":"2690f244a7a0941dd10a8f4d037bfb6e964fce594fba5d3b1b94dccb102af26f"},"ai_agent/admin/unscoped":{"count":38,"sha256":"a9110d7244ea0aeebefc315e534e0cab9d0d97cbf6af64afda4bffdf7d35c354"},"ai_agent/admin/scoped":{"count":16,"sha256":"2690f244a7a0941dd10a8f4d037bfb6e964fce594fba5d3b1b94dccb102af26f"},"ai_sandbox/viewer/unscoped":{"count":1,"sha256":"3c5f6d535e6b8054b96c08977553cd4db898f00ac7e72f383de527ff25e2ca25"},"ai_sandbox/viewer/scoped":{"count":1,"sha256":"3c5f6d535e6b8054b96c08977553cd4db898f00ac7e72f383de527ff25e2ca25"},"ai_sandbox/analyst/unscoped":{"count":2,"sha256":"f4ec3f475e4f43afcca340e56aad57e1a56a579d45d95b513bec9228b6ceb659"},"ai_sandbox/analyst/scoped":{"count":2,"sha256":"f4ec3f475e4f43afcca340e56aad57e1a56a579d45d95b513bec9228b6ceb659"},"ai_sandbox/operator/unscoped":{"count":2,"sha256":"f4ec3f475e4f43afcca340e56aad57e1a56a579d45d95b513bec9228b6ceb659"},"ai_sandbox/operator/scoped":{"count":2,"sha256":"f4ec3f475e4f43afcca340e56aad57e1a56a579d45d95b513bec9228b6ceb659"},"ai_sandbox/admin/unscoped":{"count":2,"sha256":"f4ec3f475e4f43afcca340e56aad57e1a56a579d45d95b513bec9228b6ceb659"},"ai_sandbox/admin/scoped":{"count":2,"sha256":"f4ec3f475e4f43afcca340e56aad57e1a56a579d45d95b513bec9228b6ceb659"},"market_ai/viewer/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"market_ai/viewer/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"market_ai/analyst/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"market_ai/analyst/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"market_ai/operator/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"market_ai/operator/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"market_ai/admin/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"market_ai/admin/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"customer_service_ai/viewer/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"customer_service_ai/viewer/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"customer_service_ai/analyst/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"customer_service_ai/analyst/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"customer_service_ai/operator/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"customer_service_ai/operator/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"customer_service_ai/admin/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"customer_service_ai/admin/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"codex_mcp/viewer/unscoped":{"count":18,"sha256":"fd72da2559c20058d73a884089b12322333420130ca573e1173e1d769f326832"},"codex_mcp/viewer/scoped":{"count":13,"sha256":"21df8c15b7e40c0e7a1377c13f9bfd1a69122c5ecd702647099075c47950a910"},"codex_mcp/analyst/unscoped":{"count":30,"sha256":"4077b42ab4fe35866e61f27bea1cd94e1ea69af6f72e59c442a499c8da11790e"},"codex_mcp/analyst/scoped":{"count":15,"sha256":"c7f4834a546a7ed21e5b5506e049192fef3e748641259001597194973744f049"},"codex_mcp/operator/unscoped":{"count":30,"sha256":"4077b42ab4fe35866e61f27bea1cd94e1ea69af6f72e59c442a499c8da11790e"},"codex_mcp/operator/scoped":{"count":15,"sha256":"c7f4834a546a7ed21e5b5506e049192fef3e748641259001597194973744f049"},"codex_mcp/admin/unscoped":{"count":37,"sha256":"736891d06890c10818b4cf1677a862e3815d1fa3f679e5aaf8892949e73df8c9"},"codex_mcp/admin/scoped":{"count":15,"sha256":"c7f4834a546a7ed21e5b5506e049192fef3e748641259001597194973744f049"},"test/viewer/unscoped":{"count":18,"sha256":"fd72da2559c20058d73a884089b12322333420130ca573e1173e1d769f326832"},"test/viewer/scoped":{"count":13,"sha256":"21df8c15b7e40c0e7a1377c13f9bfd1a69122c5ecd702647099075c47950a910"},"test/analyst/unscoped":{"count":31,"sha256":"3df08c901679d6942ab9b5e2c8f7580065b7109bc2f98130bf1344a05522a6fc"},"test/analyst/scoped":{"count":16,"sha256":"2690f244a7a0941dd10a8f4d037bfb6e964fce594fba5d3b1b94dccb102af26f"},"test/operator/unscoped":{"count":31,"sha256":"3df08c901679d6942ab9b5e2c8f7580065b7109bc2f98130bf1344a05522a6fc"},"test/operator/scoped":{"count":16,"sha256":"2690f244a7a0941dd10a8f4d037bfb6e964fce594fba5d3b1b94dccb102af26f"},"test/admin/unscoped":{"count":38,"sha256":"a9110d7244ea0aeebefc315e534e0cab9d0d97cbf6af64afda4bffdf7d35c354"},"test/admin/scoped":{"count":16,"sha256":"2690f244a7a0941dd10a8f4d037bfb6e964fce594fba5d3b1b94dccb102af26f"},"business_collection/viewer/unscoped":{"count":1,"sha256":"f40ed46d9f4238c6cb8ba1528f91b914122df50e88e8858866b40041ff725604"},"business_collection/viewer/scoped":{"count":1,"sha256":"f40ed46d9f4238c6cb8ba1528f91b914122df50e88e8858866b40041ff725604"},"business_collection/analyst/unscoped":{"count":1,"sha256":"f40ed46d9f4238c6cb8ba1528f91b914122df50e88e8858866b40041ff725604"},"business_collection/analyst/scoped":{"count":1,"sha256":"f40ed46d9f4238c6cb8ba1528f91b914122df50e88e8858866b40041ff725604"},"business_collection/operator/unscoped":{"count":1,"sha256":"f40ed46d9f4238c6cb8ba1528f91b914122df50e88e8858866b40041ff725604"},"business_collection/operator/scoped":{"count":1,"sha256":"f40ed46d9f4238c6cb8ba1528f91b914122df50e88e8858866b40041ff725604"},"business_collection/admin/unscoped":{"count":2,"sha256":"6b419898211faca7acc42555400d0e8874e2ff63a7ed43ea0e355310ac741cd8"},"business_collection/admin/scoped":{"count":1,"sha256":"f40ed46d9f4238c6cb8ba1528f91b914122df50e88e8858866b40041ff725604"},"business_agent_v2/viewer/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_v2/viewer/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_v2/analyst/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_v2/analyst/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_v2/operator/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_v2/operator/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_v2/admin/unscoped":{"count":2,"sha256":"8fe980218d345df11189231ab97a8df1d9838d4dd74e6b58fdde7be060527d19"},"business_agent_v2/admin/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_budget_v1/viewer/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_budget_v1/viewer/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_budget_v1/analyst/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_budget_v1/analyst/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_budget_v1/operator/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_budget_v1/operator/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_budget_v1/admin/unscoped":{"count":3,"sha256":"c960ed25d39c0c6f1145bc56472b1f2e105052aec68273826c0fc0a8429cdfd1"},"business_agent_budget_v1/admin/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_integrated_v1/viewer/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_integrated_v1/viewer/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_integrated_v1/analyst/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_integrated_v1/analyst/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_integrated_v1/operator/unscoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_integrated_v1/operator/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},"business_agent_integrated_v1/admin/unscoped":{"count":3,"sha256":"c00c4dded4f0cdc26d2e4acb3867f14220adcbfe02057ae6b39622abe0ee87f6"},"business_agent_integrated_v1/admin/scoped":{"count":0,"sha256":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}}};
test("all 47 legacy entries and their 96 catalog projections preserve exact canonical bytes", () => {
  assert.deepEqual(aiToolSurfaces.slice(0, baseline.surfaces.length), baseline.surfaces);
  assert.deepEqual(aiToolSurfaces.slice(baseline.surfaces.length), [
    surface, "business_agent_screening_promotion_v1",
    "business_agent_screening_promotion_market_v2",
  ]);
  const old = aiToolRegistry.filter(entry => !["get_business_promotion_screening_package_v1", "get_business_promotion_screening_analysis_v1", "get_business_promotion_screening_budget_v1", "get_business_promotion_keyword_sku_v1"].includes(entry.name) && !names.includes(entry.name) && !["get_business_netshop_continuation_page", "get_business_sales_continuation_page", "get_business_market_continuation_page", "get_business_finance_source_page"].includes(entry.name));
  assert.deepEqual(old.map(entry => entry.name), baseline.names);
  assert.deepEqual({count:old.length,sha256:sha(canonicalAiEdge(old.map(strip)))}, baseline.registry);
  const catalogs: Record<string, unknown> = {};
  for (const oldSurface of baseline.surfaces as AiToolSurface[]) for (const role of ["viewer","analyst","operator","admin"] as const) for (const scoped of [false,true]) {
    // The new collector-only continuations are tested independently; retain the
    // exact old entry/schema bytes rather than replacing the stored baseline.
    const entries = getToolsForPrincipal({...admin,role,scope:scoped?{warehouses:[],channels:[],platforms:[]}:null},oldSurface)
      .filter(entry => !["get_business_netshop_continuation_page", "get_business_sales_continuation_page", "get_business_market_continuation_page", "get_business_finance_source_page"].includes(entry.name)).map(strip);
    catalogs[`${oldSurface}/${role}/${scoped?"scoped":"unscoped"}`]={count:entries.length,sha256:sha(canonicalAiEdge(entries))};
  }
  assert.deepEqual(catalogs,baseline.catalogs);
});

test("three new tools are exclusive to the screening admin-unscoped surface", () => {
  validateToolRegistry(aiToolRegistry);
  assert.deepEqual(getToolsForPrincipal(admin, surface).map(entry => entry.name), names);
  assert.deepEqual(getOpenAiTools(admin, surface).map(entry => entry.function.name), names);
  assert.deepEqual(getAnthropicTools(admin, surface).map(entry => entry.name), names);
  for (const old of aiToolSurfaces.filter(value => value !== surface)) assert.equal(getToolsForPrincipal(admin, old).some(entry => names.includes(entry.name)), false);
  for (const role of ["viewer", "analyst", "operator"] as const) assert.deepEqual(getToolsForPrincipal({ ...admin, role }, surface), []);
  assert.deepEqual(getToolsForPrincipal({ ...admin, scope: { warehouses: [], channels: [], platforms: [] } }, surface), []);
  for (const tool of [directory, table, budget]) {
    assert.deepEqual(tool.execution.allowedSurfaces, [surface]);
    assert.equal(tool.execution.maxResultCharacters, 40000); assert.equal(tool.execution.maxCallsPerRequest, 8);
    assert.equal("limit" in tool.inputSchema.properties, false);
  }
  assert.match(table.description, /table\.pagination\.nextOffset/);
  assert.match(budget.description, /budget\.pagination\.nextOffset/);
});

test("fixed report/run IDs, integer bounds, no digests or page-size parameters", async t => {
  isolated(t); let calls = 0; globalThis.fetch = async () => { calls++; return response({ ok: true }); };
  for (const [tool, max] of [[directory, 9999], [budget, 99]] as const) {
    const args = tool === directory ? packageArgs : base;
    validateToolArguments(args, tool.inputSchema); validateToolArguments({ ...args, offset: max }, tool.inputSchema);
    for (const extra of [{ offset: max+1 }, { offset: -1 }, { offset: true }, { offset: null }, { offset: 0.5 }, { limit: 20 }, { catalogDigest: pairKey }, { budgetRef: {} }]) {
      assert.throws(() => validateToolArguments({ ...args, ...extra }, tool.inputSchema));
      await assert.rejects(tool.handler({ ...args, ...extra }, context), /参数无效/);
    }
    for (const args of [{ runId: "run-1" }, { reportId: "report-1" }, { ...base, reportId: "../private" }, { ...base, runId: "x".repeat(161) }]) await assert.rejects(tool.handler(args, context), /参数无效/);
  }
  assert.equal(calls, 0);
});

test("native and mapped selectors are mutually exclusive even when handler called directly", async t => {
  isolated(t); let calls = 0; globalThis.fetch = async () => { calls++; return response({ ok: true }); };
  const native = { ...base, mode: "native", dimension: "shop", sourceKey: "sales" }, mapped = { ...base, mode: "mapped", dimension: "sku", pairKey };
  validateToolArguments(native, table.inputSchema); validateToolArguments(mapped, table.inputSchema);
  for (const args of [{ ...base, mode: "native", dimension: "shop" }, { ...native, pairKey }, { ...native, baselinePairKey }, { ...mapped, sourceKey: "sales" }, { ...mapped, baselineKey: "before" }, { ...mapped, dimension: "keyword" }, { ...mapped, pairKey: "BAD" }, { ...mapped, baselinePairKey: "B".repeat(64) }, { ...mapped, offset: true }, { ...mapped, offset: null }, { ...mapped, offset: 250001 }, { ...native, dimension: "customer" }, { ...native, limit: 20 }, { ...native, mode: "auto" }]) await assert.rejects(table.handler(args, context), /参数无效/);
  assert.equal(calls, 0);
});

test("three signed owning-reader paths forward exact IDs and actual offsets without limit", async t => {
  isolated(t); const observed: { path: string; query: Record<string, string> }[] = [];
  globalThis.fetch = async (url, init) => {
    const target = new URL(String(url)); assert.equal(target.origin, environment.TERUISI_DJANGO_AI_READER_BASE_URL);
    assert.equal(init?.method, "GET"); assert.equal(init?.cache, "no-store"); assert.ok(new Headers(init?.headers).has("x-teruisi-signature"));
    observed.push({ path: target.pathname, query: Object.fromEntries(target.searchParams) });
    return response(boundPayload(target.pathname.endsWith("screening/package") ? directory.name : target.pathname.endsWith("screening/budget") ? budget.name : table.name));
  };
  assert.deepEqual(await directory.handler(packageArgs, context), boundPayload(directory.name));
  await budget.handler({ ...base, offset: 1 }, context);
  await table.handler({ ...base, mode: "native", dimension: "category", sourceKey: "sales", baselineKey: "before", offset: 7 }, context);
  await table.handler({ ...base, mode: "mapped", dimension: "spu", pairKey, baselinePairKey }, context);
  assert.deepEqual(observed, [
    { path: "/api/ai/reports/report-1/screening/package", query: { runId: "run-1", screeningId: "screen-1", offset: "0", role: "commerce" } },
    { path: "/api/ai/reports/report-1/screening/budget", query: { runId: "run-1", screeningId: "screen-1", offset: "1" } },
    { path: "/api/ai/reports/report-1/screening/analysis", query: { runId: "run-1", screeningId: "screen-1", offset: "7", mode: "native", dimension: "category", sourceKey: "sales", baselineKey: "before" } },
    { path: "/api/ai/reports/report-1/screening/analysis", query: { runId: "run-1", screeningId: "screen-1", offset: "0", mode: "mapped", dimension: "spu", pairKey, baselinePairKey } },
  ]);
});

test("every screening tool enforces exact serialized UTF8 size without truncation", async t => {
  isolated(t); let payload: object = {}; globalThis.fetch = async () => response(payload);
  const entries = [[directory, packageArgs], [budget, base], [table, { ...base, mode: "native", sourceKey: "sales", dimension: "shop" }]] as const;
  for (const [tool, args] of entries) {
    const empty = { ...boundPayload(tool.name), text: "" }, padding = 38000-Buffer.byteLength(JSON.stringify(empty));
    payload = { ...empty, text: "a".repeat(padding) };
    assert.equal(Buffer.byteLength(JSON.stringify(payload)), 38000);
    assert.deepEqual(await tool.handler(args, context), payload);
    for (const value of ["a".repeat(padding+1), "中".repeat(13000), '中\n"'.repeat(6000)]) {
      payload = { ...empty, text: value };
      await assert.rejects(tool.handler(args, context), /不得截断/);
    }
  }
});

test("receipts bind exact schema and report/run, with role or screening intent", async t => {
  isolated(t); let payload: object = {}; globalThis.fetch = async () => response(payload);
  for (const [tool,args] of [[directory,packageArgs],[budget,base],[table,{...base,mode:"mapped",dimension:"sku",pairKey}]] as const) {
    const good=boundPayload(tool.name);
    const broken: object[]=[{}, {...good,schemaVersion:"old"}];
    if(tool===directory) for(const extra of [{role:"report"},{reportId:"other"},{evidenceRunId:"other"},{role:undefined}]) broken.push({...good,...extra});
    else for(const reference of [null,[],{}, {reportId:base.reportId,evidenceRunId:base.runId,screeningIntent:{id:"other"}}, {reportId:"other",evidenceRunId:base.runId,screeningIntent:{id:base.screeningId}}, {reportId:base.reportId,evidenceRunId:"other",screeningIntent:{id:base.screeningId}}]) broken.push({...good,reference});
    for(const bad of broken){payload=bad;await assert.rejects(tool.handler(args,context),/回执协议或固定身份不一致/);}
    payload=good;assert.deepEqual(await tool.handler(args,context),good);
  }
});

test("absent budget and cancellation propagate rather than synthesize success", async t => {
  isolated(t); globalThis.fetch = async () => response({ error: "没有固定预算", code: "not_found" }, 404);
  await assert.rejects(budget.handler(base, context));
  let calls = 0; globalThis.fetch = async () => { calls++; return response({ rows: [] }); };
  const ctl = new AbortController(); ctl.abort();
  await assert.rejects(directory.handler(packageArgs, { ...context, signal: ctl.signal }));
  assert.equal(calls, 0);
});

test("signed edge keeps policy digest, authorization, audit and old-surface denial", async t => {
  isolated(t); const seen: string[] = [];
  globalThis.fetch = async (url) => {
    const target = new URL(String(url)); seen.push(target.pathname);
    if (target.pathname === "/api/ai/consumer") return response({ ok: true });
    if (target.pathname === "/api/ai/reports/report-1/screening/package") return response({ ...boundPayload(directory.name), items: [], nextOffset: null });
    throw new Error("Unexpected request "+target.pathname);
  };
  async function edge(body: object, actor = admin) {
    const raw = JSON.stringify(body), path = "/api/ai/internal/edge";
    const headers = await aiHeaders({ secret: environment.TERUISI_DJANGO_INTERNAL_SECRET, principal: actor, path, method: "POST", query: "", body: raw, requestId: "integrated-edge" });
    return handleAiEdge(new Request("https://synthetic.invalid"+path, { method: "POST", headers, body: raw }));
  }
  const catalogResponse = await edge({ action: "catalog", surface }); assert.equal(catalogResponse.status, 200);
  const catalog = (await catalogResponse.json() as { entries: AiToolEntry[] }).entries; assert.deepEqual(catalog.map(e => e.name), names);
  const request = { action: "execute", surface, name: directory.name, arguments: packageArgs, requestId: "integrated-edge", policyDigest: sha(canonicalAiEdge(catalog)) };
  assert.equal((await edge({ ...request, policyDigest: "0".repeat(64) })).status, 403); assert.deepEqual(seen, []);
  const success = await edge(request); assert.equal(success.status, 200); assert.equal((await success.json() as { ok: boolean }).ok, true);
  assert.deepEqual(seen, ["/api/ai/consumer", "/api/ai/reports/report-1/screening/package", "/api/ai/consumer"]);
  assert.equal((await edge({ ...request, surface: "ai_agent" })).status, 403);
  const denied = await executeRegisteredToolCall(directory.name, packageArgs, { ...context, surface: "ai_chat" }); assert.equal(denied.ok, false);
});

test("package role and every fixed identity are required; denied actors never reach transport",async t=>{
 isolated(t);let calls=0;globalThis.fetch=async()=>{calls++;return response(boundPayload(directory.name));};
 for(const extra of [{role:undefined},{role:"admin"},{role:null},{screeningId:undefined},{screeningId:"../screen"},{jobId:"job"},{offset:10000}]) await assert.rejects(directory.handler({...packageArgs,...extra},context));
 for(const tool of [directory,table,budget]) for(const principal of [{...admin,role:"viewer" as const},{...admin,scope:{warehouses:[],channels:[],platforms:[]}}]) await assert.rejects(tool.handler(packageArgs,{...context,principal}));
 assert.equal(calls,0);
});

test("only the three exact screening reader paths are admitted",()=>{
 for(const op of ["package","analysis","budget"]) assert.equal(isPublicAiPath(`/api/ai/reports/report-1/screening/${op}`),true);
 for(const path of ["/api/ai/reports/report-1/screening","/api/ai/reports/report-1/screening/publish","/api/ai/reports/report-1/screening/package/extra","/api/ai/reports/../screening/package",`/api/ai/reports/${"x".repeat(161)}/screening/package`]) assert.equal(isPublicAiPath(path),false);
});

test("eighth package read succeeds but ninth is denied before handler dispatch",async t=>{
 isolated(t);let calls=0;globalThis.fetch=async()=>{calls++;return response(boundPayload(directory.name));};
 const runtime=createAiToolExecutionRuntime({context,entries:[directory],audit:async()=>{},limits:{maxTotalCalls:20}});
 for(let i=0;i<8;i++) assert.equal((await runtime.execute(directory.name,packageArgs)).ok,true);
 assert.equal((await runtime.execute(directory.name,packageArgs)).ok,false);
 assert.equal(calls,8);
});
test("upstream authority/conflict/unknown failure never falls back or fabricates a page",async t=>{
 isolated(t);
 for(const status of [403,409,503]) {
  let calls=0;globalThis.fetch=async url=>{calls++;assert.equal(new URL(String(url)).origin,environment.TERUISI_DJANGO_AI_READER_BASE_URL);return response({error:"synthetic blocked",code:status===403?"access_denied":status===409?"conflict":"service_unavailable"},status);};
  await assert.rejects(directory.handler(packageArgs,context));assert.equal(calls,1);
 }
});
