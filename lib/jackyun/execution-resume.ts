import { lstat, mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";
import { recoverySha, jackyunWorkflowId, type PreflightEvidence } from "./preflight-recovery";
import { jackyunCaptureDate, jackyunExportFirstPolicyVersion } from "./run-contract";
import type { JackyunExportTaskBinding } from "./export-task";

export type JackyunResumePermit = { version: 1; root: string; executionId: string; runId: string; createdAt: string;
  planSha256: string; controllerSha256: string; policySha256: string; activeSha256: string;
  evidence: PreflightEvidence; task: JackyunExportTaskBinding };
const paths = (root: string, id: string) => ({
  plan: path.join(root, "outputs/jackyun-export-first", `n8n-export-first-${id}.json`),
  controller: path.join(root, "outputs/jackyun-import-runs", `n8n-export-first-${id}`, "browser-controller-state.json"),
  active: path.join(root, "outputs/jackyun-export-first/active.json"), policy: path.join(root, "config/jackyun-export-first-policy.json"),
  permit: path.join(root, "outputs/jackyun-export-first/resume-permits", `n8n-export-first-${id}.json`),
  consumption: path.join(root, "outputs/jackyun-export-first/resumptions", `n8n-export-first-${id}.json`),
});
async function bytes(file: string) {
  const info = await lstat(file);
  if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size > 65536) throw new Error("续跑证据文件身份异常。");
  return readFile(file);
}
async function create(file: string, value: unknown) {
  await mkdir(path.dirname(file), { recursive: true }); await writeFile(file, JSON.stringify(value) + "\n", { flag: "wx" });
}
export async function inspectJackyunResumePermit(root: string, executionId: string, evidence: PreflightEvidence,
  task: JackyunExportTaskBinding, createdAt: string): Promise<JackyunResumePermit> {
  if (!/^[1-9]\d{0,19}$/.test(executionId)) throw new Error("续跑身份无效。");
  root = path.resolve(root); const p = paths(root, executionId);
  const [planRaw, controllerRaw, policyRaw, activeRaw] = await Promise.all([p.plan, p.controller, p.policy, p.active].map(bytes));
  const plan = JSON.parse(planRaw.toString()), controller = JSON.parse(controllerRaw.toString());
  const active = JSON.parse(activeRaw.toString()), policy = JSON.parse(policyRaw.toString()), inventoryState = controller.modules?.inventory;
  const timeline = [evidence.startedAt, plan.createdAt, inventoryState?.navigationIntentAt, inventoryState?.queryIntentAt,
    inventoryState?.tableStableAt, inventoryState?.exportIntentAt, evidence.stoppedAt, createdAt].map(value => Date.parse(value));
  if (evidence.executionId !== executionId || evidence.workflowId !== jackyunWorkflowId || evidence.status !== "error"
    || evidence.activeExecutions !== 0 || evidence.retrySuccessId !== null || evidence.httpCode !== "500" || evidence.lastNode !== "1·分仓库存：筛选并导出所有页"
    || evidence.requestUrl !== "http://127.0.0.1:5791/jackyun/export-first/export/inventory"
    || !evidence.error.startsWith("FILE_BINDING_FAILED [download_binding]:") || !/^[a-f0-9]{64}$/.test(evidence.executionDataSha256)
    || !isDeepStrictEqual(evidence.runNodes, ["手动运行", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", evidence.lastNode])
    || plan.executionId !== executionId || plan.runId !== `n8n-export-first-${executionId}` || plan.phase !== "exporting"
    || Object.keys(plan.exports ?? {}).length || plan.exportIntent !== "inventory" || plan.protocol !== jackyunExportFirstPolicyVersion
    || timeline.some((time, index) => !Number.isFinite(time) || (index > 0 && time < timeline[index - 1]))
    || policy.version !== plan.protocol || plan.runDate !== jackyunCaptureDate(createdAt) || plan.runDate !== jackyunCaptureDate(plan.createdAt)
    || plan.asOfDate !== new Date(Date.parse(`${plan.runDate}T00:00:00Z`) - 86400000).toISOString().slice(0, 10)
    || plan.baseUrl !== "http://localhost:3000" || !path.isAbsolute(policy.browser?.downloadDirectory ?? "")
    || controller.runId !== plan.runId || controller.policyVersion !== plan.protocol || Object.keys(controller.modules).length !== 1
    || inventoryState?.status !== "export_armed" || inventoryState.filePath || inventoryState.downloadProvenance || inventoryState.exportTaskBinding || !inventoryState.exportIntentAt
    || !isDeepStrictEqual(active, { runId: plan.runId, executionId })
    || task.version !== 1 || task.module !== "inventory" || task.sourceRows !== inventoryState.expectedSourceRows
    || !Number.isSafeInteger(task.sourceRows) || task.sourceRows <= 0
    || !new RegExp(`^【导出任务-(?:明文|密文)】分仓库存查询\\(${task.sourceRows}条\\)$`).test(task.label)
    || !/^sys-\d{1,20}$/.test(task.taskId) || !/^[a-f0-9]{64}$/.test(task.sourceUrlHash)
    || !Number.isFinite(Date.parse(task.createdAt)) || !Number.isFinite(Date.parse(task.observedAt))
    || Date.parse(task.createdAt) < Math.floor(Date.parse(inventoryState.exportIntentAt) / 1000) * 1000
    || Date.parse(task.observedAt) < Date.parse(task.createdAt) || Date.parse(task.observedAt) > Date.parse(createdAt)
    || Date.parse(evidence.stoppedAt) < Date.parse(inventoryState.exportIntentAt) || Date.parse(createdAt) < Date.parse(evidence.stoppedAt)) {
    throw new Error("现有导出任务、失败阶段或原运行身份无法证明可以续跑。");
  }
  if (!isDeepStrictEqual((await readdir(path.dirname(p.controller))).sort(), ["browser-controller-state.json"])) {
    throw new Error("原运行已有额外下载或导入证据，不能使用首次下载续跑许可。");
  }
  for (const target of [path.join(root, "outputs/jackyun-browser-events", plan.runId),
    path.join(root, "outputs/jackyun-export-first-validation", plan.runId),
    path.join(policy.browser.downloadDirectory, "jackyun", plan.runId)]) {
    const existing = await lstat(target).catch(error => { if (error.code === "ENOENT") return null; throw error; });
    if (existing) throw new Error("原运行已有额外下载或导入证据，不能使用首次下载续跑许可。");
  }
  return { version: 1, root, executionId, runId: plan.runId, createdAt, evidence, task,
    planSha256: recoverySha(planRaw), controllerSha256: recoverySha(controllerRaw), policySha256: recoverySha(policyRaw), activeSha256: recoverySha(activeRaw) };
}
export async function publishJackyunResumePermit(permit: JackyunResumePermit, evidence: PreflightEvidence, approvedSha: string) {
  if (approvedSha !== recoverySha(JSON.stringify(permit))) throw new Error("续跑许可摘要不匹配。");
  const fresh = await inspectJackyunResumePermit(permit.root, permit.executionId, evidence, permit.task, permit.createdAt);
  if (!isDeepStrictEqual(fresh, permit)) throw new Error("原续跑证据已变化。");
  await create(paths(permit.root, permit.executionId).permit, permit);
}
export async function claimJackyunResumePermit(root: string, originalId: string, executionId: string, action: string, now: string) {
  if (![originalId, executionId].every(id => /^[1-9]\d{0,19}$/.test(id)) || originalId === executionId || !Number.isFinite(Date.parse(now))) throw new Error("续跑身份无效。");
  const p = paths(root, originalId), raw = await bytes(p.permit), permit = JSON.parse(raw.toString()) as JackyunResumePermit;
  if (permit.root !== path.resolve(root) || permit.executionId !== originalId || permit.runId !== `n8n-export-first-${originalId}`
    || permit.version !== 1 || jackyunCaptureDate(permit.createdAt) !== jackyunCaptureDate(now)) throw new Error("续跑许可已过期或属于其他运行。");
  const binding = { version: 1, originalExecutionId: originalId, executionId, permitSha256: recoverySha(raw) };
  const existing = await bytes(p.consumption).catch(error => { if (error.code === "ENOENT") return null; throw error; });
  if (existing) {
    if (!isDeepStrictEqual(JSON.parse(existing.toString()), binding)) throw new Error("续跑许可已被其他 execution 领取。");
  } else {
    if (action !== "plan" || Date.parse(now) - Date.parse(permit.createdAt) > 30 * 60_000 || Date.parse(now) < Date.parse(permit.createdAt)) {
      throw new Error("续跑必须在许可后 30 分钟内从完整 n8n 计划节点开始。");
    }
    const fresh = await inspectJackyunResumePermit(root, originalId, permit.evidence, permit.task, permit.createdAt);
    if (!isDeepStrictEqual(fresh, permit)) throw new Error("许可之后原始文件已变化。");
    await create(p.consumption, binding);
  }
  return permit.task;
}
