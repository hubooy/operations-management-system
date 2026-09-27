import assert from "node:assert/strict";
import test from "node:test";
import { readFile, mkdir, mkdtemp, writeFile } from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";
import { apiSha, permissionCanonical, buildApiParameters, prepareApiExport, readApiScope, type ApiTemplates } from "../lib/jackyun/api-plan";
import { apiTaskWindowObserved, apiTaskWindowStart } from "../lib/jackyun/api-clock";
import { JackyunHttpSession } from "../lib/jackyun/direct-http";
import { inspectSubmittedApiTask, runApiExports } from "../tools/jackyun-api-export";
import { selectNewWebSessionTask } from "../lib/jackyun/web-session-export";
import { jackyunCaptureDate } from "../lib/jackyun/run-contract";

const templates = JSON.parse(await readFile(new URL("../config/jackyun-api-templates.json", import.meta.url), "utf8")) as ApiTemplates;
const fields = { visible: ["coupon_password", "creditOverdueLimit"], write: ["creditOverdueLimit"] };
const modules = ["branch_stock", "goods_managet_combination", "order_detail_list", "warehouse_age_analysis", "goods_managet_query"];
const functions = ["branch_stock_export", "goods_managet_combination_export", "goods_managet_query_export", "oms_order_detail_list_export"];
const scope = { warehouseIds: Array.from({ length: 256 }, (_, i) => String(i + 1)), ownerId: "123", permissionSha256: templates.permissionFieldsSha256, observedAt: new Date().toISOString() };
const today = jackyunCaptureDate(new Date().toISOString());
const yesterday = new Date(Date.parse(today + "T00:00:00Z") - 86400000).toISOString().slice(0, 10);
function fake(overrides: Record<string, unknown> = {}) {
  const calls: string[] = [];
  const http = { get serverClock() { const now = new Date().toISOString(); return { requestStartedAt: now, receivedAt: now, serverDate: new Date(Math.floor(Date.now() / 1000) * 1000).toISOString() }; },
    request: async (operation: string) => {
      calls.push(operation);
      if (operation === "submitExport") throw new Error("SUBMIT_RESPONSE_LOST");
      const data: Record<string, unknown> = {
        rolePermissions: modules.map(moduleCode => ({ moduleCode, hasPermission: 1, isBuy: 1 })),
        roleFunctions: functions.map(funCode => ({ funCode })), dataFieldPermissions: fields,
        warehouses: scope.warehouseIds.map(warehouseId => ({ warehouseId, warehouseTypeCode: "1" })),
        owners: [{ id: "123", jlinkOwnerId: "123", name: "自营", memberName: "fixture" }],
        inventoryCount: "25734", goodsCount: 1942, ageCount: 5684, salesCount: "6556", validateExport: null, tasks: [], ...overrides,
      };
      const result = data[operation];
      return { data: result, pageInfo: operation === "tasks" ? { total: Array.isArray(result) ? result.length : 0 } : null,
        noPrivilegeItem: null, desensitizationItem: null };
    } } as unknown as JackyunHttpSession;
  return { http, calls };
}

test("API metadata requires unique permissions, complete warehouses and the current tenant owner", async () => {
  const result = await readApiScope(fake().http, templates, "fixture");
  assert.equal(result.warehouseIds.length, 256);
  assert.equal(result.ownerId, "123");
  for (const override of [
    { rolePermissions: modules.slice(1).map(moduleCode => ({ moduleCode, hasPermission: 1, isBuy: 1 })) },
    { roleFunctions: [] }, { dataFieldPermissions: { visible: [], write: [] } },
    { warehouses: [{ warehouseId: "1", warehouseTypeCode: "1" }] },
    { owners: [{ id: "123", jlinkOwnerId: "123", name: "自营", memberName: "wrong" }] },
  ]) await assert.rejects(readApiScope(fake(override).http, templates, "fixture"));
  assert.equal(apiSha(permissionCanonical({ write: fields.write, visible: [...fields.visible].reverse() })), templates.permissionFieldsSha256);
});

