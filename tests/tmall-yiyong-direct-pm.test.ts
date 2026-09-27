import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import type { TmallStore } from "../lib/netshop/tmall-store-registry";
import { buildTmallN8nWorkflow, buildTmallYiyongDirectPmCandidateWorkflow, tmallN8nWorkflowDefinitions } from "../tools/generate-tmall-n8n-workflows";
import { assertNoLegacyMasterAction } from "../tools/tmall-direct-product-master-export";
import { directPromotionLegacyAuditBlocks } from "../tools/tmall-direct-promotion-export";
import { decideTmallProductMasterCadence, loadTmallProductMasterCadenceState, migrateTmallProductMasterCadenceInterval, recordTmallProductMasterCadenceSuccess } from "../tools/tmall-product-master-cadence";
import { assertTmallDirectPmStore, TMALL_YIJIU_DIRECT_PM_PROTOCOL, TMALL_YIYONG_DIRECT_PM_PROTOCOL, tmallDirectPmProtocolError } from "../tools/tmall-yijiu-direct-pm-contract";
import { runTmallProductMasterTerminalStage } from "../tools/tmall-sycm-cookie-pipeline";

test("亿用直连有独立协议，两条路由拒绝错店、错协议、空值和多值头", () => {
  for (const route of ["/promotion-direct-v1", "/product-master-direct-v1"]) {
    assert.equal(tmallDirectPmProtocolError({ route, storeKey: "tmall-yiyong", protocol: TMALL_YIYONG_DIRECT_PM_PROTOCOL }), null);
    for (const protocol of [undefined, "", TMALL_YIJIU_DIRECT_PM_PROTOCOL, [TMALL_YIYONG_DIRECT_PM_PROTOCOL]]) {
      assert.deepEqual(tmallDirectPmProtocolError({ route, storeKey: "tmall-yiyong", protocol }), { error: "missing_or_invalid_tmall_direct_pm_protocol" });
    }
    assert.deepEqual(tmallDirectPmProtocolError({ route, storeKey: "tmall-yijiu", protocol: TMALL_YIYONG_DIRECT_PM_PROTOCOL }), { error: "missing_or_invalid_tmall_direct_pm_protocol" });
    for (const storeKey of [null, "tmall-lili", "tmall-tuofeng", "tmall-cuizhiwang", "tmall-masitu", "constructor", "__proto__"]) {
      assert.deepEqual(tmallDirectPmProtocolError({ route, storeKey, protocol: TMALL_YIYONG_DIRECT_PM_PROTOCOL }), {
        error: storeKey === "tmall-lili" && route === "/product-master-direct-v1" ? "missing_or_invalid_tmall_direct_pm_protocol" : "tmall_direct_pm_store_not_allowed",
      });
      assert.throws(() => assertTmallDirectPmStore(storeKey ?? ""), /只允许/);
    }
  }
});

