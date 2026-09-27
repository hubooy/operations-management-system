import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp,mkdir,readFile,writeFile} from "node:fs/promises";
import path from "node:path";
import {tmpdir} from "node:os";
import {createHash} from "node:crypto";
import {jackyunSalesPeriod,rollingJackyunSalesStartDate} from "../lib/jackyun/sales-period";
import {buildApiParameters,type ApiTemplates} from "../lib/jackyun/api-plan";
import {validateDirectExportPayload} from "../lib/jackyun/direct-export";
import {runJackyunExportFirstAction} from "../tools/jackyun-export-first-pipeline";
import {runApiExports} from "../tools/jackyun-api-export";
import {runSalesImport} from "../tools/sales-import-runner";
import {createXlsxWorkbookBytes} from "../lib/imports/xlsx-write";
import {parseXlsxFirstSheet} from "../lib/imports/xlsx";
import {createJackyunInputContractHash,type JackyunInputContract,jackyunCaptureDate} from "../lib/jackyun/run-contract";
import {verifyPublishedJackyunBatches} from "../tools/jackyun-n8n-pipeline";
import {inspectPreflightClosure,jackyunWorkflowId} from "../lib/jackyun/preflight-recovery";

test("rolling sales spans exactly 45 days across month, year and leap-day boundaries",()=>{
  for(const [end,start] of [["2026-09-22","2026-08-09"],["2026-08-31","2026-07-18"],["2026-02-28","2026-01-15"],["2024-03-01","2024-01-17"],["2026-01-01","2025-11-18"],["2026-03-31","2026-02-15"]]){
    assert.equal(rollingJackyunSalesStartDate(end),start);
    assert.deepEqual(jackyunSalesPeriod(end,start),{startDate:start,endDate:end});
    assert.equal((Date.parse(end)-Date.parse(start))/86400000+1,45);
  }
  assert.equal(jackyunSalesPeriod("2026-08-31").startDate,"2026-08-01");
  for(const date of ["2026-02-29","2026-02-30","2026-13-01","2026-9-22",""]){assert.throws(()=>rollingJackyunSalesStartDate(date));}
  for(const start of ["2026-08-08","2026-08-10","2026-09-01"]){assert.throws(()=>jackyunSalesPeriod("2026-09-22",start));}
});

test("API count and export bind the same rolling shipment scope and reject monthly payload substitution",async()=>{
  const t=JSON.parse(await readFile(new URL('../config/jackyun-api-templates.json',import.meta.url),'utf8')) as ApiTemplates;
  const scope={warehouseIds:['1'],ownerId:'1',permissionSha256:t.permissionFieldsSha256,observedAt:'2026-09-23T00:00:00Z'};
  const p=buildApiParameters('sales',t.modules.sales,scope,'2026-09-22','2026-08-09');
  const filter=JSON.parse(p.query.jsonStr);assert.deepEqual(filter,JSON.parse(p.data.conditionJson).filterOrderDetailDto);
  assert.equal(filter.timeBegin,'2026-08-09 00:00:00');assert.equal(filter.timeEnd,'2026-09-22 23:59:59');assert.equal(String(filter.timeType),'4');
  const data: Record<string,string>={...p.data,exportTotal:'10'};
  assert.doesNotThrow(()=>validateDirectExportPayload('sales',new URLSearchParams(data).toString(),p.moduleCode,'2026-09-22','2026-08-09'));
  assert.throws(()=>validateDirectExportPayload('sales',new URLSearchParams(data).toString(),p.moduleCode,'2026-09-22'));
  const altered=JSON.parse(data.conditionJson);altered.filterOrderDetailDto.timeBegin='2026-09-01 00:00:00';
  assert.throws(()=>validateDirectExportPayload('sales',new URLSearchParams({...data,conditionJson:JSON.stringify(altered)}).toString(),p.moduleCode,'2026-09-22','2026-08-09'));
});

