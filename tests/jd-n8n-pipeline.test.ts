import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { tmpdir } from "node:os";

import type { JdStore } from "../lib/jd/store-registry";
import type { RunnerAudit } from "../tools/jd-multi-store-runner";
import {
  contiguousJdMissingRanges,
  jdHelperRequestError,
  planJdN8nRun,
  runJdN8nPlan,
  verifyJdN8nPlan,
} from "../tools/jd-n8n-pipeline";

function store(shopName = "测试京东店") : JdStore {
  return {
    storeKey: "jd-test", accountLabel: "测试", platform: "京东", shopName, shopId: "10001", enabled: true,
    browser: {
      executablePath: "unused/chromium.exe",
      userDataDir: "unused/user-data",
      profileName: "Default",
      profileDir: "unused/user-data/Default",
      debugPort: 9224,
      downloadDir: "unused/downloads",
    },
  };
}

function importResult(step: "jd_product_master" | "jd_sku_daily" | "spu_daily", shopName: string, batchId: string, startDate = "2026-07-01", endDate = "2026-07-20") {
  const daily = step !== "jd_product_master";
  return {
    status: "imported", batchId, rowCount: 3, batchStatus: "completed", warningCount: 0,
    platform: "京东", shopName, source: daily ? "jd_sku_daily" : "jd_product_master",
    dataset: step === "jd_product_master" ? "product_master" : step === "jd_sku_daily" ? "sku_daily" : "spu_daily",
    ...(daily ? { dateMin: startDate, dateMax: endDate } : {}),
  };
}

function dates(startDate: string, endDate: string) {
  const result: string[] = [];
  const cursor = new Date(`${startDate}T00:00:00Z`);
  while (cursor <= new Date(`${endDate}T00:00:00Z`)) {
    result.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return result;
}

function coverageRequest(missing: Partial<Record<"sku" | "spu", string[]>> = {}): typeof fetch {
  return async (input) => {
    const url = new URL(String(input));
    if (url.pathname === "/") return new Response("ok", { status: 200 });
    assert.equal(url.pathname, "/api/netshop/product-performance");
    assert.equal(url.searchParams.get("platform"), "京东");
    assert.match(url.searchParams.get("outlet") ?? "", /^京东\u001f/);
    assert.equal(url.searchParams.get("view"), "full");
    assert.equal(url.searchParams.get("pageSize"), "1");
    const dimension = url.searchParams.get("dimension") as "sku" | "spu";
    const startDate = url.searchParams.get("startDate")!;
    const endDate = url.searchParams.get("endDate")!;
    const missingDates = missing[dimension] ?? [];
    return Response.json({
      dimension,
      dataset: dimension === "sku" ? "sku_daily" : "spu_daily",
      requestedPeriod: { startDate, endDate },
      coverage: { actualDates: dates(startDate, endDate).filter((date) => !missingDates.includes(date)), missingDates, truncated: false },
    });
  };
}

test("JD helper binds one execution and rejects empty, foreign, out-of-order, and busy requests", () => {
  assert.equal(jdHelperRequestError("ready", false, "/jd/plan", "execution-1", null), null);
  assert.deepEqual(jdHelperRequestError("ready", false, "/jd/run", "execution-1", null), { error: "execution_not_claimed", expected: "/jd/plan" });
  assert.deepEqual(jdHelperRequestError("planned", false, "/jd/run", "other", "execution-1"), { error: "execution_mismatch" });
  assert.deepEqual(jdHelperRequestError("planned", true, "/jd/run", "execution-1", "execution-1"), { error: "pipeline_busy" });
  assert.deepEqual(jdHelperRequestError("planned", false, "/jd/verify", "execution-1", "execution-1"), { error: "invalid_stage", expected: "executed|completed", actual: "planned" });
  assert.deepEqual(jdHelperRequestError("ready", false, "/jd/plan", null, null), { error: "missing_or_invalid_execution_id" });
});

test("JD helper permits same-execution retries after A, B, or C response loss", () => {
  assert.equal(jdHelperRequestError("planned", false, "/jd/plan", "execution-1", "execution-1"), null);
  assert.equal(jdHelperRequestError("executed", false, "/jd/plan", "execution-1", "execution-1"), null);
  assert.equal(jdHelperRequestError("completed", false, "/jd/plan", "execution-1", "execution-1"), null);
  assert.equal(jdHelperRequestError("executed", false, "/jd/run", "execution-1", "execution-1"), null);
  assert.equal(jdHelperRequestError("completed", false, "/jd/verify", "execution-1", "execution-1"), null);
  assert.deepEqual(jdHelperRequestError("completed", false, "/jd/run", "execution-1", "execution-1"), { error: "invalid_stage", expected: "planned|executed", actual: "completed" });
});

test("loopback helper preserves the persisted JD stage on an idempotent A retry", async () => {
  const helper = await readFile("tools/tmall-sycm-cookie-pipeline.ts", "utf8");
  const jdPlanBranch = helper.slice(helper.indexOf('request.url === "/jd/plan"'), helper.indexOf('request.url === "/jd/run"'));
  assert.match(jdPlanBranch, /stage = jdPlan\.stage/);
  assert.doesNotMatch(jdPlanBranch, /stage = "planned"/);
});

test("JD plan fails closed when the n8n execution owner is missing or empty", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jd-n8n-owner-"));
  const base = {
    root, now: new Date("2026-07-21T01:00:00+08:00"), baseUrl: "http://localhost:3000",
    request: coverageRequest(),
    profileStatus: async () => "ready" as const, runIdFactory: () => "jd-n8n-owner", stores: [store()],
  };
  await assert.rejects(() => planJdN8nRun({ ...base, executionId: "" }), /execution ID 无效/);
  await assert.rejects(() => planJdN8nRun(base as unknown as Parameters<typeof planJdN8nRun>[0]), /execution ID 无效/);
});

