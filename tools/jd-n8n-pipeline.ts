import { randomUUID } from "node:crypto";
import { mkdir, readdir, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { readJsonFile, writeJsonAtomic } from "../lib/jackyun/json-file";
import { isJdNaturalDate } from "../lib/jd/date-range";
import { loadJdStores, type JdStore } from "../lib/jd/store-registry";
import { netshopOutletKey } from "../lib/netshop/query-contract";
import {
  auditCounts,
  runMultiStore,
  shanghaiDefaultRange,
  validateStepResult,
  type AuditItem,
  type RunnerAudit,
} from "./jd-multi-store-runner";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const planDirectoryName = "jd-n8n-pipeline";

export type JdN8nStage = "planned" | "running" | "executed" | "completed" | "failed";
export type JdHelperRoute = "/jd/plan" | "/jd/run" | "/jd/verify";
export type JdProfileStatus = "ready" | "missing" | "invalid";
export type JdDailyDimension = "sku" | "spu";

type JdDailyCoverage = {
  storeKey: string;
  dimension: JdDailyDimension;
  actualDates: string[];
  missingDates: string[];
};

export type JdN8nOperation = {
  storeKey: string;
  step: "jd_product_master" | "jd_sku_daily" | "spu_daily";
  startDate?: string;
  endDate?: string;
  status: "planned" | "running" | "completed" | "failed";
  runnerAuditPath?: string;
};

export type JdN8nPlan = {
  version: 2;
  runId: string;
  generatedAt: string;
  updatedAt: string;
  baseUrl: string;
  startDate: string;
  endDate: string;
  ownerExecutionId: string;
  storeKeys: string[];
  stores: Array<{ storeKey: string; shopId: string; shopName: string }>;
  coverage: JdDailyCoverage[];
  operations: JdN8nOperation[];
  silentNoWindow?: boolean;
  stage: JdN8nStage;
  failure?: { code: string; stage: "plan" | "run" | "verify"; message: string; at: string };
};

type RuntimePaths = { root: string; planDirectory: string };
type PlanOptions = {
  root?: string;
  now?: Date;
  baseUrl?: string;
  stores?: JdStore[];
  request?: typeof fetch;
  runIdFactory?: () => string;
  profileStatus?: (stores: readonly JdStore[]) => Promise<JdProfileStatus>;
  executionId: string;
  silentNoWindow?: boolean;
};
type RunOptions = {
  root?: string;
  run?: typeof runMultiStore;
  stores?: JdStore[];
};
type VerifyOptions = { root?: string; stores?: JdStore[]; request?: typeof fetch };

function pathsFor(root = projectRoot): RuntimePaths {
  const resolvedRoot = path.resolve(root);
  return { root: resolvedRoot, planDirectory: path.join(resolvedRoot, "outputs", planDirectoryName) };
}

function validDate(value: string) {
  return isJdNaturalDate(value);
}

function validRunId(value: string) {
  return /^[A-Za-z0-9._-]{1,96}$/.test(value);
}

function validExecutionId(value: string | undefined) {
  return typeof value === "string" && value.length >= 1 && value.length <= 128 && /^[A-Za-z0-9._:-]+$/.test(value);
}

function storeIdentity(stores: readonly JdStore[]) {
  return stores.map((store) => ({ storeKey: store.storeKey, shopId: store.shopId, shopName: store.shopName }));
}

function sameStoreIdentity(left: readonly { storeKey: string; shopId: string; shopName: string }[], right: readonly { storeKey: string; shopId: string; shopName: string }[]) {
  return left.length === right.length && left.every((store, index) => store.storeKey === right[index]?.storeKey
    && store.shopId === right[index]?.shopId && store.shopName === right[index]?.shopName);
}

function inclusiveDates(startDate: string, endDate: string) {
  const dates: string[] = [];
  const cursor = new Date(`${startDate}T00:00:00Z`);
  const end = new Date(`${endDate}T00:00:00Z`);
  while (cursor <= end) {
    dates.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return dates;
}

export function contiguousJdMissingRanges(dates: readonly string[]) {
  const ranges: Array<{ startDate: string; endDate: string }> = [];
  for (const value of dates) {
    if (!validDate(value) || (ranges.length > 0 && value <= ranges.at(-1)!.endDate)) {
      throw new Error("京东商品日缺口日期无效、重复或未按升序排列");
    }
    const current = ranges.at(-1);
    if (current) {
      const next = new Date(`${current.endDate}T00:00:00Z`);
      next.setUTCDate(next.getUTCDate() + 1);
      if (next.toISOString().slice(0, 10) === value) {
        current.endDate = value;
        continue;
      }
    }
    ranges.push({ startDate: value, endDate: value });
  }
  return ranges;
}

type JdCoveragePayload = {
  dimension?: unknown;
  dataset?: unknown;
  requestedPeriod?: { startDate?: unknown; endDate?: unknown };
  coverage?: { actualDates?: unknown; missingDates?: unknown; truncated?: unknown };
};

async function readJdDailyCoverage({
  baseUrl,
  store,
  dimension,
  startDate,
  endDate,
  request,
}: {
  baseUrl: string;
  store: JdStore;
  dimension: JdDailyDimension;
  startDate: string;
  endDate: string;
  request: typeof fetch;
}) {
  const params = new URLSearchParams({
    dimension,
    view: "full",
    pageSize: "1",
    platform: "京东",
    outlet: netshopOutletKey("京东", store.shopName),
    startDate,
    endDate,
  });
  const response = await request(`${baseUrl}/api/netshop/product-performance?${params}`, {
    headers: { accept: "application/json" },
    signal: AbortSignal.timeout(30_000),
  });
  const payload = await response.json().catch(() => null) as JdCoveragePayload | null;
  const expectedDataset = dimension === "sku" ? "sku_daily" : "spu_daily";
  const actualDates = payload?.coverage?.actualDates;
  const missingDates = payload?.coverage?.missingDates;
  if (!response.ok || payload?.dimension !== dimension || payload.dataset !== expectedDataset
    || payload.requestedPeriod?.startDate !== startDate || payload.requestedPeriod?.endDate !== endDate
    || !Array.isArray(actualDates) || !Array.isArray(missingDates) || payload.coverage?.truncated !== false
    || actualDates.some((date) => typeof date !== "string" || !validDate(date))
    || missingDates.some((date) => typeof date !== "string" || !validDate(date))) {
    throw new Error(`无法读取 ${store.shopName} 的京东 ${dimension.toUpperCase()} 分天完整覆盖`);
  }
  const expected = inclusiveDates(startDate, endDate);
  const actual = [...new Set(actualDates as string[])].sort();
  const missing = [...new Set(missingDates as string[])].sort();
  if (actual.length !== (actualDates as string[]).length || missing.length !== (missingDates as string[]).length
    || actual.some((date) => date < startDate || date > endDate)
    || missing.some((date) => date < startDate || date > endDate)
    || expected.some((date) => (actual.includes(date) ? 1 : 0) + (missing.includes(date) ? 1 : 0) !== 1)
    || actual.some((date) => missing.includes(date))) {
    throw new Error(`${store.shopName} 的京东 ${dimension.toUpperCase()} 分天覆盖不是完整互斥日期集合`);
  }
  return { storeKey: store.storeKey, dimension, actualDates: actual, missingDates: missing } satisfies JdDailyCoverage;
}

function safeError(error: unknown) {
  return (error instanceof Error ? error.message : String(error))
    .replace(/https?:\/\/\S+/gi, "<url>")
    .replace(/(cookie|token|password|secret)\s*[=:]\s*\S+/gi, (_match, key: string) => `${key}=<redacted>`)
    .slice(0, 1_000);
}

export function normalizeJdLocalBaseUrl(value: string) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["localhost", "127.0.0.1"].includes(url.hostname)
    || (url.pathname !== "/" && url.pathname !== "") || url.username || url.password || url.search || url.hash) {
    throw new Error("京东 n8n 工作流只允许连接本机运营系统根地址");
  }
  return url.toString().replace(/\/$/, "");
}

