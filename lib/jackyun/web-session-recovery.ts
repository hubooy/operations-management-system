import { lstat, mkdir, readFile, readdir, realpath, writeFile } from "node:fs/promises";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";
import { recoverySha, jackyunWorkflowId, type PreflightEvidence } from "./preflight-recovery";
import { jackyunCaptureDate, jackyunExportFirstPolicyVersion } from "./run-contract";
import { jackyunWebSessionTransport } from "./web-session-export";
import { writeJsonAtomic } from "./json-file";

// Explicit operator exception for the observed MiniUI deferred onclick binding.
// No automatic retry rule is inferred from an absent download or task.
export const auditedWebConfirmation849 = {
  executionId: "849", startedAt: "2026-09-06T16:45:50.975Z", stoppedAt: "2026-09-06T16:47:02.303Z",
  executionDataSha256: "e8f9c46acbae31c6b781f53c33abdbd732d6b4257c60c0948b810f8dd981f0cc",
  exportIntentAt: "2026-09-06T16:46:46.706Z",
} as const;
export type WebRecoveryObservation = { observedAt: string; taskIds: string[]; newestTaskAt: string };
export function assert849ReprepareWindow(rows:number|undefined,originalIntentAt:string,tasks:readonly {createdAt:number}[]){
  if(rows!==1942||originalIntentAt!==auditedWebConfirmation849.exportIntentAt||!tasks.length
    ||tasks.some(task=>!Number.isFinite(task.createdAt)||task.createdAt>=Math.floor(Date.parse(originalIntentAt)/1000)*1000))throw Error("849 原确认窗口出现导出任务或组合装范围变化；禁止重新准备导出。");
}
export type WebRecoveryPermit = { version: 1; root: string; executionId: string; runId: string; createdAt: string;
  evidence: PreflightEvidence; observation: WebRecoveryObservation; hashes: Record<string,string> };
