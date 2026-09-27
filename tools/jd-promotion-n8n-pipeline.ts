import { randomUUID } from "node:crypto";
import { mkdir, readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { getJdProfilesStatus } from "./jd-n8n-pipeline";
import {
  normalizeLocalBaseUrl,
  runJdPromotionExport,
  shanghaiYesterday,
  verifyJdPromotionPublishedBatch,
  type JdPromotionExportOptions,
  type JdPromotionExportResult,
} from "./jd-promotion-export";
import { inspectJdPromotionCsv } from "../lib/jd/promotion-report";
import { getJdStore, type JdStore } from "../lib/jd/store-registry";
import { readJsonFile, writeJsonAtomic } from "../lib/jackyun/json-file";
import { netshopOutletKey } from "../lib/netshop/query-contract";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const planDirectoryName = "jd-promotion-n8n-pipeline";
const maximumScanDays = 730;
const maximumMissingDatesPerRun = 31;
export const jdPromotionStartDateHeader = "x-teruisi-jd-promotion-start-date";
export const jdPromotionEndDateHeader = "x-teruisi-jd-promotion-end-date";
export const jdPromotionStoreKeyHeader = "x-teruisi-jd-promotion-store-key";
export const jdPromotionStoreKeys = ["jd-yiyong-director", "jd-maidehao-operator1"] as const;
export type JdPromotionStoreKey = typeof jdPromotionStoreKeys[number];

export type JdPromotionN8nStage = "planned" | "running" | "executed" | "completed" | "failed";
export type JdPromotionHelperRoute = "/jd-promotion/plan" | "/jd-promotion/run" | "/jd-promotion/verify";

export type JdPromotionN8nPlan = {
  version: 2;
  runId: string;
  generatedAt: string;
  updatedAt: string;
  baseUrl: string;
  ownerExecutionId: string;
  store: { storeKey: string; shopId: string; shopName: string; accountLabel: string };
  scannedStartDate: string;
  scannedEndDate: string;
  startDate: string;
  endDate: string;
  dates: string[];
  missingDateCount: number;
  deferredDateCount: number;
  stage: JdPromotionN8nStage;
  results: JdPromotionExportResult[];
  failure?: { code: string; stage: "plan" | "run" | "verify"; message: string; at: string };
};

type PromotionCoveragePayload = {
  requestedPeriod?: { startDate?: unknown; endDate?: unknown };
  coverage?: {
    promotionDates?: unknown;
    promotionDatesPagination?: { total?: unknown; returned?: unknown; truncated?: unknown };
  };
};

type PlanOptions = {
  root?: string;
  now?: Date;
  baseUrl?: string;
  executionId: string;
  startDate?: string;
  endDate?: string;
  storeKey?: string;
  store?: JdStore;
  request?: typeof fetch;
  profileStatus?: (stores: readonly JdStore[]) => Promise<"ready" | "missing" | "invalid">;
  runIdFactory?: () => string;
};

type RunOptions = {
  root?: string;
  store?: JdStore;
  run?: (options: JdPromotionExportOptions) => Promise<JdPromotionExportResult>;
};

type VerifyOptions = { root?: string; store?: JdStore; request?: typeof fetch };

function pathsFor(root = projectRoot) {
  const resolved = path.resolve(root);
  return { root: resolved, planDirectory: path.join(resolved, "outputs", planDirectoryName) };
}

function validExecutionId(value: string | undefined) {
  return typeof value === "string" && value.length >= 1 && value.length <= 128 && /^[A-Za-z0-9._:-]+$/.test(value);
}

function validRunId(value: string) {
  return /^[A-Za-z0-9._-]{1,96}$/.test(value);
}

function validDate(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
}

function inclusiveDates(startDate: string, endDate: string) {
  const dates: string[] = [];
  const cursor = new Date(`${startDate}T00:00:00Z`);
  const end = new Date(`${endDate}T00:00:00Z`);
  while (cursor <= end) {
    dates.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
    if (dates.length > maximumScanDays) throw new Error(`京准通推广缺口扫描不能超过 ${maximumScanDays} 天`);
  }
  return dates;
}

function safeError(error: unknown) {
  return (error instanceof Error ? error.message : String(error))
    .replace(/https?:\/\/\S+/gi, "<url>")
    .replace(/(cookie|token|password|secret)\s*[=:]\s*\S+/gi, (_match, key: string) => `${key}=<redacted>`)
    .slice(0, 1_000);
}

function storeIdentity(store: JdStore) {
  return { storeKey: store.storeKey, shopId: store.shopId, shopName: store.shopName, accountLabel: store.accountLabel };
}

function sameStore(left: JdPromotionN8nPlan["store"], right: JdPromotionN8nPlan["store"]) {
  return left.storeKey === right.storeKey && left.shopId === right.shopId && left.shopName === right.shopName && left.accountLabel === right.accountLabel;
}

function planPath(root: string, runId: string) {
  if (!validRunId(runId)) throw new Error("京准通 n8n 运行编号无效");
  return path.join(root, `plan-${runId}.json`);
}

function dailyRunId(runId: string, date: string) {
  const suffix = `-${date.replaceAll("-", "")}`;
  return `${runId.slice(0, 96 - suffix.length)}${suffix}`;
}

async function persistPlan(directory: string, plan: JdPromotionN8nPlan) {
  plan.updatedAt = new Date().toISOString();
  await writeJsonAtomic(planPath(directory, plan.runId), plan);
}

async function findPriorPlan(
  directory: string,
  input: Pick<JdPromotionN8nPlan, "baseUrl" | "scannedStartDate" | "scannedEndDate" | "store">,
) {
  const entries = await readdir(directory, { withFileTypes: true }).catch(() => []);
  const plans = await Promise.all(entries.filter((entry) => entry.isFile() && /^plan-[A-Za-z0-9._-]+\.json$/.test(entry.name))
    .map((entry) => readJsonFile<JdPromotionN8nPlan>(path.join(directory, entry.name)).catch(() => null)));
  return plans.filter((plan): plan is JdPromotionN8nPlan => Boolean(plan))
    .filter((plan) => plan.version === 2 && plan.baseUrl === input.baseUrl
      && plan.scannedStartDate === input.scannedStartDate && plan.scannedEndDate === input.scannedEndDate
      && sameStore(plan.store, input.store))
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))[0];
}