export function createJdN8nRunId() {
  return `jd-n8n-${randomUUID()}`;
}

export function jdHelperRequestError(
  stage: "ready" | JdN8nStage,
  busy: boolean,
  route: JdHelperRoute,
  requestExecutionId: string | null,
  claimedExecutionId: string | null,
) {
  if (!requestExecutionId) return { error: "missing_or_invalid_execution_id" as const };
  if (claimedExecutionId && requestExecutionId !== claimedExecutionId) return { error: "execution_mismatch" as const };
  if (!claimedExecutionId && route !== "/jd/plan") return { error: "execution_not_claimed" as const, expected: "/jd/plan" as const };
  if (busy) return { error: "pipeline_busy" as const };
  if (route === "/jd/plan") {
    // The same n8n execution may retry A after the response was lost. The
    // persisted plan remains the source of truth and planJdN8nRun is idempotent.
    return ["ready", "planned", "executed", "completed"].includes(stage)
      ? null
      : { error: "invalid_stage" as const, expected: "ready|planned|executed|completed", actual: stage };
  }
  if (route === "/jd/run") {
    return stage === "planned" || stage === "executed"
      ? null
      : { error: "invalid_stage" as const, expected: "planned|executed", actual: stage };
  }
  return stage === "executed" || stage === "completed"
    ? null
    : { error: "invalid_stage" as const, expected: "executed|completed", actual: stage };
}

