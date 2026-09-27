from __future__ import annotations

import json
from unittest.mock import patch

from django.test import TestCase

from netshop.models import NetshopDataRevision, NetshopImportBatch, NetshopRow
from sales.tests.factories import TEST_SECRET, signed_headers


class NetshopConsumerContractTests(TestCase):
    def setUp(self) -> None:
        NetshopDataRevision.objects.update_or_create(
            domain="netshop", defaults={"revision": 2, "source_digest": "b" * 64}
        )
        NetshopImportBatch.objects.create(
            id="master-1", source="jd_product_master", dataset="product_master",
            platform="京东", shop_name="京东一店", file_name="master.xlsx",
            file_size_bytes=10, file_hash="b" * 64, raw_file_hash="c" * 64,
            content_hash="d" * 64, scope_key="e" * 64, published_state_token="f" * 64,
            status="completed", row_count=1, inserted_count=1, created_at="2026-08-30T00:00:00Z",
            completed_at="2026-08-30T00:01:00Z",
        )
        NetshopRow.objects.create(
            source_row_key="master-row", source_row_hash="1" * 64,
            first_import_batch_id="master-1", last_import_batch_id="master-1", source_row_number=2,
            source="jd_product_master", dataset="product_master", platform="京东", shop_name="京东一店",
            snapshot_date="2026-08-30", product_code="SPU-1", product_name="饮水机",
            sku_id="SKU-1", spu_id="SPU-1", metrics_json={}, raw_json={"商家SKU": "ERP-1", "品牌": "志高"},
            brand="志高", created_at="2026-08-30T00:00:00Z", updated_at="2026-08-30T00:00:00Z",
        )
        NetshopImportBatch.objects.create(
            id="daily-1", source="jd_sku_daily", dataset="sku_daily", platform="京东",
            shop_name="京东一店", file_name="daily.xlsx", file_size_bytes=10,
            file_hash="2" * 64, raw_file_hash="3" * 64, content_hash="4" * 64,
            scope_key="5" * 64, published_state_token="6" * 64, status="completed",
            row_count=1, inserted_count=1, date_min="2026-08-30", date_max="2026-08-30",
            created_at="2026-08-30T00:00:00Z", completed_at="2026-08-30T00:01:00Z",
        )
        NetshopRow.objects.create(
            source_row_key="daily-row", source_row_hash="7" * 64,
            first_import_batch_id="daily-1", last_import_batch_id="daily-1", source_row_number=2,
            source="jd_sku_daily", dataset="sku_daily", platform="京东", shop_name="京东一店",
            business_date="2026-08-30", product_code="P-1", product_name="饮水机",
            sku_id="SKU-1", spu_id="SPU-1", metrics_json={"transactionAmountCents": 12345}, raw_json={},
            transaction_amount_cents=12_345, created_at="2026-08-30T00:00:00Z", updated_at="2026-08-30T00:00:00Z",
        )
        NetshopDataRevision.objects.filter(domain="netshop").update(
            revision=3, source_digest="a" * 64)

    def query(self, payload: dict[str, object], *, scope=None, request_id="netshop-consumer"):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}):
            return self.client.post(
                "/api/netshop/consumers/query", data=body, content_type="application/json",
                headers=signed_headers(
                    "/api/netshop/consumers/query", method="POST", body=body,
                    scope=scope, request_id=request_id,
                ),
            )

    def test_product_master_lookup_is_bounded_and_scope_aware(self) -> None:
        response = self.query({
            "operation": "product_master_lookup", "lookupCodes": ["ERP-1"],
            "spuIds": [], "limit": 10,
        })
        self.assertEqual(response.status_code, 200, response.content)
        row = response.json()["data"]["rows"][0]
        self.assertEqual(row["skuId"], "SKU-1")
        self.assertEqual(row["onlineSpecCode"], "ERP-1")
        self.assertEqual(response["X-Netshop-Data-Revision"], "3:aaaaaaaaaaaa")

        denied = self.query(
            {"operation": "product_master_lookup", "lookupCodes": ["ERP-1"], "spuIds": [], "limit": 10},
            scope={"warehouses": [], "channels": [], "platforms": ["天猫"]},
            request_id="netshop-consumer-scoped",
        )
        self.assertEqual(denied.status_code, 200)
        self.assertEqual(denied.json()["data"]["rows"], [])

    def test_market_projection_page_contains_only_minimal_fields(self) -> None:
        response = self.query({
            "operation": "market_projection_page", "offset": 0, "limit": 100,
            "expectedRevision": None,
        })
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["total"], 4)
        self.assertEqual({row["kind"] for row in data["rows"]}, {"metric", "identity", "brand"})
        metric = next(row for row in data["rows"] if row["kind"] == "metric")
        self.assertEqual(metric["transactionAmountCents"], 12_345)
        self.assertNotIn("raw", metric)
        self.assertNotIn("metrics", metric)

        changed = self.query({
            "operation": "market_projection_page", "offset": 0, "limit": 100,
            "expectedRevision": "2:bbbbbbbbbbbb",
        }, request_id="netshop-market-stale")
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.json()["code"], "version_conflict")

    def test_search_latest_heads_preserve_scope_dates_ties_and_exact_pagination(self) -> None:
        from django.db.models import F, OuterRef, Subquery, Value
        from django.db.models.functions import Coalesce
        from netshop.consumers import _latest_rows
        from sales.auth import Principal

        batch_template = NetshopImportBatch.objects.get(pk="master-1")
        row_template = NetshopRow.objects.get(source_row_key="master-row")

        def add(key, *, snapshot=None, date_max=None, completed="2026-08-30T00:01:00Z",
                shop="京东一店", platform="京东", source="jd_product_master", status="completed",
                row_shop=None, batch_ref=None):
            batch = NetshopImportBatch.objects.get(pk=batch_template.pk)
            batch.pk = key
            batch.file_hash = key
            batch.snapshot_date, batch.date_max = snapshot, date_max
            batch.completed_at, batch.shop_name, batch.platform = completed, shop, platform
            batch.source, batch.status = source, status
            batch.save(force_insert=True)
            row = NetshopRow.objects.get(pk=row_template.pk)
            row.pk = None
            row.source_row_key, row.sku_id = key, key
            row.last_import_batch_id = batch_ref or key
            row.shop_name, row.platform, row.source = row_shop or shop, platform, source
            row.save(force_insert=True)

        add("old", snapshot="2026-08-29")
        add("by-date-max", date_max="2026-08-31")
        add("tie-a", snapshot="2026-08-31", completed=None)
        add("tie-z", snapshot="2026-08-31", completed=None)
        add("unpublished", snapshot="2026-09-02", status="processing")
        add("shop-two", snapshot="2026-08-30", shop="京东二店")
        add("tmall", snapshot="2026-08-30", platform="天猫")
        add("promotion", snapshot="2026-08-30", source="jd_promotion")
        add("wrong-shop", snapshot="2026-08-28", row_shop="错误店铺", batch_ref="tie-z")
        # Empty snapshot_date retains the existing COALESCE contract: it does
        # not fall through to date_max and supersede the August head.
        add("empty-snapshot", snapshot="", date_max="2026-09-03")

        principal = Principal(email="analyst@example.test", display_name="Analyst", role="analyst", scope=None)
        latest = (NetshopImportBatch.objects.filter(
            **{field: OuterRef(field) for field in ("source", "dataset", "platform", "shop_name")},
            status="completed",
        ).exclude(source__in=["jd_promotion", "tmall_promotion"])
            .annotate(head_date=Coalesce("snapshot_date", "date_max", Value("")))
            .order_by("-head_date", "-completed_at", "-created_at", "-id").values("id")[:1])
        previous = (NetshopRow.objects.exclude(source__in=["jd_promotion", "tmall_promotion"])
                    .annotate(head=Subquery(latest)).filter(last_import_batch_id=F("head")))
        self.assertSetEqual(set(_latest_rows(principal).values_list("id", flat=True)),
                            set(previous.values_list("id", flat=True)))
        self.assertSetEqual(set(_latest_rows(principal).values_list("source_row_key", flat=True)),
                            {"tie-z", "shop-two", "tmall", "daily-row"})

        payload = {"operation": "row_search", "query": "饮水机", "offset": 0, "limit": 1}
        first = self.query(payload).json()["data"]
        second = self.query({**payload, "offset": 1}, request_id="second").json()["data"]
        self.assertEqual(first["total"], 4)
        self.assertTrue(first["truncated"])
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        denied = self.query(payload, scope={"warehouses": [], "channels": [], "platforms": []},
                            request_id="empty-scope").json()["data"]
        self.assertEqual(denied, {"items": [], "total": 0, "truncated": False})
        scoped = self.query(payload, scope={"warehouses": [], "channels": [], "platforms": ["天猫"]},
                            request_id="tmall-scope").json()["data"]
        self.assertEqual(scoped["total"], 1)
        self.assertEqual(scoped["items"][0]["id"], "tmall:京东一店")