export function buildJdPromotionCoverageUrl(
  baseUrl: string,
  store: Pick<JdStore, "shopName">,
  startDate: string,
  endDate: string,
) {
  const params = new URLSearchParams({
    platform: "京东",
    outlet: netshopOutletKey("京东", store.shopName),
    startDate,
    endDate,
  });
  return `${baseUrl}/api/netshop/promotion-performance/overview?${params}`;
}

export function assertJdPromotionCoveragePayload(
  payload: PromotionCoveragePayload | null,
  expected: { startDate: string; endDate: string },
) {
  if (!payload?.coverage || payload.requestedPeriod?.startDate !== expected.startDate
    || payload.requestedPeriod?.endDate !== expected.endDate || !Array.isArray(payload.coverage.promotionDates)) {
    throw new Error("京准通推广覆盖响应与请求范围不一致");
  }
  const dates = payload.coverage.promotionDates;
  if (dates.some((date) => typeof date !== "string" || !validDate(date)
    || date < expected.startDate || date > expected.endDate)) {
    throw new Error("京准通推广覆盖响应包含非法或区间外日期");
  }
  const uniqueDates = [...new Set(dates as string[])].sort();
  const pagination = payload.coverage.promotionDatesPagination;
  if (!pagination || pagination.truncated !== false || pagination.total !== uniqueDates.length
    || pagination.returned !== uniqueDates.length) {
    throw new Error("京准通推广覆盖响应被截断或分页计数不一致");
  }
  return uniqueDates;
}

export async function fetchJdPromotionCoverage(options: {
  baseUrl: string;
  store: Pick<JdStore, "shopName">;
  startDate: string;
  endDate: string;
  request?: typeof fetch;
}) {
  const response = await (options.request ?? fetch)(buildJdPromotionCoverageUrl(
    options.baseUrl,
    options.store,
    options.startDate,
    options.endDate,
  ), { signal: AbortSignal.timeout(30_000) });
  const payload = await response.json().catch(() => null) as PromotionCoveragePayload | null;
  if (!response.ok) throw new Error(`无法读取 ${options.store.shopName} 的京准通推广日期覆盖（HTTP ${response.status}）`);
  return assertJdPromotionCoveragePayload(payload, options);
}

export function planJdPromotionMissingDates(input: {
  startDate: string;
  endDate: string;
  coveredDates: readonly string[];
  maximumDates?: number;
}) {
  if (!validDate(input.startDate) || !validDate(input.endDate) || input.startDate > input.endDate
    || input.coveredDates.some((date) => !validDate(date) || date < input.startDate || date > input.endDate)) {
    throw new Error("京准通推广缺口计划包含无效日期或区间外覆盖");
  }
  const requestedDates = inclusiveDates(input.startDate, input.endDate);
  const covered = new Set(input.coveredDates);
  const missingDates = requestedDates.filter((date) => !covered.has(date));
  const requestedMaximum = input.maximumDates ?? maximumMissingDatesPerRun;
  if (!Number.isInteger(requestedMaximum) || requestedMaximum < 1) throw new Error("京准通推广单轮缺口数量上限无效");
  const maximumDates = Math.min(maximumMissingDatesPerRun, requestedMaximum);
  return {
    dates: missingDates.slice(0, maximumDates),
    missingDateCount: missingDates.length,
    deferredDateCount: Math.max(0, missingDates.length - maximumDates),
  };
}

