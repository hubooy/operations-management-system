import { lstat, mkdir, readFile, readdir, realpath, writeFile } from "node:fs/promises";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";
import { recoverySha, jackyunWorkflowId, type PreflightEvidence } from "./preflight-recovery";
import { jackyunCaptureDate, jackyunExportFirstPolicyVersion } from "./run-contract";
import { writeJsonAtomic } from "./json-file";

// One audited capture rejection, not a general retry policy for uncertain POSTs.
export const auditedHttp890 = { executionId: "890", startedAt: "2026-09-08T00:52:42.590Z", stoppedAt: "2026-09-08T00:53:39.238Z",
  executionDataSha256: "54932d277a348a9a59c242ff2ef3407ba75849dafd459194baf0cf69bec94f22", exportIntentAt: "2026-09-08T00:53:38.284Z" } as const;
export type HttpRecoveryObservation = { observedAt: string; tasks: { taskId: string; createdAt: number }[] };
export type HttpScopePermit = { version: 1; root: string; createdAt: string; evidence: PreflightEvidence; observation: HttpRecoveryObservation; hashes: Record<string,string> };
const runId = "n8n-export-first-890", transport = "web_prepared_http_v1";
const names = (root:string) => { const base=path.join(root,"outputs/jackyun-export-first"); return {
  plan:path.join(base,runId+".json"), active:path.join(base,"active.json"), controller:path.join(root,"outputs/jackyun-import-runs",runId,"browser-controller-state.json"),
  policy:path.join(root,"config/jackyun-export-first-policy.json"), events:path.join(root,"outputs/jackyun-browser-events",runId),
  permit:path.join(base,"http-resume-permits",runId+".json"), claim:path.join(base,"http-resumptions",runId+".json"), archive:path.join(base,"http-resume-originals",runId+".controller.json") }; };
