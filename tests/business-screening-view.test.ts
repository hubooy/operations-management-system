import assert from "node:assert/strict";
import test from "node:test";
import { allowReportDetailBytes, isReportDetailPath, projectScreening, screeningRoles, SCREENING_PROFILE, ORDINARY_RESPONSE_BYTES, REPORT_DETAIL_BYTES } from "../lib/ai/business-screening-view";
import { fetchBoundedJson } from "../lib/ai/bounded-fetch";
import { requestDjangoAi } from "../lib/django/ai-service";

function fixture() {
  const period={startDate:"2026-08-01",endDate:"2026-08-02"};
  const screening={schemaVersion:"business-screening-content-v1",reference:{id:"scan",reportId:"report"}, authority:{entityDailyCoverageVerified:false,tableCount:1,partitionCount:1,requestedCoveragePlanned:true,requestedTablesExecutedComplete:true,requestedSourceDateCoverageComplete:false},sources:[{key:"source",domain:"netshop",query:{platform:"京东",shop:"sku",dataset:"promotion",window:"current",...period,ownerEmail:"HIDDEN"}}],tableBindings:[{tableKey:"table",sourceKey:"source",mode:"native",dimension:"sku"}],coverage:[{kind:"family",value:{domain:"netshop",family:"promotion",query:{shop:"sku"}}},{kind:"requested",value:{dimension:"sku",window:"current",mode:"native",status:"executed",sourceKey:"source"}},{kind:"table",value:{tableKey:"table",scannedRows:2,expectedRows:2,sourceCoverage:{status:"missing_dates",presentDates:["2026-08-01"],missingDates:["2026-08-02"]},baselineCoverage:null,sourcePeriod:period,dateCoverageComparable:false}},{kind:"partition",value:{tableKey:"table",ruleId:"rule",scannedRows:2,matchedRows:2,retainedRows:1,omittedRows:1,eligibleRows:2,ineligibleRows:0,ineligibleReasons:{},supported:true,unavailableReason:null}}],readProofs:Object.fromEntries(screeningRoles.map(role=>[role,{role,screeningId:"scan",package:{complete:true,pages:2,expectedPages:2},budget:{required:false,started:false,complete:true}}])),limitations:["并非全量明细"],sourceInfos:{ownerEmail:"HIDDEN"}};
  const professionals=Object.fromEntries(screeningRoles.slice(0,3).map(role=>[role,{schemaVersion:"business-screening-diagnosis-v1",reportId:"report",role,summary:"合成专业结论",findings:[{title:"数据缺口",kind:"gap",explanation:"缺日不可当作零销售",facts:[],action:null}]}]));
  return {screening,professionals};
}
test("screening projects every coverage record, exact identities and omitted candidates without internal data",()=>{
  const f=fixture(),p=projectScreening(f.screening,f.professionals,"report");
  assert.equal(p.sources.length,1);assert.match(p.sources[0].details,/sku/);assert.doesNotMatch(p.sources[0].details,/SKU/);
  assert.equal(p.partitions[0].omitted,1);assert.equal(p.tables.length,1);assert.match(p.tables[0].details,/2026-08-02/);assert.equal(p.dates,false);assert.equal(p.proofs.length,5);assert.equal(p.professionals.length,3);assert.doesNotMatch(JSON.stringify(p),/HIDDEN|ownerEmail/);
});
test("screening rejects incomplete, wrong-report, duplicate identities and numeric mixed types",()=>{
  for(const change of [(f:ReturnType<typeof fixture>)=>{f.screening.reference.reportId="other";},(f:ReturnType<typeof fixture>)=>{f.screening.coverage.pop();},(f:ReturnType<typeof fixture>)=>{f.screening.sources.push(f.screening.sources[0]);},(f:ReturnType<typeof fixture>)=>{f.screening.tableBindings=[];},(f:ReturnType<typeof fixture>)=>{Object.assign(f.screening.authority,{tableCount:true});},(f:ReturnType<typeof fixture>)=>{delete (f.screening.readProofs as Record<string,unknown>).commerce;}]){const f=fixture();change(f);assert.throws(()=>projectScreening(f.screening,f.professionals,"report"));}
});
test("only exact new-profile report detail can use five MiB successful response",()=>{
  const dto={snapshot:{schemaVersion:"business-report-v1",executionProfile:SCREENING_PROFILE}};
  assert.equal(isReportDetailPath("/api/ai/reports/r_1"),true);
  for(const suffix of ["/content","/files","/","?page=1"])assert.equal(isReportDetailPath("/api/ai/reports/r_1"+suffix),false);
  assert.equal(allowReportDetailBytes(dto,REPORT_DETAIL_BYTES,true),true);
  for(const [data,size,ok]of [[dto,REPORT_DETAIL_BYTES+1,true],[dto,ORDINARY_RESPONSE_BYTES+1,false],[{},ORDINARY_RESPONSE_BYTES+1,true],[{snapshot:{executionProfile:SCREENING_PROFILE}},ORDINARY_RESPONSE_BYTES+1,true],[dto,-1,true]] as const)assert.equal(allowReportDetailBytes(data,size,ok),false);
});
test("bounded JSON reports actual UTF8 bytes including whitespace and malformed bodies",async()=>{
  for(const raw of [' {"text":"中文😀"} \n','', ' {invalid 中文} ']){
    const result=await fetchBoundedJson({url:"http://fixture.invalid",init:{},timeoutMs:1000,fetcher:async()=>new Response(raw)});
    assert.equal(result.responseBytes,Buffer.byteLength(raw));
  }
});
const environment={TERUISI_DJANGO_INTERNAL_SECRET:"Isolated-hmac-transport-secret-0123456789",TERUISI_DJANGO_AI_READER_BASE_URL:"http://127.0.0.1:18111",TERUISI_DJANGO_AI_WRITER_BASE_URL:"http://127.0.0.1:18112"};
const principal={email:"owner@example.invalid",displayName:"合成",role:"admin" as const,scope:null};
const large=(profile:string,status=200,padding=ORDINARY_RESPONSE_BYTES)=>new Response(JSON.stringify({snapshot:{schemaVersion:"business-report-v1",executionProfile:profile}})+" ".repeat(padding),{status,headers:{"x-ai-revision":"42","content-type":"application/json"}});
test("writer read relay allows new screening detail only and retains byte accounting on chunked whitespace",async()=>{
  let called:Request|undefined;
  const result=await requestDjangoAi(principal,{path:"/api/ai/reports/report"},{environment,fetchImpl:async(input,init)=>{called=new Request(input,init);return large(SCREENING_PROFILE);}});
  assert.ok(result);assert.equal(new URL(called!.url).port,"18112");assert.ok(called!.headers.has("x-teruisi-signature"));
  for(const [path,profile,status,padding] of [["/api/ai/reports/report","business-agent-reference-v2",200,ORDINARY_RESPONSE_BYTES],["/api/ai/reports/report",SCREENING_PROFILE,500,ORDINARY_RESPONSE_BYTES],["/api/ai/reports/report",SCREENING_PROFILE,200,REPORT_DETAIL_BYTES],["/api/ai/reports",SCREENING_PROFILE,200,ORDINARY_RESPONSE_BYTES]] as const){
    await assert.rejects(()=>requestDjangoAi(principal,{path},{environment,fetchImpl:async()=>large(profile,status,padding)}));
  }
});

test("professional facts use Chinese names, preserve unknown metrics and do not label change ratios as cents",()=>{
  const f=fixture();
  const facts=[{metric:"spendCents",field:"value",value:100,entity:{skuId:"SKU-A"},reference:{}},{metric:"spendCents",field:"changeRate",value:-0.1,reference:{}},{metric:"future_metric",field:"difference",value:0,reference:{}}];
  Object.assign(f.professionals.commerce.findings[0],{facts});
  const p=projectScreening(f.screening,f.professionals,"report");
  assert.match(p.professionals[0].findings[0].facts[0],/SKU-A；推广花费 · 本期值：100 分/);
  assert.match(p.professionals[0].findings[0].facts[1],/变化比例：-0.1（比例/);assert.doesNotMatch(p.professionals[0].findings[0].facts[1],/-0.1 分/);
  assert.match(p.professionals[0].findings[0].facts[2],/future_metric · 差额：0/);
});