export function parseJdPromotionDateHeader(value: string | string[] | undefined) {
  if (value === undefined || value === "") return undefined;
  if (typeof value !== "string" || !validDate(value)) throw new Error("京准通 n8n 日期请求头无效");
  return value;
}

export function parseJdPromotionStoreKeyHeader(value: string | string[] | undefined): JdPromotionStoreKey {
  if (typeof value !== "string" || !jdPromotionStoreKeys.includes(value as JdPromotionStoreKey)) {
    throw new Error("京准通 n8n 店铺请求头无效或不在推广工作流白名单");
  }
  return value as JdPromotionStoreKey;
}

export function jdPromotionHelperRequestError(
  stage: "ready" | JdPromotionN8nStage,
  busy: boolean,
  route: JdPromotionHelperRoute,
  requestExecutionId: string | null,
  claimedExecutionId: string | null,
) {
  if (!requestExecutionId) return { error: "missing_or_invalid_execution_id" as const };
  if (claimedExecutionId && requestExecutionId !== claimedExecutionId) return { error: "execution_mismatch" as const };
  if (!claimedExecutionId && route !== "/jd-promotion/plan") return { error: "execution_not_claimed" as const, expected: "/jd-promotion/plan" as const };
  if (busy) return { error: "pipeline_busy" as const };
  if (route === "/jd-promotion/plan") {
    return ["ready", "planned", "executed", "completed"].includes(stage)
      ? null
      : { error: "invalid_stage" as const, expected: "ready|planned|executed|completed", actual: stage };
  }
  if (route === "/jd-promotion/run") {
    return stage === "planned" || stage === "executed"
      ? null
      : { error: "invalid_stage" as const, expected: "planned|executed", actual: stage };
  }
  return stage === "executed" || stage === "completed"
    ? null
    : { error: "invalid_stage" as const, expected: "executed|completed", actual: stage };
}

