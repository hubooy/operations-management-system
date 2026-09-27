// Real parent and picker on a synthetic loopback API; no application or model.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const root=process.cwd(), directory=path.resolve(process.argv[2]||".runtime/business-market-workbench-ui");
await fs.mkdir(directory,{recursive:true});
await fs.writeFile(path.join(directory,"entry.tsx"),`import React from 'react';import{createRoot}from'react-dom/client';import Workbench from ${JSON.stringify(path.join(root,"app/ai-business-workbench.tsx"))};createRoot(document.getElementById('root')!).render(<Workbench onReportCreated={()=>{}}/>);`);
await build({entryPoints:[path.join(directory,"entry.tsx")],bundle:true,outfile:path.join(directory,"app.js"),format:"iife",platform:"browser",jsx:"automatic",logLevel:"silent",define:{"process.env.NODE_ENV":'"production"'},tsconfig:path.join(root,"tsconfig.json")});
const canonical=v=>Array.isArray(v)?`[${v.map(canonical).join(",")}]`:v&&typeof v==="object"?`{${Object.keys(v).sort().map(k=>`${JSON.stringify(k)}:${canonical(v[k])}`).join(",")}}`:JSON.stringify(v);
const sha=v=>createHash("sha256").update(canonical(v)).digest("hex");
const principalA="a".repeat(64),principalB="b".repeat(64);
let principal=principalA,failOptions=false,dropEvidence=false,delayOptions=0;
const requests=[],created=[],results=[],errors=[],network=[];
const fixture=Array.from({length:8},(_,i)=>({platform:"京东",category:`合成类目${i+1}`,scope:"POP",rankingDimension:"SKU",priceBandFilter:"全部"}));
function optionPage(query){
  const revision="7:abcdef012345",generation="c".repeat(32),directoryDigest="d".repeat(64);
  const items=fixture.filter(identity=>Object.entries(query).every(([k,v])=>k==="q"?Object.values(identity).some(x=>x.includes(v)):identity[k]===v)).map(identity=>({
    optionKey:sha({domain:"market",identity}),identity,source:"market_daily_top",sourceDataset:"market_daily_top",
    dateMetadata:{kind:"published_import_envelope",firstDate:"2026-07-01",lastDate:"2026-09-16",snapshotDate:null,coverageVerified:false},
    provenance:{kind:"completed_import_metadata",revision,generation,directoryDigest,meaning:"historically_published_not_current_fact_coverage"}}));
  const page={schemaVersion:"business-analysis-options-v1",domain:"market",authorityVerified:true,revision,directoryGeneration:generation,directoryDigest,query,queryDigest:sha(query),items,pagination:{returned:items.length,limit:20,hasMore:false,nextCursor:null},limitations:["历史包络不是完整事实"]};
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
    if(url.pathname==="/api/ai/business-plan/market-options"){
      if(url.searchParams.get("expectedPrincipalKey")!==principal)return send({error:"账号身份已变化",code:"principal_mismatch"},403);
      if(failOptions)return send({error:"合成来源暂不可读"},503);
      const query=Object.fromEntries([...url.searchParams].filter(([k])=>!["expectedPrincipalKey","cursor","limit"].includes(k)));
      const response={schemaVersion:"business-market-options-response-v1",principalKey:principal,page:optionPage(query)};
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
const open=async()=>{await page.getByRole("button",{name:"从历史导入选择市场来源",exact:true}).click();await page.getByRole("button",{name:"添加市场来源 京东 合成类目1 POP SKU 全部",exact:true}).waitFor();};
const add=async(n)=>{await page.getByRole("button",{name:`添加市场来源 京东 合成类目${n} POP SKU 全部`,exact:true}).click();await page.getByRole("region",{name:"选择市场分析来源"}).getByRole("button",{name:`添加市场来源 京东 合成类目${n} POP SKU 全部`,exact:true}).waitFor({state:"visible"});await page.waitForFunction(()=>!Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes("取消")&&b.textContent.includes("读取")));};
const reset=async()=>{principal=principalA;failOptions=false;await page.evaluate(()=>sessionStorage.clear());await page.reload();await page.getByRole("button",{name:"从历史导入选择市场来源",exact:true}).waitFor();};
try{
  await page.goto(origin);await page.getByRole("button",{name:"从历史导入选择市场来源",exact:true}).waitFor();
  await check("manual_form_does_not_fetch_market_before_open",async()=>{assert.equal(requests.filter(r=>r.path.endsWith("market-options")).length,0);});
  await check("market_selection_preserves_shop_channels_dates_and_invalidates_preview",async()=>{
    await page.getByLabel("分析问题",{exact:true}).fill("比较市场与店铺表现");
    await page.getByLabel("开始日期",{exact:true}).fill("2026-08-01");await page.getByLabel("结束日期",{exact:true}).fill("2026-08-31");
    await page.getByLabel("店铺 1 精确名称",{exact:true}).fill("保留店铺");
    await page.getByRole("group",{name:"店铺 1",exact:true}).getByRole("button",{name:"添加 ERP 渠道",exact:true}).click();await page.getByLabel("店铺 1 ERP 渠道 1",{exact:true}).fill("精确渠道");
    await page.getByRole("button",{name:"预览完整来源计划",exact:true}).click();await page.getByRole("region",{name:"来源范围预览",exact:true}).waitFor();
    await open();await add(1);await page.getByRole("region",{name:"来源范围预览",exact:true}).waitFor({state:"hidden"});
    for(const [label,value]of [["平台","京东"],["精确类目","合成类目1"],["精确范围","POP"],["榜单维度","SKU"],["精确价格带","全部"]])assert.equal(await page.getByLabel(`市场 1 ${label}`,{exact:true}).inputValue(),value);
    assert.equal(await page.getByLabel("店铺 1 ERP 渠道 1",{exact:true}).inputValue(),"精确渠道");assert.equal(await page.getByLabel("开始日期",{exact:true}).inputValue(),"2026-08-01");
    await add(1);assert.equal(await page.getByLabel("市场 2 精确类目",{exact:true}).count(),0);
  });
  await check("exact_market_cap_seven_does_not_replace_prior",async()=>{for(let n=2;n<=8;n++)await add(n);assert.equal(await page.getByLabel("市场 8 精确类目",{exact:true}).count(),0);assert.equal(await page.getByLabel("市场 7 精确类目",{exact:true}).inputValue(),"合成类目7");assert.match(await page.locator("body").innerText(),/7/);});
  await check("lookup_failure_preserves_manual_market_flow",async()=>{await reset();failOptions=true;await page.getByRole("button",{name:"从历史导入选择市场来源",exact:true}).click();await page.getByText("合成来源暂不可读",{exact:false}).waitFor();await page.getByRole("button",{name:"添加市场条件（最多 7 项）",exact:true}).click();await page.getByLabel("市场 1 精确类目",{exact:true}).fill("手动类目");assert.equal(await page.getByLabel("市场 1 精确类目",{exact:true}).inputValue(),"手动类目");});
  await check("changed_account_never_adds_old_market",async()=>{await reset();await open();principal=principalB;await page.getByRole("button",{name:"添加市场来源 京东 合成类目1 POP SKU 全部",exact:true}).click();await page.getByText("账号已变化",{exact:false}).first().waitFor();assert.equal(await page.getByLabel("市场 1 精确类目",{exact:true}).count(),0);assert.equal(await page.getByRole("button",{name:"收起市场来源选择",exact:true}).count(),0);});
  await check("frozen_unknown_request_preserves_market_identity_and_same_body",async()=>{
    await reset();await open();await add(1);await page.getByLabel("分析问题",{exact:true}).fill("市场关联分析");await page.getByLabel("店铺 1 精确名称",{exact:true}).fill("保留店铺");await page.getByLabel("开始日期",{exact:true}).fill("2026-08-01");await page.getByLabel("结束日期",{exact:true}).fill("2026-08-31");
    await page.getByRole("button",{name:"预览完整来源计划",exact:true}).click();await page.getByRole("region",{name:"来源范围预览",exact:true}).waitFor();assert.deepEqual(requests.filter(r=>r.path.endsWith("/preview")).at(-1).body.markets,[fixture[0]]);
    await page.getByRole("checkbox",{name:/我已核对精确范围/}).check();dropEvidence=true;await page.getByRole("button",{name:"确认范围并开始后台采集",exact:true}).click();await page.getByText("有一次提交等待确认",{exact:true}).waitFor();assert.equal(await page.getByRole("button",{name:"收起市场来源选择",exact:true}).isDisabled(),true);
    await page.getByText("合成已接收但回执未知",{exact:false}).waitFor();const first=requests.filter(r=>r.method==="POST"&&r.path==="/api/ai/business-evidence").at(-1).raw;await page.reload();await page.getByRole("button",{name:"确认并重试同一次提交",exact:true}).click();await page.getByText("证据任务已保存",{exact:false}).waitFor();assert.equal(requests.filter(r=>r.method==="POST"&&r.path==="/api/ai/business-evidence").at(-1).raw,first);assert.equal(created.length,1);
  });
  await check("market_picker_mobile_layout",async()=>{await reset();await page.setViewportSize({width:390,height:844});await open();await page.screenshot({path:path.join(directory,"mobile.png"),fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true);});
  assert.deepEqual(errors,[]);assert.deepEqual(network,[]);await fs.writeFile(path.join(directory,"evidence.json"),JSON.stringify({passed:results.length,results,pageErrors:errors,externalRequests:network,operations:{production:false,models:false}},null,2));
}catch(error){await fs.writeFile(path.join(directory,"failure.json"),JSON.stringify({error:String(error),results,errors,dom:await page.locator("body").innerText()},null,2));throw error;}
finally{await browser.close();await new Promise(r=>server.close(r));}
