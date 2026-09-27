import { mkdir } from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { loadJdStores, type JdStore } from "../lib/jd/store-registry";
import { assertJdNaturalDateRange } from "../lib/jd/date-range";
import { readJsonFile, writeJsonAtomic } from "../lib/jackyun/json-file";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const auditDir = path.join(projectRoot, "outputs", "jd-multi-store-runner");
const defaultBaseUrl = (process.env.OPERATIONS_SYSTEM_URL ?? "http://localhost:3000").replace(/\/$/, "");
export type Step = "jd_product_master" | "jd_sku_daily" | "spu_daily";
type AuditStatus = "planned" | "running" | "completed" | "failed";
export type AuditItem = { storeKey: string; shopName: string; step: Step; status: AuditStatus; savedPath?: string; batchId?: string; rowCount?: number; /** The already validated, non-sensitive import proof for a later independent n8n C-stage recheck. */ importResult?: Record<string, unknown>; durationMs?: number; error?: string; stderr?: string };
export type RunnerAudit = { version: 1; baseUrl: string; startedAt: string; updatedAt: string; mode: string; dryRun: boolean; silentNoWindow?: boolean; startDate?: string; endDate?: string; storeKeys: string[]; items: AuditItem[] };
export type MultiStoreOptions = ReturnType<typeof parseRunnerArgs> & { baseUrl?: string; resumeAuditPath?: string };
export const JD_PIPELINE_RESULT_SENTINEL = "@@JD_PIPELINE_RESULT@@";

export function parseRunnerArgs(argv: string[]) {
  const value = (name: string) => { const index = argv.indexOf(name); return index >= 0 ? argv[index + 1] : undefined; };
  const has = (name: string) => argv.includes(name);
  const mode = (value("--mode") ?? "all").toLowerCase();
  if (!["all", "master", "sku-daily", "spu-daily"].includes(mode)) throw new Error("--mode 必须是 all/master/sku-daily/spu-daily");
  if (has("--dimension")) throw new Error("多店执行器不再使用 --dimension；请改用 --mode sku-daily 或 --mode spu-daily（all 会依次运行主数据、SKU、SPU）。");
  const explicitStartDate = value("--start-date");
  const explicitEndDate = value("--end-date");
  const hasStartDate = has("--start-date");
  const hasEndDate = has("--end-date");
  if (hasStartDate !== hasEndDate || (hasStartDate && (!explicitStartDate || !explicitEndDate
    || explicitStartDate.startsWith("--") || explicitEndDate.startsWith("--")))) {
    throw new Error("--start-date 与 --end-date 必须成对提供");
  }
  const range = explicitStartDate && explicitEndDate
    ? assertJdNaturalDateRange(explicitStartDate, explicitEndDate)
    : shanghaiDefaultRange();
  return {
    mode,
    startDate: range.startDate,
    endDate: range.endDate,
    storeKey: value("--store-key"),
    dryRun: has("--dry-run"),
    silentNoWindow: has("--no-visible-recovery"),
  };
}