export async function getJdProfilesStatus(stores: readonly JdStore[]): Promise<JdProfileStatus> {
  if (!stores.length) return "missing";
  const statuses = await Promise.all(stores.map(async (store) => {
    const [executable, userData, profile, localState] = await Promise.all([
      stat(store.browser.executablePath).catch(() => null),
      stat(store.browser.userDataDir).catch(() => null),
      stat(store.browser.profileDir).catch(() => null),
      stat(path.join(store.browser.userDataDir, "Local State")).catch(() => null),
    ]);
    if (!executable || !userData || !profile || !localState) return "missing" as const;
    return executable.isFile() && userData.isDirectory() && profile.isDirectory() && localState.isFile()
      ? "ready" as const
      : "invalid" as const;
  }));
  return statuses.includes("invalid") ? "invalid" : statuses.includes("missing") ? "missing" : "ready";
}

function planPath(paths: RuntimePaths, runId: string) {
  if (!validRunId(runId)) throw new Error("京东 n8n 运行编号无效");
  return path.join(paths.planDirectory, `plan-${runId}.json`);
}

function resolveRunnerAuditPath(paths: RuntimePaths, value: string) {
  const directory = path.join(paths.root, "outputs", "jd-multi-store-runner");
  const resolved = path.resolve(value);
  const relative = path.relative(directory, resolved);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative) || !/^run-\d+\.json$/.test(path.basename(resolved))) {
    throw new Error("京东 runner 审计路径不属于受控输出目录");
  }
  return resolved;
}

async function persistPlan(paths: RuntimePaths, plan: JdN8nPlan) {
  plan.updatedAt = new Date().toISOString();
  await writeJsonAtomic(planPath(paths, plan.runId), plan);
}

async function findPlanForRange(paths: RuntimePaths, identity: Pick<JdN8nPlan, "baseUrl" | "startDate" | "endDate" | "stores"> & { silentNoWindow: boolean }) {
  const entries = await readdir(paths.planDirectory, { withFileTypes: true }).catch(() => []);
  const plans = await Promise.all(entries.filter((entry) => entry.isFile() && /^plan-[A-Za-z0-9._-]+\.json$/.test(entry.name))
    .map((entry) => readJsonFile<JdN8nPlan>(path.join(paths.planDirectory, entry.name)).catch(() => null)));
  return plans.filter((plan): plan is JdN8nPlan => Boolean(plan))
    .filter((plan) => plan.version === 2 && plan.baseUrl === identity.baseUrl && plan.startDate === identity.startDate && plan.endDate === identity.endDate
      && Boolean(plan.silentNoWindow) === identity.silentNoWindow
      && Array.isArray(plan.stores) && sameStoreIdentity(plan.stores, identity.stores))
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))[0];
}

