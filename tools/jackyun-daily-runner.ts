import { mkdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JackyunBrowserStateMachine, isSafePreExportBlockedResume } from "../lib/jackyun/browser-state-machine";
import {
  assertJackyunHistoricalSnapshotEvidence,
  isValidJackyunSourceRowCountCorrection,
  type JackyunSnapshotEvidence,
  type JackyunSourceRowCountCorrection,
} from "../lib/jackyun/run-contract";
import { jackyunModuleOrder, type JackyunModule } from "../lib/jackyun/post-download";
import { isExactFailedSourceRowCountRepair, runJackyunDownload } from "./jackyun-download-runner";
import { readJsonFile, readJsonFileOr, writeJsonAtomic } from "../lib/jackyun/json-file";
import { assertBoundDownloadProvenance, type JackyunDownloadProvenance } from "../lib/jackyun/download-provenance";
import { withJackyunRunLock } from "../lib/jackyun/run-lock";
import { verifyJackyunModuleArtifact } from "../lib/jackyun/run-artifact-verification";

type DailyPolicy = {
  version: string;
  moduleOrder: JackyunModule[];
  browser: { downloadDirectory: string; eventTimeoutMs: number; pollIntervalMs: number; allowedDownloadHosts?: string[] };
  modules: Record<JackyunModule, {
    requiresQuery: boolean;
    exportConfirmation?: {
      required: boolean;
      promptIncludes: string[];
      button: string;
      maxRows: number;
    };
  }>;
};

export type BrowserExportConfirmation = {
  prompt: string;
  button: string;
  confirmedAt: string;
};

export type BrowserHandoff = {
  schemaVersion: 2;
  runId: string;
  policyVersion: string;
  module: JackyunModule;
  filePath: string;
  navigationIntentAt: string;
  queryIntentAt?: string;
  tableStableAt: string;
  exportIntentAt: string;
  exportConfirmation?: BrowserExportConfirmation;
  downloadEventAt: string;
  expectedSourceRows: number;
  snapshotEvidence?: JackyunSnapshotEvidence;
  downloadProvenance?: JackyunDownloadProvenance;
  sourceRowCountCorrection?: JackyunSourceRowCountCorrection;
  fieldChecks?: Array<{ field: string; value: string; verifiedAt: string }>;
  evidence?: Record<string, unknown>;
};

export type JackyunDailyOptions = {
  runId: string;
  snapshotDate: string;
  asOfDate: string;
  eventDirectory: string;
  outputRoot: string;
  baseUrl: string;
  dryRun: boolean;
  resume: boolean;
  signal?: AbortSignal;
};

type RunManifestModule = {
  module?: JackyunModule;
  status: "prepared" | "completed" | "failed";
  sourcePath?: string;
  sourceSha256?: string;
  inputContractHash?: string;
  outputPath?: string;
  outputSha256?: string;
  salesCostSourcePath?: string;
  batchId?: string | null;
  completedAt?: string;
};

type RunManifest = {
  runId: string;
  updatedAt: string;
  strictOrder: readonly JackyunModule[];
  modules: Partial<Record<JackyunModule, RunManifestModule>>;
};

type DailyRunContract = {
  version: 1;
  runId: string;
  policyVersion: string;
  snapshotDate: string;
  asOfDate: string;
  baseUrl: string;
  mode: "formal" | "dry_run";
  createdAt: string;
};

export type JackyunDailyFailureCode =
  | "AUTH_REQUIRED"
  | "FIELD_MISMATCH"
  | "TABLE_TIMEOUT"
  | "EXPORT_AMBIGUOUS"
  | "DOWNLOAD_TIMEOUT"
  | "FILE_BINDING_FAILED"
  | "FILE_VALIDATION_FAILED"
  | "COMBO_BASELINE_FAILED"
  | "IMPORT_FAILED"
  | "BATCH_VERIFY_FAILED"
  | "PIPELINE_FAILED";

export class JackyunDailyRunError extends Error {
  readonly failureCode: JackyunDailyFailureCode;
  readonly stage: string;
  readonly cause?: unknown;

  constructor(failureCode: JackyunDailyFailureCode, stage: string, message: string, cause?: unknown) {
    super(message);
    this.name = "JackyunDailyRunError";
    this.failureCode = failureCode;
    this.stage = stage;
    this.cause = cause;
  }
}

const stableFailureCodes = new Set<JackyunDailyFailureCode>([
  "AUTH_REQUIRED",
  "FIELD_MISMATCH",
  "TABLE_TIMEOUT",
  "EXPORT_AMBIGUOUS",
  "DOWNLOAD_TIMEOUT",
  "FILE_BINDING_FAILED",
  "FILE_VALIDATION_FAILED",
  "COMBO_BASELINE_FAILED",
  "IMPORT_FAILED",
  "BATCH_VERIFY_FAILED",
  "PIPELINE_FAILED",
]);

function codedContractError(code: JackyunDailyFailureCode, message: string) {
  return Object.assign(new Error(`${code}: ${message}`), { code, failureCode: code });
}

