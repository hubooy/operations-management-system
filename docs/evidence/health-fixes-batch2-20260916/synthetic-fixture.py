import django, time
django.setup()
from django.db import connection
from market.models import *
assert connection.settings_dict['PORT']=='55453'
assert not MarketRankingEntry.objects.exists()
seed=MarketRankingEntry.objects.create(natural_key='seed',source_row_number=1,period_start='2026-08-01',period_end='2026-08-31',category='类目0',scope='全部',ranking_dimension='SKU',sku_code='seed',rank=1,gmv_cents=10000,last_import_batch_id='synthetic')
fields=[f.column for f in MarketRankingEntry._meta.concrete_fields if f.column!='id'];q=connection.ops.quote_name
overrides={'natural_key':"'row-'||g::text",'sku_code':"'sku-'||((g-1)%50000)::text",'category':"'类目'||((g-1)%7)::text",'brand':"'品牌'||((g-1)%13)::text",'period_start':"to_char(date '2026-03-01'+(((g-1)/50000)%6)*interval '1 month','YYYY-MM-DD')",'period_end':"to_char(date '2026-03-01'+((((g-1)/50000)%6)+1)*interval '1 month'-interval '1 day','YYYY-MM-DD')",'rank':'1+(g%200)','gmv_cents':'10000+g%100000','image_url':"'https://synthetic.invalid/'||((g-1)%50000)::text",'price_band_filter':"CASE WHEN g>300000 THEN '高价段' ELSE '全部' END"}
# Identity category must remain stable across months.
overrides['category']="'类目'||(((g-1)%50000)%7)::text"
with connection.cursor() as c:
 c.execute('INSERT INTO market_ranking_entries ('+','.join(map(q,fields))+') SELECT '+','.join(overrides.get(f,'s.'+q(f)).replace('%','%%') for f in fields)+' FROM market_ranking_entries s CROSS JOIN generate_series(1,320000) g WHERE s.id=%s',[seed.id])
 c.execute("INSERT INTO market_master_identities(category,scope,ranking_dimension,sku_code,latest_entry_id,updated_at) SELECT DISTINCT ON(category,scope,ranking_dimension,sku_code) category,scope,ranking_dimension,sku_code,id,NOW() FROM market_ranking_entries WHERE id<>%s ORDER BY category,scope,ranking_dimension,sku_code,period_end DESC,rank,id",[seed.id])
 c.execute("INSERT INTO market_sku_gmv_totals SELECT sku_code,SUM(gmv_cents),NOW() FROM market_ranking_entries GROUP BY sku_code")
seed.delete()
snap=MarketPriceSnapshot.objects.create(id='seed',category='seed',sku_code='seed',month='2026-08')
fields=[f.column for f in MarketPriceSnapshot._meta.concrete_fields];q=connection.ops.quote_name
overrides={'id':"'price-'||g::text",'sku_code':"'sku-'||((g-1)%50000)::text",'category':"'类目'||(((g-1)%50000)%7)::text",'scope':"'全部'",'month':"to_char(date '2026-03-01'+(((g-1)/50000)%6)*interval '1 month','YYYY-MM')",'source_price_cents':'10000+g%30000','confirmed_market_price_cents':'10000+g%30000','confirmation_status':"CASE WHEN g%4=0 THEN 'confirmed' WHEN g%4=1 THEN 'ai_pending' ELSE 'source_table' END",'ai_price_type':"'标准售价'",'image_content_sha256':"repeat('a',64)",'ai_image_price_cents':'20000+g%10000'}
with connection.cursor() as c:
 c.execute('INSERT INTO market_price_snapshots ('+','.join(map(q,fields))+') SELECT '+','.join(overrides.get(f,'s.'+q(f)) for f in fields)+" FROM market_price_snapshots s CROSS JOIN generate_series(1,300000) g WHERE s.id='seed'")
