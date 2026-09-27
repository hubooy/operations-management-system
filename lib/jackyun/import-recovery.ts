import { lstat, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";
import { recoverySha, jackyunWorkflowId, type PreflightEvidence } from "./preflight-recovery";
import { jackyunExportFirstPolicyVersion, jackyunCaptureDate } from "./run-contract";
import { jackyunModuleOrder } from "./post-download";

// Only the independently diagnosed ERP category/column-privilege failure is
// eligible. An arbitrary 503 or uncertain import response is not a retry permit.
export const auditedImport891 = {
  executionId: "891", originalExecutionId: "890", runId: "n8n-export-first-890",
  startedAt: "2026-09-08T01:19:56.734Z", stoppedAt: "2026-09-08T01:21:34.500Z",
  executionDataSha256: "2fd3b6aa4fa2d73000d0d7110c3081cead065a85d064d3ea1780dc1eb9f6629b",
  error: "Django ERP 主数据服务暂时不可用，请稍后重试。",
};
export type ImportRecoveryPermit = {
  version: 1; root: string; createdAt: string; evidence: PreflightEvidence;
  files: Record<string, string>; failedModuleSha256: string; failedAuditSha256: string;
};
export type ImportRecoveryBinding = {
  version: 1; originalExecutionId: string; failedExecutionId: string; executionId: string;
  permitSha256: string; failedModuleSha256: string; failedAuditSha256: string;
};
const names = (root: string) => {
  const base = path.join(root, "outputs/jackyun-export-first"), id = auditedImport891.runId;
  return { base, plan: path.join(base, id + ".json"), active: path.join(base, "active.json"),
    formal: path.join(root, "outputs/jackyun-import-runs", id),
    validation: path.join(root, "outputs/jackyun-export-first-validation", id),
    events: path.join(root, "outputs/jackyun-browser-events", id),
    policy: path.join(root, "config/jackyun-export-first-policy.json"),
    previousClaim: path.join(base, "http-resumptions", id + ".json"),
    permit: path.join(base, "import-resume-permits", "891.json"),
    claim: path.join(base, "import-resumptions", "891.json"),
    archive: path.join(base, "import-resume-originals", "891") };
};
async function bytes(file: string, max = 2 * 1024 * 1024): Promise<Buffer> {
  let current = path.resolve(file);
  while (true) {
    const info = await lstat(current);
    if (info.isSymbolicLink() || (!info.isDirectory() && (!info.isFile() || info.nlink !== 1))) throw Error("导入续跑路径身份异常");
    const parent = path.dirname(current); if (parent === current) break; current = parent;
  }
  const info = await lstat(file);
  if (!info.isFile() || info.size > max) throw Error("导入续跑证据大小或类型异常");
  return readFile(file);
}
async function create(file: string, value: unknown) {
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(value) + "\n", { flag: "wx" });
}
export function assertImportRecoveryEvidence(e: PreflightEvidence, now: string) {
  const a = auditedImport891, node = "D·统一导入运营管理系统";
  if (e.executionId !== a.executionId || e.workflowId !== jackyunWorkflowId || e.status !== "error"
    || e.startedAt !== a.startedAt || e.stoppedAt !== a.stoppedAt || e.executionDataSha256 !== a.executionDataSha256
    || e.activeExecutions !== 0 || e.retrySuccessId !== null || e.lastNode !== node || e.error !== a.error
    || e.httpCode !== "500" || e.requestUrl !== "http://127.0.0.1:5791/jackyun/export-first/import"
    || !isDeepStrictEqual(e.runNodes, ["手动运行", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期",
      "B·网页校验后 HTTP 导出五表", "C·五表完整校验和导入演练", node])
    || !Number.isFinite(Date.parse(now)) || Date.parse(now) < Date.parse(e.stoppedAt)) throw Error("不是已审计的 891 ERP 导入失败");
}
export async function inspectImportRecovery(root: string, evidence: PreflightEvidence, createdAt: string): Promise<ImportRecoveryPermit> {
  assertImportRecoveryEvidence(evidence, createdAt);
  root = path.resolve(root); const p = names(root), files: Record<string, string> = {};
  const read = async (file: string) => { const raw = await bytes(file); files[file] = recoverySha(raw); return JSON.parse(raw.toString()); };
  const plan = await read(p.plan), active = await read(p.active), policy = await read(p.policy);
  const prior = await read(p.previousClaim), controller = await read(path.join(p.formal, "browser-controller-state.json"));
  const formal = await read(path.join(p.formal, "run-manifest.json")), validation = await read(path.join(p.validation, "run-manifest.json"));
  const auditPath = path.join(p.formal, "audit/products.json"), audit = await read(auditPath), failed = formal.modules?.products;
  const keys = [...jackyunModuleOrder].sort();
  if (plan.version !== 1 || plan.executionId !== "890" || plan.runId !== auditedImport891.runId || plan.phase !== "importing"
    || plan.exportTransport !== "web_prepared_http_v1" || plan.exportIntent || plan.completedAt
    || plan.protocol !== jackyunExportFirstPolicyVersion || policy.version !== plan.protocol
    || plan.runDate !== "2026-09-08" || plan.asOfDate !== "2026-09-07" || plan.baseUrl !== "http://localhost:3000"
    || !isDeepStrictEqual(Object.keys(plan.exports ?? {}).sort(), keys)
    || !isDeepStrictEqual(active, { runId: plan.runId, executionId: "890" })
    || prior.version !== 1 || prior.originalExecutionId !== "890" || prior.executionId !== "891"
    || !/^[a-f0-9]{64}$/.test(prior.permitSha256 ?? "")
    || controller.runId !== plan.runId || controller.policyVersion !== plan.protocol || controller.inspectionOnly
    || controller.exportTransport !== plan.exportTransport || !isDeepStrictEqual(Object.keys(controller.modules ?? {}).sort(), keys)
    || formal.version !== 1 || formal.runId !== plan.runId || validation.version !== 1 || validation.runId !== plan.runId
    || !isDeepStrictEqual(formal.strictOrder, jackyunModuleOrder) || !isDeepStrictEqual(validation.strictOrder, jackyunModuleOrder)
    || !isDeepStrictEqual(Object.keys(formal.modules ?? {}), ["products"])
    || !isDeepStrictEqual(Object.keys(validation.modules ?? {}).sort(), keys)
    || failed?.module !== "products" || failed.status !== "failed" || failed.batchId || failed.outputPath
    || audit.version !== 1 || audit.runId !== plan.runId || audit.module !== "products" || audit.status !== "failed" || audit.import
    || audit.error?.stage !== "chunk_upload_and_import" || audit.error?.message !== auditedImport891.error
    || audit.error?.details?.httpStatus !== 503 || audit.error?.details?.status !== "rejected"
    || audit.timings?.failedAt !== "2026-09-08T01:21:34.457Z"
    || failed.inputContractHash !== validation.modules.products.inputContractHash
    || audit.source?.sha256 !== failed.sourceSha256 || failed.sourceSha256 !== plan.exports.products.fileSha256) throw Error("原导入阶段或失败证据变化");
  const downloadRoot = path.resolve(policy.browser.downloadDirectory, "jackyun", plan.runId);
  if (!isDeepStrictEqual((await readdir(p.formal)).sort(), ["audit", "browser-controller-state.json", "processed", "raw", "run-manifest.json"])
    || !isDeepStrictEqual(await readdir(path.join(p.formal, "audit")), ["products.json"])
    || (await readdir(path.join(p.formal, "processed"))).length
    || !isDeepStrictEqual(await readdir(path.join(p.formal, "raw")), ["products-" + path.basename(failed.sourcePath)])) throw Error("原失败运行出现额外导入产物");
  for (const [index, module] of jackyunModuleOrder.entries()) {
    const event = path.join(p.events, `${String(index + 1).padStart(2, "0")}-${module}.json`), handoff = await read(event);
    const state = controller.modules[module], receipt = plan.exports[module], prepared = validation.modules[module];
    if (state.status !== "handed_off" || state.filePath !== handoff.filePath || handoff.runId !== plan.runId || handoff.module !== module
      || path.resolve(path.dirname(handoff.filePath)) !== path.join(downloadRoot, module)
      || files[event] !== receipt.handoffSha256 || prepared.status !== "prepared" || prepared.batchId
      || prepared.sourceSha256 !== receipt.fileSha256 || prepared.sourcePath !== handoff.filePath) throw Error("五表交接或演练记录不一致");
    const raw = await bytes(handoff.filePath, 512 * 1024 * 1024);
    if (raw.length !== receipt.bytes || recoverySha(raw) !== receipt.fileSha256) throw Error("原导出文件变化");
    files[handoff.filePath] = recoverySha(raw);
  }
  // Include all archived/processed files, not only the five top-level receipts.
  let count = 0, total = 0;
  const scan = async (directory: string): Promise<void> => {
    if ((await lstat(directory)).isSymbolicLink()) throw Error("续跑归档目录不能为链接");
    for (const name of (await readdir(directory)).sort()) {
      const file = path.join(directory, name), info = await lstat(file);
      if (++count > 200 || info.isSymbolicLink()) throw Error("续跑归档超限或链接异常");
      if (info.isDirectory()) await scan(file);
      else { const raw = await bytes(file, 512 * 1024 * 1024); total += raw.length;
        if (total > 1024 * 1024 * 1024) throw Error("续跑归档字节超限"); files[file] = recoverySha(raw); }
    }
  };
  for (const directory of [p.formal, p.validation, p.events]) await scan(directory);
  return { version: 1, root, createdAt, evidence, files,
    failedModuleSha256: recoverySha(JSON.stringify(failed)), failedAuditSha256: files[auditPath] };
}
export async function publishImportRecovery(permit: ImportRecoveryPermit, evidence: PreflightEvidence, approvedSha: string) {
  if (approvedSha !== recoverySha(JSON.stringify(permit))
    || !isDeepStrictEqual(await inspectImportRecovery(permit.root, evidence, permit.createdAt), permit)) throw Error("导入续跑计划或文件已变化");
  await create(names(permit.root).permit, permit);
}
export async function claimImportRecovery(root: string, originalId: string, executionId: string, action: string, now: string) {
  if (originalId !== "890" || !/^[1-9]\d{0,19}$/.test(executionId) || BigInt(executionId) <= BigInt(891)) throw Error("导入续跑执行身份无效");
  root = path.resolve(root); const p = names(root), raw = await bytes(p.permit), permit = JSON.parse(raw.toString()) as ImportRecoveryPermit;
  if (permit.version !== 1 || permit.root !== root || !Number.isFinite(Date.parse(now))) throw Error("导入许可身份无效");
  const binding: ImportRecoveryBinding = { version: 1, originalExecutionId: originalId, failedExecutionId: "891", executionId,
    permitSha256: recoverySha(raw), failedModuleSha256: permit.failedModuleSha256, failedAuditSha256: permit.failedAuditSha256 };
  const previous = await bytes(p.claim).catch(error => { if (error.code === "ENOENT") return null; throw error; });
  if (previous) {
    if (!isDeepStrictEqual(JSON.parse(previous.toString()), binding)) throw Error("导入续跑许可已被其他 execution 领取");
    for (const [name, file] of [["plan.json", p.plan], ["manifest.json", path.join(p.formal, "run-manifest.json")],
      ["products.failed.json", path.join(p.formal, "audit/products.json")]]) {
      if (recoverySha(await bytes(path.join(p.archive, name))) !== permit.files[file]) throw Error("原失败归档变化");
    }
    const mutable = new Set([p.plan, path.join(p.formal, "run-manifest.json"), path.join(p.formal, "audit/products.json")]);
    for (const [file, expected] of Object.entries(permit.files)) {
      if (!mutable.has(file) && recoverySha(await bytes(file, 512 * 1024 * 1024)) !== expected) throw Error("续跑领取后原始文件或校验证据变化");
    }
    return binding;
  }
  if (action !== "plan-direct-http" || Date.parse(now) < Date.parse(permit.createdAt) || Date.parse(now) - Date.parse(permit.createdAt) > 30 * 60_000
    || jackyunCaptureDate(now) !== jackyunCaptureDate(permit.createdAt)) throw Error("续跑必须在许可后 30 分钟内从完整 n8n 计划入口开始");
  if (!isDeepStrictEqual(await inspectImportRecovery(root, permit.evidence, permit.createdAt), permit)) throw Error("领取前原导入证据变化");
  // Archive before claiming. A crash partway through fails closed; no old
  // manifest, audit, n8n execution, controller or existing permit is deleted.
  await mkdir(p.archive, { recursive: true });
  for (const [name, file] of [["plan.json", p.plan], ["manifest.json", path.join(p.formal, "run-manifest.json")],
    ["products.failed.json", path.join(p.formal, "audit/products.json")]]) await writeFile(path.join(p.archive, name), await bytes(file), { flag: "wx" });
  await create(p.claim, binding); return binding;
}
export function assertExactFailedImportRetry(input: {
  runId: string; module: string; sourceSha256: string; inputContractHash: string;
  prior: unknown; auditRaw: Buffer; binding: ImportRecoveryBinding;
}) {
  const prior = input.prior as Record<string, unknown>, b = input.binding;
  if (input.runId !== auditedImport891.runId || input.module !== "products" || b.version !== 1 || b.originalExecutionId !== "890" || b.failedExecutionId !== "891"
    || !/^[1-9]\d{0,19}$/.test(b.executionId) || BigInt(b.executionId) <= BigInt(891)
    || !/^[a-f0-9]{64}$/.test(b.permitSha256) || recoverySha(JSON.stringify(prior)) !== b.failedModuleSha256
    || recoverySha(input.auditRaw) !== b.failedAuditSha256 || prior.status !== "failed" || prior.batchId || prior.outputPath
    || prior.sourceSha256 !== input.sourceSha256 || prior.inputContractHash !== input.inputContractHash) throw Error("导入续跑与原失败文件/契约不一致");
}