const names = (root:string,id:string) => {
  const runId=`n8n-export-first-${id}`, pipeline=path.join(root,"outputs/jackyun-export-first");
  return {runId,pipeline,plan:path.join(pipeline,runId+".json"),active:path.join(pipeline,"active.json"),
    controller:path.join(root,"outputs/jackyun-import-runs",runId,"browser-controller-state.json"),
    handoff:path.join(root,"outputs/jackyun-browser-events",runId,"02-inventory.json"),
    policy:path.join(root,"config/jackyun-export-first-policy.json"),permit:path.join(pipeline,"web-resume-permits",runId+".json"),
    consumption:path.join(pipeline,"web-resumptions",runId+".json"),archive:path.join(pipeline,"web-resume-originals",runId+".controller.json")};
};
async function bytes(file:string,max=65536):Promise<Buffer> {
  const absolute=path.resolve(file);let current=absolute;
  while(true){const s=await lstat(current);if(s.isSymbolicLink()||(!s.isDirectory()&&(!s.isFile()||s.nlink!==1)))throw Error("恢复路径身份异常。");const parent=path.dirname(current);if(parent===current)break;current=parent;}
  const s=await lstat(absolute);if(!s.isFile()||s.size>max||path.resolve(await realpath(absolute)).toLowerCase()!==absolute.toLowerCase())throw Error("恢复证据文件异常。");
  return readFile(absolute);
}
async function create(file:string,value:unknown){await mkdir(path.dirname(file),{recursive:true});await writeFile(file,JSON.stringify(value)+"\n",{flag:"wx"});}
export async function inspectWebConfirmationRecovery(root:string,e:PreflightEvidence,observation:WebRecoveryObservation,createdAt:string):Promise<WebRecoveryPermit>{
  root=path.resolve(root);const a=auditedWebConfirmation849,p=names(root,e.executionId),node="B·共用登录态：顺序导出五表";
  if(e.executionId!==a.executionId||e.startedAt!==a.startedAt||e.stoppedAt!==a.stoppedAt||e.executionDataSha256!==a.executionDataSha256
    ||e.workflowId!==jackyunWorkflowId||e.status!=="error"||e.activeExecutions!==0||e.retrySuccessId!==null||e.httpCode!=="500"
    ||e.lastNode!==node||e.requestUrl!=="http://127.0.0.1:5791/jackyun/export-first/export-all"
    ||e.error!=="组合装确认框未关闭；保留原导出意图，禁止再次点击。"
    ||!isDeepStrictEqual(e.runNodes,["手动运行","领取共享 helper","helper 领取成功？","A·固定采集日和销售日期",node]))throw Error("不是已审计的 849 确认失败。");
  const times=[e.stoppedAt,observation.observedAt,createdAt].map(Date.parse);
  if(times.some((t,i)=>!Number.isFinite(t)||(i>0&&t<times[i-1]))||times[2]-times[1]>60000
    ||!observation.taskIds.length||new Set(observation.taskIds).size!==observation.taskIds.length||observation.taskIds.length>100
    ||observation.taskIds.some(id=>!/^sys-\d{1,20}$/.test(id))||!Number.isFinite(Date.parse(observation.newestTaskAt))
    ||Date.parse(observation.newestTaskAt)>=Math.floor(Date.parse(a.exportIntentAt)/1000)*1000)throw Error("原组合装任务窗口不是已核验的空缺。");
  const raw=await Promise.all([p.plan,p.controller,p.active,p.policy,p.handoff].map(f=>bytes(f)));
  const [plan,controller,active,policy,handoff]=raw.map(b=>JSON.parse(b.toString()));const c=controller.modules?.combos,i=controller.modules?.inventory;
  if(plan.executionId!==e.executionId||plan.runId!==p.runId||plan.protocol!==jackyunExportFirstPolicyVersion||policy.version!==plan.protocol
    ||plan.exportTransport!==jackyunWebSessionTransport||controller.exportTransport!==jackyunWebSessionTransport||controller.inspectionOnly
    ||plan.phase!=="exporting"||plan.exportIntent!=="combos"||!isDeepStrictEqual(Object.keys(plan.exports??{}),["inventory"])
    ||plan.runDate!=="2026-09-07"||plan.runDate!==jackyunCaptureDate(createdAt)||plan.asOfDate!=="2026-09-06"||plan.baseUrl!=="http://localhost:3000"
    ||!isDeepStrictEqual(active,{runId:p.runId,executionId:e.executionId})||controller.runId!==p.runId||controller.policyVersion!==plan.protocol
    ||!isDeepStrictEqual(Object.keys(controller.modules??{}),["inventory","combos"])||i?.status!=="handed_off"
    ||c?.status!=="export_armed"||c.exportIntentAt!==a.exportIntentAt||c.expectedSourceRows!==1942||c.exportConfirmation||c.exportTaskBinding||c.filePath||c.webSession?.pendingTaskId
    ||!c.webSession?.baselineIds?.length||handoff.runId!==p.runId||handoff.module!=="inventory"||handoff.filePath!==i.filePath
    ||handoff.expectedSourceRows!==25709||recoverySha(raw[4])!==plan.exports.inventory.handoffSha256)throw Error("849 原运行或库存前缀不符。");
  const file=await bytes(handoff.filePath,25*1024*1024);const fileDirectory=path.join(policy.browser.downloadDirectory,"jackyun",p.runId,"inventory");
  if(path.resolve(path.dirname(handoff.filePath))!==path.resolve(fileDirectory)||file.length!==plan.exports.inventory.bytes||recoverySha(file)!==plan.exports.inventory.fileSha256
    ||!isDeepStrictEqual(await readdir(path.dirname(p.controller)),["browser-controller-state.json"])
    ||!isDeepStrictEqual(await readdir(path.dirname(p.handoff)),["02-inventory.json"])
    ||!isDeepStrictEqual(await readdir(path.dirname(fileDirectory)),["inventory"])
    ||!isDeepStrictEqual(await readdir(fileDirectory),[path.basename(handoff.filePath)]))throw Error("已有额外文件、导入或库存文件变化。");
  if(await lstat(path.join(root,"outputs/jackyun-export-first-validation",p.runId)).then(()=>true,error=>{if(error.code==="ENOENT")return false;throw error;}))throw Error("849 已进入验证或导入。");
  return {version:1,root,executionId:e.executionId,runId:p.runId,createdAt,evidence:e,observation,
    hashes:Object.fromEntries(["plan","controller","active","policy","handoff","file"].map((k,index)=>[k,recoverySha([...raw,file][index])]))};
}
export async function publishWebConfirmationRecovery(permit:WebRecoveryPermit,e:PreflightEvidence,approvedSha:string){
  if(approvedSha!==recoverySha(JSON.stringify(permit)))throw Error("恢复许可摘要不符。");
  if(!isDeepStrictEqual(await inspectWebConfirmationRecovery(permit.root,e,permit.observation,permit.createdAt),permit))throw Error("原恢复证据变化。");
  await create(names(permit.root,permit.executionId).permit,permit);
}
export async function claimWebConfirmationRecovery(root:string,originalId:string,executionId:string,action:string,now:string){
  if(originalId!=="849"||!/^[1-9]\d{0,19}$/.test(executionId)||originalId===executionId||!Number.isFinite(Date.parse(now)))throw Error("恢复身份无效。");
  const p=names(path.resolve(root),originalId),raw=await bytes(p.permit),permit=JSON.parse(raw.toString()) as WebRecoveryPermit;
  if(permit.version!==1||permit.root!==path.resolve(root)||permit.executionId!==originalId||permit.runId!==p.runId
    ||jackyunCaptureDate(now)!==jackyunCaptureDate(permit.createdAt))throw Error("恢复许可身份或日期变化。");
  const binding={version:1,originalExecutionId:originalId,executionId,permitSha256:recoverySha(raw)};
  const existing=await bytes(p.consumption).catch(error=>{if(error.code==="ENOENT")return null;throw error;});
  if(existing){if(!isDeepStrictEqual(JSON.parse(existing.toString()),binding))throw Error("恢复已由其他执行领取。");return binding;}
  if(action!=="plan-web-session"||Date.parse(now)-Date.parse(permit.createdAt)>30*60000||Date.parse(now)<Date.parse(permit.createdAt))throw Error("从完整 n8n 入口在许可后 30 分钟内续跑。");
  if(!isDeepStrictEqual(await inspectWebConfirmationRecovery(root,permit.evidence,permit.observation,permit.createdAt),permit))throw Error("原运行证据已变化。");
  const controllerRaw=await bytes(p.controller),controller=JSON.parse(controllerRaw.toString());
  await mkdir(path.dirname(p.archive),{recursive:true});await writeFile(p.archive,controllerRaw,{flag:"wx"});
  await create(p.consumption,binding);
  controller.modules.combos={status:"pending",reprepareEvidence:{originalIntentAt:auditedWebConfirmation849.exportIntentAt,permitSha256:binding.permitSha256,originalControllerSha256:permit.hashes.controller}};
  await writeJsonAtomic(p.controller,controller);
  return binding;
}