test("API count and export scopes agree; shipment dates roll across month/leap-year boundaries", () => {
  for (const end of ["2026-09-07", "2026-08-31", "2024-02-29"]) {
    const p = buildApiParameters("sales", templates.modules.sales, scope, end);
    const filter = JSON.parse(p.query.jsonStr);
    assert.deepEqual(filter, JSON.parse(p.data.conditionJson).filterOrderDetailDto);
    assert.equal(filter.timeBegin, end.slice(0, 8) + "01 00:00:00");
    assert.equal(filter.timeEnd, end + " 23:59:59");
    assert.equal(String(filter.timeType), "4");
  }
  for (const moduleKey of ["inventory", "inventory_age", "products", "combos"] as const) {
    const p = buildApiParameters(moduleKey, templates.modules[moduleKey], scope, yesterday);
    assert.equal(p.data.isSyn, "false");
    assert.ok(!JSON.stringify(p).includes("@warehouse"));
    const bad = structuredClone(templates.modules[moduleKey]); bad.query.unexpected = "1";
    assert.throws(() => buildApiParameters(moduleKey, bad, scope, yesterday));
  }
  const bad = structuredClone(templates.modules.sales); const filter = JSON.parse(bad.query.jsonStr); filter.timeType = 1; bad.query.jsonStr = JSON.stringify(filter);
  assert.throws(() => buildApiParameters("sales", bad, scope, yesterday));
  assert.throws(() => buildApiParameters("sales", templates.modules.sales, scope, "2026-02-29"));
});

test("API totals fail before any submission for partial scopes, over-limit exports or combo image truncation", async () => {
  assert.equal((await prepareApiExport(fake().http, templates, scope, "inventory", today, yesterday)).sourceRows, 25734);
  for (const count of [0, 19999, 500001, 20000.5, "NaN", "20000x"]) {
    await assert.rejects(prepareApiExport(fake({ inventoryCount: count }).http, templates, scope, "inventory", today, yesterday));
  }
  await assert.rejects(prepareApiExport(fake({ goodsCount: 2001 }).http, templates, scope, "combos", today, yesterday), /TRUNCATE/);
});

test("platform time calibration finds a new task one second behind local time without choosing the baseline", () => {
  const intent = "2026-09-08T04:43:10.088Z";
  const clock = { requestStartedAt: "2026-09-08T04:43:09.700Z", receivedAt: "2026-09-08T04:43:10.000Z", serverDate: "2026-09-08T04:43:09.000Z" };
  const task = { taskId: "sys-123", label: "【导出任务-密文】分仓库存查询(25734条)", createdAt: Date.parse(clock.serverDate), completed: true, urls: ["https://oss.example.invalid/a.xlsx?signature=fixture"] };
  const expected = { module: "inventory" as const, sourceRows: 25734, exportIntentAt: apiTaskWindowStart(clock, intent), observedAt: "2026-09-08T04:44:00Z", allowedHosts: ["oss.example.invalid"], baselineIds: ["sys-100"] };
  assert.equal(selectNewWebSessionTask({ records: [{ ...task, taskId: "sys-100" }, task], failedIds: [] }, expected).result?.binding.taskId, "sys-123");
  assert.throws(() => selectNewWebSessionTask({ records: [task, { ...task, taskId: "sys-124" }], failedIds: [] }, expected), /不唯一/);
  for (const bad of [undefined, { ...clock, serverDate: "2026-09-07T00:00:00Z" }, { ...clock, receivedAt: "2026-09-08T04:43:20Z" }]) assert.throws(() => apiTaskWindowStart(bad, intent));
});

test("platform task observations use the same clock when the server is ahead of the local host", () => {
  const clock = { requestStartedAt: "2026-09-15T16:10:12.891Z", receivedAt: "2026-09-15T16:10:13.099Z", serverDate: "2026-09-15T16:10:14.000Z" };
  const intent = "2026-09-15T16:10:13.101Z";
  const observed = "2026-09-15T16:10:13.200Z";
  assert.equal(apiTaskWindowStart(clock, intent), clock.serverDate);
  assert.equal(apiTaskWindowObserved(clock, observed), "2026-09-15T16:10:14.101Z");
  assert.throws(() => selectNewWebSessionTask({ records: [], failedIds: [] }, { module: "inventory", sourceRows: 25871,
    exportIntentAt: apiTaskWindowStart(clock, intent), observedAt: observed, allowedHosts: ["oss.example.invalid"], baselineIds: [] }), /绑定条件/);
  assert.doesNotThrow(() => selectNewWebSessionTask({ records: [], failedIds: [] }, { module: "inventory", sourceRows: 25871,
    exportIntentAt: apiTaskWindowStart(clock, intent), observedAt: apiTaskWindowObserved(clock, observed),
    allowedHosts: ["oss.example.invalid"], baselineIds: [] }));
  assert.throws(() => apiTaskWindowObserved(clock, "2026-09-15T16:10:12.000Z"));
});

