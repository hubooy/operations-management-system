from __future__ import annotations

import copy
import hashlib
import json
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from erp_reference.import_service import IMPORT_VERSION
from erp_reference.models import (
    ErpComboItem,
    ErpProductMaster,
    ErpReferenceImportAttempt,
    ErpReferenceImportBatch,
    ErpReferenceWriteAuthority,
)
from sales.models import SalesDataRevision, SalesOrderLine
from sales.tests.factories import TEST_SECRET, make_line, signed_headers


def product_payload(*, raw_seed: str = "products", rows: list[dict[str, object]] | None = None):
    normalized_rows = rows or [
        {
            "productCode": "P1", "productName": "商用饮水机", "brand": "TERUISI",
            "specification": "标准款", "barcode": "690000000001", "category": "饮水设备",
            "supplier": "供应商A", "productStatus": "在售", "sourceRowNumber": 2,
        },
        {
            "productCode": "P2", "productName": "商用制冰机", "brand": "TERUISI",
            "specification": "80kg", "barcode": "690000000002", "category": "制冰设备",
            "supplier": "供应商B", "productStatus": "在售", "sourceRowNumber": 3,
        },
    ]
    return {
        "version": IMPORT_VERSION, "source": "products", "fileName": "货品.xlsx",
        "fileSizeBytes": 1024, "rawFileHash": hashlib.sha256(raw_seed.encode()).hexdigest(),
        "sheetName": "货品", "sourceRowCount": len(normalized_rows), "rows": normalized_rows,
        "warnings": [], "totals": {"sourceRowCount": len(normalized_rows)},
    }


def combo_payload(*, raw_seed: str = "combos"):
    return {
        "version": IMPORT_VERSION, "source": "combos", "fileName": "组合装.xlsx",
        "fileSizeBytes": 512, "rawFileHash": hashlib.sha256(raw_seed.encode()).hexdigest(),
        "sheetName": "组合装", "sourceRowCount": 1,
        "rows": [{
            "parentCode": "KIT-1", "parentName": "饮水组合",
            "childCode": "P1", "childName": "商用饮水机",
            "childQuantityMilli": 2000, "sourceRowNumber": 2,
        }],
        "warnings": [], "totals": {"sourceRowCount": 1},
    }


