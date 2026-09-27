import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { planTmallDailyGaps } from "../tools/tmall-daily-gap-plan";
import { advanceTmallBackfill, beginTmallBackfill, tmallBackfillMaximumMs } from "../tools/tmall-daily-backfill";
import { helperRequestError, tmallStoreContextError } from "../tools/tmall-sycm-cookie-pipeline";
import { buildTmallN8nWorkflow, tmallN8nWorkflowDefinitions, buildTmallYijiuDirectPmCandidateWorkflow, buildTmallYiyongDirectPmCandidateWorkflow } from "../tools/generate-tmall-n8n-workflows";

const days = Array.from({ length: 20 }, (_, i) => `2026-09-${String(i + 1).padStart(2, "0")}`);
function plan(products: string[], promotions = products) {
  const range = { startDate: days[0]!, endDate: days.at(-1)! };
  return { ...range, ...planTmallDailyGaps({ ...range, productDailyDates: products, promotionDates: promotions, maximumDays: 1 }), planPathBase64: "test-plan" };
}
const identity = { executionId: "100", storeKey: "tmall-lili" };
test("逐日循环只接受两类覆盖均完成；成功后从新覆盖选择下一日", () => {
  let state = beginTmallBackfill(identity.executionId, identity.storeKey, plan([], days), 0);
  assert.deepEqual(state.currentDates, [days[0]]);
  state = advanceTmallBackfill(state, { ...identity, cycle: "0", now: 100, plan: plan([days[0]!], days) });
  assert.deepEqual(state.completedDates, [days[0]]);
  assert.deepEqual(state.currentDates, [days[1]]);
  assert.throws(() => advanceTmallBackfill(state, { ...identity, cycle: "1", now: 200, plan: plan([days[0]!], days) }), /无进展/);
  assert.equal(state.cycle, 1);
});
test("拒绝错 execution、错店、重复/过期循环序号、并发、日期范围扩大和时钟回退", () => {
  const initial = beginTmallBackfill(identity.executionId, identity.storeKey, plan([]), 100);
  const valid = { ...identity, cycle: "0", now: 200, plan: plan([days[0]!]) };
  for (const invalid of [{ executionId: "101" }, { storeKey: "tmall-yiyong" }, { cycle: "1" }, { cycle: ["0"] },
    { cycle: undefined }, { now: 99 }, { plan: { ...valid.plan, endDate: "2026-09-21" } }]) {
    assert.throws(() => advanceTmallBackfill(initial, { ...valid, ...invalid }), /不一致/);
  }
  const advanced = advanceTmallBackfill(initial, valid);
  assert.throws(() => advanceTmallBackfill(advanced, valid), /不一致/);
  for (const route of ["/plan-backfill", "/next-day"] as const) {
    assert.ok(helperRequestError("promoted", true, route, "100", "100"));
    assert.ok(helperRequestError("promoted", false, route, "101", "100"));
  }
  for (const stage of ["ready", "planned", "fetched", "imported", "completed", "failed"] as const) {
    assert.ok(helperRequestError(stage, false, "/next-day", "100", "100"));
  }
  assert.equal(helperRequestError("promoted", false, "/next-day", "100", "100"), null);
  assert.ok(tmallStoreContextError("tmall-lili", "tmall-yiyong"));
});
test("14 天或 30 分钟预算到达后保存剩余缺口，不能误报完成或继续推进", () => {
  let state = beginTmallBackfill(identity.executionId, identity.storeKey, plan([]), 0);
  for (let i = 0; i < 14; i++) state = advanceTmallBackfill(state, { ...identity, cycle: String(i), now: 100 + i, plan: plan(days.slice(0, i + 1)) });
  assert.equal(state.status, "budget_exhausted");
  assert.equal(state.budgetReason, "days");
  assert.deepEqual(state.currentDates, []);
  assert.deepEqual(state.remainingDates, days.slice(14));
  assert.throws(() => advanceTmallBackfill(state, { ...identity, cycle: "14", now: 200, plan: plan(days.slice(0, 15)) }));
  const initial = beginTmallBackfill(identity.executionId, identity.storeKey, plan([]), 0);
  const timed = advanceTmallBackfill(initial, { ...identity, cycle: "0", now: tmallBackfillMaximumMs, plan: plan([days[0]!]) });
  assert.equal(timed.status, "budget_exhausted");
  assert.equal(timed.budgetReason, "time");
  assert.equal(advanceTmallBackfill(initial, { ...identity, cycle: "0", now: tmallBackfillMaximumMs, plan: plan(days) }).status, "completed");
});
test("零缺口仍经过 P 活动清单检查与最终覆盖复验；新 execution 从实际未完成日恢复", () => {
  const empty = beginTmallBackfill(identity.executionId, identity.storeKey, plan(days), 0);
  assert.equal(empty.status, "running");
  assert.equal(advanceTmallBackfill(empty, { ...identity, cycle: "0", now: 10, plan: plan(days) }).status, "completed");
  const recovered = beginTmallBackfill("101", identity.storeKey, plan(days.slice(0, 2), [days[0]!]), 200);
  assert.deepEqual(recovered.currentDates, [days[1]]);
  assert.deepEqual(plan(days.slice(0, 2), [days[0]!]).productDownloadDates, []);
});
test("所有现行六店模板走逐日循环，M 只在退出循环后执行且预算未完成分支失败", async () => {
  const source = JSON.parse(await readFile(new URL("../automation/n8n/tmall-yijiu-sycm-cookie-daily.workflow.json", import.meta.url), "utf8"));
  for (const definition of tmallN8nWorkflowDefinitions) {
    const workflow = definition.storeKey === "tmall-yijiu" ? buildTmallYijiuDirectPmCandidateWorkflow(source)
      : definition.storeKey === "tmall-yiyong" ? buildTmallYiyongDirectPmCandidateWorkflow(source) : buildTmallN8nWorkflow(source, definition);
    const edges = workflow.connections as Record<string, { main: { node: string }[][] }>;
    const promotion = workflow.nodes.find(n => n.name.startsWith("P·"))!;
    const master = workflow.nodes.find(n => n.name.startsWith("M·"))!;
    assert.equal(edges[promotion.name]!.main[0]![0]!.node, "N·复查缺口并计划下一日");
    assert.equal(edges["还有缺口且预算充足？"]!.main[0]![0]!.node, "B·逐日下载并验证 XLS");
    assert.equal(edges["还有缺口且预算充足？"]!.main[1]![0]!.node, master.name);
    assert.equal(edges[master.name]!.main[0]![0]!.node, "全部缺失日已补齐？");
    assert.equal(edges["全部缺失日已补齐？"]!.main[1]![0]!.node, "补缺未完成·已释放店铺资源");
    assert.equal(new Set(workflow.nodes.map(n => n.name)).size, workflow.nodes.length);
    assert.equal(workflow.nodes.find(n => n.name === "补缺未完成·已释放店铺资源")?.type, "n8n-nodes-base.stopAndError");
    assert.equal(workflow.nodes.find(n => n.name === "A·计划目标日期")?.parameters?.url, "http://127.0.0.1:5791/plan-backfill");
  }
});