function errorRecord(error: unknown) {
  return error && typeof error === "object" ? error as Record<string, unknown> : null;
}

export function classifyJackyunDailyFailure(input: {
  error: unknown;
  dailyStage: string;
  runnerAudit?: Record<string, unknown> | null;
}) {
  const direct = errorRecord(input.error);
  const auditError = input.runnerAudit?.error && typeof input.runnerAudit.error === "object"
    ? input.runnerAudit.error as Record<string, unknown>
    : null;
  const directCode = String(direct?.failureCode ?? direct?.code ?? "") as JackyunDailyFailureCode;
  const stage = typeof direct?.stage === "string"
    ? direct.stage
    : typeof auditError?.stage === "string"
      ? auditError.stage
      : input.dailyStage;
  if (stableFailureCodes.has(directCode)) return { failureCode: directCode, stage };

  const stageCodes: Record<string, JackyunDailyFailureCode> = {
    wait_browser_handoff: "DOWNLOAD_TIMEOUT",
    validate_browser_handoff: "FIELD_MISMATCH",
    transition_browser_state: "FIELD_MISMATCH",
    strict_sequence: "FIELD_MISMATCH",
    wait_download_file_stable: "FILE_BINDING_FAILED",
    verify_download_binding: "FILE_BINDING_FAILED",
    read_and_hash_source: "FILE_BINDING_FAILED",
    copy_raw_source: "FILE_BINDING_FAILED",
    validate_and_prepare_workbook: "FILE_VALIDATION_FAILED",
    sales_filter_cost_match_import_verify: "FILE_VALIDATION_FAILED",
    verify_combo_relation_baseline: "COMBO_BASELINE_FAILED",
    chunk_upload_and_import: "IMPORT_FAILED",
    verify_exact_import_batch: "BATCH_VERIFY_FAILED",
    classify_runner_result: "BATCH_VERIFY_FAILED",
    assemble_success_audit: "BATCH_VERIFY_FAILED",
    write_success_audit: "BATCH_VERIFY_FAILED",
    write_run_manifest: "BATCH_VERIFY_FAILED",
  };
  const message = input.error instanceof Error ? input.error.message : String(input.error);
  if (/登录|验证码|二次验证|认证|AUTH_REQUIRED/i.test(message)) return { failureCode: "AUTH_REQUIRED" as const, stage };
  if (/表格|查询.*(?:超时|0\s*行|未稳定)|zero_rows|TABLE_TIMEOUT/i.test(message)) return { failureCode: "TABLE_TIMEOUT" as const, stage };
  if (/导出.*(?:歧义|多个|不同内容|不存在|不明确)|EXPORT_AMBIGUOUS/i.test(message)) return { failureCode: "EXPORT_AMBIGUOUS" as const, stage };
  if (/下载.*超时|未捕获.*下载|DOWNLOAD_TIMEOUT/i.test(message)) return { failureCode: "DOWNLOAD_TIMEOUT" as const, stage };
  if (/组合装.*(?:基线|95%|覆盖)|COMBO_BASELINE_FAILED/i.test(message)) return { failureCode: "COMBO_BASELINE_FAILED" as const, stage };
  if (/批次|落库回查|verified|BATCH_VERIFY_FAILED/i.test(message)) return { failureCode: "BATCH_VERIFY_FAILED" as const, stage };
  if (/分片|上传|导入失败|IMPORT_FAILED/i.test(message)) return { failureCode: "IMPORT_FAILED" as const, stage };
  if (/文件|SHA|路径|绑定|crdownload|FILE_BINDING_FAILED/i.test(message)) return { failureCode: "FILE_BINDING_FAILED" as const, stage };
  if (/字段|模式|日期.*不一致|仓库.*读回|货主|FIELD_MISMATCH/i.test(message)) return { failureCode: "FIELD_MISMATCH" as const, stage };
  return { failureCode: stageCodes[stage] ?? "PIPELINE_FAILED", stage };
}

type RunnerResultLike = {
  status: string;
  existing?: { status?: string };
};

export function classifyJackyunModuleResult(result: RunnerResultLike, dryRun: boolean) {
  if (dryRun) {
    if (result.status === "prepared"
      || (result.status === "duplicate_ignored" && result.existing?.status === "prepared")) return "prepared" as const;
    throw new JackyunDailyRunError(
      "BATCH_VERIFY_FAILED",
      "classify_runner_result",
      `dry-run 只能产生 prepared，实际为 ${result.status}/${result.existing?.status ?? "none"}。`,
    );
  }
  if (result.status === "completed"
    || (result.status === "duplicate_ignored" && result.existing?.status === "completed")) return "verified_completed" as const;
  throw new JackyunDailyRunError(
    "BATCH_VERIFY_FAILED",
    "classify_runner_result",
    `正式每日任务只接受已核验 completed，实际为 ${result.status}/${result.existing?.status ?? "none"}。`,
  );
}

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const policyPath = path.join(projectRoot, "config", "jackyun-daily-policy.json");

