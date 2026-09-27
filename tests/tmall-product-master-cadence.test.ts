import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import type { TmallStore } from "../lib/netshop/tmall-store-registry";
import {
  addIsoCalendarDays,
  decideTmallProductMasterCadence,
  getTmallProductMasterCadenceDecision,
  loadTmallProductMasterCadenceState,
  migrateTmallProductMasterCadenceInterval,
  parseTmallForceProductMasterHeader,
  recordTmallProductMasterCadenceSuccess,
  shanghaiBusinessDate,
} from "../tools/tmall-product-master-cadence";
import { runTmallProductMasterTerminalStage } from "../tools/tmall-sycm-cookie-pipeline";

function store(overrides: Partial<TmallStore> = {}): TmallStore {
  return {
    storeKey: "tmall-test",
    platform: "天猫",
    shopName: "天猫-测试店",
    enabled: true,
    loginMode: "windows_dpapi_credentials",
    productMasterCadence: { intervalDays: 3, initialDueDate: "2026-08-25" },
    initialStartDate: "2026-08-01",
    portalUrl: "https://sycm.taobao.com/portal/home.htm",
    browser: { profileDir: "test", debugPort: 9399, downloadDir: "test" },
    ...overrides,
  };
}

test("天猫 M 三日节奏按上海日期到期，跨月仍精确增加三个日历日", () => {
  assert.equal(shanghaiBusinessDate(new Date("2026-08-24T16:30:00.000Z")), "2026-08-25");
  assert.equal(addIsoCalendarDays("2026-08-30", 3), "2026-09-02");
  const beforeDue = decideTmallProductMasterCadence({
    store: store(), operationDate: "2026-08-24", state: null,
  });
  assert.deepEqual({ due: beforeDue.due, reason: beforeDue.reason, nextDueDate: beforeDue.nextDueDate }, {
    due: false, reason: "not_due", nextDueDate: "2026-08-25",
  });
  const due = decideTmallProductMasterCadence({
    store: store(), operationDate: "2026-08-25", state: null,
  });
  assert.deepEqual({ due: due.due, reason: due.reason }, { due: true, reason: "scheduled" });
});

