import { lstat, mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";
import { apiTaskWindowObserved, apiTaskWindowStart } from "./api-clock";
import { jackyunApiTransport } from "./api-plan";
import type { JackyunExportTaskBinding } from "./export-task";
import { jackyunWorkflowId, recoverySha, type PreflightEvidence } from "./preflight-recovery";
import { jackyunCaptureDate, jackyunExportFirstPolicyVersion } from "./run-contract";
import { jackyunSalesPeriod } from "./sales-period";

export type JackyunApiResumePermit = {
  version: 1;
  root: string;
  executionId: string;
  runId: string;
  createdAt: string;
  planSha256: string;
  controllerSha256: string;
  policySha256: string;
  activeSha256: string;
  evidence: PreflightEvidence;
  task: JackyunExportTaskBinding;
};

const paths = (root: string, id: string) => ({
  plan: path.join(root, "outputs/jackyun-export-first", `n8n-export-first-${id}.json`),
  controller: path.join(root, "outputs/jackyun-import-runs", `n8n-export-first-${id}`, "api-controller-state.json"),
  active: path.join(root, "outputs/jackyun-export-first/active.json"),
  policy: path.join(root, "config/jackyun-export-first-policy.json"),
  permit: path.join(root, "outputs/jackyun-export-first/api-resume-permits", `n8n-export-first-${id}.json`),
  consumption: path.join(root, "outputs/jackyun-export-first/api-resumptions", `n8n-export-first-${id}.json`),
});

async function bytes(file: string) {
  const info = await lstat(file);
  if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size > 65536) throw new Error("API 续跑证据文件身份异常。");
  return readFile(file);
}

async function create(file: string, value: unknown) {
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(value) + "\n", { flag: "wx" });
}