test("submitted API task inspection binds the original completed task without another export POST", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jackyun-api-inspect-"));
  const runId = "n8n-export-first-2285";
  const directory = path.join(root, runId);
  await mkdir(directory);
  const clock = { requestStartedAt: "2026-09-15T16:10:12.891Z", receivedAt: "2026-09-15T16:10:13.099Z", serverDate: "2026-09-15T16:10:14.000Z" };
  await writeFile(path.join(directory, "api-controller-state.json"), JSON.stringify({ version: 1, runId, transport: "session_api_v1",
    runDate: "2026-09-16", asOfDate: "2026-09-15", templateSha256: apiSha(JSON.stringify(templates)), tenantId: "fixture",
    modules: { inventory: { status: "submitted", serverClock: clock, preflightStartedAt: "2026-09-15T16:10:12.164Z",
      queryIntentAt: "2026-09-15T16:10:12.164Z", queryCompletedAt: "2026-09-15T16:10:12.768Z", sourceRows: 25871,
      payloadSha256: "a".repeat(64), querySha256: "b".repeat(64), permissionSha256: templates.permissionFieldsSha256,
      templateSha256: apiSha(JSON.stringify(templates)), baselineIds: ["sys-100"], exportIntentAt: "2026-09-15T16:10:13.101Z" } } }));
  const f = fake({ tasks: [{ id: "101", gmtCreate: Date.parse(clock.serverDate), taskTitle: "【成功】【导出任务-密文】分仓库存查询(25871条)",
    taskStatus: 4, attachmentList: [{ attachmentUrl: "https://oss.example.invalid/inventory.xlsx?signature=fixture" }] }] });
  const binding = await inspectSubmittedApiTask({ runId, outputRoot: root, allowedHosts: ["oss.example.invalid"] }, { http: f.http, tenantId: "fixture" });
  assert.equal(binding.taskId, "sys-101");
  assert.equal(binding.sourceRows, 25871);
  assert.deepEqual(f.calls, ["tasks"]);
  const rebound = await inspectSubmittedApiTask({ runId, outputRoot: root, allowedHosts: ["oss.example.invalid"], binding },
    { http: f.http, tenantId: "fixture" });
  assert.deepEqual(rebound, binding);
  await assert.rejects(inspectSubmittedApiTask({ runId, outputRoot: root, allowedHosts: ["oss.example.invalid"],
    binding: { ...binding, taskId: "sys-999" } }, { http: f.http, tenantId: "fixture" }), /替换/);
});

test("an uncertain API submission is durably fenced and resumes polling without replaying its POST", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jackyun-api-fence-"));
  const options = { runId: "api-fence-test", runDate: today, asOfDate: yesterday, outputRoot: root, eventRoot: root + "/events", downloadDirectory: root + "/downloads" };
  const f = fake(); const deps = { http: f.http, tenantId: "fixture", templates, taskTimeoutMs: 5, pollIntervalMs: 1 };
  await assert.rejects(runApiExports(options, deps), /SUBMIT_RESPONSE_LOST/);
  const state = JSON.parse(await readFile(path.join(root, options.runId, "api-controller-state.json"), "utf8"));
  assert.equal(state.modules.inventory.status, "submit_intent");
  assert.equal(state.tenantId, "fixture");
  assert.ok(state.modules.inventory.exportIntentAt);
  const callsBeforeWrongTenant = f.calls.length;
  await assert.rejects(runApiExports(options, { ...deps, tenantId: "another-tenant" }), /TENANT_BINDING_CHANGED/);
  assert.equal(f.calls.length, callsBeforeWrongTenant);
  await assert.rejects(runApiExports(options, deps), /ORIGINAL_EXPORT_TASK_PENDING/);
  assert.equal(f.calls.filter(operation => operation === "submitExport").length, 1);
  assert.equal(f.calls.filter(operation => operation === "inventoryCount").length, 1);
});

test("generated API n8n graph retains the validation barrier and disables automatic POST retries", async () => {
  const workflow = JSON.parse(await readFile(new URL("../automation/n8n/jackyun-five-dataset-api.workflow.json", import.meta.url), "utf8"));
  assert.equal(workflow.active, false);
  const http = workflow.nodes.filter((node: { type: string; parameters?: { url?: string } }) => node.type === "n8n-nodes-base.httpRequest" && node.parameters?.url?.includes("/jackyun/export-first/"));
  assert.equal(http.length, 5);
  assert.ok(JSON.stringify(http).includes("plan-api"));
  assert.ok(!JSON.stringify(http).includes("plan-direct-http"));
  assert.ok(http.every((node: { retryOnFail?: boolean }) => !node.retryOnFail));
});
