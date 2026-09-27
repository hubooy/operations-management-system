import { createHash } from "node:crypto";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  assertJackyunHistoricalSnapshotEvidence,
  assertJackyunSnapshotEvidence,
  assertJackyunHandoffEvidence,
  createJackyunInputContractHash,
  isValidJackyunSourceRowCountCorrection,
  type JackyunInputContract,
  type JackyunHandoffEvidence,
  type JackyunHistoricalSnapshotEvidence,
  type JackyunSnapshotEvidence,
  type JackyunSourceRowCountCorrection,
} from "../lib/jackyun/run-contract";
import {
  assertComboRelationBaseline,
  JackyunValidationError,
  jackyunModuleOrder,
  prepareJackyunWorkbook,
  type JackyunModule,
  type JackyunWorkbookModule,
} from "../lib/jackyun/post-download";
import { runSalesImport, salesSourceRowCountSemantic } from "./sales-import-runner";
import { readJsonFile, readJsonFileOr, writeJsonAtomic } from "../lib/jackyun/json-file";
import { jackyunSalesPeriod } from "../lib/jackyun/sales-period";
import {
  assertBoundDownloadProvenance,
  defaultJackyunDownloadHosts,
  type JackyunDownloadProvenance,
} from "../lib/jackyun/download-provenance";
import { withJackyunRunLock } from "../lib/jackyun/run-lock";
import { verifyJackyunModuleArtifact } from "../lib/jackyun/run-artifact-verification";
import { jackyunDjangoImportReceipt } from "../lib/jackyun/django-import-receipt";
import { jackyunExportFirstPolicyVersion } from "../lib/jackyun/run-contract";
import { auditedComboNameRejection, isAuditedComboNameRepair } from "../lib/jackyun/combo-name-recovery";
import { assertExactFailedImportRetry, type ImportRecoveryBinding } from "../lib/jackyun/import-recovery";

type CliOptions = {
  module: JackyunModule;
  filePath: string;
  runId: string;
  policyVersion: string;
  snapshotDate?: string;
  snapshotEvidence?: JackyunSnapshotEvidence;
  asOfDate?: string;
  salesStartDate?: string;
  costSourcePath?: string;
  exportStart: string;
  expectedSourceRows: number;
  previousComboRows?: number;
  baseUrl: string;
  outputRoot: string;
  downloadDirectory: string;
  downloadProvenance?: JackyunDownloadProvenance;
  handoffEvidence?: JackyunHandoffEvidence;
  allowedDownloadHosts?: readonly string[];
  sourceRowCountCorrection?: JackyunSourceRowCountCorrection;
  importRecovery?: ImportRecoveryBinding;
  dryRun: boolean;
};

export type JackyunDownloadRunOptions = CliOptions;

export function assertNoJackyunPreparedPromotion(status: string, dryRun: boolean) {
  if (status === "prepared" && !dryRun) {
    throw new Error("dry-run prepared 模块不得在同一 run id 下升级为正式完成；请使用新的 run id。");
  }
}

type ModuleStatus = "prepared" | "completed" | "failed";

type ModuleManifest = {
  module: JackyunModule;
  status: ModuleStatus;
  sourcePath: string;
  sourceSha256?: string;
  outputPath?: string;
  outputSha256?: string;
  salesCostSourcePath?: string;
  salesCostSourceSha256?: string;
  inputContractHash?: string;
  batchId?: string | null;
  startedAt: string;
  completedAt?: string;
  error?: string;
};

type RunManifest = {
  version: 1;
  runId: string;
  startedAt: string;
  updatedAt: string;
  strictOrder: readonly JackyunModule[];
  modules: Partial<Record<JackyunModule, ModuleManifest>>;
};

type BatchSummary = {
  id: string;
  status: string;
  rowCount: number;
  insertedCount?: number;
  updatedCount?: number;
  excludedCount?: number;
  warningCount?: number;
  snapshotDate?: string | null;
  warnings?: unknown[];
};

type StableFileEvidence = {
  size: number;
  mtimeMs: number;
  mtime: Date;
};

type BrowserHandoffFile = {
  schemaVersion?: number;
  runId?: string;
  policyVersion?: string;
  module?: string;
  filePath?: string;
  navigationIntentAt?: string;
  queryIntentAt?: string;
  tableStableAt?: string;
  exportIntentAt?: string;
  downloadEventAt?: string;
  expectedSourceRows?: number;
  downloadProvenance?: JackyunDownloadProvenance;
  snapshotEvidence?: JackyunHistoricalSnapshotEvidence;
};

export function isExactFailedSourceRowCountRepair(input: {
  runId: string;
  module: JackyunModule;
  filePath: string;
  rawSha256: string;
  expectedSourceRows: number;
  correction?: JackyunSourceRowCountCorrection;
  priorModule?: Record<string, unknown> | null;
  failedAudit?: Record<string, unknown> | null;
}) {
  if (input.module === "sales"
    || !isValidJackyunSourceRowCountCorrection(input.correction, input.expectedSourceRows)
    || !/^[a-f0-9]{64}$/i.test(input.rawSha256)) return false;
  const prior = input.priorModule;
  const audit = input.failedAudit;
  if (!prior || !audit || prior.status !== "failed" || audit.status !== "failed"
    || prior.module !== input.module || audit.module !== input.module || audit.runId !== input.runId
    || typeof prior.inputContractHash !== "string" || prior.batchId || prior.outputPath || audit.import) return false;
  const auditSource = audit.source as Record<string, unknown> | null | undefined;
  const auditError = audit.error as Record<string, unknown> | null | undefined;
  const details = auditError?.details as Record<string, unknown> | null | undefined;
  const timings = audit.timings as Record<string, unknown> | null | undefined;
  const failedAt = typeof timings?.failedAt === "string" ? Date.parse(timings.failedAt) : Number.NaN;
  try {
    return path.resolve(String(prior.sourcePath ?? "")) === path.resolve(input.filePath)
      && path.resolve(String(auditSource?.path ?? "")) === path.resolve(input.filePath)
      && prior.sourceSha256 === input.rawSha256
      && auditSource?.sha256 === input.rawSha256
      && auditError?.stage === "validate_and_prepare_workbook"
      && Number(details?.expectedSourceRows) === input.correction.previousExpectedSourceRows
      && Number(details?.actualSourceRows) === input.correction.exactExpectedSourceRows
      && Number.isFinite(failedAt)
      && Date.parse(input.correction.observedAt) >= failedAt;
  } catch {
    return false;
  }
}