export async function planJdN8nRun(options: PlanOptions) {
  const paths = pathsFor(options.root);
  const now = options.now ?? new Date();
  const baseUrl = normalizeJdLocalBaseUrl(options.baseUrl ?? process.env.OPERATIONS_SYSTEM_URL ?? "http://localhost:3000");
  const range = shanghaiDefaultRange(now);
  const executionId = options.executionId;
  if (!validExecutionId(executionId)) throw new Error("京东 n8n execution ID 无效");
  const stores = (options.stores ?? await loadJdStores()).filter((store) => store.enabled);
  if (!stores.length) throw new Error("没有启用的京东店铺注册项，拒绝启动工作流");
  const profiles = await (options.profileStatus ?? getJdProfilesStatus)(stores);
  if (profiles !== "ready") throw new Error("京东店铺受控 Chromium 或对应 profile 缺失、结构无效；请先恢复对应店铺会话");
  const response = await (options.request ?? fetch)(`${baseUrl}/`, { signal: AbortSignal.timeout(5_000) });
  if (!response.ok) throw new Error(`本机运营系统不可用 (HTTP ${response.status})`);
  await mkdir(paths.planDirectory, { recursive: true });
  const storesIdentity = storeIdentity(stores);
  const silentNoWindow = options.silentNoWindow === true;
  const prior = await findPlanForRange(paths, { baseUrl, ...range, stores: storesIdentity, silentNoWindow });
  if (prior) {
    assertJdPlanContract(prior);
    if (!validExecutionId(prior.ownerExecutionId)) throw new Error("京东 n8n 既有计划缺少有效执行所有者");
    if (prior.ownerExecutionId !== executionId && prior.stage === "completed") {
      // A later manual/scheduled execution must perform a fresh export so new
      // store SKUs can enter the system; only the original execution is idempotent.
    } else {
      if (prior.stage === "running") {
        throw new Error(`当前范围已有执行中的京东 n8n 运行 ${prior.runId}；拒绝跨执行接管`);
      }
      if (prior.ownerExecutionId !== executionId) prior.ownerExecutionId = executionId;
      const runningOperation = prior.operations.find((operation) => operation.status === "running");
      if (runningOperation) throw new Error("京东 n8n 既有计划包含未关闭的运行步骤，拒绝自动接管");
      if (prior.stage === "failed" && prior.failure?.stage === "verify") {
        prior.stage = "executed";
      } else if (prior.stage === "failed") {
        prior.stage = "planned";
        const failed = prior.operations.find((operation) => operation.status === "failed");
        if (failed) failed.status = "planned";
      }
      delete prior.failure;
      await persistPlan(paths, prior);
      return prior;
    }
  }
  const coverage: JdDailyCoverage[] = [];
  const operations: JdN8nOperation[] = [];
  for (const store of stores) {
    operations.push({ storeKey: store.storeKey, step: "jd_product_master", status: "planned" });
    for (const dimension of ["sku", "spu"] as const) {
      const result = await readJdDailyCoverage({
        baseUrl,
        store,
        dimension,
        startDate: range.startDate,
        endDate: range.endDate,
        request: options.request ?? fetch,
      });
      coverage.push(result);
      const step = dimension === "sku" ? "jd_sku_daily" : "spu_daily";
      operations.push(...contiguousJdMissingRanges(result.missingDates).map((missingRange) => ({
        storeKey: store.storeKey,
        step,
        ...missingRange,
        status: "planned" as const,
      })));
    }
  }
  const plan: JdN8nPlan = {
    version: 2,
    runId: options.runIdFactory?.() ?? createJdN8nRunId(),
    generatedAt: now.toISOString(),
    updatedAt: now.toISOString(),
    baseUrl,
    startDate: range.startDate,
    endDate: range.endDate,
    ownerExecutionId: executionId,
    storeKeys: stores.map((store) => store.storeKey),
    stores: storesIdentity,
    coverage,
    operations,
    silentNoWindow,
    stage: "planned",
  };
  if (!validRunId(plan.runId)) throw new Error("京东 n8n 运行编号无效");
  await persistPlan(paths, plan);
  return plan;
}

export function publicJdPlan(plan: JdN8nPlan) {
  return {
    ok: true,
    stage: "plan",
    runId: plan.runId,
    startDate: plan.startDate,
    endDate: plan.endDate,
    storeCount: plan.storeKeys.length,
    masterOperationCount: plan.operations.filter((operation) => operation.step === "jd_product_master").length,
    skuMissingDateCount: plan.coverage.filter((item) => item.dimension === "sku").reduce((sum, item) => sum + item.missingDates.length, 0),
    spuMissingDateCount: plan.coverage.filter((item) => item.dimension === "spu").reduce((sum, item) => sum + item.missingDates.length, 0),
    dailyOperationCount: plan.operations.filter((operation) => operation.step !== "jd_product_master").length,
    verificationOnly: plan.stage === "executed" || plan.stage === "completed",
    silentNoWindow: Boolean(plan.silentNoWindow),
  };
}

