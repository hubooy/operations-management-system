"""Offline, unreviewed what-if view; never edits the report's evidence tables."""
from .budget import calculate
from .contracts import AnalysisContractError, canonical

ROW_FIELDS = ("budgetCents", "equivalentBaselineSpendCents", "budgetChangeCents", "status", "observationDays", "reviewAfterSpendCents",
    "minimumRoasBps", "projectedClicks", "projectedOrderLines", "projectedAttributedGmvCents", "projectedRoas", "assumedContributionAfterAdCents")


def projection(result):
    return {"allocation": result["allocation"], "scenarios": [{"summary": scenario["summary"],
        "rows": [{key: row[key] for key in ROW_FIELDS} for row in scenario["rows"]]} for scenario in result["scenarios"]]}


def payload(result, report_id):
    baselines = [row["baseline"] for row in result["scenarios"][0]["rows"]]
    expected = projection(calculate(result["plan"], baselines))
    if expected != projection(result):
        raise AnalysisContractError("离线预算初值与服务端结果不一致")
    return {"schemaVersion": "business-offline-budget-v1", "reportId": report_id, "planDigest": result["planDigest"],
        "plan": result["plan"], "baselines": baselines, "expected": expected}


def render(value):
    data = canonical(value).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    ui = UI.replace("Excel 当前保留原报告参数快照。", "Excel 附有独立可编辑试算页；超出其精确计算范围时请使用本页，原报告仍保留。") if value.get("excelEnabled") else UI
    return '<script type="application/json" id="budget-data">'+data+'</script><script>'+ENGINE+ui+'</script>'


