from __future__ import annotations

import copy
import hashlib
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from netshop.errors import NetshopApiError
from netshop.models import (
    NetshopDataRevision,
    NetshopImportAttempt,
    NetshopImportBatch,
    NetshopImportScopeHead,
    NetshopRow,
    NetshopWriteAuthority,
    NetshopWriteRequestReceipt,
)
from sales.tests.factories import TEST_SECRET, signed_headers

from .factories import body_bytes, netshop_row, prepared_payload


class NetshopApiContractTests(TestCase):
    def setUp(self) -> None:
        NetshopWriteAuthority.objects.filter(id=1).update(
            status="postgres",
            authority_epoch=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            cutover_id="netshop-test-cutover",
            migration_verify_run_id="netshop-test-migration",
            activated_at=timezone.now(),
        )

    def post(self, payload: dict[str, object], request_id: str):
        body = body_bytes(payload)
        return self.client.post(
            "/api/netshop/imports",
            data=body,
            content_type="application/json; charset=utf-8",
            headers=signed_headers(
                "/api/netshop/imports", method="POST", body=body, request_id=request_id
            ),
        )

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_promotion_import_and_duplicate_expose_exact_database_readback(self) -> None:
        for shop_index, shop in enumerate(["志高商用设备旗舰店", "志高切肉机旗舰店"]):
            with self.subTest(shop=shop):
                rows = [netshop_row(
                    source="jd_promotion", dataset="ad", shop_name=shop,
                    business_date=f"2026-09-0{day}", sku_id=f"SKU-{day}",
                    metrics={"spendCents": day * 100, "impressions": 100, "clicks": 5},
                    row_number=day + 1,
                ) for day in [4, 5]]
                payload = prepared_payload(*rows, raw_seed=shop)
                for attempt, expected_status in enumerate(["imported", "duplicate"]):
                    response = self.post(payload, f"promotion-proof-{shop_index}-{attempt}")
                    self.assertEqual(response.status_code, 201 if attempt == 0 else 200, response.content)
                    result = response.json()
                    self.assertEqual(result["status"], expected_status)
                    self.assertEqual(result["batch"]["totals"]["rawFileHash"], payload["rawFileHash"])
                    proof = result["verification"]
                    self.assertTrue(proof["verified"])
                    self.assertEqual(proof["readbackRowCount"], 2)
                    self.assertEqual(proof["rowCount"], 2)
                    self.assertEqual(proof["dataset"], "ad")
                    self.assertEqual(proof["platform"], "京东")
                    self.assertEqual(proof["shopName"], shop)
                    self.assertEqual([proof["dateMin"], proof["dateMax"]], ["2026-09-04", "2026-09-05"])
                self.assertEqual(NetshopRow.objects.filter(shop_name=shop).count(), 2)
                self.assertEqual(NetshopImportBatch.objects.filter(shop_name=shop).count(), 1)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_duplicate_readback_dates_describe_snapshot_facts_not_scope_bounds(self) -> None:
        payload = prepared_payload(netshop_row(
            source="jd_product_master", dataset="product_master",
            business_date="", snapshot_date="2026-09-06",
        ))
        first = self.post(payload, "snapshot-proof-first")
        self.assertEqual(first.status_code, 201, first.content)
        duplicate = self.post(payload, "snapshot-proof-duplicate")
        self.assertEqual(duplicate.status_code, 200, duplicate.content)
        for response in [first, duplicate]:
            self.assertEqual(response.json()["verification"]["readbackRowCount"], 1)
            self.assertIsNone(response.json()["verification"]["dateMin"])
            self.assertIsNone(response.json()["verification"]["dateMax"])

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_import_is_typed_atomic_idempotent_and_replay_fenced(self) -> None:
        payload = prepared_payload(netshop_row())
        first = self.post(payload, "netshop-import-1")
        self.assertEqual(first.status_code, 201, first.content)
        first_payload = first.json()
        self.assertEqual(first_payload["status"], "imported")
        self.assertEqual(first_payload["verification"]["verified"], True)
        self.assertEqual(first_payload["verification"]["rowCount"], 1)
        self.assertEqual(first_payload["verification"]["readbackRowCount"], 1)
        self.assertEqual(first_payload["verification"]["dataset"], payload["dataset"])
        self.assertEqual(first_payload["verification"]["platform"], payload["platform"])
        self.assertEqual(first_payload["verification"]["shopName"], payload["shopName"])
        self.assertEqual(first_payload["verification"]["dateMin"], payload["startDate"])
        self.assertEqual(first_payload["verification"]["dateMax"], payload["endDate"])
        row = NetshopRow.objects.get()
        self.assertEqual(row.transaction_amount_cents, 12_345)
        self.assertEqual(row.visitors, 50)
        self.assertEqual(NetshopImportBatch.objects.count(), 1)

        replay = self.post(payload, "netshop-import-1")
        self.assertEqual(replay.status_code, 201, replay.content)
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        self.assertEqual(NetshopImportBatch.objects.count(), 1)

        duplicate = copy.deepcopy(payload)
        duplicate["rawFileHash"] = hashlib.sha256(b"resaved-file").hexdigest()
        duplicate["fileName"] = "重新保存.xlsx"
        response = self.post(duplicate, "netshop-import-2")
        self.assertEqual(response.status_code, 200, response.content)
        duplicate_payload = response.json()
        self.assertEqual(duplicate_payload["status"], "duplicate")
        self.assertEqual(duplicate_payload["verification"]["verified"], True)
        self.assertEqual(duplicate_payload["verification"]["rowCount"], 1)
        self.assertEqual(duplicate_payload["verification"]["readbackRowCount"], 1)
        self.assertEqual(duplicate_payload["verification"]["dataset"], payload["dataset"])
        self.assertEqual(duplicate_payload["verification"]["platform"], payload["platform"])
        self.assertEqual(duplicate_payload["verification"]["shopName"], payload["shopName"])
        self.assertEqual(duplicate_payload["verification"]["dateMin"], payload["startDate"])
        self.assertEqual(duplicate_payload["verification"]["dateMax"], payload["endDate"])
        self.assertEqual(NetshopImportAttempt.objects.filter(outcome="duplicate").count(), 1)

        collision = copy.deepcopy(payload)
        collision["rawFileHash"] = hashlib.sha256(b"collision").hexdigest()
        response = self.post(collision, "netshop-import-1")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "version_conflict")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_changed_exact_scope_replaces_rows_and_advances_revision(self) -> None:
        first = self.post(prepared_payload(netshop_row()), "netshop-replace-1")
        self.assertEqual(first.status_code, 201, first.content)
        old_batch = first.json()["batch"]["id"]
        changed_row = netshop_row(
            metrics={"transactionAmountCents": 98_765, "transactionQuantity": 3}
        )
        changed = prepared_payload(changed_row, raw_seed="changed")
        second = self.post(changed, "netshop-replace-2")
        self.assertEqual(second.status_code, 201, second.content)
        self.assertNotEqual(second.json()["batch"]["id"], old_batch)
        self.assertEqual(NetshopRow.objects.count(), 1)
        self.assertEqual(NetshopRow.objects.get().transaction_amount_cents, 98_765)
        head = NetshopImportScopeHead.objects.get()
        self.assertEqual(head.current_batch_id, second.json()["batch"]["id"])
        self.assertEqual(head.status, "ready")
        self.assertEqual(NetshopDataRevision.objects.get(domain="netshop").revision, 2)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_same_historical_exact_scope_is_duplicate_after_another_date_changes(self) -> None:
        day_one = netshop_row(business_date="2026-08-29", sku_id="SKU-1")
        day_two = netshop_row(
            business_date="2026-08-30", sku_id="SKU-2", row_number=3
        )
        first = self.post(prepared_payload(day_one, day_two), "netshop-range-1")
        self.assertEqual(first.status_code, 201, first.content)

        changed_day_two = netshop_row(
            business_date="2026-08-30",
            sku_id="SKU-2",
            metrics={"transactionAmountCents": 88_888, "transactionQuantity": 4},
            row_number=3,
        )
        changed = self.post(
            prepared_payload(changed_day_two, raw_seed="changed-day-two"),
            "netshop-range-2",
        )
        self.assertEqual(changed.status_code, 201, changed.content)

        retry_day_one = self.post(
            prepared_payload(day_one, raw_seed="resaved-day-one"),
            "netshop-range-3",
        )
        self.assertEqual(retry_day_one.status_code, 200, retry_day_one.content)
        self.assertEqual(retry_day_one.json()["status"], "duplicate")
        self.assertEqual(NetshopImportBatch.objects.count(), 2)
        self.assertEqual(NetshopRow.objects.count(), 2)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_publication_failure_is_audited_releases_scope_and_can_retry_request_id(self) -> None:
        payload = prepared_payload(netshop_row())
        with patch(
            "netshop.import_service._build_row_models",
            side_effect=NetshopApiError(
                "测试发布失败", code="service_unavailable", status=503
            ),
        ):
            failed = self.post(payload, "netshop-publication-failure")
        self.assertEqual(failed.status_code, 503, failed.content)
        attempt = NetshopImportAttempt.objects.get()
        self.assertEqual(attempt.outcome, "failed")
        self.assertEqual(attempt.error_code, "SERVICE_UNAVAILABLE")
        self.assertTrue(attempt.metadata["reservationPersisted"])
        self.assertTrue(attempt.metadata["reservationReleased"])
        self.assertEqual(NetshopImportBatch.objects.count(), 0)
        self.assertEqual(NetshopRow.objects.count(), 0)
        self.assertEqual(NetshopImportScopeHead.objects.get().status, "ready")
        self.assertFalse(
            NetshopWriteRequestReceipt.objects.filter(
                request_id="netshop-publication-failure"
            ).exists()
        )

        retry = self.post(payload, "netshop-publication-failure")
        self.assertEqual(retry.status_code, 201, retry.content)
        self.assertEqual(retry.json()["status"], "imported")
        self.assertEqual(NetshopImportAttempt.objects.filter(outcome="failed").count(), 1)
        self.assertEqual(NetshopImportAttempt.objects.filter(outcome="imported").count(), 1)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_stale_processing_request_receipt_is_reclaimed_with_exact_binding(self) -> None:
        payload = prepared_payload(netshop_row())
        body = body_bytes(payload)
        receipt = NetshopWriteRequestReceipt.objects.create(
            request_id="netshop-stale-receipt",
            body_sha256=hashlib.sha256(body).hexdigest(),
            query_sha256=hashlib.sha256(b"").hexdigest(),
            method="POST",
            path="/api/netshop/imports",
            actor_email="admin@example.test",
        )
        NetshopWriteRequestReceipt.objects.filter(pk=receipt.pk).update(
            created_at=timezone.now() - timedelta(minutes=6)
        )

        response = self.post(payload, "netshop-stale-receipt")
        self.assertEqual(response.status_code, 201, response.content)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, "completed")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_writer_fails_closed_until_postgres_authority_is_active(self) -> None:
        NetshopWriteAuthority.objects.filter(id=1).update(
            status="d1",
            authority_epoch=None,
            cutover_id="",
            activated_at=None,
        )
        response = self.post(prepared_payload(netshop_row()), "netshop-authority-off")
        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response.json()["code"], "netshop_write_authority_inactive")
        self.assertEqual(NetshopRow.objects.count(), 0)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_rejected_parser_result_is_audited_without_claiming_scope(self) -> None:
        rejected = {
            "schemaVersion": "netshop-normalized-v1",
            "disposition": "rejected",
            "fileName": "坏文件.xlsx",
            "fileSizeBytes": 12,
            "rawFileHash": hashlib.sha256(b"bad").hexdigest(),
            "source": "jd_sku_daily",
            "platform": "京东",
            "shopName": "京东一店",
            "warnings": [],
            "errors": [{"code": "PARSE_ERROR", "message": "解析失败"}],
            "message": "解析失败",
        }
        response = self.post(rejected, "netshop-rejected")
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(NetshopImportBatch.objects.count(), 0)
        self.assertEqual(NetshopImportScopeHead.objects.count(), 0)
        self.assertEqual(NetshopImportAttempt.objects.get().outcome, "rejected")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_duplicate_json_keys_are_rejected(self) -> None:
        body = b'{"operation":"brand_options","query":"A","query":"B","limit":10}'
        response = self.client.post(
            "/api/netshop/consumers/query",
            data=body,
            content_type="application/json",
            headers=signed_headers(
                "/api/netshop/consumers/query",
                method="POST",
                body=body,
                request_id="netshop-duplicate-json",
            ),
        )
        self.assertEqual(response.status_code, 400)
