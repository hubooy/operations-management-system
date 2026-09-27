// Isolated Chrome rehearsal: bundled real parent/builder, synthetic loopback API.
// This script never connects to the application, production data, or a model.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const root = process.cwd(), directory = path.resolve(process.argv[2] || ".runtime/business-budget-workbench-ui");
await fs.mkdir(directory, { recursive: true });
const entry = path.join(directory, "entry.tsx");
await fs.writeFile(entry, `import React from 'react';import{createRoot}from'react-dom/client';import Workbench from ${JSON.stringify(path.join(root,"app/ai-business-workbench.tsx"))};createRoot(document.getElementById('root')!).render(<Workbench onReportCreated={id=>{document.getElementById('report-result')!.textContent=id;}}/>);`);
await build({ entryPoints: [entry], bundle: true, outfile: path.join(directory,"app.js"), format: "iife", platform: "browser", jsx: "automatic", logLevel: "silent", define: { "process.env.NODE_ENV": '"production"' }, tsconfig: path.join(root,"tsconfig.json") });
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(",")}]` : value && typeof value === "object" ? `{${Object.keys(value).sort().map(key=>`${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}` : JSON.stringify(value);
const sha = value => createHash("sha256").update(canonical(value),"utf8").digest("hex");
const principalA = "a".repeat(64), principalB = "b".repeat(64), catalog = "c".repeat(64);
let principal = principalA, dropReport = false;
const requests = [], reports = [], results = [], pageErrors = [], serverErrors = [], rejectedNetwork = [];
const source = { ordinal:1,key:"ads",domain:"netshop",query:{platform:"京东",shop:"合成预算店铺",dataset:"promotion",window:"current",startDate:"2026-08-01",endDate:"2026-08-01"} };
const run = { id:"budget-evidence",question:"合成首次预算分析",clientRequestId:"fixture",version:3,status:"sealed",collection:{status:"sealed"},createdAt:"2026-09-17T00:00:00Z",storedBytes:1024,sourceCount:1,completedSources:1,rowCount:2,workbenchAnalysisEnabled:true,budgetSupported:true,
  plan:{schemaVersion:"business-evidence-v2",sourceCount:1,catalogDigest:catalog,analysisRequest:{question:"合成首次预算分析"}},sources:{ads:{pageCount:1,rowCount:2,complete:true}} };
const binding = { evidenceRunId:run.id,evidenceVersion:run.version,evidencePlanDigest:"d".repeat(64),catalogDigest:catalog,sealedDigest:"e".repeat(64) };
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
      if(url.pathname==="/api/ai/business-evidence")return send({principalKey:principal,items:principal===principalA?[run]:[],total:principal===principalA?1:0});
      if(url.pathname==="/api/ai/business-reports"){
        assert.equal(req.method,"POST");assert.equal(body.expectedPrincipalKey,principal);assert.equal(principal,principalA);assert.equal(body.evidenceRunId,run.id);
        let report=reports.find(item=>item.clientRequestId===body.clientRequestId);const replayed=Boolean(report);
        if(report)assert.equal(report.raw,raw);else{report={...body,raw,id:`budget-report-${reports.length+1}`};reports.push(report);}
        if(dropReport){dropReport=false;res.writeHead(200,{"content-type":"application/json","content-length":"999"});res.write('{"item":');setTimeout(()=>res.destroy(),25);return;}
        return send({item:{id:report.id},replayed});
      }
      if(principal!==principalA)return send({error:"synthetic owner mismatch"},403);
      const base=`/api/ai/business-evidence/${run.id}`;
      if(url.pathname===base)return send({item:run,reports:[],reportsPagination:{hasMore:false},principalKey:principal});
      if(url.pathname===base+"/sources")return send({schemaVersion:"business-evidence-directory-page-v2",runId:run.id,evidenceVersion:run.version,catalogDigest:catalog,offset:0,total:1,returned:1,nextOffset:null,items:[source]});
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
try{
  await page.goto(origin);
  await check("choose_two_authoritative_rows_and_blank_parameters_never_submit",async()=>{
    await choose();assert.equal(await budgetButton().isDisabled(),true);await targets();
    await builder().getByText("已选择 2 / 100 个目标",{exact:false}).waitFor();
    await builder().getByRole("button",{name:"免费试算预算",exact:true}).click();await builder().getByRole("alert").filter({hasText:"请明确填写总预算"}).waitFor();
    assert.equal(previews().length,0);assert.equal(posts().length,0);assert.equal(await budgetButton().isDisabled(),true);
  });
  await check("explicit_parameters_free_preview_preserve_exact_ids_and_minor_units",async()=>{
    await fill();await calculate();assert.equal(posts().length,0);assert.equal(previews().length,1);
    const plan=previews()[0].body.budgetPlan;assert.equal(plan.totalBudgetCents,10000);assert.equal(plan.reserveCents,1000);assert.equal(plan.scenarios[0].contributionMarginBps,null);
    assert.deepEqual(plan.targets.map(t=>[t.rowIndex,t.rowId]),rows.map(r=>[r.rowIndex,r.id]));
    await page.screenshot({path:path.join(directory,"budget-preview-desktop.png"),fullPage:true});
  });
  await check("parameter_change_invalidates_old_preview_until_recalculated",async()=>{
    await builder().getByLabel("总预算（元）",{exact:true}).fill("120");assert.equal(await builder().getByRole("region",{name:"当前预算试算",exact:true}).count(),0);assert.equal(await budgetButton().isDisabled(),true);assert.equal(posts().length,0);await calculate();assert.equal(previews().at(-1).body.budgetPlan.totalBudgetCents,12000);
  });
  await check("unknown_report_refresh_reuses_exact_frozen_uuid_and_original_body",async()=>{
    const expected=structuredClone(previews().at(-1).body.budgetPlan);dropReport=true;await budgetButton().click();await page.getByTestId("write-error").waitFor();assert.equal(posts().length,1);
    const first=posts()[0];assert.deepEqual(first.body.budgetPlan,expected);assert.equal(first.body.dryRun,false);assert.equal(first.body.expectedPrincipalKey,principalA);assert.match(first.body.clientRequestId,/^workbench-[0-9a-f-]{36}$/i);
    assert.equal(await builder().getByLabel("总预算（元）",{exact:true}).isDisabled(),true);
    await page.reload();await page.getByText("有一次提交等待确认",{exact:true}).waitFor();assert.equal(posts().length,1);
    await page.getByRole("button",{name:"确认并重试同一次提交",exact:true}).click();await page.locator("#report-result").filter({hasText:"budget-report-1"}).waitFor();assert.equal(posts().length,2);assert.equal(posts()[1].raw,first.raw);assert.equal(reports.length,1);
  });
  await check("ordinary_report_button_never_injects_budget_plan",async()=>{
    await choose();await page.getByRole("button",{name:"模拟分析（不调用模型）",exact:true}).click();await page.locator("#report-result").filter({hasText:"budget-report-2"}).waitFor();assert.equal("budgetPlan" in posts().at(-1).body,false);assert.equal(posts().at(-1).body.dryRun,true);
  });
  await check("account_change_hides_prior_pending_and_never_replays_old_owner",async()=>{
    await targets();await fill();await calculate();dropReport=true;await budgetButton().click();await page.getByTestId("write-error").waitFor();const before=posts().length,raw=posts().at(-1).raw;
    principal=principalB;await page.reload();await page.getByText("当前页暂无任务。",{exact:false}).waitFor();assert.equal(await page.getByText("有一次提交等待确认",{exact:true}).count(),0);assert.equal(await builder().count(),0);assert.equal(posts().length,before);
    principal=principalA;await page.reload();await page.getByText("有一次提交等待确认",{exact:true}).waitFor();assert.equal(posts().length,before);await page.getByRole("button",{name:"确认并重试同一次提交",exact:true}).click();await page.locator("#report-result").filter({hasText:"budget-report-3"}).waitFor();assert.equal(posts().at(-1).raw,raw);assert.equal(reports.length,3);
  });
  await check("mobile_complete_budget_form_has_no_document_overflow",async()=>{
    await choose();await targets();await fill();await calculate();await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);await page.screenshot({path:path.join(directory,"budget-preview-mobile.png"),fullPage:true});
  });
  assert.deepEqual(pageErrors,[]);assert.deepEqual(serverErrors,[]);assert.deepEqual(rejectedNetwork,[]);
  await fs.writeFile(path.join(directory,"evidence.json"),JSON.stringify({passed:true,syntheticOnly:true,cases:results,pageErrors,serverErrors,rejectedNetwork,reportPostCount:posts().length,uniqueReports:reports.length,previewCount:previews().length},null,2));
}catch(error){await fs.writeFile(path.join(directory,"failure.json"),JSON.stringify({error:String(error),results,pageErrors,serverErrors,requests,dom:await page.locator("body").innerText()},null,2));throw error;}
finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
