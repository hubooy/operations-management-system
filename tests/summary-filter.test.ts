import test from "node:test";
import assert from "node:assert/strict";
import { toggleSingleFilter, toggleFilterGroup, workCardStatuses, applyWorkCardDates } from "../lib/ui/summary-filter";

test("classification cards select, switch and cancel without changing other filters", () => {
  let state = { owner: "运营甲", status: "" };
  for (const [clicked, expected] of [["active", "active"], ["completed", "completed"], ["completed", ""]]) {
    state = { ...state, status: toggleSingleFilter(state.status, clicked!, "") };
    assert.equal(state.status, expected);
    assert.equal(state.owner, "运营甲");
  }
  assert.deepEqual(toggleFilterGroup(["warning", "urgent"], ["urgent", "warning"]), []);
  assert.deepEqual(toggleFilterGroup(["healthy"], ["urgent"]), ["urgent"]);
});

test("work cards cancel to all statuses and use exclusive Shanghai date bounds", () => {
  assert.deepEqual(workCardStatuses("all"), ["待开始", "工作中", "已完成"]);
  assert.deepEqual(workCardStatuses("today"), ["待开始", "工作中"]);
  assert.deepEqual(workCardStatuses("overdue"), ["待开始", "工作中"]);
  const params = new URLSearchParams({ owner: "运营甲", dueFrom: "2026-09-01", dueTo: "2026-10-01" });
  applyWorkCardDates(params, "today", "2026-09-14", "2026-09-15");
  assert.equal(params.get("dueFrom"), "2026-09-14");
  assert.equal(params.get("dueTo"), "2026-09-15");
  assert.equal(params.get("owner"), "运营甲");
  const older = new URLSearchParams({ dueTo: "2026-09-10" });
  applyWorkCardDates(older, "overdue", "2026-09-14", "2026-09-15");
  assert.equal(older.get("dueTo"), "2026-09-10");
});