function compactDate(value: Date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(value);
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

function shanghaiYesterday() {
  const today = new Date(`${compactDate(new Date())}T00:00:00Z`);
  today.setUTCDate(today.getUTCDate() - 1);
  return today.toISOString().slice(0, 10);
}

function defaultRunId() {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date()).replace(/[-: ]/g, "").replace(/^(\d{8})(\d{6})$/, "$1-$2");
}

function parseCli(): JackyunDailyOptions {
  const yesterday = shanghaiYesterday();
  const values = new Map<string, string>();
  let dryRun = false;
  let resume = false;
  const args = process.argv.slice(2);
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--dry-run") {
      dryRun = true;
      continue;
    }
    if (args[index] === "--resume") {
      resume = true;
      continue;
    }
    const next = args[index + 1];
    if (!next) throw new Error(`参数 ${args[index]} 缺少取值。`);
    values.set(args[index], next);
    index += 1;
  }
  return {
    runId: values.get("--run-id") ?? defaultRunId(),
    snapshotDate: values.get("--snapshot") ?? yesterday,
    asOfDate: values.get("--as-of") ?? yesterday,
    eventDirectory: path.resolve(values.get("--event-dir") ?? path.join(projectRoot, "outputs", "jackyun-browser-events")),
    outputRoot: path.resolve(values.get("--output-root") ?? path.join(projectRoot, "outputs", "jackyun-import-runs")),
    baseUrl: (values.get("--base-url") ?? "http://localhost:3000").replace(/\/$/, ""),
    dryRun,
    resume,
  };
}

function eventFileName(index: number, module: JackyunModule) {
  return `${String(index + 1).padStart(2, "0")}-${module}.json`;
}

async function waitForHandoff(filePath: string, notBeforeMs: number, timeoutMs: number, pollIntervalMs: number, signal?: AbortSignal) {
  const deadline = Date.now() + timeoutMs;
  let backoffMs = pollIntervalMs;
  while (Date.now() < deadline) {
    if (signal?.aborted) throw signal.reason instanceof Error ? signal.reason : new Error("每日 runner 已取消。");
    const info = await stat(filePath).catch(() => null);
    if (info?.isFile() && info.mtimeMs >= notBeforeMs) {
      return await readJsonFile<BrowserHandoff>(filePath);
    }
    await new Promise((resolve) => setTimeout(resolve, backoffMs));
    backoffMs = Math.min(Math.round(backoffMs * 1.5), 2_000);
  }
  throw new Error(`等待浏览器下载事件超时：${filePath}`);
}

function assertTimestamp(value: string | undefined, label: string) {
  if (!value || !Number.isFinite(Date.parse(value))) throw new Error(`${label} 缺少有效时间戳。`);
}