test("亿用试点使用原 ID 与调度，A/B/C 完全不变，候选生成确定且默认停用", async () => {
  const base = JSON.parse(await readFile(new URL("../automation/n8n/tmall-yijiu-sycm-cookie-daily.workflow.json", import.meta.url), "utf8"));
  const definition = tmallN8nWorkflowDefinitions.find((item) => item.storeKey === "tmall-yiyong")!;
  const legacy = buildTmallN8nWorkflow(base, definition);
  const workflow = buildTmallYiyongDirectPmCandidateWorkflow(base);
  const generated = JSON.parse(await readFile(new URL("../automation/n8n/tmall-yiyong-direct-pm-candidate.workflow.json", import.meta.url), "utf8"));
  assert.deepEqual(workflow, generated);
  assert.deepEqual(buildTmallYiyongDirectPmCandidateWorkflow(base), workflow);
  assert.equal(workflow.id, "TmallYiyongDaily2026");
  assert.equal(workflow.active, false);
  assert.equal(workflow.settings?.timezone, "Asia/Shanghai");
  assert.equal(definition.productMasterCadence.intervalDays, 1);
  for (const name of ["领取共享 helper", "A·计划目标日期", "B·逐日下载并验证 XLS", "C·签收、导入并覆盖回查", "每天 11:50 运行", "手动完整运行（强制 M）"]) {
    assert.deepEqual(workflow.nodes.find((node) => node.name === name), legacy.nodes.find((node) => node.name === name));
  }
  const requests = workflow.nodes.filter((node) => node.type === "n8n-nodes-base.httpRequest");
  assert.equal(requests.length, 7);
  for (const node of requests) {
    const headers = node.parameters?.headerParameters?.parameters ?? [];
    assert.deepEqual(headers.filter((header) => header.name === "X-TERUISI-TMALL-STORE-KEY"), [{ name: "X-TERUISI-TMALL-STORE-KEY", value: "tmall-yiyong" }]);
    assert.deepEqual(headers.filter((header) => header.name === "X-TERUISI-N8N-EXECUTION-ID"), [{ name: "X-TERUISI-N8N-EXECUTION-ID", value: "={{ $execution.id }}" }]);
    const direct = String(node.parameters?.url).endsWith("-direct-v1");
    assert.deepEqual(headers.filter((header) => header.name === "X-TERUISI-TMALL-CANDIDATE-PROTOCOL"), direct ? [{ name: "X-TERUISI-TMALL-CANDIDATE-PROTOCOL", value: TMALL_YIYONG_DIRECT_PM_PROTOCOL }] : []);
  }
  const edges = workflow.connections as Record<string, { main: { node: string }[][] }>;
  assert.equal(edges["C·签收、导入并覆盖回查"]!.main[0]![0]!.node, "P·直连创建商品报表、下载、汇总导入并回查");
  assert.equal(edges["P·直连创建商品报表、下载、汇总导入并回查"]!.main[0]![0]!.node, "N·复查缺口并计划下一日");
  assert.equal(edges["还有缺口且预算充足？"]!.main[1]![0]!.node, "M·MTOP 分批导出、合并校验并导入");
  assert.doesNotMatch(JSON.stringify(workflow), /tmall-yijiu|亿玖|csrfId=[^ ]|cookie2=/);
});

