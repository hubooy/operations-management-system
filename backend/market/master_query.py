"""Read-only master pagination. Enrichment is restricted to the selected page."""
import math

from django.db import connection
from django.db.models import Q

from .models import (MarketBrandSuggestion, MarketImageCache, MarketPriceSnapshot,
                     MarketSkuAnnotation, MarketSkuGmvTotal)


def _read(sql, params):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def master_page(query, *, history, pending, price_statuses, candidate_sources,
                annotation_statuses, page, page_size):
    columns = ('id', 'category', 'scope', 'ranking_dimension', 'sku_code',
               'period_end', 'rank', 'image_url')
    source, params = query.order_by().values(*columns).query.sql_with_params()
    identity = 'category,scope,ranking_dimension,sku_code'
    if history:
        identity += ',SUBSTR(period_end,1,7)'
    # The previous iterator selected the first row BEFORE status filtering.
    # PostgreSQL ascending rank has NULLS LAST; SQLite ascending puts NULL first.
    preferred = f'''SELECT source.*,ROW_NUMBER() OVER (
        PARTITION BY {identity} ORDER BY period_end DESC,rank,id) preference FROM source'''
    preferred_filter = 'WHERE preference=1'
    if connection.vendor == 'postgresql':
        preferred = f'''SELECT DISTINCT ON ({identity}) source.* FROM source
            ORDER BY {identity},period_end DESC,rank,id'''
        preferred_filter = ''
    regex = '~' if connection.vendor == 'postgresql' else 'REGEXP'
    needs_price = pending or price_statuses or candidate_sources or annotation_statuses
    join = '''LEFT JOIN market_price_snapshots s ON s.category=p.category AND s.scope=p.scope
        AND s.ranking_dimension=p.ranking_dimension AND s.sku_code=p.sku_code
        AND s.month=SUBSTR(p.period_end,1,7)''' if needs_price else ''
    # Master deliberately retains its existing contract (including zero price),
    # which differs from ranking's strictly-positive official-price rule.
    fields = f''', CASE WHEN s.confirmation_status='confirmed'
        AND s.ai_price_type IN ('标准售价','到手价','券后价')
        AND LENGTH(s.image_content_sha256)=64
        AND s.image_content_sha256 {regex} '^[a-f0-9]{{64}}$'
        AND s.confirmed_market_price_cents IS NOT NULL THEN 'confirmed'
        WHEN s.ai_image_price_cents IS NOT NULL OR s.source_price_cents IS NOT NULL
          OR s.average_transaction_price_cents IS NOT NULL THEN 'pending' ELSE 'missing' END price_status,
        CASE WHEN s.ai_image_price_cents IS NOT NULL AND (s.confirmation_status='ai_pending'
          OR (s.source_price_cents IS NULL AND s.average_transaction_price_cents IS NULL))
          THEN 'ai' ELSE 'non_ai' END candidate_source''' if needs_price else ''
    if annotation_statuses:
        join += ''' LEFT JOIN market_image_cache c ON p.image_url<>'' AND c.source_url=p.image_url
            LEFT JOIN market_sku_annotations a ON a.category=p.category AND a.scope=p.scope
            AND a.ranking_dimension=p.ranking_dimension AND a.sku_code=p.sku_code
            AND a.image_content_sha256=CASE WHEN c.status='ready' THEN c.content_sha256
                ELSE COALESCE(s.image_content_sha256,'') END'''
        fields += ",CASE WHEN a.id IS NULL THEN 'pending' ELSE 'committed' END annotation_status"
    conditions = []
    if pending:
        conditions.append("price_status<>'confirmed'")
    params = list(params)
    for name, values in (('price_status', price_statuses), ('candidate_source', candidate_sources),
                         ('annotation_status', annotation_statuses)):
        if values:
            conditions.append(name+' IN ('+','.join(['%s']*len(values))+')')
            params.extend(sorted(values))
    where = 'WHERE '+' AND '.join(conditions) if conditions else ''
    cte = f'''WITH source AS ({source}), preferred AS ({preferred}),
        chosen AS (SELECT * FROM preferred {preferred_filter}),
        enriched AS (SELECT p.* {fields} FROM chosen p {join}),
        selected AS (SELECT * FROM enriched {where})'''
    total = int(_read(cte+' SELECT COUNT(*) FROM selected', params)[0][0])
    safe_page = min(page, max(1, math.ceil(total/page_size)))
    ids = [row[0] for row in _read(cte+''' SELECT s.id FROM selected s
        LEFT JOIN market_sku_gmv_totals g ON g.sku_code=s.sku_code
        ORDER BY COALESCE(g.gmv_total_cents,0) DESC,s.period_end,s.id LIMIT %s OFFSET %s''',
        [*params, page_size, (safe_page-1)*page_size])]
    return ids, {'page': safe_page, 'pageSize': page_size, 'total': total,
                 'pageCount': max(1, math.ceil(total/page_size))}


def identity(row):
    return row.category, row.scope, row.ranking_dimension, row.sku_code


def page_context(rows, versions):
    """Exact identities only; no full-table Python scan or per-row SQL."""
    if not rows:
        return {}
    prices = Q(pk__in=[])
    identities = Q(pk__in=[])
    for row in rows:
        match = Q(category=row.category, scope=row.scope,
                  ranking_dimension=row.ranking_dimension, sku_code=row.sku_code)
        prices |= match & Q(month=row.period_end[:7])
        identities |= match
    snapshots = {(*identity(s), s.month): s for s in MarketPriceSnapshot.objects.filter(prices)}
    caches = MarketImageCache.objects.in_bulk({r.image_url for r in rows if r.image_url})
    annotations = Q(pk__in=[])
    for row in rows:
        snapshot = snapshots.get((*identity(row), row.period_end[:7]))
        cache = caches.get(row.image_url)
        digest = cache.content_sha256 if cache and cache.status == 'ready' else snapshot.image_content_sha256 if snapshot else ''
        annotations |= Q(category=row.category, scope=row.scope, ranking_dimension=row.ranking_dimension,
                         sku_code=row.sku_code, image_content_sha256=digest)
    return {
        'snapshots': snapshots, 'caches': caches,
        'suggestions': {identity(s): s for s in MarketBrandSuggestion.objects.filter(identities)},
        'totals': MarketSkuGmvTotal.objects.in_bulk({r.sku_code for r in rows}),
        'annotations': {(*identity(a), a.image_content_sha256): a for a in MarketSkuAnnotation.objects.filter(annotations)},
        'versions': versions,
    }