test("fresh API plans freeze 45 days at Shanghai midnight while legacy plans retain monthly meaning",async()=>{
  for(const [now,end,start] of [['2026-09-22T15:59:59Z','2026-09-21','2026-08-08'],['2026-09-22T16:00:00Z','2026-09-22','2026-08-09']]){
    const root=await mkdtemp(path.join(tmpdir(),'rolling-plan-'));await mkdir(path.join(root,'config'));
    await writeFile(path.join(root,'config/jackyun-export-first-policy.json'),JSON.stringify({version:'2026-09-06.export-first.1',browser:{downloadDirectory:path.join(root,'downloads'),controller:{profileDirectory:path.join(root,'profile')}}}));
    const deps={root,lockDirectory:path.join(root,'lock'),now:()=>new Date(now),profileReady:async()=>true,request:(async()=>new Response('{}')) as typeof fetch};
    const result=await runJackyunExportFirstAction('plan-api','555',deps);assert.equal(result.salesStartDate,start);assert.equal(result.salesEndDate,end);
    const p=path.join(root,'outputs/jackyun-export-first/n8n-export-first-555.json');const plan=JSON.parse(await readFile(p,'utf8'));assert.equal(plan.version,2);
    const evidence={executionId:'555',workflowId:jackyunWorkflowId,status:'error',startedAt:plan.createdAt,stoppedAt:new Date(Date.parse(now)+1000).toISOString(),retrySuccessId:null,
      lastNode:'B·接口校验与五表下载',runNodes:['手动运行','领取共享 helper','helper 领取成功？','A·固定采集日和销售日期','B·接口校验与五表下载'],
      error:'API_LOGIN_PAGE_NOT_UNIQUE',httpCode:'500',requestUrl:'http://127.0.0.1:5791/jackyun/export-first/export-all',executionDataSha256:'a'.repeat(64),activeExecutions:0};
    assert.equal((await inspectPreflightClosure(root,'555',evidence,new Date(Date.parse(now)+2000).toISOString())).reason,'verified_api_page_selection_without_business_effects');
    assert.equal((await runJackyunExportFirstAction('plan-api','555',{...deps,now:()=>new Date('2026-09-24T00:00:00Z')})).salesStartDate,start);
    await assert.rejects(runJackyunExportFirstAction('export-all','555',{...deps,now:()=>new Date('2026-09-24T00:00:00Z')}),/跨日/);
    await writeFile(p,JSON.stringify({...plan,salesStartDate:undefined}));await assert.rejects(runJackyunExportFirstAction('plan-api','555',deps),/版本/);
    await writeFile(p,JSON.stringify({...plan,version:1,salesStartDate:undefined}));assert.equal((await runJackyunExportFirstAction('plan-api','555',deps)).salesStartDate,end.slice(0,8)+'01');
  }
});

test("a controller from another sales range cannot resume, before any request",async()=>{
  const root=await mkdtemp(path.join(tmpdir(),'rolling-controller-')),runId='range-change';await mkdir(path.join(root,runId));
  const runDate=jackyunCaptureDate(new Date().toISOString()),end=new Date(Date.parse(runDate+'T00:00:00Z')-86400000).toISOString().slice(0,10);
  const t=JSON.parse(await readFile(new URL('../config/jackyun-api-templates.json',import.meta.url),'utf8'));
  await writeFile(path.join(root,runId,'api-controller-state.json'),JSON.stringify({version:1,runId,transport:'session_api_v1',runDate,asOfDate:end,templateSha256:createHash('sha256').update(JSON.stringify(t)).digest('hex'),modules:{}}));
  await assert.rejects(runApiExports({runId,runDate,asOfDate:end,salesStartDate:rollingJackyunSalesStartDate(end),outputRoot:root,eventRoot:path.join(root,'events'),downloadDirectory:path.join(root,'downloads')},{http:{request:async()=>{throw Error('must not request')}} as never,tenantId:'fixture'}),/BINDING_CHANGED/);
});

