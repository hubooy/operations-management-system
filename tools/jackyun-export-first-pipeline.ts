import { apiTaskWindowObserved, apiTaskWindowStart } from "../lib/jackyun/api-clock";
import type { JackyunServerClock } from "../lib/jackyun/direct-http";
import { createHash } from "node:crypto";
import { mkdir, readFile, realpath, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";
import { readJsonFile, readJsonFileOr, writeJsonAtomic } from "../lib/jackyun/json-file";
import { assertBoundDownloadProvenance } from "../lib/jackyun/download-provenance";
import { assertJackyunHandoffEvidence, assertJackyunSnapshotEvidence, jackyunCaptureDate,
  jackyunExportFirstPolicyVersion, jackyunExportOrder } from "../lib/jackyun/run-contract";
import { jackyunModuleOrder, prepareJackyunWorkbook, type JackyunModule } from "../lib/jackyun/post-download";
import { verifyJackyunModuleArtifact, type JackyunArtifactManifestModule } from "../lib/jackyun/run-artifact-verification";
import { withJackyunRunLock } from "../lib/jackyun/run-lock";
import { assertClosedPreflight, preflightClosurePath, publishAutomatedCredentialClosure, publishAutomatedPageClosure } from "../lib/jackyun/preflight-recovery";
import { classifyJackyunPreflightFailure } from "../lib/jackyun/preflight-failure";
import { isRetryableJackyunCredentialPreparationFailure } from "../lib/jackyun/windows-dpapi";
import { claimJackyunResumePermit } from "../lib/jackyun/execution-resume";
import { claimWebConfirmationRecovery } from "../lib/jackyun/web-session-recovery";
import { claimHttpScopeRecovery } from "../lib/jackyun/http-scope-recovery";
import { claimImportRecovery, type ImportRecoveryBinding } from "../lib/jackyun/import-recovery";
import { claimJackyunApiResumePermit } from "../lib/jackyun/api-execution-resume";
import { runController } from "./jackyun-browser-controller";
import { jackyunWebSessionTransport } from "../lib/jackyun/web-session-export";
import { jackyunDirectTransport } from "../lib/jackyun/direct-export";
import { jackyunApiTransport } from "../lib/jackyun/api-plan";
import { runApiExports } from "./jackyun-api-export";
import { runJackyunDownload, type JackyunDownloadRunOptions } from "./jackyun-download-runner";
import type { BrowserHandoff } from "./jackyun-daily-runner";
import { getJackyunProfileStatus, normalizeJackyunLocalBaseUrl, verifyPublishedJackyunBatches } from "./jackyun-n8n-pipeline";
import { jackyunSalesPeriod, rollingJackyunSalesStartDate } from "../lib/jackyun/sales-period";

export const jackyunExportFirstPrefix = "/jackyun/export-first/";
export const jackyunWebSessionActions = ["plan-web-session", "export-all", "validate", "import", "verify"];
export const jackyunDirectActions = ["plan-direct-http", "export-all", "validate", "import", "verify"];
export const jackyunExportFirstActions = ["plan", ...jackyunExportOrder.map(module => `export/${module}`), ...jackyunWebSessionActions, "plan-direct-http", "plan-api"];
type Phase = "exporting" | "exported" | "validating" | "validated" | "importing" | "imported" | "completed";
type ExportReceipt = { handoffSha256: string; fileSha256: string; bytes: number };
export type JackyunExportFirstPlan = {
  version: 1 | 2;
  protocol: typeof jackyunExportFirstPolicyVersion;
  executionId: string;
  runId: string;
  runDate: string;
  asOfDate: string;
  salesStartDate?: string;
  baseUrl: string;
  createdAt: string;
  phase: Phase;
  exports: Partial<Record<JackyunModule, ExportReceipt>>;
  exportIntent?: JackyunModule;
  exportTransport?: typeof jackyunWebSessionTransport | typeof jackyunDirectTransport | typeof jackyunApiTransport;
  completedAt?: string;
};
type Policy = {
  version: string;
  browser: { downloadDirectory: string; allowedDownloadHosts: string[]; controller: { profileDirectory: string } };
};
export type ExportFirstDependencies = {
  root: string;
  lockDirectory?: string;
  now?: () => Date;
  request?: typeof fetch;
  profileReady?: () => Promise<boolean>;
  runBrowser?: typeof runController;
  runApi?: typeof runApiExports;
  runDownload?: typeof runJackyunDownload;
  recoverPreviousPreflight?: (previousId: string, replacementId: string, at: string) => Promise<void>;
};
const sha = (bytes: Uint8Array | string) => createHash("sha256").update(bytes).digest("hex");
const nowOf = (deps: ExportFirstDependencies) => (deps.now?.() ?? new Date()).toISOString();
function paths(root: string) {
  return {
    outputRoot: path.join(root, "outputs", "jackyun-import-runs"),
    eventRoot: path.join(root, "outputs", "jackyun-browser-events"),
    pipelineRoot: path.join(root, "outputs", "jackyun-export-first"),
    validationRoot: path.join(root, "outputs", "jackyun-export-first-validation"),
  };
}
function handoffPath(root: string, plan: JackyunExportFirstPlan, module: JackyunModule) {
  return path.join(paths(root).eventRoot, plan.runId, `${String(jackyunModuleOrder.indexOf(module) + 1).padStart(2, "0")}-${module}.json`);
}
function checkPlan(plan: JackyunExportFirstPlan, executionId: string) {
  jackyunSalesPeriod(plan.asOfDate, plan.salesStartDate);
  if (plan.version === 2 ? plan.salesStartDate === undefined || plan.exportTransport !== jackyunApiTransport : plan.salesStartDate !== undefined) {
    throw new Error("销售范围与计划版本不一致。");
  }
  if (![1, 2].includes(plan.version) || plan.protocol !== jackyunExportFirstPolicyVersion || plan.executionId !== executionId
    || !/^[A-Za-z0-9._-]{1,96}$/.test(plan.runId)) throw new Error("运行身份或工作流协议不一致。");
  const yesterday = new Date(`${plan.runDate}T00:00:00Z`);
  yesterday.setUTCDate(yesterday.getUTCDate() - 1);
  if (plan.runDate !== jackyunCaptureDate(plan.createdAt) || plan.asOfDate !== yesterday.toISOString().slice(0, 10)
    || normalizeJackyunLocalBaseUrl(plan.baseUrl) !== "http://localhost:3000") throw new Error("计划日期或目标运营系统发生变化。");
  let incomplete = false;
  for (const moduleKey of jackyunExportOrder) {
    if (!plan.exports[moduleKey]) incomplete = true;
    else if (incomplete) throw new Error("导出清单不是规定顺序的完整前缀。");
  }
}
export function assertExportFirstAction(plan: JackyunExportFirstPlan, executionId: string, action: string, importRecovery = false) {
  checkPlan(plan, executionId);
  if (plan.exportTransport && ![jackyunWebSessionTransport, jackyunDirectTransport, jackyunApiTransport].includes(plan.exportTransport)) throw new Error("未知导出版本。");
  if (action === "plan-api" && plan.exportTransport !== jackyunApiTransport) throw new Error("纯接口模式不能接管旧计划。");
  if (action === "plan-direct-http" && plan.exportTransport !== jackyunDirectTransport) throw new Error("HTTP 模式不能接管旧计划。");
  if (action === "plan-web-session" && plan.exportTransport !== jackyunWebSessionTransport) throw new Error("网页模式不能接管 HTTP 计划。");
  if (action === "export-all" && !plan.exportTransport)
    throw new Error("网页批量模式不能接管旧单表计划。");
  if ((action === "plan" || action.startsWith("export/")) && plan.exportTransport)
    throw new Error("网页批量计划不能降级为旧单表模式。");
  if (action === "plan" || action === "plan-web-session" || action === "plan-direct-http" || action === "plan-api") return;
  if (importRecovery && plan.exportTransport === jackyunDirectTransport && ["importing", "imported", "completed"].includes(plan.phase)
    && ["export-all", "validate"].includes(action)) {
    if (jackyunExportOrder.some(module => !plan.exports[module])) throw new Error("导入续跑缺少五表");
    return;
  }
  if (action === "export-all") {
    if (!["exporting", "exported"].includes(plan.phase)) throw new Error("网页批量导出阶段不匹配。");
    return;
  }
  if (action.startsWith("export/")) {
    const moduleKey = action.slice(7) as JackyunModule;
    if (!jackyunExportOrder.includes(moduleKey)) throw new Error("未知导出模块。");
    if (plan.exports[moduleKey]) return;
    if (plan.phase !== "exporting" || jackyunExportOrder.find(item => !plan.exports[item]) !== moduleKey
      || (plan.exportIntent && plan.exportIntent !== moduleKey)) throw new Error("导出步骤乱序，已拒绝执行。");
    return;
  }
  if (jackyunExportOrder.some(module => !plan.exports[module])) throw new Error("五张表尚未全部导出，禁止导入。");
  const allowed: Record<string, Phase[]> = {
    validate: ["exported", "validating", "validated"], import: ["validated", "importing", "imported"], verify: ["imported", "completed"],
  };
  if (!allowed[action]?.includes(plan.phase)) throw new Error("工作流阶段不匹配，已拒绝跳步或不明确的恢复。");
}

async function readBoundHandoff(root: string, plan: JackyunExportFirstPlan, policy: Policy, module: JackyunModule) {
  const rawHandoff = await readFile(handoffPath(root, plan, module));
  const handoff = JSON.parse(rawHandoff.toString("utf8").replace(/^\uFEFF/, "")) as BrowserHandoff;
  if (handoff.schemaVersion !== 2 || handoff.runId !== plan.runId || handoff.module !== module
    || handoff.policyVersion !== plan.protocol || !Number.isSafeInteger(handoff.expectedSourceRows)
    || handoff.expectedSourceRows <= 0) throw new Error(`${module} 文件交接身份或行数无效。`);
  assertJackyunHandoffEvidence(handoff, module);
  if (!handoff.queryIntentAt || Date.parse(handoff.queryIntentAt) < Date.parse(handoff.navigationIntentAt)
    || Date.parse(handoff.queryIntentAt) > Date.parse(handoff.tableStableAt)
    || Date.parse(handoff.navigationIntentAt) < Date.parse(plan.createdAt)) throw new Error(`${module} 缺少本轮筛选证据。`);
  assertBoundDownloadProvenance(handoff.downloadProvenance, policy.browser.allowedDownloadHosts, {
    runId: plan.runId, module, policyVersion: plan.protocol,
  });
  const taskBinding = handoff.evidence?.exportTaskBinding as import("../lib/jackyun/export-task").JackyunExportTaskBinding | undefined;
  if (plan.exportTransport && (handoff.evidence?.exportTransport !== plan.exportTransport
    || handoff.evidence?.taskQuerySource !== ([jackyunDirectTransport, jackyunApiTransport].includes(plan.exportTransport) ? "direct_http_api" : "web_session_api") || !taskBinding)) throw new Error(`${module} 缺少任务与导出方式证据。`);
  if ([jackyunDirectTransport, jackyunApiTransport].includes(plan.exportTransport ?? "") && !/^[a-f0-9]{64}$/.test(String(handoff.evidence?.directPayloadSha256))) throw new Error(`${module} 缺少 HTTP 导出参数摘要。`);
  if (plan.exportTransport === jackyunApiTransport && (handoff.evidence?.controller !== "authenticated_http_api"
    || handoff.evidence.apiPreflightStartedAt !== handoff.navigationIntentAt || handoff.evidence.apiQueryCompletedAt !== handoff.tableStableAt
    || ["apiQuerySha256", "permissionSha256", "templateSha256"].some(key => !/^[a-f0-9]{64}$/.test(String(handoff.evidence?.[key]))))) throw new Error("纯接口查询、权限或参数模板证据不完整。");
  const taskWindowStartAt = plan.exportTransport === jackyunApiTransport ? apiTaskWindowStart(handoff.evidence?.serverClock as JackyunServerClock | undefined, handoff.exportIntentAt) : handoff.exportIntentAt;
  const taskObservedUpperAt = plan.exportTransport === jackyunApiTransport
    ? apiTaskWindowObserved(handoff.evidence?.serverClock as JackyunServerClock | undefined, handoff.downloadProvenance.completedAt)
    : handoff.downloadProvenance.completedAt;
  if (taskBinding && (taskBinding.version !== 1 || taskBinding.module !== module || taskBinding.sourceRows !== handoff.expectedSourceRows
    || taskBinding.sourceUrlHash !== handoff.downloadProvenance.sourceUrlHash || !/^sys-\d{1,20}$/.test(taskBinding.taskId)
    || !Number.isFinite(Date.parse(taskBinding.createdAt)) || !Number.isFinite(Date.parse(taskBinding.observedAt))
    || Date.parse(taskBinding.createdAt) < Math.floor(Date.parse(taskWindowStartAt) / 1000) * 1000
    || Date.parse(taskBinding.observedAt) < Date.parse(taskBinding.createdAt)
    || Date.parse(taskBinding.observedAt) > Date.parse(taskObservedUpperAt))) {
    throw new Error("导出任务记录与本轮文件交接不一致。");
  }
  if (handoff.downloadEventAt !== handoff.downloadProvenance.completedAt) throw new Error("下载时间不匹配。");
  if (module === "inventory" || module === "inventory_age") {
    assertJackyunSnapshotEvidence(handoff.snapshotEvidence, {
      module, runId: plan.runId, policyVersion: plan.protocol, snapshotDate: plan.runDate,
      navigationIntentAt: handoff.navigationIntentAt, exportIntentAt: handoff.exportIntentAt,
    });
    if (handoff.snapshotEvidence.queryIntentAt !== handoff.queryIntentAt
      || handoff.snapshotEvidence.tableStableAt !== handoff.tableStableAt) throw new Error("快照与交接时间线不一致。");
  } else if (handoff.snapshotEvidence) throw new Error("主数据或销售不接受库存快照证据。");
  if (module === "products" && !handoff.fieldChecks?.some(item => item.field === "模式" && item.value === "规格模式(SKU)")) {
    throw new Error("货品没有读回规格模式 SKU。");
  }
  if (module === "sales" && (!handoff.fieldChecks?.some(item => item.field === "统计时间类型" && item.value === "发货时间")
    || !handoff.fieldChecks.some(item => item.field === "日期区间"
      && item.value === `${jackyunSalesPeriod(plan.asOfDate, plan.salesStartDate).startDate} 00:00:00 至 ${plan.asOfDate} 23:59:59`))) {
    throw new Error("销售发货时间或计划日期范围没有精确读回。");
  }
  const directory = path.resolve(policy.browser.downloadDirectory, "jackyun", plan.runId, module);
  if (path.dirname(path.resolve(handoff.filePath)) !== directory || path.extname(handoff.filePath).toLowerCase() !== ".xlsx") {
    throw new Error("导出文件不在本轮模块专属目录中。");
  }
  const info = await stat(handoff.filePath);
  if (!info.isFile() || info.size <= 0 || info.size > 512 * 1024 * 1024
    || path.resolve(await realpath(handoff.filePath)) !== path.resolve(handoff.filePath)) throw new Error("下载文件类型、大小或实际路径无效。");
  const bytes = await readFile(handoff.filePath);
  const receipt = { handoffSha256: sha(rawHandoff), fileSha256: sha(bytes), bytes: bytes.length };
  if (receipt.fileSha256 !== handoff.downloadProvenance.sha256 || receipt.bytes !== handoff.downloadProvenance.bytes
    || (plan.exports[module] && !isDeepStrictEqual(plan.exports[module], receipt))) throw new Error("文件或交接证据已经变化。");
  if (module !== "sales") {
    const prepared = prepareJackyunWorkbook(module, bytes, { snapshotDate: plan.runDate });
    const sourceRows = module === "combos" ? prepared.validation.parentRowCount : prepared.validation.sourceRowCount;
    if (sourceRows !== handoff.expectedSourceRows) throw new Error(`${module} 页面总数与导出文件行数不一致。`);
  }
  return { handoff, receipt };
}

async function runImports(root: string, plan: JackyunExportFirstPlan, policy: Policy, dryRun: boolean, deps: ExportFirstDependencies, importRecovery?: ImportRecoveryBinding) {
  // This full revalidation happens before the first upload, including on retries.
  const bound = new Map<JackyunModule, BrowserHandoff>();
  for (const moduleKey of jackyunExportOrder) bound.set(moduleKey, (await readBoundHandoff(root, plan, policy, moduleKey)).handoff);
  const outputRoot = dryRun ? paths(root).validationRoot : paths(root).outputRoot;
  let costSourcePath: string | undefined;
  for (const moduleKey of jackyunModuleOrder) {
    const handoff = bound.get(moduleKey)!;
    const options: JackyunDownloadRunOptions = {
      module: moduleKey, filePath: handoff.filePath, runId: plan.runId, policyVersion: plan.protocol,
      snapshotDate: moduleKey === "inventory" || moduleKey === "inventory_age" ? plan.runDate : undefined,
      snapshotEvidence: handoff.snapshotEvidence, asOfDate: moduleKey === "sales" ? plan.asOfDate : undefined,
      salesStartDate: moduleKey === "sales" ? plan.salesStartDate : undefined,
      costSourcePath: moduleKey === "sales" ? costSourcePath : undefined,
      exportStart: handoff.exportIntentAt, expectedSourceRows: handoff.expectedSourceRows,
      baseUrl: plan.baseUrl, outputRoot, downloadDirectory: policy.browser.downloadDirectory,
      downloadProvenance: handoff.downloadProvenance, handoffEvidence: {
        navigationIntentAt: handoff.navigationIntentAt, queryIntentAt: handoff.queryIntentAt,
        tableStableAt: handoff.tableStableAt, exportIntentAt: handoff.exportIntentAt, downloadEventAt: handoff.downloadEventAt,
      }, allowedDownloadHosts: policy.browser.allowedDownloadHosts, dryRun,
      ...(!dryRun && moduleKey === "products" && importRecovery ? { importRecovery } : {}),
    };
    const result = await (deps.runDownload ?? runJackyunDownload)(options);
    if (!(dryRun ? ["prepared", "duplicate_ignored"] : ["completed", "duplicate_ignored"]).includes(result.status)) {
      throw new Error(`${moduleKey} 导入或校验未完成。`);
    }
    if (moduleKey === "inventory") {
      costSourcePath = "salesCostSourcePath" in result ? result.salesCostSourcePath : result.existing.salesCostSourcePath;
      if (!costSourcePath) throw new Error("没有本轮库存成本源，禁止销售导入。");
    }
  }
}

export async function verifyJackyunPreparedImports(root: string, plan: JackyunExportFirstPlan, policy: Policy) {
  const runDirectory = path.join(paths(root).validationRoot, plan.runId);
  const manifest = await readJsonFile<{ modules: Record<JackyunModule, JackyunArtifactManifestModule> }>(path.join(runDirectory, "run-manifest.json"));
  for (const moduleKey of jackyunModuleOrder) {
    await readBoundHandoff(root, plan, policy, moduleKey);
    await verifyJackyunModuleArtifact({ runDirectory, runId: plan.runId, module: moduleKey,
      snapshotDate: moduleKey === "sales" ? plan.asOfDate : plan.runDate, salesStartDate: moduleKey === "sales" ? plan.salesStartDate : undefined, policyVersion: plan.protocol,
      manifestModule: manifest.modules[moduleKey], expectedStatus: "prepared", allowedDownloadHosts: policy.browser.allowedDownloadHosts,
      handoffPath: handoffPath(root, plan, moduleKey), requireAtomicHandoff: true });
  }
}

async function verifyImports(root: string, plan: JackyunExportFirstPlan, policy: Policy, deps: ExportFirstDependencies) {
  const runDirectory = path.join(paths(root).outputRoot, plan.runId);
  const manifest = await readJsonFile<{ modules: Record<JackyunModule, JackyunArtifactManifestModule> }>(path.join(runDirectory, "run-manifest.json"));
  const modules = [];
  for (const moduleKey of jackyunModuleOrder) {
    await readBoundHandoff(root, plan, policy, moduleKey);
    const verified = await verifyJackyunModuleArtifact({
      runDirectory, runId: plan.runId, module: moduleKey, snapshotDate: moduleKey === "sales" ? plan.asOfDate : plan.runDate,
      salesStartDate: moduleKey === "sales" ? plan.salesStartDate : undefined,
      policyVersion: plan.protocol, manifestModule: manifest.modules[moduleKey],
      allowedDownloadHosts: policy.browser.allowedDownloadHosts, handoffPath: handoffPath(root, plan, moduleKey), requireAtomicHandoff: true,
    });
    if (!verified.batchId || !verified.rowCount) throw new Error("没有精确成功批次。");
    const audit = await readJsonFile<{ import: { result: { djangoReceipt?: { contentHash: string; rawFileHash: string } } } }>(verified.auditPath);
    modules.push({ module: moduleKey, status: "completed", batchId: verified.batchId, rowCount: verified.rowCount,
      warningCount: verified.warningCount ?? 0, outputSha256: manifest.modules[moduleKey].outputSha256!,
      djangoReceipt: audit.import.result.djangoReceipt });
  }
  return verifyPublishedJackyunBatches({ baseUrl: plan.baseUrl, asOfDate: plan.asOfDate,
    snapshotDate: plan.runDate, salesStartDate: plan.salesStartDate, modules, request: deps.request });
}

export async function runJackyunExportFirstAction(action: string, executionId: string, deps: ExportFirstDependencies) {
  if (!jackyunExportFirstActions.includes(action) || !/^[1-9]\d{0,19}$/.test(executionId)) throw new Error("节点或 n8n execution ID 无效。");
  if (!deps.root || !path.isAbsolute(deps.root)) throw new Error("缺少执行器提供的受保护数据目录。");
  const root = path.resolve(deps.root);
  let runId = `n8n-export-first-${executionId}`;
  return withJackyunRunLock({ runId, purpose: "n8n_export_first",
    ...(deps.lockDirectory ? { lockDirectory: deps.lockDirectory } : {}) }, async () => {
    const policy = await readJsonFile<Policy>(path.join(root, "config", "jackyun-export-first-policy.json"));
    if (policy.version !== jackyunExportFirstPolicyVersion) throw new Error("导出策略版本不一致。");
    let newPlanReady = false;
    const ensureNewPlanReady = async () => {
      if (newPlanReady) return;
      const ready = await (deps.profileReady?.() ?? getJackyunProfileStatus(policy.browser.controller.profileDirectory, root).then(value => value === "ready"));
      if (!ready) throw new Error("吉客云专用浏览器未完成首次登录配置。");
      const response = await (deps.request ?? fetch)(`${normalizeJackyunLocalBaseUrl("http://localhost:3000")}/api/sales/data-health`, { signal: AbortSignal.timeout(30000) });
      if (!response.ok) throw new Error("运营系统身份或只读数据健康检查未通过。");
      newPlanReady = true;
    };
    let resumeTaskBinding: import("../lib/jackyun/export-task").JackyunExportTaskBinding | undefined;
    let apiResumeTaskBinding: import("../lib/jackyun/export-task").JackyunExportTaskBinding | undefined;
    let webConfirmationRecovery: Awaited<ReturnType<typeof claimWebConfirmationRecovery>> | undefined;
    let httpScopeRecovery: Awaited<ReturnType<typeof claimHttpScopeRecovery>> | undefined;
    let importRecovery: ImportRecoveryBinding | undefined;
    const activePath = path.join(paths(root).pipelineRoot, "active.json");
    const active = await readJsonFileOr<{ runId: string; executionId: string } | null>(activePath, null);
    if (active && active.executionId !== executionId) {
      if (!/^n8n-export-first-[1-9]\d{0,19}$/.test(active.runId)) throw new Error("活动运行编号无效。");
      const previous = await readJsonFile<JackyunExportFirstPlan>(path.join(paths(root).pipelineRoot, `${active.runId}.json`));
      if (active.runId !== `n8n-export-first-${active.executionId}`) throw new Error("活动运行身份不一致。");
      // A completed recovery still needs to resolve its logical ID on an exact
      // execution replay. A fresh execution may start a new normal run.
      if (previous.phase === "completed" && active.executionId === "890") {
        const existing = await readJsonFileOr<ImportRecoveryBinding | null>(path.join(paths(root).pipelineRoot, "import-resumptions/891.json"), null);
        if (existing?.executionId === executionId) {
          importRecovery = await claimImportRecovery(root, active.executionId, executionId, action, nowOf(deps));
          executionId = active.executionId; runId = active.runId;
        }
      } else if (previous.phase !== "completed") {
        if (action === "plan-api" && previous.exportTransport === jackyunApiTransport && deps.recoverPreviousPreflight) {
          await ensureNewPlanReady();
          await deps.recoverPreviousPreflight(active.executionId, executionId, nowOf(deps));
        }
        const closed = await assertClosedPreflight(root, active.executionId, executionId).then(() => true, () => false);
        if (!closed) {
          try {
            if (previous.exportTransport === jackyunWebSessionTransport) {
              webConfirmationRecovery = await claimWebConfirmationRecovery(root, active.executionId, executionId, action, nowOf(deps));
            } else if (previous.exportTransport === jackyunApiTransport) {
              apiResumeTaskBinding = await claimJackyunApiResumePermit(root, active.executionId, executionId, action, nowOf(deps));
            } else if (previous.exportTransport === jackyunDirectTransport) {
              if (["importing", "imported"].includes(previous.phase)) {
                importRecovery = await claimImportRecovery(root, active.executionId, executionId, action, nowOf(deps));
              } else httpScopeRecovery = await claimHttpScopeRecovery(root, active.executionId, executionId, action, nowOf(deps));
            } else resumeTaskBinding = await claimJackyunResumePermit(root, active.executionId, executionId, action, nowOf(deps));
          }
          catch { throw new Error(`原运行 ${active.runId} 尚未闭合，且当前执行没有有效续跑许可；禁止新建重复导出。`); }
          assertExportFirstAction(previous, active.executionId, action, Boolean(importRecovery));
          executionId = active.executionId; runId = active.runId;
        }
      }
    }
    const planPath = path.join(paths(root).pipelineRoot, `${runId}.json`);
    if (await stat(preflightClosurePath(root, executionId)).then(() => true, error => {
      if (error.code === "ENOENT") return false; throw error;
    })) throw new Error("原登录失败运行已经闭合，禁止重放；只能由新的完整 n8n execution 从计划节点开始。");
    let plan = await readJsonFileOr<JackyunExportFirstPlan | null>(planPath, null);
    if (!plan) {
      if (!["plan", "plan-web-session", "plan-direct-http", "plan-api"].includes(action)) throw new Error("缺少本 execution 的计划，禁止单节点执行。");
      await ensureNewPlanReady();
      const baseUrl = normalizeJackyunLocalBaseUrl("http://localhost:3000");
      const createdAt = nowOf(deps);
      const runDate = jackyunCaptureDate(createdAt);
      const yesterday = new Date(`${runDate}T00:00:00Z`);
      yesterday.setUTCDate(yesterday.getUTCDate() - 1);
      plan = { version: action === "plan-api" ? 2 : 1, protocol: jackyunExportFirstPolicyVersion, executionId, runId, runDate,
        asOfDate: yesterday.toISOString().slice(0, 10), baseUrl, createdAt, phase: "exporting", exports: {},
        ...(action === "plan-api" ? { salesStartDate: rollingJackyunSalesStartDate(yesterday.toISOString().slice(0, 10)) } : {}),
        ...(action === "plan-web-session" ? { exportTransport: jackyunWebSessionTransport } : action === "plan-direct-http" ? { exportTransport: jackyunDirectTransport } : action === "plan-api" ? { exportTransport: jackyunApiTransport } : {}) };
      await mkdir(path.dirname(planPath), { recursive: true });
      await writeFile(planPath, `${JSON.stringify(plan, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
      await writeJsonAtomic(activePath, { runId, executionId });
    }
    assertExportFirstAction(plan, executionId, action, Boolean(importRecovery));
    if (action === "plan" || action === "plan-web-session" || action === "plan-direct-http" || action === "plan-api") return publicExportFirstPlan(plan);
    if (importRecovery && ["export-all", "validate"].includes(action)) {
      // Revalidate original dry-run evidence without replaying the browser,
      // producing replacement workbooks, or rewinding the import phase.
      await verifyJackyunPreparedImports(root, plan, policy);
      return { ...publicExportFirstPlan(plan), reusedExports: true, resumedFromExecutionId: "891" };
    }
    if (action === "export-all") {
      for (const moduleKey of jackyunExportOrder) if (plan.exports[moduleKey]) await readBoundHandoff(root, plan, policy, moduleKey);
      if (plan.phase === "exported") return publicExportFirstPlan(plan);
      if (plan.runDate !== jackyunCaptureDate(nowOf(deps))) throw new Error("采集过程已跨日，禁止启动浏览器采集旧日期。");
      const currentPlan = plan;
      const callbacks = {
        beforeModule: async (module: JackyunModule) => {
          if (currentPlan.exports[module]) { await readBoundHandoff(root, currentPlan, policy, module); return; }
          if (currentPlan.runDate !== jackyunCaptureDate(nowOf(deps))) throw new Error("采集跨日。");
          if (jackyunExportOrder.find(key => !currentPlan.exports[key]) !== module || currentPlan.exportIntent && currentPlan.exportIntent !== module) throw new Error("导出模块乱序。");
          currentPlan.exportIntent = module; await writeJsonAtomic(planPath, currentPlan);
        },
        afterModule: async (module: JackyunModule) => {
          if (!currentPlan.exports[module] && currentPlan.exportIntent !== module) throw new Error("缺少本轮导出意图。");
          currentPlan.exports[module] = (await readBoundHandoff(root, currentPlan, policy, module)).receipt;
          delete currentPlan.exportIntent; await writeJsonAtomic(planPath, currentPlan);
        },
      };
      let result: Awaited<ReturnType<typeof runApiExports>> | Awaited<ReturnType<typeof runController>>;
      try {
        result = plan.exportTransport === jackyunApiTransport ? await (deps.runApi ?? runApiExports)({
        runId, runDate: plan.runDate, asOfDate: plan.asOfDate, salesStartDate: plan.salesStartDate, eventRoot: paths(root).eventRoot, outputRoot: paths(root).outputRoot,
        downloadDirectory: policy.browser.downloadDirectory, resumeTaskBinding: apiResumeTaskBinding, ...callbacks,
      }) : await (deps.runBrowser ?? runController)({
        runId, snapshotDate: plan.runDate, asOfDate: plan.asOfDate,
        eventRoot: paths(root).eventRoot, outputRoot: paths(root).outputRoot,
        headless: true, launchOnly: false, checkLoginOnly: false, exportFirstBatch: true,
        directHttp: plan.exportTransport === jackyunDirectTransport,
        webConfirmationRecovery,
        httpScopeRecovery,
        beforeModule: async module => {
          if (currentPlan.exports[module]) { await readBoundHandoff(root, currentPlan, policy, module); return; }
          if (currentPlan.runDate !== jackyunCaptureDate(nowOf(deps))) throw new Error("采集过程已跨日，禁止把新采集结果记入旧日期。");
          if (jackyunExportOrder.find(key => !currentPlan.exports[key]) !== module
            || (currentPlan.exportIntent && currentPlan.exportIntent !== module)) throw new Error("网页五表导出乱序。");
          currentPlan.exportIntent = module;
          await writeJsonAtomic(planPath, currentPlan);
        },
        afterModule: async module => {
          if (!currentPlan.exports[module] && currentPlan.exportIntent !== module) throw new Error("网页导出没有对应的本轮 intent。");
          const { receipt } = await readBoundHandoff(root, currentPlan, policy, module);
          currentPlan.exports[module] = receipt;
          delete currentPlan.exportIntent;
          await writeJsonAtomic(planPath, currentPlan);
        },
        });
      } catch (error) {
        const pageFailure = error instanceof Error && classifyJackyunPreflightFailure(error.message) === "automatic";
        if (plan.exportTransport !== jackyunApiTransport || (!isRetryableJackyunCredentialPreparationFailure(error) && !pageFailure)) throw error;
        // The browser/session has already unwound. While still holding the
        // global run lock, prove that B created no export or import effects.
        try {
          if (pageFailure) await publishAutomatedPageClosure(root, executionId, nowOf(deps), (error as Error).message);
          else await publishAutomatedCredentialClosure(root, executionId, nowOf(deps));
        }
        catch { throw error; }
        throw new Error("JACKYUN_PREFLIGHT_RETRY_READY");
      }
      if (result.status !== "exported" || jackyunExportOrder.some(module => !plan!.exports[module])) throw new Error("网页五表导出未全部完成。");
      plan.phase = "exported";
    } else if (action.startsWith("export/")) {
      const moduleKey = action.slice(7) as JackyunModule;
      if (plan.exports[moduleKey]) {
        await readBoundHandoff(root, plan, policy, moduleKey);
        return publicExportFirstPlan(plan);
      }
      if (!plan.exports[moduleKey]) {
        if (plan.runDate !== jackyunCaptureDate(nowOf(deps))) throw new Error("采集过程已跨日，禁止把新采集结果记入旧日期。");
        plan.exportIntent = moduleKey;
        await writeJsonAtomic(planPath, plan);
        const result = await (deps.runBrowser ?? runController)({
          runId, snapshotDate: plan.runDate, asOfDate: plan.asOfDate,
          eventRoot: paths(root).eventRoot, outputRoot: paths(root).outputRoot,
          headless: true, launchOnly: false, checkLoginOnly: false, exportOnlyModule: moduleKey, resumeTaskBinding,
        });
        if (result.status !== "exported") throw new Error(`${moduleKey} 导出未完成：${result.status}`);
      }
      const { receipt } = await readBoundHandoff(root, plan, policy, moduleKey);
      plan.exports[moduleKey] = receipt;
      delete plan.exportIntent;
      if (jackyunExportOrder.every(item => plan!.exports[item])) plan.phase = "exported";
    } else if (action === "validate") {
      if (plan.phase !== "validated") {
        plan.phase = "validating";
        await writeJsonAtomic(planPath, plan);
        await runImports(root, plan, policy, true, deps);
        plan.phase = "validated";
      }
    } else if (action === "import") {
      if (plan.phase !== "imported") {
        plan.phase = "importing";
        await writeJsonAtomic(planPath, plan);
        if (importRecovery) await verifyJackyunPreparedImports(root, plan, policy);
        await runImports(root, plan, policy, false, deps, importRecovery);
        plan.phase = "imported";
      }
    } else if (action === "verify") {
      const result = await verifyImports(root, plan, policy, deps);
      plan.phase = "completed";
      plan.completedAt = nowOf(deps);
      await writeJsonAtomic(planPath, plan);
      return { ...publicExportFirstPlan(plan), results: result.modules };
    }
    await writeJsonAtomic(planPath, plan);
    return publicExportFirstPlan(plan);
  });
}

function publicExportFirstPlan(plan: JackyunExportFirstPlan) {
  return { ok: true, protocol: plan.protocol, runId: plan.runId, phase: plan.phase,
    exportTransport: plan.exportTransport ?? "browser_menu",
    snapshotDate: plan.runDate, salesStartDate: jackyunSalesPeriod(plan.asOfDate, plan.salesStartDate).startDate, salesEndDate: plan.asOfDate,
    exported: jackyunExportOrder.filter(module => plan.exports[module]), importOrder: jackyunModuleOrder };
}
