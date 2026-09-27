from __future__ import annotations

import json
from unittest.mock import patch

from django.test import SimpleTestCase

from netshop.errors import NetshopApiError
from netshop.sales_client import read_sales_consumer
from sales.auth import Principal


TEST_SECRET = "w8Jp2Qv6Nm4Ts9Kx7By5Rf3Hc1Zd8LuA"


class _Response:
    def __init__(self, revision: str) -> None:
        self.headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Sales-Data-Revision": revision,
        }
        self._body = json.dumps(
            {"operation": "freshness", "data": {"revision": revision}},
            separators=(",", ":"),
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self._body


class NetshopSalesClientTests(SimpleTestCase):
    principal = Principal(
        email="operator@example.invalid",
        display_name="Operator",
        role="operator",
        scope=None,
    )

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    @patch("netshop.sales_client.urllib.request.urlopen")
    def test_accepts_authoritative_sales_and_erp_revision_pair(self, urlopen) -> None:
        urlopen.return_value = _Response("7:3")

        data, revision = read_sales_consumer(
            self.principal, {"operation": "freshness"}
        )

        self.assertEqual(revision, "7:3")
        self.assertEqual(data, {"revision": "7:3"})

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    @patch("netshop.sales_client.urllib.request.urlopen")
    def test_rejects_non_sales_revision_shape(self, urlopen) -> None:
        urlopen.return_value = _Response("7:abcdef012345")

        with self.assertRaises(NetshopApiError) as raised:
            read_sales_consumer(self.principal, {"operation": "freshness"})

        self.assertEqual(raised.exception.code, "service_unavailable")
