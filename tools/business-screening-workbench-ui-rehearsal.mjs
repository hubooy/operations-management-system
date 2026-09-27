// Isolated Chrome rehearsal: bundled real parent/builder, synthetic loopback API.
// This script never connects to the application, production data, or a model.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const root = process.cwd(), directory = path.resolve(process.argv[2] || ".runtime/business-screening-workbench-ui");
await fs.mkdir(directory, { recursive: true });
const entry = path.join(directory, "entry.tsx");
await fs.writeFile(entry, `import React from 'react';import{createRoot}from'react-dom/client';import Workbench from ${JSON.stringify(path.join(root,"app/ai-report-workbench-view.tsx"))};createRoot(document.getElementById('root')!).render(<Workbench kind="pipelines"/>);`);
await build({ entryPoints: [entry], bundle: true, outfile: path.join(directory,"app.js"), format: "iife", platform: "browser", jsx: "automatic", logLevel: "silent", define: { "process.env.NODE_ENV": '"production"' }, tsconfig: path.join(root,"tsconfig.json") });
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(",")}]` : value && typeof value === "object" ? `{${Object.keys(value).sort().map(key=>`${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}` : JSON.stringify(value);
const sha = value => createHash("sha256").update(canonical(value),"utf8").digest("hex");
const principalA = "a".repeat(64), principalB = "b".repeat(64);
let principal = principalA, dropReport = false, failDirectory = false, failDetail = false, delayDirectory = false;
const requests = [], reports = [], results = [], pageErrors = [], serverErrors = [], rejectedNetwork = [];
const q={platform:"京东",shop:"合成预算店铺",window:"current",startDate:"2026-08-01",endDate:"2026-08-01"};
const entries=[{key:"ads",domain:"netshop",query:{...q,dataset:"promotion"}},{key:"master",domain:"netshop",query:{...q,dataset:"master"}},{key:"sales",domain:"sales",query:{...q,channel:"精确合成渠道"}}]
  .sort((a,b)=>canonical(a)<canonical(b)?-1:1).map((s,i)=>({...s,ordinal:i+1,queryDigest:sha(s.query)}));
let catalog=sha({schemaVersion:"business-evidence-directory-v2",entries});
const run={id:"budget-evidence",question:"合成首次预算分析",clientRequestId:"fixture",version:3,status:"sealed",collection:{status:"sealed"},createdAt:"2026-09-17T00:00:00Z",storedBytes:1024,sourceCount:3,completedSources:3,rowCount:4,workbenchAnalysisEnabled:true,budgetSupported:true,mappingSupported:true,screeningSupported:true,
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
let preparationStatus="queued_scan", showContent=false, actualDetail=null;
function content(reportId) {
 const period={startDate:"2026-08-01",endDate:"2026-08-02"},roles=["commerce","promotion","market_b2b","independent_review","report"];
 const tableBindings=Array.from({length:21},(_,i)=>({tableKey:`table${i}`,sourceKey:"ads",mode:"native",dimension:"sku"}));
 const coverage=tableBindings.flatMap((t,i)=>[{kind:"table",value:{tableKey:t.tableKey,scannedRows:2,expectedRows:2,sourceCoverage:{status:"missing_dates",presentDates:["2026-08-01"],missingDates:["2026-08-02"]},baselineCoverage:null,sourcePeriod:period,dateCoverageComparable:false}},{kind:"partition",value:{tableKey:t.tableKey,ruleId:`rule${i}`,scannedRows:2,matchedRows:2,retainedRows:1,omittedRows:1,eligibleRows:2,ineligibleRows:0,ineligibleReasons:{},supported:true,unavailableReason:null}}]);
 return {screening:{schemaVersion:"business-screening-content-v1",reference:{id:"scan",reportId},authority:{entityDailyCoverageVerified:false,tableCount:21,partitionCount:21,requestedCoveragePlanned:true,requestedTablesExecutedComplete:true,requestedSourceDateCoverageComplete:false},sources:entries,sourceInfos:{ownerEmail:"HIDDEN_INTERNAL_OWNER"},tableBindings,coverage:[{kind:"family",value:{domain:"netshop",family:"promotion",query:q}},{kind:"requested",value:{dimension:"sku",window:"current",mode:"native",status:"executed",sourceKey:"ads"}},...coverage],readProofs:Object.fromEntries(roles.map(role=>[role,{role,screeningId:"scan",package:{complete:true,pages:2,expectedPages:2},budget:{required:false,started:false,complete:true}}])),limitations:["源日期不代表每个实体每日完整"]}, professionalAnalyses:Object.fromEntries(roles.slice(0,3).map(role=>[role,{schemaVersion:"business-screening-diagnosis-v1",reportId,role,summary:"合成专业结论",findings:[{title:"合成候选发现",kind:"finding",explanation:"筛查数值并非因果效果",facts:[{metric:"spendCents",field:"value",value:-100,reference:{candidateId:"fixed"}}],action:null}]}]))};
}
function reportDetail(report) {
 const screen=report.analysisMode==="screening-v1";
 return {item:{id:report.id,name:"合成经营分析报告",status:"running",dryRun:report.dryRun,workflowId:"flow",version:1,createdAt:"2026-09-17",template:"固定",skills:[],scope:q},snapshot:{schemaVersion:"business-report-v1",executionProfile:screen?"business-agent-screening-reference-v1":"business-agent-reference-v2",evidenceProtocol:"reference-v2",question:report.question},delivery:null,workflow:{id:"flow",status:"running",version:showContent?2:1,retryable:false,nodes:[{id:"n",key:"commerce",status:"completed",version:1,output:{answer:"HIDDEN_RAW_OUTPUT"}}]},...(screen?{screeningPreparation:{status:preparationStatus,scanPublished:showContent,agentsStarted:showContent}}:{}),...(showContent?{sections:[{title:"合成报告",body:"候选已保留缺口"}],...content(report.id),...(report.budgetPlan?{budget:preview(report.budgetPlan).budget}:{})}:{})};
}
const server=http.createServer(async(req,res)=>{
  try {
    const url=new URL(req.url,"http://localhost");
    const send=(value,status=200)=>{res.writeHead(status,{"content-type":"application/json","cache-control":"no-store"});res.end(JSON.stringify(value));};
    if(url.pathname.startsWith("/api/")){
      const chunks=[];for await(const chunk of req)chunks.push(chunk);const raw=Buffer.concat(chunks).toString(),body=raw?JSON.parse(raw):null;
      requests.push({path:url.pathname,query:url.search,method:req.method,raw,body,principal});
      if(url.pathname==="/api/ai/report-library")return send({item:{version:0,config:{templates:[],skills:[],pipelines:[]}},history:[],hasMore:false});
      if(url.pathname==="/api/ai/reports")return send({items:actualDetail?[actualDetail.item]:[],total:actualDetail?1:0});
      if(/^\/api\/ai\/reports\/[^/]+\/files$/.test(url.pathname))return send({items:[]});
      if(/^\/api\/ai\/reports\/[^/]+$/.test(url.pathname)){if(actualDetail&&url.pathname.endsWith("/"+actualDetail.item.id))return send(actualDetail);const r=reports.find(r=>r.id===url.pathname.split("/").at(-1));assert.ok(r);return send(reportDetail(r));}
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
        if(delayDirectory&&limit===20){delayDirectory=false;await new Promise(resolve=>setTimeout(resolve,900));}
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
const refresh=()=>page.getByRole("button",{name:"刷新选中任务",exact:true}).click();
const expectPair=body=>assert.deepEqual(body.mappingPairs,[{salesKey:"sales",masterKey:"master"}]);
const mode=()=>page.getByLabel("分析方式",{exact:true});
const startScreening=()=>page.getByRole("button",{name:"启动完整筛查与多 Agent 分析（调用模型）",exact:true});
const detail=()=>page.getByRole("region",{name:"完整规则筛查详情",exact:true});
const waitReport=async id=>{await page.locator(".report-detail small").filter({hasText:id}).waitFor();};
try{
 await page.goto(origin);await choose();
 await check("legacy_default_preserves_old_body",async()=>{assert.equal(await mode().inputValue(),"legacy");await ordinary().click();await waitReport("budget-report-1");assert.equal("analysisMode" in posts().at(-1).body,false);assert.equal(await detail().count(),0);});
 await check("explicit_screening_no_simulation_and_safe_queued_status",async()=>{await mode().selectOption("screening-v1");assert.equal(await ordinary().count(),0);assert.equal(await builder().getByRole("button",{name:"创建预算模拟分析（不调用模型）",exact:true}).count(),0);await startScreening().click();await waitReport("budget-report-2");assert.equal(posts().at(-1).body.analysisMode,"screening-v1");assert.equal(posts().at(-1).body.dryRun,false);assert.equal(posts().at(-1).body.expectedPrincipalKey,principalA);await detail().getByText("等待后台规则筛查",{exact:true}).waitFor();assert.doesNotMatch(await page.locator(".report-detail").innerText(),/HIDDEN_RAW_OUTPUT/);});
 await check("unready_scope_retains_mode_and_never_downgrades",async()=>{run.screeningSupported=false;await refresh();await page.getByRole("alert").filter({hasText:"当前筛查范围"}).waitFor();assert.equal(await mode().inputValue(),"screening-v1");assert.equal(await startScreening().isDisabled(),true);run.screeningSupported=true;await refresh();await page.getByRole("alert").filter({hasText:"当前筛查范围"}).waitFor({state:"detached"});});
 await check("mapping_budget_mode_frozen_together",async()=>{await targets();await fill();await calculate();await selectMapping();showContent=true;preparationStatus="analyzing";await budgetButton().click();await waitReport("budget-report-3");const body=posts().at(-1).body;expectPair(body);assert.equal(body.analysisMode,"screening-v1");assert.deepEqual(body.budgetPlan,previews().at(-1).body.budgetPlan);await detail().getByRole("region",{name:"本报告固定预算",exact:true}).waitFor();assert.equal(await page.getByRole("button",{name:"保存为新预算版本",exact:true}).count(),0);});
 await check("complete_coverage_pagination_and_five_role_proofs_safe_text",async()=>{await detail().getByText("全部分析表 · 21 条完整记录",{exact:true}).click();await detail().getByRole("button",{name:"全部分析表下一页",exact:true}).click();await detail().getByText("21–21 / 21",{exact:true}).waitFor();await detail().getByText("规则分区与候选 · 21 条完整记录",{exact:true}).click();await detail().getByRole("button",{name:"规则分区与候选下一页",exact:true}).click();assert.equal(await detail().getByText(/已完整读取角色包/).count(),5);const text=await detail().innerText();assert.match(text,/保留 1；省略 1/);assert.match(text,/-100 分/);assert.match(text,/2026-08-02/);assert.doesNotMatch(text,/HIDDEN_INTERNAL_OWNER|HIDDEN_RAW_OUTPUT|ownerEmail/);await page.screenshot({path:path.join(directory,"screening-desktop.png"),fullPage:true});});
 await check("unknown_status_not_claimed_ready",async()=>{preparationStatus="future_unknown";await page.getByRole("button",{name:"刷新详情",exact:true}).click();await detail().getByText("准备状态尚未确认，请刷新详情。",{exact:true}).waitFor();preparationStatus="analyzing";});
 await check("unknown_reply_refresh_preserves_uuid_body_and_mode",async()=>{dropReport=true;await startScreening().click();await page.getByTestId("write-error").waitFor();const first=posts().at(-1),before=posts().length;assert.equal(await mode().isDisabled(),true);await page.reload();await page.getByText("有一次提交等待确认",{exact:true}).waitFor();assert.equal(posts().length,before);await page.getByRole("button",{name:"确认并重试同一次提交",exact:true}).click();await waitReport("budget-report-4");assert.equal(posts().at(-1).raw,first.raw);assert.equal(reports.length,4);});
 await check("unknown_request_account_switch_never_replays",async()=>{await choose();await mode().selectOption("screening-v1");dropReport=true;await startScreening().click();await page.getByTestId("write-error").waitFor();const before=posts().length;principal=principalB;await page.reload();await page.getByText("当前页暂无任务。",{exact:false}).waitFor();assert.equal(posts().length,before);assert.equal(await page.getByRole("button",{name:"确认并重试同一次提交",exact:true}).count(),0);principal=principalA;await page.reload();await page.getByText("有一次提交等待确认",{exact:true}).waitFor();assert.equal(posts().length,before);await page.getByRole("button",{name:"确认并重试同一次提交",exact:true}).click();await waitReport("budget-report-5");});
 await check("mobile_complete_detail_no_document_overflow",async()=>{await page.setViewportSize({width:390,height:844});await detail().waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);await page.screenshot({path:path.join(directory,"screening-mobile.png"),fullPage:true});});
 const actualPath=process.env.SCREENING_UI_SYNTHETIC_DETAIL;
 if(actualPath)await check("actual_database_http_dto_renders_complete_professional_budget_coverage",async()=>{
  actualDetail=JSON.parse(await fs.readFile(actualPath,"utf8"));await page.reload();await page.getByRole("button",{name:"查看与复核",exact:true}).click();await waitReport(actualDetail.item.id);await detail().getByText("等待人工复核",{exact:true}).waitFor();
  assert.equal(await detail().getByRole("alert").count(),0);assert.equal(await detail().getByText(/已完整读取角色包/).count(),5);
  await detail().getByText(`全部分析表 · ${actualDetail.screening.authority.tableCount} 条完整记录`,{exact:true}).click();await detail().getByText(`规则分区与候选 · ${actualDetail.screening.authority.partitionCount} 条完整记录`,{exact:true}).click();await detail().getByRole("region",{name:"本报告固定预算",exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);await page.screenshot({path:path.join(directory,"actual-http-mobile.png"),fullPage:true});await page.setViewportSize({width:1280,height:1000});await page.screenshot({path:path.join(directory,"actual-http-desktop.png"),fullPage:true});
 });
 assert.deepEqual(pageErrors,[]);assert.deepEqual(serverErrors,[]);assert.deepEqual(rejectedNetwork,[]);
 await fs.writeFile(path.join(directory,"evidence.json"),JSON.stringify({passed:true,syntheticOnly:true,cases:results,pageErrors,serverErrors,rejectedNetwork,reportPostCount:posts().length,uniqueReports:reports.length},null,2));
}catch(error){await fs.writeFile(path.join(directory,"failure.json"),JSON.stringify({error:String(error),results,pageErrors,serverErrors,requests,dom:await page.locator("body").innerText()},null,2));throw error;}
finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