test("亿用每日节奏迁移保留最后成功事实，重复迁移拒绝，同日免动作、跨日到期", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "tmall-yiyong-cadence-"));
  try {
    const registry = JSON.parse(await readFile(new URL("../config/tmall-store-accounts.json", import.meta.url), "utf8")) as { stores: TmallStore[] };
    const store = registry.stores.find((item) => item.storeKey === "tmall-yiyong")!;
    assert.equal(store.productMasterCadence?.intervalDays, 1);
    for (const item of registry.stores.filter((item) => item.enabled && !["tmall-yijiu", "tmall-yiyong", "tmall-lili"].includes(item.storeKey))) assert.equal(item.productMasterCadence?.intervalDays, 3);
    const file = path.join(root, "tmall-yiyong.json");
    const old = { version: 1, storeKey: store.storeKey, intervalDays: 3, lastSuccessDate: "2026-08-26", lastSnapshotDate: "2026-08-26", nextDueDate: "2026-08-29", updatedAt: "2026-08-26T06:00:00Z" };
    await writeFile(file, JSON.stringify(old));
    await assert.rejects(loadTmallProductMasterCadenceState(store, root), /配置不一致/);
    await assert.rejects(migrateTmallProductMasterCadenceInterval({ store, expectedPreviousIntervalDays: 3, expectedLastSuccessDate: "2026-08-25", stateDirectory: root }), /最后成功日期已变化/);
    assert.deepEqual(JSON.parse(await readFile(file, "utf8")), old);
    const migrated = await migrateTmallProductMasterCadenceInterval({ store, expectedPreviousIntervalDays: 3, expectedLastSuccessDate: "2026-08-26", stateDirectory: root });
    assert.equal(migrated.lastSuccessDate, old.lastSuccessDate);
    assert.equal(migrated.lastSnapshotDate, old.lastSnapshotDate);
    assert.equal(migrated.nextDueDate, "2026-08-27");
    await assert.rejects(migrateTmallProductMasterCadenceInterval({ store, expectedPreviousIntervalDays: 3, expectedLastSuccessDate: "2026-08-26", stateDirectory: root }), /配置不一致/);
    const decision = decideTmallProductMasterCadence({ store, operationDate: "2026-09-06", state: migrated });
    assert.equal(decision.due, true);
    // Failed execution never calls the success writer: old success stays intact.
    assert.deepEqual(await loadTmallProductMasterCadenceState(store, root), migrated);
    const success = await recordTmallProductMasterCadenceSuccess({ store, decision, snapshotDate: "2026-09-06", stateDirectory: root });
    assert.equal(success?.nextDueDate, "2026-09-07");
    assert.equal(decideTmallProductMasterCadence({ store, operationDate: "2026-09-06", state: success }).reason, "not_due");
    assert.equal(decideTmallProductMasterCadence({ store, operationDate: "2026-09-07", state: success }).due, true);
    assert.equal(decideTmallProductMasterCadence({ store, operationDate: "2026-09-06", state: success, pendingAudit: true }).reason, "pending_audit");
    await writeFile(file, JSON.stringify({ ...success, storeKey: "tmall-lili" }));
    await assert.rejects(loadTmallProductMasterCadenceState(store, root), /配置不一致/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("亿用旧管家已确认任务必须拦截直连，不能因换策略或跨日删除证据", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "tmall-yiyong-legacy-"));
  try {
    const file = path.join(root, "active-tmall-yiyong.json");
    for (const stage of ["export_submitting", "export_submitted", "export_confirmed", "downloaded", "failed"]) {
      const raw = JSON.stringify({ storeKey: "tmall-yiyong", stage, snapshotDate: "2026-09-04" });
      await writeFile(file, raw);
      await assert.rejects(assertNoLegacyMasterAction({ storeKey: "tmall-yiyong" }, [root]), /拒绝.*接管/);
      assert.equal(await readFile(file, "utf8"), raw);
      await assertNoLegacyMasterAction({ storeKey: "tmall-yijiu" }, [root]);
    }
    for (const stage of ["report_submitting", "report_submitted", "downloaded", "importing"]) {
      assert.equal(directPromotionLegacyAuditBlocks({ stage: "failed", resumeStage: stage }), true);
    }
    assert.equal(directPromotionLegacyAuditBlocks({ stage: "failed", resumeStage: "browser_ready" }), false);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("亿用直连 M 失败不推进节奏，不得退回管家/UI 导出；未到期只收尾", async () => {
  const registry = JSON.parse(await readFile(new URL("../config/tmall-store-accounts.json", import.meta.url), "utf8")) as { stores: TmallStore[] };
  const store = registry.stores.find((item) => item.storeKey === "tmall-yiyong")!;
  let writes = 0;
  let closes = 0;
  const calls: string[] = [];
  const options = {
    store, forced: false, mode: "direct_mtop" as const,
    getDecision: async () => decideTmallProductMasterCadence({ store, state: null, operationDate: "2026-09-06" }),
    runDirect: async () => { calls.push("direct"); throw new Error("导入回查失败"); },
    runProductManager: async () => { throw new Error("不能回退管家"); },
    runPagewise: async () => { throw new Error("不能回退 UI"); },
    recordSuccess: async () => { writes++; return null; },
    closeBrowser: async () => { closes++; return { ok: true as const, status: "closed" as const }; },
  };
  await assert.rejects(runTmallProductMasterTerminalStage(options), /导入回查失败/);
  assert.deepEqual(calls, ["direct"]);
  assert.equal(writes, 0);
  // Failed-stage browser cleanup belongs to the outer helper failure handler.
  const result = await runTmallProductMasterTerminalStage({
    ...options,
    getDecision: async () => decideTmallProductMasterCadence({ store, operationDate: "2026-09-06", state: {
      version: 1, storeKey: store.storeKey, intervalDays: 1, lastSuccessDate: "2026-09-06",
      lastSnapshotDate: "2026-09-06", nextDueDate: "2026-09-07", updatedAt: "2026-09-06T07:00:00Z",
    } }),
  });
  assert.equal(result.status, "not_due");
  assert.equal(writes, 0);
  assert.equal(closes, 1);
  assert.deepEqual(calls, ["direct"]);
});
