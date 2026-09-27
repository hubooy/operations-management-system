import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp,mkdir,readFile,writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { auditedHttp890 as a,assert890ReprepareWindow,inspectHttpScopeRecovery,publishHttpScopeRecovery,claimHttpScopeRecovery } from "../lib/jackyun/http-scope-recovery";
import { recoverySha,jackyunWorkflowId,type PreflightEvidence } from "../lib/jackyun/preflight-recovery";
const node="B·网页校验后 HTTP 导出五表",now="2026-09-08T01:10:00.000Z";
const evidence:PreflightEvidence={...a,workflowId:jackyunWorkflowId,status:"error",activeExecutions:0,retrySuccessId:null,lastNode:node,
  runNodes:["手动运行","领取共享 helper","helper 领取成功？","A·固定采集日和销售日期",node],httpCode:"500",requestUrl:"http://127.0.0.1:5791/jackyun/export-first/export-all",error:"HTTP_SALES_SCOPE_CHANGED"};
const observation={observedAt:now,tasks:[{taskId:"sys-112040405",createdAt:Date.parse("2026-09-07T15:11:56Z")}]};
async function fixture(){
  const root=await mkdtemp(path.join(tmpdir(),"http-scope-recovery-")),runId="n8n-export-first-890",prefix=path.join(root,"outputs/jackyun-export-first");
  const stateDir=path.join(root,"outputs/jackyun-import-runs",runId),events=path.join(root,"outputs/jackyun-browser-events",runId),downloads=path.join(root,"downloads");
  for(const d of [prefix,stateDir,events,path.join(root,"config")])await mkdir(d,{recursive:true});
  const protocol="2026-09-06.export-first.1",transport="web_prepared_http_v1";
  const completed:Record<string,unknown>={},exports:Record<string,unknown>={},files:string[]=[];
  for(const [module,name] of [["inventory","02-inventory.json"],["combos","05-combos.json"]]){
    const filePath=path.join(downloads,"jackyun",runId,module,"original.xlsx"),file=Buffer.from(module+" immutable workbook");
    await mkdir(path.dirname(filePath),{recursive:true});await writeFile(filePath,file);files.push(filePath);
    const handoff=JSON.stringify({runId,module,filePath});await writeFile(path.join(events,name),handoff);
    completed[module]={status:"handed_off",filePath,directPayloadSha256:"a".repeat(64)};
    exports[module]={handoffSha256:recoverySha(handoff),fileSha256:recoverySha(file),bytes:file.length};
  }
  const controller={version:1,runId,policyVersion:protocol,exportTransport:transport,modules:{...completed,sales:{status:"export_armed",exportIntentAt:a.exportIntentAt,webSession:{baselineIds:["sys-112040405"]}}}};
  const plan={executionId:"890",runId,protocol,exportTransport:transport,phase:"exporting",exportIntent:"sales",runDate:"2026-09-08",asOfDate:"2026-09-07",baseUrl:"http://localhost:3000",exports};
  const statePath=path.join(stateDir,"browser-controller-state.json"),planPath=path.join(prefix,runId+".json");
  for(const [p,value] of [[statePath,controller],[planPath,plan],[path.join(prefix,"active.json"),{runId,executionId:"890"}],[path.join(root,"config/jackyun-export-first-policy.json"),{version:protocol,browser:{downloadDirectory:downloads}}]] as const)await writeFile(p,JSON.stringify(value));
  return {root,runId,prefix,stateDir,statePath,planPath,controller,plan,files};
}
test("HTTP 890 recovery binds one full n8n execution and preserves both completed files and original controller",async()=>{
  const f=await fixture(),original=await readFile(f.statePath),plan=await readFile(f.planPath),files=await Promise.all(f.files.map(p=>readFile(p)));
  const permit=await inspectHttpScopeRecovery(f.root,evidence,observation,now);await publishHttpScopeRecovery(permit,evidence,recoverySha(JSON.stringify(permit)));
  await assert.rejects(claimHttpScopeRecovery(f.root,"890","891","export-all",now));
  await assert.rejects(claimHttpScopeRecovery(f.root,"890","891","plan-web-session",now));
  const binding=await claimHttpScopeRecovery(f.root,"890","891","plan-direct-http",now);
  assert.deepEqual(await readFile(f.planPath),plan);assert.deepEqual(await Promise.all(f.files.map(p=>readFile(p))),files);
  assert.deepEqual(await readFile(path.join(f.prefix,"http-resume-originals",f.runId+".controller.json")),original);
  const state=JSON.parse(await readFile(f.statePath,"utf8")),before=JSON.parse(original.toString());assert.deepEqual(state.modules.inventory,before.modules.inventory);assert.deepEqual(state.modules.combos,before.modules.combos);
  assert.equal(state.modules.sales.status,"pending");assert.equal(state.modules.sales.reprepareEvidence.permitSha256,binding.permitSha256);
  assert.deepEqual(await claimHttpScopeRecovery(f.root,"890","891","export-all",now),binding);
  await assert.rejects(claimHttpScopeRecovery(f.root,"890","892","plan-direct-http",now));
});
test("HTTP recovery refuses other failures, uncertain POSTs, changed prefixes, extra artifacts and invalid task windows",async()=>{
  for(const fault of ["id","sha","active","error","task","stale","transport","submitted","file","extra","validation","permit"]){
    const f=await fixture(),e={...evidence},o=structuredClone(observation);
    if(fault==="id")e.executionId="891";if(fault==="sha")e.executionDataSha256="0".repeat(64);if(fault==="active")e.activeExecutions=1;if(fault==="error")e.error="HTTP_TIMEOUT";
    if(fault==="task")o.tasks[0].createdAt=Date.parse(a.exportIntentAt);if(fault==="stale")o.observedAt="2026-09-08T01:00:00Z";
    if(fault==="transport")await writeFile(f.planPath,JSON.stringify({...f.plan,exportTransport:"web_session_batch_v1"}));
    if(fault==="submitted")await writeFile(f.statePath,JSON.stringify({...f.controller,modules:{...f.controller.modules,sales:{...f.controller.modules.sales,directPayloadSha256:"a".repeat(64)}}}));
    if(fault==="file")await writeFile(f.files[1],"changed");if(fault==="extra")await writeFile(path.join(f.stateDir,"run-manifest.json"),"{}");
    if(fault==="validation")await mkdir(path.join(f.root,"outputs/jackyun-export-first-validation",f.runId),{recursive:true});
    if(fault==="permit"){const p=await inspectHttpScopeRecovery(f.root,e,o,now);await assert.rejects(publishHttpScopeRecovery(p,e,"0".repeat(64)));}
    else await assert.rejects(inspectHttpScopeRecovery(f.root,e,o,now),fault);
  }
  assert.throws(()=>assert890ReprepareWindow(a.exportIntentAt,[]));assert.throws(()=>assert890ReprepareWindow(now,observation.tasks));
  assert.throws(()=>assert890ReprepareWindow(a.exportIntentAt,[{createdAt:Math.floor(Date.parse(a.exportIntentAt)/1000)*1000}]));
});
test("HTTP permit expires, cannot cross business dates, and detects post-approval evidence changes",async()=>{
  for(const fault of ["expired","cross-day","changed"]){
    const f=await fixture(),p=await inspectHttpScopeRecovery(f.root,evidence,observation,now);await publishHttpScopeRecovery(p,evidence,recoverySha(JSON.stringify(p)));
    if(fault==="changed")await writeFile(f.files[0],"changed after approval");
    await assert.rejects(claimHttpScopeRecovery(f.root,"890","891","plan-direct-http",fault==="expired"?"2026-09-08T02:00:00Z":fault==="cross-day"?"2026-09-08T16:00:00Z":now));
  }
});
