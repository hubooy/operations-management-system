from __future__ import annotations

import hashlib

from django.test import TestCase, override_settings
from django.utils import timezone

from erp_reference.models import (
    ErpReferenceImportBatch,
    ErpReferenceImportScopeHead,
    ErpReferenceWriteAuthority,
)
from erp_reference.import_service import combined_database_digest, content_hash
from sales.models import (
    ErpProductMaster,
    SalesCutoverAttestation,
    SalesDataRevision,
    SalesWriteAuthority,
)
from sales.tests.cutover_fixtures import install_lightweight_attestation


SALES_DIGEST = "c" * 64
ERP_DIGEST = "d" * 64
WRITER_EPOCH = "11111111-1111-4111-8111-111111111111"
WRITER_CUTOVER = "sales-cutover-20260828"
ERP_CUTOVER = "erp-reference-test-cutover"
ERP_VERIFY_RUN = "erp-reference-" + "a" * 32


class HealthTests(TestCase):
    def _create_erp_product(self) -> None:
        ErpProductMaster.objects.update_or_create(
            product_code="P1",
            defaults={
                "product_name": "ERP readiness fixture",
                "source_row_number": 1,
                "last_import_batch_id": "products:baseline",
                "created_at": "2026-08-28 00:00:00",
                "updated_at": "2026-08-28 00:00:00",
            },
        )

    def _create_terminal_erp_state(self, *, erp_revision: int, erp_digest: str) -> None:
        self._create_erp_product()
        for source, row_count in (("products", 1), ("combos", 0)):
            scope_key = hashlib.sha256(
                f'{{"source":"{source}"}}'.encode("utf-8")
            ).hexdigest()
            batch_id = f"{source}:baseline"
            current_content_hash = content_hash(
                source,
                [
                    {
                        "productCode": "P1",
                        "productName": "ERP readiness fixture",
                        "brand": "",
                        "specification": "",
                        "barcode": "",
                        "category": "",
                        "supplier": "",
                        "productStatus": "",
                        "sourceRowNumber": 1,
                    }
                ]
                if source == "products"
                else [],
            )
            ErpReferenceImportBatch.objects.update_or_create(
                id=batch_id,
                defaults={
                    "source_key": source,
                    "source_label": source,
                    "file_name": f"{source}.xlsx",
                    "file_size_bytes": 1,
                    "file_hash": hashlib.sha256(batch_id.encode()).hexdigest(),
                    "raw_file_hash": hashlib.sha256(f"raw:{batch_id}".encode()).hexdigest(),
                    "content_hash": current_content_hash,
                    "scope_key": scope_key,
                    "published_state_token": erp_digest,
                    "sheet_name": "Sheet1",
                    "status": "completed",
                    "row_count": row_count,
                    "inserted_count": row_count,
                    "completed_at": timezone.now(),
                },
            )
            ErpReferenceImportScopeHead.objects.filter(source_key=source).update(
                scope_key=scope_key,
                state_token=erp_digest,
                status="ready",
                owner_token="",
                current_batch_id=batch_id,
            )
        ErpReferenceWriteAuthority.objects.filter(id=1).update(
            status="postgres",
            authority_epoch="33333333-3333-4333-8333-333333333333",
            cutover_id=ERP_CUTOVER,
            migration_verify_run_id=ERP_VERIFY_RUN,
            activated_at=timezone.now(),
        )
        SalesDataRevision.objects.filter(domain="erp").update(
            revision=erp_revision,
            source_digest=combined_database_digest(),
        )

    def _create_revisions(
        self,
        *,
        sales_revision: int = 7,
        erp_revision: int = 3,
        sales_digest: str = SALES_DIGEST,
        erp_digest: str = ERP_DIGEST,
        terminal_erp: bool = True,
    ) -> None:
        SalesDataRevision.objects.update_or_create(
            domain="sales",
            defaults={"revision": sales_revision, "source_digest": sales_digest},
        )
        SalesDataRevision.objects.update_or_create(
            domain="erp",
            defaults={"revision": erp_revision, "source_digest": erp_digest},
        )
        if terminal_erp and erp_revision > 0 and len(erp_digest) == 64:
            self._create_terminal_erp_state(
                erp_revision=erp_revision, erp_digest=erp_digest
            )

    def test_liveness_does_not_require_database_state(self):
        response = self.client.get("/health/live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "teruisi-django"})
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_reader_readiness_requires_initialized_revisions(self):
        self.assertEqual(self.client.get("/health/ready").status_code, 503)
        self._create_revisions()
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reader"], "ready")
        self.assertNotIn("projection", response.json())

    def test_readiness_rejects_zero_revision_and_malformed_digest(self):
        self._create_revisions(sales_revision=0, erp_digest="not-a-digest")
        self.assertEqual(self.client.get("/health/ready").status_code, 503)

    def test_readiness_rejects_empty_source_digest(self):
        self._create_revisions(sales_digest="", erp_digest="")
        self.assertEqual(self.client.get("/health/ready").status_code, 503)

    def test_readiness_rejects_missing_or_inactive_erp_authority(self):
        self._create_revisions(terminal_erp=False)
        self.assertEqual(self.client.get("/health/ready").status_code, 503)
        self._create_terminal_erp_state(erp_revision=3, erp_digest=ERP_DIGEST)
        self.assertEqual(self.client.get("/health/ready").status_code, 200)
        ErpReferenceWriteAuthority.objects.filter(id=1).update(
            status="d1",
            authority_epoch=None,
            cutover_id="",
            migration_verify_run_id="",
            activated_at=None,
        )
        self.assertEqual(self.client.get("/health/ready").status_code, 503)

    def test_readiness_rejects_scope_or_row_count_divergence(self):
        self._create_revisions()
        ErpReferenceImportScopeHead.objects.filter(source_key="products").update(
            owner_token="busy"
        )
        self.assertEqual(self.client.get("/health/ready").status_code, 503)
        ErpReferenceImportScopeHead.objects.filter(source_key="products").update(
            owner_token=""
        )
        ErpReferenceImportBatch.objects.filter(id="products:baseline").update(
            row_count=2
        )
        self.assertEqual(self.client.get("/health/ready").status_code, 503)

    @override_settings(DJANGO_EXPECT_READ_ONLY=True)
    def test_readiness_fails_when_online_connection_is_writable(self):
        self._create_revisions()
        self.assertEqual(self.client.get("/health/ready").status_code, 503)

    @override_settings(
        DJANGO_PROCESS_ROLE="sales_writer",
        DJANGO_EXPECT_READ_ONLY=False,
        DJANGO_ENVIRONMENT="test",
        SALES_WRITE_AUTHORITY_EPOCH=WRITER_EPOCH,
        SALES_WRITE_CUTOVER_ID=WRITER_CUTOVER,
    )
    def test_writer_readiness_uses_authority(self):
        self._create_revisions()
        install_lightweight_attestation(WRITER_CUTOVER)
        SalesWriteAuthority.objects.filter(id=1).update(
            status="active",
            authority_epoch=WRITER_EPOCH,
            cutover_id=WRITER_CUTOVER,
        )
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["writer"], "ready")
        self.assertNotIn("projection", response.json())

        SalesWriteAuthority.objects.filter(id=1).update(
            authority_epoch="22222222-2222-4222-8222-222222222222"
        )
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "sales_writer_unavailable")

    @override_settings(
        DJANGO_PROCESS_ROLE="sales_writer",
        DJANGO_EXPECT_READ_ONLY=False,
        DJANGO_ENVIRONMENT="test",
        SALES_WRITE_AUTHORITY_EPOCH=WRITER_EPOCH,
        SALES_WRITE_CUTOVER_ID=WRITER_CUTOVER,
    )
    def test_writer_readiness_fails_closed_without_terminal_erp_authority(self):
        install_lightweight_attestation(WRITER_CUTOVER)
        SalesWriteAuthority.objects.filter(id=1).update(
            status="active",
            authority_epoch=WRITER_EPOCH,
            cutover_id=WRITER_CUTOVER,
        )
        self._create_revisions(terminal_erp=False)
        self.assertEqual(self.client.get("/health/ready").status_code, 503)
        self._create_terminal_erp_state(erp_revision=3, erp_digest=ERP_DIGEST)
        self.assertEqual(self.client.get("/health/ready").status_code, 200)

    @override_settings(
        DJANGO_PROCESS_ROLE="sales_writer",
        DJANGO_EXPECT_READ_ONLY=False,
        DJANGO_ENVIRONMENT="test",
        SALES_WRITE_AUTHORITY_EPOCH=WRITER_EPOCH,
        SALES_WRITE_CUTOVER_ID=WRITER_CUTOVER,
    )
    def test_writer_readiness_fails_closed_on_erp_digest_or_count_divergence(self):
        self._create_revisions()
        install_lightweight_attestation(WRITER_CUTOVER)
        SalesWriteAuthority.objects.filter(id=1).update(
            status="active",
            authority_epoch=WRITER_EPOCH,
            cutover_id=WRITER_CUTOVER,
        )
        SalesDataRevision.objects.filter(domain="erp").update(
            revision=3, source_digest="not-a-digest"
        )
        self.assertEqual(self.client.get("/health/ready").status_code, 503)
        SalesDataRevision.objects.filter(domain="erp").update(
            source_digest=combined_database_digest()
        )
        ErpReferenceImportBatch.objects.filter(id="products:baseline").update(row_count=2)
        self.assertEqual(self.client.get("/health/ready").status_code, 503)

    @override_settings(
        DJANGO_PROCESS_ROLE="sales_writer",
        DJANGO_EXPECT_READ_ONLY=False,
        DJANGO_ENVIRONMENT="test",
        SALES_WRITE_AUTHORITY_EPOCH=WRITER_EPOCH,
        SALES_WRITE_CUTOVER_ID=WRITER_CUTOVER,
    )
    def test_writer_readiness_fails_closed_while_authority_is_pending(self):
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "sales_writer_unavailable")

    @override_settings(
        DJANGO_PROCESS_ROLE="sales_writer",
        DJANGO_EXPECT_READ_ONLY=False,
        DJANGO_ENVIRONMENT="test",
        SALES_WRITE_AUTHORITY_EPOCH=WRITER_EPOCH,
        SALES_WRITE_CUTOVER_ID=WRITER_CUTOVER,
    )
    def test_writer_readiness_requires_an_untampered_v2_attestation(self):
        self._create_revisions()
        SalesWriteAuthority.objects.filter(id=1).update(
            status="active", authority_epoch=WRITER_EPOCH, cutover_id=WRITER_CUTOVER
        )
        self.assertEqual(self.client.get("/health/ready").status_code, 503)
        attestation = install_lightweight_attestation(WRITER_CUTOVER)
        self.assertEqual(self.client.get("/health/ready").status_code, 200)
        payload = dict(attestation.payload)
        payload["d1Blockers"] = dict(payload["d1Blockers"])
        del payload["d1Blockers"]["uploadChunks"]
        SalesCutoverAttestation.objects.filter(cutover_id=WRITER_CUTOVER).update(
            payload=payload
        )
        self.assertEqual(self.client.get("/health/ready").status_code, 503)

    def test_non_loopback_peer_is_rejected(self):
        response = self.client.get("/health/live", REMOTE_ADDR="192.0.2.10")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "loopback_only")
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_forwarded_address_is_ignored_but_invalid_host_is_rejected(self):
        response = self.client.get(
            "/health/live",
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_FORWARDED_FOR="192.0.2.10",
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.get(
            "/health/live", REMOTE_ADDR="127.0.0.1", HTTP_HOST="attacker.example"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_host")

    @override_settings(DJANGO_MAX_HEADER_BYTES=128)
    def test_oversized_headers_are_rejected_before_the_view(self):
        response = self.client.get("/health/live", HTTP_X_FILLER="x" * 256)
        self.assertEqual(response.status_code, 431)
        self.assertEqual(response.json()["code"], "request_headers_too_large")

    @override_settings(DJANGO_MAX_BODY_BYTES=16)
    def test_oversized_body_is_rejected_before_the_view(self):
        response = self.client.generic(
            "GET", "/health/live", data=b"", CONTENT_LENGTH="17"
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["code"], "request_body_too_large")
