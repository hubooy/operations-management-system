import assert from "node:assert/strict";
import test from "node:test";

import { confirmAndSyncDraftPlans, retainFailedPlanSelections } from "../lib/inventory/replenishment-batch";

const plans = [
  { id: "draft-1", plannedQuantity: 10 },
  { id: "draft-2", plannedQuantity: 20 },
  { id: "draft-3", plannedQuantity: 30 },
];

test("草稿批量确认后逐条提交钉钉并保留失败项", async () => {
  const calls: string[] = [];
  const results = await confirmAndSyncDraftPlans(
    plans,
    async (plan) => { calls.push(`confirm:${plan.id}:${plan.plannedQuantity}`); },
    async (plan) => {
      calls.push(`sync:${plan.id}`);
      if (plan.id === "draft-2") throw new Error("钉钉暂时不可用");
    },
    () => false,
  );

  assert.deepEqual(calls, [
    "confirm:draft-1:10", "sync:draft-1",
    "confirm:draft-2:20", "sync:draft-2",
    "confirm:draft-3:30", "sync:draft-3",
  ]);
  assert.deepEqual(results.map(({ id, ok, confirmed }) => ({ id, ok, confirmed })), [
    { id: "draft-1", ok: true, confirmed: true },
    { id: "draft-2", ok: false, confirmed: true },
    { id: "draft-3", ok: true, confirmed: true },
  ]);
});

test("授权类失败停止后续确认且所有未完成项保持失败", async () => {
  const confirmed: string[] = [];
  const results = await confirmAndSyncDraftPlans(
    plans,
    async (plan) => { confirmed.push(plan.id); },
    async () => { throw new Error("钉钉授权已失效"); },
    (message) => /授权已失效/.test(message),
  );

  assert.deepEqual(confirmed, ["draft-1"]);
  assert.deepEqual(results.map(({ id, confirmed: localConfirmed }) => ({ id, localConfirmed })), [
    { id: "draft-1", localConfirmed: true },
    { id: "draft-2", localConfirmed: false },
    { id: "draft-3", localConfirmed: false },
  ]);
  assert.ok(results.every((result) => !result.ok));
});

test("混合选择只移除本次成功项并保留另一状态与失败项", () => {
  assert.deepEqual(retainFailedPlanSelections(
    ["confirmed-kept", "confirmed-kept"],
    [
      { id: "draft-success", ok: true },
      { id: "draft-failed", ok: false },
    ],
  ), ["confirmed-kept", "draft-failed"]);
});
