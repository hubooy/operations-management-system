import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { importRunDuration, importRunStatus } from "../lib/imports/run-presentation";
import { normalizeShellLocation } from "../app/shell/navigation-contract";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ImportChainRulesView from "../app/import-chain-rules-view";
import { completedAtLabel, formatChainStatusTime, todayStatusLabel, validateTodayStatus, type ChainTodayResponse } from "../lib/imports/chain-status";

test("in-flight, failed and unknown imports never acquire a green completed status", () => {
  for (const status of ["processing", "running", "pending", "queued", "rejected", "failed", "expired", "future_status", ""]) {
    assert.notEqual(importRunStatus(status, 0).tone, "success");
    assert.notEqual(importRunStatus(status, 3).tone, "success");
  }
  assert.equal(importRunStatus("completed").group, "completed");
  assert.equal(importRunStatus("completed", 3).group, "warning");
  assert.equal(importRunStatus("duplicate").group, "duplicate");
  assert.equal(importRunStatus("partial").group, "warning");
});

test("duration leaves missing, invalid and reversed timestamps unknown", () => {
  assert.equal(importRunDuration("2026-09-10T00:00:00Z", null), "—");
  assert.equal(importRunDuration("invalid", "invalid"), "—");
  assert.equal(importRunDuration("2026-09-10T00:01:00Z", "2026-09-10T00:00:00Z"), "—");
  assert.equal(importRunDuration("2026-09-10T00:00:00Z", "2026-09-10T00:01:20Z"), "1 分 20 秒");
});

test("removed continuity links normalize safely and chain links survive refresh", () => {
  assert.equal(normalizeShellLocation("/?module=import&view=continuity"), "/?module=import");
  assert.equal(normalizeShellLocation("/?module=import&view=chains"), "/?module=import&view=chains");
  assert.equal(normalizeShellLocation("/?module=import&view=history"), "/?module=import&view=history");
});

test("display catalog matches allowlisted workflow definitions without bundling operational configuration", async () => {
  const { buildImportChainCatalog } = await import("../tools/build-import-chain-catalog.mjs");
  const text = await readFile(new URL("../lib/imports/chain-catalog.generated.json", import.meta.url), "utf8");
  const catalog = JSON.parse(text);
  assert.deepEqual(catalog, await buildImportChainCatalog());
  const backend = JSON.parse(await readFile(new URL("../backend/workflow/import_chain_catalog.json", import.meta.url), "utf8"));
  assert.deepEqual(backend.workflowIds, catalog.rules.map((r: { workflowId: string }) => r.workflowId));
  assert.equal(new Set(catalog.rules.map((r: { workflowId: string }) => r.workflowId)).size, catalog.rules.length);
  const jdRule = catalog.rules.find((r: { chainKey: string }) => r.chainKey === "jd");
  assert.equal(jdRule?.workflowId, "JdN8nSilentCopy2026");
  assert.equal(catalog.rules.some((r: { workflowId: string }) => r.workflowId === "JdN8nM3uL7tI2026"), false);
  assert.doesNotMatch(text, /userDataDir|profileDir|debugPort|downloadDir|credentials|httpRequest|localhost:5791/);
  assert.doesNotMatch(text, /"active"|"lastRun"|"nextRun"/);
  const entities = new Set(catalog.entities.map((e: { key: string }) => e.key));
  for (const rule of catalog.rules) {
    assert.ok(rule.entityKeys.every((key: string) => entities.has(key)));
    assert.ok(catalog.chains.some((c: { key: string }) => c.key === rule.chainKey));
  }
});

test("matrix renders workflows across columns and shops down rows", () => {
  const html = renderToStaticMarkup(createElement(ImportChainRulesView, { currentUser: null }));
  const head = html.match(/<thead>([\s\S]*?)<\/thead>/)?.[1] || "";
  const body = html.match(/<tbody>([\s\S]*?)<\/tbody>/)?.[1] || "";
  assert.match(head, /n8n 工作流/);
  assert.match(head, /京东 · 商品数据/);
  assert.doesNotMatch(head, /志高商用设备旗舰店/);
  assert.match(body, /<th scope="row"><strong>志高商用设备旗舰店/);
  assert.match(body, /读取今天状态/);
});

test("today status rejects impossible completion and shows later failure distinctly", () => {
  assert.equal(formatChainStatusTime("2026-09-09T16:30:00+00:00"), "09/10 00:30");
  const item = { workflowId: "test", active: true, state: "failed" as const, completedToday: true, completedAt: "2026-09-10T01:00:00Z", executionId: "1", startedAt: null, finishedAt: null };
  const response: ChainTodayResponse = { date: "2026-09-10", timezone: "Asia/Shanghai", checkedAt: "2026-09-10T02:00:00Z", source: "n8n_execution_metadata", items: [item] };
  assert.equal(validateTodayStatus(response), true);
  assert.equal(todayStatusLabel(item).tone, "danger");
  assert.equal(todayStatusLabel(item).label, "最近自动执行失败");
  assert.equal(todayStatusLabel().label, "今天：无法核实");
  assert.equal(validateTodayStatus({ ...response, items: [{ ...item, state: "completed", completedAt: null }] }), false);
  assert.equal(validateTodayStatus({ ...response, items: [item, item] }), false);
});

test("today status distinguishes scheduled completion from an automatic retry", () => {
  const base = { workflowId: "test", active: true, state: "completed" as const, completedToday: true,
    completedAt: "2026-09-10T01:00:00Z", executionId: "1", startedAt: "2026-09-10T00:00:00Z", finishedAt: "2026-09-10T01:00:00Z" };
  const scheduled = { ...base, completedMode: "trigger" as const, executionMode: "trigger" as const };
  const retried = { ...base, completedMode: "webhook" as const, executionMode: "webhook" as const };
  assert.equal(todayStatusLabel(scheduled).label, "今天定时已完成");
  assert.equal(completedAtLabel(scheduled), "今日定时完成于");
  assert.equal(todayStatusLabel(retried).label, "今天重试已完成");
  assert.equal(completedAtLabel(retried), "今日重试完成于");
});