test("M 到期失败不推进日期，成功后从实际完成日再顺延三天", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "tmall-master-cadence-"));
  try {
    const target = store();
    const firstDue = decideTmallProductMasterCadence({
      store: target, operationDate: "2026-08-25", state: null,
    });
    assert.equal(firstDue.due, true);
    assert.equal(await loadTmallProductMasterCadenceState(target, root), null);

    const retryDue = decideTmallProductMasterCadence({
      store: target, operationDate: "2026-08-26", state: null,
    });
    assert.equal(retryDue.due, true);
    const recorded = await recordTmallProductMasterCadenceSuccess({
      store: target,
      decision: retryDue,
      snapshotDate: "2026-08-25",
      stateDirectory: root,
      updatedAt: "2026-08-26T06:00:00.000Z",
    });
    assert.equal(recorded?.lastSuccessDate, "2026-08-26");
    assert.equal(recorded?.lastSnapshotDate, "2026-08-25");
    assert.equal(recorded?.nextDueDate, "2026-08-29");
    const persisted = await loadTmallProductMasterCadenceState(target, root);
    assert.equal(persisted?.nextDueDate, "2026-08-29");
    assert.equal(decideTmallProductMasterCadence({
      store: target, operationDate: "2026-08-28", state: persisted,
    }).due, false);
    assert.equal(decideTmallProductMasterCadence({
      store: target, operationDate: "2026-08-29", state: persisted,
    }).due, true);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("亿玖从三日节奏迁移为每日时使用最后成功日计算下一到期日并做 CAS 校验", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "tmall-master-cadence-migrate-"));
  try {
    await writeFile(path.join(root, "tmall-yijiu.json"), JSON.stringify({
      version: 1,
      storeKey: "tmall-yijiu",
      intervalDays: 3,
      lastSuccessDate: "2026-09-03",
      lastSnapshotDate: "2026-09-03",
      nextDueDate: "2026-09-06",
      updatedAt: "2026-09-03T07:07:03.024Z",
    }), "utf8");
    const target = store({
      storeKey: "tmall-yijiu",
      shopName: "天猫-志高亿玖专卖店",
      productMasterCadence: { intervalDays: 1, initialDueDate: "2026-08-27" },
    });
    const migrated = await migrateTmallProductMasterCadenceInterval({
      store: target,
      expectedPreviousIntervalDays: 3,
      expectedLastSuccessDate: "2026-09-03",
      stateDirectory: root,
      updatedAt: "2026-09-03T08:00:00.000Z",
    });
    assert.deepEqual(migrated, {
      version: 1,
      storeKey: "tmall-yijiu",
      intervalDays: 1,
      lastSuccessDate: "2026-09-03",
      lastSnapshotDate: "2026-09-03",
      nextDueDate: "2026-09-04",
      updatedAt: "2026-09-03T08:00:00.000Z",
    });
    await assert.rejects(migrateTmallProductMasterCadenceInterval({
      store: target,
      expectedPreviousIntervalDays: 3,
      expectedLastSuccessDate: "2026-09-02",
      stateDirectory: root,
    }), /状态与店铺配置不一致|最后成功日期已变化/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("手动强制和未决活动清单都会越过未到期门禁，未配置店铺保持每日兼容", () => {
  const target = store();
  assert.equal(decideTmallProductMasterCadence({
    store: target, operationDate: "2026-08-24", state: null, forced: true,
  }).reason, "forced");
  assert.equal(decideTmallProductMasterCadence({
    store: target, operationDate: "2026-08-24", state: null, pendingAudit: true,
  }).reason, "pending_audit");
  const compatibility = decideTmallProductMasterCadence({
    store: store({ productMasterCadence: undefined }), operationDate: "2026-08-24", state: null,
  });
  assert.equal(compatibility.due, true);
  assert.equal(compatibility.reason, "daily_compatibility");
});

test("未决活动清单从任一 M 策略目录命中都会要求续接", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "tmall-master-audit-"));
  const stateRoot = await mkdtemp(path.join(tmpdir(), "tmall-master-state-"));
  const productManager = path.join(root, "manager");
  const pagewise = path.join(root, "pagewise");
  const directMtop = path.join(root, "direct-mtop");
  try {
    await mkdir(pagewise, { recursive: true });
    await writeFile(path.join(pagewise, "active-tmall-test.json"), "{}", "utf8");
    const decision = await getTmallProductMasterCadenceDecision({
      store: store(),
      now: new Date("2026-08-24T06:00:00.000Z"),
      stateDirectory: stateRoot,
      auditDirectories: [productManager, pagewise, directMtop],
    });
    assert.equal(decision.due, true);
    assert.equal(decision.reason, "pending_audit");
    await rm(path.join(pagewise, "active-tmall-test.json"));
    await mkdir(directMtop, { recursive: true });
    await writeFile(path.join(directMtop, "active-tmall-test.json"), "{}", "utf8");
    const directDecision = await getTmallProductMasterCadenceDecision({
      store: store(),
      now: new Date("2026-08-24T06:00:00.000Z"),
      stateDirectory: stateRoot,
      auditDirectories: [productManager, pagewise, directMtop],
    });
    assert.equal(directDecision.reason, "pending_audit");
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(stateRoot, { recursive: true, force: true });
  }
});

test("节奏状态损坏、跨店或请求头歧义都失败关闭", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "tmall-master-corrupt-"));
  try {
    await writeFile(path.join(root, "tmall-test.json"), JSON.stringify({
      version: 1,
      storeKey: "tmall-other",
      intervalDays: 3,
      lastSuccessDate: "2026-08-24",
      lastSnapshotDate: "2026-08-24",
      nextDueDate: "2026-08-27",
      updatedAt: "2026-08-24T06:00:00.000Z",
    }), "utf8");
    await assert.rejects(loadTmallProductMasterCadenceState(store(), root), /状态与店铺配置不一致/);
    await writeFile(path.join(root, "tmall-test.json"), "{bad-json", "utf8");
    await assert.rejects(loadTmallProductMasterCadenceState(store(), root), /状态无法解析/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
  assert.equal(parseTmallForceProductMasterHeader(undefined), false);
  assert.equal(parseTmallForceProductMasterHeader("0"), false);
  assert.equal(parseTmallForceProductMasterHeader("1"), true);
  assert.throws(() => parseTmallForceProductMasterHeader("true"), /请求头无效/);
  assert.throws(() => parseTmallForceProductMasterHeader(["1"]), /请求头无效/);
});