class RunnerHttpError extends Error {
  readonly details: Record<string, unknown>;

  constructor(message: string, details: Record<string, unknown>) {
    super(message);
    this.name = "RunnerHttpError";
    this.details = details;
  }
}

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const defaultDownloadDirectory = "D:\\谷歌浏览器";
const dailyPolicyPath = path.join(projectRoot, "config", "jackyun-daily-policy.json");

function shanghaiIsoToday() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

function shanghaiIsoYesterday() {
  const today = new Date(`${shanghaiIsoToday()}T00:00:00Z`);
  today.setUTCDate(today.getUTCDate() - 1);
  return today.toISOString().slice(0, 10);
}

function isIsoDate(value: string | undefined): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

function isJackyunModule(value: string): value is JackyunModule {
  return (jackyunModuleOrder as readonly string[]).includes(value);
}

async function parseCli(): Promise<CliOptions> {
  const args = process.argv.slice(2);
  const values = new Map<string, string>();
  let dryRun = false;
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (argument === "--dry-run") {
      dryRun = true;
      continue;
    }
    if (!argument.startsWith("--")) throw new Error(`不支持的参数：${argument}`);
    const next = args[index + 1];
    if (!next || next.startsWith("--")) throw new Error(`参数 ${argument} 缺少取值。`);
    values.set(argument, next);
    index += 1;
  }
  const moduleValue = values.get("--module") ?? "";
  if (!isJackyunModule(moduleValue)) throw new Error("--module 必须是 products、inventory、inventory_age、combos 或 sales。");
  const filePath = values.get("--file");
  if (!filePath) throw new Error("缺少 --file，本任务不允许自动猜测最新下载文件。");
  const runId = values.get("--run-id");
  if (!runId) throw new Error("缺少 --run-id；五个模块必须显式共用同一个运行编号。");
  if (!/^[A-Za-z0-9._-]+$/.test(runId)) throw new Error("--run-id 只能包含字母、数字、点、下划线和连字符。");
  const currentPolicy = await readJsonFile<{ version?: string }>(dailyPolicyPath);
  const policyVersion = currentPolicy.version?.trim();
  if (!policyVersion) throw new Error("吉客云每日策略缺少有效 version。");
  const snapshotDate = values.get("--snapshot");
  const asOfDate = values.get("--as-of");
  const exportStart = values.get("--export-start");
  if (!exportStart || !Number.isFinite(Date.parse(exportStart))) {
    throw new Error("正式下载绑定必须提供有效的 --export-start ISO 日期时间。");
  }
  const expectedSourceRows = Number(values.get("--expected-source-rows"));
  if (!Number.isSafeInteger(expectedSourceRows) || expectedSourceRows <= 0) {
    throw new Error("必须提供页面稳定后读取的正整数 --expected-source-rows。");
  }
  const previousComboRowsValue = values.get("--previous-combo-rows");
  const previousComboRows = previousComboRowsValue === undefined ? undefined : Number(previousComboRowsValue);
  if (previousComboRows !== undefined && (!Number.isSafeInteger(previousComboRows) || previousComboRows <= 0)) {
    throw new Error("--previous-combo-rows 必须是正整数。");
  }
  if (previousComboRows !== undefined && (!dryRun || moduleValue !== "combos")) {
    throw new Error("--previous-combo-rows 仅允许 combos 的离线 --dry-run 使用；正式任务必须读取系统成功批次。");
  }
  if ((moduleValue === "inventory" || moduleValue === "inventory_age") && !isIsoDate(snapshotDate)) {
    throw new Error(`${moduleValue} 必须提供有效的 --snapshot YYYY-MM-DD。`);
  }
  if ((moduleValue === "inventory" || moduleValue === "inventory_age") && snapshotDate !== shanghaiIsoYesterday()) {
    throw new Error(`每日 ${moduleValue} 的 --snapshot 必须是北京时间昨天 ${shanghaiIsoYesterday()}；历史补跑请使用独立人工流程。`);
  }
  if (moduleValue === "sales" && !isIsoDate(asOfDate)) throw new Error("sales 必须提供有效的 --as-of YYYY-MM-DD。");
  if (moduleValue === "sales" && asOfDate !== shanghaiIsoYesterday()) {
    throw new Error(`每日 sales 的 --as-of 必须是北京时间昨天 ${shanghaiIsoYesterday()}；历史补跑请使用独立人工流程。`);
  }
  if (moduleValue === "sales" && !values.get("--cost-source")) {
    throw new Error("sales 必须显式提供本轮库存输出 --cost-source。");
  }
  const handoffFilePath = values.get("--handoff-file");
  const handoff = handoffFilePath
    ? await readJsonFile<BrowserHandoffFile>(path.resolve(handoffFilePath))
    : undefined;
  if (!handoff && !dryRun) {
    throw new Error("正式下载后处理必须提供本轮 controller 原子生成的 --handoff-file。");
  }
  if (handoff) {
    if (handoff.schemaVersion !== 2
      || handoff.runId !== runId
      || handoff.policyVersion !== policyVersion
      || handoff.module !== moduleValue
      || !handoff.filePath || path.resolve(handoff.filePath) !== path.resolve(filePath)
      || Date.parse(handoff.exportIntentAt ?? "") !== Date.parse(exportStart)
      || handoff.expectedSourceRows !== expectedSourceRows) {
      throw Object.assign(
        new Error("FILE_BINDING_FAILED: --handoff-file 的版本、运行、策略、模块、文件、导出时间或页面行数与本轮参数不一致。"),
        { code: "FILE_BINDING_FAILED" },
      );
    }
    const navigationAt = Date.parse(handoff.navigationIntentAt ?? "");
    const queryAt = Date.parse(handoff.queryIntentAt ?? "");
    const tableStableAt = Date.parse(handoff.tableStableAt ?? "");
    const exportAt = Date.parse(handoff.exportIntentAt ?? "");
    const requiresQuery = moduleValue === "inventory" || moduleValue === "inventory_age" || moduleValue === "sales";
    if (!Number.isFinite(navigationAt) || !Number.isFinite(tableStableAt)
      || (requiresQuery && !Number.isFinite(queryAt))
      || tableStableAt < (requiresQuery ? queryAt : navigationAt)
      || tableStableAt > exportAt) {
      throw Object.assign(
        new Error("FIELD_MISMATCH: --handoff-file 的导航、查询、表格稳定与导出时间线无效。"),
        { code: "FIELD_MISMATCH" },
      );
    }
  }
  const handoffEvidence = handoff ? {
    navigationIntentAt: handoff.navigationIntentAt!,
    queryIntentAt: handoff.queryIntentAt,
    tableStableAt: handoff.tableStableAt!,
    exportIntentAt: handoff.exportIntentAt!,
    downloadEventAt: handoff.downloadEventAt!,
  } : undefined;
  if (handoffEvidence) assertJackyunHandoffEvidence(handoffEvidence, moduleValue);
  const downloadMethod = values.get("--download-method");
  if (handoff?.downloadProvenance && downloadMethod) {
    throw new Error("--handoff-file 与分散的 --download-* 证据不能同时提供。");
  }
  const downloadProvenance = handoff?.downloadProvenance ?? (downloadMethod ? {
    runId,
    module: moduleValue,
    policyVersion,
    downloadId: values.get("--download-id") ?? "",
    method: downloadMethod as JackyunDownloadProvenance["method"],
    completedAt: values.get("--download-completed-at") ?? "",
    originalFileName: values.get("--original-file-name") ?? path.basename(filePath),
    sourceHost: values.get("--source-host"),
    sourceUrlHash: values.get("--source-url-hash"),
    sha256: values.get("--expected-download-sha256"),
    bytes: values.has("--download-bytes") ? Number(values.get("--download-bytes")) : undefined,
  } : undefined);
  assertBoundDownloadProvenance(downloadProvenance, defaultJackyunDownloadHosts, {
    runId,
    module: moduleValue,
    policyVersion,
  });
  if (handoff && handoff.downloadEventAt !== downloadProvenance.completedAt) {
    throw Object.assign(
      new Error("FILE_BINDING_FAILED: --handoff-file 的 downloadEventAt 与 provenance.completedAt 不一致。"),
      { code: "FILE_BINDING_FAILED" },
    );
  }
  const snapshotEvidenceFile = values.get("--snapshot-evidence-file");
  let snapshotEvidence: JackyunHistoricalSnapshotEvidence | undefined;
  if (moduleValue === "inventory" || moduleValue === "inventory_age") {
    if (!dryRun && snapshotEvidenceFile) {
      throw new Error("正式任务的历史快照证据必须内嵌于 controller 原子 handoff，不接受独立证据文件修补。");
    }
    if (!snapshotEvidenceFile && !handoff?.snapshotEvidence) {
      throw new Error(`${moduleValue} 必须提供含历史日期读回的 --handoff-file；单独的 --snapshot 不能证明历史快照。`);
    }
    if (snapshotEvidenceFile && handoff?.snapshotEvidence) {
      throw new Error("--handoff-file 已包含历史快照证据，不得再提供 --snapshot-evidence-file。");
    }
    const payload = snapshotEvidenceFile
      ? await readJsonFile<unknown>(path.resolve(snapshotEvidenceFile))
      : handoff?.snapshotEvidence;
    const candidate = payload && typeof payload === "object" && "snapshotEvidence" in payload
      ? (payload as { snapshotEvidence?: unknown }).snapshotEvidence
      : payload;
    assertJackyunHistoricalSnapshotEvidence(candidate, {
      module: moduleValue,
      runId,
      snapshotDate: snapshotDate!,
      navigationIntentAt: handoff?.navigationIntentAt,
      exportIntentAt: exportStart,
    });
    if (handoff && (candidate.queryIntentAt !== handoff.queryIntentAt
      || candidate.tableStableAt !== handoff.tableStableAt)) {
      throw Object.assign(
        new Error("FIELD_MISMATCH: 历史快照证据与 handoff 的查询/表格稳定时间不一致。"),
        { code: "FIELD_MISMATCH" },
      );
    }
    snapshotEvidence = candidate;
  } else if (snapshotEvidenceFile || handoff?.snapshotEvidence) {
    throw new Error(`${moduleValue} 不接受历史库存快照证据。`);
  }
  return {
    module: moduleValue,
    filePath: path.resolve(filePath),
    runId,
    policyVersion,
    snapshotDate,
    snapshotEvidence,
    asOfDate,
    costSourcePath: values.get("--cost-source") ? path.resolve(values.get("--cost-source")!) : undefined,
    exportStart,
    expectedSourceRows,
    previousComboRows,
    baseUrl: (values.get("--base-url") ?? process.env.OPERATIONS_SYSTEM_URL ?? "http://localhost:3000").replace(/\/$/, ""),
    outputRoot: path.resolve(values.get("--output-root") ?? path.join(projectRoot, "outputs", "jackyun-import-runs")),
    downloadDirectory: path.resolve(values.get("--download-dir") ?? defaultDownloadDirectory),
    downloadProvenance,
    handoffEvidence,
    dryRun,
  };
}