export async function planJdPromotionN8nRun(options: PlanOptions) {
  if (!validExecutionId(options.executionId)) throw new Error("京准通 n8n execution ID 无效");
  const paths = pathsFor(options.root);
  if (options.store && options.storeKey && options.store.storeKey !== options.storeKey) {
    throw new Error("京准通工作流店铺对象与受控请求头不一致");
  }
  const requestedStoreKey = parseJdPromotionStoreKeyHeader(options.storeKey ?? options.store?.storeKey);
  const store = options.store ?? await getJdStore(requestedStoreKey);
  if (!store.enabled || store.storeKey !== requestedStoreKey) throw new Error("京准通工作流店铺未启用或与受控请求头不一致");
  const yesterday = shanghaiYesterday(options.now ?? new Date());
  const scannedStartDate = options.startDate ?? store.promotionInitialStartDate;
  const scannedEndDate = options.endDate ?? (options.startDate ?? yesterday);
  if (!scannedStartDate) throw new Error("京准通工作流店铺缺少推广缺口扫描起始日");
  if (!validDate(scannedStartDate) || !validDate(scannedEndDate) || scannedStartDate > scannedEndDate || scannedEndDate > yesterday) {
    throw new Error("京准通 n8n 计划要求不晚于上海昨天的有效日期范围");
  }
  inclusiveDates(scannedStartDate, scannedEndDate);
  const baseUrl = normalizeLocalBaseUrl(options.baseUrl ?? process.env.OPERATIONS_SYSTEM_URL ?? "http://localhost:3000");
  const coverage = await fetchJdPromotionCoverage({
    baseUrl,
    store,
    startDate: scannedStartDate,
    endDate: scannedEndDate,
    request: options.request,
  });
  const missing = planJdPromotionMissingDates({ startDate: scannedStartDate, endDate: scannedEndDate, coveredDates: coverage });
  if (missing.dates.length > 0) {
    const profiles = await (options.profileStatus ?? getJdProfilesStatus)([store]);
    if (profiles !== "ready") throw new Error(`京准通受控 Chromium 或 ${store.browser.profileName} 缺失、结构无效`);
  }
  const startDate = missing.dates[0] ?? scannedStartDate;
  const endDate = missing.dates.at(-1) ?? scannedEndDate;
  await mkdir(paths.planDirectory, { recursive: true });
  const identity = storeIdentity(store);
  const prior = await findPriorPlan(paths.planDirectory, { baseUrl, scannedStartDate, scannedEndDate, store: identity });
  if (prior && (prior.ownerExecutionId === options.executionId || prior.stage !== "completed")) {
    if (prior.stage === "running" && prior.ownerExecutionId !== options.executionId) throw new Error("京准通同范围已有执行中的 n8n 运行，拒绝跨执行接管");
    prior.ownerExecutionId = options.executionId;
    if (prior.stage === "executed" || prior.stage === "completed" || prior.failure?.stage === "verify") {
      if (prior.stage !== "completed") prior.stage = "executed";
      delete prior.failure;
      await persistPlan(paths.planDirectory, prior);
      return prior;
    }
    prior.startDate = startDate;
    prior.endDate = endDate;
    prior.dates = missing.dates;
    prior.missingDateCount = missing.missingDateCount;
    prior.deferredDateCount = missing.deferredDateCount;
    prior.results = prior.results.filter((result) => missing.dates.includes(result.startDate));
    prior.stage = missing.dates.length === 0 ? "executed" : "planned";
    delete prior.failure;
    await persistPlan(paths.planDirectory, prior);
    return prior;
  }
  const plan: JdPromotionN8nPlan = {
    version: 2,
    runId: options.runIdFactory?.() ?? `jd-promotion-n8n-${randomUUID()}`,
    generatedAt: (options.now ?? new Date()).toISOString(),
    updatedAt: (options.now ?? new Date()).toISOString(),
    baseUrl,
    ownerExecutionId: options.executionId,
    store: identity,
    scannedStartDate,
    scannedEndDate,
    startDate,
    endDate,
    dates: missing.dates,
    missingDateCount: missing.missingDateCount,
    deferredDateCount: missing.deferredDateCount,
    stage: missing.dates.length === 0 ? "executed" : "planned",
    results: [],
  };
  if (!validRunId(plan.runId)) throw new Error("京准通 n8n 运行编号无效");
  await persistPlan(paths.planDirectory, plan);
  return plan;
}

export function publicJdPromotionPlan(plan: JdPromotionN8nPlan) {
  return {
    ok: true,
    stage: "plan",
    runId: plan.runId,
    storeKey: plan.store.storeKey,
    scannedStartDate: plan.scannedStartDate,
    scannedEndDate: plan.scannedEndDate,
    startDate: plan.startDate,
    endDate: plan.endDate,
    dates: plan.dates,
    missingDateCount: plan.missingDateCount,
    deferredDateCount: plan.deferredDateCount,
    verificationOnly: plan.stage === "executed" || plan.stage === "completed",
  };
}

function assertDailyResult(result: JdPromotionExportResult, store: JdStore, date: string, expectedRunId: string) {
  if (!result.ok || result.storeKey !== store.storeKey || result.shopName !== store.shopName
    || result.runId !== expectedRunId || result.startDate !== date || result.endDate !== date
    || result.importResult.source !== "jd_promotion" || result.importResult.dataset !== "ad"
    || result.importResult.platform !== "京东" || result.importResult.shopName !== store.shopName
    || result.importResult.dateMin !== date || result.importResult.dateMax !== date
    || result.importResult.warningCount !== 0 || result.importResult.rowCount !== result.rowCount) {
    throw new Error("京准通逐日 runner 未返回完整下载、导入和落库证明");
  }
}

