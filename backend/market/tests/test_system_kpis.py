from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from market.admin import system_kpis
from market.models import (MarketRankingEntry, MarketMasterIdentity, MarketPriceSnapshot,
    MarketAnnotationItem, MarketAnnotationPromptVersion, MarketSubcategoryTaxonomy, MarketImageCache)


class SystemKpiTests(TestCase):
    def identity(self, name, *, scope='all', dimension='SKU', prompt=True):
        row = MarketRankingEntry.objects.create(natural_key=f'{name}-{scope}-{dimension}',
            source_row_number=1, period_start='2026-09-01', period_end='2026-09-15',
            category=name, scope=scope, ranking_dimension=dimension, sku_code='shared-code', last_import_batch_id='fixture')
        identity = dict(category=name, scope=scope, ranking_dimension=dimension, sku_code='shared-code')
        MarketMasterIdentity.objects.create(**identity, latest_entry_id=row.id)
        if prompt:
            MarketAnnotationPromptVersion.objects.get_or_create(id='prompt-'+name, defaults=dict(
                category=name, version=1, source='manual', status='active', segments_json=['valid'],
                prompt_body='fixture', created_by='fixture@example.invalid'))
        return identity

    def snapshot(self, identity, month='2026-09', image='a', **extra):
        return MarketPriceSnapshot.objects.create(id='|'.join(identity.values())+month, **identity,
            month=month, image_content_sha256=image*64, **extra)

    def result(self, identity, ident, month='2026-09', image='a', **extra):
        return MarketAnnotationItem.objects.create(id=ident, job_id=ident, **identity,
            month=month, image_content_sha256=image*64, **extra)

    def invariant(self, result):
        self.assertEqual(sum(result[k] for k in ['sameImageReuseCount','priceOnlyRecognitionCount',
            'fullRecognitionCount','blockedRecognitionCount']), result['pendingAiCount'])
        self.assertEqual(result['pendingAiCount']+result['completedAiCount'], result['marketIdentityTotal'])

    def test_empty_and_missing_snapshots_are_not_completed(self):
        self.assertTrue(all(n==0 for n in system_kpis().values()))
        self.identity('missing')
        result=system_kpis();self.invariant(result)
        self.assertEqual((result['pendingAiCount'],result['pendingPriceCount'],result['blockedRecognitionCount']), (1,1,1))

    def test_cross_month_routes_deduplicate_and_choose_highest_cost(self):
        reuse=self.identity('reuse')
        self.snapshot(reuse,'2026-07',confirmed_market_price_cents=100,ai_price_type='标准售价')
        self.snapshot(reuse,'2026-08');self.snapshot(reuse)
        price=self.identity('price');self.snapshot(price,image='b')
        self.result(price,'segment','2026-08',status='committed',reviewed_segment='valid')
        full=self.identity('full')
        self.snapshot(full,'2026-07',confirmed_market_price_cents=100,ai_price_type='标准售价')
        self.snapshot(full,'2026-08');self.snapshot(full,image='b')
        for name,kwargs in [('no-image',{}),('spu',{'dimension':'SPU'}),('no-prompt',{'prompt':False})]:
            identity=self.identity(name,**kwargs);self.snapshot(identity,image='' if name=='no-image' else 'a')
        capped=self.identity('capped');self.snapshot(capped)
        self.result(capped,'failed',status='failed',attempt_count=3)
        completed=self.identity('completed');self.snapshot(completed)
        self.result(completed,'done',status='review_pending',ai_segment='valid')
        partial=self.identity('partial');self.snapshot(partial,'2026-08');self.snapshot(partial,image='')
        self.result(partial,'partial-result','2026-08',status='review_pending',ai_segment='valid')
        result=system_kpis();self.invariant(result)
        self.assertEqual(result, dict(marketIdentityTotal=9,pendingPriceCount=9,pendingAiCount=8,
            completedAiCount=1,sameImageReuseCount=1,priceOnlyRecognitionCount=1,
            fullRecognitionCount=1,blockedRecognitionCount=5))

    def test_result_scope_month_image_and_orphans_cannot_hide_work(self):
        identity=self.identity('exact');self.snapshot(identity)
        for index,changes in enumerate([{'scope':'other'},{'ranking_dimension':'SPU'},{'category':'other'},
                                        {'sku_code':'other'},{'month':'2026-08'},{'image':'b'}]):
            args={**identity,**changes};month=args.pop('month','2026-09');image=args.pop('image','a')
            self.result(args,f'wrong-{index}',month,image,status='committed',ai_segment='valid')
        self.snapshot({**identity,'category':'orphan'})
        result=system_kpis();self.invariant(result)
        self.assertEqual(result['pendingAiCount'],1);self.assertEqual(result['fullRecognitionCount'],1)
        self.result(identity,'correct',status='approved',ai_image_price_cents=0)
        self.assertEqual(system_kpis()['completedAiCount'],1)

    def test_current_cache_hash_overrides_stale_snapshot_results(self):
        identity=self.identity('cache')
        self.snapshot(identity,image_url='https://fixture.invalid/image')
        self.result(identity,'old',status='review_pending',ai_segment='valid')
        MarketImageCache.objects.create(source_url='https://fixture.invalid/image',status='ready',content_sha256='b'*64)
        self.assertEqual(system_kpis()['fullRecognitionCount'],1)
        self.result(identity,'new',image='b',status='review_pending',ai_segment='valid')
        self.assertEqual(system_kpis()['pendingAiCount'],0)

    def test_prompt_taxonomy_and_terminal_replacement_are_exact(self):
        identity=self.identity('taxonomy');self.snapshot(identity)
        MarketSubcategoryTaxonomy.objects.create(category='taxonomy',subcategory='changed')
        self.assertEqual(system_kpis()['blockedRecognitionCount'],1)
        MarketSubcategoryTaxonomy.objects.all().delete()
        self.result(identity,'cap',status='failed',attempt_count=3)
        self.assertEqual(system_kpis()['blockedRecognitionCount'],1)
        self.result({**identity,'scope':'other'},'wrong-replacement',status='queued')
        self.assertEqual(system_kpis()['blockedRecognitionCount'],1)
        self.result(identity,'replacement',status='queued')
        self.assertEqual(system_kpis()['fullRecognitionCount'],1)
        MarketAnnotationPromptVersion.objects.update(segments_json=[])
        self.assertEqual(system_kpis()['blockedRecognitionCount'],1)

    def test_rejected_result_does_not_trigger_automatic_reinference(self):
        identity=self.identity('rejected');self.snapshot(identity)
        self.result(identity,'rejected-result',status='rejected',ai_reason='needs manual correction')
        self.assertEqual(system_kpis()['pendingAiCount'],0)

    def test_one_read_only_statement_for_many_identities(self):
        for i in range(40): self.snapshot(self.identity(f'scale-{i}'))
        with CaptureQueriesContext(connection) as queries:
            result=system_kpis()
        self.invariant(result)
        self.assertEqual(result['fullRecognitionCount'],40)
        self.assertEqual(len(queries),1)
        self.assertTrue(queries[0]['sql'].lstrip().startswith('WITH'))
