// Isolated browser contract rehearsal: synthetic API only, no application service.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const root = process.cwd(), directory = path.resolve(process.argv[2] || ".runtime/business-workbench-ui");
await fs.mkdir(directory, { recursive: true });
const entry = path.join(directory, "entry.tsx");
await fs.writeFile(entry, `import React from 'react';import{createRoot}from'react-dom/client';import Workbench from ${JSON.stringify(path.join(root,"app/ai-business-workbench.tsx"))};createRoot(document.getElementById('root')!).render(<Workbench onReportCreated={id=>{document.getElementById('report-result')!.textContent=id;}}/>);`);
await build({ entryPoints: [entry], bundle: true, outfile: path.join(directory,"app.js"), format: "iife", platform: "browser", jsx: "automatic", logLevel: "silent", define: { "process.env.NODE_ENV": '"production"' }, tsconfig: path.join(root,"tsconfig.json") });
const principalA = "a".repeat(64), principalB = "b".repeat(64);
let principal = principalA, dropEvidence = false, dropReport = false, detailDelay = "", flipAfterPreflight = false, rejectEvidence = 0, previewDelay = 0, previewFault = "";
const evidence = [], reportRows = [], requests = [], pageErrors = [];
const source = (key="source-1") => ({ key, domain: "netshop", query: { platform: "京东", shop: "合成店铺", dataset: "promotion", window: "current" } });
function item(id, question, status="collecting") { return { id, question, clientRequestId:id, status, version:1, collection:{status:status==="collecting"?"reading":status}, createdAt:"2026-09-16T08:00:00Z", storedBytes:2048, sourceCount:2, completedSources:0, rowCount:12, owner:principalA,
  plan:{analysisRequest:{question},collector:{version:1,surface:"business_collection",pageSize:100},sources:[source(),{...source("source-2"),query:{...source().query,dataset:"master"}}]},sources:{"source-1":{pageCount:1,rowCount:12,complete:status==="sealed"}} }; }