function sha256(bytes: Uint8Array) {
  return createHash("sha256").update(bytes).digest("hex");
}

function initialManifest(runId: string): RunManifest {
  const now = new Date().toISOString();
  return { version: 1, runId, startedAt: now, updatedAt: now, strictOrder: jackyunModuleOrder, modules: {} };
}

function assertStrictSequence(manifest: RunManifest, module: JackyunModule, dryRun: boolean) {
  if (manifest.strictOrder.length !== jackyunModuleOrder.length
    || manifest.strictOrder.some((item, index) => item !== jackyunModuleOrder[index])) {
    throw new Error(`运行清单使用旧模块顺序，不能与当前流程混跑；请创建新的 run id。当前顺序：${jackyunModuleOrder.join(" -> ")}。`);
  }
  const moduleIndex = jackyunModuleOrder.indexOf(module);
  for (const previous of jackyunModuleOrder.slice(0, moduleIndex)) {
    const status = manifest.modules[previous]?.status;
    const allowed = dryRun ? status === "prepared" || status === "completed" : status === "completed";
    if (!allowed) throw new Error(`严格串行检查失败：${previous} 尚未完成，不能处理 ${module}。`);
  }
}

async function waitForStableFile(filePath: string) {
  let previous: { size: number; mtimeMs: number } | null = null;
  let stableSince = 0;
  const deadline = Date.now() + 6_000;
  const pollIntervalMs = 250;
  const requiredStableMs = 1_000;
  while (Date.now() < deadline) {
    const partialExists = await stat(`${filePath}.crdownload`).then(() => true).catch(() => false);
    if (partialExists) {
      previous = null;
      await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
      continue;
    }
    const current = await stat(filePath);
    if (!current.isFile() || current.size === 0) throw new Error("下载文件为空或不是普通文件。");
    if (previous && previous.size === current.size && previous.mtimeMs === current.mtimeMs) {
      if (stableSince && Date.now() - stableSince >= requiredStableMs) {
        return { size: current.size, mtimeMs: current.mtimeMs, mtime: current.mtime } satisfies StableFileEvidence;
      }
    } else {
      stableSince = Date.now();
    }
    previous = { size: current.size, mtimeMs: current.mtimeMs };
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
  throw new Error("下载文件在 6 秒内仍持续变化或存在 .crdownload，未连续稳定 1 秒。");
}

function assertCurrentDownload(options: CliOptions, file: StableFileEvidence) {
  const exactRunModuleDirectory = path.join(
    options.downloadDirectory,
    "jackyun",
    options.runId,
    options.module,
  );
  const relative = path.relative(exactRunModuleDirectory, options.filePath);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)
    || path.resolve(path.dirname(options.filePath)) !== path.resolve(exactRunModuleDirectory)) {
    throw Object.assign(
      new Error(`FILE_BINDING_FAILED: 原始文件必须位于本轮模块专属目录内：${exactRunModuleDirectory}`),
      { code: "FILE_BINDING_FAILED" },
    );
  }
  if (path.extname(options.filePath).toLowerCase() !== ".xlsx") throw new Error("下载文件扩展名必须为 .xlsx。");
  assertBoundDownloadProvenance(
    options.downloadProvenance,
    options.allowedDownloadHosts ?? defaultJackyunDownloadHosts,
    { runId: options.runId, module: options.module, policyVersion: options.policyVersion },
  );
  if (Date.parse(options.downloadProvenance.completedAt) < Date.parse(options.exportStart)) {
    throw Object.assign(new Error("下载完成时间早于本轮导出 intent。"), { code: "FILE_BINDING_FAILED" });
  }
  if (options.downloadProvenance.bytes !== file.size) {
    throw Object.assign(new Error("下载事件字节数与稳定文件大小不一致。"), { code: "FILE_BINDING_FAILED" });
  }
  assertJackyunDownloadFreshness(file.mtimeMs, options.exportStart);
}