function operationMode(step: JdN8nOperation["step"]): "master" | "sku-daily" | "spu-daily" {
  return step === "jd_product_master" ? "master" : step === "jd_sku_daily" ? "sku-daily" : "spu-daily";
}

function assertJdPlanContract(plan: JdN8nPlan) {
  if (plan.version !== 2 || !validRunId(plan.runId) || !validDate(plan.startDate) || !validDate(plan.endDate)
    || plan.startDate > plan.endDate || plan.baseUrl !== normalizeJdLocalBaseUrl(plan.baseUrl)
    || !validExecutionId(plan.ownerExecutionId) || !Array.isArray(plan.stores) || !Array.isArray(plan.coverage)
    || !Array.isArray(plan.operations) || (plan.silentNoWindow !== undefined && typeof plan.silentNoWindow !== "boolean")
    || plan.stores.some((store) => !store.storeKey || !store.shopId || !store.shopName)
    || plan.storeKeys.length !== plan.stores.length || plan.storeKeys.some((storeKey, index) => storeKey !== plan.stores[index]?.storeKey)) {
    throw new Error("京东 n8n 计划格式无效");
  }
  for (const store of plan.stores) {
    const storeCoverage = plan.coverage.filter((item) => item.storeKey === store.storeKey);
    if (storeCoverage.length !== 2 || !storeCoverage.some((item) => item.dimension === "sku")
      || !storeCoverage.some((item) => item.dimension === "spu")) {
      throw new Error(`京东 ${store.storeKey} 计划缺少唯一 SKU/SPU 覆盖证据`);
    }
    const masterOperations = plan.operations.filter((item) => item.storeKey === store.storeKey && item.step === "jd_product_master");
    if (masterOperations.length !== 1 || masterOperations[0]?.startDate || masterOperations[0]?.endDate) {
      throw new Error(`京东 ${store.storeKey} 计划没有且仅有一个每日商品主数据步骤`);
    }
    for (const coverage of storeCoverage) {
      const expectedDates = inclusiveDates(plan.startDate, plan.endDate);
      if (!Array.isArray(coverage.actualDates) || !Array.isArray(coverage.missingDates)
        || coverage.actualDates.some((date) => !validDate(date)) || coverage.missingDates.some((date) => !validDate(date))
        || new Set(coverage.actualDates).size !== coverage.actualDates.length || new Set(coverage.missingDates).size !== coverage.missingDates.length
        || coverage.actualDates.some((date, index) => index > 0 && date <= coverage.actualDates[index - 1]!)
        || coverage.missingDates.some((date, index) => index > 0 && date <= coverage.missingDates[index - 1]!)
        || expectedDates.some((date) => (coverage.actualDates.includes(date) ? 1 : 0) + (coverage.missingDates.includes(date) ? 1 : 0) !== 1)
        || coverage.actualDates.some((date) => date < plan.startDate || date > plan.endDate)
        || coverage.missingDates.some((date) => date < plan.startDate || date > plan.endDate)) {
        throw new Error(`京东 ${store.storeKey}/${coverage.dimension} 覆盖证据不是完整互斥日期集合`);
      }
      const step = coverage.dimension === "sku" ? "jd_sku_daily" : "spu_daily";
      const actualRanges = plan.operations.filter((item) => item.storeKey === store.storeKey && item.step === step)
        .map((item) => ({ startDate: item.startDate, endDate: item.endDate }));
      const expectedRanges = contiguousJdMissingRanges(coverage.missingDates);
      if (actualRanges.length !== expectedRanges.length || actualRanges.some((item, index) => item.startDate !== expectedRanges[index]?.startDate
        || item.endDate !== expectedRanges[index]?.endDate)) {
        throw new Error(`京东 ${store.storeKey}/${coverage.dimension} 下载计划与缺口日期不一致`);
      }
    }
  }
  if (plan.coverage.some((item) => !plan.storeKeys.includes(item.storeKey))
    || plan.operations.some((item) => !plan.storeKeys.includes(item.storeKey)
      || !["jd_product_master", "jd_sku_daily", "spu_daily"].includes(item.step)
      || !["planned", "running", "completed", "failed"].includes(item.status)
      || (item.step !== "jd_product_master" && (!item.startDate || !item.endDate)))) {
    throw new Error("京东 n8n 计划包含越界店铺、步骤或日期");
  }
}