export async function inspectJackyunApiResumePermit(root: string, executionId: string, evidence: PreflightEvidence,
  task: JackyunExportTaskBinding, createdAt: string): Promise<JackyunApiResumePermit> {
  if (!/^[1-9]\d{0,19}$/.test(executionId)) throw new Error("API 续跑身份无效。");
  root = path.resolve(root);
  const p = paths(root, executionId);
  const [planRaw, controllerRaw, policyRaw, activeRaw] = await Promise.all([p.plan, p.controller, p.policy, p.active].map(bytes));
  const plan = JSON.parse(planRaw.toString());
  const controller = JSON.parse(controllerRaw.toString());
  const policy = JSON.parse(policyRaw.toString());
  const active = JSON.parse(activeRaw.toString());
  jackyunSalesPeriod(plan.asOfDate, plan.salesStartDate);
  if (![1, 2].includes(plan.version) || (plan.version === 2 ? plan.salesStartDate === undefined : plan.salesStartDate !== undefined)) {
    throw new Error("续跑计划的销售范围版本不一致。");
  }
  const inventory = controller.modules?.inventory;
  const timeline = [evidence.startedAt, plan.createdAt, inventory?.preflightStartedAt, inventory?.queryIntentAt,
    inventory?.queryCompletedAt, inventory?.serverClock?.requestStartedAt, inventory?.serverClock?.receivedAt,
    inventory?.exportIntentAt, evidence.stoppedAt, createdAt].map(value => Date.parse(value));
  const windowStart = apiTaskWindowStart(inventory?.serverClock, inventory?.exportIntentAt);
  const observedByCreatedAt = apiTaskWindowObserved(inventory?.serverClock, createdAt);
  const taskAlreadyBound = inventory?.pendingTaskId === task.taskId && isDeepStrictEqual(inventory?.binding, task);
  const taskNotYetBound = inventory?.pendingTaskId === undefined && inventory?.binding === undefined;
  const allowedTriggers = new Set(["手动运行", "每天本机时间 00:10", "失败后每小时安全重试入口"]);
  const expectedTail = ["领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", "B·接口校验与五表下载"];
  if (evidence.executionId !== executionId || evidence.workflowId !== jackyunWorkflowId || evidence.status !== "error"
    || evidence.activeExecutions !== 0 || evidence.retrySuccessId !== null || evidence.httpCode !== "500"
    || evidence.lastNode !== "B·接口校验与五表下载" || evidence.requestUrl !== "http://127.0.0.1:5791/jackyun/export-first/export-all"
    || evidence.error !== (taskAlreadyBound ? "fetch failed" : "导出任务绑定条件无效。") || !/^[a-f0-9]{64}$/.test(evidence.executionDataSha256)
    || evidence.runNodes.length !== 5 || !allowedTriggers.has(evidence.runNodes[0])
    || !isDeepStrictEqual(evidence.runNodes.slice(1), expectedTail)
    || plan.executionId !== executionId || plan.runId !== `n8n-export-first-${executionId}` || plan.phase !== "exporting"
    || plan.exportTransport !== jackyunApiTransport || Object.keys(plan.exports ?? {}).length || plan.exportIntent !== "inventory"
    || plan.protocol !== jackyunExportFirstPolicyVersion || policy.version !== plan.protocol
    || plan.runDate !== jackyunCaptureDate(createdAt) || plan.runDate !== jackyunCaptureDate(plan.createdAt)
    || plan.asOfDate !== new Date(Date.parse(`${plan.runDate}T00:00:00Z`) - 86400000).toISOString().slice(0, 10)
    || plan.baseUrl !== "http://localhost:3000" || !path.isAbsolute(policy.browser?.downloadDirectory ?? "")
    || !Array.isArray(policy.browser?.allowedDownloadHosts) || !policy.browser.allowedDownloadHosts.length
    || timeline.some((time, index) => !Number.isFinite(time) || index > 0 && time < timeline[index - 1])
    || controller.version !== 1 || controller.runId !== plan.runId || controller.transport !== jackyunApiTransport
    || controller.runDate !== plan.runDate || controller.asOfDate !== plan.asOfDate || controller.salesStartDate !== plan.salesStartDate || !/^[a-f0-9]{64}$/.test(controller.templateSha256)
    || Object.keys(controller.modules ?? {}).length !== 1 || !controller.tenantId
    || inventory?.status !== "submitted" || (!taskNotYetBound && !taskAlreadyBound) || inventory.filePath
    || inventory.provenance || inventory.handoffSha256 || !Number.isSafeInteger(inventory.sourceRows) || inventory.sourceRows <= 0
    || ![inventory.payloadSha256, inventory.querySha256, inventory.permissionSha256, inventory.templateSha256].every(
      (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value))
    || inventory.templateSha256 !== controller.templateSha256 || !Array.isArray(inventory.baselineIds)
    || new Set(inventory.baselineIds).size !== inventory.baselineIds.length
    || !inventory.baselineIds.every((value: unknown) => typeof value === "string" && /^sys-\d{1,20}$/.test(value))
    || !isDeepStrictEqual(active, { runId: plan.runId, executionId })
    || task.version !== 1 || task.module !== "inventory" || task.sourceRows !== inventory.sourceRows
    || inventory.baselineIds.includes(task.taskId) || !/^sys-\d{1,20}$/.test(task.taskId)
    || !new RegExp(`^【导出任务-(?:明文|密文)】分仓库存查询\\(${task.sourceRows}条\\)$`).test(task.label)
    || !/^[a-f0-9]{64}$/.test(task.sourceUrlHash) || !Number.isFinite(Date.parse(task.createdAt))
    || !Number.isFinite(Date.parse(task.observedAt)) || Date.parse(task.createdAt) < Date.parse(windowStart)
    || Date.parse(task.observedAt) < Date.parse(task.createdAt) || Date.parse(task.observedAt) > Date.parse(observedByCreatedAt)) {
    throw new Error("API 原任务、失败阶段或时间证据无法证明可以续跑。");
  }
  if (!isDeepStrictEqual((await readdir(path.dirname(p.controller))).sort(), ["api-controller-state.json"])) {
    throw new Error("API 原运行已有额外下载或导入证据，不能使用续跑许可。");
  }
  for (const target of [path.join(root, "outputs/jackyun-browser-events", plan.runId),
    path.join(root, "outputs/jackyun-export-first-validation", plan.runId)]) {
    const existing = await lstat(target).catch(error => { if (error.code === "ENOENT") return null; throw error; });
    if (existing) throw new Error("API 原运行已有额外下载或导入证据，不能使用续跑许可。");
  }
  const downloadRun = path.join(policy.browser.downloadDirectory, "jackyun", plan.runId);
  const existingDownload = await lstat(downloadRun).catch(error => { if (error.code === "ENOENT") return null; throw error; });
  if (taskAlreadyBound) {
    const inventoryDirectory = path.join(downloadRun, "inventory");
    const inventoryInfo = existingDownload?.isDirectory() && !existingDownload.isSymbolicLink()
      ? await lstat(inventoryDirectory).catch(() => null) : null;
    if (!inventoryInfo?.isDirectory() || inventoryInfo.isSymbolicLink()
      || !isDeepStrictEqual((await readdir(downloadRun)).sort(), ["inventory"])
      || (await readdir(inventoryDirectory)).length !== 0) {
      throw new Error("API 原运行的下载目录不能证明绑定任务在文件落地前停止。");
    }
  } else if (existingDownload) throw new Error("API 原运行已有额外下载或导入证据，不能使用续跑许可。");
  return { version: 1, root, executionId, runId: plan.runId, createdAt, evidence, task,
    planSha256: recoverySha(planRaw), controllerSha256: recoverySha(controllerRaw),
    policySha256: recoverySha(policyRaw), activeSha256: recoverySha(activeRaw) };
}