export function assertJackyunDownloadFreshness(fileMtimeMs: number, exportStart: string) {
  const exportStartMs = Date.parse(exportStart);
  if (!Number.isFinite(exportStartMs) || !Number.isFinite(fileMtimeMs)) throw new Error("下载文件时间证据无效。");
  if (fileMtimeMs < exportStartMs) {
    throw new Error("下载文件修改时间早于本轮导出 intent，已拒绝复用历史文件。");
  }
}

async function responseJson(response: Response) {
  const body = await response.json().catch(() => null) as Record<string, unknown> | null;
  if (!response.ok || !body || body.ok === false) {
    throw new RunnerHttpError(
      typeof body?.message === "string" ? body.message : `请求失败：HTTP ${response.status}`,
      {
        httpStatus: response.status,
        status: body?.status ?? null,
        errorCount: body?.errorCount ?? null,
        errors: Array.isArray(body?.errors) ? body.errors.slice(0, 20) : [],
        warnings: Array.isArray(body?.warnings) ? body.warnings.slice(0, 20) : [],
      },
    );
  }
  return body;
}

function fetchWithTimeout(input: string | URL | Request, init: RequestInit = {}, timeoutMs = 60_000) {
  return fetch(input, { ...init, signal: init.signal ?? AbortSignal.timeout(timeoutMs) });
}

async function verifyComboRelationBaseline(options: CliOptions, currentRowCount: number) {
  if (options.previousComboRows !== undefined) {
    return {
      ...assertComboRelationBaseline(currentRowCount, {
        id: "offline-dry-run-baseline",
        rowCount: options.previousComboRows,
      }),
      source: "offline_dry_run_argument" as const,
    };
  }
  const history = await responseJson(await fetchWithTimeout(
    `${options.baseUrl}/api/imports/erp?source=combos&limit=100`,
    { cache: "no-store" },
  ));
  if (!Array.isArray(history.items)) throw new Error("组合装导入历史响应缺少 items，不能执行安全替换。");
  const latestCompleted = history.items.find((item) => {
    if (!item || typeof item !== "object") return false;
    const batch = item as Record<string, unknown>;
    return batch.status === "completed" && Number.isSafeInteger(batch.rowCount) && Number(batch.rowCount) > 0;
  }) as Record<string, unknown> | undefined;
  const baseline = latestCompleted
    ? { id: String(latestCompleted.id ?? ""), rowCount: Number(latestCompleted.rowCount) }
    : null;
  return {
    ...assertComboRelationBaseline(currentRowCount, baseline),
    source: "operations_system_history" as const,
  };
}

