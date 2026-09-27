from __future__ import annotations

from django.test import TestCase

from netshop.models import (
    NetshopDataRevision,
    NetshopPromotionAggregateManifest,
    NetshopPromotionProductDaily,
)
from netshop.query import promotion_items


class NetshopQueryContractTests(TestCase):
    def setUp(self) -> None:
        NetshopDataRevision.objects.update_or_create(
            domain="netshop", defaults={"revision": 1, "source_digest": "a" * 64}
        )
        NetshopPromotionAggregateManifest.objects.create(
            platform="京东", ready=True, data_version=1
        )

    def test_promotion_item_cutoff_uses_full_filtered_scope_not_current_page(self) -> None:
        NetshopPromotionProductDaily.objects.bulk_create(
            [
                NetshopPromotionProductDaily(
                    platform="京东",
                    shop_name="京东一店",
                    business_date="2026-08-29",
                    product_id="TOP",
                    source="jd_promotion",
                    product_name="高成交商品",
                    net_transaction_amount_cents=99_999,
                    source_batch_id="batch-1",
                    source_batch_count=1,
                    rebuilt_at="2026-08-31T00:00:00Z",
                ),
                NetshopPromotionProductDaily(
                    platform="京东",
                    shop_name="京东一店",
                    business_date="2026-08-30",
                    product_id="LATER",
                    source="jd_promotion",
                    product_name="较晚商品",
                    net_transaction_amount_cents=1,
                    source_batch_id="batch-2",
                    source_batch_count=1,
                    rebuilt_at="2026-08-31T00:00:00Z",
                ),
            ]
        )

        result = promotion_items(
            query="",
            page=1,
            page_size=1,
            platforms=["京东"],
            outlets=[],
            requested_period={"startDate": "2026-08-29", "endDate": "2026-08-30"},
        )

        self.assertEqual(result["items"][0]["id"], "TOP")
        self.assertEqual(result["items"][0]["dateMax"], "2026-08-29")
        self.assertEqual(result["dataCutoffDate"], "2026-08-30")