function run(command: string, args: string[]) {
  return new Promise<{ code: number; stdout: string; stderr: string }>((resolve, reject) => {
    const child = spawn(command, args, { cwd: process.cwd(), shell: false, env: process.env, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (chunk) => { stdout += String(chunk); process.stdout.write(chunk); });
    child.stderr?.on("data", (chunk) => { stderr += String(chunk); process.stderr.write(chunk); });
    child.once("error", reject);
    child.once("exit", (code) => resolve({ code: code ?? 1, stdout, stderr }));
  });
}

/** Child scripts may log progress before the final machine-readable result. */
export function parseTrailingJson(stdout: string): Record<string, unknown> | null {
  let result: Record<string, unknown> | null = null;
  for (let index = 0; index < stdout.length; index += 1) {
    if (stdout[index] !== "{") continue;
    try {
      const candidate = JSON.parse(stdout.slice(index)) as unknown;
      if (candidate && typeof candidate === "object" && !Array.isArray(candidate)) result = candidate as Record<string, unknown>;
    } catch { /* Try the next possible JSON object. */ }
  }
  return result;
}

export function parsePipelineResult(stdout: string): Record<string, unknown> | null {
  const lines = stdout.split(/\r?\n/).filter((line) => line.startsWith(JD_PIPELINE_RESULT_SENTINEL));
  if (lines.length !== 1) return null;
  try {
    const parsed = JSON.parse(lines[0]!.slice(JD_PIPELINE_RESULT_SENTINEL.length)) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
  } catch { return null; }
}

export function shanghaiDefaultRange(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(now);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "";
  const today = `${part("year")}-${part("month")}-${part("day")}`;
  const yesterday = new Date(`${today}T00:00:00Z`); yesterday.setUTCDate(yesterday.getUTCDate() - 1);
  const endDate = yesterday.toISOString().slice(0, 10);
  return { startDate: `${endDate.slice(0, 8)}01`, endDate };
}

export function validateStepResult(step: Step, payload: Record<string, unknown>, store: Pick<JdStore, "shopName">, requested: { startDate?: string; endDate?: string } = {}) {
  const imported = payload.importResult;
  if (!imported || typeof imported !== "object" || Array.isArray(imported)) return "missing verified auto-import result";
  const result = imported as Record<string, unknown>;
  if ((result.status !== "imported" && result.status !== "duplicate") || typeof result.batchId !== "string" || !result.batchId
    || typeof result.rowCount !== "number" || !Number.isSafeInteger(result.rowCount) || result.rowCount <= 0
    || result.batchStatus !== "completed" || result.warningCount !== 0 || result.platform !== "京东" || result.shopName !== store.shopName) return "invalid import batch identity or status";
  const daily = step !== "jd_product_master";
  const expectedDataset = step === "jd_sku_daily" ? "sku_daily" : step === "spu_daily" ? "spu_daily" : "product_master";
  const expectedSource = daily ? "jd_sku_daily" : "jd_product_master";
  if (result.dataset !== expectedDataset || result.source !== expectedSource) return "unexpected import source or dataset";
  if (daily) {
    const defaults = shanghaiDefaultRange();
    const range = { startDate: requested.startDate ?? defaults.startDate, endDate: requested.endDate ?? defaults.endDate };
    if (result.dateMin !== range.startDate || result.dateMax !== range.endDate) return "unexpected daily import date range";
  }
  return null;
}

export function auditCounts(items: AuditItem[]) {
  return { planned: items.filter((item) => item.status === "planned").length, running: items.filter((item) => item.status === "running").length, completed: items.filter((item) => item.status === "completed").length, failed: items.filter((item) => item.status === "failed").length };
}

function inside(directory: string, filePath: string) {
  const relative = path.relative(directory, filePath);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

export function assertResumeAuditContract(audit: RunnerAudit, options: MultiStoreOptions, stores: JdStore[], baseUrl: string) {
  const expectedSteps = stepsForMode(options.mode);
  if (audit.version !== 1 || audit.baseUrl !== baseUrl || audit.mode !== options.mode || audit.dryRun !== false
    || Boolean(audit.silentNoWindow) !== options.silentNoWindow
    || audit.startDate !== options.startDate || audit.endDate !== options.endDate
    || audit.storeKeys.length !== stores.length || audit.storeKeys.some((storeKey, index) => storeKey !== stores[index]?.storeKey)
    || audit.items.length !== stores.length * expectedSteps.length) {
    throw new Error("恢复审计与当前京东店铺、模式、日期或本机地址契约不一致");
  }
  let encounteredIncomplete = false;
  let encounteredFailed = false;
  for (const [storeIndex, store] of stores.entries()) {
    for (const [stepIndex, step] of expectedSteps.entries()) {
      const item = audit.items[storeIndex * expectedSteps.length + stepIndex];
      if (!item || item.storeKey !== store.storeKey || item.shopName !== store.shopName || item.step !== step
        || !["planned", "running", "completed", "failed"].includes(item.status)) {
        throw new Error("恢复审计的店铺或步骤顺序无效");
      }
      if (!encounteredIncomplete && item.status === "completed") {
        const invalid = validateStepResult(step, { importResult: item.importResult }, store, options);
        if (invalid || !item.batchId || item.batchId !== item.importResult?.batchId || item.rowCount !== item.importResult?.rowCount) {
          throw new Error(`恢复审计的已完成步骤无法复核: ${store.storeKey}/${step}`);
        }
      }
      if (item.status === "running") throw new Error(`恢复审计包含未关闭步骤，拒绝自动接管: ${store.storeKey}/${step}`);
      if (item.status !== "completed") {
        if (item.status === "failed") {
          if (encounteredIncomplete || encounteredFailed) throw new Error("恢复审计只允许首个未完成步骤为单一 failed");
          encounteredFailed = true;
        } else if (item.status !== "planned") {
          throw new Error("恢复审计包含未知步骤状态");
        }
        encounteredIncomplete = true;
      } else if (encounteredIncomplete) {
        throw new Error("恢复审计必须是连续 completed 前缀，禁止未完成步骤后的 completed");
      }
    }
  }
}

function stepsForMode(mode: string): Step[] {
  if (mode === "master") return ["jd_product_master"];
  if (mode === "sku-daily") return ["jd_sku_daily"];
  if (mode === "spu-daily") return ["spu_daily"];
  return ["jd_product_master", "jd_sku_daily", "spu_daily"];
}

export function jdChildArgs(store: JdStore, step: Step, options: MultiStoreOptions, baseUrl: string) {
  const args = ["--import", "tsx"];
  if (step === "jd_product_master") {
    const command = [...args, "tools/jackyun-ware-export.ts", "--store-key", store.storeKey, "--base-url", baseUrl];
    if (options.silentNoWindow) command.push("--no-visible-recovery");
    return command;
  }
  const dimension = step === "jd_sku_daily" ? "SKU" : "SPU";
  const command = [...args, "tools/jdsz-product-detail-export.ts", "--store-key", store.storeKey, "--dimension", dimension, "--download-dir", store.browser.downloadDir, "--base-url", baseUrl];
  if (options.startDate) command.push("--start-date", options.startDate);
  if (options.endDate) command.push("--end-date", options.endDate);
  if (options.silentNoWindow) command.push("--no-visible-recovery");
  return command;
}

function stderrSummary(stderr: string) {
  return stderr.trim().slice(-2_000) || undefined;
}

export async function runMultiStore(options: MultiStoreOptions, stores?: JdStore[]) {
  const defaults = shanghaiDefaultRange();
  if (Boolean(options.startDate) !== Boolean(options.endDate)) throw new Error("京东多店铺日期必须成对提供");
  const range = assertJdNaturalDateRange(options.startDate ?? defaults.startDate, options.endDate ?? defaults.endDate);
  const resolvedOptions = { ...options, ...range };
  const enabled = (stores ?? await loadJdStores()).filter((store) => store.enabled);
  const selected = resolvedOptions.storeKey ? enabled.filter((store) => store.storeKey === resolvedOptions.storeKey) : enabled;
  if (resolvedOptions.storeKey && selected.length !== 1) throw new Error(`未找到启用的京东店铺注册键: ${resolvedOptions.storeKey}`);
  if (!selected.length) throw new Error("没有启用的京东店铺，拒绝空运行");
  await mkdir(auditDir, { recursive: true });
  const baseUrl = (resolvedOptions.baseUrl ?? defaultBaseUrl).replace(/\/$/, "");
  const auditPath = resolvedOptions.resumeAuditPath ? path.resolve(resolvedOptions.resumeAuditPath) : path.join(auditDir, `run-${Date.now()}.json`);
  if (!inside(auditDir, auditPath) || !/^run-\d+\.json$/.test(path.basename(auditPath))) throw new Error("恢复审计路径不属于受控 runner 输出目录");
  let audit: RunnerAudit;
  if (resolvedOptions.resumeAuditPath) {
    audit = await readJsonFile<RunnerAudit>(auditPath);
    assertResumeAuditContract(audit, resolvedOptions, selected, baseUrl);
    const firstIncomplete = audit.items.find((item) => item.status !== "completed");
    if (firstIncomplete?.status === "failed") {
      firstIncomplete.status = "planned";
      delete firstIncomplete.error;
      delete firstIncomplete.stderr;
      delete firstIncomplete.durationMs;
    }
  } else {
    audit = { version: 1, baseUrl, startedAt: new Date().toISOString(), updatedAt: new Date().toISOString(), mode: resolvedOptions.mode, dryRun: resolvedOptions.dryRun, silentNoWindow: resolvedOptions.silentNoWindow, startDate: resolvedOptions.startDate, endDate: resolvedOptions.endDate, storeKeys: selected.map((store) => store.storeKey), items: selected.flatMap((store) => stepsForMode(resolvedOptions.mode).map((step) => ({ storeKey: store.storeKey, shopName: store.shopName, step, status: "planned" as const }))) };
  }
  const persist = async () => { audit = { ...audit, updatedAt: new Date().toISOString() }; await writeJsonAtomic(auditPath, audit); };
  await persist();

  for (const store of selected) {
    for (const step of stepsForMode(resolvedOptions.mode)) {
      const item = audit.items.find((candidate) => candidate.storeKey === store.storeKey && candidate.step === step)!;
      if (item.status === "completed") continue;
      if (resolvedOptions.dryRun) continue; // Planned is intentional: nothing was executed.
      item.status = "running";
      await persist();
      const started = Date.now();
      let outcome: Awaited<ReturnType<typeof run>>;
      try {
        outcome = await run(process.execPath, jdChildArgs(store, step, resolvedOptions, baseUrl));
      } catch (error) {
        item.status = "failed";
        item.durationMs = Date.now() - started;
        item.error = error instanceof Error ? error.message : String(error);
        await persist();
        return { ok: false, auditPath, audit };
      }
      const payload = parsePipelineResult(outcome.stdout);
      if (outcome.code !== 0 || !payload) {
        item.status = "failed";
        item.durationMs = Date.now() - started;
        item.error = outcome.code !== 0 ? `child exited with ${outcome.code}` : "child did not emit exactly one sentinel pipeline result";
        item.stderr = stderrSummary(outcome.stderr);
        await persist();
        return { ok: false, auditPath, audit };
      }
      const importResult = payload.importResult as Record<string, unknown> | undefined;
      const invalid = validateStepResult(step, payload, store, resolvedOptions);
      if (invalid) {
        item.status = "failed";
        item.durationMs = Date.now() - started;
        item.error = invalid;
        item.stderr = stderrSummary(outcome.stderr);
        await persist();
        return { ok: false, auditPath, audit };
      }
      item.status = "completed";
      item.durationMs = Date.now() - started;
      item.savedPath = typeof payload.savedPath === "string" ? payload.savedPath : typeof payload.downloadSavedPath === "string" ? payload.downloadSavedPath : undefined;
      item.batchId = typeof payload.batchId === "string" ? payload.batchId : typeof importResult?.batchId === "string" ? importResult.batchId : undefined;
      item.rowCount = typeof payload.rowCount === "number" ? payload.rowCount : typeof importResult?.rowCount === "number" ? importResult.rowCount : undefined;
      item.importResult = importResult;
      await persist();
    }
  }
  return { ok: true, auditPath, audit };
}

async function main() {
  const result = await runMultiStore(parseRunnerArgs(process.argv.slice(2)));
  console.log(JSON.stringify({ ok: result.ok, baseUrl: result.audit.baseUrl, storeCount: result.audit.storeKeys.length, stepCounts: auditCounts(result.audit.items), auditPath: result.auditPath }, null, 2));
  if (!result.ok) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  void main().catch((error: unknown) => { console.error(error instanceof Error ? error.stack ?? error.message : String(error)); process.exitCode = 1; });
}