async function uploadWorkbook(options: CliOptions, module: JackyunWorkbookModule, bytes: Uint8Array, fileName: string, fingerprint: string) {
  const isInventory = module === "inventory";
  const endpoint = `${options.baseUrl}/api/imports/${isInventory ? "inventory" : "erp"}/chunks`;
  const source = isInventory ? undefined : module;
  const chunkSize = 1024 * 1024;
  const chunkCount = Math.ceil(bytes.byteLength / chunkSize);
  const initBody = await responseJson(await fetchWithTimeout(endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      action: "init",
      ...(source ? { source } : {}),
      fileName,
      fileSizeBytes: bytes.byteLength,
      chunkCount,
      fingerprint: `${module}:${fingerprint}`,
      ...(options.snapshotDate ? { snapshotDate: options.snapshotDate } : {}),
    }),
  }));
  const upload = initBody.upload as { id?: string; receivedChunkIndexes?: number[] } | undefined;
  if (!upload?.id) throw new Error("初始化分片上传后未返回 upload id。");
  const received = new Set(upload.receivedChunkIndexes ?? []);
  // Upload chunks in parallel with limited concurrency to reduce total upload time.
  // The server tracks received chunks by index, so out-of-order arrival is safe.
  const pendingIndexes: number[] = [];
  for (let index = 0; index < chunkCount; index += 1) {
    if (!received.has(index)) pendingIndexes.push(index);
  }
  const uploadId = upload.id;
  const uploadConcurrency = 3;
  for (let batchStart = 0; batchStart < pendingIndexes.length; batchStart += uploadConcurrency) {
    const batch = pendingIndexes.slice(batchStart, batchStart + uploadConcurrency);
    await Promise.all(batch.map(async (index) => {
      const start = index * chunkSize;
      const end = Math.min(start + chunkSize, bytes.byteLength);
      const chunk = new Uint8Array(end - start);
      chunk.set(bytes.subarray(start, end));
      await responseJson(await fetchWithTimeout(endpoint, {
        method: "PUT",
        headers: {
          "content-type": "application/octet-stream",
          "x-upload-id": uploadId,
          "x-chunk-index": String(index),
        },
        body: chunk.buffer,
      }));
    }));
  }
  return responseJson(await fetchWithTimeout(endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      action: "complete",
      ...(source ? { source } : {}),
      uploadId: upload.id,
      ...(options.snapshotDate ? { snapshotDate: options.snapshotDate } : {}),
    }),
  }, 10 * 60_000));
}

function summarizeBatch(batch: Record<string, unknown>): BatchSummary {
  const warnings = Array.isArray(batch.warnings) ? batch.warnings : [];
  return {
    id: String(batch.id ?? ""),
    status: String(batch.status ?? ""),
    rowCount: Number(batch.rowCount ?? 0),
    insertedCount: typeof batch.insertedCount === "number" ? batch.insertedCount : undefined,
    updatedCount: typeof batch.updatedCount === "number" ? batch.updatedCount : undefined,
    excludedCount: typeof batch.excludedCount === "number" ? batch.excludedCount : undefined,
    warningCount: typeof batch.warningCount === "number" ? batch.warningCount : warnings.length,
    snapshotDate: typeof batch.snapshotDate === "string" || batch.snapshotDate === null ? batch.snapshotDate : undefined,
    warnings: warnings.slice(0, 20),
  };
}

function verifyWorkbookImport(
  options: CliOptions,
  module: JackyunWorkbookModule,
  fingerprint: string,
  expectedRows: number,
  importResponse: Record<string, unknown>,
) {
  const isInventory = module === "inventory";
  const expectedId = isInventory ? fingerprint : `${module}:${fingerprint}`;
  const item = importResponse.batch && typeof importResponse.batch === "object"
    ? importResponse.batch as Record<string, unknown>
    : null;
  if (!item) throw new Error(`导入后未找到本轮批次：${expectedId}`);
  if (options.policyVersion === jackyunExportFirstPolicyVersion) {
    jackyunDjangoImportReceipt(module, fingerprint, importResponse);
  } else if (String(item.id ?? "") !== expectedId) throw new Error(`导入后批次号不一致：期望 ${expectedId}。`);
  const batch = summarizeBatch(item);
  if (batch.status !== "completed") throw new Error(`本轮批次状态不是 completed：${batch.status}`);
  if (batch.rowCount !== expectedRows) throw new Error(`导入后行数不一致：期望 ${expectedRows}，实际 ${batch.rowCount}。`);
  if ((module === "inventory" || module === "inventory_age") && batch.snapshotDate !== options.snapshotDate) {
    throw new Error(`导入后快照日期不一致：期望 ${options.snapshotDate ?? ""}，实际 ${batch.snapshotDate ?? ""}。`);
  }
  if ((module === "products" || module === "combos") && batch.snapshotDate !== null) {
    throw new Error(`${module} 不应包含快照日期。`);
  }
  return batch;
}

async function processSales(
  options: CliOptions,
  manifest: RunManifest,
  boundSalesPath: string,
  expectedRawSha256: string,
  rawBytes: Uint8Array,
) {
  const currentInventoryPath = manifest.modules.inventory?.salesCostSourcePath;
  const currentInventorySha256 = manifest.modules.inventory?.salesCostSourceSha256;
  if (!currentInventoryPath || path.resolve(currentInventoryPath) !== path.resolve(options.costSourcePath!)) {
    throw new Error("--cost-source 必须精确指向本轮 inventory runner 保存的原始成本源副本。");
  }
  if (!currentInventorySha256) throw new Error("本轮 inventory 清单缺少成本源 SHA，不能启动销售任务。");
  return runSalesImport({
    asOfDate: options.asOfDate!,
    salesStartDate: options.salesStartDate,
    downloadPath: boundSalesPath,
    downloadBytes: rawBytes,
    preserveRawCopy: false,
    costSourcePath: options.costSourcePath!,
    expectedDownloadSha256: expectedRawSha256,
    expectedCostSha256: currentInventorySha256,
    expectedSourceRows: options.expectedSourceRows,
    auditRootPath: path.join(options.outputRoot, options.runId, "sales"),
    baseUrl: options.baseUrl,
    dryRun: options.dryRun,
  });
}