# BigInt intermediates preserve cent allocation and half-up rounding. The
# editable form changes numeric planning parameters only; references stay fixed.
ENGINE = r'''
function calculateOfflineBudget(plan, bases) {
  const int=(v,lo,hi)=>{if(!Number.isSafeInteger(v)||v<lo||v>hi)throw Error("参数必须为范围内整数");return v;};
  const bi=BigInt, sum=xs=>{const n=xs.reduce((a,b)=>a+bi(b),0n);if(n>9007199254740991n||n< -9007199254740991n)throw Error("合计超过离线精确数值容量");return Number(n);}, money=10**12;
  const round=(n,d,places=0)=>{if(d<=0n)throw Error("计算分母无效");const sign=n<0n?-1n:1n;n=(n<0n?-n:n)*10n**bi(places);const v=sign*((2n*n+d)/(2n*d));if(v>9007199254740991n||v< -9007199254740991n)throw Error("计算结果超过离线精确数值容量");return Number(v)/10**places;};
  int(plan.totalBudgetCents,1,money);int(plan.reserveCents,0,plan.totalBudgetCents);int(plan.horizonDays,1,93);int(plan.observationDays,1,plan.horizonDays);
  int(plan.reviewAfterSpendBps,1,10000);int(plan.minimumClicks,1,1000000);int(plan.minimumOrderLines,1,1000000);
  if(!Array.isArray(plan.targets)||plan.targets.length<1||plan.targets.length>100||bases.length!==plan.targets.length||!Array.isArray(plan.scenarios)||plan.scenarios.length<1||plan.scenarios.length>5)throw Error("对象或情景数量无效");
  const targets=plan.targets,seen=new Set();
  targets.forEach((t,i)=>{int(t.weight,1,10000);int(t.maxBudgetCents,0,money);int(t.minBudgetCents,0,t.maxBudgetCents);int(t.minimumRoasBps,0,1000000);
    const key=[t.sourceKey,t.dimension,t.rowId].join("|");if(seen.has(key)||bases[i].rowId!==t.rowId)throw Error("预算对象身份不一致");seen.add(key);});
  plan.scenarios.forEach(a=>{[a.cpcFactorBps,a.orderRateFactorBps,a.orderValueFactorBps].forEach(v=>int(v,1000,30000));if(a.contributionMarginBps!==null)int(a.contributionMarginBps,0,10000);});
  const available=plan.totalBudgetCents-plan.reserveCents,amounts=targets.map(t=>t.minBudgetCents);
  if(sum(amounts)>available)throw Error("最低预算合计超过扣除预留后的预算");
  let remaining=Math.min(available,sum(targets.map(t=>t.maxBudgetCents)))-sum(amounts),active=targets.map((t,i)=>i).filter(i=>amounts[i]<targets[i].maxBudgetCents);
  while(remaining&&active.length){const w=bi(sum(active.map(i=>targets[i].weight))),n=new Map(active.map(i=>[i,bi(remaining)*bi(targets[i].weight)]));
    const capped=active.filter(i=>n.get(i)>=bi(targets[i].maxBudgetCents-amounts[i])*w);
    if(capped.length){capped.forEach(i=>{remaining-=targets[i].maxBudgetCents-amounts[i];amounts[i]=targets[i].maxBudgetCents;});active=active.filter(i=>!capped.includes(i));continue;}
    active.forEach(i=>{const q=Number(n.get(i)/w);amounts[i]+=q;remaining-=q;});
    const identity=i=>[targets[i].sourceKey,targets[i].dimension,targets[i].rowId];
    active.sort((a,b)=>{const x=n.get(a)%w,y=n.get(b)%w;if(x!==y)return x>y?-1:1;const aa=identity(a),bb=identity(b);for(let k=0;k<3;k++){if(aa[k]!==bb[k])return aa[k]<bb[k]?-1:1;}return 0;});
    active.slice(0,remaining).forEach(i=>amounts[i]++);remaining=0;
  }
  const allocation={totalBudgetCents:plan.totalBudgetCents,reservedCents:plan.reserveCents,allocatedCents:sum(amounts),unallocatedCents:available-sum(amounts),targetCount:targets.length,scope:"selected_targets_only"};
  const scenarios=plan.scenarios.map(a=>{const rows=targets.map((t,i)=>{const base=bases[i],m=base.metrics,budget=amounts[i],metrics=[m.spendCents,m.clicks,m.reportedOrderLines,m.reportedGmvCents];
    int(base.days,1,93);metrics.forEach(v=>{if(v!==null&&v!==undefined)int(v,-(10**15)+1,10**15-1);});
    const missing=metrics.some(v=>!Number.isInteger(v)),bad=!base.datesPresent||missing||m.spendCents<=0||m.clicks<=0||m.reportedOrderLines<=0||m.reportedGmvCents<0;
    const normalized=Number.isInteger(m.spendCents)&&m.spendCents>=0&&base.datesPresent?round(bi(m.spendCents)*bi(plan.horizonDays),bi(base.days)):null;
    let clicks=null,orders=null,gmv=null,roas=null,contribution=null;
    if(!bad){const b=bi(budget),spend=bi(m.spendCents),cpc=bi(a.cpcFactorBps);
      clicks=round(b*bi(m.clicks)*10000n,spend*cpc,4);orders=round(b*bi(m.reportedOrderLines)*bi(a.orderRateFactorBps),spend*cpc,4);
      gmv=round(b*bi(m.reportedGmvCents)*bi(a.orderRateFactorBps)*bi(a.orderValueFactorBps),spend*cpc*10000n);
      if(Math.abs(gmv)>=10**15)throw Error("情景金额超过精确文件容量");roas=budget?round(bi(gmv),b,6):null;
      if(a.contributionMarginBps!==null)contribution=round(bi(gmv)*bi(a.contributionMarginBps),10000n)-budget;
    }
    return{budgetCents:budget,equivalentBaselineSpendCents:normalized,budgetChangeCents:normalized===null?null:budget-normalized,
      status:bad?"unavailable":m.clicks<plan.minimumClicks||m.reportedOrderLines<plan.minimumOrderLines?"low_sample_scenario":"assumption_scenario",
      observationDays:plan.observationDays,reviewAfterSpendCents:round(bi(budget)*bi(plan.reviewAfterSpendBps),10000n),minimumRoasBps:t.minimumRoasBps,
      projectedClicks:clicks,projectedOrderLines:orders,projectedAttributedGmvCents:gmv,projectedRoas:roas,assumedContributionAfterAdCents:contribution};});
    const reporting=[...new Set(bases.map(b=>b.source||"unspecified"))].sort(),unavailable=rows.filter(r=>r.projectedAttributedGmvCents===null).length;
    const known=sum(rows.map(r=>r.projectedAttributedGmvCents||0)),byReportingBasis=reporting.map(source=>({source,knownAttributedGmvCents:sum(rows.map((r,i)=>(bases[i].source||"unspecified")===source?r.projectedAttributedGmvCents||0:0)),unavailableTargets:rows.filter((r,i)=>(bases[i].source||"unspecified")===source&&r.projectedAttributedGmvCents===null).length}));
    return{rows,summary:{knownAttributedGmvCents:reporting.length===1?known:null,projectedAttributedGmvCents:!unavailable&&reporting.length===1?known:null,unavailableTargets:unavailable,
      mixedReportingBases:reporting.length!==1,byReportingBasis,assumedContributionAfterAdCents:reporting.length===1&&rows.every(r=>r.assumedContributionAfterAdCents!==null)?sum(rows.map(r=>r.assumedContributionAfterAdCents)):null,
      breakEvenRoas:a.contributionMarginBps?round(10000n,bi(a.contributionMarginBps),6):null}};
  });return{allocation,scenarios};
}
'''