test("JD missing dates are grouped only when naturally contiguous", () => {
  assert.deepEqual(contiguousJdMissingRanges(["2026-07-02", "2026-07-03", "2026-07-05"]), [
    { startDate: "2026-07-02", endDate: "2026-07-03" },
    { startDate: "2026-07-05", endDate: "2026-07-05" },
  ]);
  assert.throws(() => contiguousJdMissingRanges(["2026-07-03", "2026-07-02"]), /升序/);
  assert.throws(() => contiguousJdMissingRanges(["2026-07-02", "2026-07-02"]), /重复/);
});

test("JD plan keeps daily master refreshes and schedules only SKU/SPU missing ranges", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jd-n8n-plan-"));
  const request = coverageRequest({ sku: ["2026-07-02", "2026-07-03", "2026-07-05"] });
  const options = {
    root, now: new Date("2026-07-21T01:00:00+08:00"), baseUrl: "http://localhost:3000", request,
    profileStatus: async () => "ready" as const, runIdFactory: () => "jd-n8n-test-run", executionId: "execution-1", stores: [store()], silentNoWindow: true,
  };
  const plan = await planJdN8nRun(options);
  assert.equal(plan.silentNoWindow, true);
  assert.deepEqual([plan.startDate, plan.endDate], ["2026-07-01", "2026-07-20"]);
  assert.deepEqual(plan.stores, [{ storeKey: "jd-test", shopId: "10001", shopName: "测试京东店" }]);
  assert.deepEqual(plan.operations.map(({ step, startDate, endDate }) => ({ step, startDate, endDate })), [
    { step: "jd_product_master", startDate: undefined, endDate: undefined },
    { step: "jd_sku_daily", startDate: "2026-07-02", endDate: "2026-07-03" },
    { step: "jd_sku_daily", startDate: "2026-07-05", endDate: "2026-07-05" },
  ]);
  assert.equal(plan.coverage.find((item) => item.dimension === "spu")?.missingDates.length, 0);
  let failedAuditPath = "";
  const failedRun = await runJdN8nPlan(plan, {
    root, stores: [store()],
    run: async (runOptions) => {
      failedAuditPath = path.join(root, "outputs", "jd-multi-store-runner", "run-1.json");
      return { ok: false, auditPath: failedAuditPath, audit: { items: [{ storeKey: "jd-test", step: "jd_product_master", status: "failed" }] } as unknown as RunnerAudit };
    },
  }).catch((error) => error);
  assert.ok(failedRun instanceof Error);
  const resumed = await planJdN8nRun(options);
  assert.equal(resumed.runId, plan.runId);
  assert.equal(resumed.stage, "planned");
  let resumedAuditPath = "";
  let call = 0;
  await runJdN8nPlan(resumed, {
    root, stores: [store()],
    run: async (runOptions) => {
      call += 1;
      assert.equal(runOptions.silentNoWindow, true);
      if (call === 1) resumedAuditPath = runOptions.resumeAuditPath ?? "";
      const step = runOptions.mode === "master" ? "jd_product_master" : runOptions.mode === "sku-daily" ? "jd_sku_daily" : "spu_daily";
      const proof = importResult(step, "测试京东店", `batch-${call}`, runOptions.startDate!, runOptions.endDate!);
      return {
        ok: true,
        auditPath: runOptions.resumeAuditPath ?? path.join(root, "outputs", "jd-multi-store-runner", `run-${call + 1}.json`),
        audit: { items: [{ storeKey: "jd-test", shopName: "测试京东店", step, status: "completed", batchId: proof.batchId, rowCount: proof.rowCount, importResult: proof }] } as unknown as RunnerAudit,
      };
    },
  });
  assert.match(resumedAuditPath, /outputs[\\/]jd-multi-store-runner[\\/]run-1\.json$/);
  resumed.stage = "completed";
  await writeFile(path.join(root, "outputs", "jd-n8n-pipeline", `plan-${resumed.runId}.json`), JSON.stringify(resumed), "utf8");
  const sameExecution = await planJdN8nRun({ ...options, runIdFactory: () => "must-not-create" });
  assert.equal(sameExecution.runId, plan.runId);
  const fresh = await planJdN8nRun({ ...options, executionId: "execution-2", runIdFactory: () => "jd-n8n-new-run" });
  assert.equal(fresh.runId, "jd-n8n-new-run");
  const changedStores = [store("已变更店铺")];
  const changed = await planJdN8nRun({ ...options, stores: changedStores, runIdFactory: () => "jd-n8n-changed" });
  assert.equal(changed.runId, "jd-n8n-changed");
});