export async function runJdN8nPlan(plan: JdN8nPlan, options: RunOptions = {}) {
  const paths = pathsFor(options.root);
  assertJdPlanContract(plan);
  if (plan.stage === "executed" || plan.stage === "completed") {
    return { ok: true, stage: "run", runId: plan.runId, verificationOnly: true };
  }
  if (plan.stage !== "planned") throw new Error("京东 n8n 计划尚未进入可执行阶段");
  const stores = (options.stores ?? await loadJdStores()).filter((store) => store.enabled);
  if (!sameStoreIdentity(plan.stores, storeIdentity(stores)) || plan.storeKeys.length !== stores.length
    || plan.storeKeys.some((storeKey, index) => storeKey !== stores[index]?.storeKey)) {
    throw new Error("京东店铺注册表身份或顺序已变化，拒绝用旧计划执行");
  }
  plan.stage = "running";
  delete plan.failure;
  await persistPlan(paths, plan);
  for (const operation of plan.operations) {
    if (operation.status === "completed") continue;
    const store = stores.find((candidate) => candidate.storeKey === operation.storeKey)!;
    operation.status = "running";
    await persistPlan(paths, plan);
    try {
      const startDate = operation.startDate ?? plan.startDate;
      const endDate = operation.endDate ?? plan.endDate;
      const result = await (options.run ?? runMultiStore)({
        mode: operationMode(operation.step),
        startDate,
        endDate,
        storeKey: operation.storeKey,
        dryRun: false,
        silentNoWindow: Boolean(plan.silentNoWindow),
        baseUrl: plan.baseUrl,
        ...(operation.runnerAuditPath ? { resumeAuditPath: resolveRunnerAuditPath(paths, operation.runnerAuditPath) } : {}),
      }, stores);
      operation.runnerAuditPath = result.auditPath;
      const item = result.audit.items[0];
      if (!result.ok || result.audit.items.length !== 1 || auditCounts(result.audit.items).completed !== 1
        || item?.storeKey !== operation.storeKey || item.step !== operation.step || item.status !== "completed") {
        throw new Error(`京东 ${operation.storeKey}/${operation.step} runner 未完成计划步骤`);
      }
      operation.status = "completed";
      await persistPlan(paths, plan);
    } catch (error) {
      operation.status = "failed";
      plan.stage = "failed";
      plan.failure = { code: "JD_N8N_RUN_FAILED", stage: "run", message: safeError(error), at: new Date().toISOString() };
      await persistPlan(paths, plan);
      throw error;
    }
  }
  plan.stage = "executed";
  await persistPlan(paths, plan);
  return {
    ok: true,
    stage: "run",
    runId: plan.runId,
    verificationOnly: false,
    storeCount: plan.storeKeys.length,
    operationCount: plan.operations.length,
  };
}