export function validateHandoff(
  handoff: BrowserHandoff,
  module: JackyunModule,
  policy: DailyPolicy,
  expected: { runId: string; snapshotDate?: string },
) {
  if (handoff.schemaVersion !== 2) throw new Error("浏览器事件 schemaVersion 不是当前历史快照证据版本。");
  if (handoff.runId !== expected.runId || handoff.policyVersion !== policy.version) {
    throw codedContractError("FILE_BINDING_FAILED", "浏览器事件的 runId 或 policyVersion 与本轮任务不一致。");
  }
  if (handoff.module !== module) throw new Error(`浏览器事件模块不一致：期望 ${module}，实际 ${handoff.module}。`);
  if (!Number.isSafeInteger(handoff.expectedSourceRows) || handoff.expectedSourceRows <= 0) throw new Error("expectedSourceRows 必须是正整数。");
  assertTimestamp(handoff.navigationIntentAt, "navigationIntentAt");
  if (policy.modules[module].requiresQuery) assertTimestamp(handoff.queryIntentAt, "queryIntentAt");
  assertTimestamp(handoff.tableStableAt, "tableStableAt");
  assertTimestamp(handoff.exportIntentAt, "exportIntentAt");
  const confirmationPolicy = policy.modules[module].exportConfirmation;
  const confirmation = handoff.exportConfirmation;
  if (confirmationPolicy?.required && !confirmation) {
    throw new Error(`${module} 必须记录导出确认框及“${confirmationPolicy.button}”按钮的单次确认。`);
  }
  if (confirmation) {
    assertTimestamp(confirmation.confirmedAt, "exportConfirmation.confirmedAt");
    if (Date.parse(confirmation.confirmedAt) < Date.parse(handoff.exportIntentAt)) {
      throw new Error("导出确认时间早于导出 intent。");
    }
    if (confirmationPolicy) {
      if (confirmation.button.trim() !== confirmationPolicy.button) {
        throw new Error(`导出确认按钮不一致：期望 ${confirmationPolicy.button}，实际 ${confirmation.button}。`);
      }
      for (const fragment of confirmationPolicy.promptIncludes) {
        if (!confirmation.prompt.includes(fragment)) {
          throw new Error(`导出确认提示缺少固定文本：${fragment}`);
        }
      }
    }
  }
  assertTimestamp(handoff.downloadEventAt, "downloadEventAt");
  assertBoundDownloadProvenance(handoff.downloadProvenance, policy.browser.allowedDownloadHosts, {
    runId: expected.runId,
    module,
    policyVersion: policy.version,
  });
  if (handoff.downloadEventAt !== handoff.downloadProvenance.completedAt) {
    throw codedContractError(
      "FILE_BINDING_FAILED",
      "downloadEventAt 必须与本轮下载 provenance.completedAt 完全一致。",
    );
  }
  if (Date.parse(handoff.downloadProvenance.completedAt) < Date.parse(handoff.exportIntentAt)) {
    throw codedContractError("FILE_BINDING_FAILED", "下载来源证据的完成时间早于导出 intent。");
  }
  if (Date.parse(handoff.tableStableAt) < Date.parse(handoff.queryIntentAt ?? handoff.navigationIntentAt)) {
    throw new Error("表格稳定时间早于本轮查询或导航 intent。" );
  }
  if (Date.parse(handoff.downloadEventAt) < Date.parse(handoff.exportIntentAt)) {
    throw new Error("下载事件早于导出 intent。" );
  }
  if (confirmation && Date.parse(handoff.downloadEventAt) < Date.parse(confirmation.confirmedAt)) {
    throw new Error("下载事件早于导出确认。" );
  }
  if (handoff.sourceRowCountCorrection
    && !isValidJackyunSourceRowCountCorrection(handoff.sourceRowCountCorrection, handoff.expectedSourceRows)) {
    throw new Error("页面总行数修正证据无效。");
  }
  if (module === "inventory" || module === "inventory_age") {
    if (!expected.snapshotDate) throw codedContractError("FIELD_MISMATCH", `${module} 缺少预期快照日期。`);
    assertJackyunHistoricalSnapshotEvidence(handoff.snapshotEvidence, {
      module,
      runId: expected.runId,
      snapshotDate: expected.snapshotDate,
      navigationIntentAt: handoff.navigationIntentAt,
      exportIntentAt: handoff.exportIntentAt,
    });
    if (handoff.snapshotEvidence.queryIntentAt !== handoff.queryIntentAt) {
      throw codedContractError("FIELD_MISMATCH", `${module} 快照证据的查询 intent 与浏览器交接不一致。`);
    }
    if (handoff.snapshotEvidence.tableStableAt !== handoff.tableStableAt) {
      throw codedContractError("TABLE_TIMEOUT", `${module} 快照证据的表格稳定时间与浏览器交接不一致。`);
    }
  } else if (handoff.snapshotEvidence) {
    throw codedContractError("FIELD_MISMATCH", `${module} 不允许携带历史快照日期证据。`);
  }
}

export function assertPolicyModuleOrder(policy: DailyPolicy) {
  if (policy.moduleOrder.length !== jackyunModuleOrder.length
    || policy.moduleOrder.some((module, index) => module !== jackyunModuleOrder[index])) {
    throw new Error(`策略模块顺序与代码不一致：期望 ${jackyunModuleOrder.join(" -> ")}。`);
  }
}

async function writeCompactResult(filePath: string, value: unknown) {
  await writeJsonAtomic(filePath, value, false);
}

function assertManifestOrder(manifest: RunManifest) {
  if (manifest.strictOrder.length !== jackyunModuleOrder.length
    || manifest.strictOrder.some((module, index) => module !== jackyunModuleOrder[index])) {
    throw new Error(`运行清单模块顺序与当前代码不一致：${manifest.strictOrder.join(" -> ")}。`);
  }
}

export function firstIncompleteModuleIndex(manifest: RunManifest | null) {
  if (!manifest) return 0;
  const firstIncomplete = jackyunModuleOrder.findIndex((module) => manifest.modules[module]?.status !== "completed");
  if (firstIncomplete < 0) return jackyunModuleOrder.length;
  for (const later of jackyunModuleOrder.slice(firstIncomplete + 1)) {
    if (manifest.modules[later]?.status === "completed") {
      throw new Error("运行清单存在非连续的已完成模块，不能自动续跑。");
    }
  }
  return firstIncomplete;
}