snap.delete()
MarketPriceBandVersion.objects.create(id='bands',category='*',version=1,status='published')
for i in range(4):MarketPriceBandItem.objects.create(id=str(i),version_id='bands',label=f'价格带{i}',min_cents=i*10000,max_cents=(i+1)*10000,sort_order=i)
MarketNetshopProjectionControl.objects.update_or_create(id=1,defaults={'active_revision':'synthetic'})
seed=MarketNetshopProjection.objects.create(projection_revision='synthetic',projection_key='seed',kind='metric',dataset='sku_daily',source='jd_sku_daily',sku_id='seed',business_date='2026-08-15',transaction_amount_cents=12345)
fields=[f.column for f in MarketNetshopProjection._meta.concrete_fields if f.column!='id']
overrides={'projection_key':"'metric-'||g::text",'sku_id':"'sku-'||((g-1)%50000)::text",'business_date':"to_char(date '2026-03-15'+(((g-1)/50000)%6)*interval '1 month','YYYY-MM-DD')"}
with connection.cursor() as c:
 c.execute('INSERT INTO market_netshop_projection ('+','.join(map(q,fields))+') SELECT '+','.join(overrides.get(f,'s.'+q(f)) for f in fields)+" FROM market_netshop_projection s CROSS JOIN generate_series(1,150000) g WHERE s.projection_key='seed'")
 c.execute('ANALYZE')
seed.delete()
print('seeded 320000 rankings,50000 identities,300000 prices,150000 metrics',flush=True)


import django
django.setup()
from django.db import connection
from django.utils import timezone
from market.models import *
from sales.models import SalesOrderLine
from sales.tests.factories import make_line
assert connection.settings_dict['PORT']=='55453'
assert not MarketImageCache.objects.exists() and not SalesOrderLine.objects.exists()
MarketImageCache.objects.bulk_create([MarketImageCache(source_url=f'https://synthetic.invalid/{i}',status='ready',content_sha256='a'*64) for i in range(50000)],batch_size=1000)
MarketBrandSuggestion.objects.bulk_create([MarketBrandSuggestion(id=f'brand-{i}',category=f'类目{i%7}',scope='全部',sku_code=f'sku-{i}',ai_brand='合成品牌') for i in range(25000)],batch_size=1000)
MarketSkuAnnotation.objects.bulk_create([MarketSkuAnnotation(id=f'annotation-{i}',category=f'类目{i%7}',scope='全部',sku_code=f'sku-{i}',image_content_sha256='a'*64,segment='合成细分',reviewed_at=timezone.now()) for i in range(25000)],batch_size=1000)
seed=make_line(1,'synthetic-sales-seed',product_code='seed',last_import_batch_id='synthetic',first_import_batch_id='synthetic');seed.save()
with connection.cursor() as c:c.execute("SELECT setval(pg_get_serial_sequence('sales_order_lines','id'),1,true)")
fields=[f.column for f in SalesOrderLine._meta.concrete_fields if f.column!='id'];q=connection.ops.quote_name
overrides={'source_line_key':"'synthetic-line-'||g::text",'product_code':"'sku-'||((g-1)%50000)::text"}
with connection.cursor() as c:
 c.execute('INSERT INTO sales_order_lines ('+','.join(map(q,fields))+') SELECT '+','.join(overrides.get(f,'s.'+q(f)) for f in fields)+" FROM sales_order_lines s CROSS JOIN generate_series(1,100000) g WHERE s.source_line_key='synthetic-sales-seed'")
 c.execute('ANALYZE')
seed.delete()
with connection.cursor() as c:
 c.execute('CREATE ROLE health_market_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE')
 c.execute('GRANT USAGE ON SCHEMA public TO health_market_reader')
 names=[m._meta.db_table for m in __import__('django.apps',fromlist=['apps']).apps.get_app_config('market').get_models()]+['sales_order_lines']
 c.execute('GRANT SELECT ON '+','.join(map(q,names))+' TO health_market_reader')
print('synthetic enrichment and SELECT-only role ready')
