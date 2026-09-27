import django,importlib.util,time,json,tracemalloc
from pathlib import Path
from unittest.mock import patch
django.setup()
from django.db import connection
from market import admin,query
from sales.auth import Principal
from sales.consumers import execute_consumer_query,validate_consumer_request
assert connection.settings_dict['PORT']=='55453'
ROOT=Path(__file__).parent;records=[]
def load(name,file):
 spec=importlib.util.spec_from_file_location('market.'+name,ROOT/file)
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
old_admin=load('_baseline_admin','old_admin.py');old_rank=load('_baseline_rank','old_ranking_query.py');old_query=load('_baseline_query','old_query.py')
old_query.ranking_page=old_rank.ranking_page
def sales(p,r):return execute_consumer_query(p,validate_consumer_request(r)),'0:0'
principal=Principal(email='synthetic@example.invalid',display_name='Synthetic',role='viewer',scope=None)
with connection.cursor() as c:
 c.execute('SET ROLE health_market_reader');c.execute("SET statement_timeout='6s'");c.execute('SET default_transaction_read_only=on')
def run(label,fn,trace=False):
 count=0;sqlms=0
 def wrapper(execute,sql,params,many,context):
  nonlocal count,sqlms
  start=time.perf_counter()
  try:return execute(sql,params,many,context)
  finally:count+=1;sqlms+=(time.perf_counter()-start)*1000
 if trace:tracemalloc.start()
 start=time.perf_counter()
 with connection.execute_wrapper(wrapper):value=fn()
 result={'label':label,'ms':(time.perf_counter()-start)*1000,'sqlCount':count,'sqlMs':sqlms,'traceEnabled':trace}
 if trace:result['pythonPeakBytes']=tracemalloc.get_traced_memory()[1];tracemalloc.stop()
 records.append(result);print(json.dumps(result),flush=True)
 return value
for params in ({'page':1,'filters':None},{'page':2,'filters':None},{'page':1,'filters':{'priceBands':['未确认价格']}},
               {'page':1,'filters':{'startDate':'2026-08-01','endDate':'2026-08-31'}}):
 request={'operation':'overview','view':'ranking','pageSize':20,**params}
 old=run('ranking-old-'+str(params),lambda:old_query.overview(principal,request,sales_loader=sales))
 new=run('ranking-new-'+str(params),lambda:query.overview(principal,request,sales_loader=sales))
 assert old==new,params
codes=[f'sku-{i}' for i in range(1000)]
for module,label in ((old_admin,'old'),(admin,'new')):
 original=module._master_queryset
 with patch.object(module,'_master_queryset',lambda p,history=False:original(p,history=history).filter(sku_code__in=codes)):
  result=run('master-'+label+'-1000',lambda:module.list_master({}))
  if label=='old':expected=result
  else:assert result==expected
  run('master-'+label+'-1000-traced',lambda:module.list_master({}),True)
(ROOT/'comparison.json').write_text(json.dumps({'pairedSameDatabaseAndOriginalIndexes':True,'rankingFullPayloadEqual':True,
 'master1000FullPayloadEqual':True,'records':records},ensure_ascii=False,indent=2),encoding='utf8')
print('all paired payloads equal',flush=True)