export async function preflightJackyunResume(options: JackyunDailyOptions) {
  const policy = JSON.parse(await readFile(policyPath, "utf8")) as DailyPolicy;
  assertPolicyModuleOrder(policy);
  const runDirectory = path.join(options.outputRoot, options.runId);
  const eventRunDirectory = path.join(options.eventDirectory, options.runId);
  const [manifest, existingRunContract] = await Promise.all([
    readJsonFileOr<RunManifest | null>(path.join(runDirectory, "run-manifest.json"), null),
    readJsonFileOr<DailyRunContract | null>(path.join(runDirectory, "daily-run-contract.json"), null),
  ]);
  const expectedRunContract = {
    version: 1 as const,
    runId: options.runId,
    policyVersion: policy.version,
    snapshotDate: options.snapshotDate,
    asOfDate: options.asOfDate,
    baseUrl: options.baseUrl,
    mode: options.dryRun ? "dry_run" as const : "formal" as const,
  };
  if (existingRunContract) {
    const mismatch = Object.entries(expectedRunContract)
      .find(([key, value]) => existingRunContract[key as keyof typeof expectedRunContract] !== value);
    if (mismatch) {
      throw new JackyunDailyRunError(
        "BATCH_VERIFY_FAILED",
        "preflight_resume_contract",
        `运行契约 ${mismatch[0]} 与本次参数或策略版本不一致；浏览器启动前拒绝跨口径续跑。`,
      );
    }
  } else if (manifest) {
    throw new JackyunDailyRunError(
      "BATCH_VERIFY_FAILED",
      "preflight_resume_contract",
      "既有运行缺少当前版本的日期与策略证据契约；浏览器启动前拒绝续跑。",
    );
  }
  if (!options.resume && (manifest || existingRunContract)) {
    throw new JackyunDailyRunError(
      "BATCH_VERIFY_FAILED",
      "preflight_existing_run",
      `运行编号 ${options.runId} 已经存在；启动浏览器前要求显式使用 --resume。`,
    );
  }
  if (!manifest) return { startIndex: 0 };
  if (manifest.runId !== options.runId) throw new Error("运行清单中的 run id 与续跑参数不一致。");
  assertManifestOrder(manifest);
  const preparedModules = jackyunModuleOrder.filter((module) => manifest.modules[module]?.status === "prepared");
  if (preparedModules.length > 0) {
    throw new JackyunDailyRunError(
      "BATCH_VERIFY_FAILED",
      "preflight_resume_manifest",
      `运行清单包含 dry-run prepared 模块（${preparedModules.join(", ")}），浏览器启动前拒绝续跑。`,
    );
  }
  const startIndex = firstIncompleteModuleIndex(manifest);
  for (let index = 0; index < startIndex; index += 1) {
    const moduleKey = jackyunModuleOrder[index];
    const manifestModule = manifest.modules[moduleKey];
    if (!manifestModule) throw new Error(`续跑缺少 ${moduleKey} 清单记录。`);
    await verifyJackyunModuleArtifact({
      runDirectory,
      runId: options.runId,
      module: moduleKey,
      snapshotDate: options.asOfDate,
      policyVersion: policy.version,
      allowedDownloadHosts: policy.browser.allowedDownloadHosts,
      manifestModule,
      handoffPath: path.join(eventRunDirectory, eventFileName(index, moduleKey)),
      requireAtomicHandoff: true,
    });
  }
  return { startIndex };
}

async function compactCompletedResult(
  eventPath: string,
  module: JackyunModule,
  runDirectory: string,
  manifestModule: RunManifestModule,
) {
  const saved = await readJsonFileOr<Record<string, unknown> | null>(`${eventPath}.result.json`, null);
  if (saved) return saved;
  const auditPath = path.join(runDirectory, "audit", `${module}.json`);
  const audit = await readJsonFileOr<Record<string, unknown> | null>(auditPath, null);
  const imported = audit?.import as Record<string, unknown> | undefined;
  const batch = imported?.batch as Record<string, unknown> | undefined;
  const timings = audit?.timings as Record<string, unknown> | undefined;
  return {
    module,
    status: "completed",
    outputPath: manifestModule.outputPath ?? null,
    batchId: manifestModule.batchId ?? null,
    rowCount: typeof batch?.rowCount === "number" ? batch.rowCount : null,
    warningCount: typeof batch?.warningCount === "number" ? batch.warningCount : null,
    auditPath,
    runnerElapsedMs: typeof timings?.elapsedMs === "number" ? timings.elapsedMs : null,
  };
}

async function readRunnerTiming(auditPath: string) {
  const audit = await readJsonFileOr<Record<string, unknown> | null>(auditPath, null);
  const timings = audit?.timings as Record<string, unknown> | undefined;
  return {
    runnerElapsedMs: typeof timings?.elapsedMs === "number" ? timings.elapsedMs : null,
    stageElapsedMs: timings?.stageElapsedMs && typeof timings.stageElapsedMs === "object" ? timings.stageElapsedMs : null,
  };
}

