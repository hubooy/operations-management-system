// Real parent and picker on a synthetic loopback API; no application or model.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const root=process.cwd(), directory=path.resolve(process.argv[2]||".runtime/business-scope-workbench-ui");
await fs.mkdir(directory,{recursive:true});
await fs.writeFile(path.join(directory,"entry.tsx"),`import React from 'react';import{createRoot}from'react-dom/client';import Workbench from ${JSON.stringify(path.join(root,"app/ai-business-workbench.tsx"))};createRoot(document.getElementById('root')!).render(<Workbench onReportCreated={()=>{}}/>);`);
await build({entryPoints:[path.join(directory,"entry.tsx")],bundle:true,outfile:path.join(directory,"app.js"),format:"iife",platform:"browser",jsx:"automatic",logLevel:"silent",define:{"process.env.NODE_ENV":'"production"'},tsconfig:path.join(root,"tsconfig.json")});
const canonical=v=>Array.isArray(v)?`[${v.map(canonical).join(",")}]`:v&&typeof v==="object"?`{${Object.keys(v).sort().map(k=>`${JSON.stringify(k)}:${canonical(v[k])}`).join(",")}}`:JSON.stringify(v);
const sha=v=>createHash("sha256").update(canonical(v)).digest("hex");
const principalA="a".repeat(64),principalB="b".repeat(64);
let principal=principalA,failOptions=false,dropEvidence=false,delayOptions=0;
const requests=[],created=[],results=[],errors=[],network=[];
const mappings={promotion:["jd_promotion","ad"],master:["jd_product_master","product_master"],sku:["jd_sku_daily","sku_daily"]};
const fixture=[["京东","合成范围店","promotion"],["京东","合成范围店","sku"],["京东","另一店","master"],["京东","三店","promotion"],["京东","四店","promotion"],["京东","五店","promotion"],["天猫","合成范围店","promotion"]];
function optionPage(query){
  const revision="7:abcdef012345";
  const items=fixture.map(([platform,shop,dataset])=>{
    const identity={platform,shop,dataset},[source,sourceDataset]=platform==="天猫"?["tmall_promotion","promotion_daily"]:mappings[dataset];
    return {optionKey:sha({domain:"netshop",identity}),identity,source,sourceDataset,dateMetadata:{kind:"published_import_envelope",firstDate:dataset==="master"?null:"2026-07-01",lastDate:dataset==="master"?null:"2026-09-16",snapshotDate:dataset==="master"?"2026-09-16":null,coverageVerified:false},provenance:{kind:"completed_import_metadata",revision,meaning:"historically_published_not_current_fact_coverage"}};
  }).filter(i=>(!query.platform||i.identity.platform===query.platform)&&(!query.dataset||i.identity.dataset===query.dataset)&&(!query.q||i.identity.shop.includes(query.q)||i.identity.platform.includes(query.q)))
    .sort((a,b)=>{const x=[a.identity.platform,a.identity.shop,a.source,a.sourceDataset].join("\0"),y=[b.identity.platform,b.identity.shop,b.source,b.sourceDataset].join("\0");return x<y?-1:x>y?1:0;});
  const page={schemaVersion:"business-analysis-options-v1",domain:"netshop",revision,query,queryDigest:sha(query),items,pagination:{returned:items.length,limit:20,hasMore:false,nextCursor:null},limitations:["历史发布来源，日期包络不表示完整覆盖。"]};
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
    if(url.pathname==="/api/ai/business-plan/netshop-options"){
      if(url.searchParams.get("expectedPrincipalKey")!==principal)return send({error:"账号身份已变化",code:"principal_mismatch"},403);
      if(failOptions)return send({error:"合成来源暂不可读"},503);
      const query=Object.fromEntries([...url.searchParams].filter(([k])=>!["expectedPrincipalKey","cursor","limit"].includes(k)));
      const response={schemaVersion:"business-netshop-options-response-v1",principalKey:principal,page:optionPage(query)};
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
const open=async()=>{await page.getByRole("button",{name:"从历史导入选择网店来源",exact:true}).click();await page.getByRole("button",{name:"添加来源 京东 合成范围店 sku",exact:true}).waitFor();};
const add=async(platform,shop,label,wait=true)=>{const dataset=({SKU:"sku",推广:"promotion",主数据:"master"})[label];await page.getByRole("button",{name:`添加来源 ${platform} ${shop} ${dataset}`,exact:true}).click();if(wait)await page.getByText("正在读取并核验来源…",{exact:true}).waitFor({state:"hidden"});};
const reset=async()=>{principal=principalA;failOptions=false;await page.evaluate(()=>sessionStorage.clear());await page.reload();await page.getByRole("button",{name:"从历史导入选择网店来源",exact:true}).waitFor();};
const shopCard=n=>page.getByRole("group",{name:`店铺 ${n}`,exact:true});
try{
  await page.goto(origin);await page.getByRole("button",{name:"从历史导入选择网店来源",exact:true}).waitFor();
  await check("manual_form_does_not_fetch_options_until_opened",async()=>{assert.equal(requests.filter(r=>r.path.endsWith("netshop-options")).length,0);});
  await check("explicit_single_dataset_replaces_empty_placeholder_without_dates",async()=>{await open();await add("京东","合成范围店","SKU");await page.getByLabel("店铺 1 精确名称",{exact:true}).filter({visible:true}).waitFor();assert.equal(await page.getByLabel("店铺 1 精确名称",{exact:true}).inputValue(),"合成范围店");assert.equal(await shopCard(1).getByRole("checkbox",{name:"SKU 销售",exact:true}).isChecked(),true);assert.equal(await shopCard(1).getByRole("checkbox",{name:"推广与关键词",exact:true}).isChecked(),false);assert.equal(await shopCard(1).getByRole("checkbox",{name:"商品主数据",exact:true}).isChecked(),false);assert.equal(await page.getByLabel("开始日期",{exact:true}).inputValue(),"");assert.equal(await page.getByLabel("结束日期",{exact:true}).inputValue(),"");});
  await check("same_identity_merges_preserves_channel_dates_market_and_invalidates_preview",async()=>{
    await page.getByLabel("分析问题",{exact:true}).fill("核验来源选择");await page.getByLabel("开始日期",{exact:true}).fill("2026-08-01");await page.getByLabel("结束日期",{exact:true}).fill("2026-08-31");
    await page.getByRole("checkbox",{name:"环比",exact:true}).check();await shopCard(1).getByRole("button",{name:"添加 ERP 渠道",exact:true}).click();await page.getByLabel("店铺 1 ERP 渠道 1",{exact:true}).fill("精确B端渠道");
    await page.getByRole("button",{name:"添加市场条件（最多 7 项）",exact:true}).click();for(const [label,value]of [["平台","京东"],["精确类目","切肉机"],["精确范围","POP"],["榜单维度","SKU"],["精确价格带","全部"]])await page.getByLabel(`市场 1 ${label}`,{exact:true}).fill(value);
    await page.getByRole("button",{name:"预览完整来源计划",exact:true}).click();await page.getByRole("region",{name:"来源范围预览",exact:true}).waitFor();await add("京东","合成范围店","推广");await page.getByRole("region",{name:"来源范围预览",exact:true}).waitFor({state:"hidden"});
    assert.equal(await page.getByLabel("店铺 1 ERP 渠道 1",{exact:true}).inputValue(),"精确B端渠道");assert.equal(await page.getByLabel("开始日期",{exact:true}).inputValue(),"2026-08-01");assert.equal(await page.getByRole("checkbox",{name:"环比",exact:true}).isChecked(),true);assert.equal(await page.getByLabel("市场 1 精确类目",{exact:true}).inputValue(),"切肉机");
    await add("京东","合成范围店","推广");assert.equal(await page.getByLabel("店铺 2 精确名称",{exact:true}).count(),0);
  });
  await check("same_name_other_platform_separate_and_four_shop_limit",async()=>{await add("天猫","合成范围店","推广");assert.equal(await page.getByLabel("店铺 2 平台",{exact:true}).inputValue(),"天猫");await add("京东","三店","推广");await add("京东","四店","推广");await add("京东","五店","推广");assert.equal(await page.getByLabel("店铺 5 精确名称",{exact:true}).count(),0);assert.match(await page.locator("body").innerText(),/最多.*4|4.*店铺/);await page.getByText("未添加，请查看分析范围中的提示。",{exact:true}).waitFor();assert.equal(await page.getByText("已添加：京东 · 五店",{exact:false}).count(),0);});
  await check("deleted_row_not_overwritten_by_late_selection",async()=>{await page.getByRole("button",{name:"删除店铺 1",exact:true}).click();delayOptions=500;const count=requests.length;await add("京东","另一店","主数据",false);await page.getByRole("button",{name:"删除店铺 1",exact:true}).click();await page.waitForFunction(()=>Array.from(document.querySelectorAll('input')).some(e=>e.value==="另一店"));assert.equal(await page.getByLabel("店铺 1 精确名称",{exact:true}).inputValue(),"三店");assert.ok(requests.slice(count).some(r=>r.path.endsWith("netshop-options")));});
  await check("lookup_error_keeps_manual_flow_available",async()=>{await reset();failOptions=true;await page.getByRole("button",{name:"从历史导入选择网店来源",exact:true}).click();await page.getByText("合成来源暂不可读",{exact:false}).waitFor();await page.getByLabel("店铺 1 精确名称",{exact:true}).fill("手动精确店");assert.equal(await page.getByLabel("店铺 1 精确名称",{exact:true}).inputValue(),"手动精确店");failOptions=false;});
  await check("account_change_before_add_refreshes_parent_without_applying_source",async()=>{await reset();await open();principal=principalB;await add("京东","合成范围店","SKU");await page.getByText("账号已变化",{exact:false}).first().waitFor();assert.equal(await page.getByLabel("店铺 1 精确名称",{exact:true}).inputValue(),"");assert.equal(await page.getByRole("button",{name:"收起网店来源选择",exact:true}).count(),0);});
  await check("frozen_unknown_submission_disables_picker_and_replays_identical_body",async()=>{
    await reset();await open();await add("京东","合成范围店","SKU");await page.getByLabel("分析问题",{exact:true}).fill("只采集明确选择的来源");await page.getByLabel("开始日期",{exact:true}).fill("2026-08-01");await page.getByLabel("结束日期",{exact:true}).fill("2026-08-31");
    await page.getByRole("button",{name:"预览完整来源计划",exact:true}).click();await page.getByRole("region",{name:"来源范围预览",exact:true}).waitFor();const planned=requests.filter(r=>r.path.endsWith("/preview")).at(-1).body;assert.deepEqual(planned.shops,[{platform:"京东",shop:"合成范围店",datasets:["sku"],salesChannels:[]}]);
    await page.getByRole("checkbox",{name:/我已核对精确范围/}).check();dropEvidence=true;await page.getByRole("button",{name:"确认范围并开始后台采集",exact:true}).click();await page.getByText("有一次提交等待确认",{exact:true}).waitFor();assert.equal(await page.getByRole("button",{name:"收起网店来源选择",exact:true}).isDisabled(),true);
    await page.getByText("合成已接收但回执未知",{exact:false}).waitFor();const first=requests.filter(r=>r.method==="POST"&&r.path==="/api/ai/business-evidence").at(-1).raw;await page.reload();await page.getByRole("button",{name:"确认并重试同一次提交",exact:true}).click();await page.getByText("证据任务已保存",{exact:false}).waitFor();const posted=requests.filter(r=>r.method==="POST"&&r.path==="/api/ai/business-evidence");assert.equal(posted.at(-1).raw,first);assert.equal(created.length,1);
  });
  await check("mobile_parent_picker_layout",async()=>{await reset();await page.setViewportSize({width:390,height:844});await open();await page.screenshot({path:path.join(directory,"mobile.png"),fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true);});
  assert.deepEqual(errors,[]);assert.deepEqual(network,[]);await fs.writeFile(path.join(directory,"evidence.json"),JSON.stringify({passed:results.length,results,pageErrors:errors,externalRequests:network,operations:{production:false,models:false}},null,2));
}finally{await browser.close();await new Promise(r=>server.close(r));}
