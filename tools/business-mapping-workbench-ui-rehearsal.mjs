// Isolated Chrome rehearsal: bundled real parent/builder, synthetic loopback API.
// This script never connects to the application, production data, or a model.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const root = process.cwd(), directory = path.resolve(process.argv[2] || ".runtime/business-mapping-workbench-ui");
await fs.mkdir(directory, { recursive: true });
const entry = path.join(directory, "entry.tsx");
await fs.writeFile(entry, `import React from 'react';import{createRoot}from'react-dom/client';import Workbench from ${JSON.stringify(path.join(root,"app/ai-business-workbench.tsx"))};createRoot(document.getElementById('root')!).render(<Workbench onReportCreated={id=>{document.getElementById('report-result')!.textContent=id;}}/>);`);
await build({ entryPoints: [entry], bundle: true, outfile: path.join(directory,"app.js"), format: "iife", platform: "browser", jsx: "automatic", logLevel: "silent", define: { "process.env.NODE_ENV": '"production"' }, tsconfig: path.join(root,"tsconfig.json") });
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(",")}]` : value && typeof value === "object" ? `{${Object.keys(value).sort().map(key=>`${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}` : JSON.stringify(value);
const sha = value => createHash("sha256").update(canonical(value),"utf8").digest("hex");
const principalA = "a".repeat(64), principalB = "b".repeat(64);
let principal = principalA, dropReport = false, failDirectory = false, failDetail = false, delayDirectory = false, delayedStarted = false;
const requests = [], reports = [], results = [], pageErrors = [], serverErrors = [], rejectedNetwork = [];
const q={platform:"京东",shop:"合成预算店铺",window:"current",startDate:"2026-08-01",endDate:"2026-08-01"};
const entries=[{key:"ads",domain:"netshop",query:{...q,dataset:"promotion"}},{key:"master",domain:"netshop",query:{...q,dataset:"master"}},{key:"sales",domain:"sales",query:{...q,channel:"精确合成渠道"}}]
  .sort((a,b)=>canonical(a)<canonical(b)?-1:1).map((s,i)=>({...s,ordinal:i+1,queryDigest:sha(s.query)}));
let catalog=sha({schemaVersion:"business-evidence-directory-v2",entries});
const run={id:"budget-evidence",question:"合成首次预算分析",clientRequestId:"fixture",version:3,status:"sealed",collection:{status:"sealed"},createdAt:"2026-09-17T00:00:00Z",storedBytes:1024,sourceCount:3,completedSources:3,rowCount:4,workbenchAnalysisEnabled:true,budgetSupported:true,mappingSupported:true,
 plan:{schemaVersion:"business-evidence-v2",sourceCount:3,catalogDigest:catalog,capacityProfile:"catalog-48-facts-v1",collector:{version:1,surface:"business_collection",pageSize:100},limits:{factBytes:67108864,factPages:2000},analysisRequest:{schemaVersion:"business-analysis-request-v1",question:"合成首次预算分析",requestedDimensions:["sku"],requestedWindows:["current"]}},
 sources:Object.fromEntries(entries.map(s=>[s.key,{pageCount:1,rowCount:2,complete:true}]))};
const binding={evidenceRunId:run.id,evidenceVersion:run.version,evidencePlanDigest:sha(run.plan),catalogDigest:catalog,sealedDigest:"e".repeat(64)};
const rows = [0,1].map(index=>({ id:sha(["synthetic-target",index]),rowIndex:index,entity:{platform:"京东",shop:"合成预算店铺",sku:`SKU-${index+1}`},dimensionMissing:false,
  metrics:Object.fromEntries(Object.entries({spendCents:3000,clicks:300,reportedOrderLines:30,reportedGmvCents:15000}).map(([key,value])=>[key,{value,missingRows:0}])) }));
function preview(plan) {
  const assigned = plan.totalBudgetCents-plan.reserveCents;
  return {schemaVersion:"business-budget-preview-v1",previewOnly:true,evidenceBinding:binding,budget:{schemaVersion:"business-budget-v1",...binding,plan,planDigest:sha(plan),
    allocation:{totalBudgetCents:plan.totalBudgetCents,reservedCents:plan.reserveCents,allocatedCents:assigned,unallocatedCents:0,targetCount:plan.targets.length},
    scenarios:plan.scenarios.map(assumptions=>({assumptions,summary:{projectedAttributedGmvCents:45000,assumedContributionAfterAdCents:0,unavailableTargets:0,mixedReportingBases:false},rows:plan.targets.map(target=>({entity:rows[target.rowIndex].entity,budgetCents:assigned/2,status:"available",reviewAfterSpendCents:1000,ownerRole:target.ownerRole}))})),limitations:["仅合成UI回执；金额预测不是正式模型或经营验收。"]}};
}
const server=http.createServer(async(req,res)=>{
  try {
    const url=new URL(req.url,"http://localhost");
    const send=(value,status=200)=>{res.writeHead(status,{"content-type":"application/json","cache-control":"no-store"});res.end(JSON.stringify(value));};
    if(url.pathname.startsWith("/api/")){
      const chunks=[];for await(const chunk of req)chunks.push(chunk);const raw=Buffer.concat(chunks).toString(),body=raw?JSON.parse(raw):null;
      requests.push({path:url.pathname,query:url.search,method:req.method,raw,body,principal});
      if(url.pathname==="/api/ai/business-evidence")return send({principalKey:principal,items:principal===principalA?[run,{...run,id:"other-evidence",question:"另一合成证据任务"}]:[],total:principal===principalA?2:0});
      if(url.pathname==="/api/ai/business-reports"){
        assert.equal(req.method,"POST");assert.equal(body.expectedPrincipalKey,principal);assert.equal(principal,principalA);assert.equal(body.evidenceRunId,run.id);
        let report=reports.find(item=>item.clientRequestId===body.clientRequestId);const replayed=Boolean(report);
        if(report)assert.equal(report.raw,raw);else{report={...body,raw,id:`budget-report-${reports.length+1}`};reports.push(report);}
        if(dropReport){dropReport=false;res.writeHead(200,{"content-type":"application/json","content-length":"999"});res.write('{"item":');setTimeout(()=>res.destroy(),25);return;}
        return send({item:{id:report.id},replayed});
      }
      if(principal!==principalA)return send({error:"synthetic owner mismatch"},403);
      const currentRun=url.pathname.startsWith("/api/ai/business-evidence/other-evidence")?{...run,id:"other-evidence",plan:{...run.plan,analysisRequest:{...run.plan.analysisRequest,question:"另一合成证据任务"}}}:run;
      const base=`/api/ai/business-evidence/${currentRun.id}`;
      if(url.pathname===base)return failDetail?send({error:"synthetic detail failure"},500):send({item:currentRun,reports:[],reportsPagination:{hasMore:false},principalKey:principal});
      if(url.pathname===base+"/sources"){
        const limit=Number(url.searchParams.get("limit")),offset=Number(url.searchParams.get("offset")),items=entries.slice(offset,offset+limit),end=offset+items.length;
        if(failDirectory&&limit===20)return send({error:"synthetic directory failure"},500);
        const value={schemaVersion:"business-evidence-directory-page-v2",runId:currentRun.id,evidenceVersion:currentRun.version,planDigest:sha(currentRun.plan),catalogDigest:catalog,offset,requestedLimit:limit,total:entries.length,returned:items.length,nextOffset:end<entries.length?end:null,items};
        if(delayDirectory&&limit===20){delayDirectory=false;delayedStarted=true;await new Promise(resolve=>setTimeout(resolve,900));}
        return send({...value,pageDigest:sha(value)});
      }
      if(url.pathname===base+"/budget-targets"){
        assert.equal(req.method,"GET");assert.equal(url.searchParams.get("sourceKey"),"ads");assert.equal(url.searchParams.get("dimension"),"sku");assert.equal(url.searchParams.get("offset"),"0");assert.equal(url.searchParams.has("limit"),false);
        return send({schemaVersion:"business-budget-targets-v1",evidenceBinding:binding,sourceKey:"ads",dimension:"sku",sourceMetadata:{coverage:{status:"dates_present"}},rows,pagination:{offset:0,limit:20,total:2,nextOffset:null,hasMore:false}});
      }
      if(url.pathname===base+"/budget-preview"){
        assert.equal(req.method,"POST");assert.deepEqual(Object.keys(body).sort(),["budgetPlan","evidenceBinding"]);assert.deepEqual(body.evidenceBinding,binding);
        return send(preview(body.budgetPlan));
      }
      throw Error(`Unexpected synthetic API ${req.method} ${url.pathname}`);
    }
    if(["/app.js","/app.css"].includes(url.pathname)){res.setHeader("content-type",url.pathname.endsWith("css")?"text/css":"text/javascript");res.end(await fs.readFile(path.join(directory,url.pathname.slice(1))));return;}
    res.setHeader("content-type","text/html;charset=utf-8");res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"></head><body style="margin:8px;font-family:Arial"><main id="root"></main><div id="report-result"></div><script src="/app.js"></script></body></html>');
  } catch(error){serverErrors.push(String(error));res.statusCode=500;res.end(JSON.stringify({error:String(error)}));}
});
await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
const origin=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",headless:true});
const page=await browser.newPage({viewport:{width:1280,height:1000}});
page.on("pageerror",error=>pageErrors.push(String(error)));
await page.route("**/*",route=>{if(new URL(route.request().url()).origin!==origin){rejectedNetwork.push(route.request().url());return route.abort();}return route.continue();});
const builder=()=>page.getByRole("region",{name:"初次固定预算配置",exact:true});
const budgetButton=()=>builder().getByRole("button",{name:"创建预算正式分析（调用模型）",exact:true});
const posts=()=>requests.filter(r=>r.method==="POST"&&r.path==="/api/ai/business-reports");
const previews=()=>requests.filter(r=>r.method==="POST"&&r.path.endsWith("/budget-preview"));
const choose=async()=>{await page.getByRole("button",{name:/合成首次预算分析/}).click();await builder().getByRole("button",{name:"选择来源 ads",exact:true}).waitFor();};
const targets=async()=>{await builder().getByRole("button",{name:"选择来源 ads",exact:true}).click();await builder().getByLabel("预算聚合维度",{exact:true}).selectOption("sku");for(const n of [1,2])await builder().getByRole("button",{name:`选择目标 ${n}`,exact:true}).click();};
const fill=async()=>{
  const fields={"总预算（元）":"100","预留预算（元）":"10","规划天数（1–93）":"30","观察天数":"7","消耗复核比例（%）":"20","最低点击数":"10","最低订单行数":"1"};
  for(const [label,value]of Object.entries(fields))await builder().getByLabel(label,{exact:true}).fill(value);
  for(const n of [1,2])for(const [label,value]of Object.entries({"分配权重（1–10000）":"1","最低预算（元）":"0","最高预算（元）":"100","负责人角色":`合成负责人${n}`,"最低产出比":"2"}))await builder().getByLabel(`目标${n} ${label}`,{exact:true}).fill(value);
  for(const [label,value]of Object.entries({"情景名称":"合成条件","点击成本系数（10–300%）":"100","订单行率系数（10–300%）":"100","订单行价值系数（10–300%）":"100"}))await builder().getByLabel(`情景1 ${label}`,{exact:true}).fill(value);
};
const calculate=async()=>{await builder().getByRole("button",{name:"免费试算预算",exact:true}).click();await builder().getByRole("region",{name:"当前预算试算",exact:true}).waitFor();assert.equal(await budgetButton().isDisabled(),false);};
const check=async(name,fn)=>{await fn();results.push({name,passed:true});console.log(JSON.stringify(results.at(-1)));};
const ordinary=()=>page.getByRole("button",{name:"模拟分析（不调用模型）",exact:true});
const mapping=()=>page.getByRole("region",{name:"商品关联选择",exact:true});
const selectMapping=async()=>{await mapping().getByLabel("关联主数据 sales",{exact:true}).selectOption("master");await mapping().getByText("已核验完整目录：3 个来源；已明确选择 1 组关联。",{exact:true}).waitFor();};
const reloadMapping=()=>mapping().getByRole("button",{name:"重读关联目录（清空选择）",exact:true}).click();
const refresh=()=>page.getByRole("button",{name:"刷新选中任务",exact:true}).click();
const expectPair=body=>assert.deepEqual(body.mappingPairs,[{salesKey:"sales",masterKey:"master"}]);
try{
  await page.goto(origin);await choose();
  await check("never_selected_keeps_original_report_body",async()=>{
    await ordinary().click();await page.locator("#report-result").filter({hasText:"budget-report-1"}).waitFor();assert.equal("mappingPairs" in posts().at(-1).body,false);assert.equal("budgetPlan" in posts().at(-1).body,false);
  });
  await check("explicit_mapping_on_ordinary_submit",async()=>{
    await selectMapping();await ordinary().click();await page.locator("#report-result").filter({hasText:"budget-report-2"}).waitFor();expectPair(posts().at(-1).body);assert.equal("budgetPlan" in posts().at(-1).body,false);
  });
  await check("combined_fixed_budget_and_mapping_preserve_both",async()=>{
    await targets();await fill();await calculate();await selectMapping();await budgetButton().click();await page.locator("#report-result").filter({hasText:"budget-report-3"}).waitFor();expectPair(posts().at(-1).body);assert.deepEqual(posts().at(-1).body.budgetPlan,previews().at(-1).body.budgetPlan);
  });
  await check("reload_failure_and_success_empty_never_silently_downgrade",async()=>{
    await selectMapping();failDirectory=true;await reloadMapping();await mapping().getByRole("alert").waitFor();assert.equal(await ordinary().isDisabled(),true);assert.equal(await budgetButton().isDisabled(),true);
    failDirectory=false;await reloadMapping();await mapping().getByText("已核验完整目录：3 个来源；已明确选择 0 组关联。",{exact:true}).waitFor();assert.equal(await ordinary().isDisabled(),true);
    await mapping().getByRole("button",{name:"清空关联选择",exact:true}).click();assert.equal(await ordinary().isDisabled(),false);
    await ordinary().click();await page.locator("#report-result").filter({hasText:"budget-report-4"}).waitFor();assert.equal("mappingPairs" in posts().at(-1).body,false);
  });
  await check("last_select_removed_requires_explicit_clear",async()=>{
    await selectMapping();await mapping().getByLabel("关联主数据 sales",{exact:true}).selectOption("");assert.equal(await ordinary().isDisabled(),true);
    await page.getByRole("button",{name:"明确清空原关联意图",exact:true}).click();assert.equal(await ordinary().isDisabled(),false);
  });
  await check("server_mapping_flag_loss_and_detail_error_preserve_intent",async()=>{
    await selectMapping();run.mappingSupported=false;await refresh();await mapping().waitFor({state:"detached"});assert.equal(await ordinary().isDisabled(),true);
    run.mappingSupported=true;await refresh();await mapping().getByLabel("关联主数据 sales",{exact:true}).waitFor();assert.equal(await ordinary().isDisabled(),true);
    await selectMapping();failDetail=true;await refresh();await page.getByRole("alert").filter({hasText:"synthetic detail failure"}).waitFor();assert.equal(await ordinary().isDisabled(),true);
    failDetail=false;await refresh();await page.getByRole("alert").filter({hasText:"synthetic detail failure"}).waitFor({state:"detached"});await mapping().getByLabel("关联主数据 sales",{exact:true}).waitFor();assert.equal(await ordinary().isDisabled(),true);await selectMapping();assert.equal(await ordinary().isDisabled(),false);
  });
  await check("same_task_version_change_requires_reselect",async()=>{
    run.version++;binding.evidenceVersion=run.version;await refresh();await page.getByText(`版本 ${run.version}`,{exact:false}).first().waitFor();await mapping().getByLabel("关联主数据 sales",{exact:true}).waitFor();assert.equal(await ordinary().isDisabled(),true);await selectMapping();assert.equal(await ordinary().isDisabled(),false);
  });
  await check("catalog_change_invalidates_previous_selection",async()=>{
    const sales=entries.find(entry=>entry.key==="sales");sales.query.channel="更新后的精确渠道";sales.queryDigest=sha(sales.query);catalog=sha({schemaVersion:"business-evidence-directory-v2",entries});run.plan.catalogDigest=catalog;binding.catalogDigest=catalog;binding.evidencePlanDigest=sha(run.plan);
    await refresh();await mapping().getByText(/更新后的精确渠道/).waitFor();assert.equal(await ordinary().isDisabled(),true);await selectMapping();assert.equal(await ordinary().isDisabled(),false);
  });
  await check("different_task_drops_late_response_and_does_not_copy_mapping",async()=>{
    delayDirectory=true;delayedStarted=false;await reloadMapping();while(!delayedStarted)await new Promise(resolve=>setTimeout(resolve,10));await page.getByRole("button",{name:/另一合成证据任务/}).click();await page.locator(".bw-question").filter({hasText:"另一合成证据任务"}).waitFor();await mapping().getByLabel("关联主数据 sales",{exact:true}).waitFor();await new Promise(resolve=>setTimeout(resolve,1050));assert.equal(await mapping().getByLabel("关联主数据 sales",{exact:true}).inputValue(),"");assert.equal(await ordinary().isDisabled(),false);
    await choose();await selectMapping();
  });
  await check("unknown_mapped_submit_refresh_keeps_original_uuid_and_body",async()=>{
    dropReport=true;await ordinary().click();await page.getByTestId("write-error").waitFor();const first=posts().at(-1);expectPair(first.body);const before=posts().length;
    await page.reload();await page.getByText("有一次提交等待确认",{exact:true}).waitFor();assert.equal(posts().length,before);await page.getByRole("button",{name:"确认并重试同一次提交",exact:true}).click();await page.locator("#report-result").filter({hasText:"budget-report-5"}).waitFor();assert.equal(posts().at(-1).raw,first.raw);assert.equal(reports.length,5);
  });
  await check("account_switch_drops_late_directory_and_does_not_replay",async()=>{
    await choose();await selectMapping();delayDirectory=true;delayedStarted=false;await reloadMapping();while(!delayedStarted)await new Promise(resolve=>setTimeout(resolve,10));principal=principalB;await page.getByRole("button",{name:"刷新任务列表",exact:true}).click();await page.getByText("当前页暂无任务。",{exact:false}).waitFor();await new Promise(resolve=>setTimeout(resolve,1050));assert.equal(await mapping().count(),0);const before=posts().length;
    principal=principalA;await page.reload();await choose();await mapping().getByLabel("关联主数据 sales",{exact:true}).waitFor();assert.equal(await mapping().getByLabel("关联主数据 sales",{exact:true}).inputValue(),"");assert.equal(await ordinary().isDisabled(),false);assert.equal(posts().length,before);
  });
  await check("report_utf8_capacity_rejection_does_not_poison_storage",async()=>{
    run.plan.analysisRequest.question="中".repeat(22000);await refresh();await page.locator(".bw-question").filter({hasText:"中".repeat(5)}).waitFor();const before=posts().length;await ordinary().click();await page.getByTestId("write-error").filter({hasText:"65536 UTF-8"}).waitFor();assert.equal(posts().length,before);assert.equal(await page.evaluate(()=>Object.keys(sessionStorage).filter(k=>k.startsWith("ai-business-workbench-pending-v1:")).length),0);assert.equal(await ordinary().isDisabled(),false);
    run.plan.analysisRequest.question="\\".repeat(20000);await refresh();await page.locator(".bw-question").filter({hasText:"\\".repeat(5)}).waitFor();await ordinary().click();await page.getByTestId("write-error").filter({hasText:"100000 字符"}).waitFor();assert.equal(posts().length,before);assert.equal(await ordinary().isDisabled(),false);
    run.plan.analysisRequest.question="合成首次预算分析";binding.evidencePlanDigest=sha(run.plan);await refresh();await page.locator(".bw-question").filter({hasText:"合成首次预算分析"}).waitFor();await ordinary().click();await page.locator("#report-result").filter({hasText:"budget-report-6"}).waitFor();assert.equal("mappingPairs" in posts().at(-1).body,false);
  });
  await check("mobile_mapping_form_has_no_document_overflow",async()=>{
    await page.setViewportSize({width:390,height:844});await selectMapping();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);await page.screenshot({path:path.join(directory,"mapping-mobile.png"),fullPage:true});
  });
  assert.deepEqual(pageErrors,[]);assert.deepEqual(serverErrors,[]);assert.deepEqual(rejectedNetwork,[]);
  await fs.writeFile(path.join(directory,"evidence.json"),JSON.stringify({passed:true,syntheticOnly:true,cases:results,pageErrors,serverErrors,rejectedNetwork,reportPostCount:posts().length,uniqueReports:reports.length,previewCount:previews().length},null,2));
}catch(error){await fs.writeFile(path.join(directory,"failure.json"),JSON.stringify({error:String(error),results,pageErrors,serverErrors,requests,dom:await page.locator("body").innerText()},null,2));throw error;}
finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
