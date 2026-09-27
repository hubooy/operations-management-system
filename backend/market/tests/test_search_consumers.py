from __future__ import annotations

import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from market.models import MarketSkuAnnotation, MarketWriteAuthority
from sales.tests.factories import TEST_SECRET, signed_headers
from .factories import body_bytes, market_row, prepared_payload


@override_settings(MARKET_WRITE_AUTHORITY_EPOCH="11111111-1111-4111-8111-111111111111", MARKET_WRITE_CUTOVER_ID="market-search-test")
@patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
class MarketSearchConsumerTests(TestCase):
    def setUp(self):
        MarketWriteAuthority.objects.filter(id=1).update(
            status="postgres", authority_epoch=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            cutover_id="market-search-test", migration_verify_run_id="market-search-migration", activated_at=timezone.now(),
        )

    def post(self, operation="sku_search", *, query="志高", offset=0, limit=4, role="admin", scope=None, extra=None):
        path = "/api/market/consumers/query"
        payload = {"operation": operation, "query": query, "offset": offset, "limit": limit, **(extra or {})}
        body = body_bytes(payload)
        return self.client.post(path, data=body, content_type="application/json", headers=signed_headers(
            path, method="POST", body=body, role=role, scope=scope, request_id=str(uuid.uuid4()),
        ))

    def seed_rankings(self):
        payload = prepared_payload(
            market_row(skuCode="SKU-A", productName="志高旧机"),
            market_row(sourceRowNumber=3, skuCode="SKU-A", productName="志高100%_新机", periodStart="2026-09-01", periodEnd="2026-09-05", priceCents=12345),
            market_row(sourceRowNumber=4, skuCode="SKU-B", productName="志高其他机", rank=2),
        )
        path = "/api/market/imports"
        body = body_bytes(payload)
        response = self.client.post(path, data=body, content_type="application/json", headers=signed_headers(
            path, method="POST", body=body, request_id="market-search-seed",
        ))
        self.assertEqual(response.status_code, 201, response.content)

    def test_current_identity_literal_search_and_exact_pagination(self):
        self.seed_rankings()
        response = self.post(limit=1)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertRegex(response["X-Market-Data-Revision"], r"^\d+:[a-f0-9]{12}$")
        self.assertEqual(set(response.json()), {"items", "total", "truncated"})
        self.assertEqual(response.json()["total"], 2)
        self.assertEqual(response.json()["items"][0]["title"], "志高100%_新机")
        self.assertEqual(response.json()["items"][0]["amountCents"], 12345)
        self.assertTrue(response.json()["truncated"])
        literal = self.post(query="100%_")
        self.assertEqual(literal.json()["total"], 1)
        self.assertEqual(self.post(query="旧机").json()["total"], 0)
        self.assertEqual(self.post(offset=100).json(), {"items": [], "total": 2, "truncated": False})

    def test_signed_principal_scope_and_batch_roles_fail_closed(self):
        for operation in ("sku_search", "annotation_search", "import_batch_search"):
            response = self.post(operation, scope={"warehouses": [], "channels": [], "platforms": ["京东"]})
            self.assertEqual(response.status_code, 403)
        for role in ("viewer", "analyst"):
            self.assertEqual(self.post("import_batch_search", role=role).status_code, 403)
        self.assertEqual(self.post("import_batch_search", role="operator").status_code, 200)
        self.assertEqual(self.client.post("/api/market/consumers/query", data={}, content_type="application/json").status_code, 401)

    def test_batches_have_the_plain_bounded_consumer_shape(self):
        self.seed_rankings()
        response = self.post("import_batch_search", query="", limit=1)
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(set(payload["items"][0]), {"id", "sourceType", "fileName", "status", "rowCount", "periodStart", "periodEnd", "createdAt", "completedAt"})

    def test_invalid_bounds_unknown_fields_and_revision_changes_are_rejected(self):
        for params in ({"offset": -1}, {"limit": 101}, {"limit": True}, {"query": "字" * 121}, {"extra": {"table": "ai_models"}}):
            self.assertEqual(self.post(**params).status_code, 400)
        with patch("market.views.revision_value", side_effect=["1:aaa", "2:bbb", "3:ccc", "4:ddd"]):
            self.assertEqual(self.post().status_code, 503)

    def test_annotations_expose_only_the_public_search_fields(self):
        MarketSkuAnnotation.objects.create(id="annotation-1", sku_code="SKU-X", category="净水机", segment="商用", image_price_cents=12345, reviewed_at=timezone.now(), reviewed_by="test@example.test")
        response = self.post("annotation_search", query="净水机")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(set(response.json()["items"][0]), {"id", "title", "subtitle", "detail", "updatedAt", "amountCents"})
