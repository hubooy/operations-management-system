import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp,mkdir,readFile,writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { auditedWebConfirmation849 as a,assert849ReprepareWindow,inspectWebConfirmationRecovery,publishWebConfirmationRecovery,claimWebConfirmationRecovery } from "../lib/jackyun/web-session-recovery";
import { recoverySha,jackyunWorkflowId,type PreflightEvidence } from "../lib/jackyun/preflight-recovery";
const node="B·共用登录态：顺序导出五表",now="2026-09-06T17:10:00.000Z";
const evidence:PreflightEvidence={...a,workflowId:jackyunWorkflowId,status:"error",activeExecutions:0,retrySuccessId:null,lastNode:node,
  runNodes:["手动运行","领取共享 helper","helper 领取成功？","A·固定采集日和销售日期",node],httpCode:"500",requestUrl:"http://127.0.0.1:5791/jackyun/export-first/export-all",
  error:"组合装确认框未关闭；保留原导出意图，禁止再次点击。"};
const observation={observedAt:now,taskIds:["sys-111900787"],newestTaskAt:"2026-09-06T14:31:32.000Z"};
test("a task appearing after approval, absent window, changed intent or row count prevents reprepare",()=>{
  const older=[{createdAt:Date.parse(observation.newestTaskAt)}];assert.doesNotThrow(()=>assert849ReprepareWindow(1942,a.exportIntentAt,older));
  assert.throws(()=>assert849ReprepareWindow(1942,a.exportIntentAt,[...older,{createdAt:Math.floor(Date.parse(a.exportIntentAt)/1000)*1000}]));
  assert.throws(()=>assert849ReprepareWindow(1943,a.exportIntentAt,older));assert.throws(()=>assert849ReprepareWindow(1942,now,older));assert.throws(()=>assert849ReprepareWindow(1942,a.exportIntentAt,[]));
});
async function fixture(){
  const root=await mkdtemp(path.join(tmpdir(),"web-confirm-recovery-")),runId="n8n-export-first-849",prefix=path.join(root,"outputs/jackyun-export-first");
  const stateDir=path.join(root,"outputs/jackyun-import-runs",runId),events=path.join(root,"outputs/jackyun-browser-events",runId),downloads=path.join(root,"downloads");
  const filePath=path.join(downloads,"jackyun",runId,"inventory","original.xlsx"),file=Buffer.from("original immutable workbook");
  for(const d of [prefix,stateDir,events,path.dirname(filePath),path.join(root,"config")])await mkdir(d,{recursive:true});
  await writeFile(filePath,file);
  const handoff={runId,module:"inventory",filePath,expectedSourceRows:25709},handoffRaw=JSON.stringify(handoff),protocol="2026-09-06.export-first.1",transport="web_session_batch_v1";
  const controller={version:1,runId,policyVersion:protocol,exportTransport:transport,modules:{inventory:{status:"handed_off",filePath},combos:{status:"export_armed",exportIntentAt:a.exportIntentAt,expectedSourceRows:1942,webSession:{baselineIds:observation.taskIds}}}};
  const plan={executionId:"849",runId,protocol,exportTransport:transport,phase:"exporting",exportIntent:"combos",runDate:"2026-09-07",asOfDate:"2026-09-06",baseUrl:"http://localhost:3000",exports:{inventory:{handoffSha256:recoverySha(handoffRaw),fileSha256:recoverySha(file),bytes:file.length}}};
  const statePath=path.join(stateDir,"browser-controller-state.json"),planPath=path.join(prefix,runId+".json");
  for(const [p,value] of [[statePath,controller],[planPath,plan],[path.join(prefix,"active.json"),{runId,executionId:"849"}],[path.join(root,"config/jackyun-export-first-policy.json"),{version:protocol,browser:{downloadDirectory:downloads}}],[path.join(events,"02-inventory.json"),handoff]] as const)await writeFile(p,JSON.stringify(value));
  return {root,runId,prefix,stateDir,statePath,planPath,filePath,controller,plan};
}
test("849 explicit permit preserves inventory and original controller, binds one full n8n execution",async()=>{
  const f=await fixture(),originalState=await readFile(f.statePath),originalPlan=await readFile(f.planPath),originalFile=await readFile(f.filePath);
  const permit=await inspectWebConfirmationRecovery(f.root,evidence,observation,now);await publishWebConfirmationRecovery(permit,evidence,recoverySha(JSON.stringify(permit)));
  await assert.rejects(claimWebConfirmationRecovery(f.root,"849","850","export-all",now));
  await assert.rejects(claimWebConfirmationRecovery(f.root,"849","850","plan",now));
  const binding=await claimWebConfirmationRecovery(f.root,"849","850","plan-web-session",now);
  assert.deepEqual(await readFile(f.planPath),originalPlan);assert.deepEqual(await readFile(f.filePath),originalFile);
  assert.deepEqual(await readFile(path.join(f.prefix,"web-resume-originals",f.runId+".controller.json")),originalState);
  const state=JSON.parse(await readFile(f.statePath,"utf8"));assert.deepEqual(state.modules.inventory,f.controller.modules.inventory);assert.equal(state.modules.combos.status,"pending");
  assert.equal(state.modules.combos.reprepareEvidence.permitSha256,binding.permitSha256);
  assert.deepEqual(await claimWebConfirmationRecovery(f.root,"849","850","export-all",now),binding);
  await assert.rejects(claimWebConfirmationRecovery(f.root,"849","851","plan-web-session",now));
});
test("confirmation recovery rejects unknown outcomes, changed files, extra effects and invalid evidence",async()=>{
  for(const fault of ["id","sha","active","task","stale","plan","controller","file","extra","validation","permit"]){
    const f=await fixture(),permit=await inspectWebConfirmationRecovery(f.root,evidence,observation,now);
    const e={...evidence},o={...observation};
    if(fault==="id")e.executionId="850";if(fault==="sha")e.executionDataSha256="0".repeat(64);if(fault==="active")e.activeExecutions=1;
    if(fault==="task")o.newestTaskAt=a.exportIntentAt;if(fault==="stale")o.observedAt="2026-09-06T17:00:00Z";
    if(fault==="plan")await writeFile(f.planPath,JSON.stringify({...f.plan,exportTransport:undefined}));
    if(fault==="controller")await writeFile(f.statePath,JSON.stringify({...f.controller,modules:{...f.controller.modules,combos:{...f.controller.modules.combos,exportConfirmation:{confirmedAt:now}}}}));
    if(fault==="file")await writeFile(f.filePath,"changed");if(fault==="extra")await writeFile(path.join(f.stateDir,"run-manifest.json"),"{}");
    if(fault==="validation")await mkdir(path.join(f.root,"outputs/jackyun-export-first-validation",f.runId),{recursive:true});
    if(fault==="permit")await assert.rejects(publishWebConfirmationRecovery(permit,evidence,"0".repeat(64)));
    else await assert.rejects(inspectWebConfirmationRecovery(f.root,e,o,now));
  }
});
test("web permit expires, fails across dates and detects changes before consumption",async()=>{
  for(const fault of ["expired","cross-day","changed"]){
    const f=await fixture(),permit=await inspectWebConfirmationRecovery(f.root,evidence,observation,now);await publishWebConfirmationRecovery(permit,evidence,recoverySha(JSON.stringify(permit)));
    if(fault==="changed")await writeFile(f.statePath,JSON.stringify({...f.controller,changed:true}));
    await assert.rejects(claimWebConfirmationRecovery(f.root,"849","850","plan-web-session",fault==="expired"?"2026-09-06T18:00:00Z":fault==="cross-day"?"2026-09-07T16:00:00Z":now));
  }
});