test("JD plan fails closed on truncated or non-partitioned coverage", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jd-n8n-coverage-"));
  const targetStore = store();
  const base = {
    root, now: new Date("2026-07-03T01:00:00+08:00"), baseUrl: "http://localhost:3000",
    profileStatus: async () => "ready" as const, executionId: "execution-coverage", stores: [targetStore],
  };
  const truncated: typeof fetch = async (input) => {
    const response = await coverageRequest()(input);
    if (new URL(String(input)).pathname === "/") return response;
    const payload = await response.json() as { coverage: { truncated: boolean } };
    payload.coverage.truncated = true;
    return Response.json(payload);
  };
  await assert.rejects(() => planJdN8nRun({ ...base, request: truncated }), /完整覆盖/);
  const overlapping: typeof fetch = async (input) => {
    const response = await coverageRequest()(input);
    if (new URL(String(input)).pathname === "/") return response;
    const payload = await response.json() as { coverage: { actualDates: string[]; missingDates: string[] } };
    payload.coverage.missingDates = [payload.coverage.actualDates[0]];
    return Response.json(payload);
  };
  await assert.rejects(() => planJdN8nRun({ ...base, request: overlapping }), /互斥日期集合/);
});

test("JD C-stage rechecks every planned batch and requires final SKU/SPU coverage", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jd-n8n-verify-"));
  const targetStore = store();
  const startedAt = "2026-07-21T00:00:00.000Z";
  const updatedAt = "2026-07-21T00:01:00.000Z";
  const makeAudit = (step: "jd_product_master" | "jd_sku_daily", mode: "master" | "sku-daily", batchId: string, startDate: string, endDate: string): RunnerAudit => {
    const proof = importResult(step, targetStore.shopName, batchId, startDate, endDate);
    return {
      version: 1, baseUrl: "http://localhost:3000", startedAt, updatedAt, mode, dryRun: false,
      startDate, endDate, storeKeys: [targetStore.storeKey],
      items: [{ storeKey: targetStore.storeKey, shopName: targetStore.shopName, step, status: "completed", batchId, rowCount: proof.rowCount, importResult: proof }],
    };
  };
  const masterAudit = makeAudit("jd_product_master", "master", "batch-master", "2026-07-01", "2026-07-20");
  const skuAudit = makeAudit("jd_sku_daily", "sku-daily", "batch-sku", "2026-07-02", "2026-07-03");
  const auditDirectory = path.join(root, "outputs", "jd-multi-store-runner");
  const masterAuditPath = path.join(auditDirectory, "run-1.json");
  const skuAuditPath = path.join(auditDirectory, "run-2.json");
  await mkdir(auditDirectory, { recursive: true });
  await writeFile(masterAuditPath, JSON.stringify(masterAudit), "utf8");
  await writeFile(skuAuditPath, JSON.stringify(skuAudit), "utf8");
  const plan = {
    version: 2 as const, runId: "jd-n8n-verify", generatedAt: startedAt, updatedAt, baseUrl: masterAudit.baseUrl, ownerExecutionId: "execution-1",
    startDate: "2026-07-01", endDate: "2026-07-20", storeKeys: [targetStore.storeKey],
    stores: [{ storeKey: targetStore.storeKey, shopId: targetStore.shopId, shopName: targetStore.shopName }],
    coverage: [
      { storeKey: targetStore.storeKey, dimension: "sku" as const, actualDates: dates("2026-07-01", "2026-07-20").filter((date) => !["2026-07-02", "2026-07-03"].includes(date)), missingDates: ["2026-07-02", "2026-07-03"] },
      { storeKey: targetStore.storeKey, dimension: "spu" as const, actualDates: dates("2026-07-01", "2026-07-20"), missingDates: [] },
    ],
    operations: [
      { storeKey: targetStore.storeKey, step: "jd_product_master" as const, status: "completed" as const, runnerAuditPath: masterAuditPath },
      { storeKey: targetStore.storeKey, step: "jd_sku_daily" as const, startDate: "2026-07-02", endDate: "2026-07-03", status: "completed" as const, runnerAuditPath: skuAuditPath },
    ],
    stage: "executed" as const,
  };
  const request: typeof fetch = async (input) => {
    const url = new URL(String(input));
    if (url.pathname === "/api/netshop/product-performance") return coverageRequest()(input);
    const batchId = url.searchParams.get("batchId")!;
    const step = batchId === "batch-master" ? "jd_product_master" : "jd_sku_daily";
    const proof = importResult(step, targetStore.shopName, batchId, "2026-07-02", "2026-07-03");
    return Response.json({ items: [{ id: batchId, status: "completed", source: proof.source, dataset: proof.dataset, platform: proof.platform, shopName: proof.shopName, warningCount: 0, rowCount: 3, dateMin: proof.dateMin ?? null, dateMax: proof.dateMax ?? null }] });
  };
  const verified = await verifyJdN8nPlan(plan, { root, stores: [targetStore], request });
  assert.equal(verified.stage, "verify");
  assert.equal(plan.stage, "completed");
  const bad = { ...plan, stage: "executed" as const };
  await assert.rejects(() => verifyJdN8nPlan(bad, { root, stores: [targetStore], request: async () => Response.json({ items: [] }) }), /精确导入批次/);
  const remaining = { ...plan, stage: "executed" as const };
  const missingRequest: typeof fetch = async (input) => {
    const url = new URL(String(input));
    if (url.pathname === "/api/netshop/product-performance") return coverageRequest({ spu: ["2026-07-04"] })(input);
    return request(input);
  };
  await assert.rejects(() => verifyJdN8nPlan(remaining, { root, stores: [targetStore], request: missingRequest }), /导入后仍有缺口/);
});
