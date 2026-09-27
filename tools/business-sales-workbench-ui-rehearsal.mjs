// Real parent and picker on a synthetic loopback API; no application or model.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const root=process.cwd(), directory=path.resolve(process.argv[2]||".runtime/business-sales-workbench-ui");
await fs.mkdir(directory,{recursive:true});
await fs.writeFile(path.join(directory,"entry.tsx"),`import React from 'react';import{createRoot}from'react-dom/client';import Workbench from ${JSON.stringify(path.join(root,"app/ai-business-workbench.tsx"))};createRoot(document.getElementById('root')!).render(<Workbench onReportCreated={()=>{}}/>);`);
await build({entryPoints:[path.join(directory,"entry.tsx")],bundle:true,outfile:path.join(directory,"app.js"),format:"iife",platform:"browser",jsx:"automatic",logLevel:"silent",define:{"process.env.NODE_ENV":'"production"'},tsconfig:path.join(root,"tsconfig.json")});
const canonical=v=>Array.isArray(v)?`[${v.map(canonical).join(",")}]`:v&&typeof v==="object"?`{${Object.keys(v).sort().map(k=>`${JSON.stringify(k)}:${canonical(v[k])}`).join(",")}}`:JSON.stringify(v);
const sha=v=>createHash("sha256").update(canonical(v)).digest("hex");
const principalA="a".repeat(64),principalB="b".repeat(64);
let principal=principalA,failOptions=false,dropEvidence=false,delayOptions=0;
const requests=[],created=[],results=[],errors=[],network=[];
const fixture=[{platform:"京东",shop:"保留店铺",channel:"渠道一"},{platform:"京东",shop:"新增ERP店",channel:"渠道二"}];
function optionPage(query){
 const revision="7:2",generation="c".repeat(32),directoryDigest="d".repeat(64);
 const items=fixture.filter(identity=>Object.entries(query).every(([k,v])=>identity[k]===v)).map(identity=>({
  optionKey:sha({domain:"sales",identity}),identity,source:"erp_sales",sourceDataset:"sales_order_lines",
  dateMetadata:{kind:"current_fact_business_date_envelope",firstDate:"2026-07-01",lastDate:"2026-09-16",snapshotDate:null,coverageVerified:false},
  provenance:{kind:"current_exact_business_projection",revision,meaning:"observed_current_identity_not_complete_period_coverage"}}));
 const page={schemaVersion:"business-analysis-options-v1",domain:"sales",authorityVerified:true,revision,directoryGeneration:generation,directoryDigest,query,queryDigest:sha(query),items,pagination:{returned:items.length,limit:20,hasMore:false,nextCursor:null},limitations:["当前日期包络不证明完整覆盖"]};
 return {...page,pageDigest:sha(page)};
}
function preview(body){
  const coverage=[];
  const add=(domain,query)=>coverage.push({domain,query,status:"planned",availability:"not_collected",reason:"合成支持性预览，尚未采集",sourceKey:`source-${coverage.length}`});
  for(const s of body.shops){for(const dataset of s.datasets)for(const window of dataset==="master"?["current"]:body.windows)add("netshop",{platform:s.platform,shop:s.shop,dataset,window,startDate:body.startDate,endDate:body.endDate});for(const channel of s.salesChannels)for(const window of body.windows)add("sales",{platform:s.platform,shop:s.shop,channel,window,startDate:body.startDate,endDate:body.endDate});}
  for(const m of body.markets)for(const window of body.windows)add("market",{...m,window,startDate:body.startDate,endDate:body.endDate});
  const sources=coverage.map(r=>({key:r.sourceKey,domain:r.domain,query:r.query})),fits=sources.length>0&&sources.length<=48;
  return {schemaVersion:"business-plan-preview-v2",principalKey:principal,canCollect:fits,planDigest:fits?sha(body):null,catalogDigest:fits?sha(sources):null,request:body,coverage,capacity:{sourceCount:sources.length,maxSources:48,planBytes:fits?4000:null,maxPlanBytes:16000,workflowBytes:fits?5000:null,maxWorkflowBytes:8000,queryBytes:500,maxQueryBytes:4096,directoryQueryBytes:500*sources.length,maxDirectoryQueryBytes:131072,factBytes:67108864,factPages:2000},limitations:["合成预览，不代表真实覆盖"],evidenceRequest:{schemaVersion:"business-evidence-v2",sources,collectionMode:"bulk",autoCollect:true,analysisRequest:{schemaVersion:"business-analysis-request-v1",question:body.question,requestedDimensions:["shop","sku"],requestedWindows:body.windows}}};
}
const server=http.createServer(async(req,res)=>{try{
  const url=new URL(req.url,"http://localhost");const send=(value,status=200)=>{res.writeHead(status,{"content-type":"application/json","cache-control":"no-store"});res.end(JSON.stringify(value));};
  if(url.pathname.startsWith("/api/")){
    const chunks=[];for await(const c of req)chunks.push(c);const raw=Buffer.concat(chunks).toString(),body=raw?JSON.parse(raw):null;
    requests.push({path:url.pathname,query:url.search,method:req.method,body,raw,principal});
    if(url.pathname==="/api/ai/business-plan/sales-options"){
      if(url.searchParams.get("expectedPrincipalKey")!==principal)return send({error:"账号身份已变化",code:"principal_mismatch"},403);
      if(failOptions)return send({error:"合成来源暂不可读"},503);
      const query=Object.fromEntries([...url.searchParams].filter(([k])=>!["expectedPrincipalKey","cursor","limit"].includes(k)));
      const response={schemaVersion:"business-sales-options-response-v1",principalKey:principal,page:optionPage(query)};
      const delay=delayOptions;delayOptions=0;if(delay)await new Promise(r=>setTimeout(r,delay));return send(response);
    }
    if(url.pathname==="/api/ai/business-plan/preview")return send(preview(body));
    if(url.pathname==="/api/ai/business-evidence"&&req.method==="GET")return send({principalKey:principal,items:[],total:0});
    if(url.pathname==="/api/ai/business-evidence"&&req.method==="POST"){
      assert.equal(body.expectedPrincipalKey,principal);const old=created.find(x=>x.body.clientRequestId===body.clientRequestId);
      if(old)assert.equal(old.raw,raw);else created.push({body,raw});
      if(dropEvidence){dropEvidence=false;return send({error:"合成已接收但回执未知"},503);}return send({item:{id:"fixture-evidence"}});
    }
    if(url.pathname==="/api/ai/business-evidence/fixture-evidence")return send({error:"合成测试不启动采集"},404);
    return send({error:"unexpected synthetic route"},404);
  }
  if(["/app.js","/app.css"].includes(url.pathname)){res.setHeader("content-type",url.pathname.endsWith("css")?"text/css":"text/javascript");return res.end(await fs.readFile(path.join(directory,url.pathname.slice(1))));}
  res.setHeader("content-type","text/html;charset=utf-8");res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"></head><body style="margin:8px;font-family:Arial"><div id="root"></div><script src="/app.js"></script></body></html>');
}catch(e){errors.push(String(e));res.statusCode=500;res.end(JSON.stringify({error:String(e)}));}});
await new Promise(r=>server.listen(0,"127.0.0.1",r));const origin=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",headless:true});
const page=await browser.newPage({viewport:{width:1280,height:1000}});page.on("pageerror",e=>errors.push(String(e)));
await page.route("**/*",route=>{if(new URL(route.request().url()).origin!==origin){network.push(route.request().url());return route.abort();}return route.continue();});
const check=async(name,fn)=>{await fn();results.push({name,passed:true});console.log(JSON.stringify(results.at(-1)));};
const toggle=()=>page.getByRole("button",{name:"选择ERP店铺与销售渠道",exact:true});
const chosen=n=>page.getByRole("button",{name:`添加ERP来源 ${fixture[n].platform} ${fixture[n].shop} ${fixture[n].channel}`,exact:true});
const open=async()=>{await toggle().click();await chosen(0).waitFor();};
const add=async n=>{await chosen(n).click();await page.getByRole("region",{name:"选择ERP分析来源"}).getByText(`已添加：${fixture[n].platform} · ${fixture[n].shop} · ${fixture[n].channel}。分析日期保持原设置。`,{exact:true}).waitFor();};
const reset=async()=>{principal=principalA;failOptions=false;dropEvidence=false;delayOptions=0;await page.evaluate(()=>sessionStorage.clear());await page.reload();await toggle().waitFor();};
const manual=async()=>{
 await page.getByLabel("分析问题",{exact:true}).fill("ERP与店铺市场联合分析");
 await page.getByLabel("开始日期",{exact:true}).fill("2026-08-01");await page.getByLabel("结束日期",{exact:true}).fill("2026-08-31");
 await page.getByLabel("店铺 1 精确名称",{exact:true}).fill("保留店铺");
 await page.getByRole("group",{name:"店铺 1",exact:true}).getByRole("button",{name:"添加 ERP 渠道",exact:true}).click();
 await page.getByLabel("店铺 1 ERP 渠道 1",{exact:true}).fill("手填渠道");
 await page.getByRole("button",{name:"添加市场条件（最多 7 项）",exact:true}).click();
 for(const [label,value] of [["平台","京东"],["精确类目","手填类目"],["精确范围","POP"],["榜单维度","SKU"],["精确价格带","手填价格带"]])await page.getByLabel(`市场 1 ${label}`,{exact:true}).fill(value);
};
const lastPreview=()=>requests.filter(r=>r.path.endsWith("/preview")).at(-1).body;
const previewNow=async()=>{await page.getByRole("button",{name:"预览完整来源计划",exact:true}).click();await page.getByRole("region",{name:"来源范围预览",exact:true}).waitFor();};
try{
 await page.goto(origin);await toggle().waitFor();
 await check("closed_picker_never_reads_sales_directory",async()=>{assert.equal(requests.filter(r=>r.path.endsWith("sales-options")).length,0);});
 await check("sales_selection_preserves_manual_dates_markets_datasets_and_invalidates_preview",async()=>{
  await manual();await previewNow();const before=structuredClone(lastPreview());
  await open();await add(0);await page.getByRole("region",{name:"来源范围预览",exact:true}).waitFor({state:"hidden"});
  assert.equal(await page.getByLabel("店铺 1 ERP 渠道 1",{exact:true}).inputValue(),"手填渠道");
  assert.equal(await page.getByLabel("店铺 1 ERP 渠道 2",{exact:true}).inputValue(),"渠道一");
  await previewNow();const after=lastPreview();
  for(const key of ["question","startDate","endDate","windows","markets"])assert.deepEqual(after[key],before[key]);
  assert.deepEqual(after.shops[0],{...before.shops[0],salesChannels:["手填渠道","渠道一"]});
 });
 await check("duplicate_selection_is_idempotent",async()=>{await add(0);assert.equal(await page.getByLabel("店铺 1 ERP 渠道 3",{exact:true}).count(),0);});
 await check("new_sales_shop_does_not_invent_netshop_datasets",async()=>{await add(1);await previewNow();assert.deepEqual(lastPreview().shops[1],{platform:"京东",shop:"新增ERP店",datasets:[],salesChannels:["渠道二"]});});
 await check("failed_directory_preserves_existing_manual_inputs",async()=>{
  await reset();await manual();failOptions=true;await toggle().click();await page.getByText("合成来源暂不可读",{exact:false}).waitFor();
  assert.equal(await page.getByLabel("店铺 1 ERP 渠道 1",{exact:true}).inputValue(),"手填渠道");
  assert.equal(await page.getByLabel("市场 1 精确类目",{exact:true}).inputValue(),"手填类目");
  await page.getByLabel("店铺 1 ERP 渠道 1",{exact:true}).fill("错误后仍可手填");await previewNow();assert.deepEqual(lastPreview().shops[0].salesChannels,["错误后仍可手填"]);
 });
 await check("account_change_rejects_stale_selection",async()=>{
  await reset();await manual();await open();principal=principalB;await chosen(0).click();await page.getByText("账号已变化",{exact:false}).first().waitFor();
  assert.equal(await page.getByLabel("店铺 1 ERP 渠道 2",{exact:true}).count(),0);
  assert.equal(await page.getByRole("button",{name:"收起ERP来源选择",exact:true}).count(),0);
 });
 await check("unknown_submission_replays_exact_frozen_sales_body",async()=>{
  await reset();await manual();await open();await add(0);await previewNow();
  assert.deepEqual(lastPreview().shops[0].salesChannels,["手填渠道","渠道一"]);
  await page.getByRole("checkbox",{name:/我已核对精确范围/}).check();dropEvidence=true;
  await page.getByRole("button",{name:"确认范围并开始后台采集",exact:true}).click();await page.getByText("有一次提交等待确认",{exact:true}).waitFor();
  assert.equal(await page.getByRole("button",{name:"收起ERP来源选择",exact:true}).isDisabled(),true);
  await page.getByText("合成已接收但回执未知",{exact:false}).waitFor();
  const first=requests.filter(r=>r.method==="POST"&&r.path==="/api/ai/business-evidence").at(-1).raw;
  await page.reload();await page.getByRole("button",{name:"确认并重试同一次提交",exact:true}).click();await page.getByText("证据任务已保存",{exact:false}).waitFor();
  assert.equal(requests.filter(r=>r.method==="POST"&&r.path==="/api/ai/business-evidence").at(-1).raw,first);assert.equal(created.length,1);
 });
 await check("desktop_and_390px_layout_have_no_overflow",async()=>{
  await reset();await manual();await open();await add(0);await page.screenshot({path:path.join(directory,"desktop.png"),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true);
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(directory,"mobile.png"),fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true);
 });
 assert.deepEqual(errors,[]);assert.deepEqual(network,[]);
 await fs.writeFile(path.join(directory,"evidence.json"),JSON.stringify({passed:results.length,results,pageErrors:errors,externalRequests:network,operations:{production:false,models:false}},null,2));
}catch(error){await fs.writeFile(path.join(directory,"failure.json"),JSON.stringify({error:String(error),results,errors,dom:await page.locator("body").innerText()},null,2));throw error;}
finally{await browser.close();await new Promise(r=>server.close(r));}
