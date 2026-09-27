from __future__ import annotations

import json
import importlib
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import clear_url_caches
from django.utils import timezone
from datetime import timedelta

from sales.models import (
    SalesCutoverAttestation,
    SalesRawUploadSession,
    SalesWriteAuthority,
    SalesWriteRequestReceipt,
)
from sales.write_requests import (
    claim_write_request,
    complete_write_request,
)
from sales.write_service import SalesImportServiceError
from sales.write_service import (
    begin_raw_upload,
    begin_staged_import,
    claim_raw_upload,
    complete_staged_import,
    register_raw_upload_chunk,
    stage_normalized_chunk,
)

from .factories import TEST_SECRET, make_line, signed_headers
from .test_write_service import (
    AUTHORITY_EPOCH,
    CUTOVER_ID,
    normalized_row,
    raw_chunk_registration,
)
from .cutover_fixtures import install_writer_runtime_guard


def json_body(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


@patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
@override_settings(
    DJANGO_PROCESS_ROLE="sales_writer",
    DJANGO_EXPECT_READ_ONLY=False,
    SALES_WRITE_AUTHORITY_EPOCH=AUTHORITY_EPOCH,
    SALES_WRITE_CUTOVER_ID=CUTOVER_ID,
)
class SalesWriteApiTests(TestCase):
    def setUp(self) -> None:
        SalesWriteAuthority.objects.filter(id=1).update(
            status="active",
            authority_epoch=AUTHORITY_EPOCH,
            cutover_id=CUTOVER_ID,
            activated_at=timezone.now(),
        )
        install_writer_runtime_guard(CUTOVER_ID)

    def _post(
        self,
        url: str,
        payload: dict[str, object],
        *,
        request_id: str,
        role: str = "admin",
        scope=None,
    ):
        body = json_body(payload)
        return self.client.post(
            url,
            data=body,
            content_type="application/json",
            headers=signed_headers(
                url,
                role=role,
                scope=scope,
                method="POST",
                body=body,
                request_id=request_id,
            ),
        )

    def test_request_id_receipt_replays_exact_response_and_rejects_changed_body(self) -> None:
        url = "/api/sales/imports/uploads"
        payload: dict[str, object] = {
            "action": "init",
            "fingerprint": "api-upload",
            "fileName": "sales.xlsx",
            "fileSizeBytes": 1,
            "chunkCount": 1,
            "expectedStartDate": "2024-01-01",
            "expectedEndDate": "2024-01-01",
        }
        first = self._post(url, payload, request_id="write-replay-1")
        self.assertEqual(first.status_code, 200, first.content)
        replay = self._post(url, payload, request_id="write-replay-1")
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.json(), first.json())
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        receipt = SalesWriteRequestReceipt.objects.get(request_id="write-replay-1")
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(receipt.response_status, 200)

        changed = self._post(
            url,
            {**payload, "fileName": "different.xlsx"},
            request_id="write-replay-1",
        )
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.json()["code"], "request_replay_mismatch")

    def test_post_fails_closed_with_zero_business_write_after_attestation_is_deleted(self) -> None:
        SalesCutoverAttestation.objects.all().delete()
        response = self._post(
            "/api/sales/imports/uploads",
            {
                "action": "init",
                "fingerprint": "missing-attestation",
                "fileName": "sales.xlsx",
                "fileSizeBytes": 1,
                "chunkCount": 1,
                "expectedStartDate": "2024-01-01",
                "expectedEndDate": "2024-01-01",
            },
            request_id="missing-attestation-1",
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(SalesRawUploadSession.objects.count(), 0)

    def test_post_fails_closed_with_zero_business_write_after_erp_facts_drift(self) -> None:
        from sales.models import ErpProductMaster

        ErpProductMaster.objects.filter(product_code="GUARD-ERP-1").update(
            product_name="tampered-after-checkpoint"
        )
        response = self._post(
            "/api/sales/imports/uploads",
            {
                "action": "init",
                "fingerprint": "drifted-erp",
                "fileName": "sales.xlsx",
                "fileSizeBytes": 1,
                "chunkCount": 1,
                "expectedStartDate": "2024-01-01",
                "expectedEndDate": "2024-01-01",
            },
            request_id="drifted-erp-1",
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(SalesRawUploadSession.objects.count(), 0)

    def test_receipt_claim_token_fences_stale_completion_and_survives_authority_rotation(self) -> None:
        first = claim_write_request(
            request_id="receipt-aba-1",
            actor_email="admin@example.test",
            method="POST",
            path="/api/sales/imports/uploads",
            body_sha256="a" * 64,
        )
        SalesWriteRequestReceipt.objects.filter(request_id=first.request_id).update(
            updated_at=timezone.now() - timedelta(minutes=6)
        )
        second = claim_write_request(
            request_id="receipt-aba-1",
            actor_email="admin@example.test",
            method="POST",
            path="/api/sales/imports/uploads",
            body_sha256="a" * 64,
        )
        self.assertNotEqual(first.claim_token, second.claim_token)
        with self.assertRaises(SalesImportServiceError):
            complete_write_request(
                first, response_status=200, response_payload={"owner": "stale"}
            )
        SalesWriteAuthority.objects.filter(id=1).update(status="disabled")
        complete_write_request(
            second, response_status=200, response_payload={"owner": "current"}
        )
        receipt = SalesWriteRequestReceipt.objects.get(request_id=first.request_id)
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(receipt.response_payload, {"owner": "current"})

    def test_rejected_service_response_is_also_replay_safe(self) -> None:
        url = "/api/sales/imports/staged"
        payload = {"action": "unknown"}
        first = self._post(url, payload, request_id="write-rejected-1")
        self.assertEqual(first.status_code, 400)
        replay = self._post(url, payload, request_id="write-rejected-1")
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        self.assertEqual(first.json(), replay.json())

    def test_unknown_top_level_fields_are_rejected_and_receipted(self) -> None:
        response = self._post(
            "/api/sales/imports/uploads",
            {
                "action": "init",
                "fingerprint": "api-unknown",
                "fileName": "sales.xlsx",
                "fileSizeBytes": 1,
                "chunkCount": 1,
                "expectedStartDate": "2024-01-01",
                "expectedEndDate": "2024-01-01",
                "unexpected": True,
            },
            request_id="unknown-field-1",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "unknown_fields")
        self.assertEqual(
            SalesWriteRequestReceipt.objects.get(request_id="unknown-field-1").status,
            "completed",
        )

    @override_settings(DJANGO_PROCESS_ROLE="reader", DJANGO_EXPECT_READ_ONLY=True)
    def test_reader_process_cannot_mutate(self) -> None:
        response = self._post(
            "/api/sales/imports/uploads",
            {"action": "init"},
            request_id="reader-denied-1",
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "sales_writer_process_required")
        self.assertEqual(SalesWriteRequestReceipt.objects.count(), 0)

    def test_reader_surface_exposes_raw_get_but_not_raw_mutations(self) -> None:
        upload = begin_raw_upload(
            {
                "fingerprint": "reader-resume",
                "fileName": "sales.xlsx",
                "fileSizeBytes": 1,
                "chunkCount": 1,
                "expectedStartDate": "2024-01-01",
                "expectedEndDate": "2024-01-01",
            },
            "admin@example.test",
        )
        import sales.urls as sales_urls
        import teruisi_backend.urls as root_urls

        try:
            with override_settings(
                DJANGO_PROCESS_ROLE="reader", DJANGO_EXPECT_READ_ONLY=True
            ):
                importlib.reload(sales_urls)
                importlib.reload(root_urls)
                clear_url_caches()
                url = f"/api/sales/imports/uploads?uploadId={upload['id']}"
                response = self.client.get(url, headers=signed_headers(url))
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()["id"], upload["id"])

                body = json_body({"action": "claim", "uploadId": upload["id"]})
                rejected = self.client.post(
                    "/api/sales/imports/uploads",
                    data=body,
                    content_type="application/json",
                    headers=signed_headers(
                        "/api/sales/imports/uploads",
                        method="POST",
                        body=body,
                        request_id="reader-route-method-1",
                    ),
                )
                self.assertEqual(rejected.status_code, 405)
        finally:
            importlib.reload(sales_urls)
            importlib.reload(root_urls)
            clear_url_caches()

    def test_write_api_requires_admin_without_restricted_scope(self) -> None:
        url = "/api/sales/imports/uploads"
        operator = self._post(
            url,
            {"action": "init"},
            request_id="operator-denied-1",
            role="operator",
        )
        self.assertEqual(operator.status_code, 403)
        restricted = self._post(
            url,
            {"action": "init"},
            request_id="scope-denied-1",
            scope={"warehouses": [], "channels": [], "platforms": []},
        )
        self.assertEqual(restricted.status_code, 403)
        self.assertEqual(SalesWriteRequestReceipt.objects.count(), 0)

    def test_chunk_payload_read_is_owner_fenced_and_not_a_write_receipt(self) -> None:
        upload = begin_raw_upload(
            {
                "fingerprint": "chunk-payload-api",
                "fileName": "sales.xlsx",
                "fileSizeBytes": 7,
                "chunkCount": 1,
                "expectedStartDate": "2024-01-01",
                "expectedEndDate": "2024-01-01",
            },
            "admin@example.test",
        )
        registration = raw_chunk_registration(str(upload["id"]), 0, b"payload")
        register_raw_upload_chunk(registration, "admin@example.test")
        owner = str(
            claim_raw_upload(str(upload["id"]), "admin@example.test")["ownerToken"]
        )
        url = "/api/sales/imports/uploads/chunk"
        payload: dict[str, object] = {
            "uploadId": upload["id"],
            "chunkIndex": 0,
            "ownerToken": owner,
        }
        body = json_body(payload)
        response = self.client.post(
            url,
            data=body,
            content_type="application/json",
            headers=signed_headers(
                url,
                method="POST",
                body=body,
                request_id="chunk-payload-read-1",
            ),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json()["chunk"]["contentBase64"],
            registration["contentBase64"],
        )
        self.assertEqual(SalesWriteRequestReceipt.objects.count(), 0)

        stale_body = json_body({**payload, "ownerToken": "f" * 32})
        stale = self.client.post(
            url,
            data=stale_body,
            content_type="application/json",
            headers=signed_headers(
                url,
                method="POST",
                body=stale_body,
                request_id="chunk-payload-read-2",
            ),
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "conflict")

    def test_verify_and_import_listing_read_back_published_batch(self) -> None:
        session = begin_staged_import(
            {
                "fingerprint": "api-verify",
                "fileName": "api-verify.xlsx",
                "fileSizeBytes": 1_024,
                "rawFileHash": "f" * 64,
                "sheetName": "销售单明细账",
                "expectedStartDate": "2024-01-01",
                "expectedEndDate": "2024-01-01",
                "chunkCount": 1,
                "sourceTotals": {},
            },
            "admin@example.test",
        )
        stage_normalized_chunk(
            {"sessionId": session["id"], "chunkIndex": 0, "rows": [normalized_row(1)]},
            "admin@example.test",
        )
        result = complete_staged_import(session["id"], "admin@example.test")
        batch_id = str(result["batch"]["id"])
        excluded = make_line(
            99,
            "excluded-brush-row",
            first_import_batch_id="legacy-batch",
            last_import_batch_id="legacy-batch",
            warehouse=" 刷刷仓 ",
            ship_time="2024-01-01 11:00:00",
            sales_time="2024-01-01 10:00:00",
        )
        excluded.save()

        verify_url = (
            "/api/sales/imports/verify?startDate=2024-01-01&endDate=2024-01-01"
            f"&batchId={batch_id}"
        )
        verified = self.client.get(verify_url, headers=signed_headers(verify_url))
        self.assertEqual(verified.status_code, 200, verified.content)
        self.assertEqual(verified.json()["period"]["endExclusive"], "2024-01-02")
        self.assertEqual(verified.json()["stats"]["rowCount"], 1)
        self.assertEqual(verified.json()["stats"]["excludedWarehouseRows"], 1)
        self.assertEqual(verified.json()["stats"]["rowsNotOwnedByBatch"], 0)
        self.assertEqual(verified.json()["batch"]["id"], batch_id)

        listing_url = "/api/sales/imports?page=1&pageSize=10"
        listing = self.client.get(
            listing_url,
            headers=signed_headers(listing_url, role="viewer"),
        )
        self.assertEqual(listing.status_code, 200, listing.content)
        self.assertEqual(listing.json()["items"][0]["id"], batch_id)
