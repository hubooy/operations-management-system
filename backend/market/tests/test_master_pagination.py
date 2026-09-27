import math
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from market.admin import _master_item, _master_queryset, list_master
from market.errors import MarketApiError
from market.models import (MarketRankingEntry, MarketMasterIdentity, MarketPriceSnapshot,
    MarketImageCache, MarketSkuAnnotation, MarketSkuGmvTotal, MarketBrandSuggestion,
    MarketPriceBandVersion, MarketPriceBandItem)


def legacy_list(params, pending=False):
    """Frozen pre-pagination algorithm, used only on small fixtures."""
    history = pending or bool(params.get('includeHistory'))
    values, seen = [], set()
    for row in _master_queryset(params, history=history):
        item = _master_item(row)
        key = (row.category, row.scope, row.ranking_dimension, row.sku_code, item['month'] if history else '')
        if key in seen:
            continue
        seen.add(key)
        status = 'confirmed' if item['officialMarketPriceCents'] is not None else 'pending' if item['candidatePriceCents'] is not None else 'missing'
        if pending and status == 'confirmed':
            continue
        if params.get('priceStatuses') and status not in params['priceStatuses']:
            continue
        source = 'ai' if item['candidatePriceSource'] == 'ai_suggestion' else 'non_ai'
        if params.get('candidatePriceSources') and source not in params['candidatePriceSources']:
            continue
        if params.get('annotationStatuses') and item['annotationStatus'] not in params['annotationStatuses']:
            continue
        values.append(item)
    values.sort(key=lambda r: (-int(r['gmvTotalCents']), str(r['periodEnd']), int(r['id'])))
    size = params.get('pageSize', 30)
    count = max(1, math.ceil(len(values)/size))
    page = min(params.get('page', 1), count)
    return {'items': values[(page-1)*size:page*size], 'pagination': {
        'page': page, 'pageSize': size, 'total': len(values), 'pageCount': count}}


class MasterPaginationTests(TestCase):
    def entry(self, code, **values):
        return MarketRankingEntry.objects.create(natural_key=str(MarketRankingEntry.objects.count()),
            source_row_number=1, last_import_batch_id='synthetic', **{
                'category': '测试类目', 'scope': '全部', 'ranking_dimension': 'SKU', 'sku_code': code,
                'period_start': '2026-08-01', 'period_end': '2026-08-31', 'rank': 1,
                'brand': '原品牌', 'product_name': '测试产品', 'image_url': 'https://synthetic.invalid/'+code, **values})

    def setUp(self):
        for i in range(12):
            row = self.entry(str(i), rank=None if i%3 == 0 else i)
            MarketMasterIdentity.objects.create(category=row.category, scope=row.scope,
                ranking_dimension=row.ranking_dimension, sku_code=row.sku_code, latest_entry_id=row.id)
            self.entry(str(i), period_start='2026-07-01', period_end='2026-07-31', brand='历史品牌')
            self.entry(str(i), period_start='2026-08-02', rank=10, product_name='旧候选')
            if i < 11:
                MarketPriceSnapshot.objects.create(id=str(i), category=row.category, scope=row.scope,
                    sku_code=row.sku_code, month='2026-08', image_content_sha256='a'*64 if i != 9 else 'a'*63+'\n',
                    confirmation_status='confirmed' if i<4 or i==9 else 'ai_pending' if i==4 else 'source_table',
                    confirmed_market_price_cents=[0,-1,10000,None][i%4], ai_price_type='标准售价' if i!=3 else '定金',
                    source_price_cents=20000 if i<6 else None, average_transaction_price_cents=15000 if i==6 else None,
                    ai_image_price_cents=10000 if i<8 else None)
            if i < 10:
                MarketSkuGmvTotal.objects.create(sku_code=str(i), gmv_total_cents=(i%4)*10000)
            if i%2 == 0:
                digest = '' if i==2 else 'b'*64
                MarketImageCache.objects.create(source_url=row.image_url, status='ready', content_sha256=digest)
                MarketSkuAnnotation.objects.create(id=str(i), category=row.category, scope=row.scope,
                    sku_code=row.sku_code, image_content_sha256=digest, segment='测试', reviewed_at=timezone.now())
            MarketBrandSuggestion.objects.create(id=str(i),category=row.category,scope=row.scope,
                sku_code=row.sku_code,ai_brand='建议品牌')
        MarketPriceBandVersion.objects.create(id='v',category='*',version=1,status='published')
        MarketPriceBandItem.objects.create(id='b',version_id='v',label='测试价带',min_cents=0,max_cents=20000)
        # Exact scope/dimension identity must not share annotation/snapshot accidentally.
        self.entry('2', scope='另榜', ranking_dimension='SPU')

    def test_all_fields_match_old_contract_before_and_after_filters(self):
        cases = [{}, {'includeHistory': True}, {'q': '旧候选', 'includeHistory': True},
            {'q': "%' OR 1=1 --"}, {'brands': ['历史品牌'], 'includeHistory': True},
            {'categories': ['不存在']}, {'rankingDimensions': ['SPU'], 'includeHistory': True},
            *[{'priceStatuses': [v]} for v in ('confirmed','pending','missing','unknown')],
            *[{'candidatePriceSources': [v]} for v in ('ai','non_ai','unknown')],
            *[{'annotationStatuses': [v]} for v in ('committed','pending','unknown')],
            {'priceStatuses': ['pending'], 'candidatePriceSources': ['non_ai'], 'annotationStatuses': ['pending']}]
        for case in cases:
            for pending in (False, True):
                for page in (1, 2, 100):
                    params = {**case, 'pageSize': 5, 'page': page}
                    with self.subTest(params=params, pending=pending):
                        self.assertEqual(list_master(params, pending=pending), legacy_list(params, pending))

    def test_enrichment_and_query_count_bounded_by_page(self):
        with patch('market.admin._master_item', wraps=_master_item) as item, CaptureQueriesContext(connection) as sql:
            result = list_master({'pageSize': 5, 'includeHistory': True, 'annotationStatuses': ['committed']})
        self.assertEqual(item.call_count, len(result['items']))
        self.assertLessEqual(len(sql), 10)
        self.assertTrue(all('raw_json' not in q['sql'] for q in sql))

    def test_next_read_reflects_changes_without_cache(self):
        before = list_master({'priceStatuses': ['confirmed']})
        MarketPriceSnapshot.objects.filter(id='4').update(confirmation_status='confirmed',confirmed_market_price_cents=10000)
        after = list_master({'priceStatuses': ['confirmed']})
        self.assertEqual(after['pagination']['total'], before['pagination']['total']+1)

    def test_removed_page_row_fails_closed_with_retryable_read_error(self):
        with patch('django.db.models.query.QuerySet.in_bulk', return_value={}):
            with self.assertRaises(MarketApiError) as raised:
                list_master({})
        self.assertEqual(raised.exception.status, 503)