class ErpReferenceApiTests(TestCase):
    def setUp(self) -> None:
        SalesDataRevision.objects.update_or_create(
            domain="erp", defaults={"revision": 0, "source_digest": "0" * 64}
        )
        ErpReferenceWriteAuthority.objects.filter(id=1).update(
            status="postgres",
            authority_epoch=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            cutover_id="erp-reference-test-cutover",
            migration_verify_run_id="erp-reference-test-migration",
            activated_at=timezone.now(),
        )

    def post_import(self, payload: dict[str, object], request_id: str):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        return self.client.post(
            "/api/erp-reference/imports",
            data=body,
            content_type="application/json; charset=utf-8",
            headers=signed_headers(
                "/api/erp-reference/imports", method="POST", body=body, request_id=request_id
            ),
        )

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_category_fallback_uses_only_erp_sales_column_permissions(self) -> None:
        for index, category in enumerate(["\t 原类目 \n", "", "\u3000", "\t 原类目 \n"], 1):
            make_line(index, f"fallback-{index}", product_code="P1", category=category).save(force_insert=True)
        first = self.post_import(product_payload(), "erp-fallback-permissions-1")
        self.assertEqual(first.status_code, 201, first.content)
        before = list(SalesOrderLine.objects.order_by("id").values())
        payload = product_payload(raw_seed="removed-P1")
        payload["rows"] = [payload["rows"][1]]
        payload["sourceRowCount"] = 1
        with CaptureQueriesContext(connection) as queries:
            response = self.post_import(payload, "erp-fallback-permissions-2")
        self.assertEqual(response.status_code, 201, response.content)
        sales_selects = [q["sql"] for q in queries if q["sql"].lstrip().upper().startswith("SELECT") and '"sales_order_lines"' in q["sql"]]
        self.assertTrue(sales_selects)
        for sql in sales_selects:
            self.assertNotIn('"sales_order_lines"."id"', sql)
        after = list(SalesOrderLine.objects.order_by("id").values())
        self.assertEqual([row["resolved_category"] for row in after], ["原类目", "未分类", "未分类", "原类目"])
        for previous, current in zip(before, after):
            previous.pop("resolved_category")
            current.pop("resolved_category")
            self.assertEqual(previous, current)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_product_and_combo_imports_are_atomic_idempotent_and_replay_fenced(self) -> None:
        make_line(1, "erp-line-1", product_code="P1", category="旧类目").save(force_insert=True)
        first_payload = product_payload()
        first = self.post_import(first_payload, "erp-import-products-1")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(first.json()["status"], "imported")
        self.assertEqual(ErpProductMaster.objects.count(), 2)
        self.assertEqual(SalesOrderLine.objects.get(id=1).resolved_category, "饮水设备")
        self.assertEqual(SalesDataRevision.objects.get(domain="erp").revision, 1)

        replay = self.post_import(first_payload, "erp-import-products-1")
        self.assertEqual(replay.status_code, 201, replay.content)
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        self.assertEqual(ErpReferenceImportBatch.objects.count(), 1)

        duplicate_payload = copy.deepcopy(first_payload)
        duplicate_payload["rawFileHash"] = hashlib.sha256(b"resaved-products").hexdigest()
        duplicate = self.post_import(duplicate_payload, "erp-import-products-2")
        self.assertEqual(duplicate.status_code, 200, duplicate.content)
        self.assertEqual(duplicate.json()["status"], "duplicate")
        self.assertEqual(ErpReferenceImportAttempt.objects.filter(outcome="duplicate").count(), 1)
        self.assertEqual(SalesDataRevision.objects.get(domain="erp").revision, 1)

        collision = copy.deepcopy(first_payload)
        collision["rawFileHash"] = hashlib.sha256(b"request-id-collision").hexdigest()
        rejected = self.post_import(collision, "erp-import-products-1")
        self.assertEqual(rejected.status_code, 409, rejected.content)
        self.assertEqual(rejected.json()["code"], "version_conflict")

        combo = self.post_import(combo_payload(), "erp-import-combos-1")
        self.assertEqual(combo.status_code, 201, combo.content)
        self.assertEqual(ErpComboItem.objects.get().child_quantity_milli, 2000)
        self.assertEqual(SalesDataRevision.objects.get(domain="erp").revision, 2)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_replacement_restores_sales_fallback_and_consumer_is_bounded(self) -> None:
        make_line(1, "erp-line-2", product_code="P1", category="原销售类目").save(force_insert=True)
        imported = self.post_import(product_payload(), "erp-replacement-1")
        self.assertEqual(imported.status_code, 201, imported.content)
        replacement_rows = [{
            "productCode": "P2", "productName": "商用制冰机", "brand": "TERUISI",
            "specification": "80kg", "barcode": "690000000002", "category": "新制冰设备",
            "supplier": "供应商B", "productStatus": "在售", "sourceRowNumber": 2,
        }]
        replaced = self.post_import(
            product_payload(raw_seed="replacement", rows=replacement_rows), "erp-replacement-2"
        )
        self.assertEqual(replaced.status_code, 201, replaced.content)
        self.assertEqual(list(ErpProductMaster.objects.values_list("product_code", flat=True)), ["P2"])
        self.assertEqual(SalesOrderLine.objects.get(id=1).resolved_category, "原销售类目")

        query_payload = {
            "operation": "product_search",
            "params": {"query": "制冰", "offset": 0, "limit": 10},
        }
        body = json.dumps(query_payload, ensure_ascii=False, separators=(",", ":")).encode()
        consumer = self.client.post(
            "/api/erp-reference/consumers/query",
            data=body,
            content_type="application/json; charset=utf-8",
            headers=signed_headers(
                "/api/erp-reference/consumers/query", method="POST", body=body,
                request_id="erp-consumer-1",
            ),
        )
        self.assertEqual(consumer.status_code, 200, consumer.content)
        self.assertEqual(consumer.json()["data"]["total"], 1)
        self.assertTrue(consumer["X-Erp-Reference-Data-Revision"].startswith("2:"))

        denied = self.client.post(
            "/api/erp-reference/consumers/query",
            data=body,
            content_type="application/json; charset=utf-8",
            headers=signed_headers(
                "/api/erp-reference/consumers/query", method="POST", body=body,
                request_id="erp-consumer-scoped", scope={"warehouses": ["主仓"]},
            ),
        )
        self.assertEqual(denied.status_code, 403)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_edge_rejection_is_audited_without_creating_fact_batch(self) -> None:
        payload = {
            "kind": "rejection", "version": IMPORT_VERSION, "source": "products",
            "fileName": "坏文件.xlsx", "fileSizeBytes": 12, "rawFileHash": "a" * 64,
            "message": "缺少固定表头", "warnings": [],
            "errors": [{"code": "INVALID_HEADER", "message": "缺少固定表头"}],
        }
        response = self.post_import(payload, "erp-rejection-1")
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(response.json()["status"], "rejected")
        self.assertEqual(ErpReferenceImportBatch.objects.count(), 0)
        self.assertEqual(ErpReferenceImportAttempt.objects.get().outcome, "rejected")