export async function verifyJdN8nPlan(plan: JdN8nPlan, options: VerifyOptions = {}) {
  const paths = pathsFor(options.root);
  assertJdPlanContract(plan);
  if (plan.stage !== "executed" && plan.stage !== "completed") {
    throw new Error("京东 n8n 运行尚未进入可核验阶段");
  }
  try {
    const stores = (options.stores ?? await loadJdStores()).filter((store) => plan.storeKeys.includes(store.storeKey));
    if (!sameStoreIdentity(plan.stores, storeIdentity(stores)) || stores.length !== plan.storeKeys.length) {
      throw new Error("京东店铺注册表身份或顺序已变化，拒绝核验旧计划");
    }
    for (const operation of plan.operations) {
      if (operation.status !== "completed" || !operation.runnerAuditPath) {
        throw new Error(`京东 ${operation.storeKey}/${operation.step} 缺少完成审计`);
      }
      const store = stores.find((candidate) => candidate.storeKey === operation.storeKey)!;
      const startDate = operation.startDate ?? plan.startDate;
      const endDate = operation.endDate ?? plan.endDate;
      const audit = await readJsonFile<RunnerAudit>(resolveRunnerAuditPath(paths, operation.runnerAuditPath));
      const item = audit.items[0];
      if (audit.baseUrl !== plan.baseUrl || audit.mode !== operationMode(operation.step) || audit.dryRun
        || audit.startDate !== startDate || audit.endDate !== endDate
        || Boolean(audit.silentNoWindow) !== Boolean(plan.silentNoWindow)
        || audit.storeKeys.length !== 1 || audit.storeKeys[0] !== operation.storeKey || audit.items.length !== 1
        || !item || item.storeKey !== operation.storeKey || item.shopName !== store.shopName || item.step !== operation.step
        || item.status !== "completed" || !item.batchId || typeof item.rowCount !== "number" || !item.importResult) {
        throw new Error(`京东 ${operation.storeKey}/${operation.step} runner 审计与计划不一致`);
      }
      const invalid = validateStepResult(operation.step, { importResult: item.importResult }, store, { startDate, endDate });
      if (invalid) throw new Error(`京东 ${store.storeKey}/${operation.step} 导入复核失败: ${invalid}`);
      await verifyPublishedBatch({
        baseUrl: plan.baseUrl,
        store,
        step: operation.step,
        item,
        startDate,
        endDate,
        request: options.request ?? fetch,
      });
    }
    const missingAfterImport: Array<{ storeKey: string; dimension: JdDailyDimension; dates: string[] }> = [];
    for (const store of stores) {
      for (const dimension of ["sku", "spu"] as const) {
        const coverage = await readJdDailyCoverage({
          baseUrl: plan.baseUrl,
          store,
          dimension,
          startDate: plan.startDate,
          endDate: plan.endDate,
          request: options.request ?? fetch,
        });
        if (coverage.missingDates.length > 0) {
          missingAfterImport.push({ storeKey: store.storeKey, dimension, dates: coverage.missingDates });
        }
      }
    }
    if (missingAfterImport.length > 0) {
      throw new Error(`京东 SKU/SPU 导入后仍有缺口：${missingAfterImport.map((item) => `${item.storeKey}/${item.dimension}:${item.dates.join(",")}`).join("；")}`);
    }
    plan.stage = "completed";
    await persistPlan(paths, plan);
    return { ok: true, stage: "verify", runId: plan.runId, startDate: plan.startDate, endDate: plan.endDate, stores: stores.map((store) => store.storeKey), missingAfterImport: [] };
  } catch (error) {
    plan.stage = "failed";
    plan.failure = { code: "JD_N8N_VERIFY_FAILED", stage: "verify", message: safeError(error), at: new Date().toISOString() };
    await persistPlan(paths, plan);
    throw error;
  }
}

async function verifyPublishedBatch({
  baseUrl,
  store,
  step,
  item,
  startDate,
  endDate,
  request,
}: {
  baseUrl: string;
  store: JdStore;
  step: "jd_product_master" | "jd_sku_daily" | "spu_daily";
  item: AuditItem;
  startDate: string;
  endDate: string;
  request: typeof fetch;
}) {
  const source = step === "jd_product_master" ? "jd_product_master" : "jd_sku_daily";
  const params = new URLSearchParams({ limit: "1", batchId: item.batchId!, source, platform: "京东", shop: store.shopName });
  const response = await request(`${baseUrl}/api/netshop/import?${params}`, { signal: AbortSignal.timeout(30_000) });
  const payload = await response.json().catch(() => null) as { items?: unknown } | null;
  const batches = Array.isArray(payload?.items) ? payload.items : [];
  if (!response.ok || batches.length !== 1 || !batches[0] || typeof batches[0] !== "object") {
    throw new Error(`京东 ${store.storeKey}/${step} 无法读取精确导入批次`);
  }
  const batch = batches[0] as Record<string, unknown>;
  const dataset = step === "jd_product_master" ? "product_master" : step === "jd_sku_daily" ? "sku_daily" : "spu_daily";
  if (batch.id !== item.batchId || batch.status !== "completed" || batch.source !== source || batch.dataset !== dataset
    || batch.platform !== "京东" || batch.shopName !== store.shopName || batch.warningCount !== 0 || batch.rowCount !== item.rowCount
    || (step !== "jd_product_master" && (batch.dateMin !== startDate || batch.dateMax !== endDate))) {
    throw new Error(`京东 ${store.storeKey}/${step} 已发布批次与审计不一致`);
  }
}
