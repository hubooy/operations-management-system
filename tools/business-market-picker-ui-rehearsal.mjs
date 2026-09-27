// Real component + helper in headless Chrome, synthetic loopback metadata only.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const root=process.cwd(), directory=path.resolve(process.argv[2]||".runtime/business-market-picker-ui");
assert.ok(directory.startsWith(root+path.sep));
await fs.mkdir(directory,{recursive:true});
const keyA="a".repeat(64),keyB="b".repeat(64);
const entry=path.join(directory,"entry.tsx");
await fs.writeFile(entry,`import React,{useState}from'react';import{createRoot}from'react-dom/client';
import Picker from ${JSON.stringify(path.join(root,"app/ai-business-market-picker.tsx"))};
import ${JSON.stringify(path.join(root,"app/ai-business-workbench.css"))};
function App(){const[key,setKey]=useState('${keyA}'),[disabled,setDisabled]=useState(false),[mounted,setMounted]=useState(true),[events,setEvents]=useState([]),[mismatch,setMismatch]=useState(0),[submits,setSubmits]=useState(0);
return <main className="business-workbench"><button type="button" id="actor" onClick={()=>setKey(k=>k==='${keyA}'?'${keyB}':'${keyA}')}>切换模拟账号</button><button type="button" id="disable" onClick={()=>setDisabled(v=>!v)}>切换禁用</button><button type="button" id="mount" onClick={()=>setMounted(v=>!v)}>切换挂载</button><label><input id="reject" type="checkbox"/>模拟父级拒绝</label><form onSubmit={e=>{e.preventDefault();setSubmits(v=>v+1);}}><input aria-label="父级开始日期" type="date" defaultValue="2026-09-01"/><fieldset disabled={disabled}>{mounted&&<Picker principalKey={key} disabled={disabled} onSelect={v=>{if(document.getElementById('reject').checked)return false;setEvents(a=>[...a,v]);return true;}} onIdentityMismatch={()=>setMismatch(v=>v+1)}/>}</fieldset></form><output id="events">{JSON.stringify(events)}</output><output id="mismatches">{mismatch}</output><output id="submits">{submits}</output></main>};createRoot(document.getElementById('root')).render(<App/>);`);
await build({entryPoints:[entry],bundle:true,outfile:path.join(directory,"app.js"),format:"iife",platform:"browser",jsx:"automatic",logLevel:"silent",define:{"process.env.NODE_ENV":'"production"'},tsconfig:path.join(root,"tsconfig.json")});
const canonical=value=>Array.isArray(value)?`[${value.map(canonical).join(",")}]`:value&&typeof value==="object"?`{${Object.keys(value).sort().map(k=>`${JSON.stringify(k)}:${canonical(value[k])}`).join(",")}}`:JSON.stringify(value);
const sha=value=>createHash("sha256").update(canonical(value),"utf8").digest("hex");
let actor=keyA, mode="normal", count=23, delay=false, started=false, finished=false;
const requests=[],results=[],pageErrors=[],serverErrors=[],external=[];
function fixture(query,cursor){
  const revision=mode==="revision"?"8:abcdef123456":"7:abcdef123456";
  const generation=mode==="generation"?"d".repeat(32):"c".repeat(32),directoryDigest=mode==="directory"?"f".repeat(64):"e".repeat(64);
  let items=Array.from({length:count},(_,i)=>{const identity={platform:"京东",category:`合成类目${String(i).padStart(4,"0")}`,scope:"POP全部渠道",rankingDimension:"SKU",priceBandFilter:"原价带 100-200"};return {optionKey:sha({domain:"market",identity}),identity,source:"market_daily_top",sourceDataset:"market_daily_top",
    dateMetadata:{kind:"published_import_envelope",firstDate:"2026-08-01",lastDate:"2026-08-03",snapshotDate:null,coverageVerified:false},
    provenance:{kind:"completed_import_metadata",revision,generation,directoryDigest,meaning:"historically_published_not_current_fact_coverage"}};});
  items=items.filter(item=>Object.entries(query).every(([k,v])=>k==="q"?Object.values(item.identity).some(value=>value.includes(v)):item.identity[k]===v));
  const offset=cursor?Number(cursor.split(":")[0].slice(1)):0,total=items.length;
  items=items.slice(offset,offset+20);
  const page={schemaVersion:"business-analysis-options-v1",domain:"market",authorityVerified:true,revision,directoryGeneration:generation,directoryDigest,query,queryDigest:sha(query),items,
    pagination:{returned:items.length,limit:20,hasMore:offset+items.length<total,nextCursor:offset+items.length<total?`p${offset+items.length}:1:synthetic`:null},
    limitations:["仅合成历史导入包络，不证明当前覆盖。"]};
  return {...page,pageDigest:sha(page)};
}
const server=http.createServer(async(req,res)=>{
  try{
    const url=new URL(req.url,"http://localhost");
    const send=(body,status=200)=>{res.writeHead(status,{"content-type":"application/json","cache-control":"no-store"});res.end(JSON.stringify(body));};
    if(url.pathname.startsWith("/api/")){
      requests.push({method:req.method,path:url.pathname,query:url.search});assert.equal(req.method,"GET");assert.equal(url.pathname,"/api/ai/business-plan/market-options");
      if(url.searchParams.get("expectedPrincipalKey")!==actor)return send({code:"principal_mismatch",error:"账号已变化"},403);
      if(mode==="conflict"&&url.searchParams.has("cursor"))return send({code:"options_revision_changed"},409);
      const query=Object.fromEntries([...url.searchParams].filter(([key])=>!["cursor","expectedPrincipalKey"].includes(key)));
      const page=fixture(query,url.searchParams.get("cursor"));
      if(mode==="bad")page.pageDigest="0".repeat(64);
      if(mode==="denied")return send({code:"access_denied",error:"已撤权"},403);
      if(mode==="not-ready")return send({code:"options_not_ready",error:"市场目录尚未初始化"},503);
      const value={schemaVersion:"business-market-options-response-v1",principalKey:mode==="wrong-key"?keyB:actor,page};
      if(delay){delay=false;started=true;finished=false;await new Promise(resolve=>setTimeout(resolve,650));finished=true;}
      return send(value);
    }
    if(url.pathname==="/app.js"||url.pathname==="/app.css"){res.setHeader("content-type",url.pathname.endsWith("css")?"text/css":"text/javascript");return res.end(await fs.readFile(path.join(directory,url.pathname.slice(1))));}
    res.setHeader("content-type","text/html;charset=utf-8");res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"></head><body style="margin:8px;font-family:Arial"><div id="root"></div><script src="/app.js"></script></body></html>');
  }catch(error){serverErrors.push(String(error));res.writeHead(500);res.end();}
});
await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
const origin=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",headless:true});
const page=await browser.newPage({viewport:{width:1280,height:900}});
page.on("pageerror",error=>pageErrors.push(String(error)));
await page.route("**/*",route=>{if(new URL(route.request().url()).origin!==origin){external.push(route.request().url());return route.abort();}return route.continue();});
const picker=()=>page.getByRole("region",{name:"选择市场分析来源",exact:true});
const add=()=>picker().getByRole("button",{name:/^添加市场来源 /});
const events=async()=>JSON.parse(await page.locator("#events").innerText());
const ready=async(expected=20)=>{await page.waitForFunction(n=>[...document.querySelectorAll('button[aria-label^="添加市场来源 "]')].length===n,expected);await page.getByRole("button",{name:"取消市场来源读取",exact:true}).waitFor({state:"detached"});};
const read=async()=>{await picker().getByRole("button",{name:"从首页重读",exact:true}).click();};
const waitDelay=async()=>{for(let i=0;!started&&i<200;i++)await new Promise(resolve=>setTimeout(resolve,10));assert.equal(started,true);};
const waitFinished=async()=>{for(let i=0;!finished&&i<200;i++)await new Promise(resolve=>setTimeout(resolve,10));assert.equal(finished,true);await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));};
const reload=async()=>{actor=keyA;mode="normal";count=23;delay=false;await page.goto(origin);await ready();};
const check=async(name,run)=>{await run();results.push({name,passed:true});console.log(JSON.stringify(results.at(-1)));};
try{
  await reload();
  await check("manual_selection_revalidates_identity_and_never_changes_dates_or_submits",async()=>{
    assert.equal((await events()).length,0);const before=requests.length;await add().first().click();await page.waitForFunction(()=>JSON.parse(document.querySelector('#events').textContent).length===1);
    assert.equal(requests.length,before+1);assert.equal((await events())[0].identity.category,"合成类目0000");assert.equal((await events())[0].identity.rankingDimension,"SKU");
    assert.deepEqual(Object.keys((await events())[0]).sort(),["principalKey","revision","directoryGeneration","directoryDigest","queryDigest","optionKey","identity"].sort());
    assert.equal((await events())[0].identity.priceBandFilter,"原价带 100-200");assert.equal((await events())[0].directoryGeneration,"c".repeat(32));
    assert.equal(await page.getByLabel("父级开始日期",{exact:true}).inputValue(),"2026-09-01");assert.equal(await page.locator("#submits").innerText(),"0");
  });
  await check("twenty_then_three_with_previous_and_exact_filters",async()=>{
    await picker().getByRole("button",{name:"下一页市场来源",exact:true}).click();await ready(3);assert.match(await add().first().getAttribute("aria-label"),/0020/);
    await picker().getByRole("button",{name:"上一页市场来源",exact:true}).click();await ready();assert.match(await add().first().getAttribute("aria-label"),/0000/);
    assert.equal(await picker().getByLabel("市场平台",{exact:true}).inputValue(),"京东");await picker().getByLabel("市场精确类目",{exact:true}).fill("合成类目0022");await picker().getByLabel("市场榜单范围",{exact:true}).fill("POP全部渠道");await picker().getByLabel("市场排名维度",{exact:true}).selectOption("SKU");await picker().getByLabel("市场原始价格带",{exact:true}).fill("原价带 100-200");await read();await ready(1);
    assert.match(requests.at(-1).query,/rankingDimension=SKU/);assert.match(await add().first().getAttribute("aria-label"),/0022/);
    await picker().getByLabel("市场名称搜索",{exact:true}).fill("无此名称");await read();await ready(0);await picker().getByText("当前筛选没有已发布来源；这不等于没有经营活动。",{exact:true}).waitFor();
  });
  await check("parent_rejection_never_claims_added",async()=>{
    await reload();await page.locator("#reject").check();await add().first().click();await picker().getByText("未添加，请查看分析范围中的提示。",{exact:true}).waitFor();assert.equal((await events()).length,0);await page.locator("#reject").uncheck();
  });
  await check("bad_page_erases_candidates_without_selection",async()=>{
    await reload();mode="bad";await read();await picker().getByRole("alert").waitFor();assert.equal(await add().count(),0);assert.equal((await events()).length,0);mode="normal";await read();await ready();
  });
  await check("revision_409_requires_explicit_first_page_reread",async()=>{
    mode="conflict";await picker().getByRole("button",{name:"下一页市场来源",exact:true}).click();await picker().getByRole("alert").filter({hasText:"从首页重读"}).waitFor();assert.equal(await add().count(),0);
    mode="normal";await read();await ready();assert.equal(new URLSearchParams(requests.at(-1).query).has("cursor"),false);
  });
  await check("changed_account_before_add_clears_and_notifies",async()=>{
    actor=keyB;await add().first().click();await page.waitForFunction(()=>document.querySelector('#mismatches').textContent==='1');assert.equal(await add().count(),0);assert.equal((await events()).length,0);
    await page.locator("#actor").click();await ready();assert.equal(new URLSearchParams(requests.at(-1).query).get("expectedPrincipalKey"),keyB);
  });
  await check("mismatched_success_wrapper_is_also_rejected",async()=>{
    await reload();mode="wrong-key";await read();await page.waitForFunction(()=>document.querySelector('#mismatches').textContent==='1');assert.equal(await add().count(),0);assert.equal((await events()).length,0);
  });
  await check("changed_revision_before_selection_never_emits",async()=>{
    await reload();mode="revision";await add().first().click();await picker().getByRole("alert").waitFor();assert.equal(await add().count(),0);assert.equal((await events()).length,0);
  });
  await check("generation_and_digest_changes_before_selection_never_emit",async()=>{
    for(const change of ["generation","directory"]){await reload();mode=change;await add().first().click();await picker().getByRole("alert").waitFor();assert.equal(await add().count(),0);assert.equal((await events()).length,0);}
  });
  await check("revoked_permission_notifies_but_not_ready_never_claims_empty",async()=>{
    await reload();mode="denied";await read();await page.waitForFunction(()=>document.querySelector('#mismatches').textContent==='1');assert.equal(await add().count(),0);
    await reload();mode="not-ready";await read();await picker().getByRole("alert").filter({hasText:"尚未初始化"}).waitFor();assert.equal(await add().count(),0);assert.equal(await picker().getByText("当前筛选没有已发布来源；这不等于没有经营活动。",{exact:true}).count(),0);
  });
  await check("late_old_filter_response_cannot_replace_new_page",async()=>{
    await reload();delay=true;started=false;await read();await waitDelay();await picker().getByLabel("市场名称搜索",{exact:true}).fill("合成类目0022");await read();await ready(1);await waitFinished();assert.equal(await add().count(),1);assert.match(await add().first().getAttribute("aria-label"),/0022/);
  });
  await check("cancel_pending_selection_never_emits",async()=>{
    await reload();delay=true;started=false;await add().first().click();await waitDelay();await picker().getByRole("button",{name:"取消市场来源读取",exact:true}).click();await waitFinished();assert.equal((await events()).length,0);assert.equal(await add().count(),0);
  });
  await check("disabled_and_unmounted_late_selection_never_emits",async()=>{
    await read();await ready();delay=true;started=false;await add().first().click();await waitDelay();await page.locator("#disable").click();await waitFinished();assert.equal((await events()).length,0);assert.equal(await add().count(),0);
    await page.locator("#disable").click();await ready();delay=true;started=false;await add().first().click();await waitDelay();await page.locator("#mount").click();await waitFinished();assert.equal((await events()).length,0);
    await page.locator("#mount").click();await ready();
  });
  await check("changed_principal_prop_drops_late_selection",async()=>{
    await reload();delay=true;started=false;await add().first().click();await waitDelay();actor=keyB;await page.locator("#actor").click();await ready();await waitFinished();assert.equal((await events()).length,0);
    await reload();
  });
  await check("hundred_page_bound_reports_remaining_instead_of_clipping",async()=>{
    count=2001;await read();await ready();for(let n=2;n<=100;n++){await picker().getByRole("button",{name:"下一页市场来源",exact:true}).click();await picker().getByText(`第 ${n} 页 · 本页 20 个来源 · 后面还有来源`,{exact:true}).waitFor();}
    assert.equal(await picker().getByRole("button",{name:"下一页市场来源",exact:true}).isDisabled(),true);await picker().getByText("已浏览 100 页，仍有来源未展示。请缩小筛选后从首页重读。",{exact:true}).waitFor();assert.equal(await add().count(),20);
  });
  await check("desktop_mobile_readable_without_overflow",async()=>{
    count=23;await read();await ready();await page.screenshot({path:path.join(directory,"desktop.png"),fullPage:true});await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:path.join(directory,"mobile.png"),fullPage:true});
  });
  assert.deepEqual(pageErrors,[]);assert.deepEqual(serverErrors,[]);assert.deepEqual(external,[]);assert.ok(requests.every(r=>r.method==="GET"));
  await fs.writeFile(path.join(directory,"evidence.json"),JSON.stringify({passed:true,syntheticOnly:true,cases:results,requestCount:requests.length,pageErrors,serverErrors,external},null,2));
}catch(error){await fs.writeFile(path.join(directory,"failure.json"),JSON.stringify({error:String(error),results,pageErrors,serverErrors,external,requests,dom:await page.locator("body").innerText()},null,2));throw error;}
finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
