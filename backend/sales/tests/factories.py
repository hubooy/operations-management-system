from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlsplit

from django.utils.encoding import iri_to_uri

from sales.models import (
    ErpProductMaster,
    SalesDataRevision,
    SalesImportBatch,
    SalesOrderLine,
    sales_projection_values,
)


TEST_SECRET = "django-sales-contract-test-secret-at-least-32-bytes"


def signed_headers(
    url: str,
    *,
    scope=None,
    secret: str = TEST_SECRET,
    role: str = "admin",
    method: str = "GET",
    body: bytes | str = b"",
    request_id: str = "test-request-1",
    email: str = "admin@example.test",
    display_name: str = "Test Admin",
) -> dict[str, str]:
    split = urlsplit(iri_to_uri(url))
    principal = {
        "email": email,
        "displayName": display_name,
        "role": role,
        "scope": scope,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(principal, ensure_ascii=False, separators=(",", ":")).encode()).decode().rstrip("=")
    timestamp = str(int(time.time()))
    body_bytes = body.encode("utf-8") if isinstance(body, str) else body
    body_digest = hashlib.sha256(body_bytes).hexdigest()
    canonical = "\n".join(
        [
            "v1",
            timestamp,
            request_id,
            method.upper(),
            split.path,
            split.query,
            body_digest,
            encoded,
        ]
    )
    signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Teruisi-Principal": encoded,
        "X-Teruisi-Timestamp": timestamp,
        "X-Teruisi-Request-Id": request_id,
        "X-Teruisi-Content-SHA256": body_digest,
        "X-Teruisi-Signature": f"v1={signature}",
    }


def make_line(identifier: int, key: str, **overrides) -> SalesOrderLine:
    defaults = {
        "id": identifier,
        "source_line_key": key,
        "source_row_hash": f"hash-{key}",
        "first_import_batch_id": "batch-1",
        "last_import_batch_id": "batch-1",
        "source_row_number": identifier,
        "order_no": f"order-{identifier}",
        "online_order_no": "",
        "channel": "渠道A",
        "platform": "京东",
        "shop_name": "京东一店",
        "logistics_company": "物流",
        "warehouse": "主仓",
        "product_code": "P1",
        "online_spec_code": "",
        "product_name": "饮水机",
        "specification": "标准",
        "barcode": f"barcode-{identifier}",
        "supplier": "供应商",
        "category": "旧类目",
        "quantity": 1,
        "list_unit_price_cents": 10_000,
        "cost_amount_cents": 7_000,
        "allocated_unit_price_cents": 10_000,
        "allocated_amount_cents": 10_000,
        "fee_allocation_cents": 0,
        "gross_profit_cents": 3_000,
        "gross_margin_bps": 3_000,
        "untaxed_gross_profit_cents": 3_000,
        "untaxed_gross_margin_bps": 3_000,
        "order_time": "2026-08-01 08:00:00",
        "sales_time": "2026-08-01 08:00:00",
        "ship_time": "2026-08-01 10:00:00",
        "line_ship_time": "2026-08-01 10:00:00",
        "business_type": "销售",
        "created_at": "2026-08-01 10:00:00",
        "updated_at": "2026-08-01 10:00:00",
    }
    defaults.update(overrides)
    erp_category = (
        ErpProductMaster.objects.filter(product_code=defaults["product_code"])
        .values_list("category", flat=True)
        .first()
        or ""
    )
    defaults.update(sales_projection_values(defaults, erp_category=erp_category))
    return SalesOrderLine(**defaults)


def install_fixture() -> None:
    # The ERP Django migration owns the singleton revision row.  Keep this
    # cross-domain fixture compatible with a database whose migrations have
    # already seeded that control row.
    SalesDataRevision.objects.update_or_create(
        domain="sales", defaults={"revision": 7, "source_digest": "sales"}
    )
    SalesDataRevision.objects.update_or_create(
        domain="erp", defaults={"revision": 3, "source_digest": "erp"}
    )
    SalesImportBatch.objects.create(
        id="batch-1", source="test", file_name="sales.xlsx", file_size_bytes=100,
        file_hash="a" * 64, sheet_name="销售", status="completed", row_count=5,
        inserted_count=5, duplicate_count=0, warning_count=0, warnings_json="[]",
        totals_json="{}", created_at="2026-08-01 11:00:00", completed_at="2026-08-01 11:01:00",
    )
    ErpProductMaster.objects.create(
        product_code="P1", product_name="饮水机", brand="", specification="", barcode="",
        category="饮水设备", supplier="", product_status="", source_row_number=1,
        last_import_batch_id="erp-1", created_at="2026-08-01 00:00:00", updated_at="2026-08-01 00:00:00",
    )
    SalesOrderLine.objects.bulk_create([
        make_line(1, "L1", quantity=2, allocated_amount_cents=10_000, cost_amount_cents=7_000, gross_profit_cents=3_000),
        make_line(2, "L2", quantity=-1, allocated_amount_cents=-2_000, cost_amount_cents=-1_500, gross_profit_cents=-500, ship_time="2026-08-02 10:00:00"),
        make_line(3, "L3", product_code="P2", product_name="制冰机", category="制冰设备", quantity=1, allocated_amount_cents=5_000, cost_amount_cents=3_500, gross_profit_cents=1_000, ship_time="2026-08-02 11:00:00"),
        make_line(4, "L4", product_code="P3", product_name="未知商品", category="", quantity=1, allocated_amount_cents=1_000, cost_amount_cents=800, gross_profit_cents=200, shop_name="京东二店", ship_time="2026-08-02 12:00:00"),
        make_line(5, "L5", product_code="P4", product_name="排除商品", category="排除品类", warehouse=" 刷刷仓 ", quantity=9, allocated_amount_cents=99_999, cost_amount_cents=49_999, gross_profit_cents=50_000, ship_time="2026-08-02 13:00:00"),
    ])
