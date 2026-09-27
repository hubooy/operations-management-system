"""Generate native-formula fixtures for independent spreadsheet recalculation."""
from copy import deepcopy
import json
from pathlib import Path
import random
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"backend"))
from business_analysis.budget import calculate
from business_analysis.budget_offline import payload
from business_analysis.report_files import Column, Table, write_pair
from business_analysis.test_budget import fixture

directory=Path(sys.argv[1]).resolve(); directory.mkdir(parents=True,exist_ok=True)
if sys.argv[2:] not in ([], ["--renderer-version=5"]):
    raise ValueError("Only explicit renderer-version=5 is supported; default remains historical")
xlsx_opc_version = 2 if sys.argv[2:] else 1


def generate(name, plan, bases):
    model=payload(calculate(plan,bases),"synthetic-excel-budget")
    model['excelEnabled']=True
    with (directory/(name+'.xlsx')).open('wb') as xlsx,(directory/(name+'.html')).open('wb') as html:
        return write_pair(xlsx,html,title="合成预算公式验收",metadata={"synthetic":True},
            tables=[Table("original","原报告固定数值","原表不随试算变化",(Column("gmv","情景金额（分）","integer"),),[[45000]],1)],
            offline_budget=model,excel_budget=model,xlsx_opc_version=xlsx_opc_version)


plan,bases=fixture()
proof=generate('report',plan,bases)
cases=[]


def add_case(name, plan, bases, edits, scenario=0, filename='report.xlsx', precision=False, baseline_precision=()):
    computed=calculate(plan,bases);sc=computed['scenarios'][scenario]
    cases.append({'name':name,'file':filename,'edits':edits,'allocation':computed['allocation'],
                  'rows':sc['rows'],'summary':sc['summary'],'precision':precision,'baselinePrecisionTargets':list(baseline_precision)})


for kind in ("original","stress","money","weights","caps","unknown_margin","zero_margin","zero_budget","minima","low_sample","days","fractional_assumptions"):
    changed=deepcopy(plan); edits=[]; scenario=0
    if kind=="stress":scenario=1;edits.append([0,"B12",2])
    if kind in ("money","weights","caps"):
        changed["totalBudgetCents"]=15050;edits.append([0,"B5",150.5])
    if kind=="weights":changed["targets"][0]["weight"]=1;edits.append([1,"B5",1])
    if kind=="caps":changed["targets"][0]["maxBudgetCents"]=3000;edits.append([1,"D5",30])
    if kind in ("unknown_margin","zero_margin"):
        v=None if kind=="unknown_margin" else 0;changed["scenarios"][0]["contributionMarginBps"]=v;edits.append([0,"F16",v])
    if kind=='zero_budget': changed['reserveCents']=10000;edits.append([0,'B6',100])
    if kind=='minima': changed['targets'][1]['minBudgetCents']=8001;edits.append([1,'C6',80.01])
    if kind=='low_sample': changed['minimumClicks']=500;edits.append([0,'B10',500])
    if kind=='days': changed['horizonDays']=13;changed['observationDays']=4;edits.extend([[0,'B7',13],[0,'B8',4]])
    if kind=='fractional_assumptions':
        for key,address,value in [('cpcFactorBps','C16',13333),('orderRateFactorBps','D16',7777),('orderValueFactorBps','E16',12555)]:
            changed['scenarios'][0][key]=value;edits.append([0,address,value/100])
    add_case(kind,changed,bases,edits,scenario)

rng=random.Random(140916)
for i in range(20):
    changed=deepcopy(plan);edits=[]
    total=rng.randint(1001,100000);changed['totalBudgetCents']=total;edits.append([0,'B5',total/100])
    for j in range(2):
        weight=rng.randint(1,10000);cap=rng.randint(0,100000)
        changed['targets'][j].update(weight=weight,maxBudgetCents=cap)
        edits.extend([[1,'B'+str(j+5),weight],[1,'D'+str(j+5),cap/100]])
    add_case('random-'+str(i),changed,bases,edits)

for variant in ('hundred','missing','mixed','precision','initial_blank','maximum','baseline_precision'):
    changed,bb=fixture()
    if variant=='hundred':
        t,b=deepcopy(changed['targets'][0]),deepcopy(bb[0]);changed['targets']=[];bb=[]
        for i in range(100):
            target={**t,'rowId':f'{i:064x}','rowIndex':i,'weight':rng.randint(1,10000),'maxBudgetCents':rng.randint(0,10000)}
            changed['targets'].append(target);bb.append({**b,'rowId':target['rowId'],'entity':{'skuId':f'S{i}'}})
        changed['totalBudgetCents']=123456
    if variant=='missing': bb[0]['datesPresent']=False;bb[1]['metrics']['reportedGmvCents']=None
    if variant=='mixed': bb[0]['source']='jd-gross';bb[1]['source']='tmall-net'
    if variant=='initial_blank': changed['scenarios'][0]['contributionMarginBps']=None
    if variant=='precision':
        bb[0]['metrics'].update(spendCents=999983,reportedGmvCents=999999999989)
        changed['scenarios'][0].update(cpcFactorBps=10007,orderRateFactorBps=10009,orderValueFactorBps=10037)
    if variant=='maximum':
        changed['totalBudgetCents']=10**12
        for target in changed['targets']: target['maxBudgetCents']=10**12
    if variant=='baseline_precision':
        changed['horizonDays']=93
        bb[0]['days']=1
        bb[0]['metrics'].update(spendCents=9999999999999,reportedGmvCents=9999999999999)
    generate(variant,changed,bb)
    add_case(variant,changed,bb,[],filename=variant+'.xlsx',precision=variant=='precision',baseline_precision=[0] if variant=='baseline_precision' else [])
    if variant=='baseline_precision':
        recovered=deepcopy(changed);recovered['horizonDays']=7
        add_case('baseline_precision_recovered',recovered,bb,[[0,'B7',7]],filename=variant+'.xlsx')
        add_case('baseline_precision_reintroduced',changed,bb,[[0,'B7',7],[0,'B7',93]],filename=variant+'.xlsx',baseline_precision=[0])

invalid=[{'name':name,'edits':edits,'invalid':True,'file':'report.xlsx'} for name,edits in (
    ('text_total',[[0,'B5','bad']]),('blank_total',[[0,'B5',None]]),('negative_weight',[[1,'B5',-1]]),
    ('too_many_decimals',[[0,'B5',10.001]]),('minimum_exceeds_budget',[[1,'C5',99]]),
    ('invalid_case',[[0,'B12',3]]),('text_margin',[[0,'F16','bad']]),('zero_cpc',[[0,'C16',0]]))]
cases.extend(invalid)
(directory/"expected.json").write_text(json.dumps({"sheets":proof["budgetCalculator"]["sheets"],"cases":cases},ensure_ascii=False),encoding="utf-8")
print(json.dumps({"status":"generated","cases":len(cases),"calculator":proof["budgetCalculator"]},ensure_ascii=False))
