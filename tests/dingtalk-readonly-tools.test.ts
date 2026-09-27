import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { executeToolCallWithRegistry, type AiToolEntry } from "../lib/ai/tool-registry-contract";

test("DingTalk opt-in is explicitly covers all business modules with read-only tools", async () => {
  const source = (await readFile(new URL("../lib/ai/tool-registry.ts", import.meta.url), "utf8")).replace(/\r\n/g, "\n");
  const entries = source.split(/\n  \{\n    name: /).slice(1);
  const names = entries.filter(entry => /execution:.*dingTalkReadOnlyExecution/.test(entry)).map(entry => entry.match(/^"([^"]+)"/)![1]).sort();
  assert.deepEqual(names, ["get_data_freshness", "get_sales_summary", "get_sales_category_analysis", "get_inventory_health", "get_inventory_page_data", "get_netshop_performance", "get_netshop_page_data", "get_system_dataset_records", "describe_system_datasets", "query_system_dataset", "list_my_agent_jobs", "list_my_agent_workflows", "get_product_performance", "list_replenishment_plans", "get_customer_service_conversations", "get_market_overview", "get_market_sku_trend", "get_market_brand_analysis", "get_market_price_band_analysis", "get_market_pending_review_summary", "search_system_data", "get_finance_page_data", "get_workflow_page_data", "get_import_status", "get_automation_run_status", "get_market_workspace_data", "get_operating_settings_summary"].sort());
  for (const entry of entries.filter(entry => /execution:.*dingTalkReadOnlyExecution/.test(entry))) {
    assert.match(entry, /risk: "read_only"/);
  }
});

test("a model cannot execute a web-only tool from the DingTalk surface", async () => {
  let executed = false;
  const entry: AiToolEntry = {
    name: "search_personal_memory", title: "Private", description: "Private",
    risk: "read_only", annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    allowedRoles: ["admin"], scopePolicy: "unscoped_only",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    execution: { environment: "worker_inline", mode: "direct", allowedSurfaces: ["ai_chat"], timeoutMs: 1000, maxResultCharacters: 1000, maxCallsPerRequest: 1 },
    handler: async () => { executed = true; return {}; },
  };
  const result = await executeToolCallWithRegistry(entry.name, {}, {
    principal: { email: "fixture@example.invalid", displayName: "Fixture", role: "admin", scope: null },
    surface: "dingtalk_chat", requestId: "ding-fixture",
  }, { entries: [entry], audit: async () => {} });
  assert.equal(result.ok, false);
  assert.equal(executed, false);
});