test("M 未到期不调用任何导出器，但仍关闭本店受控 Chromium", async () => {
  let productManagerRuns = 0;
  let pagewiseRuns = 0;
  let stateWrites = 0;
  let browserCloses = 0;
  const decision = decideTmallProductMasterCadence({
    store: store(), operationDate: "2026-08-24", state: null,
  });
  const result = await runTmallProductMasterTerminalStage({
    store: store(),
    forced: false,
    getDecision: async () => decision,
    runProductManager: async () => { productManagerRuns += 1; throw new Error("不应运行"); },
    runPagewise: async () => { pagewiseRuns += 1; throw new Error("不应运行"); },
    recordSuccess: async () => { stateWrites += 1; return null; },
    closeBrowser: async () => { browserCloses += 1; return { ok: true, status: "closed" }; },
  });
  assert.equal(result.status, "not_due");
  assert.equal(productManagerRuns, 0);
  assert.equal(pagewiseRuns, 0);
  assert.equal(stateWrites, 0);
  assert.equal(browserCloses, 1);
  assert.equal((result.cadence as { nextDueDate?: string }).nextDueDate, "2026-08-25");
});

test("M 未到期但本店 Chromium 关闭失败时不得伪报正常终态", async () => {
  const decision = decideTmallProductMasterCadence({
    store: store(), operationDate: "2026-08-24", state: null,
  });
  await assert.rejects(runTmallProductMasterTerminalStage({
    store: store(),
    forced: false,
    getDecision: async () => decision,
    runProductManager: async () => { throw new Error("不应运行"); },
    runPagewise: async () => { throw new Error("不应运行"); },
    recordSuccess: async () => null,
    closeBrowser: async () => { throw new Error("关闭失败"); },
  }), /关闭失败/);
});

test("到期 M 只有完整导入成功并推进节奏后才关闭浏览器返回成功", async () => {
  const target = store({ productMasterExportMode: "on_sale_pagewise_excel" });
  const decision = decideTmallProductMasterCadence({
    store: target, operationDate: "2026-08-25", state: null,
  });
  const calls: string[] = [];
  const result = await runTmallProductMasterTerminalStage({
    store: target,
    forced: false,
    getDecision: async () => decision,
    runProductManager: async () => { throw new Error("逐页店不得调用管家模式"); },
    runPagewise: async () => {
      calls.push("pagewise");
      return {
        ok: true, stage: "product_master", status: "imported", storeKey: target.storeKey,
        shopName: target.shopName, snapshotDate: "2026-08-25", batchId: "batch", rowCount: 20, warningCount: 0,
      };
    },
    recordSuccess: async () => {
      calls.push("state");
      return {
        version: 1, storeKey: target.storeKey, intervalDays: 3, lastSuccessDate: "2026-08-25",
        lastSnapshotDate: "2026-08-25", nextDueDate: "2026-08-28", updatedAt: "2026-08-25T06:00:00.000Z",
      };
    },
    closeBrowser: async () => { calls.push("close"); return { ok: true, status: "closed" }; },
  });
  assert.deepEqual(calls, ["pagewise", "state", "close"]);
  assert.equal(result.status, "imported");
  assert.equal((result.cadence as { nextDueDate?: string }).nextDueDate, "2026-08-28");
});

test("亿玖每日 M 到期时只调用 MTOP 直连导出器并把下一到期日推进到次日", async () => {
  const target = store({
    storeKey: "tmall-yijiu",
    shopName: "天猫-志高亿玖专卖店",
    productMasterCadence: { intervalDays: 1, initialDueDate: "2026-08-27" },
  });
  const decision = decideTmallProductMasterCadence({
    store: target, operationDate: "2026-08-27", state: null,
  });
  const calls: string[] = [];
  const result = await runTmallProductMasterTerminalStage({
    store: target,
    forced: false,
    mode: "direct_mtop",
    getDecision: async () => decision,
    runProductManager: async () => { throw new Error("直连现行流程不得调用商品管家"); },
    runPagewise: async () => { throw new Error("直连现行流程不得调用 UI 逐页导出"); },
    runDirect: async () => {
      calls.push("direct");
      return {
        ok: true, stage: "product_master", status: "duplicate", storeKey: target.storeKey,
        shopName: target.shopName, snapshotDate: "2026-08-27", batchId: "batch-direct", rowCount: 43, warningCount: 0,
      };
    },
    recordSuccess: async () => {
      calls.push("state");
      return {
        version: 1, storeKey: target.storeKey, intervalDays: 1, lastSuccessDate: "2026-08-27",
        lastSnapshotDate: "2026-08-27", nextDueDate: "2026-08-28", updatedAt: "2026-08-27T06:00:00.000Z",
      };
    },
    closeBrowser: async () => { calls.push("close"); return { ok: true, status: "closed" }; },
  });
  assert.deepEqual(calls, ["direct", "state", "close"]);
  assert.equal(result.status, "duplicate");
  assert.equal((result.cadence as { nextDueDate?: string }).nextDueDate, "2026-08-28");
});