async function bytes(file:string,max=65536):Promise<Buffer>{
  const absolute=path.resolve(file);let current=absolute;
  while(true){const s=await lstat(current);if(s.isSymbolicLink()||(!s.isDirectory()&&(!s.isFile()||s.nlink!==1)))throw Error("HTTP 恢复路径异常");const parent=path.dirname(current);if(parent===current)break;current=parent;}
  const s=await lstat(absolute);if(!s.isFile()||s.size>max||path.resolve(await realpath(absolute)).toLowerCase()!==absolute.toLowerCase())throw Error("HTTP 恢复证据异常");return readFile(absolute);
}
async function create(file:string,value:unknown){await mkdir(path.dirname(file),{recursive:true});await writeFile(file,JSON.stringify(value)+"\n",{flag:"wx"});}
export function assert890ReprepareWindow(intent:string,tasks:readonly {createdAt:number}[]){
  if(intent!==auditedHttp890.exportIntentAt||!tasks.length||tasks.length>100||tasks.some(t=>!Number.isFinite(t.createdAt)||t.createdAt>=Math.floor(Date.parse(intent)/1000)*1000))throw Error("890 原销售窗口出现任务或证据不足，禁止重新导出");
}
export async function inspectHttpScopeRecovery(root:string,e:PreflightEvidence,o:HttpRecoveryObservation,createdAt:string):Promise<HttpScopePermit>{
  root=path.resolve(root);const a=auditedHttp890,p=names(root),node="B·网页校验后 HTTP 导出五表";
  if(e.executionId!==a.executionId||e.startedAt!==a.startedAt||e.stoppedAt!==a.stoppedAt||e.executionDataSha256!==a.executionDataSha256||e.workflowId!==jackyunWorkflowId
    ||e.status!=="error"||e.activeExecutions!==0||e.retrySuccessId!==null||e.httpCode!=="500"||e.lastNode!==node||e.error!=="HTTP_SALES_SCOPE_CHANGED"
    ||e.requestUrl!=="http://127.0.0.1:5791/jackyun/export-first/export-all"||!isDeepStrictEqual(e.runNodes,["手动运行","领取共享 helper","helper 领取成功？","A·固定采集日和销售日期",node]))throw Error("不是已审计的 890 拦截拒绝");
  assert890ReprepareWindow(a.exportIntentAt,o.tasks);
  const times=[e.stoppedAt,o.observedAt,createdAt].map(Date.parse);
  if(times.some((t,i)=>!Number.isFinite(t)||(i>0&&t<times[i-1]))||times[2]-times[1]>60000||new Set(o.tasks.map(t=>t.taskId)).size!==o.tasks.length||o.tasks.some(t=>!/^sys-\d{1,20}$/.test(t.taskId)))throw Error("任务观察无效");
  const files:Record<string,string>={plan:p.plan,controller:p.controller,active:p.active,policy:p.policy};
  const raw=await Promise.all(Object.values(files).map(f=>bytes(f)));const [plan,c,active,policy]=raw.map(b=>JSON.parse(b.toString()));const s=c.modules?.sales;
  if(plan.executionId!=="890"||plan.runId!==runId||plan.protocol!==jackyunExportFirstPolicyVersion||policy.version!==plan.protocol||plan.exportTransport!==transport||c.exportTransport!==transport||c.inspectionOnly
    ||plan.phase!=="exporting"||plan.exportIntent!=="sales"||!isDeepStrictEqual(Object.keys(plan.exports??{}).sort(),["combos","inventory"])
    ||plan.runDate!=="2026-09-08"||plan.runDate!==jackyunCaptureDate(createdAt)||plan.asOfDate!=="2026-09-07"||plan.baseUrl!=="http://localhost:3000"
    ||!isDeepStrictEqual(active,{runId,executionId:"890"})||c.runId!==runId||c.policyVersion!==plan.protocol||!isDeepStrictEqual(Object.keys(c.modules??{}).sort(),["combos","inventory","sales"])
    ||s?.status!=="export_armed"||s.exportIntentAt!==a.exportIntentAt||s.directPayloadSha256||s.exportTaskBinding||s.filePath||s.webSession?.pendingTaskId||!s.webSession?.baselineIds?.length||s.reprepareEvidence)throw Error("890 原运行状态变化");
  if(!isDeepStrictEqual(await readdir(path.dirname(p.controller)),["browser-controller-state.json"])||!isDeepStrictEqual((await readdir(p.events)).sort(),["02-inventory.json","05-combos.json"]))throw Error("存在额外运行产物");
  const hashes=Object.fromEntries(Object.keys(files).map((key,i)=>[key,recoverySha(raw[i])]));
  const downloadRoot=path.join(policy.browser.downloadDirectory,"jackyun",runId);
  if(!isDeepStrictEqual((await readdir(downloadRoot)).sort(),["combos","inventory"]))throw Error("存在额外下载目录");
  for(const [module,name] of [["inventory","02-inventory.json"],["combos","05-combos.json"]]){
    const hraw=await bytes(path.join(p.events,name)),h=JSON.parse(hraw.toString()),state=c.modules[module],receipt=plan.exports[module];
    if(state.status!=="handed_off"||!state.directPayloadSha256||h.runId!==runId||h.module!==module||h.filePath!==state.filePath||recoverySha(hraw)!==receipt.handoffSha256||path.resolve(path.dirname(h.filePath))!==path.resolve(downloadRoot,module))throw Error("已完成前缀变化");
    const file=await bytes(h.filePath,25*1024*1024);if(file.length!==receipt.bytes||recoverySha(file)!==receipt.fileSha256||!isDeepStrictEqual(await readdir(path.dirname(h.filePath)),[path.basename(h.filePath)]))throw Error("原工作簿变化");
    hashes[module+"Handoff"]=recoverySha(hraw);hashes[module+"File"]=recoverySha(file);
  }
  if(await lstat(path.join(root,"outputs/jackyun-export-first-validation",runId)).then(()=>true,error=>{if(error.code==="ENOENT")return false;throw error;}))throw Error("已进入验证或导入");
  return {version:1,root,createdAt,evidence:e,observation:o,hashes};
}
export async function publishHttpScopeRecovery(permit:HttpScopePermit,e:PreflightEvidence,sha:string){
  if(sha!==recoverySha(JSON.stringify(permit))||!isDeepStrictEqual(await inspectHttpScopeRecovery(permit.root,e,permit.observation,permit.createdAt),permit))throw Error("HTTP 恢复许可或原证据变化");await create(names(permit.root).permit,permit);
}
export async function claimHttpScopeRecovery(root:string,originalId:string,executionId:string,action:string,now:string){
  if(originalId!=="890"||!/^[1-9]\d{0,19}$/.test(executionId)||executionId===originalId||!Number.isFinite(Date.parse(now)))throw Error("HTTP 恢复身份无效");
  root=path.resolve(root);const p=names(root),raw=await bytes(p.permit),permit=JSON.parse(raw.toString()) as HttpScopePermit;
  if(permit.version!==1||permit.root!==root||permit.evidence.executionId!==originalId||jackyunCaptureDate(now)!==jackyunCaptureDate(permit.createdAt))throw Error("HTTP 许可身份或日期变化");
  const binding={version:1,originalExecutionId:originalId,executionId,permitSha256:recoverySha(raw)};
  const existing=await bytes(p.claim).catch(error=>{if(error.code==="ENOENT")return null;throw error;});
  if(existing){if(!isDeepStrictEqual(JSON.parse(existing.toString()),binding))throw Error("HTTP 恢复已由其他执行领取");return binding;}
  if(action!=="plan-direct-http"||Date.parse(now)-Date.parse(permit.createdAt)>30*60000||Date.parse(now)<Date.parse(permit.createdAt))throw Error("HTTP 续跑须从完整 n8n 计划入口开始");
  if(!isDeepStrictEqual(await inspectHttpScopeRecovery(root,permit.evidence,permit.observation,permit.createdAt),permit))throw Error("HTTP 原证据变化");
  const original=await bytes(p.controller),controller=JSON.parse(original.toString());await mkdir(path.dirname(p.archive),{recursive:true});await writeFile(p.archive,original,{flag:"wx"});await create(p.claim,binding);
  controller.modules.sales={status:"pending",reprepareEvidence:{originalIntentAt:auditedHttp890.exportIntentAt,permitSha256:binding.permitSha256,originalControllerSha256:permit.hashes.controller}};
  await writeJsonAtomic(p.controller,controller);return binding;
}