export async function runJdPromotionN8nPlan(plan: JdPromotionN8nPlan, options: RunOptions = {}) {
  const paths = pathsFor(options.root);
  if (plan.stage === "executed" || plan.stage === "completed") {
    return { ok: true, stage: "run", runId: plan.runId, verificationOnly: true, status: plan.dates.length === 0 ? "no_gap" : "resumed" };
  }
  if (plan.stage !== "planned" || !validExecutionId(plan.ownerExecutionId) || !validRunId(plan.runId) || plan.dates.length === 0) {
    throw new Error("京准通 n8n 计划尚未进入可执行阶段或格式无效");
  }
  const store = options.store ?? await getJdStore(plan.store.storeKey);
  if (!sameStore(plan.store, storeIdentity(store))) throw new Error("京准通店铺注册表身份已变化，拒绝用旧计划执行");
  plan.stage = "running";
  delete plan.failure;
  await persistPlan(paths.planDirectory, plan);
  try {
    for (const date of plan.dates) {
      const expectedRunId = dailyRunId(plan.runId, date);
      const existing = plan.results.find((result) => result.startDate === date && result.endDate === date);
      if (existing) {
        assertDailyResult(existing, store, date, expectedRunId);
        continue;
      }
      const result = await (options.run ?? runJdPromotionExport)({
        storeKey: store.storeKey,
        startDate: date,
        endDate: date,
        baseUrl: plan.baseUrl,
        runId: expectedRunId,
        visibleRecovery: false,
        interactiveLogin: false,
      });
      assertDailyResult(result, store, date, expectedRunId);
      plan.results.push(result);
      await persistPlan(paths.planDirectory, plan);
    }
    plan.stage = "executed";
    await persistPlan(paths.planDirectory, plan);
    return {
      ok: true,
      stage: "run",
      runId: plan.runId,
      verificationOnly: false,
      status: "executed",
      completedDates: plan.results.map((result) => result.startDate).sort(),
      rowCount: plan.results.reduce((sum, result) => sum + result.rowCount, 0),
      batchIds: plan.results.map((result) => result.importResult.batchId),
    };
  } catch (error) {
    plan.stage = "failed";
    plan.failure = { code: "JD_PROMOTION_N8N_RUN_FAILED", stage: "run", message: safeError(error), at: new Date().toISOString() };
    await persistPlan(paths.planDirectory, plan);
    throw error;
  }
}

export async function verifyJdPromotionN8nPlan(plan: JdPromotionN8nPlan, options: VerifyOptions = {}) {
  const paths = pathsFor(options.root);
  if (plan.stage !== "executed" && plan.stage !== "completed") throw new Error("京准通 n8n 运行尚未进入可核验阶段");
  const store = options.store ?? await getJdStore(plan.store.storeKey);
  if (!sameStore(plan.store, storeIdentity(store))) throw new Error("京准通 C 节点店铺身份与计划不一致");
  try {
    const resultDates = plan.results.map((result) => result.startDate).sort();
    if (plan.results.length !== plan.dates.length || resultDates.join("\u001f") !== [...plan.dates].sort().join("\u001f")) {
      throw new Error("京准通逐日结果日期与缺口计划不一致");
    }
    for (const result of plan.results) {
      const bytes = new Uint8Array(await readFile(result.savedPath));
      const inspection = inspectJdPromotionCsv(bytes, result.startDate, result.endDate);
      if (inspection.sha256 !== result.sha256 || inspection.rowCount !== result.rowCount
        || inspection.accountNicknames.join("\u001f") !== result.accountNicknames.join("\u001f")) {
        throw new Error("京准通 C 节点重验的文件哈希、行数或账户集合与 B 节点不一致");
      }
      await verifyJdPromotionPublishedBatch({
        storeKey: store.storeKey,
        startDate: result.startDate,
        endDate: result.endDate,
        baseUrl: plan.baseUrl,
        runId: result.runId,
        visibleRecovery: false,
        interactiveLogin: false,
      }, store, result.importResult, options.request ?? fetch);
    }
    const coverageStart = plan.dates[0] ?? plan.scannedStartDate;
    const coverageEnd = plan.dates.at(-1) ?? plan.scannedEndDate;
    const coverage = await fetchJdPromotionCoverage({
      baseUrl: plan.baseUrl,
      store,
      startDate: coverageStart,
      endDate: coverageEnd,
      request: options.request,
    });
    const expectedDates = plan.dates.length > 0 ? plan.dates : inclusiveDates(plan.scannedStartDate, plan.scannedEndDate);
    const missingAfterImport = expectedDates.filter((date) => !coverage.includes(date));
    if (missingAfterImport.length > 0) throw new Error(`京准通逐日导入后仍缺少日期覆盖：${missingAfterImport.join("、")}`);
    plan.stage = "completed";
    await persistPlan(paths.planDirectory, plan);
    return {
      ok: true,
      stage: "verify",
      status: plan.dates.length === 0 ? "no_gap" : "completed",
      runId: plan.runId,
      startDate: plan.startDate,
      endDate: plan.endDate,
      completedDates: plan.results.map((result) => result.startDate).sort(),
      rowCount: plan.results.reduce((sum, result) => sum + result.rowCount, 0),
      batchIds: plan.results.map((result) => result.importResult.batchId),
      warningCount: 0,
      deferredDateCount: plan.deferredDateCount,
    };
  } catch (error) {
    plan.stage = "failed";
    plan.failure = { code: "JD_PROMOTION_N8N_VERIFY_FAILED", stage: "verify", message: safeError(error), at: new Date().toISOString() };
    await persistPlan(paths.planDirectory, plan);
    throw error;
  }
}