export async function runJackyunDaily(options: JackyunDailyOptions) {
  const policy = JSON.parse(await readFile(policyPath, "utf8")) as DailyPolicy;
  assertPolicyModuleOrder(policy);
  const startedAtMs = Date.now();
  const runDirectory = path.join(options.outputRoot, options.runId);
  const eventRunDirectory = path.join(options.eventDirectory, options.runId);
  await Promise.all([mkdir(runDirectory, { recursive: true }), mkdir(eventRunDirectory, { recursive: true })]);
  const manifestPath = path.join(runDirectory, "run-manifest.json");
  const runContractPath = path.join(runDirectory, "daily-run-contract.json");
  const [manifest, existingRunContract] = await Promise.all([
    readJsonFileOr<RunManifest | null>(manifestPath, null),
    readJsonFileOr<DailyRunContract | null>(runContractPath, null),
  ]);
  const expectedRunContract = {
    version: 1 as const,
    runId: options.runId,
    policyVersion: policy.version,
    snapshotDate: options.snapshotDate,
    asOfDate: options.asOfDate,
    baseUrl: options.baseUrl,
    mode: options.dryRun ? "dry_run" as const : "formal" as const,
  };
  if (existingRunContract) {
    const mismatch = Object.entries(expectedRunContract)
      .find(([key, value]) => existingRunContract[key as keyof typeof expectedRunContract] !== value);
    if (mismatch) {
      throw new JackyunDailyRunError(
        "BATCH_VERIFY_FAILED",
        "validate_run_contract",
        `运行契约 ${mismatch[0]} 与本次参数或策略版本不一致；不得跨口径续跑，请使用新的 run id。`,
      );
    }
  } else if (manifest) {
    throw new JackyunDailyRunError(
      "BATCH_VERIFY_FAILED",
      "validate_run_contract",
      "既有运行缺少当前版本的日期与策略证据契约，不得续跑或升级为新口径完成；请使用新的 run id。",
    );
  } else {
    await writeJsonAtomic(runContractPath, { ...expectedRunContract, createdAt: new Date().toISOString() });
  }
  if (manifest) {
    if (manifest.runId !== options.runId) throw new Error("运行清单中的 run id 与续跑参数不一致。");
    assertManifestOrder(manifest);
    const preparedModules = jackyunModuleOrder.filter((module) => manifest.modules[module]?.status === "prepared");
    if (preparedModules.length > 0) {
      throw new JackyunDailyRunError(
        "BATCH_VERIFY_FAILED",
        "validate_manifest_status",
        `运行清单包含 dry-run prepared 模块（${preparedModules.join(", ")}），不得续跑或提升为正式完成；请使用新的 run id。`,
      );
    }
    if (!options.resume) {
      throw new Error(`运行编号 ${options.runId} 已经存在；继续该批次必须显式使用 --resume。`);
    }
  }
  const startIndex = firstIncompleteModuleIndex(manifest);
  let rowCountRepair: JackyunSourceRowCountCorrection | undefined;
  if (options.resume && startIndex < jackyunModuleOrder.length) {
    const incompleteModule = jackyunModuleOrder[startIndex];
    const failedModule = manifest?.modules[incompleteModule];
    if (failedModule?.status === "failed") {
      const handoff = await readJsonFileOr<BrowserHandoff | null>(
        path.join(options.eventDirectory, options.runId, eventFileName(startIndex, incompleteModule)),
        null,
      );
      const failedAudit = await readJsonFileOr<Record<string, unknown> | null>(
        path.join(runDirectory, "audit", `${incompleteModule}.json`),
        null,
      );
      const correction = handoff?.sourceRowCountCorrection;
      const repairable = handoff
        && isValidJackyunSourceRowCountCorrection(correction, handoff.expectedSourceRows)
        && isExactFailedSourceRowCountRepair({
          runId: options.runId,
          module: incompleteModule,
          filePath: handoff.filePath,
          rawSha256: failedModule.sourceSha256 ?? "",
          expectedSourceRows: handoff.expectedSourceRows,
          correction,
          priorModule: failedModule as unknown as Record<string, unknown>,
          failedAudit,
        });
      if (!repairable) {
        throw new Error(`${incompleteModule} 已产生下载后处理失败清单；该失败没有精确的导入前总行数修正证据，不能自动重导或覆盖。`);
      }
      rowCountRepair = correction;
    }
  }
  const statePath = path.join(runDirectory, "browser-state.json");
  const stateExists = Boolean(await stat(statePath).catch(() => null));
  const stateMachine = options.resume && stateExists
    ? await JackyunBrowserStateMachine.load(statePath)
    : await JackyunBrowserStateMachine.create({ statePath, runId: options.runId, policyVersion: policy.version });
  if (options.resume && stateExists && stateMachine.snapshot().policyVersion !== policy.version) {
    throw new JackyunDailyRunError(
      "BATCH_VERIFY_FAILED",
      "validate_browser_state_policy",
      "浏览器状态属于旧策略版本，不得在新历史快照口径下续跑；请使用新的 run id。",
    );
  }
  const resumedFromState = options.resume && stateExists ? stateMachine.snapshot().currentState : null;
  if (startIndex < jackyunModuleOrder.length) {
    const startModule = jackyunModuleOrder[startIndex];
    if (options.resume && stateExists) {
      if (resumedFromState === "BLOCKED") {
        const controller = await readJsonFileOr<{
          runId?: string;
          policyVersion?: string;
          modules?: Partial<Record<JackyunModule, Record<string, unknown>>>;
        } | null>(path.join(runDirectory, "browser-controller-state.json"), null);
        const safe = controller?.runId === options.runId
          && controller.policyVersion === policy.version
          && isSafePreExportBlockedResume(stateMachine.snapshot(), startModule, controller.modules?.[startModule]);
        if (!safe && !rowCountRepair) {
          throw new Error(`当前浏览器状态 ${resumedFromState} 缺少“未查询、未导出、未下载”的完整证据，不能自动续跑。`);
        }
      }
      await stateMachine.reconcileForResume(startModule, { resumedAt: new Date(startedAtMs).toISOString(), manifestPath, policyVersion: policy.version });
    } else if (startModule === "products") {
      await stateMachine.transition("products", "ENTER_MODULE", { baseUrl: options.baseUrl, startedAt: new Date(startedAtMs).toISOString() });
    } else {
      await stateMachine.reconcileForResume(startModule, { reconstructedFromManifest: true, manifestPath, policyVersion: policy.version });
    }
  }

  const results: Array<Record<string, unknown>> = [];
  for (let index = 0; index < startIndex; index += 1) {
    const completedModule = jackyunModuleOrder[index];
    const completedManifest = manifest?.modules[completedModule];
    if (!completedManifest) throw new Error(`续跑缺少 ${completedModule} 清单记录。`);
    await verifyJackyunModuleArtifact({
      runDirectory,
      runId: options.runId,
      module: completedModule,
      snapshotDate: options.asOfDate,
      policyVersion: policy.version,
      allowedDownloadHosts: policy.browser.allowedDownloadHosts,
      manifestModule: completedManifest,
      handoffPath: path.join(eventRunDirectory, eventFileName(index, completedModule)),
      requireAtomicHandoff: true,
    });
    results.push(await compactCompletedResult(
      path.join(eventRunDirectory, eventFileName(index, completedModule)),
      completedModule,
      runDirectory,
      completedManifest,
    ));
  }
  let inventoryCostSource = manifest?.modules.inventory?.salesCostSourcePath;
  const resumeNotBeforeMs = manifest?.updatedAt ? Date.parse(manifest.updatedAt) - 5_000 : startedAtMs;
  for (let index = startIndex; index < jackyunModuleOrder.length; index += 1) {
    const moduleKey = jackyunModuleOrder[index];
    let dailyStage = "wait_browser_handoff";
    try {
      const eventPath = path.join(eventRunDirectory, eventFileName(index, moduleKey));
      const handoff = await waitForHandoff(eventPath, index === startIndex ? resumeNotBeforeMs : startedAtMs, policy.browser.eventTimeoutMs, policy.browser.pollIntervalMs, options.signal);
      dailyStage = "validate_browser_handoff";
      validateHandoff(handoff, moduleKey, policy, { runId: options.runId, snapshotDate: options.snapshotDate });
      dailyStage = "transition_browser_state";
      if (handoff.fieldChecks?.length) await stateMachine.transition(moduleKey, "VERIFY_FIELD", { checks: handoff.fieldChecks });
      if (policy.modules[moduleKey].requiresQuery) await stateMachine.transition(moduleKey, "QUERY_ONCE", { queryIntentAt: handoff.queryIntentAt });
      await stateMachine.transition(moduleKey, "WAIT_TABLE_STABLE", { tableStableAt: handoff.tableStableAt, expectedSourceRows: handoff.expectedSourceRows });
      await stateMachine.transition(moduleKey, "ARM_DOWNLOAD", { exportIntentAt: handoff.exportIntentAt });
      await stateMachine.transition(moduleKey, "EXPORT_ONCE", { exportIntentAt: handoff.exportIntentAt });
      if (handoff.exportConfirmation) {
        await stateMachine.transition(moduleKey, "CONFIRM_EXPORT_DIALOG", handoff.exportConfirmation);
      }
      await stateMachine.transition(moduleKey, "WAIT_EVENT_AND_FILE", { downloadEventAt: handoff.downloadEventAt, filePath: handoff.filePath });
      await stateMachine.transition(moduleKey, "HANDOFF_EXACT_PATH", { filePath: handoff.filePath, ...handoff.evidence });

      dailyStage = "run_download_runner";
      const result = await runJackyunDownload({
        module: moduleKey,
        filePath: path.resolve(handoff.filePath),
        runId: options.runId,
        policyVersion: policy.version,
        snapshotDate: moduleKey === "inventory" || moduleKey === "inventory_age" ? options.snapshotDate : undefined,
        snapshotEvidence: moduleKey === "inventory" || moduleKey === "inventory_age" ? handoff.snapshotEvidence : undefined,
        asOfDate: moduleKey === "sales" ? options.asOfDate : undefined,
        costSourcePath: moduleKey === "sales" ? inventoryCostSource : undefined,
        exportStart: handoff.exportIntentAt,
        expectedSourceRows: handoff.expectedSourceRows,
        baseUrl: options.baseUrl,
        outputRoot: options.outputRoot,
        downloadDirectory: policy.browser.downloadDirectory,
        downloadProvenance: handoff.downloadProvenance,
        handoffEvidence: {
          navigationIntentAt: handoff.navigationIntentAt,
          queryIntentAt: handoff.queryIntentAt,
          tableStableAt: handoff.tableStableAt,
          exportIntentAt: handoff.exportIntentAt,
          downloadEventAt: handoff.downloadEventAt,
        },
        sourceRowCountCorrection: handoff.sourceRowCountCorrection,
        allowedDownloadHosts: policy.browser.allowedDownloadHosts,
        dryRun: options.dryRun,
      });
      if (moduleKey === "inventory") {
        inventoryCostSource = "salesCostSourcePath" in result
          ? result.salesCostSourcePath
          : result.existing.salesCostSourcePath;
        if (!inventoryCostSource) throw new Error("库存模块未返回本轮销售成本源副本。");
      }
      dailyStage = "classify_runner_result";
      const disposition = classifyJackyunModuleResult(result, options.dryRun);
      const compact = {
        module: moduleKey,
        status: disposition === "prepared" ? "prepared" : result.status,
        outputPath: "outputPath" in result ? result.outputPath : result.existing.outputPath ?? null,
        batchId: "batch" in result ? result.batch?.id ?? null : result.existing.batchId ?? null,
        rowCount: "batch" in result ? result.batch?.rowCount ?? null : null,
        warningCount: "batch" in result ? result.batch?.warningCount ?? 0 : null,
        auditPath: result.auditPath,
        resumedFrom: index === startIndex ? resumedFromState : null,
        ...await readRunnerTiming(result.auditPath),
      };
      results.push(compact);
      await writeCompactResult(`${eventPath}.result.json`, compact);
      if (disposition === "prepared") {
        const summary = {
          status: "prepared" as const,
          dryRun: true,
          runId: options.runId,
          policyVersion: policy.version,
          elapsedMs: Date.now() - startedAtMs,
          resumed: options.resume,
          salesAsOfDate: options.asOfDate,
          preparedModule: moduleKey,
          results,
        };
        await writeCompactResult(path.join(runDirectory, "daily-summary.json"), summary);
        console.log(JSON.stringify({ type: "module_prepared", runId: options.runId, ...compact }));
        return summary;
      }
      await stateMachine.transition(moduleKey, "RUNNER_VERIFIED", { status: result.status, auditPath: result.auditPath });
      await stateMachine.transition(moduleKey, "MODULE_DONE", { batchId: "batch" in result ? result.batch?.id ?? null : result.existing.batchId ?? null });
      console.log(JSON.stringify({ type: "module_completed", runId: options.runId, ...compact }));
      if (index < jackyunModuleOrder.length - 1) await stateMachine.startNextModule();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const runnerAuditPath = path.join(runDirectory, "audit", `${moduleKey}.json`);
      const runnerAudit = await readJsonFileOr<Record<string, unknown> | null>(runnerAuditPath, null);
      const failure = classifyJackyunDailyFailure({ error, dailyStage, runnerAudit });
      await stateMachine.block(failure.failureCode, message, {
        module: moduleKey,
        stage: failure.stage,
        runnerAuditPath: runnerAudit ? runnerAuditPath : null,
      });
      throw error instanceof JackyunDailyRunError
        ? error
        : new JackyunDailyRunError(failure.failureCode, failure.stage, message, error);
    }
  }
  const stateSnapshot = stateMachine.snapshot();
  const browserElapsedByModule = Object.fromEntries(jackyunModuleOrder.map((moduleKey) => [
    moduleKey,
    stateSnapshot.events
      .filter((event) => event.module === moduleKey && !["HANDOFF_EXACT_PATH", "RUNNER_VERIFIED"].includes(event.state))
      .reduce((total, event) => total + event.elapsedMs, 0),
  ]));
  const moduleTimings = Object.fromEntries(results.map((result) => {
    const moduleKey = result.module as JackyunModule;
    const runnerMs = typeof result.runnerElapsedMs === "number" ? result.runnerElapsedMs : null;
    const browserMs = browserElapsedByModule[moduleKey] ?? 0;
    return [moduleKey, {
      browserMs,
      runnerMs,
      totalMs: runnerMs === null ? browserMs : browserMs + runnerMs,
      stageElapsedMs: result.stageElapsedMs ?? null,
    }];
  }));
  if (options.dryRun || results.some((result) => result.status === "prepared")) {
    throw new JackyunDailyRunError(
      "BATCH_VERIFY_FAILED",
      "assemble_daily_summary",
      "dry-run/prepared 结果不得汇总为 completed。",
    );
  }
  const summary = {
    status: "completed",
    runId: options.runId,
    policyVersion: policy.version,
    elapsedMs: Date.now() - startedAtMs,
    resumed: options.resume,
    salesAsOfDate: options.asOfDate,
    moduleTimings,
    results,
  };
  await writeCompactResult(path.join(runDirectory, "daily-summary.json"), summary);
  return summary;
}

if (path.resolve(process.argv[1] ?? "") === path.resolve(fileURLToPath(import.meta.url))) {
  const cliOptions = parseCli();
  withJackyunRunLock(
    { runId: cliOptions.runId, purpose: "daily_import_runner" },
    () => runJackyunDaily(cliOptions),
  )
    .then((result) => console.log(JSON.stringify({
      type: result.status === "completed" ? "daily_completed" : "daily_prepared",
      ...result,
    })))
    .catch((error: unknown) => {
      console.error(error instanceof Error ? error.message : String(error));
      process.exitCode = 1;
    });
}