function makePreview(body) {
  const coverage=[];
  const add=(domain,query)=>coverage.push({domain,query,status:"planned",availability:"not_collected",reason:"查询组合已列入计划，数据可用性待采集核验",sourceKey:"s-"+coverage.length});
  for(const shop of body.shops){
    for(const dataset of shop.datasets) for(const window of dataset==="master"?["current"]:body.windows) add("netshop",{platform:shop.platform,shop:shop.shop,dataset,window,startDate:body.startDate,endDate:body.endDate});
    for(const channel of shop.salesChannels) for(const window of body.windows) add("sales",{platform:shop.platform,shop:shop.shop,channel,window});
  }
  for(const market of body.markets)for(const window of body.windows)add("market",{...market,window});
  const sources=coverage.map(c=>({key:c.sourceKey,domain:c.domain,query:c.query}));
  const fits=sources.length>0&&sources.length<=48;
  return {schemaVersion:"business-plan-preview-v2",principalKey:principal,canCollect:fits,planDigest:fits?"d".repeat(64):null,catalogDigest:fits?"c".repeat(64):null,request:body,coverage,
    capacity:{sourceCount:sources.length,maxSources:48,planBytes:fits?4000:null,maxPlanBytes:16000,workflowBytes:fits?5000:null,maxWorkflowBytes:8000,queryBytes:500,maxQueryBytes:4096,directoryQueryBytes:500*sources.length,maxDirectoryQueryBytes:131072,factBytes:67108864,factPages:2000},
    limitations:["尚未采集，不代表业务记录已存在。",...(sources.length>48?["来源数量超过上限48；完整请求保留，不自动缩小。"]:[])],
    evidenceRequest:{schemaVersion:"business-evidence-v2",sources,collectionMode:"bulk",autoCollect:true,analysisRequest:{schemaVersion:"business-analysis-request-v1",question:body.question,requestedDimensions:["shop","category","spu","sku","keyword"],requestedWindows:body.windows}}};
}
const server=http.createServer(async(req,res)=>{
  try {
    const url=new URL(req.url,"http://localhost");
    if(url.pathname.startsWith("/api/")){
      const bytes=[];for await(const chunk of req)bytes.push(chunk);const raw=Buffer.concat(bytes).toString();const body=raw?JSON.parse(raw):null;
      requests.push({path:url.pathname,method:req.method,body,raw,principal});
      const send=(data,status=200)=>{res.writeHead(status,{"content-type":"application/json","cache-control":"no-store"});res.end(JSON.stringify(data));};
      if(url.pathname==="/api/ai/business-plan/preview"){assert.equal(body.schemaVersion,"business-plan-request-v2");const result=makePreview(body),delay=previewDelay;if(previewFault==="preview")result.schemaVersion="business-plan-preview-v1";if(previewFault==="evidence")delete result.evidenceRequest.schemaVersion;if(previewFault==="coverage")result.coverage.pop();if(previewFault==="nullCapacity")result.capacity.planBytes=null;previewFault="";previewDelay=0;if(delay)await new Promise(resolve=>setTimeout(resolve,delay));return send(result);}
      if(url.pathname==="/api/ai/business-evidence"&&req.method==="GET"){
        const page=Number(url.searchParams.get("page")||1),size=Number(url.searchParams.get("pageSize")||10),owned=evidence.filter(e=>e.owner===principal);
        send({items:owned.slice((page-1)*size,page*size),principalKey:principal,pagination:{page,pageSize:size,total:owned.length,hasMore:page*size<owned.length}});
        if(size===1&&flipAfterPreflight){flipAfterPreflight=false;principal=principal===principalA?principalB:principalA;}
        return;
      }
      if(url.pathname==="/api/ai/business-evidence"&&req.method==="POST"){
        if(body.expectedPrincipalKey!==principal)return send({error:"账号绑定不一致"},403);
        if(rejectEvidence){const status=rejectEvidence;rejectEvidence=0;return send({error:"合成重试权限拒绝"},status);}
        let row=evidence.find(e=>e.clientRequestId===body.clientRequestId&&e.owner===principal);
        if(!row){row=item("evidence-"+(evidence.length+1),body.analysisRequest.question);row.clientRequestId=body.clientRequestId;row.plan={...body,collector:{version:1,surface:"business_collection",pageSize:100}};row.owner=principal;if(body.schemaVersion==="business-evidence-v2"){row.directory=body.sources.map((source,index)=>({...source,ordinal:index+1}));row.plan={schemaVersion:body.schemaVersion,sourceCount:body.sources.length,catalogDigest:"c".repeat(64),collector:row.plan.collector,analysisRequest:body.analysisRequest};row.sourceCount=body.sources.length;row.sources=Object.fromEntries(body.sources.map(source=>[source.key,{pageCount:1,rowCount:12,complete:false}]));row.workbenchAnalysisEnabled=true;}evidence.unshift(row);}
        if(dropEvidence){dropEvidence=false;return send({error:"合成写入成功但回执不可确认"},503);}
        return send({item:row});
      }
      if(url.pathname==="/api/ai/business-reports"){
        if(body.expectedPrincipalKey!==principal)return send({error:"账号绑定不一致"},403);
        let row=reportRows.find(e=>e.clientRequestId===body.clientRequestId);if(!row){row={...body,id:"report-"+(reportRows.length+1),workflowId:"workflow-1",status:"running",createdAt:"2026-09-16T08:05:00Z"};reportRows.push(row);}
        if(dropReport){dropReport=false;return send({error:"合成分析已创建但回执不可确认"},503);}
        return send({item:row});
      }
      const found=url.pathname.match(/^\/api\/ai\/business-evidence\/([^/]+)(?:\/(control|finish|sources))?$/);
      if(found){
        const row=evidence.find(e=>e.id===found[1]&&e.owner===principal);if(!row)return send({error:"任务不存在"},404);
        if(found[2]==="sources"){const offset=Number(url.searchParams.get("offset")),limit=Number(url.searchParams.get("limit")),items=row.directory.slice(offset,offset+limit),next=offset+items.length;return send({schemaVersion:"business-evidence-directory-page-v2",runId:row.id,evidenceVersion:row.version,catalogDigest:row.plan.catalogDigest,offset,total:row.directory.length,returned:items.length,nextOffset:next<row.directory.length?next:null,items});}
        if(found[2]){if(body.expectedVersion!==row.version)return send({error:"版本冲突"},409);row.version++;row.collection.status=body.action==="resume"?"queued":body.action==="pause"?"paused":"cancelled";if(body.action==="cancel")row.status="cancelled";return send({item:row});}
        const response={item:structuredClone(row),reports:reportRows.filter(r=>r.evidenceRunId===row.id),reportsPagination:{limit:10,hasMore:false},principalKey:principal};
        if(detailDelay===row.id)await new Promise(resolve=>setTimeout(resolve,500));
        return send(response);
      }
      return send({error:"未知合成路由"},404);
    }
    if(url.pathname==="/app.js"||url.pathname==="/app.css") {res.setHeader("content-type",url.pathname.endsWith("css")?"text/css":"text/javascript");res.end(await fs.readFile(path.join(directory,url.pathname.slice(1))));return;}
    res.setHeader("content-type","text/html;charset=utf-8");res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"></head><body style="margin:8px;font-family:Arial,sans-serif"><main id="root"></main><div id="report-result"></div><script src="/app.js"></script></body></html>');
  } catch(error){res.statusCode=500;res.end(JSON.stringify({error:String(error)}));}
});
await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
const url=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",headless:true});
const page=await browser.newPage({viewport:{width:1280,height:1000}});page.on("pageerror",error=>pageErrors.push(String(error)));
const count=path=>requests.filter(r=>r.path===path&&r.method==="POST").length;
const results=[];
const check=async(name,task)=>{await task();results.push({name,passed:true});console.log(JSON.stringify({case:name,passed:true}));};
try{
 await page.goto(url);await page.getByLabel("分析问题",{exact:true}).fill("推广投入为什么没有带动销售？");await page.getByLabel("开始日期",{exact:true}).fill("2026-09-01");await page.getByLabel("结束日期",{exact:true}).fill("2026-09-15");await page.getByLabel("店铺 1 精确名称",{exact:true}).fill("合成店铺");
 await check("explicit_source_preview",async()=>{
  await page.getByRole("button",{name:"预览完整来源计划",exact:true}).click();await page.getByText("拟采集 2 / 48 个来源",{exact:false}).waitFor();
  assert.equal(await page.getByRole("cell",{name:"尚未取数",exact:true}).count(),2);assert.equal(count("/api/ai/business-evidence"),0);assert.equal(count("/api/ai/business-reports"),0);
 });
 await check("v2_protocol_downgrade_and_incomplete_coverage_rejected",async()=>{
  for(const fault of ["preview","evidence","coverage","nullCapacity"]){previewFault=fault;await page.getByRole("button",{name:"预览完整来源计划",exact:true}).click();await page.getByRole("alert").filter({hasText:"来源预览协议或完整性不匹配"}).waitFor();assert.equal(await page.getByRole("region",{name:"来源范围预览"}).count(),0);}
  assert.equal(count("/api/ai/business-evidence"),0);await page.getByRole("button",{name:"预览完整来源计划",exact:true}).click();await page.getByText("拟采集 2 / 48 个来源",{exact:false}).waitFor();
 });
 await check("unknown_evidence_survives_refresh_and_reuses_payload",async()=>{
  dropEvidence=true;await page.getByLabel("我已核对精确范围与限制，确认采集以上全部来源").check();await page.getByRole("button",{name:"确认范围并开始后台采集",exact:true}).click();await page.getByTestId("write-error").waitFor();
  const first=requests.find(r=>r.path==="/api/ai/business-evidence"&&r.method==="POST").raw;
  await page.getByRole("button",{name:"刷新任务列表",exact:true}).click();assert.match(await page.getByTestId("write-error").innerText(),/待确认/);
  await page.reload();await page.getByText("有一次提交等待确认",{exact:true}).waitFor();assert.equal(count("/api/ai/business-evidence"),1);assert.equal(await page.getByLabel("分析问题",{exact:true}).isDisabled(),true);
  rejectEvidence=403;await page.getByRole("button",{name:"确认并重试同一次提交",exact:true}).click();await page.getByTestId("write-error").filter({hasText:"合成重试权限拒绝"}).waitFor();assert.equal(await page.getByLabel("分析问题",{exact:true}).isDisabled(),true);
  await page.reload();await page.getByText("有一次提交等待确认",{exact:true}).waitFor();assert.equal(count("/api/ai/business-evidence"),2);
  await page.getByRole("button",{name:"确认并重试同一次提交",exact:true}).click();await page.getByRole("region",{name:"选中任务详情"}).getByText("后台读取中",{exact:false}).waitFor();
  for(const r of requests.filter(r=>r.path==="/api/ai/business-evidence"&&r.method==="POST"))assert.equal(r.raw,first);assert.equal(evidence.length,1);
 });
 await check("background_controls_and_manual_sealed_analysis",async()=>{
  assert.equal(await page.getByRole("button",{name:"启动多 Agent 分析（调用模型）",exact:true}).isDisabled(),true);
  await page.getByRole("button",{name:"暂停后台采集",exact:true}).click();await page.getByRole("button",{name:"恢复后台采集",exact:true}).waitFor();
  await page.getByRole("button",{name:"恢复后台采集",exact:true}).click();await page.getByRole("button",{name:"暂停后台采集",exact:true}).waitFor();
  evidence[0].status="sealed";evidence[0].collection.status="sealed";evidence[0].version++;await page.getByRole("button",{name:"刷新选中任务",exact:true}).click();await page.getByRole("region",{name:"选中任务详情"}).getByText("证据已封存",{exact:false}).waitFor();
  assert.equal(count("/api/ai/business-reports"),0);dropReport=true;await page.getByRole("button",{name:"启动多 Agent 分析（调用模型）",exact:true}).click();await page.getByTestId("write-error").waitFor();
  const first=requests.find(r=>r.path==="/api/ai/business-reports").raw;await page.reload();await page.getByText("有一次提交等待确认",{exact:true}).waitFor();assert.equal(count("/api/ai/business-reports"),1);
  await page.getByRole("button",{name:"确认并重试同一次提交",exact:true}).click();await page.locator("#report-result").filter({hasText:"report-1"}).waitFor();assert.equal(requests.filter(r=>r.path==="/api/ai/business-reports")[1].raw,first);assert.equal(reportRows.length,1);assert.equal(reportRows[0].dryRun,false);
 });
 await check("late_detail_response_isolation",async()=>{
  evidence.unshift(item("late-A","合成迟到任务A","sealed"),item("latest-B","合成当前任务B","sealed"));detailDelay="late-A";
  await page.getByRole("button",{name:"刷新任务列表",exact:true}).click();await page.getByRole("button",{name:/合成迟到任务A/}).click();await page.getByRole("button",{name:/合成当前任务B/}).click();
  await page.getByRole("region",{name:"选中任务详情"}).getByText("合成当前任务B",{exact:true}).waitFor();await page.waitForTimeout(650);assert.equal(await page.getByRole("region",{name:"选中任务详情"}).getByText("合成迟到任务A",{exact:true}).count(),0);detailDelay="";
 });
 await check("legacy_manual_collection_controls",async()=>{
  const old=item("legacy-manual","合成历史手动采集");delete old.plan.collector;old.collection.status="manual";evidence.unshift(old);await page.getByRole("button",{name:"刷新任务列表",exact:true}).click();await page.getByRole("button",{name:/合成历史手动采集/}).click();await page.getByText("此历史任务使用手动采集模式",{exact:false}).waitFor();assert.equal(await page.getByRole("button",{name:"暂停后台采集",exact:true}).count(),0);assert.equal(await page.getByRole("button",{name:"恢复后台采集",exact:true}).count(),0);
  await page.getByRole("button",{name:"取消采集任务",exact:true}).click();await page.getByRole("region",{name:"选中任务详情"}).getByText("已取消",{exact:false}).waitFor();assert.equal(old.status,"cancelled");
 });
 await check("exact_erp_market_scope_editing",async()=>{
  await page.getByLabel("分析问题",{exact:true}).fill("精确范围测试");await page.getByLabel("开始日期",{exact:true}).fill("2026-09-01");await page.getByLabel("结束日期",{exact:true}).fill("2026-09-15");await page.getByLabel("店铺 1 精确名称",{exact:true}).fill("sku");
  await page.getByRole("button",{name:"添加 ERP 渠道",exact:true}).click();await page.getByLabel("店铺 1 ERP 渠道 1",{exact:true}).fill("current");await page.getByRole("button",{name:"添加市场条件（最多 7 项）",exact:true}).click();
  for(const [field,value]of Object.entries({平台:"京东",精确类目:"合成三级类目",精确范围:"POP",榜单维度:"SKU",精确价格带:"合成价格带"}))await page.getByLabel("市场 1 "+field,{exact:true}).fill(value);
  await page.getByRole("button",{name:"预览完整来源计划",exact:true}).click();await page.getByText("拟采集 4 / 48 个来源",{exact:false}).waitFor();const matrix=await page.getByRole("region",{name:"来源范围预览"}).innerText();assert.match(matrix,/店铺：sku/);assert.match(matrix,/ERP渠道：current/);assert.match(matrix,/类目：合成三级类目/);
  await page.getByRole("button",{name:"删除渠道 1",exact:true}).click();await page.getByRole("button",{name:"删除市场条件 1",exact:true}).click();assert.equal(await page.getByRole("region",{name:"来源范围预览"}).count(),0);
 });
 await check("late_preview_after_range_edit_is_ignored",async()=>{
  previewDelay=500;const started=page.waitForRequest(r=>r.url().endsWith("/business-plan/preview"));await page.getByRole("button",{name:"预览完整来源计划",exact:true}).click();await started;await page.getByLabel("店铺 1 精确名称",{exact:true}).fill("合成新范围");await page.getByRole("button",{name:"预览完整来源计划",exact:true}).click();await page.getByRole("region",{name:"来源范围预览"}).getByRole("cell",{name:/店铺：合成新范围/}).first().waitFor();await page.waitForTimeout(650);assert.equal(await page.getByRole("region",{name:"来源范围预览"}).getByRole("cell",{name:/店铺：sku/}).count(),0);
 });
 await check("nineteen_sources_three_windows_remain_collectable",async()=>{
  await page.getByLabel("分析问题",{exact:true}).fill("四店同比环比诊断");await page.getByLabel("开始日期",{exact:true}).fill("2026-09-01");await page.getByLabel("结束日期",{exact:true}).fill("2026-09-15");await page.getByLabel("店铺 1 精确名称",{exact:true}).fill("合成1");
  for(let i=2;i<=4;i++){await page.getByRole("button",{name:"添加店铺（最多 4 家）",exact:true}).click();await page.getByLabel(`店铺 ${i} 精确名称`,{exact:true}).fill("合成"+i);}
  await page.getByLabel("同比",{exact:true}).check();await page.getByLabel("环比",{exact:true}).check();await page.getByRole("button",{name:"添加市场条件（最多 7 项）",exact:true}).click();
  for(const [field,value]of Object.entries({平台:"京东",精确类目:"合成三级类目",精确范围:"POP",榜单维度:"SKU",精确价格带:"合成价格带"}))await page.getByLabel("市场 1 "+field,{exact:true}).fill(value);
  await page.getByRole("button",{name:"预览完整来源计划",exact:true}).click();await page.getByText("拟采集 19 / 48 个来源",{exact:false}).waitFor();assert.equal(await page.getByRole("region",{name:"来源范围预览"}).getByRole("cell",{name:"尚未取数",exact:true}).count(),19);await page.getByLabel("我已核对精确范围与限制，确认采集以上全部来源").check();assert.equal(await page.getByRole("button",{name:"确认范围并开始后台采集",exact:true}).isDisabled(),false);await page.getByRole("button",{name:"删除市场条件 1",exact:true}).click();
 });
 await check("over_capacity_keeps_all_sources",async()=>{
  for(const label of ["SKU 销售","SPU 销售","B 端销售"])for(const checkbox of await page.getByLabel(label,{exact:true}).all())await checkbox.check();
  await page.getByLabel("同比",{exact:true}).check();await page.getByLabel("环比",{exact:true}).check();await page.getByRole("button",{name:"预览完整来源计划",exact:true}).click();await page.getByText("拟采集 52 / 48 个来源",{exact:false}).waitFor();
  assert.equal(await page.getByRole("region",{name:"来源范围预览"}).getByRole("cell",{name:"尚未取数",exact:true}).count(),52);assert.match(await page.getByRole("region",{name:"来源范围预览"}).innerText(),/尚不可测算/);assert.equal(await page.getByRole("button",{name:"确认范围并开始后台采集",exact:true}).isDisabled(),true);
 });
 await check("mobile_no_page_overflow",async()=>{await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);await page.screenshot({path:path.join(directory,"workbench-mobile.png"),fullPage:true});});
 await check("paged_list_restore",async()=>{
  for(let i=0;i<12;i++)evidence.push(item("history-"+i,"合成历史任务"+i,"sealed"));await page.getByRole("button",{name:"刷新任务列表",exact:true}).click();await page.getByRole("button",{name:"下一页",exact:true}).click();await page.getByRole("button",{name:/合成历史任务11/}).waitFor();assert.equal(await page.getByRole("region",{name:"已有证据任务"}).getByRole("button",{name:/合成历史/}).count(),evidence.length-10);
 });
 await check("storage_failure_blocks_creation",async()=>{
  const blocked=await browser.newPage();await blocked.addInitScript(()=>{Storage.prototype.setItem=function(){throw new DOMException("storage disabled","QuotaExceededError");};});await blocked.goto(url);await blocked.getByText("浏览器无法可靠保存提交",{exact:false}).waitFor();assert.equal(await blocked.getByLabel("分析问题",{exact:true}).isDisabled(),true);await blocked.close();
 });
 await check("principal_storage_isolation",async()=>{
  await page.reload();await page.getByLabel("分析问题",{exact:true}).fill("账号A待确认问题");await page.getByLabel("开始日期",{exact:true}).fill("2026-09-01");await page.getByLabel("结束日期",{exact:true}).fill("2026-09-15");await page.getByLabel("店铺 1 精确名称",{exact:true}).fill("账号A合成店铺");await page.getByRole("button",{name:"预览完整来源计划",exact:true}).click();await page.getByText("拟采集 2 / 48 个来源",{exact:false}).waitFor();await page.getByLabel("我已核对精确范围与限制，确认采集以上全部来源").check();dropEvidence=true;await page.getByRole("button",{name:"确认范围并开始后台采集",exact:true}).click();await page.getByTestId("write-error").waitFor();
  const creates=count("/api/ai/business-evidence");principal=principalB;await page.getByRole("button",{name:"刷新任务列表",exact:true}).click();await page.getByTestId("write-error").filter({hasText:"账号已变化"}).waitFor();assert.equal(await page.getByText("账号A待确认问题",{exact:true}).count(),0);assert.equal(await page.getByText("有一次提交等待确认",{exact:true}).count(),0);assert.equal(await page.getByLabel("分析问题",{exact:true}).inputValue(),"");assert.equal(count("/api/ai/business-evidence"),creates);assert.equal(await page.evaluate(key=>Boolean(sessionStorage.getItem("ai-business-workbench-pending-v1:"+key)),principalA),true);
 });
 await check("principal_changes_between_preflight_and_post",async()=>{
  await page.getByLabel("分析问题",{exact:true}).fill("账号切换边界");await page.getByLabel("开始日期",{exact:true}).fill("2026-09-01");await page.getByLabel("结束日期",{exact:true}).fill("2026-09-15");await page.getByLabel("店铺 1 精确名称",{exact:true}).fill("合成账号B店铺");await page.getByRole("button",{name:"预览完整来源计划",exact:true}).click();await page.getByText("拟采集 2 / 48 个来源",{exact:false}).waitFor();
  await page.getByLabel("我已核对精确范围与限制，确认采集以上全部来源").check();flipAfterPreflight=true;const before=evidence.length;
  await page.getByRole("button",{name:"确认范围并开始后台采集",exact:true}).click();await page.getByTestId("write-error").filter({hasText:"账号绑定不一致"}).waitFor();assert.equal(evidence.length,before);assert.equal(await page.getByRole("region",{name:"选中任务详情"}).count(),0);assert.equal(requests.filter(r=>r.path==="/api/ai/business-evidence"&&r.method==="POST").at(-1).body.expectedPrincipalKey,principalB);
 });
 await check("legacy_pending_replays_exact_v1_body_without_upgrade",async()=>{
  const oldPage=await browser.newPage();await oldPage.goto(url);
  const legacy=JSON.stringify({clientRequestId:"legacy-saved-submit",expectedPrincipalKey:principalA,sources:[source()],collectionMode:"bulk",autoCollect:true,analysisRequest:{schemaVersion:"business-analysis-request-v1",question:"旧版原样恢复",requestedDimensions:["shop"],requestedWindows:["current"]}});
  await oldPage.evaluate(({key,bodyJson})=>sessionStorage.setItem("ai-business-workbench-pending-v1:"+key,JSON.stringify({schemaVersion:1,principalKey:key,kind:"evidence",bodyJson,label:"旧版原样恢复",createdAt:"2026-09-16T00:00:00Z",outcome:"unknown"})),{key:principalA,bodyJson:legacy});
  await oldPage.reload();await oldPage.getByText("有一次提交等待确认",{exact:true}).waitFor();const before=count("/api/ai/business-evidence");await oldPage.getByRole("button",{name:"确认并重试同一次提交",exact:true}).click();await oldPage.getByRole("region",{name:"选中任务详情"}).getByText("旧版原样恢复",{exact:true}).waitFor();assert.equal(count("/api/ai/business-evidence"),before+1);assert.equal(requests.filter(r=>r.path==="/api/ai/business-evidence"&&r.method==="POST").at(-1).raw,legacy);await oldPage.close();
 });
 assert.deepEqual(pageErrors,[]);await fs.writeFile(path.join(directory,"evidence.json"),JSON.stringify({passed:true,cases:results,apiRequests:requests.length,evidenceCreates:count("/api/ai/business-evidence"),reportCreates:count("/api/ai/business-reports"),pageErrors,syntheticOnly:true},null,2));
} catch(error){await fs.writeFile(path.join(directory,"failure.json"),JSON.stringify({error:String(error),requests,pageErrors,dom:await page.locator("body").innerText(),html:await page.locator("body").innerHTML()},null,2));throw error;} finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
