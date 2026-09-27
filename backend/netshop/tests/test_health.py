from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase, override_settings

from netshop.models import NetshopDataRevision
from teruisi_backend.health import (
    ReadinessError,
    _validate_netshop_relation_privilege_rows,
)


class NetshopHealthContractTests(TestCase):
    @override_settings(
        DJANGO_PROCESS_ROLE="netshop_reader",
        DJANGO_EXPECT_READ_ONLY=False,
        DJANGO_ENVIRONMENT="test",
    )
    def test_reader_readiness_is_independent_from_sales_consumer_availability(self) -> None:
        NetshopDataRevision.objects.update_or_create(
            domain="netshop", defaults={"revision": 1, "source_digest": "a" * 64}
        )
        with patch(
            "netshop.sales_client.read_sales_consumer",
            side_effect=AssertionError("netshop readiness must not probe sales"),
        ):
            response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["netshopReader"], "ready")

    def test_writer_relation_grants_are_bounded_to_exact_netshop_dml(self) -> None:
        _validate_netshop_relation_privilege_rows(
            [
                ("public", "netshop_rows", True, True, True, False, False, False),
                (
                    "public",
                    "netshop_write_request_receipts",
                    True,
                    True,
                    True,
                    False,
                    False,
                    False,
                ),
                ("public", "sales_order_lines", False, False, False, False, False, False),
            ],
            application_schema="public",
        )

        for excessive in (
            ("public", "sales_order_lines", True, False, False, False, False, False),
            ("public", "sales_order_lines", False, False, False, False, True, False),
            ("public", "netshop_rows", True, True, True, True, False, False),
            ("other", "netshop_rows", True, False, False, False, False, False),
        ):
            with self.subTest(excessive=excessive):
                with self.assertRaises(ReadinessError):
                    _validate_netshop_relation_privilege_rows(
                        [excessive], application_schema="public"
                    )