UI = r'''
(()=>{
  const source=JSON.parse(document.getElementById("budget-data").textContent),el=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  const section=el("section");section.id="offline-budget";section.style.cssText="max-width:1752px;margin:0 auto 24px;padding:24px;background:white;border:1px solid #d8e3db;border-radius:12px;overflow-wrap:anywhere";
  document.querySelector(".layout").after(section);section.append(el("h2","离线预算试算"),el("p","修改仅影响下方试算，原报告表格和已复核结论保持原样。离线结果未复核，不是实际利润或收益承诺，不会上传或执行投放。Excel 当前保留原报告参数快照。"));
  const status=el("p"),errors=el("p"),form=el("form"),output=el("div"),download=el("button","下载本次试算 JSON"),reset=el("button","恢复原报告参数");
  errors.setAttribute("role","alert");status.setAttribute("aria-live","polite");download.type=reset.type="button";download.disabled=true;
  let revision=0,current=null;const controls=[];
  const editable=(group,title,object,key,scale=1,nullable=false)=>{const label=el("label",title),input=el("input");input.type="text";input.inputMode="decimal";input.value=object[key]===null?"":String(object[key]/scale);input.setAttribute("aria-label",title);input.dataset.budgetKey=key;input.style.cssText="min-width:0;width:100%;display:block";label.style.cssText="min-width:0;display:block";label.append(input);group.append(label);controls.push({input,object,key,scale,nullable});};
  let plan=structuredClone(source.plan);
  const grid=parent=>{const g=el("div");g.style.cssText="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px;margin:16px 0";parent.append(g);return g;};
  const globals=grid(form);
  [["预算上限（元）","totalBudgetCents",100],["预留预算（元）","reserveCents",100],["规划天数","horizonDays",1],["观察天数","observationDays",1],["提前复盘花费占比（%）","reviewAfterSpendBps",100],["样本点击门槛","minimumClicks",1],["样本订单口径门槛","minimumOrderLines",1]].forEach(([title,key,scale])=>editable(globals,title,plan,key,scale));
  const objects=el("details");objects.append(el("summary","对象预算边界与权重"));form.append(objects);
  plan.targets.forEach((t,i)=>{const part=el("fieldset");part.style.minWidth="0";part.append(el("legend",Object.values(source.baselines[i].entity).filter(v=>v!==null).join(" · ")));objects.append(part);const g=grid(part);[["权重","weight",1],["最低预算（元）","minBudgetCents",100],["最高预算（元）","maxBudgetCents",100],["最低归因产出比（倍）","minimumRoasBps",10000]].forEach(([title,key,scale])=>editable(g,title,t,key,scale));});
  plan.scenarios.forEach(a=>{const part=el("fieldset");part.style.cssText="min-width:0;margin-top:16px";part.append(el("legend",a.name));form.append(part);const g=grid(part);[["点击成本相对基期（%）","cpcFactorBps"],["订单效率相对基期（%）","orderRateFactorBps"],["订单金额相对基期（%）","orderValueFactorBps"],["假设贡献率（%，未知留空）","contributionMarginBps"]].forEach(([title,key])=>editable(g,title,a,key,100,key==="contributionMarginBps"));});
  const calculate=el("button","按新参数试算");calculate.type="submit";calculate.style.margin="16px 8px 16px 0";form.append(calculate,reset);section.append(form,errors,status,output,download);
  const canonicalLocal=v=>v&&typeof v==="object"?Array.isArray(v)?v.map(canonicalLocal):Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonicalLocal(v[k])])):v;
  const money=v=>v===null?"不可测算或不可合计":(v/100).toLocaleString("zh-CN",{minimumFractionDigits:2,maximumFractionDigits:2})+" 元";
  function show(result,changed){output.replaceChildren();status.textContent=changed?"本地试算 v"+revision+" · 未保存到系统、未复核":"原报告参数 · 初值已与服务端逐项核对";
    output.append(el("p","已分配 "+money(result.allocation.allocatedCents)+"；预留 "+money(result.allocation.reservedCents)+"；未分配 "+money(result.allocation.unallocatedCents)));
    result.scenarios.forEach((s,i)=>{output.append(el("h3",plan.scenarios[i].name),el("p","情景归因金额："+money(s.summary.projectedAttributedGmvCents)+"；假设贡献扣推广余额："+money(s.summary.assumedContributionAfterAdCents)+"；不可测算对象 "+s.summary.unavailableTargets+" 项。"));
      if(s.summary.mixedReportingBases)output.append(el("p",s.summary.byReportingBasis.map(b=>b.source+"：已知对象 "+money(b.knownAttributedGmvCents)).join("；")));
      const wrap=el("div"),table=el("table"),head=el("tr");wrap.className="scroll";["对象","预算","情景归因金额","提前复盘花费","观察天数","状态"].forEach(x=>head.append(el("th",x)));table.append(head);
      s.rows.forEach((r,j)=>{const row=el("tr");[Object.values(source.baselines[j].entity).filter(v=>v!==null).join(" · "),money(r.budgetCents),money(r.projectedAttributedGmvCents),money(r.reviewAfterSpendCents),r.observationDays,{unavailable:"不可测算",low_sample_scenario:"低样本假设",assumption_scenario:"假设情景"}[r.status]].forEach(x=>row.append(el("td",x)));table.append(row);});wrap.append(table);output.append(wrap);
    });}
  try {const initial=calculateOfflineBudget(plan,source.baselines);if(JSON.stringify(canonicalLocal(initial))!==JSON.stringify(canonicalLocal(source.expected)))throw Error("初值核对不一致，禁止试算");show(initial,false);}
  catch(e){errors.textContent=e.message;calculate.disabled=reset.disabled=true;return;}
  form.addEventListener("input",()=>{current=null;download.disabled=true;output.replaceChildren();status.textContent="参数已修改，尚未重算";errors.textContent="";});
  form.addEventListener("submit",event=>{event.preventDefault();current=null;download.disabled=true;output.replaceChildren();try{
    controls.forEach(({input,object,key,scale,nullable})=>{const raw=input.value.trim(),digits=Math.log10(scale);if(nullable&&!raw){object[key]=null;return;}if(!(digits?new RegExp("^\\d{1,13}(?:\\.\\d{1,"+digits+"})?$"):/^\d{1,13}$/).test(raw))throw Error("数字格式无效，金额及百分比最多两位小数");const [whole,fraction=""]=raw.split(".");object[key]=Number(whole)*scale+(digits?Number(fraction.padEnd(digits,"0")):0);});
    const result=calculateOfflineBudget(plan,source.baselines);revision++;current={schemaVersion:source.schemaVersion,reportId:source.reportId,originalPlanDigest:source.planDigest,localRevision:revision,reviewStatus:"unreviewed_local_scenario",plan:structuredClone(plan),result};show(result,true);errors.textContent="";download.disabled=false;
  }catch(e){errors.textContent=e.message;status.textContent="参数无效；未产生试算结果";}});
  reset.onclick=()=>{controls.forEach(c=>{const index=plan.targets.indexOf(c.object),scenario=plan.scenarios.indexOf(c.object),original=index>=0?source.plan.targets[index]:scenario>=0?source.plan.scenarios[scenario]:source.plan;c.object[c.key]=original[c.key];c.input.value=original[c.key]===null?"":String(original[c.key]/c.scale);});current=null;download.disabled=true;errors.textContent="";show(calculateOfflineBudget(plan,source.baselines),false);};
  download.onclick=()=>{if(!current)return;const url=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:"application/json;charset=utf-8"})),a=el("a");a.href=url;a.download="预算试算-v"+revision+"-未复核.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
})();
'''
