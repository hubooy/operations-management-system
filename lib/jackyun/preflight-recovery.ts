import { createHash } from "node:crypto";
import { lstat, mkdir, readdir, readFile, realpath, writeFile } from "node:fs/promises";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";
import { jackyunCaptureDate, jackyunExportFirstPolicyVersion } from "./run-contract";
import { jackyunSalesPeriod } from "./sales-period";
import { classifyJackyunPreflightFailure } from "./preflight-failure";

export const jackyunWorkflowId = "J8kY2mQ5vR7sT4pN";
const failedNode = "1·分仓库存：筛选并导出所有页";
const expectedNodes = ["手动运行", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", failedNode];
const loginFailure = "inventory 导出未完成：login_unknown";
const queryFailure = "TABLE_TIMEOUT [query_refresh]: inventory 未观测到本轮查询触发的包含目标日期 缺失 的模块网络请求完成；拒绝把旧表格当作新结果。";
const legacyMenuFailure = "未找到当前模块唯一的导出所有页菜单。";
// Audited historical exception, not a rule for arbitrary export_armed runs.
// In this exact deployed controller the named error is thrown only after
// menu lookup expires, before entering its final clickText branch. The old
// exportIntent was written too early, before right-click/menu preparation.
const audited843 = {
  executionId: "843", releaseId: "20260906T113045Z-e1a943dd272d5547",
  controllerSourceSha256: "8d64fafc73e815c94b7aa0f56020b2314b8dd3754a829863c5f15eedf4fb7677",
  executionDataSha256: "c72519e3ce9fca4069e28c3c309531744ffd3c1629727ff8ef87327802badb54",
  planSha256: "f7f871b06decd6aecde7becae70434d155d0d4b6a240428920b2b48bf3c36f59",
  controllerSha256: "fdfa4f7d58ba9517222c750aa45c20a8ee09760180358db8b639a7277ca2a56e",
} as const;
// Exact reviewed 897 failure: waitForModuleControls throws while state is
// navigated, before warehouse selection, querying or the final export POST.
const audited897 = {
  releaseId: "20260908T023322Z-d783739f19e43a9d",
  controllerSourceSha256: "35d006f60461f9ce7f8b6fcd4d224f10fd973202a88dd2c5c5bd84e9543dadae",
  planSha256: "4e64b64820dcc43faca1997df86ea363dc50a9bf99eedad7b94efd4d0256aeb7",
  controllerSha256: "17579899de469c2ecf4e57dc1eac895d436434a0f1b51e7a0d20ad3d71c7c28f",
  evidence: {
    executionId: "897", workflowId: jackyunWorkflowId, status: "error",
    startedAt: "2026-09-08T03:51:42.636Z", stoppedAt: "2026-09-08T03:52:12.107Z", retrySuccessId: null,
    lastNode: "B·网页校验后 HTTP 导出五表",
    runNodes: ["手动运行", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", "B·网页校验后 HTTP 导出五表"],
    error: "模块页面控件尚未就绪：branch_stock_main / warehouseCom", httpCode: "500",
    requestUrl: "http://127.0.0.1:5791/jackyun/export-first/export-all",
    executionDataSha256: "87b51149300402e7dfbb642244985b1bc2beb13ff3d16f8c9b7f89d53d99c586", activeExecutions: 0,
  },
} as const;
export const recoverySha = (raw: string | Uint8Array) => createHash("sha256").update(raw).digest("hex");
// Audited 2621: session initialization failed before runApiExports entered its
// execute callback. No beforeModule intent, controller or downloaded file exists.
const audited2621 = {
  planSha256: "aaf9ffe080605d152213aab8e3225fc4b164fbd1d0f92c8ff2a31fa38d694b7c",
  evidence: {
    executionId: "2621", workflowId: jackyunWorkflowId, status: "error",
    startedAt: "2026-09-16T16:10:01.909Z", stoppedAt: "2026-09-16T16:10:12.384Z", retrySuccessId: null,
    lastNode: "B·接口校验与五表下载",
    runNodes: ["每天本机时间 00:10", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", "B·接口校验与五表下载"],
    error: "waiting_login：吉客云 DPAPI 凭据配置或解密未完成（initialize）。", httpCode: "500",
    requestUrl: "http://127.0.0.1:5791/jackyun/export-first/export-all",
    executionDataSha256: "27aac9dd30454319f2158b7b9428549e026762f9701c9a0d98c9b6e71064f24f", activeExecutions: 0,
  },
} as const;
const apiDownloadNode = "B·接口校验与五表下载";
// Exact 3134 failure, before credentials were read and before runApiExports
// entered its callback. This is not a general allowance for DPAPI failures.
const audited3134 = {
  planSha256: "9741967ab88f4e78fec77842132a4d769b17d8ca18b91978873f48c9c3a845db",
  evidence: {
    executionId: "3134", workflowId: jackyunWorkflowId, status: "error",
    startedAt: "2026-09-18T16:10:00.032Z", stoppedAt: "2026-09-18T16:10:09.951Z", retrySuccessId: null,
    lastNode: apiDownloadNode,
    runNodes: ["每天本机时间 00:10", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", apiDownloadNode],
    error: "waiting_login：吉客云 DPAPI 凭据配置或解密未完成（binding）。", httpCode: "500",
    requestUrl: "http://127.0.0.1:5791/jackyun/export-first/export-all",
    executionDataSha256: "5141ae5b5afe83128a086725db70b3cc12fde4d460c5f14305e4662d6b9832f7", activeExecutions: 0,
  },
} as const;
// Exact 4163 failure: the DPAPI binding check stopped before the API export
// callback or any module intent. This is not a general DPAPI retry rule.
const audited4163 = {
  planSha256: "91e7c31381e24432361ba378918babc46f25a9affebea639a61249241ba19505",
  evidence: {
    executionId: "4163", workflowId: jackyunWorkflowId, status: "error",
    startedAt: "2026-09-24T16:10:04.350Z", stoppedAt: "2026-09-24T16:10:19.144Z", retrySuccessId: null,
    lastNode: apiDownloadNode,
    runNodes: ["每天本机时间 00:10", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", apiDownloadNode],
    error: "waiting_login：吉客云 DPAPI 凭据配置或解密未完成（binding）。", httpCode: "500",
    requestUrl: "http://127.0.0.1:5791/jackyun/export-first/export-all",
    executionDataSha256: "b2ee49e4c89878433f0414155e822f26bed593388e76aa4dcdc895cd08ebfdba", activeExecutions: 0,
  },
} as const;
// Exact 4299 midnight failure under the previous helper release. The current
// automatic rule cannot retroactively close it because B already terminated.
const audited4299 = {
  planSha256: "ebb4ea6f90dbdd3c1ae54d36b6e83de7b597c1114e5d3b03a2b8eb299acce564",
  evidence: {
    executionId: "4299", workflowId: jackyunWorkflowId, status: "error",
    startedAt: "2026-09-25T16:10:00.102Z", stoppedAt: "2026-09-25T16:10:11.055Z", retrySuccessId: null,
    lastNode: apiDownloadNode,
    runNodes: ["每天本机时间 00:10", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", apiDownloadNode],
    error: "waiting_login：吉客云 DPAPI 凭据配置或解密未完成（binding）。", httpCode: "500",
    requestUrl: "http://127.0.0.1:5791/jackyun/export-first/export-all",
    executionDataSha256: "e76bb8dea15cb5f42bfd45efa4aafa0c887c8882b08d33aba8694f7dd369e3c5", activeExecutions: 0,
  },
} as const;
// Exact 4726 manual verification: the vault lookup stopped before credential
// read, form filling and the export callback. Do not auto-close missing credentials.
const audited4726 = {
  planSha256: "6138ff2d7a95a5e09ec264592671d76e6ba4462f31da6843eee6384a1f6f3343",
  evidence: {
    executionId: "4726", workflowId: jackyunWorkflowId, status: "error",
    startedAt: "2026-09-27T01:49:15.180Z", stoppedAt: "2026-09-27T01:49:21.611Z", retrySuccessId: null,
    lastNode: apiDownloadNode,
    runNodes: ["手动运行", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", apiDownloadNode],
    error: "waiting_login：吉客云 DPAPI 凭据配置或解密未完成（missing）。", httpCode: "500",
    requestUrl: "http://127.0.0.1:5791/jackyun/export-first/export-all",
    executionDataSha256: "0cf4719a609968a537fe23fa276f449ec32ec46a55d44cee4aafed5c6fbf4100", activeExecutions: 0,
  },
} as const;
const apiLoginChallengeFailure = "waiting_login：吉客云登录已停止（challenge_present）。";
const apiPageSelectionFailure = "API_LOGIN_PAGE_NOT_UNIQUE";
const isApiPageSelectionFailure = (error: string) => error === apiPageSelectionFailure
  || /^API_LOGIN_PAGE_NOT_UNIQUE: (?:no_context|eligible=\d{1,6}; total=\d{1,6}; blank=\d{1,6}; jackyun=\d{1,6}; other=\d{1,6})$/.test(error);
// Exact 4098 failure: page selection throws before login submission and before
// the API export callback. Do not generalize this to arbitrary page errors.
const audited4098 = {
  planSha256: "73271811656a77ef5534388fbf503edfcc536425f11e4dd30d5c45a0227c7714",
  evidence: {
    executionId: "4098", workflowId: jackyunWorkflowId, status: "error",
    startedAt: "2026-09-22T16:10:00.032Z", stoppedAt: "2026-09-22T16:10:04.434Z", retrySuccessId: null,
    lastNode: apiDownloadNode,
    runNodes: ["每天本机时间 00:10", "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", apiDownloadNode],
    error: "API_LOGIN_PAGE_NOT_UNIQUE", httpCode: "500",
    requestUrl: "http://127.0.0.1:5791/jackyun/export-first/export-all",
    executionDataSha256: "b44a28839e5d2ea0741c9d0420bb3d2aefa54ac3d29ea7309501a420a352dafb", activeExecutions: 0,
  },
} as const;
const apiBrowserOccupiedFailure = "waiting_login：专用浏览器端口已占用，自动任务不会接管已打开的浏览器。";
const apiPlanTriggers = new Set(["手动运行", "每天本机时间 00:10", "失败后每小时安全完整重跑", "失败后每小时安全重试入口"]);
export type PreflightEvidence = {
  executionId: string; workflowId: string; status: string; startedAt: string; stoppedAt: string;
  lastNode: string; runNodes: string[]; error: string; httpCode: string; requestUrl: string;
  executionDataSha256: string; activeExecutions: number; retrySuccessId: string | null;
};
type EmptyPlan = { version: number; protocol: string; executionId: string; runId: string; runDate: string;
  asOfDate: string; salesStartDate?: string; baseUrl: string; createdAt: string; phase: string; exports: Record<string, unknown>; exportIntent: string };
export type PreflightClosure = {
  version: 1; status: "closed_before_business" | "closed_before_export"; executionId: string; runId: string; closedAt: string;
  root: string; downloadDirectory: string; policySha256: string; planSha256: string; activeSha256: string;
  evidence: PreflightEvidence; absentPaths: string[];
  reason: "verified_api_preflight_without_business_effects" | "verified_login_failure_without_business_effects" | "verified_api_login_challenge_without_business_effects" | "verified_api_browser_occupied_without_business_effects" | "verified_api_page_selection_without_business_effects" | "verified_query_failure_before_export_intent" | "audited_843_menu_lookup_before_export_click" | "audited_897_controls_before_query_and_export" | "audited_2621_dpapi_before_api_exports" | "audited_3134_dpapi_before_api_exports" | "audited_4163_dpapi_before_api_exports" | "audited_4299_dpapi_before_api_exports" | "audited_4726_dpapi_before_api_exports" | "audited_4098_page_selection_before_api_exports";
  controllerEvidence?: { path: string; sha256: string };
  historicalCodeEvidence?: { releaseId: string; controllerSourceSha256: string };
};
const canonical = (value: unknown): string => JSON.stringify(value);
export function preflightClosurePath(root: string, executionId: string) {
  if (!/^[1-9]\d{0,19}$/.test(executionId)) throw new Error("恢复 execution ID 无效。");
  return path.join(root, "outputs", "jackyun-export-first", "preflight-closures", `n8n-export-first-${executionId}.json`);
}
type AutomatedCredentialClosure = {
  version: 2; status: "closed_before_business"; reason: "verified_transient_dpapi_preflight_without_business_effects";
  executionId: string; runId: string; closedAt: string; planSha256: string; activeSha256: string;
  policySha256: string; absentPaths: string[];
};

type PreflightZeroEffectProof = Omit<AutomatedCredentialClosure, "version" | "reason">;
async function inspectPreflightZeroEffects(root: string, executionId: string, closedAt: string): Promise<PreflightZeroEffectProof> {
  preflightClosurePath(root, executionId);
  const directory = path.join(root, "outputs", "jackyun-export-first");
  const planRaw = await readRegular(path.join(directory, `n8n-export-first-${executionId}.json`));
  const activeRaw = await readRegular(path.join(directory, "active.json"));
  const policyRaw = await readRegular(path.join(root, "config", "jackyun-export-first-policy.json"));
  const plan = parse<EmptyPlan & { exportTransport?: string }>(planRaw);
  const active = parse<{ runId: string; executionId: string }>(activeRaw);
  const policy = parse<{ version: string; browser: { downloadDirectory: string } }>(policyRaw);
  jackyunSalesPeriod(plan.asOfDate, plan.salesStartDate);
  const date = new Date(`${plan.runDate}T00:00:00Z`); date.setUTCDate(date.getUTCDate() - 1);
  if (plan.version !== 2 || plan.protocol !== jackyunExportFirstPolicyVersion || plan.exportTransport !== "session_api_v1"
    || plan.executionId !== executionId || plan.runId !== `n8n-export-first-${executionId}`
    || plan.phase !== "exporting" || typeof plan.salesStartDate !== "string"
    || !plan.exports || typeof plan.exports !== "object" || Array.isArray(plan.exports) || Object.keys(plan.exports).length !== 0
    || Object.hasOwn(plan, "exportIntent")
    || Object.keys(plan).some(key => !["version", "protocol", "executionId", "runId", "runDate", "asOfDate", "salesStartDate", "baseUrl", "createdAt", "phase", "exports", "exportTransport"].includes(key))
    || !isDeepStrictEqual(active, { runId: plan.runId, executionId }) || policy.version !== plan.protocol
    || !path.isAbsolute(policy.browser.downloadDirectory) || plan.baseUrl !== "http://localhost:3000"
    || plan.runDate !== jackyunCaptureDate(plan.createdAt) || plan.asOfDate !== date.toISOString().slice(0, 10)
    || !Number.isFinite(Date.parse(closedAt)) || Date.parse(closedAt) < Date.parse(plan.createdAt)) {
    throw new Error("登录准备自动闭合前的原运行身份或零业务状态不成立。");
  }
  const absentPaths = effectPaths(root, policy.browser.downloadDirectory, plan.runId);
  await assertAbsentEffects(absentPaths);
  return { status: "closed_before_business",
    executionId, runId: plan.runId, closedAt, planSha256: recoverySha(planRaw), activeSha256: recoverySha(activeRaw),
    policySha256: recoverySha(policyRaw), absentPaths };
}

/** Caller holds the Jackyun run lock and caught only a local pre-login binding failure. */
export async function publishAutomatedCredentialClosure(root: string, executionId: string, closedAt: string) {
  const receipt = await inspectAutomatedCredentialClosure(root, executionId, closedAt);
  const target = preflightClosurePath(root, executionId);
  await mkdir(path.dirname(target), { recursive: true });
  await assertEntityPath(path.dirname(target));
  await writeFile(target, `${canonical(receipt)}\n`, { encoding: "utf8", flag: "wx" });
  return { status: receipt.status, receiptSha256: recoverySha(await readRegular(target)) };
}

async function inspectAutomatedCredentialClosure(root: string, executionId: string, closedAt: string): Promise<AutomatedCredentialClosure> {
  const { status, ...proof } = await inspectPreflightZeroEffects(root, executionId, closedAt);
  return { version: 2, status, reason: "verified_transient_dpapi_preflight_without_business_effects", ...proof };
}

/** A session/page failure before any login submission or export callback. */
export async function publishAutomatedPageClosure(root: string, executionId: string, closedAt: string, error: string) {
  if (classifyJackyunPreflightFailure(error) !== "automatic") throw new Error("页面失败不允许自动重试。");
  const proof = await inspectPreflightZeroEffects(root, executionId, closedAt);
  const receipt = { version: 4, reason: "verified_transient_page_preflight_without_business_effects", error, proof };
  const target = preflightClosurePath(root, executionId);
  await mkdir(path.dirname(target), { recursive: true }); await assertEntityPath(path.dirname(target));
  await writeFile(target, `${canonical(receipt)}\n`, { encoding: "utf8", flag: "wx" });
  return { status: proof.status, receiptSha256: recoverySha(await readRegular(target)) };
}
async function assertEntityPath(target: string, allowMissing = false) {
  const absolute = path.resolve(target);
  const parent = path.dirname(absolute);
  if (parent !== absolute) await assertEntityPath(parent);
  try {
    const info = await lstat(absolute);
    if (info.isSymbolicLink() || (!info.isDirectory() && (!info.isFile() || info.nlink !== 1))
      || path.resolve(await realpath(absolute)).toLowerCase() !== absolute.toLowerCase()) throw new Error("恢复路径身份异常。");
    return info;
  } catch (error) {
    if (allowMissing && (error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
}
async function readRegular(target: string) {
  if (!(await assertEntityPath(target))?.isFile()) throw new Error("恢复证据不是普通文件。");
  const raw = await readFile(target);
  if (raw.length > 64 * 1024) throw new Error("恢复证据超过大小限制。");
  return raw;
}
function parse<T>(raw: Uint8Array): T { return JSON.parse(Buffer.from(raw).toString("utf8").replace(/^\uFEFF/, "")) as T; }
function assertEvidence(e: PreflightEvidence, plan: EmptyPlan) {
  if (e.executionId !== plan.executionId || e.workflowId !== jackyunWorkflowId || e.status !== "error"
    || e.retrySuccessId !== null || e.activeExecutions !== 0 || e.lastNode !== failedNode
    || !isDeepStrictEqual(e.runNodes, expectedNodes) || e.httpCode !== "500"
    || (e.error !== loginFailure && e.error !== queryFailure && e.error !== legacyMenuFailure)
    || e.requestUrl !== "http://127.0.0.1:5791/jackyun/export-first/export/inventory"
    || !/^[a-f0-9]{64}$/.test(e.executionDataSha256)
    || !Number.isFinite(Date.parse(e.startedAt)) || !Number.isFinite(Date.parse(e.stoppedAt))
    || Date.parse(e.startedAt) > Date.parse(plan.createdAt) || Date.parse(e.stoppedAt) < Date.parse(plan.createdAt)) {
    throw new Error("仅允许核实的首个库存节点登录或指定查询验证失败，禁止恢复导出点击未决或已进入后续阶段的运行。");
  }
}
async function inspectAudited843(directory: string, planRaw: Uint8Array, evidence: PreflightEvidence) {
  if (evidence.executionId !== audited843.executionId || evidence.executionDataSha256 !== audited843.executionDataSha256
    || evidence.startedAt !== "2026-09-06T11:37:04.545Z" || evidence.stoppedAt !== "2026-09-06T11:37:26.934Z"
    || recoverySha(planRaw) !== audited843.planSha256 || !(await assertEntityPath(directory))?.isDirectory()
    || !isDeepStrictEqual((await readdir(directory)).sort(), ["browser-controller-state.json"])) {
    throw new Error("该运行不属于已审计的 843 菜单查找失败，禁止闭合导出意图。");
  }
  const target = path.join(directory, "browser-controller-state.json"), raw = await readRegular(target);
  if (recoverySha(raw) !== audited843.controllerSha256) throw new Error("843 原控制状态已变化，禁止闭合。");
  return { path: target, sha256: recoverySha(raw) };
}
async function inspectQueryFailure(directory: string, plan: EmptyPlan, evidence: PreflightEvidence) {
  if (!(await assertEntityPath(directory))?.isDirectory()
    || !isDeepStrictEqual((await readdir(directory)).sort(), ["browser-controller-state.json"])) {
    throw new Error("查询失败目录只能保留唯一控制状态，禁止含有导出或导入文件。");
  }
  const target = path.join(directory, "browser-controller-state.json"), raw = await readRegular(target);
  const state = parse<Record<string, unknown>>(raw);
  const exactKeys = (value: unknown, keys: string[]): value is Record<string, unknown> => value !== null
    && typeof value === "object" && !Array.isArray(value)
    && isDeepStrictEqual(Object.keys(value).sort(), [...keys].sort());
  if (!exactKeys(state, ["version", "runId", "policyVersion", "updatedAt", "modules"])
    || state.version !== 1 || state.runId !== plan.runId || state.policyVersion !== plan.protocol
    || !exactKeys(state.modules, ["inventory"])) throw new Error("查询失败控制身份不匹配。");
  const inventory = state.modules.inventory;
  if (!exactKeys(inventory, ["status", "navigationIntentAt", "timings", "fieldChecks", "queryIntentAt", "tableReadbackFailure"])
    || inventory.status !== "queried" || !exactKeys(inventory.timings, ["enterModuleMs"])
    || !Number.isFinite(inventory.timings.enterModuleMs) || Number(inventory.timings.enterModuleMs) < 0
    || !exactKeys(inventory.tableReadbackFailure, ["code", "observedAt"]) || inventory.tableReadbackFailure.code !== "table_timeout"
    || !Array.isArray(inventory.fieldChecks) || inventory.fieldChecks.length !== 1
    || !exactKeys(inventory.fieldChecks[0], ["field", "value", "verifiedAt"])
    || inventory.fieldChecks[0].field !== "仓库" || !/^已勾选:[1-9]\d*条$/.test(String(inventory.fieldChecks[0].value))) {
    throw new Error("控制状态不能证明在导出意图之前停止。");
  }
  const times = [evidence.startedAt, plan.createdAt, inventory.navigationIntentAt, inventory.fieldChecks[0].verifiedAt,
    inventory.queryIntentAt, inventory.tableReadbackFailure.observedAt, state.updatedAt, evidence.stoppedAt].map(value => Date.parse(String(value)));
  if (times.some((time, index) => !Number.isFinite(time) || (index > 0 && time < times[index - 1]))) {
    throw new Error("查询失败时间证据顺序不成立。");
  }
  return { path: target, sha256: recoverySha(raw) };
}
function assertEmptyPlan(plan: EmptyPlan, executionId: string) {
  const date = new Date(`${plan.runDate}T00:00:00Z`); date.setUTCDate(date.getUTCDate() - 1);
  if (plan.version !== 1 || plan.protocol !== jackyunExportFirstPolicyVersion || plan.executionId !== executionId
    || plan.runId !== `n8n-export-first-${executionId}` || plan.phase !== "exporting" || plan.exportIntent !== "inventory"
    || !plan.exports || Array.isArray(plan.exports) || Object.keys(plan.exports).length !== 0
    || Object.keys(plan).some(key => !["version", "protocol", "executionId", "runId", "runDate", "asOfDate", "baseUrl", "createdAt", "phase", "exports", "exportIntent"].includes(key))
    || plan.baseUrl !== "http://localhost:3000" || plan.runDate !== jackyunCaptureDate(plan.createdAt)
    || plan.asOfDate !== date.toISOString().slice(0, 10)) throw new Error("原计划不属于无业务结果的首次登录失败。");
}
function assertEmptyApiChallengePlan(plan: EmptyPlan & { exportTransport?: string }, executionId: string, evidence: PreflightEvidence,
  errorAllowed: (error: string) => boolean = (error) => error === apiLoginChallengeFailure || error === apiBrowserOccupiedFailure) {
  jackyunSalesPeriod(plan.asOfDate, plan.salesStartDate);
  const date = new Date(`${plan.runDate}T00:00:00Z`); date.setUTCDate(date.getUTCDate() - 1);
  const expectedRunNodes = [evidence.runNodes[0], "领取共享 helper", "helper 领取成功？", "A·固定采集日和销售日期", apiDownloadNode];
  if (![1, 2].includes(plan.version) || (plan.version === 2 ? plan.salesStartDate === undefined : plan.salesStartDate !== undefined)
    || plan.protocol !== jackyunExportFirstPolicyVersion || plan.executionId !== executionId
    || plan.runId !== `n8n-export-first-${executionId}` || plan.phase !== "exporting" || plan.exportTransport !== "session_api_v1"
    || !plan.exports || typeof plan.exports !== "object" || Array.isArray(plan.exports) || Object.keys(plan.exports).length !== 0
    || Object.prototype.hasOwnProperty.call(plan, "exportIntent")
    || Object.keys(plan).some(key => !["version", "protocol", "executionId", "runId", "runDate", "asOfDate", "salesStartDate", "baseUrl", "createdAt", "phase", "exports", "exportTransport"].includes(key))
    || plan.baseUrl !== "http://localhost:3000" || plan.runDate !== jackyunCaptureDate(plan.createdAt)
    || plan.asOfDate !== date.toISOString().slice(0, 10)
    || evidence.executionId !== executionId || evidence.workflowId !== jackyunWorkflowId || evidence.status !== "error"
    || evidence.retrySuccessId !== null || evidence.activeExecutions !== 0 || evidence.lastNode !== apiDownloadNode
    || !apiPlanTriggers.has(evidence.runNodes[0] ?? "") || !isDeepStrictEqual(evidence.runNodes, expectedRunNodes)
    || !errorAllowed(evidence.error) || evidence.httpCode !== "500"
    || evidence.requestUrl !== "http://127.0.0.1:5791/jackyun/export-first/export-all"
    || !/^[a-f0-9]{64}$/.test(evidence.executionDataSha256)
    || !Number.isFinite(Date.parse(evidence.startedAt)) || !Number.isFinite(Date.parse(evidence.stoppedAt))
    || Date.parse(evidence.startedAt) > Date.parse(plan.createdAt) || Date.parse(evidence.stoppedAt) < Date.parse(plan.createdAt)) {
    throw new Error("仅允许闭合 API 五表下载前、没有导出意图或业务产物的精确登录安全验证失败。");
  }
}
function effectPaths(root: string, downloadDirectory: string, runId: string) {
  return [path.join(root, "outputs", "jackyun-browser-events", runId),
    path.join(root, "outputs", "jackyun-import-runs", runId),
    path.join(root, "outputs", "jackyun-export-first-validation", runId), path.join(downloadDirectory, "jackyun", runId)];
}
async function assertAbsentEffects(targets: string[]) {
  for (const target of targets) {
    // Existing ancestors must be real entities. Missing ancestors are also
    // evidence of absence, never an excuse to follow a junction elsewhere.
    let ancestor = path.resolve(target);
    while (true) {
      try { await lstat(ancestor); break; } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
        const parent = path.dirname(ancestor); if (parent === ancestor) throw error; ancestor = parent;
      }
    }
    await assertEntityPath(ancestor);
    try { await lstat(target); } catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") continue; throw error; }
    throw new Error("原运行已存在浏览器、下载、演练或导入证据，禁止按无业务操作闭合。");
  }
}
export async function inspectPreflightClosure(root: string, executionId: string, evidence: PreflightEvidence, closedAt: string): Promise<PreflightClosure> {
  preflightClosurePath(root, executionId);
  root = path.resolve(root);
  const directory = path.join(root, "outputs", "jackyun-export-first");
  const planRaw = await readRegular(path.join(directory, `n8n-export-first-${executionId}.json`));
  const activeRaw = await readRegular(path.join(directory, "active.json"));
  const policyRaw = await readRegular(path.join(root, "config", "jackyun-export-first-policy.json"));
  const plan = parse<EmptyPlan>(planRaw), active = parse<{ runId: string; executionId: string }>(activeRaw);
  const policy = parse<{ version: string; browser: { downloadDirectory: string } }>(policyRaw);
  const controlsOnly = executionId === "897";
  const apiLoginAudit = executionId === "2621" ? audited2621 : executionId === "3134" ? audited3134
    : executionId === "4163" ? audited4163 : executionId === "4299" ? audited4299 : executionId === "4726" ? audited4726 : executionId === "4098" ? audited4098 : null;
  const apiChallengeOnly = (plan as EmptyPlan & { exportTransport?: string }).exportTransport === "session_api_v1"
    && (evidence.error === apiLoginChallengeFailure || evidence.error === apiBrowserOccupiedFailure);
  const apiPageSelectionOnly = (plan as EmptyPlan & { exportTransport?: string }).exportTransport === "session_api_v1"
    && isApiPageSelectionFailure(evidence.error);
  const genericPreflight = (plan as EmptyPlan & { exportTransport?: string }).exportTransport === "session_api_v1"
    && classifyJackyunPreflightFailure(evidence.error) !== null;
  if (apiLoginAudit) {
    if (recoverySha(planRaw) !== apiLoginAudit.planSha256 || !isDeepStrictEqual(evidence, apiLoginAudit.evidence)) throw new Error(`${executionId} 原失败运行身份或证据已变化。`);
  } else if (controlsOnly) {
    if (recoverySha(planRaw) !== audited897.planSha256 || !isDeepStrictEqual(evidence, audited897.evidence)) throw new Error("897 原失败运行身份或证据已变化。");
  } else if (apiChallengeOnly) assertEmptyApiChallengePlan(plan, executionId, evidence);
  else if (apiPageSelectionOnly) assertEmptyApiChallengePlan(plan, executionId, evidence, isApiPageSelectionFailure);
  else if (genericPreflight) assertEmptyApiChallengePlan(plan, executionId, evidence, message => classifyJackyunPreflightFailure(message) !== null);
  else { assertEmptyPlan(plan, executionId); assertEvidence(evidence, plan); }
  if (!isDeepStrictEqual(active, { runId: plan.runId, executionId }) || policy.version !== plan.protocol
    || !path.isAbsolute(policy.browser.downloadDirectory) || !Number.isFinite(Date.parse(closedAt))
    || Date.parse(closedAt) < Date.parse(evidence.stoppedAt)) throw new Error("活动运行、策略或恢复时间不一致。");
  const effects = effectPaths(root, policy.browser.downloadDirectory, plan.runId);
  const queryOnly = evidence.error === queryFailure;
  const legacyMenuOnly = evidence.error === legacyMenuFailure;
  let controllerEvidence = queryOnly ? await inspectQueryFailure(effects[1], plan, evidence)
    : legacyMenuOnly ? await inspectAudited843(effects[1], planRaw, evidence) : undefined;
  if (controlsOnly) {
    if (!(await assertEntityPath(effects[1]))?.isDirectory() || !isDeepStrictEqual((await readdir(effects[1])).sort(), ["browser-controller-state.json"])) throw new Error("897 存在额外运行文件，拒绝闭合。");
    const target = path.join(effects[1], "browser-controller-state.json"), raw = await readRegular(target);
    if (recoverySha(raw) !== audited897.controllerSha256) throw new Error("897 原控制状态已变化。");
    controllerEvidence = { path: target, sha256: recoverySha(raw) };
  }
  const absentPaths = queryOnly || legacyMenuOnly || controlsOnly ? effects.filter((_, index) => index !== 1) : effects;
  await assertAbsentEffects(absentPaths);
  if (apiLoginAudit) return { version: 1, status: "closed_before_business", executionId, runId: plan.runId, closedAt, root,
    downloadDirectory: policy.browser.downloadDirectory, policySha256: recoverySha(policyRaw), planSha256: recoverySha(planRaw),
    activeSha256: recoverySha(activeRaw), evidence, absentPaths,
    reason: executionId === "2621" ? "audited_2621_dpapi_before_api_exports" : executionId === "3134" ? "audited_3134_dpapi_before_api_exports"
      : executionId === "4163" ? "audited_4163_dpapi_before_api_exports"
        : executionId === "4299" ? "audited_4299_dpapi_before_api_exports" : executionId === "4726" ? "audited_4726_dpapi_before_api_exports" : "audited_4098_page_selection_before_api_exports" };
  if (controlsOnly) return { version: 1, status: "closed_before_export", executionId, runId: plan.runId, closedAt, root,
    downloadDirectory: policy.browser.downloadDirectory, policySha256: recoverySha(policyRaw), planSha256: recoverySha(planRaw),
    activeSha256: recoverySha(activeRaw), evidence, absentPaths, reason: "audited_897_controls_before_query_and_export", controllerEvidence,
    historicalCodeEvidence: { releaseId: audited897.releaseId, controllerSourceSha256: audited897.controllerSourceSha256 } };
  if (apiChallengeOnly || apiPageSelectionOnly) return { version: 1, status: "closed_before_business", executionId, runId: plan.runId, closedAt, root,
    downloadDirectory: policy.browser.downloadDirectory, policySha256: recoverySha(policyRaw), planSha256: recoverySha(planRaw),
    activeSha256: recoverySha(activeRaw), evidence, absentPaths,
    reason: apiPageSelectionOnly ? "verified_api_page_selection_without_business_effects"
      : evidence.error === apiLoginChallengeFailure ? "verified_api_login_challenge_without_business_effects" : "verified_api_browser_occupied_without_business_effects" };
  if (genericPreflight) return { version: 1, status: "closed_before_business", executionId, runId: plan.runId, closedAt, root,
    downloadDirectory: policy.browser.downloadDirectory, policySha256: recoverySha(policyRaw), planSha256: recoverySha(planRaw),
    activeSha256: recoverySha(activeRaw), evidence, absentPaths, reason: "verified_api_preflight_without_business_effects" };
  if (legacyMenuOnly) return { version: 1, status: "closed_before_export", executionId, runId: plan.runId, closedAt, root,
    downloadDirectory: policy.browser.downloadDirectory, policySha256: recoverySha(policyRaw), planSha256: recoverySha(planRaw),
    activeSha256: recoverySha(activeRaw), evidence, absentPaths, reason: "audited_843_menu_lookup_before_export_click", controllerEvidence,
    historicalCodeEvidence: { releaseId: audited843.releaseId, controllerSourceSha256: audited843.controllerSourceSha256 } };
  if (queryOnly) return { version: 1, status: "closed_before_export", executionId, runId: plan.runId, closedAt, root,
    downloadDirectory: policy.browser.downloadDirectory, policySha256: recoverySha(policyRaw), planSha256: recoverySha(planRaw),
    activeSha256: recoverySha(activeRaw), evidence, absentPaths, reason: "verified_query_failure_before_export_intent", controllerEvidence };
  return { version: 1, status: "closed_before_business", executionId, runId: plan.runId, closedAt, root,
    downloadDirectory: policy.browser.downloadDirectory, policySha256: recoverySha(policyRaw), planSha256: recoverySha(planRaw),
    activeSha256: recoverySha(activeRaw), evidence, absentPaths, reason: "verified_login_failure_without_business_effects" };
}
export async function publishPreflightClosure(root: string, approved: PreflightClosure, freshEvidence: PreflightEvidence, expectedSha256: string) {
  if (recoverySha(canonical(approved)) !== expectedSha256) throw new Error("恢复计划摘要未获精确匹配。");
  const actual = await inspectPreflightClosure(root, approved.executionId, freshEvidence, approved.closedAt);
  if (!isDeepStrictEqual(actual, approved)) throw new Error("恢复证据已变化，拒绝使用旧批准计划。");
  const target = preflightClosurePath(root, approved.executionId);
  await mkdir(path.dirname(target), { recursive: true }); await assertEntityPath(path.dirname(target));
  // Original plan and active pointer stay byte-for-byte intact. Only the next
  // complete n8n execution may advance the active pointer under its run lock.
  await writeFile(target, `${canonical(approved)}\n`, { encoding: "utf8", flag: "wx" });
  return { status: approved.status, runId: approved.runId, closureSha256: recoverySha(await readRegular(target)) };
}
type ReplacementClosure = {
  version: 3; reason: "verified_preflight_for_fresh_execution";
  replacementExecutionId: string; replacementMode: "manual" | "trigger" | "webhook";
  credentialReadinessVerified: boolean; proof: PreflightClosure;
};

/** The run-lock owner has verified the failed and replacement n8n executions. */
export async function publishReplacementPreflightClosure(root: string, proof: PreflightClosure,
  replacement: { executionId: string; mode: ReplacementClosure["replacementMode"] }, credentialReadinessVerified: boolean) {
  const policy = classifyJackyunPreflightFailure(proof.evidence.error);
  if (!policy || (policy === "manual" && replacement.mode !== "manual") || (policy === "credential_ready" && !credentialReadinessVerified)
    || !["manual", "trigger", "webhook"].includes(replacement.mode) || typeof credentialReadinessVerified !== "boolean"
    || !/^[1-9]\d{0,19}$/.test(replacement.executionId) || BigInt(replacement.executionId) <= BigInt(proof.executionId)) throw new Error("旧任务未满足安全恢复条件。");
  const actual = await inspectPreflightClosure(root, proof.executionId, proof.evidence, proof.closedAt);
  if (!isDeepStrictEqual(actual, proof)) throw new Error("旧任务恢复证据已变化。");
  const receipt: ReplacementClosure = { version: 3, reason: "verified_preflight_for_fresh_execution",
    replacementExecutionId: replacement.executionId, replacementMode: replacement.mode, credentialReadinessVerified, proof };
  const target = preflightClosurePath(root, proof.executionId);
  await mkdir(path.dirname(target), { recursive: true }); await assertEntityPath(path.dirname(target));
  await writeFile(target, `${canonical(receipt)}\n`, { encoding: "utf8", flag: "wx" });
  await assertClosedPreflight(root, proof.executionId, replacement.executionId);
  return { status: "closed_before_business", receiptSha256: recoverySha(await readRegular(target)) };
}

export async function assertClosedPreflight(root: string, executionId: string, replacementExecutionId?: string) {
  const raw = await readRegular(preflightClosurePath(root, executionId));
  const pageFailure = parse<{ version: number; reason: string; error: string; proof: PreflightZeroEffectProof }>(raw);
  if (pageFailure.version === 4) {
    if (pageFailure.reason !== "verified_transient_page_preflight_without_business_effects"
      || classifyJackyunPreflightFailure(pageFailure.error) !== "automatic" || pageFailure.proof?.executionId !== executionId
      || !isDeepStrictEqual(Object.keys(pageFailure).sort(), ["version", "reason", "error", "proof"].sort())
      || !isDeepStrictEqual(pageFailure.proof, await inspectPreflightZeroEffects(root, executionId, pageFailure.proof.closedAt))) throw new Error("页面失败闭合证据已变化。");
    return;
  }
  const replacement = parse<ReplacementClosure>(raw);
  if (replacement.version === 3) {
    const policy = classifyJackyunPreflightFailure(replacement.proof?.evidence?.error ?? "");
    if (replacement.reason !== "verified_preflight_for_fresh_execution" || !policy || !replacementExecutionId
      || replacement.replacementExecutionId !== replacementExecutionId || replacement.proof.executionId !== executionId
      || !/^[1-9]\d{0,19}$/.test(replacementExecutionId) || BigInt(replacementExecutionId) <= BigInt(executionId)
      || typeof replacement.credentialReadinessVerified !== "boolean"
      || !["manual", "trigger", "webhook"].includes(replacement.replacementMode)
      || (policy === "manual" && replacement.replacementMode !== "manual")
      || (policy === "credential_ready" && replacement.credentialReadinessVerified !== true)) throw new Error("旧任务闭合只允许绑定的新完整执行消费。");
    const actual = await inspectPreflightClosure(root, executionId, replacement.proof.evidence, replacement.proof.closedAt);
    if (!isDeepStrictEqual(actual, replacement.proof)
      || !isDeepStrictEqual(Object.keys(replacement).sort(), ["version", "reason", "replacementExecutionId", "replacementMode", "credentialReadinessVerified", "proof"].sort())) throw new Error("旧任务闭合证据已变化。");
    return;
  }
  const receipt = parse<PreflightClosure>(raw);
  if ((receipt as { reason?: string }).reason === "verified_transient_dpapi_preflight_without_business_effects") {
    const automated = receipt as unknown as AutomatedCredentialClosure;
    if (!isDeepStrictEqual(automated, await inspectAutomatedCredentialClosure(root, executionId, automated.closedAt))) {
      throw new Error("DPAPI 自动闭合证据已变化。");
    }
    return;
  }
  const loginClosed = receipt.status === "closed_before_business" && receipt.reason === "verified_login_failure_without_business_effects";
  const apiChallengeClosed = receipt.status === "closed_before_business" && receipt.reason === "verified_api_login_challenge_without_business_effects";
  const apiBrowserOccupiedClosed = receipt.status === "closed_before_business" && receipt.reason === "verified_api_browser_occupied_without_business_effects";
  const apiPageSelectionClosed = receipt.status === "closed_before_business" && receipt.reason === "verified_api_page_selection_without_business_effects";
  const apiPreflightClosed = receipt.status === "closed_before_business" && receipt.reason === "verified_api_preflight_without_business_effects";
  const queryClosed = receipt.status === "closed_before_export" && receipt.reason === "verified_query_failure_before_export_intent";
  const menuClosed = receipt.status === "closed_before_export" && receipt.reason === "audited_843_menu_lookup_before_export_click";
  const controlsClosed = receipt.status === "closed_before_export" && receipt.reason === "audited_897_controls_before_query_and_export";
  const apiLoginClosed = receipt.status === "closed_before_business"
    && (receipt.reason === "audited_2621_dpapi_before_api_exports" || receipt.reason === "audited_3134_dpapi_before_api_exports"
      || receipt.reason === "audited_4163_dpapi_before_api_exports" || receipt.reason === "audited_4299_dpapi_before_api_exports" || receipt.reason === "audited_4726_dpapi_before_api_exports"
      || receipt.reason === "audited_4098_page_selection_before_api_exports");
  if (receipt.version !== 1 || receipt.executionId !== executionId || (!loginClosed && !apiChallengeClosed && !apiBrowserOccupiedClosed && !apiPageSelectionClosed && !apiPreflightClosed && !queryClosed && !menuClosed && !controlsClosed && !apiLoginClosed)) {
    throw new Error("原运行未持有有效的导出前失败闭合证据。");
  }
  const actual = await inspectPreflightClosure(root, executionId, receipt.evidence, receipt.closedAt);
  if (!isDeepStrictEqual(actual, receipt)) throw new Error("原运行的闭合证据已变化。");
}