test("new start date enters the input digest without reinterpreting legacy hashes",()=>{
  const c={runId:'test',policyVersion:'p',module:'sales',rawSha256:'a'.repeat(64),asOfDate:'2026-09-22',expectedSourceRows:1,exportStart:'now',downloadEventAt:'now',downloadProvenance:{},baseUrl:'http://localhost:3000'} as unknown as JackyunInputContract;
  const old=createJackyunInputContractHash(c),rolling=createJackyunInputContractHash({...c,salesStartDate:'2026-08-09'});assert.notEqual(old,rolling);
  assert.equal(old,createJackyunInputContractHash({...c,salesStartDate:undefined}));
  assert.throws(()=>createJackyunInputContractHash({...c,salesStartDate:'2026-09-01'}));
  assert.throws(()=>createJackyunInputContractHash({...c,module:'products',salesStartDate:'2026-08-09'}));
});

test("sales preprocessing retains both rolling edges and prior-month rows, excluding outside and current day",async()=>{
  const root=await mkdtemp(path.join(tmpdir(),'rolling-workbook-'));
  const header=['网店订单号','销售渠道','发货仓库','货品编号','货品名称','数量','下单时间','发货时间','货品成本','分摊后单价','分摊后金额','费用分摊','毛利'];
  const rows=['2026-08-08','2026-08-09','2026-08-31','2026-09-22','2026-09-23'].map((d,i)=>['ORDER-'+i,'京东-志高切肉机旗舰店（志高迈德豪）','主仓','SKU-1','测试货品',1,d+' 09:00:00',d+' 10:00:00',0,100,100,5,0]);
  const raw=createXlsxWorkbookBytes([{name:'sales',rows:[header,...rows]}]);
  const cost=createXlsxWorkbookBytes([{name:'cost',rows:[['货品编号','固定成本价','货品名称'],['SKU-1',20,'测试货品']]}]);
  const file=path.join(root,'sales.xlsx'),costFile=path.join(root,'cost.xlsx');await writeFile(file,raw);await writeFile(costFile,cost);
  const result=await runSalesImport({asOfDate:'2026-09-22',salesStartDate:'2026-08-09',downloadPath:file,costSourcePath:costFile,expectedSourceRows:5,auditRootPath:path.join(root,'audit'),baseUrl:'http://localhost:3000',dryRun:true});
  const audit=result.audit as {period:{startDate:string;endDate:string};output:{path:string};filtering:{retainedRows:number;excludedOutOfPeriodRows:number;excludedTodayRows:number}};
  assert.equal(audit.period.startDate,'2026-08-09');assert.equal(audit.period.endDate,'2026-09-22');assert.equal(audit.filtering.retainedRows,3);assert.equal(audit.filtering.excludedOutOfPeriodRows,1);assert.equal(audit.filtering.excludedTodayRows,1);
  const workbook=parseXlsxFirstSheet(new Uint8Array(await readFile(audit.output.path)));assert.equal(workbook.rows.length,4);
});

test("independent batch verification queries rolling dates and rejects a monthly response",async()=>{
  const modules=(['products','inventory','inventory_age','sales','combos'] as const).map(module=>({module,status:'completed',batchId:module+'-batch',rowCount:1,warningCount:0,outputSha256:'a'.repeat(64)}));
  const request=(async(input:string|URL|Request)=>{const url=new URL(String(input));if(url.pathname.endsWith('/sales/verify')){assert.equal(url.searchParams.get('startDate'),'2026-08-09');return Response.json({policyVersion:'p',period:{startDate:'2026-09-01',endDate:'2026-09-22'}});}const id=url.searchParams.get('batchId')!;return Response.json({items:[{id,status:'completed',rowCount:1,ownedRowCount:1,excludedCount:0,isCurrent:true,snapshotDate:/inventory/.test(id)?'2026-09-23':null}]});}) as typeof fetch;
  await assert.rejects(verifyPublishedJackyunBatches({baseUrl:'http://localhost:3000',asOfDate:'2026-09-22',salesStartDate:'2026-08-09',snapshotDate:'2026-09-23',modules,request}),/sales/);
});
