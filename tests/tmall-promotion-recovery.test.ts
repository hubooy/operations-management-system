import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { getTmallStore } from "../lib/netshop/tmall-store-registry";
import {
  readTmallPromotionRecovery,
  runTmallPromotionStage,
  TMALL_PROMOTION_REPORT_PROTOCOL,
  TMALL_PROMOTION_MARKETING_SCENES,
  TMALL_PROMOTION_DIMENSIONS,
} from "../tools/tmall-promotion-export";

const storeKey = "tmall-lili";
const originalDate = "2026-09-02";
const requestedDate = "2026-09-05";
const baseUrl = "http://localhost:3000";

async function withAudit(run: (directory: string, auditPath: string, audit: Record<string, unknown>) => Promise<void>) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "tmall-recovery-test-"));
  const auditPath = path.join(directory, `active-${storeKey}.json`);
  const store = await getTmallStore(storeKey);
  const audit = {
    version: 2, reportProtocol: TMALL_PROMOTION_REPORT_PROTOCOL,
    reportName: "商品报表", runId: "original-task", storeKey, shopName: store.shopName,
    baseUrl, startDate: originalDate, endDate: originalDate, dates: [originalDate],
    startedAt: "2026-09-02T05:40:00.000Z", updatedAt: "2026-09-02T05:41:00.000Z",
    stage: "report_submitted", marketingScenes: TMALL_PROMOTION_MARKETING_SCENES,
    dimensions: TMALL_PROMOTION_DIMENSIONS, timeGranularity: "分天", metrics: "全部数据指标",
  };
  try {
    await writeFile(auditPath, JSON.stringify(audit), "utf8");
    await run(directory, auditPath, audit);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

function mockCoverage(missingDate?: string) {
  return (async (input: Parameters<typeof fetch>[0], init?: RequestInit) => {
    assert.equal(init?.method ?? "GET", "GET");
    const url = new URL(String(input));
    const startDate = url.searchParams.get("startDate")!;
    const endDate = url.searchParams.get("endDate")!;
    assert.equal(startDate, endDate, "recovery checks only the exact old day");
    return Response.json({ requestedPeriod: { startDate, endDate }, coverage: {
      productDailyDates: startDate === missingDate ? [] : [startDate],
      // Existing coverage cannot bypass an unfinished task's completion proof.
      promotionDates: [startDate],
    } });
  }) as typeof fetch;
}

function completed(date: string) {
  return {
    ok: true, stage: "promotion_day" as const, status: "duplicate" as const,
    storeKey, shopName: "天猫-志高丽力专卖店", date, startDate: date, endDate: date, dates: [date],
    metrics: "全部数据指标" as const, fileName: "fixture.zip", sha256: "a".repeat(64),
    rowCount: 1, batchId: `fixture-${date}`, warningCount: 0, coverageConfirmed: true,
  };
}

test("one pending report resumes at its original date without replaying an already-covered requested day", async () => {
  await withAudit(async (directory, auditPath, audit) => {
    const executed: string[] = [];
    const result = await runTmallPromotionStage({
      storeKey, baseUrl, dates: [requestedDate], maximumDays: 1,
      auditDirectory: directory, request: mockCoverage(),
      executeDate: async ({ plan, recoveryRunId }) => {
        assert.deepEqual(JSON.parse(await readFile(auditPath, "utf8")), audit);
        assert.equal(recoveryRunId, plan.startDate === originalDate ? audit.runId : undefined);
        executed.push(plan.startDate);
        return completed(plan.startDate);
      },
    });
    assert.deepEqual(executed, [originalDate]);
    assert.deepEqual(result.completedDates, executed);
    assert.equal(result.recoveryDate, originalDate);
  });
});

test("an empty new plan resumes an already-imported audit on its exact original date", async () => {
  await withAudit(async (directory, auditPath, audit) => {
    await writeFile(auditPath, JSON.stringify({
      ...audit,
      stage: "failed",
      resumeStage: "importing",
      file: {
        fileName: "fixture.zip",
        filePath: "fixture.zip",
        size: 1,
        sha256: "a".repeat(64),
        rowCount: 1,
        dateMin: originalDate,
        dateMax: originalDate,
      },
      error: "import response proof not acknowledged",
    }), "utf8");
    const executed: string[] = [];
    const result = await runTmallPromotionStage({
      storeKey, baseUrl, dates: [], maximumDays: 1,
      auditDirectory: directory, request: mockCoverage(),
      executeDate: async ({ plan, recoveryRunId }) => {
        assert.equal(recoveryRunId, audit.runId);
        executed.push(plan.startDate);
        return completed(plan.startDate);
      },
    });
    assert.deepEqual(executed, [originalDate]);
    assert.deepEqual(result.completedDates, [originalDate]);
    assert.equal(result.recoveryDate, originalDate);
  });
});

test("a lost resumeStage uses post-submission task scan proof and never creates a second report", async () => {
  await withAudit(async (directory, auditPath, audit) => {
    await writeFile(auditPath, JSON.stringify({
      ...audit,
      stage: "failed",
      dialogAttempts: 1,
      taskScanDiagnostic: {
        capturedAt: "2026-09-07T13:42:37.435Z",
        rowCandidates: 0,
        visibleRows: 0,
        strictRows: 0,
        downloadActions: 0,
        visibleDownloadActions: 0,
        strictActionScopes: 0,
        visibleActionBoxes: [],
        candidateCount: 0,
        candidates: [],
      },
      error: "outer timeout replaced the original failure",
    }), "utf8");
    const executed: string[] = [];
    const result = await runTmallPromotionStage({
      storeKey,
      baseUrl,
      dates: [requestedDate],
      maximumDays: 1,
      auditDirectory: directory,
      request: mockCoverage(),
      executeDate: async ({ plan, recoveryRunId }) => {
        assert.equal(recoveryRunId, audit.runId);
        executed.push(plan.startDate);
        return completed(plan.startDate);
      },
    });
    assert.deepEqual(executed, [originalDate]);
    assert.equal(result.recoveryDate, originalDate);
  });
});

test("an empty covered plan without recovery rechecks only the immutable plan range", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "tmall-empty-plan-test-"));
  try {
    let requestedRange: [string, string] | undefined;
    const request = (async (input: Parameters<typeof fetch>[0]) => {
      const url = new URL(String(input));
      const startDate = url.searchParams.get("startDate")!;
      const endDate = url.searchParams.get("endDate")!;
      requestedRange = [startDate, endDate];
      return Response.json({ requestedPeriod: { startDate, endDate }, coverage: {
        productDailyDates: [startDate],
        promotionDates: [startDate],
      } });
    }) as typeof fetch;
    const result = await runTmallPromotionStage({
      storeKey,
      baseUrl,
      dates: [],
      planStartDate: "2026-09-05",
      planEndDate: "2026-09-05",
      maximumDays: 1,
      auditDirectory: directory,
      request,
      resolveRecovery: async () => null,
      executeDate: async () => { assert.fail("covered empty plan must not execute a report"); },
    });
    assert.deepEqual(requestedRange, ["2026-09-05", "2026-09-05"]);
    assert.equal(result.status, "skipped");
    assert.equal(result.coverageConfirmed, true);
    await assert.rejects(runTmallPromotionStage({
      storeKey,
      baseUrl,
      dates: [],
      planStartDate: "2026-09-05",
      auditDirectory: directory,
      request,
      resolveRecovery: async () => null,
    }), /计划范围必须完整/);
    await writeFile(path.join(directory, `active-${storeKey}.json`), JSON.stringify({ stage: "failed" }));
    const emptyPlan = {
      storeKey,
      baseUrl,
      dates: [],
      planStartDate: "2026-09-05",
      planEndDate: "2026-09-05",
      maximumDays: 1,
      auditDirectory: directory,
      request,
      resolveRecovery: async () => null,
    };
    await assert.rejects(runTmallPromotionStage(emptyPlan), /仍有活动清单/);
    const specialized = await runTmallPromotionStage({ ...emptyPlan, pendingAuditDirectories: [] });
    assert.equal(specialized.status, "skipped");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("same-day recovery runs once and failure of the old report prevents new-date execution", async () => {
  await withAudit(async (directory) => {
    const calls: string[] = [];
    const result = await runTmallPromotionStage({
      storeKey, baseUrl, dates: [originalDate], maximumDays: 1,
      auditDirectory: directory, request: mockCoverage(),
      executeDate: async ({ plan }) => { calls.push(plan.startDate); return completed(plan.startDate); },
    });
    assert.deepEqual(result.completedDates, [originalDate]);
    calls.length = 0;
    await assert.rejects(runTmallPromotionStage({
      storeKey, baseUrl, dates: [requestedDate], maximumDays: 1,
      auditDirectory: directory, request: mockCoverage(),
      executeDate: async ({ plan }) => { calls.push(plan.startDate); throw new Error("original task unavailable"); },
    }), /original task unavailable/);
    assert.deepEqual(calls, [originalDate]);
  });
});

test("recovery requires old-day product coverage and preserves the requested-date cap", async () => {
  await withAudit(async (directory, auditPath, audit) => {
    const executeDate = async () => { assert.fail("business stage must not run"); };
    await assert.rejects(runTmallPromotionStage({
      storeKey, baseUrl, dates: [requestedDate], maximumDays: 1,
      auditDirectory: directory, request: mockCoverage(originalDate), executeDate,
    }), /waiting_product_daily/);
    assert.deepEqual(JSON.parse(await readFile(auditPath, "utf8")), audit);
    await assert.rejects(runTmallPromotionStage({
      storeKey, baseUrl, dates: ["2026-09-04", requestedDate], maximumDays: 1,
      auditDirectory: directory, executeDate,
      request: (async () => Response.json({
        requestedPeriod: { startDate: "2026-09-04", endDate: requestedDate },
        coverage: { productDailyDates: ["2026-09-04", requestedDate], promotionDates: [] },
      })) as typeof fetch,
    }), /超过单轮 1 天上限/);
  });
});

test("cross-store, uncertain, malformed and conflicting recovery proofs fail closed without writes", async () => {
  await withAudit(async (directory, auditPath, audit) => {
    const store = await getTmallStore(storeKey);
    const input = { store, baseUrl, auditDirectory: directory, latestAllowedDate: requestedDate };
    const invalid = [
      { storeKey: "tmall-tuofeng" }, { shopName: "another shop" },
      { baseUrl: "http://localhost:3001" }, { startDate: "2026-09-07", endDate: "2026-09-07", dates: ["2026-09-07"] },
      { endDate: "2026-09-03" }, { dates: [originalDate, requestedDate] },
      { dimensions: ["商品"] }, { marketingScenes: [] }, { metrics: "partial" },
      { stage: "report_submitting" }, { stage: "failed" }, { stage: "unknown" },
      { stage: "downloaded" }, { stage: "downloaded_unverified" },
      { stage: "browser_ready", selectedTask: { signature: "existing-business-action" } },
      { reportProtocol: "legacy_protocol" },
    ];
    for (const changes of invalid) {
      const value = { ...audit, ...changes };
      await writeFile(auditPath, JSON.stringify(value), "utf8");
      await assert.rejects(readTmallPromotionRecovery(input));
      assert.deepEqual(JSON.parse(await readFile(auditPath, "utf8")), value);
    }
    for (const stage of ["planned", "browser_ready", "dialog_opening", "dialog_ready", "report_configured", "completed"]) {
      await writeFile(auditPath, JSON.stringify({ ...audit, stage }), "utf8");
      assert.equal(await readTmallPromotionRecovery(input), null);
    }
    await writeFile(auditPath, JSON.stringify({ ...audit, stage: "failed", resumeStage: "report_submitted" }), "utf8");
    assert.deepEqual(await readTmallPromotionRecovery(input), { date: originalDate, runId: audit.runId });
  });
});

test("a removed or downgraded original task cannot turn recovery into a new report submission", async () => {
  for (const change of ["removed", "planned", "new_owner"]) {
    await withAudit(async (directory, auditPath, audit) => {
      await assert.rejects(runTmallPromotionStage({
        storeKey, baseUrl, dates: [requestedDate], maximumDays: 1,
        auditDirectory: directory, request: mockCoverage(),
        resolveRecovery: async (input) => {
          const recovery = await readTmallPromotionRecovery(input);
          if (change === "removed") await rm(auditPath);
          else await writeFile(auditPath, JSON.stringify({ ...audit, ...(change === "planned" ? { stage: "planned" } : { runId: "another-owner" }) }), "utf8");
          return recovery;
        },
        // Use the real date executor: it must reject before browser startup.
      }), /禁止为恢复日期重新创建报表/);
      if (change === "planned") assert.equal(JSON.parse(await readFile(auditPath, "utf8")).stage, "planned");
      if (change === "new_owner") assert.equal(JSON.parse(await readFile(auditPath, "utf8")).runId, "another-owner");
    });
  }
});
