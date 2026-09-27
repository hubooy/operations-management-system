from __future__ import annotations

import json
from unittest.mock import patch

from django.test import TestCase

from finance.models import FinanceTarget, FinanceWriteAuthority
from sales.tests.factories import TEST_SECRET, signed_headers

from .factories import body_bytes, prepared_payload


class FinanceConsumerApiTests(TestCase):
    def setUp(self) -> None:
        FinanceWriteAuthority.objects.filter(id=1).update(status="postgres")
        payload = prepared_payload("2026-08")
        body = body_bytes(payload)
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}):
            response = self.client.post(
                "/api/finance/imports",
                data=body,
                content_type="application/json",
                headers=signed_headers(
                    "/api/finance/imports",
                    method="POST",
                    body=body,
                    request_id="finance-consumer-fixture",
                ),
            )
        self.assertEqual(response.status_code, 201, response.content)
        FinanceTarget.objects.bulk_create(
            [
                FinanceTarget(
                    id="target-jd",
                    period_type="month",
                    period_key="2026-08",
                    platform="京东",
                    shop_name="同名店",
                    category="净水设备",
                    manager="京东负责人",
                    sales_target_cents=100_000,
                    created_at="2026-08-01T00:00:00Z",
                    updated_at="2026-08-02T00:00:00Z",
                ),
                FinanceTarget(
                    id="target-tmall",
                    period_type="month",
                    period_key="2026-08",
                    platform="天猫",
                    shop_name="同名店",
                    category="净水设备",
                    manager="天猫负责人",
                    sales_target_cents=200_000,
                    created_at="2026-08-01T00:00:00Z",
                    updated_at="2026-08-03T00:00:00Z",
                ),
            ]
        )

    def query(
        self,
        payload: dict[str, object],
        *,
        role: str = "admin",
        scope=None,
        request_id: str = "finance-consumer-query",
    ):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}):
            return self.client.post(
                "/api/finance/consumers/query",
                data=body,
                content_type="application/json",
                headers=signed_headers(
                    "/api/finance/consumers/query",
                    method="POST",
                    body=body,
                    role=role,
                    scope=scope,
                    request_id=request_id,
                ),
            )

    def test_line_search_is_bounded_and_revision_stamped(self) -> None:
        response = self.query(
            {"operation": "line_search", "query": "销售费用", "offset": 0, "limit": 2}
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["operation"], "line_search")
        self.assertEqual(len(data["data"]["items"]), 2)
        self.assertGreaterEqual(data["data"]["total"], 2)
        self.assertEqual(
            data["data"]["truncated"], 2 < data["data"]["total"]
        )
        self.assertTrue(response["X-Finance-Data-Revision"].startswith("1:"))

    def test_target_search_keeps_platform_scope_identity(self) -> None:
        response = self.query(
            {"operation": "target_search", "query": "负责人", "offset": 0, "limit": 10},
            scope={"warehouses": [], "channels": [], "platforms": ["京东"]},
        )
        self.assertEqual(response.status_code, 200, response.content)
        items = response.json()["data"]["items"]
        self.assertEqual([item["id"] for item in items], ["target-jd"])

    def test_import_search_requires_unrestricted_operator_or_admin(self) -> None:
        payload = {
            "operation": "import_batch_search",
            "query": "财报",
            "offset": 0,
            "limit": 10,
        }
        allowed = self.query(payload, role="operator", request_id="finance-import-search-ok")
        self.assertEqual(allowed.status_code, 200, allowed.content)
        self.assertEqual(allowed.json()["data"]["total"], 1)

        restricted = self.query(
            payload,
            role="operator",
            scope={"warehouses": [], "channels": [], "platforms": ["京东"]},
            request_id="finance-import-search-restricted",
        )
        self.assertEqual(restricted.status_code, 403)
        self.assertEqual(restricted.json()["code"], "access_denied")

        analyst = self.query(
            payload, role="analyst", request_id="finance-import-search-analyst"
        )
        self.assertEqual(analyst.status_code, 403)

    def test_unknown_or_duplicate_fields_fail_closed(self) -> None:
        unknown = self.query(
            {
                "operation": "line_search",
                "query": "销售费用",
                "offset": 0,
                "limit": 2,
                "sql": "SELECT *",
            },
            request_id="finance-consumer-unknown",
        )
        self.assertEqual(unknown.status_code, 400)

        raw = (
            b'{"operation":"line_search","query":"AA","query":"BB",'
            b'"offset":0,"limit":2}'
        )
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}):
            duplicate = self.client.post(
                "/api/finance/consumers/query",
                data=raw,
                content_type="application/json",
                headers=signed_headers(
                    "/api/finance/consumers/query",
                    method="POST",
                    body=raw,
                    request_id="finance-consumer-duplicate",
                ),
            )
        self.assertEqual(duplicate.status_code, 400)