export async function runJackyunDownload(options: JackyunDownloadRunOptions) {
  const startedAt = new Date().toISOString();
  const runDirectory = path.join(options.outputRoot, options.runId);
  const rawDirectory = path.join(runDirectory, "raw");
  const processedDirectory = path.join(runDirectory, "processed");
  const auditDirectory = path.join(runDirectory, "audit");
  const manifestPath = path.join(runDirectory, "run-manifest.json");
  const auditPath = path.join(auditDirectory, `${options.module}.json`);
  await Promise.all([mkdir(rawDirectory, { recursive: true }), mkdir(processedDirectory, { recursive: true }), mkdir(auditDirectory, { recursive: true })]);
  const manifest = await readJsonFileOr<RunManifest>(manifestPath, initialManifest(options.runId));
  if (manifest.runId !== options.runId) throw new Error("运行目录中的 run id 不一致。");
  let priorModule = manifest.modules[options.module];

  let rawHash = "";
  let inputContractHash = "";
  let inputContract: JackyunInputContract | null = null;
  let stage = "strict_sequence";
  let stageStartedAtMs = Date.now();
  const stageElapsedMs: Record<string, number> = {};
  const moveToStage = (nextStage: string) => {
    stageElapsedMs[stage] = (stageElapsedMs[stage] ?? 0) + Date.now() - stageStartedAtMs;
    stage = nextStage;
    stageStartedAtMs = Date.now();
  };
  const currentStageElapsed = () => ({
    ...stageElapsedMs,
    [stage]: (stageElapsedMs[stage] ?? 0) + Date.now() - stageStartedAtMs,
  });
  try {
    assertStrictSequence(manifest, options.module, options.dryRun);
    if (!options.dryRun) assertJackyunHandoffEvidence(options.handoffEvidence, options.module);
    moveToStage("verify_historical_snapshot_evidence");
    if (options.module === "inventory" || options.module === "inventory_age") {
      assertJackyunSnapshotEvidence(options.snapshotEvidence, {
        module: options.module,
        runId: options.runId,
        snapshotDate: options.snapshotDate!,
        policyVersion: options.policyVersion,
        exportIntentAt: options.exportStart,
      });
    } else if (options.snapshotEvidence) {
      throw Object.assign(new Error(`${options.module} 不接受历史库存快照证据。`), { code: "FIELD_MISMATCH" });
    }
    moveToStage("wait_download_file_stable");
    const stableFile = await waitForStableFile(options.filePath);
    const fileStableAt = new Date().toISOString();
    moveToStage("verify_download_binding");
    assertCurrentDownload(options, stableFile);
    moveToStage("read_and_hash_source");
    const rawBytes = new Uint8Array(await readFile(options.filePath));
    rawHash = sha256(rawBytes);
    if (options.downloadProvenance!.sha256!.toLowerCase() !== rawHash) {
      throw Object.assign(new Error("下载事件 SHA-256 与 runner 实际文件不一致。"), { code: "FILE_BINDING_FAILED" });
    }
    if (options.downloadProvenance!.bytes !== rawBytes.byteLength) {
      throw Object.assign(new Error("下载事件字节数与 runner 实际文件不一致。"), { code: "FILE_BINDING_FAILED" });
    }
    inputContract = {
      runId: options.runId,
      policyVersion: options.policyVersion,
      module: options.module,
      rawSha256: rawHash,
      snapshotDate: options.snapshotDate,
      snapshotEvidence: options.snapshotEvidence,
      asOfDate: options.asOfDate,
      ...(options.salesStartDate !== undefined ? { salesStartDate: options.salesStartDate } : {}),
      expectedSourceRows: options.expectedSourceRows,
      previousComboRows: options.previousComboRows,
      costOutputSha256: options.module === "sales" ? manifest.modules.inventory?.salesCostSourceSha256 : undefined,
      costSourcePath: options.module === "sales" ? options.costSourcePath : undefined,
      exportStart: options.exportStart,
      downloadEventAt: options.downloadProvenance!.completedAt,
      downloadProvenance: options.downloadProvenance!,
      handoffEvidence: options.handoffEvidence,
      baseUrl: options.baseUrl,
    };
    inputContractHash = createJackyunInputContractHash(inputContract);
    const existing = manifest.modules[options.module];
    if (existing) {
      assertNoJackyunPreparedPromotion(existing.status, options.dryRun);
      if (existing.sourceSha256 === rawHash
        && existing.inputContractHash === inputContractHash
        && (existing.status === "completed" || (existing.status === "prepared" && options.dryRun))) {
        await verifyJackyunModuleArtifact({
          runDirectory,
          runId: options.runId,
          module: options.module,
          snapshotDate: options.snapshotDate ?? options.asOfDate ?? "",
          salesStartDate: options.salesStartDate,
          policyVersion: options.policyVersion,
          allowedDownloadHosts: options.allowedDownloadHosts,
          manifestModule: existing,
          expectedStatus: existing.status,
        });
        const result = { status: "duplicate_ignored", runId: options.runId, module: options.module, auditPath, manifestPath, existing };
        return result;
      }
      const failedAudit = options.sourceRowCountCorrection || (options.module === "combos" && existing.status === "failed")
        ? await readJsonFileOr<Record<string, unknown> | null>(auditPath, null)
        : null;
      const comboRepairParse = options.module === "combos" && existing.status === "failed" && options.runId === auditedComboNameRejection.runId
        ? prepareJackyunWorkbook("combos", rawBytes) : null;
      const repairsComboName = !options.dryRun && isAuditedComboNameRepair({
        runId: options.runId, module: options.module, sourceSha256: rawHash, inputContractHash, priorModule: existing, failedAudit,
        relationCountVerified: comboRepairParse?.expectedBatchRowCount === 4392,
      });
      const repairsExactRowCount = isExactFailedSourceRowCountRepair({
        runId: options.runId,
        module: options.module,
        filePath: options.filePath,
        rawSha256: rawHash,
        expectedSourceRows: options.expectedSourceRows,
        correction: options.sourceRowCountCorrection,
        priorModule: existing as unknown as Record<string, unknown>,
        failedAudit,
      });
      if (!options.dryRun && options.importRecovery) {
        assertExactFailedImportRetry({ runId: options.runId, module: options.module,
          sourceSha256: rawHash, inputContractHash, prior: existing,
          auditRaw: await readFile(auditPath), binding: options.importRecovery });
        // The pipeline already saved the original failed manifest and audit in
        // a create-only archive before binding this new n8n execution.
        delete manifest.modules[options.module];
        priorModule = undefined;
      } else if (repairsComboName) {
        await writeFile(path.join(auditDirectory, `combos.name-whitespace-repair-${auditedComboNameRejection.auditSha256}.json`),
          JSON.stringify({ failedAudit, repair: "audited_845_combo_name_whitespace", repairedAt: new Date().toISOString() }) + "\n", { flag: "wx" });
        delete manifest.modules[options.module];
        priorModule = undefined;
      } else if (repairsExactRowCount) {
        await writeJsonAtomic(path.join(auditDirectory, `${options.module}.row-count-repair-${Date.now()}.json`), {
          ...failedAudit,
          repair: options.sourceRowCountCorrection,
          repairedAt: new Date().toISOString(),
        });
        delete manifest.modules[options.module];
        priorModule = undefined;
      } else {
        throw new Error(`${options.module} 已在本轮登记，但文件或输入参数契约不同；不能覆盖或按重复成功跳过。请使用新的 run id。`);
      }
    }
    moveToStage("copy_raw_source");
    const rawCopyPath = path.join(rawDirectory, `${options.module}-${path.basename(options.filePath)}`);
    await writeFile(rawCopyPath, rawBytes);

    let outputPath = rawCopyPath;
    let outputBytes = rawBytes.byteLength;
    let outputSha256 = rawHash;
    let moduleResult: Record<string, unknown>;
    let batch: BatchSummary | null = null;
    let validation: unknown = null;
    let sourceCountContract: unknown = null;
    let preprocessing: unknown = null;
    let comboRelationBaseline: unknown = null;
    let importHash = rawHash;
    let importBytes: Uint8Array = rawBytes;

    if (options.module === "sales") {
      moveToStage("sales_filter_cost_match_import_verify");
      const salesResult = await processSales(options, manifest, rawCopyPath, rawHash, rawBytes);
      const salesAudit = salesResult.audit as Record<string, unknown> | undefined;
      const expectedPeriod = jackyunSalesPeriod(options.asOfDate!, options.salesStartDate);
      const childPeriod = salesAudit?.period as { startDate?: string; endDate?: string } | undefined;
      if (options.salesStartDate !== undefined
        && (childPeriod?.startDate !== expectedPeriod.startDate || childPeriod?.endDate !== expectedPeriod.endDate)) {
        throw new Error("销售 child 处理范围与本轮滚动范围不一致。");
      }
      const output = salesAudit?.output as Record<string, unknown> | undefined;
      if (typeof output?.path !== "string" || typeof output.bytes !== "number" || typeof output.sha256 !== "string") {
        throw new Error("销售 runner 未返回完整的处理文件证据。");
      }
      outputPath = output.path;
      outputBytes = output.bytes;
      outputSha256 = output.sha256;
      const imported = salesAudit?.import as Record<string, unknown> | null | undefined;
      const importedBatch = imported?.batch as Record<string, unknown> | undefined;
      const postImportVerification = salesAudit?.postImportVerification as Record<string, unknown> | undefined;
      batch = importedBatch ? summarizeBatch(importedBatch) : null;
      validation = salesAudit?.validation ?? null;
      preprocessing = salesAudit?.filtering ?? null;
      const sources = salesAudit?.sources as Record<string, unknown> | undefined;
      const childRaw = sources?.rawDownload as Record<string, unknown> | undefined;
      const childCost = sources?.costSource as Record<string, unknown> | undefined;
      if (childRaw?.sha256 !== rawHash) throw new Error("销售 child 实际读取的原始文件 SHA 与父 runner 绑定值不一致。");
      if (childCost?.sha256 !== manifest.modules.inventory?.salesCostSourceSha256) {
        throw new Error("销售 child 实际读取的成本源 SHA 与本轮 inventory 清单不一致。");
      }
      const childSourceCountContract = salesAudit?.sourceCountContract as Record<string, unknown> | undefined;
      if (childSourceCountContract?.semantic !== salesSourceRowCountSemantic
        || childSourceCountContract.expected !== options.expectedSourceRows
        || childSourceCountContract.actual !== options.expectedSourceRows
        || childSourceCountContract.verified !== true) {
        throw new JackyunValidationError(
          "销售 runner 未返回页面计数与 XLSX 非空明细行精确相等的完整性证据。",
          { expectedSourceRows: options.expectedSourceRows, sourceCountContract: childSourceCountContract ?? null },
        );
      }
      sourceCountContract = { ...childSourceCountContract };
      moduleResult = {
        status: typeof salesResult.status === "string" ? salesResult.status : (options.dryRun ? "prepared" : "completed"),
        salesRunId: typeof salesAudit?.runId === "string" ? salesAudit.runId : null,
        salesPolicyVersion: typeof salesAudit?.policyVersion === "string" ? salesAudit.policyVersion : null,
        postImportVerified: postImportVerification?.verified === true,
        ...(options.salesStartDate !== undefined ? { salesPeriod: expectedPeriod } : {}),
      };
      if (!options.dryRun && (salesResult.status !== "verified_completed"
        || !batch || batch.status !== "completed" || postImportVerification?.verified !== true)) {
        throw new Error("销售 runner 未返回经过落库回查的完成批次。");
      }
    } else {
      moveToStage("validate_and_prepare_workbook");
      const prepared = prepareJackyunWorkbook(options.module, rawBytes, { snapshotDate: options.snapshotDate });
      const comparableSourceRows = options.module === "combos"
        ? prepared.validation.parentRowCount
        : prepared.validation.sourceRowCount;
      if (comparableSourceRows !== options.expectedSourceRows) {
        throw new JackyunValidationError(
          `${options.module} 源文件行数与页面稳定值不一致：页面 ${options.expectedSourceRows}，文件 ${comparableSourceRows ?? "未知"}。`,
          { expectedSourceRows: options.expectedSourceRows, actualSourceRows: comparableSourceRows ?? null },
        );
      }
      validation = prepared.validation;
      preprocessing = prepared.preprocessing;
      if (options.module === "combos") {
        moveToStage("verify_combo_relation_baseline");
        comboRelationBaseline = await verifyComboRelationBaseline(options, prepared.expectedBatchRowCount);
      }
      importBytes = prepared.importBytes;
      importHash = sha256(importBytes);
      outputBytes = importBytes.byteLength;
      outputSha256 = importHash;
      if (prepared.importBytes !== rawBytes) {
        outputPath = path.join(processedDirectory, prepared.importFileName);
        await writeFile(outputPath, importBytes);
      }
      if (options.dryRun) {
        moduleResult = { status: "prepared", dryRun: true };
      } else {
        moveToStage("chunk_upload_and_import");
        const importResponse = await uploadWorkbook(options, options.module, importBytes, prepared.importFileName, importHash);
        moveToStage("verify_exact_import_batch");
        batch = verifyWorkbookImport(options, options.module, importHash, prepared.expectedBatchRowCount, importResponse);
        moduleResult = {
          status: "completed",
          responseStatus: typeof importResponse.status === "string" ? importResponse.status : null,
          ...(options.policyVersion === jackyunExportFirstPolicyVersion ? {
            djangoReceipt: jackyunDjangoImportReceipt(options.module, importHash, importResponse),
          } : {}),
        };
      }
    }

    moveToStage("assemble_success_audit");
    const validatedAt = new Date().toISOString();
    const completedAt = new Date().toISOString();
    const status: ModuleStatus = options.dryRun ? "prepared" : "completed";
    const audit = {
      version: 1,
      runId: options.runId,
      module: options.module,
      status,
      timings: {
        startedAt,
        fileStableAt,
        validatedAt,
        completedAt,
        elapsedMs: Date.parse(completedAt) - Date.parse(startedAt),
        stageElapsedMs: currentStageElapsed(),
      },
      source: {
        path: options.filePath,
        copiedPath: rawCopyPath,
        fileName: path.basename(options.filePath),
        bytes: rawBytes.byteLength,
        modifiedAt: stableFile.mtime.toISOString(),
        sha256: rawHash,
        exportStart: options.exportStart ?? null,
        expectedSourceRows: options.expectedSourceRows,
        inputContractHash,
        inputContract,
        handoffEvidence: options.handoffEvidence ?? null,
        downloadEventAt: options.downloadProvenance?.completedAt ?? null,
        downloadProvenance: options.downloadProvenance ?? null,
        snapshotEvidence: options.snapshotEvidence ?? null,
      },
      validation,
      sourceCountContract,
      preprocessing,
      safetyChecks: {
        comboRelationBaseline,
      },
      output: {
        path: outputPath,
        bytes: outputBytes,
        sha256: outputSha256,
      },
      import: {
        result: moduleResult,
        batch,
      },
    };
    moveToStage("write_success_audit");
    await writeJsonAtomic(auditPath, audit);
    manifest.modules[options.module] = {
      module: options.module,
      status,
      sourcePath: options.filePath,
      sourceSha256: rawHash,
      outputPath,
      outputSha256,
      salesCostSourcePath: options.module === "inventory" ? rawCopyPath : undefined,
      salesCostSourceSha256: options.module === "inventory" ? rawHash : undefined,
      inputContractHash,
      batchId: batch?.id ?? null,
      startedAt,
      completedAt,
    };
    manifest.updatedAt = completedAt;
    moveToStage("write_run_manifest");
    await writeJsonAtomic(manifestPath, manifest);
    return {
      status,
      runId: options.runId,
      module: options.module,
      auditPath,
      manifestPath,
      outputPath,
      salesCostSourcePath: options.module === "inventory" ? rawCopyPath : undefined,
      batch,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const details = error instanceof JackyunValidationError || error instanceof RunnerHttpError
      ? error.details
      : undefined;
    const failedAt = new Date().toISOString();
    const failedAudit = {
      version: 1,
      runId: options.runId,
      module: options.module,
      status: "failed",
      timings: {
        startedAt,
        failedAt,
        elapsedMs: Date.parse(failedAt) - Date.parse(startedAt),
        stageElapsedMs: currentStageElapsed(),
      },
      source: {
        path: options.filePath,
        sha256: rawHash || null,
        exportStart: options.exportStart ?? null,
        downloadProvenance: options.downloadProvenance ?? null,
        snapshotEvidence: options.snapshotEvidence ?? null,
      },
      error: { stage, message, details },
    };
    if (priorModule) {
      const attemptAuditPath = path.join(auditDirectory, `${options.module}.attempt-failed-${Date.now()}.json`);
      await writeJsonAtomic(attemptAuditPath, { ...failedAudit, priorModulePreserved: true });
      throw error;
    }
    await writeJsonAtomic(auditPath, failedAudit);
    manifest.modules[options.module] = {
      module: options.module,
      status: "failed",
      sourcePath: options.filePath,
      sourceSha256: rawHash || undefined,
      inputContractHash: inputContractHash || undefined,
      startedAt,
      completedAt: failedAt,
      error: message,
    };
    manifest.updatedAt = failedAt;
    await writeJsonAtomic(manifestPath, manifest);
    throw error;
  }
}

async function main() {
  const options = await parseCli();
  const result = await withJackyunRunLock(
    { runId: options.runId, purpose: `post_download_${options.module}` },
    () => runJackyunDownload(options),
  );
  console.log(JSON.stringify({
    status: result.status,
    runId: result.runId,
    module: result.module,
    outputPath: "outputPath" in result ? result.outputPath : result.existing.outputPath ?? null,
    salesCostSourcePath: "salesCostSourcePath" in result
      ? result.salesCostSourcePath ?? null
      : result.existing.salesCostSourcePath ?? null,
    batchId: "batch" in result ? result.batch?.id ?? null : result.existing.batchId ?? null,
    rowCount: "batch" in result ? result.batch?.rowCount ?? null : null,
    warningCount: "batch" in result ? result.batch?.warningCount ?? 0 : null,
    auditPath: result.auditPath,
  }));
}

if (path.resolve(process.argv[1] ?? "") === path.resolve(fileURLToPath(import.meta.url))) {
  main().catch((error: unknown) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
