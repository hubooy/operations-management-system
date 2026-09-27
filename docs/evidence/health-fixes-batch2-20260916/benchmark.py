"""Synthetic, SELECT-only Django API benchmark. Never targets production."""
import django,json,time,sys,os,hashlib,math,statistics,threading,tracemalloc
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from unittest.mock import patch
django.setup()
from django.db import connection,connections,DatabaseError
from django.test import Client,override_settings
from market import query,admin,views
from market.models import MarketRankingEntry
from sales.consumers import execute_consumer_query,validate_consumer_request
from sales.tests.factories import signed_headers
assert connection.settings_dict['PORT']=='55453'
assert connection.settings_dict['NAME']=='health_review'
assert not MarketRankingEntry.objects.exclude(last_import_batch_id='synthetic').exists()
ROOT=Path(__file__).parent;local=threading.local();records=[]
SAMPLES=int(sys.argv[1]) if len(sys.argv)>1 else 20
OUT=ROOT/('benchmark-'+str(SAMPLES)+'.json')
def sales(p,r):return execute_consumer_query(p,validate_consumer_request(r)),'0:0'
def timed(name,fn):
 def wrapped(*a,**kw):
  start=time.perf_counter()
  try:return fn(*a,**kw)
  finally:
   phases=getattr(local,'phases',None)
   if phases is not None:phases[name]=phases.get(name,0)+(time.perf_counter()-start)*1000
 return wrapped
def request_payload(kind,params):
 if kind=='master':return {'operation':'master','view':'database_primary','params':params}
 return {'operation':'overview','view':'ranking','page':params.get('page',1),'pageSize':20,'filters':params.get('filters')}
def read(kind,params,label):
 connection.close()
 with connection.cursor() as c:
  c.execute('SET ROLE health_market_reader')
  c.execute("SET statement_timeout='6s'")
  c.execute('SET default_transaction_read_only=on')
 local.phases={};sql=[]
 def wrapper(execute,text,values,many,context):
  start=time.perf_counter()
  try:return execute(text,values,many,context)
  finally:sql.append({'ms':(time.perf_counter()-start)*1000,'sha256':hashlib.sha256(text.encode()).hexdigest()})
 payload=request_payload(kind,params);body=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
 headers=signed_headers('/api/market/queries',method='POST',body=body,role='viewer',secret=os.environ['TERUISI_DJANGO_INTERNAL_SECRET'],request_id='synthetic-'+str(time.time_ns()))
 start=time.perf_counter()
 with connection.execute_wrapper(wrapper):
  response=Client(raise_request_exception=False).post('/api/market/queries',data=body,content_type='application/json',headers=headers)
 elapsed=(time.perf_counter()-start)*1000
 value=response.json();result=value.get('masterData',value)
 row={'label':label,'kind':kind,'params':params,'ms':elapsed,'status':response.status_code,
      'returned':len(result.get('items',[])),'pagination':result.get('pagination'),'bytes':len(response.content),
      'sha256':hashlib.sha256(response.content).hexdigest(),'sqlCount':len(sql),'sqlMs':sum(s['ms'] for s in sql),
      'maxSqlMs':max((s['ms'] for s in sql),default=0),'phases':dict(local.phases)}
 if response.status_code!=200:row['error']=value
 connection.close();return row
def save():OUT.write_text(json.dumps({'syntheticOnly':True,'postgresqlPort':55453,'statementTimeoutMs':6000,
 'transport':'Django signed Client; actual PostgreSQL sales consumer in-process (HTTP hop not measured)',
 'data':{'rankingRows':320000,'identities':50000,'snapshots':300000,'projectionRows':150000,'salesRows':100000,'imageCache':50000,'annotations':25000},
 'records':records},ensure_ascii=False,indent=2),encoding='utf8')
def summarize(samples):
 values=sorted(r['ms'] for r in samples)
 return {'samples':len(values),'errors':sum(r['status']!=200 for r in samples),'p50Ms':statistics.median(values),'p95Ms':values[math.ceil(len(values)*.95)-1],
 'maxMs':max(values),'correctHashes':len({r['sha256'] for r in samples if r['status']==200})}
with ExitStack() as stack:
 stack.enter_context(override_settings(ALLOWED_HOSTS=['testserver','localhost','127.0.0.1']))
 stack.enter_context(patch.object(query,'read_sales_consumer',sales))
 for name in ('filter_options','ranking_page','_snapshot_map','_price_band_versions','_projection_metrics','_sales_metrics'):
  stack.enter_context(patch.object(query,name,timed(name,getattr(query,name))))
 for name in ('master_page','page_context'):
  stack.enter_context(patch.object(admin,name,timed(name,getattr(admin,name))))
 stack.enter_context(patch.object(views,'_json',timed('json_serialization',views._json)))
 for kind in ('master','ranking'):
  first=read(kind,{},'first-process-read');records.append(first);save();print(kind,'first',first['status'],round(first['ms']),flush=True)
  for workers in (1,4):
   with ThreadPoolExecutor(max_workers=workers) as pool:samples=list(pool.map(lambda i:read(kind,{},f'{workers}-users-{i}'),range(SAMPLES)))
   item={'kind':kind,'users':workers,'summary':summarize(samples),'samples':samples};records.append(item);save();print(json.dumps({'kind':kind,'users':workers,**item['summary']}),flush=True)
  variants=([{'page':2},{'q':'does-not-exist'},{'priceStatuses':['confirmed']},{'candidatePriceSources':['ai']},
    {'annotationStatuses':['committed']},{'includeHistory':True},{'categories':['类目0'],'brands':['品牌0']}] if kind=='master' else
    [{'page':2},{'filters':{'query':'does-not-exist'}},{'filters':{'priceBands':['未确认价格']}},
     {'filters':{'priceBands':['价格带1']}},{'filters':{'startDate':'2026-08-01','endDate':'2026-08-31'}},
     {'filters':{'brands':['品牌0']}},{'filters':{'categories':['类目0'],'rankingDimensions':['SPU']}}])
  for p in variants:
   pair=[read(kind,p,f'variant-{i}') for i in range(2)]
   assert pair[0]['status']==pair[1]['status']==200,(kind,p,pair)
   assert pair[0]['sha256']==pair[1]['sha256'],(kind,p)
   records.append({'kind':kind,'variant':p,'samples':pair});save()
  tracemalloc.start();sample=read(kind,{},'memory-with-tracing');current,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
  records.append({'kind':kind,'memoryPeakBytes':peak,'sample':sample,'timingComparable':False});save()
with connection.cursor() as c:
 c.execute('SET ROLE health_market_reader')
 try:c.execute("UPDATE market_ranking_entries SET rank=rank WHERE FALSE");denied=False
 except DatabaseError:denied=True
assert denied
records.append({'readerWriteDenied':denied});save()
print('complete',flush=True)