export async function publishJackyunApiResumePermit(permit: JackyunApiResumePermit, evidence: PreflightEvidence, approvedSha: string) {
  if (approvedSha !== recoverySha(JSON.stringify(permit))) throw new Error("API 续跑许可摘要不匹配。");
  const fresh = await inspectJackyunApiResumePermit(permit.root, permit.executionId, evidence, permit.task, permit.createdAt);
  if (!isDeepStrictEqual(fresh, permit)) throw new Error("API 原续跑证据已变化。");
  await create(paths(permit.root, permit.executionId).permit, permit);
}

export async function claimJackyunApiResumePermit(root: string, originalId: string, executionId: string, action: string, now: string) {
  if (![originalId, executionId].every(id => /^[1-9]\d{0,19}$/.test(id)) || originalId === executionId || !Number.isFinite(Date.parse(now))) {
    throw new Error("API 续跑身份无效。");
  }
  const p = paths(root, originalId);
  const raw = await bytes(p.permit);
  const permit = JSON.parse(raw.toString()) as JackyunApiResumePermit;
  if (permit.root !== path.resolve(root) || permit.executionId !== originalId || permit.runId !== `n8n-export-first-${originalId}`
    || permit.version !== 1 || jackyunCaptureDate(permit.createdAt) !== jackyunCaptureDate(now)) {
    throw new Error("API 续跑许可已过期或属于其他运行。");
  }
  const binding = { version: 1, originalExecutionId: originalId, executionId, permitSha256: recoverySha(raw) };
  const existing = await bytes(p.consumption).catch(error => { if (error.code === "ENOENT") return null; throw error; });
  if (existing) {
    if (!isDeepStrictEqual(JSON.parse(existing.toString()), binding)) throw new Error("API 续跑许可已被其他 execution 领取。");
  } else {
    if (action !== "plan-api" || Date.parse(now) - Date.parse(permit.createdAt) > 30 * 60_000 || Date.parse(now) < Date.parse(permit.createdAt)) {
      throw new Error("API 续跑必须在许可后 30 分钟内从完整 n8n 计划节点开始。");
    }
    const fresh = await inspectJackyunApiResumePermit(root, originalId, permit.evidence, permit.task, permit.createdAt);
    if (!isDeepStrictEqual(fresh, permit)) throw new Error("API 许可之后原始文件已变化。");
    await create(p.consumption, binding);
  }
  return permit.task;
}
